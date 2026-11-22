"""Four-signal disposition model (CORE-4): one SignalProvider interface, pluggable impls."""

import abc
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bridge.classification import canonicalize_issuer, is_in_label_space
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    DispositionThresholds,
    DispositionVerdict,
    dispose,
)
from bridge.ports import Port, get_adapter, resolve_mode
from contract.extraction import ExtractionResult, fields_requiring_review

_SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ExtractorLike(Protocol):
    """Structural extraction contract satisfied by ExtractionPort, FixtureExtractor, and test spies."""

    async def extract(self, artifact_name: str) -> ExtractionResult:
        """Extract fields and signals for a document artifact."""


class Signals(BaseModel):
    """The four disposition signals for one document (design/disposition.md:21-30)."""

    model_config = ConfigDict(extra="forbid")
    # process context (needed for the type-match signal)
    doctype: str
    candidate_doctypes: list[str]
    # signal 1 — legibility
    legible: bool
    # signal 3 — extraction confidence + fields requiring review
    overall_confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    flagged_fields: list[str] = []
    # sense-A canonical issuer (per-doc normalization)
    issuer: str | None = None
    # signal 2b — optional freshness (document age vs. max_age_days threshold)
    document_age_days: int | None = None
    max_age_days: int | None = None
    # signal 4 — corroboration & declared-rule flags (completeness proper is sense-B)
    failed_rules: list[str] = []

    @property
    def type_match(self) -> bool:  # signal 2 — right kind?
        """Return True if the detected document type matches the expected document type."""
        return is_in_label_space(self.doctype, self.candidate_doctypes)

    @property
    def freshness_ok(self) -> bool:
        """Return True if document_age_days is within max_age_days (or when unbounded)."""
        if self.document_age_days is None or self.max_age_days is None:
            return True
        return self.document_age_days <= self.max_age_days

    @property
    def corroboration_ok(self) -> bool:
        """Return True when no declared corroboration or cross-field rules failed."""
        return len(self.failed_rules) == 0


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _lookup_field_value(result: ExtractionResult, field_expr: str) -> str | None:
    if field_expr.startswith("key_fields."):
        return result.fields.key_fields.get(field_expr[len("key_fields.") :])
    if field_expr == "issuer":
        return result.fields.issuer
    if field_expr == "doctype":
        return result.fields.doctype
    return None


def _check_non_empty(result: ExtractionResult, field_expr: str) -> bool:
    if field_expr.startswith("tables.") and "[]." in field_expr:
        table_name, col = field_expr[len("tables.") :].split("[].", 1)
        rows = result.fields.tables.get(table_name, [])
        return all(
            row.get(col) is not None and bool(str(row.get(col)).strip()) for row in rows
        )
    val = _lookup_field_value(result, field_expr)
    return val is not None and bool(str(val).strip())


def _check_all_required_present(
    result: ExtractionResult, schema: dict[str, Any]
) -> bool:
    top_required = schema.get("required") or []
    props = schema.get("properties") or {}
    kf_spec = props.get("key_fields") if isinstance(props, dict) else None
    kf_required = kf_spec.get("required") or [] if isinstance(kf_spec, dict) else []
    for key in top_required:
        if key == "doctype" and not result.fields.doctype.strip():
            return False
        if key == "issuer" and not (result.fields.issuer or "").strip():
            return False
        if key == "key_fields" and not kf_required and not result.fields.key_fields:
            return False
    for key in kf_required:
        val = result.fields.key_fields.get(str(key))
        if val is None or not str(val).strip():
            return False
    return True


