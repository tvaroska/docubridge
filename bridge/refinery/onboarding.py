"""Example-driven doctype onboarding and paired target projection induction (P8-5).

Synthesizes a ``DRAFT`` ``OntologyEntitySpec`` and ``.okf.md`` semantic-entity bundle
from 1-5 sample documents plus optional paired target output rows, using
``gemini-4-argon`` when live credentials/client are configured and a deterministic
structural inducer in local/offline environments, gated by the 3-Tier Refinery Assay.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bridge.operational_store.consolidation_rules import parse_decimal_money
from bridge.refinery.okf_loader import render_okf_markdown
from contract.refinery import (
    ContrastiveDescription,
    OntologyEdge,
    OntologyEntitySpec,
    OntologyFieldSpec,
    OntologyRevision,
    RefineryAssayReport,
    RefineryProposal,
    RefineryProposalKind,
    RefineryProposalStatus,
)

ONBOARDING_REASONING_MODEL = "gemini-4-argon"
LIVE_LLM_ENV = "BRIDGE_REFINERY_LIVE_LLM"

_KV_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _/\-&()]{1,64}?)\s*:\s*(.+?)\s*$")
_MONEY_SYMBOL_RE = re.compile(r"(?:\$|EUR|GBP|USD|CAD)\s*\d|\d[\d,]*\.\d{2}\b", re.I)

_METRIC_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("premium", "debt_service", "installment", "contribution", "rate_amount"),
        "total_premium",
    ),
    (
        (
            "deductible",
            "retention",
            "sir",
            "origination_fee",
            "closing_fee",
            "extension_fee",
            "copay",
            "seed",
        ),
        "total_deductible",
    ),
    (
        (
            "limit",
            "sublimit",
            "loan_amount",
            "principal",
            "facility",
            "maximum_benefit",
        ),
        "total_coverage_limit",
    ),
    (
        ("insured_value", "tiv", "appraised_value", "collateral", "property_value"),
        "total_insured_value",
    ),
)


class SampleDocumentExample(BaseModel):
    """One sample document snippet or structured field map for doctype induction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = "sample-1"
    filename: str = ""
    raw_text: str = ""
    extracted_fields: dict[str, str] = Field(default_factory=dict)
    section_hints: dict[str, str] = Field(default_factory=dict)


class PairedTargetExample(BaseModel):
    """Optional target projection row paired with a sample document by `sample_id`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = "sample-1"
    target_record: dict[str, str] = Field(default_factory=dict)
    target_schema_name: str = "target_projection"


class DoctypeOnboardingResult(BaseModel):
    """Output bundle from `onboard_doctype_from_examples`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal: RefineryProposal
    assay_report: RefineryAssayReport
    draft_revision: OntologyRevision
    entity_spec: OntologyEntitySpec
    okf_markdown: str
    target_projection_hints: dict[str, str] = Field(default_factory=dict)
    induced_by_model: str = "deterministic-structural-inducer"


def _to_snake_field(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (label or "").strip().lower()).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"field_{cleaned}" if cleaned else "extracted_value"
    return cleaned


def _slugify_entity_id(raw_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (raw_id or "").strip().lower()).strip("-")
    if not slug:
        raise ValueError("entity_id must contain at least one alphanumeric character")
    return slug


def _coerce_sample(
    item: SampleDocumentExample | Mapping[str, Any],
    idx: int,
) -> SampleDocumentExample:
    if isinstance(item, SampleDocumentExample):
        return item
    payload = dict(item)
    payload.setdefault("sample_id", f"sample-{idx}")
    if "extracted_fields" in payload and isinstance(
        payload["extracted_fields"], Mapping
    ):
        payload["extracted_fields"] = {
            str(k): str(v)
            for k, v in payload["extracted_fields"].items()
            if v is not None
        }
    return SampleDocumentExample.model_validate(payload)


def _coerce_paired(
    item: PairedTargetExample | Mapping[str, Any],
    idx: int,
) -> PairedTargetExample:
    if isinstance(item, PairedTargetExample):
        return item
    payload = dict(item)
    payload.setdefault("sample_id", f"sample-{idx}")
    if "target_record" not in payload:
        reserved = {"sample_id", "target_schema_name", "target_record"}
        extra_record = {
            str(k): str(v)
            for k, v in list(payload.items())
            if k not in reserved and v is not None
        }
        for k in list(payload.keys()):
            if k not in reserved:
                payload.pop(k)
        payload["target_record"] = extra_record
    elif isinstance(payload["target_record"], Mapping):
        payload["target_record"] = {
            str(k): str(v) for k, v in payload["target_record"].items() if v is not None
        }
    return PairedTargetExample.model_validate(payload)


