"""Consolidation Rules Engine, AlloyDB Bi-Temporal Store, Role-Based Views & BigQuery Sync.

Implements Zone 3 (Consolidation Rules Engine + AlloyDB Operational Store) and Zone 4
(Role-Based Business Views + BigQuery Analytics Sync) from the Enterprise Data
Architecture:

1. **Consolidation Rules Engine (100% deterministic ``Decimal`` math, zero LLM):**
   - Extracts and normalizes monetary and coverage facts from accepted ``LedgerEntry``
     records (:func:`consolidate_entry`).
   - Evaluates source authority priority (``endorsement`` [40] > ``policy`` [30] >
     ``quote`` / ``cert`` [20] > ``application`` / default [10]).
   - Computes exact financial totals, deltas, and ratios using ``decimal.Decimal``
     (:func:`compute_financial_summary`).
   - Detects cross-document conflicts (:class:`FactConflict`) when two sources assert
     divergent values for the same field.

2. **AlloyDB Bi-Temporal Operational Store (:class:`OperationalStore`):**
   - Append-only storage (:class:`InMemoryOperationalStore` locally without a DSN;
     :class:`SqlOperationalStore` over SQLite locally or AlloyDB / Cloud SQL Postgres
     via ``DATABASE_URL`` / ``BRIDGE_OPERATIONAL_DB_URL``).
   - **No overwrites:** Every arriving update appends a new :class:`ConsolidatedFact`
     version carrying both ``recorded_at`` (when learned by the platform) and
     ``effective_from`` (when coverage takes effect in the business), plus its
     :class:`SourceAnchor` (page number + bounding box) and ``vault_uri``.
   - **Point-in-time historical queries (:meth:`OperationalStore.query_as_of`):**
     Answers both *"What did we know 2 hours ago?"* (``learned_as_of``) and *"What
     coverage limit was active on the loss date?"* (``effective_on``).

3. **Zone 4 — Role-Based Business Views & BigQuery Analytics Sync:**
   - **Broker View (:meth:`OperationalStore.broker_view`)**: Quotes, normalized
     financials, coverage gaps, conflicts, and click-to-source-page links.
   - **Claims View (:meth:`OperationalStore.claims_view`)**: Active limits and
     deductibles in effect on a specific ``loss_date`` with source page traceability.
   - **Underwriter View (:meth:`OperationalStore.underwriter_view`)**: Insured values,
     total exposure, ``Decimal`` deductible-to-limit ratio, and source links.
   - **BigQuery Analytics Sync (:meth:`OperationalStore.export_bigquery_sync_batch`)**:
     Live change-data-capture (CDC) batch export + Open Iceberg DDL
     (:data:`BIGQUERY_ICEBERG_DDL`).

Isolation: imports ``contract/`` + ``bridge.seams`` + ``pydantic`` + stdlib (SQLAlchemy
is imported lazily inside :class:`SqlOperationalStore`); NEVER ``agents/``.
"""

from __future__ import annotations

import abc
import asyncio
import json
import os
import re
import threading
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

from bridge.telemetry import trace_step
from contract.enums import Disposition
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry

OPERATIONAL_DB_ENV = "BRIDGE_OPERATIONAL_DB_URL"
FACTS_TABLE_NAME = "bridge_operational_facts"
_OP_TIMEOUT_S = 30.0

_MONEY_KEYS = frozenset(
    {
        # Insurance & Benefits
        "premium",
        "deductible",
        "coverage_limit",
        "coverage_limits",
        "insured_value",
        "total_insured_value",
        "claim_amount",
        "reserve_amount",
        "sublimit",
        "net_pay",
        # Banking & Commercial Loan Underwriting
        "loan_amount",
        "principal_amount",
        "facility_limit",
        "appraised_value",
        "collateral_value",
        "origination_fee",
        "closing_fee",
        "rate_lock_fee",
        "annual_debt_service",
        "net_operating_income",
    }
)

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def parse_decimal_money(raw: str | None) -> Decimal | None:
    """Deterministically parse a monetary string into ``Decimal(0.01)`` (or ``None``)."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return None
    token = match.group(0).replace(",", "")
    try:
        val = Decimal(token)
    except InvalidOperation:
        return None
    # Handle shorthand suffixes like "$25M" or "$500K" when explicitly adjacent
    suffix_pos = match.end()
    rest = cleaned[suffix_pos:].strip().upper()
    if rest.startswith(("M", "MILLION")):
        val *= Decimal("1000000")
    elif rest.startswith(("K", "THOUSAND")):
        val *= Decimal("1000")
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def source_priority_for_doctype(doctype: str) -> int:
    """Deterministic source authority hierarchy for consolidation rules.

    Higher number wins when effective dates are equal:
    - endorsement / amendment / addendum / closing-disclosure / fee-override: 40
    - bound policy / binder / credit-agreement / commitment-letter / loan-agreement / promissory-note: 30
    - quote / certificate / term-sheet / appraisal / rent-roll: 20
    - application / bill / statement / id / default: 10
    """
    dt = (doctype or "").strip().lower()
    if any(
        tok in dt
        for tok in (
            "endorsement",
            "amendment",
            "addendum",
            "closing-disclosure",
            "fee-override",
        )
    ):
        return 40
    if any(
        tok in dt
        for tok in (
            "policy",
            "binder",
            "credit-agreement",
            "commitment-letter",
            "loan-agreement",
            "promissory-note",
        )
    ):
        return 30
    if any(
        tok in dt
        for tok in (
            "quote",
            "cert",
            "term-sheet",
            "appraisal",
            "rent-roll",
        )
    ):
        return 20
    return 10


class SourceTrace(BaseModel):
    """Click-to-source-page traceability attached to every view metric and row."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    doctype: str
    vault_uri: str | None = None
    page_number: int = 1
    bounding_box: tuple[float, float, float, float] | None = None
    snippet: str | None = None
    skill_version: str = "1.0.0"
    skill_revision_id: str = "rev-v1-0-baseline"
    skill_urn: str | None = None
    process_skill_revision_id: str = "rev-v1-0-baseline"
    compiled_rule_ids: tuple[str, ...] = ()