def _load_validation_rules(doctype: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    registry = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
    try:
        raw_yaml = registry.read_asset(doctype, "assets/validation.yaml")
    except KeyError:
        return [], {}
    yaml_text = raw_yaml.decode("utf-8") if isinstance(raw_yaml, bytes) else raw_yaml
    parsed = yaml.safe_load(yaml_text)
    if not isinstance(parsed, dict):
        return [], {}
    raw_rules = parsed.get("rules")
    rules = (
        [r for r in raw_rules if isinstance(r, dict)]
        if isinstance(raw_rules, list)
        else []
    )
    schema: dict[str, Any] = {}
    if any(r.get("check") == "all_required_present" for r in rules):
        try:
            raw_schema = registry.read_asset(doctype, "assets/schema.json")
            schema_text = (
                raw_schema.decode("utf-8")
                if isinstance(raw_schema, bytes)
                else raw_schema
            )
            loaded = json.loads(schema_text)
            if isinstance(loaded, dict):
                schema = loaded
        except KeyError:
            schema = {}
    return rules, schema


def _eval_within_days(
    result: ExtractionResult,
    field_expr: str,
    window: object,
    ref_date: date,
) -> tuple[bool, int | None, int | None]:
    if not isinstance(window, int):
        return True, None, None
    parsed_d = _parse_iso_date(_lookup_field_value(result, field_expr))
    if parsed_d is None:
        return False, None, None
    age = (ref_date - parsed_d).days
    if age < 0:
        return False, None, None
    return True, age, window


def _eval_validation_rule(
    result: ExtractionResult,
    rule: dict[str, Any],
    *,
    schema: dict[str, Any],
    ref_date: date | None,
    today_or_ref: date,
) -> tuple[str | None, int | None, int | None]:
    rule_id = str(rule.get("id") or "")
    check = str(rule.get("check") or "")
    field_expr = str(rule.get("field") or "")
    if not rule_id:
        return None, None, None
    if check == "non_empty":
        return (None if _check_non_empty(result, field_expr) else rule_id), None, None
    if check == "all_required_present":
        ok = not schema or _check_all_required_present(result, schema)
        return (None if ok else rule_id), None, None
    if check == "date_in_future":
        parsed_d = _parse_iso_date(_lookup_field_value(result, field_expr))
        ok = parsed_d is not None and parsed_d >= today_or_ref
        return (None if ok else rule_id), None, None
    if check == "within_days" and ref_date is not None:
        ok, age, window = _eval_within_days(
            result, field_expr, rule.get("window_days"), ref_date
        )
        return (None if ok else rule_id), age, window
    return None, None, None


def _evaluate_validation_rules(
    result: ExtractionResult,
    *,
    candidate_doctypes: list[str],
    as_of: date | datetime | None = None,
) -> tuple[list[str], int | None, int | None]:
    doctype = result.fields.doctype
    if not is_in_label_space(
        doctype, candidate_doctypes
    ) or not _SKILL_SLUG_RE.fullmatch(doctype):
        return [], None, None
    rules, schema = _load_validation_rules(doctype)
    if not rules:
        return [], None, None

    ref_date = as_of.date() if isinstance(as_of, datetime) else as_of
    today_or_ref = ref_date or datetime.now(UTC).date()
    failed_rules: list[str] = []
    document_age_days: int | None = None
    max_age_days: int | None = None

    for rule in rules:
        failed_id, age, window = _eval_validation_rule(
            result,
            rule,
            schema=schema,
            ref_date=ref_date,
            today_or_ref=today_or_ref,
        )
        if failed_id is not None:
            failed_rules.append(failed_id)
        if (
            age is not None
            and window is not None
            and (
                document_age_days is None
                or max_age_days is None
                or (age - window) > (document_age_days - max_age_days)
            )
        ):
            document_age_days = age
            max_age_days = window

    return failed_rules, document_age_days, max_age_days


def signals_from_result(
    result: ExtractionResult,
    *,
    candidate_doctypes: list[str],
    as_of: date | datetime | None = None,
) -> Signals:
    """Project a raw ExtractionResult into the four-signal bundle (canonicalizes issuer).

    Evaluates declared ``skills/<doctype>/assets/validation.yaml`` rules (populating
    ``failed_rules`` and, when ``as_of`` is supplied, ``document_age_days`` /
    ``max_age_days``). The raw ``flagged_fields`` list wins whenever it is non-empty;
    ``field_reviews`` provides the dotted-path fallback when ``flagged_fields`` is empty.
    """
    failed_rules, document_age_days, max_age_days = _evaluate_validation_rules(
        result, candidate_doctypes=candidate_doctypes, as_of=as_of
    )
    return Signals(
        doctype=result.fields.doctype,
        candidate_doctypes=candidate_doctypes,
        legible=result.legible,
        overall_confidence=result.overall_confidence,
        flagged_fields=result.flagged_fields or fields_requiring_review(result),
        issuer=canonicalize_issuer(result.fields.issuer),
        document_age_days=document_age_days,
        max_age_days=max_age_days,
        failed_rules=failed_rules,
    )


class SignalProvider(abc.ABC):
    """One contract; pluggable impls (fixture / model-reported / computed)."""

    @abc.abstractmethod
    async def signals_for(
        self,
        artifact_name: str,
        *,
        candidate_doctypes: list[str],
        as_of: date | datetime | None = None,
    ) -> Signals:
        """Produce the four disposition signals for a document artifact."""


class ExtractionSignalProvider(SignalProvider):
    """Signals derived from the extraction port; canonicalizes the issuer Bridge-side."""

    def __init__(self, extractor: ExtractorLike | None = None) -> None:
        self._extractor = extractor  # None -> resolve the extraction port lazily

    async def signals_for(
        self,
        artifact_name: str,
        *,
        candidate_doctypes: list[str],
        as_of: date | datetime | None = None,
    ) -> Signals:
        """Extract quality, freshness, and schema completeness signals from a document."""
        extractor: ExtractorLike = self._extractor or get_adapter(
            Port.extraction, resolve_mode(Port.extraction)
        )
        r = await extractor.extract(artifact_name)
        return signals_from_result(
            r, candidate_doctypes=candidate_doctypes, as_of=as_of
        )


def dispose_signals(
    signals: Signals, thresholds: DispositionThresholds = DEFAULT_THRESHOLDS
) -> DispositionVerdict:
    """Run the deterministic gate over a Signals bundle (each signal drives its route)."""
    return dispose(
        doctype=signals.doctype,
        candidate_doctypes=signals.candidate_doctypes,
        legible=signals.legible,
        overall_confidence=signals.overall_confidence,
        flagged_fields=signals.flagged_fields,
        failed_rules=signals.failed_rules,
        document_age_days=signals.document_age_days,
        max_age_days=signals.max_age_days,
        thresholds=thresholds,
    )