def _extract_sample_surface_pairs(
    sample: SampleDocumentExample,
) -> list[tuple[str, str, str]]:
    """Return `(raw_label, value, section_anchor)` tuples discovered on one sample."""
    triples: list[tuple[str, str, str]] = []
    seen_norm: set[str] = set()
    for raw_k, raw_v in sample.extracted_fields.items():
        norm = _to_snake_field(raw_k)
        if not str(raw_v).strip():
            continue
        seen_norm.add(norm)
        section = (
            sample.section_hints.get(raw_k)
            or sample.section_hints.get(norm)
            or "Summary schedule or key-terms header block."
        )
        triples.append((raw_k.strip(), str(raw_v).strip(), section))

    current_heading = "Document header and schedule body."
    for raw_line in sample.raw_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or (
            stripped.isupper() and len(stripped) <= 60 and ":" not in stripped
        ):
            current_heading = f"Section '{stripped.lstrip('#').strip()}'."
            continue
        m = _KV_LINE_RE.match(stripped)
        if not m:
            continue
        label, val = m.group(1).strip(), m.group(2).strip()
        norm = _to_snake_field(label)
        if norm in seen_norm or not val:
            continue
        seen_norm.add(norm)
        section = (
            sample.section_hints.get(label)
            or sample.section_hints.get(norm)
            or current_heading
        )
        triples.append((label, val, section))
    return triples


def _values_equivalent(left: str, right: str) -> bool:
    """Check whether a sample surface value and target projection cell represent the same value."""
    l_clean = re.sub(r"\s+", " ", left.strip()).lower()
    r_clean = re.sub(r"\s+", " ", right.strip()).lower()
    if not l_clean or not r_clean:
        return False
    if l_clean == r_clean:
        return True
    l_dec = parse_decimal_money(left)
    r_dec = parse_decimal_money(right)
    return l_dec is not None and r_dec is not None and l_dec == r_dec


def _infer_metric_bucket(
    field_name: str, values: Sequence[str]
) -> tuple[bool, str | None]:
    """Classify whether `field_name` is monetary and which FinancialSummary metric it feeds."""
    lower = field_name.lower()
    if lower in (
        "currency",
        "effective_date",
        "expiry_date",
        "interest_rate",
        "coinsurance_pct",
    ):
        return False, None
    if lower.endswith(("_pct", "_percent", "_date", "_id", "_code", "_year", "_days")):
        return False, None

    matched_bucket: str | None = None
    for keywords, bucket in _METRIC_KEYWORDS:
        if any(kw in lower for kw in keywords):
            matched_bucket = bucket
            break

    has_money_val = any(
        bool(_MONEY_SYMBOL_RE.search(v)) and parse_decimal_money(v) is not None
        for v in values
    )
    if matched_bucket is not None and (
        has_money_val or any(parse_decimal_money(v) is not None for v in values)
    ):
        return True, matched_bucket
    if has_money_val and any(
        tok in lower for tok in ("amount", "value", "cost", "charge", "fee", "total")
    ):
        return True, "total_premium"
    return False, None


_ARGON_ONBOARDING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "entity_description": {
            "type": "OBJECT",
            "properties": {
                "what_it_is": {"type": "STRING"},
                "where_to_read": {"type": "STRING"},
                "distinguish_from": {"type": "STRING"},
            },
            "required": ["what_it_is"],
        },
        "aliases": {"type": "ARRAY", "items": {"type": "STRING"}},
        "fields": {"type": "OBJECT"},
        "target_projection_hints": {"type": "OBJECT"},
    },
    "required": ["entity_description", "fields"],
}