class ConsolidatedFact(BaseModel):
    """One append-only, bi-temporal fact row in the AlloyDB Operational Store."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    entity_id: str
    context_id: str
    doc_id: str
    doctype: str
    issuer: str | None = None
    field_path: str
    field_value: str
    numeric_value: str | None = None  # Decimal string (2 dp) when monetary
    currency: str = "USD"
    source_priority: int = 10
    recorded_at: datetime
    effective_from: datetime
    version: int = 1
    source_anchor: SourceAnchor | None = None
    vault_uri: str | None = None
    flagged_for_review: bool = False
    skill_version: str = "1.0.0"
    skill_revision_id: str = "rev-v1-0-baseline"
    skill_urn: str | None = None
    process_skill_revision_id: str = "rev-v1-0-baseline"
    compiled_rule_ids: tuple[str, ...] = ()

    def trace(self) -> SourceTrace:
        """Build the click-to-source-page trace for this fact."""
        anchor = self.source_anchor
        return SourceTrace(
            doc_id=self.doc_id,
            doctype=self.doctype,
            vault_uri=(
                (anchor.artifact_uri if anchor and anchor.artifact_uri else None)
                or self.vault_uri
            ),
            page_number=anchor.page_number if anchor else 1,
            bounding_box=anchor.bounding_box if anchor else None,
            snippet=anchor.snippet if anchor else self.field_value,
            skill_version=self.skill_version,
            skill_revision_id=self.skill_revision_id,
            skill_urn=self.skill_urn,
            process_skill_revision_id=self.process_skill_revision_id,
            compiled_rule_ids=self.compiled_rule_ids,
        )


class FactConflict(BaseModel):
    """A cross-document value conflict flagged by the Consolidation Rules Engine."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    field_path: str
    winning_fact_id: str
    winning_value: str
    winning_doc_id: str
    conflicting_fact_id: str
    conflicting_value: str
    conflicting_doc_id: str
    reason: str
    winning_trace: SourceTrace
    conflicting_trace: SourceTrace


class FinancialSummary(BaseModel):
    """Deterministic Decimal financial rollup across active facts."""

    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    total_premium: str = "0.00"
    total_deductible: str = "0.00"
    total_coverage_limit: str = "0.00"
    total_insured_value: str = "0.00"
    deductible_to_limit_pct: str | None = None


class BrokerQuoteRow(BaseModel):
    """One carrier or submission row in the Role-Based Broker View."""

    model_config = ConfigDict(extra="forbid")

    issuer: str
    doc_id: str
    doctype: str
    premium: str | None = None
    deductible: str | None = None
    coverage_limits: str | None = None
    exclusions: str | None = None
    effective_from: datetime
    recorded_at: datetime
    traces: dict[str, SourceTrace] = {}


class BrokerView(BaseModel):
    """Zone 4 Role-Based Broker View: quotes, coverage gaps, conflicts & source links."""

    model_config = ConfigDict(extra="forbid")

    view_name: str = "v_broker_placement"
    entity_id: str
    as_of_learned: datetime
    quotes: list[BrokerQuoteRow] = []
    coverage_gaps: list[str] = []
    conflicts: list[FactConflict] = []
    financials: FinancialSummary


class ClaimsActiveCoverage(BaseModel):
    """One active coverage item in the Role-Based Claims View on ``loss_date``."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    field_value: str
    numeric_value: str | None = None
    issuer: str | None = None
    doc_id: str
    doctype: str
    effective_from: datetime
    recorded_at: datetime
    trace: SourceTrace


class ClaimsView(BaseModel):
    """Zone 4 Role-Based Claims View: active limits and deductibles on ``loss_date``."""

    model_config = ConfigDict(extra="forbid")

    view_name: str = "v_claims_coverage"
    entity_id: str
    loss_date: datetime
    as_of_learned: datetime
    active_coverages: list[ClaimsActiveCoverage] = []
    active_limit_total: str = "0.00"
    active_deductible_total: str = "0.00"
    endorsement_count: int = 0


class UnderwriterExposureItem(BaseModel):
    """One exposure/schedule metric in the Role-Based Underwriter View."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    field_value: str
    numeric_value: str | None = None
    doc_id: str
    doctype: str
    flagged_for_review: bool = False
    trace: SourceTrace


class UnderwriterView(BaseModel):
    """Zone 4 Role-Based Underwriter View: insured values, exposure & risk ratios."""

    model_config = ConfigDict(extra="forbid")

    view_name: str = "v_underwriter_exposure"
    entity_id: str
    as_of_learned: datetime
    exposure_items: list[UnderwriterExposureItem] = []
    financials: FinancialSummary
    flagged_items_count: int = 0


class BigQuerySyncRecord(BaseModel):
    """One CDC row exported from AlloyDB to BigQuery Open Iceberg tables."""

    model_config = ConfigDict(extra="forbid")

    sync_id: str
    operation: str = "APPEND"
    iceberg_table: str = "docubridge_lakehouse.bq_operational_facts_iceberg"
    entity_id: str
    fact_id: str
    doc_id: str
    doctype: str
    issuer: str | None = None
    field_path: str
    field_value: str
    numeric_value: str | None = None
    currency: str
    recorded_at: datetime
    effective_from: datetime
    vault_uri: str | None = None
    page_number: int = 1