def _call_live_argon_inducer(  # noqa: PLR0913 - keyword-only Argon induction inputs
    *,
    client: object,
    entity_id: str,
    context_set: str,
    samples: Sequence[SampleDocumentExample],
    paired: Sequence[PairedTargetExample],
    spine_entity_id: str | None,
) -> dict[str, Any]:
    """Invoke `gemini-4-argon` to synthesize contrastive entity/field descriptions."""
    active_client: Any = client
    if active_client is None:
        from google import genai

        active_client = genai.Client()

    prompt_payload = {
        "task": "induce_okf_doctype_spec",
        "entity_id": entity_id,
        "context_set": context_set,
        "spine_entity_id": spine_entity_id,
        "samples": [s.model_dump(mode="json") for s in samples],
        "paired_target_examples": [p.model_dump(mode="json") for p in paired],
        "instructions": (
            "Return JSON with keys: entity_description ({what_it_is, where_to_read, "
            "distinguish_from}), aliases (list[str]), fields (dict[name, {required, "
            "monetary, feeds_metric, synonyms, what_it_is, where_to_read, "
            "distinguish_from}]), and target_projection_hints (dict[field_name, target_col])."
        ),
    }
    response = active_client.models.generate_content(
        model=ONBOARDING_REASONING_MODEL,
        contents=[json.dumps(prompt_payload)],
        config={
            "response_mime_type": "application/json",
            "response_schema": _ARGON_ONBOARDING_RESPONSE_SCHEMA,
        },
    )
    raw = (getattr(response, "text", None) or "").strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("gemini-4-argon onboarding response must be a JSON object")
    return parsed


def _record_paired_projection(
    paired_row: PairedTargetExample | None,
    fname: str,
    val: str,
    labels: list[str],
    projection_hints: dict[str, str],
) -> None:
    if paired_row is None:
        return
    for t_col, t_val in paired_row.target_record.items():
        if _to_snake_field(t_col) == fname or _values_equivalent(val, t_val):
            projection_hints[fname] = t_col
            if t_col not in labels:
                labels.append(t_col)


def _fallback_stats_from_paired(
    coerced_paired: Sequence[PairedTargetExample],
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, str],
    dict[str, int],
    dict[str, str],
]:
    field_labels: dict[str, list[str]] = {}
    field_values: dict[str, list[str]] = {}
    field_sections: dict[str, str] = {}
    field_presence_count: dict[str, int] = {}
    projection_hints: dict[str, str] = {}
    for p in coerced_paired:
        for t_col, t_val in p.target_record.items():
            fname = _to_snake_field(t_col)
            labels = field_labels.setdefault(fname, [])
            if t_col not in labels:
                labels.append(t_col)
            field_values.setdefault(fname, []).append(t_val)
            field_sections.setdefault(fname, "Structured schedule row.")
            field_presence_count[fname] = field_presence_count.get(fname, 0) + 1
            projection_hints[fname] = t_col
    return (
        field_labels,
        field_values,
        field_sections,
        field_presence_count,
        projection_hints,
    )


def _collect_sample_field_stats(
    coerced_samples: Sequence[SampleDocumentExample],
    coerced_paired: Sequence[PairedTargetExample],
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, str],
    dict[str, int],
    dict[str, str],
]:
    paired_by_id = {p.sample_id: p for p in coerced_paired}
    field_labels: dict[str, list[str]] = {}
    field_values: dict[str, list[str]] = {}
    field_sections: dict[str, str] = {}
    field_presence_count: dict[str, int] = {}
    projection_hints: dict[str, str] = {}

    for idx, sample in enumerate(coerced_samples):
        paired_row = paired_by_id.get(sample.sample_id) or (
            coerced_paired[idx] if idx < len(coerced_paired) else None
        )
        seen_in_sample: set[str] = set()
        for raw_label, val, section in _extract_sample_surface_pairs(sample):
            fname = _to_snake_field(raw_label)
            labels = field_labels.setdefault(fname, [])
            if raw_label not in labels:
                labels.append(raw_label)
            field_values.setdefault(fname, []).append(val)
            field_sections.setdefault(fname, section)
            if fname not in seen_in_sample:
                seen_in_sample.add(fname)
                field_presence_count[fname] = field_presence_count.get(fname, 0) + 1
            _record_paired_projection(paired_row, fname, val, labels, projection_hints)

    if not field_labels and coerced_paired:
        return _fallback_stats_from_paired(coerced_paired)

    return (
        field_labels,
        field_values,
        field_sections,
        field_presence_count,
        projection_hints,
    )


def _merge_field_synonyms(
    labels: Sequence[str],
    llm_field: Mapping[str, Any] | None,
    fname: str,
) -> tuple[str, ...]:
    syns = list(labels)
    if llm_field is not None and isinstance(llm_field.get("synonyms"), list):
        for s in llm_field["synonyms"]:
            cleaned = str(s).strip()
            if cleaned and cleaned not in syns:
                syns.append(cleaned)
    return tuple(s for s in syns if s.lower() != fname.lower()) or tuple(syns[:1])


def _build_single_induced_field(  # noqa: PLR0913 - internal field builder consolidating per-field stats
    *,
    slug: str,
    fname: str,
    labels: Sequence[str],
    vals: Sequence[str],
    section: str,
    presence_count: int,
    sample_count: int,
    projection_hint: str | None,
    negative_targets: str,
    llm_field: Mapping[str, Any] | None,
) -> tuple[OntologyFieldSpec, OntologyEdge | None, bool]:
    is_money, metric_bucket = _infer_metric_bucket(fname, vals)
    if llm_field is not None:
        if "monetary" in llm_field:
            is_money = bool(llm_field["monetary"])
        if llm_field.get("feeds_metric") in (
            "total_premium",
            "total_deductible",
            "total_coverage_limit",
            "total_insured_value",
        ):
            metric_bucket = str(llm_field["feeds_metric"])
            is_money = True
    if not is_money:
        metric_bucket = None

    dedup_syns = _merge_field_synonyms(labels, llm_field, fname)
    is_req = (
        bool(llm_field["required"])
        if llm_field is not None and "required" in llm_field
        else presence_count == sample_count
    )

    target_note = (
        f" Maps to target column '{projection_hint}'." if projection_hint else ""
    )
    default_what = f"Extracted '{fname}' attribute for `{slug}` exact text as printed.{target_note}"
    default_dist = (
        f"Do NOT confuse with non-binding worksheet estimates or superseded values "
        f"on {negative_targets}."
    )
    fdesc = (
        ContrastiveDescription(
            what_it_is=str(llm_field.get("what_it_is") or default_what).strip(),
            where_to_read=str(llm_field.get("where_to_read") or section).strip(),
            distinguish_from=str(
                llm_field.get("distinguish_from") or default_dist
            ).strip(),
        )
        if llm_field is not None
        else ContrastiveDescription(
            what_it_is=default_what,
            where_to_read=section,
            distinguish_from=default_dist,
        )
    )

    spec = OntologyFieldSpec(
        name=fname,
        field_type="string",
        required=is_req,
        synonyms=dedup_syns,
        description=fdesc,
        feeds_metric=metric_bucket,
        monetary=is_money,
    )
    edge = (
        OntologyEdge(
            edge_id=f"edge-{slug}-feeds-{fname.replace('_', '-')}",
            source_id=slug,
            relation="FEEDS_METRIC",
            target_id=f"FinancialSummary.{metric_bucket}",
            on_fields=(fname,),
            metric_operation="SUM_ACTIVE_DECIMAL",
            effective_from_revision="ont-rev-v1-2-draft",
        )
        if is_money and metric_bucket
        else None
    )
    return spec, edge, is_money


def _build_structural_edges(  # noqa: PLR0913 - internal structural edge builder
    *,
    slug: str,
    spine_entity_id: str | None,
    baseline_revision: OntologyRevision,
    supersedes_spine: bool,
    source_priority: int,
    built_fields: Mapping[str, OntologyFieldSpec],
) -> tuple[list[OntologyEdge], int]:
    structural_edges: list[OntologyEdge] = []
    effective_priority = source_priority
    cleaned_spine = (spine_entity_id or "").strip()
    if not cleaned_spine:
        return structural_edges, effective_priority

    spine_spec = baseline_revision.entities.get(cleaned_spine)
    if spine_spec is not None and supersedes_spine:
        effective_priority = max(source_priority, spine_spec.source_priority + 10)
        shared_fields = tuple(
            f for f in built_fields if f in spine_spec.fields and f != "currency"
        ) or tuple(sorted(built_fields.keys())[:3])
        structural_edges.append(
            OntologyEdge(
                edge_id=f"edge-{slug}-supersedes-{cleaned_spine}",
                source_id=slug,
                relation="SUPERSEDES",
                target_id=cleaned_spine,
                priority_rank=effective_priority,
                on_fields=shared_fields,
                condition_expr="source.effective_from >= target.effective_from",
                effective_from_revision="ont-rev-v1-2-draft",
            )
        )
    else:
        structural_edges.append(
            OntologyEdge(
                edge_id=f"edge-{slug}-distinct-from-{cleaned_spine}",
                source_id=slug,
                relation="DISTINCT_FROM",
                target_id=cleaned_spine,
                effective_from_revision="ont-rev-v1-2-draft",
            )
        )
    return structural_edges, effective_priority