class BigQuerySyncBatch(BaseModel):
    """Batch of append-only CDC records synced from AlloyDB to BigQuery Analytics."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    exported_at: datetime
    iceberg_table: str = "docubridge_lakehouse.bq_operational_facts_iceberg"
    row_count: int
    watermark_recorded_at: datetime | None = None
    records: list[BigQuerySyncRecord] = []


_ACTIVE_FACTS_CTE: str = (
    "WITH active_facts AS ("
    "SELECT *, ROW_NUMBER() OVER ("
    "PARTITION BY entity_id, field_path "
    "ORDER BY source_priority DESC, effective_from DESC, recorded_at DESC, version DESC"
    ") AS rn FROM bridge_operational_facts"
    ") "
)

# Governed SQL definitions for the three Zone 4 Role-Based Views & BigQuery Iceberg DDL.
SQL_VIEW_DEFINITIONS: dict[str, str] = {
    "v_broker_placement": (
        "CREATE OR REPLACE VIEW v_broker_placement AS "
        + _ACTIVE_FACTS_CTE
        + "SELECT entity_id, issuer, doc_id, doctype, field_path, field_value, "
        "numeric_value, currency, effective_from, recorded_at, vault_uri, "
        "source_anchor_json FROM active_facts WHERE rn = 1;"
    ),
    "v_claims_coverage": (
        "CREATE OR REPLACE VIEW v_claims_coverage AS "
        + _ACTIVE_FACTS_CTE
        + "SELECT entity_id, issuer, doc_id, doctype, field_path, field_value, "
        "numeric_value, currency, source_priority, effective_from, recorded_at, "
        "vault_uri, source_anchor_json FROM active_facts WHERE rn = 1;"
    ),
    "v_underwriter_exposure": (
        "CREATE OR REPLACE VIEW v_underwriter_exposure AS "
        + _ACTIVE_FACTS_CTE
        + "SELECT entity_id, doc_id, doctype, field_path, field_value, numeric_value, "
        "currency, flagged_for_review, effective_from, recorded_at, vault_uri, "
        "source_anchor_json FROM active_facts WHERE rn = 1;"
    ),
}

BIGQUERY_ICEBERG_DDL: str = (
    "CREATE TABLE IF NOT EXISTS `docubridge_lakehouse.bq_operational_facts_iceberg` (\n"
    "  sync_id STRING NOT NULL,\n"
    "  entity_id STRING NOT NULL,\n"
    "  fact_id STRING NOT NULL,\n"
    "  doc_id STRING NOT NULL,\n"
    "  doctype STRING NOT NULL,\n"
    "  issuer STRING,\n"
    "  field_path STRING NOT NULL,\n"
    "  field_value STRING NOT NULL,\n"
    "  numeric_value NUMERIC,\n"
    "  currency STRING NOT NULL,\n"
    "  recorded_at TIMESTAMP NOT NULL,\n"
    "  effective_from TIMESTAMP NOT NULL,\n"
    "  vault_uri STRING,\n"
    "  page_number INT64\n"
    ")\n"
    "WITH CONNECTION `us.lakehouse-connection`\n"
    "OPTIONS (file_format = 'PARQUET', table_format = 'ICEBERG');"
)


def consolidate_entry(
    entry: LedgerEntry,
    *,
    entity_id: str,
    context_id: str,
    recorded_at: datetime | None = None,
    effective_from: datetime | None = None,
    version: int = 1,
) -> list[ConsolidatedFact]:
    """Consolidate an accepted ``LedgerEntry`` into typed ``ConsolidatedFact`` rows.

    Refuses non-accepted entries (only validated/accepted facts enter AlloyDB).
    """
    if entry.disposition is not Disposition.accepted:
        raise ValueError(
            f"consolidate_entry requires Disposition.accepted; "
            f"got {entry.disposition.value!r} for doc_id={entry.doc_id!r}"
        )
    rec_ts = recorded_at or entry.recorded_at or datetime.now(UTC)
    eff_ts = effective_from or entry.effective_from
    if eff_ts is None and "effective_date" in entry.key_fields:
        try:
            parsed_d = datetime.fromisoformat(entry.key_fields["effective_date"])
            eff_ts = (
                parsed_d.replace(tzinfo=UTC) if parsed_d.tzinfo is None else parsed_d
            )
        except ValueError:
            eff_ts = rec_ts
    eff_ts = eff_ts or rec_ts

    currency = entry.key_fields.get("currency", "USD").upper()
    priority = source_priority_for_doctype(entry.doctype)
    flagged_paths = {r.path for r in entry.field_reviews}
    vault_uri = entry.vault_uri or (
        entry.artifact.uri if entry.artifact is not None else None
    )
    skill_ver = entry.skill_version or "1.0.0"
    skill_rev = entry.skill_revision_id or "rev-v1-0-baseline"
    skill_urn = (
        entry.skill_urn
        or f"urn:skill:projects-{os.environ.get('GOOGLE_CLOUD_PROJECT') or os.environ.get('BRIDGE_GCP_PROJECT') or 'docubridge-showcase'}:locations:global:private-{entry.doctype}"
    )
    proc_skill_rev = entry.process_skill_revision_id or "rev-v1-0-baseline"
    rule_ids = tuple(entry.compiled_rule_ids or ())

    facts: list[ConsolidatedFact] = []
    for k, v in entry.key_fields.items():
        path = f"fields.key_fields.{k}"
        anchor = entry.source_anchors.get(path) or entry.source_anchors.get(k)
        dec = parse_decimal_money(v) if k.lower() in _MONEY_KEYS else None
        facts.append(
            ConsolidatedFact(
                fact_id=uuid.uuid4().hex,
                entity_id=entity_id,
                context_id=context_id,
                doc_id=entry.doc_id,
                doctype=entry.doctype,
                issuer=entry.issuer,
                field_path=path,
                field_value=v,
                numeric_value=str(dec) if dec is not None else None,
                currency=currency,
                source_priority=priority,
                recorded_at=rec_ts,
                effective_from=eff_ts,
                version=version,
                source_anchor=anchor,
                vault_uri=vault_uri,
                flagged_for_review=(path in flagged_paths or k in flagged_paths),
                skill_version=skill_ver,
                skill_revision_id=skill_rev,
                skill_urn=skill_urn,
                process_skill_revision_id=proc_skill_rev,
                compiled_rule_ids=rule_ids,
            )
        )

    for table_name, rows in entry.tables.items():
        for idx, row in enumerate(rows):
            for col, val in row.items():
                path = f"fields.tables.{table_name}[{idx}].{col}"
                anchor = (
                    entry.source_anchors.get(path)
                    or entry.source_anchors.get(f"fields.tables.{table_name}")
                    or entry.source_anchors.get(table_name)
                )
                dec = parse_decimal_money(val) if col.lower() in _MONEY_KEYS else None
                facts.append(
                    ConsolidatedFact(
                        fact_id=uuid.uuid4().hex,
                        entity_id=entity_id,
                        context_id=context_id,
                        doc_id=entry.doc_id,
                        doctype=entry.doctype,
                        issuer=entry.issuer,
                        field_path=path,
                        field_value=val,
                        numeric_value=str(dec) if dec is not None else None,
                        currency=currency,
                        source_priority=priority,
                        recorded_at=rec_ts,
                        effective_from=eff_ts,
                        version=version,
                        source_anchor=anchor,
                        vault_uri=vault_uri,
                        flagged_for_review=(path in flagged_paths),
                        skill_version=skill_ver,
                        skill_revision_id=skill_rev,
                        skill_urn=skill_urn,
                        process_skill_revision_id=proc_skill_rev,
                        compiled_rule_ids=rule_ids,
                    )
                )
    return facts


def _winning_key(fact: ConsolidatedFact) -> tuple[int, datetime, datetime, int]:
    """Deterministic ordering for active fact resolution:
    (source_priority, effective_from, recorded_at, version)."""
    return (
        fact.source_priority,
        fact.effective_from,
        fact.recorded_at,
        fact.version,
    )


def resolve_active_facts(
    facts: list[ConsolidatedFact],
    *,
    learned_as_of: datetime | None = None,
    effective_on: datetime | None = None,
) -> tuple[list[ConsolidatedFact], list[FactConflict]]:
    """Filter facts by bi-temporal window and resolve winners + conflicts per field."""
    eligible: list[ConsolidatedFact] = []
    for f in facts:
        if learned_as_of is not None and f.recorded_at > learned_as_of:
            continue
        if effective_on is not None and f.effective_from > effective_on:
            continue
        eligible.append(f)

    by_field: dict[str, list[ConsolidatedFact]] = {}
    for f in eligible:
        by_field.setdefault(f.field_path, []).append(f)

    winners: list[ConsolidatedFact] = []
    conflicts: list[FactConflict] = []
    for field_path in sorted(by_field):
        candidates = sorted(by_field[field_path], key=_winning_key, reverse=True)
        winner = candidates[0]
        winners.append(winner)
        # Flag a conflict if another distinct document at the same or different source
        # asserts a different value for the same field_path.
        for other in candidates[1:]:
            if (
                other.doc_id != winner.doc_id
                and other.field_value != winner.field_value
            ):
                why = (
                    f"{winner.doctype} ({winner.doc_id}) asserts {winner.field_value!r} "
                    f"(priority {winner.source_priority}, learned {winner.recorded_at.isoformat()}), "
                    f"superseding {other.doctype} ({other.doc_id}) value {other.field_value!r}"
                )
                conflicts.append(
                    FactConflict(
                        entity_id=winner.entity_id,
                        field_path=field_path,
                        winning_fact_id=winner.fact_id,
                        winning_value=winner.field_value,
                        winning_doc_id=winner.doc_id,
                        conflicting_fact_id=other.fact_id,
                        conflicting_value=other.field_value,
                        conflicting_doc_id=other.doc_id,
                        reason=why,
                        winning_trace=winner.trace(),
                        conflicting_trace=other.trace(),
                    )
                )
                break
    return winners, conflicts


def compute_financial_summary(facts: list[ConsolidatedFact]) -> FinancialSummary:
    """100% deterministic ``Decimal`` financial summary over active facts."""
    with trace_step(
        "docubridge.consolidation.compute_financial_summary",
        {
            "docubridge.zone": "3_consolidation_engine",
            "docubridge.input_fact_count": len(facts),
            "docubridge.entity_id": facts[0].entity_id if facts else None,
        },
    ) as span:
        total_premium = Decimal("0.00")
        total_deductible = Decimal("0.00")
        total_limit = Decimal("0.00")
        total_tiv = Decimal("0.00")
        currency = "USD"

        for f in facts:
            currency = f.currency or currency
            if f.numeric_value is None:
                continue
            val = Decimal(f.numeric_value)
            leaf = f.field_path.split(".")[-1].lower()
            if leaf in ("premium", "annual_debt_service"):
                total_premium += val
            elif leaf in (
                "deductible",
                "origination_fee",
                "closing_fee",
                "rate_lock_fee",
            ):
                total_deductible += val
            elif leaf in (
                "coverage_limit",
                "coverage_limits",
                "sublimit",
                "loan_amount",
                "principal_amount",
                "facility_limit",
            ):
                total_limit += val
            elif leaf in (
                "insured_value",
                "total_insured_value",
                "appraised_value",
                "collateral_value",
            ):
                total_tiv += val

        ratio_str: str | None = None
        denom = total_limit if total_limit > Decimal("0.00") else total_tiv
        if denom > Decimal("0.00") and total_deductible > Decimal("0.00"):
            pct = (total_deductible / denom) * Decimal("100")
            ratio_str = str(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        summary = FinancialSummary(
            currency=currency,
            total_premium=str(total_premium.quantize(Decimal("0.01"))),
            total_deductible=str(total_deductible.quantize(Decimal("0.01"))),
            total_coverage_limit=str(total_limit.quantize(Decimal("0.01"))),
            total_insured_value=str(total_tiv.quantize(Decimal("0.01"))),
            deductible_to_limit_pct=ratio_str,
        )
        span.set_attribute("docubridge.total_premium", summary.total_premium)
        span.set_attribute("docubridge.total_deductible", summary.total_deductible)
        span.set_attribute(
            "docubridge.total_coverage_limit", summary.total_coverage_limit
        )
        span.set_attribute(
            "docubridge.total_insured_value", summary.total_insured_value
        )
        if summary.deductible_to_limit_pct is not None:
            span.set_attribute(
                "docubridge.deductible_to_limit_pct", summary.deductible_to_limit_pct
            )
        return summary


def _utc_dt(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _utc_iso(dt: datetime) -> str:
    """Format a datetime as a fixed-width UTC ISO string for lexicographic SQL pushdown."""
    return _utc_dt(dt).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


class OperationalStore(abc.ABC):
    """AlloyDB / Postgres bi-temporal operational store interface (append-only)."""

    @abc.abstractmethod
    def append_facts(self, facts: list[ConsolidatedFact]) -> int:
        """Append consolidated facts (never overwrites existing facts). Returns count."""

    @abc.abstractmethod
    def list_facts(
        self,
        entity_id: str | None = None,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
        since_recorded_at: datetime | None = None,
    ) -> list[ConsolidatedFact]:
        """Return stored facts in append order, with optional bi-temporal pushdown filters."""

    def ingest_entry(
        self,
        entry: LedgerEntry,
        *,
        entity_id: str,
        context_id: str,
        recorded_at: datetime | None = None,
        effective_from: datetime | None = None,
        version: int = 1,
    ) -> list[ConsolidatedFact]:
        """Consolidate an accepted ``LedgerEntry`` and append its facts to the store."""
        with trace_step(
            "docubridge.consolidation.promote_to_operational_store",
            {
                "docubridge.zone": "3_alloydb_operational_store",
                "docubridge.entity_id": entity_id,
                "docubridge.context_id": context_id,
                "docubridge.doc_id": entry.doc_id,
                "docubridge.doctype": entry.doctype,
                "docubridge.version": version,
            },
        ) as span:
            facts = consolidate_entry(
                entry,
                entity_id=entity_id,
                context_id=context_id,
                recorded_at=recorded_at,
                effective_from=effective_from,
                version=version,
            )
            self.append_facts(facts)
            span.set_attribute("docubridge.appended_fact_count", len(facts))
            span.set_attribute(
                "docubridge.source_priority",
                facts[0].source_priority
                if facts
                else source_priority_for_doctype(entry.doctype),
            )
            return facts

    def query_as_of(
        self,
        entity_id: str,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
    ) -> tuple[list[ConsolidatedFact], list[FactConflict]]:
        """Bi-temporal point-in-time query: what was active as of ``learned_as_of``
        and ``effective_on``."""
        with trace_step(
            "docubridge.consolidation.bitemporal_query_as_of",
            {
                "docubridge.zone": "3_alloydb_operational_store",
                "docubridge.entity_id": entity_id,
                "docubridge.learned_as_of": learned_as_of.isoformat()
                if learned_as_of
                else None,
                "docubridge.effective_on": effective_on.isoformat()
                if effective_on
                else None,
            },
        ) as span:
            filtered_facts = self.list_facts(
                entity_id,
                learned_as_of=learned_as_of,
                effective_on=effective_on,
            )
            winners, conflicts = resolve_active_facts(
                filtered_facts, learned_as_of=learned_as_of, effective_on=effective_on
            )
            span.set_attribute("docubridge.active_fact_count", len(winners))
            span.set_attribute("docubridge.conflict_count", len(conflicts))
            return winners, conflicts

    def broker_view(
        self,
        entity_id: str,
        *,
        learned_as_of: datetime | None = None,
        required_fields: tuple[str, ...] = (
            "premium",
            "deductible",
            "coverage_limits",
            "exclusions",
        ),
    ) -> BrokerView:
        """Zone 4 Role-Based Broker View: quotes, gaps, conflicts & source traces."""
        with trace_step(
            "docubridge.consolidation.role_based_view",
            {
                "docubridge.zone": "4_role_based_business_views",
                "docubridge.view_name": "v_broker_placement",
                "docubridge.entity_id": entity_id,
            },
        ) as span:
            as_of = learned_as_of or datetime.now(UTC)
            all_facts = self.list_facts(entity_id, learned_as_of=learned_as_of)
            active_facts, conflicts = resolve_active_facts(
                all_facts, learned_as_of=learned_as_of
            )

            # Group by doc_id to show each quote/submission row
            by_doc: dict[str, list[ConsolidatedFact]] = {}
            for f in all_facts:
                by_doc.setdefault(f.doc_id, []).append(f)

            quotes: list[BrokerQuoteRow] = []
            for doc_id, doc_facts in by_doc.items():
                first = doc_facts[0]
                kv: dict[str, str] = {}
                traces: dict[str, SourceTrace] = {}
                for df in doc_facts:
                    leaf = df.field_path.split(".")[-1]
                    kv[leaf] = df.field_value
                    traces[leaf] = df.trace()
                quotes.append(
                    BrokerQuoteRow(
                        issuer=first.issuer or first.doc_id,
                        doc_id=doc_id,
                        doctype=first.doctype,
                        premium=kv.get("premium"),
                        deductible=kv.get("deductible"),
                        coverage_limits=kv.get("coverage_limits")
                        or kv.get("coverage_limit"),
                        exclusions=kv.get("exclusions"),
                        effective_from=max(f.effective_from for f in doc_facts),
                        recorded_at=max(f.recorded_at for f in doc_facts),
                        traces=traces,
                    )
                )

            present_leaves = {f.field_path.split(".")[-1] for f in active_facts}
            gaps = [req for req in required_fields if req not in present_leaves]
            span.set_attribute("docubridge.quote_count", len(quotes))
            span.set_attribute("docubridge.conflict_count", len(conflicts))
            return BrokerView(
                entity_id=entity_id,
                as_of_learned=as_of,
                quotes=quotes,
                coverage_gaps=gaps,
                conflicts=conflicts,
                financials=compute_financial_summary(active_facts),
            )

    def claims_view(
        self,
        entity_id: str,
        *,
        loss_date: datetime,
        learned_as_of: datetime | None = None,
    ) -> ClaimsView:
        """Zone 4 Role-Based Claims View: active limits & deductibles on ``loss_date``."""
        with trace_step(
            "docubridge.consolidation.role_based_view",
            {
                "docubridge.zone": "4_role_based_business_views",
                "docubridge.view_name": "v_claims_coverage",
                "docubridge.entity_id": entity_id,
                "docubridge.loss_date": loss_date.isoformat(),
            },
        ) as span:
            as_of = learned_as_of or datetime.now(UTC)
            active_facts, _ = self.query_as_of(
                entity_id, learned_as_of=learned_as_of, effective_on=loss_date
            )
            items = [
                ClaimsActiveCoverage(
                    field_path=f.field_path,
                    field_value=f.field_value,
                    numeric_value=f.numeric_value,
                    issuer=f.issuer,
                    doc_id=f.doc_id,
                    doctype=f.doctype,
                    effective_from=f.effective_from,
                    recorded_at=f.recorded_at,
                    trace=f.trace(),
                )
                for f in active_facts
            ]
            summary = compute_financial_summary(active_facts)
            endorsement_docs = {
                f.doc_id
                for f in active_facts
                if source_priority_for_doctype(f.doctype) >= 40
            }
            span.set_attribute("docubridge.active_coverage_count", len(items))
            span.set_attribute("docubridge.endorsement_count", len(endorsement_docs))
            return ClaimsView(
                entity_id=entity_id,
                loss_date=loss_date,
                as_of_learned=as_of,
                active_coverages=items,
                active_limit_total=summary.total_coverage_limit,
                active_deductible_total=summary.total_deductible,
                endorsement_count=len(endorsement_docs),
            )

    def underwriter_view(
        self,
        entity_id: str,
        *,
        learned_as_of: datetime | None = None,
    ) -> UnderwriterView:
        """Zone 4 Role-Based Underwriter View: insured values, exposure & risk ratios."""
        with trace_step(
            "docubridge.consolidation.role_based_view",
            {
                "docubridge.zone": "4_role_based_business_views",
                "docubridge.view_name": "v_underwriter_exposure",
                "docubridge.entity_id": entity_id,
            },
        ) as span:
            as_of = learned_as_of or datetime.now(UTC)
            active_facts, _ = self.query_as_of(entity_id, learned_as_of=learned_as_of)
            items = [
                UnderwriterExposureItem(
                    field_path=f.field_path,
                    field_value=f.field_value,
                    numeric_value=f.numeric_value,
                    doc_id=f.doc_id,
                    doctype=f.doctype,
                    flagged_for_review=f.flagged_for_review,
                    trace=f.trace(),
                )
                for f in active_facts
            ]
            flagged_count = sum(1 for item in items if item.flagged_for_review)
            span.set_attribute("docubridge.exposure_item_count", len(items))
            span.set_attribute("docubridge.flagged_items_count", flagged_count)
            return UnderwriterView(
                entity_id=entity_id,
                as_of_learned=as_of,
                exposure_items=items,
                financials=compute_financial_summary(active_facts),
                flagged_items_count=flagged_count,
            )

    def export_bigquery_sync_batch(
        self,
        entity_id: str | None = None,
        *,
        since_recorded_at: datetime | None = None,
    ) -> BigQuerySyncBatch:
        """Export append-only CDC rows from AlloyDB to BigQuery Open Iceberg tables."""
        with trace_step(
            "docubridge.consolidation.bigquery_iceberg_sync",
            {
                "docubridge.zone": "4_bigquery_analytics_iceberg",
                "docubridge.entity_id": entity_id,
                "docubridge.iceberg_table": "docubridge_lakehouse.bq_operational_facts_iceberg",
            },
        ) as span:
            facts = self.list_facts(entity_id, since_recorded_at=since_recorded_at)
            records = [
                BigQuerySyncRecord(
                    sync_id=f"sync-{f.fact_id}",
                    entity_id=f.entity_id,
                    fact_id=f.fact_id,
                    doc_id=f.doc_id,
                    doctype=f.doctype,
                    issuer=f.issuer,
                    field_path=f.field_path,
                    field_value=f.field_value,
                    numeric_value=f.numeric_value,
                    currency=f.currency,
                    recorded_at=f.recorded_at,
                    effective_from=f.effective_from,
                    vault_uri=f.vault_uri,
                    page_number=f.source_anchor.page_number if f.source_anchor else 1,
                )
                for f in facts
            ]
            watermark = max((r.recorded_at for r in records), default=since_recorded_at)
            span.set_attribute("docubridge.cdc_row_count", len(records))
            return BigQuerySyncBatch(
                batch_id=uuid.uuid4().hex,
                exported_at=datetime.now(UTC),
                row_count=len(records),
                watermark_recorded_at=watermark,
                records=records,
            )

    def close(self) -> None:  # noqa: B027
        """Release any underlying database connections (no-op for in-memory)."""


class InMemoryOperationalStore(OperationalStore):
    """In-memory bi-temporal operational store (append-only)."""

    def __init__(self) -> None:
        self._facts: list[ConsolidatedFact] = []
        self._seen_ids: set[str] = set()

    def append_facts(self, facts: list[ConsolidatedFact]) -> int:
        """Append extracted canonical fact rows to the in-memory operational store."""
        for f in facts:
            if f.fact_id in self._seen_ids:
                raise ValueError(
                    f"ConsolidatedFact {f.fact_id!r} already exists; "
                    "OperationalStore is append-only (no overwrites)"
                )
            self._seen_ids.add(f.fact_id)
            self._facts.append(f)
        return len(facts)

    def list_facts(
        self,
        entity_id: str | None = None,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
        since_recorded_at: datetime | None = None,
    ) -> list[ConsolidatedFact]:
        """List canonical fact rows filtered by entity ID, domain, document ID, or fact key."""
        out: list[ConsolidatedFact] = []
        for f in self._facts:
            if entity_id is not None and f.entity_id != entity_id:
                continue
            if learned_as_of is not None and f.recorded_at > learned_as_of:
                continue
            if effective_on is not None and f.effective_from > effective_on:
                continue
            if since_recorded_at is not None and f.recorded_at <= since_recorded_at:
                continue
            out.append(f)
        return out


class SqlOperationalStore(OperationalStore):
    """AlloyDB / Cloud SQL Postgres / SQLite bi-temporal operational store.

    Uses the same lazy daemon-thread + async SQLAlchemy engine pattern as
    ``bridge.exchange_store.SqlExchangeStore`` so callers stay synchronous while
    riding ``sqlite+aiosqlite`` locally and ``postgresql+asyncpg`` on GCP.
    """

    def __init__(self, dsn: str) -> None:
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("SqlOperationalStore requires a non-empty database DSN")
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlOperationalStore refuses an in-memory SQLite DSN; use "
                "InMemoryOperationalStore or a file/Postgres DSN"
            )
        self.dsn = cleaned
        self._init_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._table: Any = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._engine is not None:
                return
            from sqlalchemy import (
                Boolean,
                Column,
                Index,
                Integer,
                MetaData,
                String,
                Table,
                Text,
            )
            from sqlalchemy.ext.asyncio import create_async_engine

            meta = MetaData()
            self._table = Table(
                FACTS_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("fact_id", String(64), unique=True, nullable=False, index=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("context_id", String(128), nullable=False, index=True),
                Column("doc_id", String(128), nullable=False),
                Column("doctype", String(128), nullable=False),
                Column("issuer", String(128), nullable=True),
                Column("field_path", String(256), nullable=False, index=True),
                Column("field_value", Text, nullable=False),
                Column("numeric_value", String(64), nullable=True),
                Column("currency", String(16), nullable=False),
                Column("source_priority", Integer, nullable=False),
                Column("recorded_at", String(64), nullable=False, index=True),
                Column("effective_from", String(64), nullable=False, index=True),
                Column("version", Integer, nullable=False),
                Column("source_anchor_json", Text, nullable=True),
                Column("vault_uri", Text, nullable=True),
                Column("flagged_for_review", Boolean, nullable=False, default=False),
                Column("skill_version", String(64), nullable=False, default="1.0.0"),
                Column(
                    "skill_revision_id",
                    String(128),
                    nullable=False,
                    default="rev-v1-0-baseline",
                ),
                Column("skill_urn", String(256), nullable=True),
                Column(
                    "process_skill_revision_id",
                    String(128),
                    nullable=False,
                    default="rev-v1-0-baseline",
                ),
                Column("compiled_rule_ids_json", Text, nullable=True),
                Index(
                    "ix_operational_facts_bitemporal",
                    "entity_id",
                    "field_path",
                    "effective_from",
                    "recorded_at",
                ),
            )
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                name="bridge-operational-store",
                daemon=True,
            )
            thread.start()
            engine = create_async_engine(self.dsn)

            async def _create() -> None:
                from sqlalchemy import inspect, text

                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)
                    existing_cols = await conn.run_sync(
                        lambda sync_conn: {
                            c["name"]
                            for c in inspect(sync_conn).get_columns(FACTS_TABLE_NAME)
                        }
                    )
                    migrations = (
                        (
                            "skill_version",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN skill_version VARCHAR(64) NOT NULL DEFAULT '1.0.0'",
                        ),
                        (
                            "skill_revision_id",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN skill_revision_id VARCHAR(128) NOT NULL DEFAULT 'rev-v1-0-baseline'",
                        ),
                        (
                            "skill_urn",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN skill_urn VARCHAR(256)",
                        ),
                        (
                            "process_skill_revision_id",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN process_skill_revision_id VARCHAR(128) NOT NULL DEFAULT 'rev-v1-0-baseline'",
                        ),
                        (
                            "compiled_rule_ids_json",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN compiled_rule_ids_json TEXT",
                        ),
                    )
                    for col_name, ddl in migrations:
                        if col_name not in existing_cols:
                            await conn.execute(text(ddl))

            fut = asyncio.run_coroutine_threadsafe(_create(), loop)
            fut.result(timeout=_OP_TIMEOUT_S)
            self._loop = loop
            self._thread = thread
            self._engine = engine

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=_OP_TIMEOUT_S)

    def append_facts(self, facts: list[ConsolidatedFact]) -> int:
        """Persist extracted canonical fact rows into the SQL operational store."""
        if not facts:
            return 0
        self._ensure_ready()
        table = self._table
        engine = self._engine

        rows = [
            {
                "fact_id": f.fact_id,
                "entity_id": f.entity_id,
                "context_id": f.context_id,
                "doc_id": f.doc_id,
                "doctype": f.doctype,
                "issuer": f.issuer,
                "field_path": f.field_path,
                "field_value": f.field_value,
                "numeric_value": f.numeric_value,
                "currency": f.currency,
                "source_priority": f.source_priority,
                "recorded_at": _utc_iso(f.recorded_at),
                "effective_from": _utc_iso(f.effective_from),
                "version": f.version,
                "source_anchor_json": (
                    json.dumps(f.source_anchor.model_dump(mode="json"))
                    if f.source_anchor is not None
                    else None
                ),
                "vault_uri": f.vault_uri,
                "flagged_for_review": f.flagged_for_review,
                "skill_version": f.skill_version,
                "skill_revision_id": f.skill_revision_id,
                "skill_urn": f.skill_urn,
                "process_skill_revision_id": f.process_skill_revision_id,
                "compiled_rule_ids_json": (
                    json.dumps(list(f.compiled_rule_ids))
                    if f.compiled_rule_ids
                    else None
                ),
            }
            for f in facts
        ]

        async def _insert() -> int:
            async with engine.begin() as conn:
                await conn.execute(table.insert(), rows)
            return len(rows)

        return self._run(_insert())

    def list_facts(
        self,
        entity_id: str | None = None,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
        since_recorded_at: datetime | None = None,
    ) -> list[ConsolidatedFact]:
        """Query canonical fact rows from SQL filtered by entity, domain, document, or key."""
        self._ensure_ready()
        from sqlalchemy import select

        table = self._table
        engine = self._engine

        async def _query() -> list[ConsolidatedFact]:
            stmt = select(table).order_by(table.c.seq.asc())
            if entity_id is not None:
                stmt = stmt.where(table.c.entity_id == entity_id)
            if learned_as_of is not None:
                stmt = stmt.where(table.c.recorded_at <= _utc_iso(learned_as_of))
            if effective_on is not None:
                stmt = stmt.where(table.c.effective_from <= _utc_iso(effective_on))
            if since_recorded_at is not None:
                stmt = stmt.where(table.c.recorded_at > _utc_iso(since_recorded_at))
            async with engine.connect() as conn:
                res = await conn.execute(stmt)
                out: list[ConsolidatedFact] = []
                for row in res.mappings():
                    anchor_raw = row["source_anchor_json"]
                    anchor = (
                        SourceAnchor.model_validate(json.loads(anchor_raw))
                        if anchor_raw
                        else None
                    )
                    out.append(
                        ConsolidatedFact(
                            fact_id=row["fact_id"],
                            entity_id=row["entity_id"],
                            context_id=row["context_id"],
                            doc_id=row["doc_id"],
                            doctype=row["doctype"],
                            issuer=row["issuer"],
                            field_path=row["field_path"],
                            field_value=row["field_value"],
                            numeric_value=row["numeric_value"],
                            currency=row["currency"],
                            source_priority=row["source_priority"],
                            recorded_at=datetime.fromisoformat(row["recorded_at"]),
                            effective_from=datetime.fromisoformat(
                                row["effective_from"]
                            ),
                            version=row["version"],
                            source_anchor=anchor,
                            vault_uri=row["vault_uri"],
                            flagged_for_review=bool(row["flagged_for_review"]),
                            skill_version=row.get("skill_version") or "1.0.0",
                            skill_revision_id=(
                                row.get("skill_revision_id") or "rev-v1-0-baseline"
                            ),
                            skill_urn=row.get("skill_urn"),
                            process_skill_revision_id=(
                                row.get("process_skill_revision_id")
                                or "rev-v1-0-baseline"
                            ),
                            compiled_rule_ids=(
                                tuple(json.loads(row["compiled_rule_ids_json"]))
                                if row.get("compiled_rule_ids_json")
                                else ()
                            ),
                        )
                    )
                return out

        return self._run(_query())

    def close(self) -> None:
        """Dispose of the underlying SQLAlchemy engine and connection pool."""
        with self._init_lock:
            if self._engine is None:
                return
            engine, loop, thread = self._engine, self._loop, self._thread
            self._engine = None
            self._loop = None
            self._thread = None
        if loop is not None:
            fut = asyncio.run_coroutine_threadsafe(engine.dispose(), loop)
            fut.result(timeout=_OP_TIMEOUT_S)
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=_OP_TIMEOUT_S)
        _SQL_STORE_CACHE.pop(self.dsn, None)


_SQL_STORE_CACHE: dict[str, SqlOperationalStore] = {}
_SQL_CACHE_LOCK = threading.Lock()


def operational_store_from_env() -> OperationalStore:
    """Resolve the OperationalStore from env: AlloyDB/Cloud SQL when a DSN is configured,
    else :class:`InMemoryOperationalStore` in local mode (refuses in-memory on GCP)."""
    dsn = (
        os.environ.get(OPERATIONAL_DB_ENV, "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if dsn:
        with _SQL_CACHE_LOCK:
            cached = _SQL_STORE_CACHE.get(dsn)
            if cached is None:
                cached = SqlOperationalStore(dsn)
                _SQL_STORE_CACHE[dsn] = cached
        return cached
    if os.environ.get("BRIDGE_SEAM_MODE", "").strip().lower() == "gcp":
        raise RuntimeError(
            "GCP seam mode requires DATABASE_URL or BRIDGE_OPERATIONAL_DB_URL for "
            "OperationalStore; refusing an in-memory fallback (the session-affinity trap)."
        )
    return InMemoryOperationalStore()