def _build_entity_description_and_aliases(  # noqa: PLR0913 - internal description/alias assembler
    *,
    slug: str,
    context_set: str,
    sample_count: int,
    negative_targets: str,
    aliases: Sequence[str],
    llm_overrides: Mapping[str, Any],
) -> tuple[ContrastiveDescription, tuple[str, ...]]:
    llm_ent_desc = llm_overrides.get("entity_description")
    if isinstance(llm_ent_desc, Mapping):
        ent_desc = ContrastiveDescription(
            what_it_is=str(
                llm_ent_desc.get("what_it_is")
                or f"Structured `{slug}` document entity induced from {sample_count} verified examples."
            ).strip(),
            where_to_read=str(
                llm_ent_desc.get("where_to_read")
                or "Title banner, issuer header, and primary structured schedule table."
            ).strip(),
            distinguish_from=str(
                llm_ent_desc.get("distinguish_from")
                or f"Distinguish strictly from {negative_targets}."
            ).strip(),
        )
    else:
        ent_desc = ContrastiveDescription(
            what_it_is=(
                f"Structured `{slug}` semantic document entity in context `{context_set}`, "
                f"induced from {sample_count} verified examples."
            ),
            where_to_read=(
                "Document title block, issuer header, and primary key-terms schedule."
            ),
            distinguish_from=(
                f"Do NOT confuse with {negative_targets} when classifying or extracting fields."
            ),
        )

    merged_aliases = list(aliases)
    raw_aliases = llm_overrides.get("aliases")
    if isinstance(raw_aliases, list):
        for a in raw_aliases:
            cleaned = str(a).strip()
            if cleaned and cleaned not in merged_aliases:
                merged_aliases.append(cleaned)
    return ent_desc, tuple(merged_aliases)


def build_onboarding_proposal(  # noqa: PLR0913 - keyword-only public doctype onboarding builder
    *,
    entity_id: str,
    samples: Sequence[SampleDocumentExample | Mapping[str, Any]],
    baseline_revision: OntologyRevision,
    paired_target_examples: Sequence[PairedTargetExample | Mapping[str, Any]] = (),
    spine_entity_id: str | None = None,
    context_set: str = "enterprise-document-intake",
    domain: str = "insurance",
    source_priority: int = 20,
    supersedes_spine: bool = True,
    aliases: Sequence[str] = (),
    client: object = None,
    use_live_llm: bool | None = None,
) -> tuple[RefineryProposal, OntologyEntitySpec, str, dict[str, str], str]:
    """Synthesize a candidate `RefineryProposal` and `.okf.md` bundle from 1-5 examples."""
    if not (1 <= len(samples) <= 5):
        raise ValueError(
            f"onboard_doctype_from_examples requires 1 to 5 samples; got {len(samples)}"
        )
    slug = _slugify_entity_id(entity_id)
    coerced_samples = [_coerce_sample(s, i + 1) for i, s in enumerate(samples)]
    coerced_paired = [
        _coerce_paired(p, i + 1) for i, p in enumerate(paired_target_examples)
    ]

    (
        field_labels,
        field_values,
        field_sections,
        field_presence_count,
        projection_hints,
    ) = _collect_sample_field_stats(coerced_samples, coerced_paired)
    if not field_labels:
        raise ValueError(
            "Samples did not contain any extractable key-value fields or structured attributes"
        )

    live_requested = (
        client is not None
        or use_live_llm is True
        or (use_live_llm is None and os.environ.get(LIVE_LLM_ENV, "").strip() == "1")
    )
    llm_overrides: dict[str, Any] = {}
    induced_by = "deterministic-structural-inducer"
    if live_requested:
        llm_overrides = _call_live_argon_inducer(
            client=client,
            entity_id=slug,
            context_set=context_set,
            samples=coerced_samples,
            paired=coerced_paired,
            spine_entity_id=spine_entity_id,
        )
        induced_by = ONBOARDING_REASONING_MODEL
        raw_hints = llm_overrides.get("target_projection_hints")
        if isinstance(raw_hints, Mapping):
            for k, v in raw_hints.items():
                projection_hints[str(k)] = str(v)

    sibling_ids = sorted(
        eid
        for eid, espec in baseline_revision.entities.items()
        if eid != slug and (espec.context_set == context_set or eid == spine_entity_id)
    )
    negative_targets = (
        ", ".join(f"`{sid}`" for sid in sibling_ids[:3])
        if sibling_ids
        else "unrelated base quotes, preliminary worksheets, or counterparty correspondence"
    )

    sample_count = len(coerced_samples)
    built_fields: dict[str, OntologyFieldSpec] = {}
    metric_edges: list[OntologyEdge] = []
    any_monetary = False
    raw_llm_fields = llm_overrides.get("fields")
    llm_fields: Mapping[str, Any] = (
        raw_llm_fields if isinstance(raw_llm_fields, Mapping) else {}
    )

    for fname in sorted(field_labels.keys()):
        raw_llm_f = llm_fields.get(fname)
        llm_f = raw_llm_f if isinstance(raw_llm_f, Mapping) else None
        fspec, edge, is_money = _build_single_induced_field(
            slug=slug,
            fname=fname,
            labels=field_labels[fname],
            vals=field_values.get(fname, []),
            section=field_sections.get(
                fname, "Primary schedule table or key-terms summary block."
            ),
            presence_count=field_presence_count.get(fname, 0),
            sample_count=sample_count,
            projection_hint=projection_hints.get(fname),
            negative_targets=negative_targets,
            llm_field=llm_f,
        )
        built_fields[fname] = fspec
        if edge is not None:
            metric_edges.append(edge)
        any_monetary = any_monetary or is_money

    if any_monetary and "currency" not in built_fields:
        built_fields["currency"] = OntologyFieldSpec(
            name="currency",
            field_type="string",
            required=False,
            synonyms=("ISO Currency", "Settlement Currency"),
            description=ContrastiveDescription(
                what_it_is="ISO 4217 3-letter currency code for monetary amounts in the document.",
                where_to_read="Schedule header or monetary column currency notation.",
                distinguish_from="Normalize currency symbols (such as '$') to ISO 4217 codes (e.g. USD).",
            ),
            monetary=False,
        )

    structural_edges, effective_priority = _build_structural_edges(
        slug=slug,
        spine_entity_id=spine_entity_id,
        baseline_revision=baseline_revision,
        supersedes_spine=supersedes_spine,
        source_priority=source_priority,
        built_fields=built_fields,
    )
    ent_desc, merged_aliases = _build_entity_description_and_aliases(
        slug=slug,
        context_set=context_set,
        sample_count=sample_count,
        negative_targets=negative_targets,
        aliases=aliases,
        llm_overrides=llm_overrides,
    )

    all_edges = (*structural_edges, *metric_edges)
    draft_rev_id = f"ont-rev-v1-2-draft-{slug[:24]}"
    spec = OntologyEntitySpec(
        entity_id=slug,
        okf_version="0.2",
        entry_type="semantic-entity",
        context_set=context_set,
        revision_id=draft_rev_id,
        source_priority=effective_priority,
        aliases=merged_aliases,
        declares_issuer=True,
        issuer_description="Canonical legal name of the issuing carrier, lender, or counterparty.",
        entity_description=ent_desc,
        fields=built_fields,
        edges=all_edges,
    )
    okf_md = render_okf_markdown(spec)

    proposal = RefineryProposal(
        proposal_id=f"prop-onboard-{uuid.uuid4().hex[:8]}",
        kind=RefineryProposalKind.PROPOSE_NEW_ENTITY,
        status=RefineryProposalStatus.CANDIDATE,
        target_entity_id=spine_entity_id or slug,
        new_entity_id=None,
        domain=domain,
        title=f"Onboard Doctype '{slug}' from {sample_count} Paired Example(s)",
        rationale=(
            f"Induced candidate OKF semantic-entity '{slug}' ({len(built_fields)} fields, "
            f"{len(all_edges)} ontology edges) from {sample_count} example(s) via {induced_by}."
        ),
        after_description=ent_desc,
        proposed_entity=spec,
        added_edges=all_edges,
        evidence_metrics={
            "sample_count": sample_count,
            "paired_target_count": len(coerced_paired),
            "target_projection_hints": dict(projection_hints),
            "spine_entity_id": spine_entity_id,
            "context_set": context_set,
            "induced_by_model": induced_by,
        },
    )
    return proposal, spec, okf_md, projection_hints, induced_by


__all__ = [
    "LIVE_LLM_ENV",
    "ONBOARDING_REASONING_MODEL",
    "DoctypeOnboardingResult",
    "PairedTargetExample",
    "SampleDocumentExample",
    "build_onboarding_proposal",
]
