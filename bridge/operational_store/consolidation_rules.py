"""Consolidation Rules Engine: 100% deterministic Decimal math, source priority, and active fact resolution."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from bridge.operational_store.models import (
    _MONEY_KEYS,
    _NUMBER_RE,
    ConsolidatedFact,
    FactConflict,
    FinancialSummary,
)
from bridge.telemetry import trace_step
from contract.enums import Disposition
from contract.wire import LedgerEntry


def _resolve_ontology_graph_context(
    doctype: str,
) -> tuple[int, str, str | None, Any]:
    """Query the Zone 6 Ontology Graph for doctype priority, revision ID, and edge ID."""
    try:
        from bridge.knowledge_catalog.catalog import _ensure_shared_ontology

        _, active_rev = _ensure_shared_ontology()
        rev_id = active_rev.revision_id
    except Exception:
        active_rev = None
        rev_id = "ont-rev-v1-1-baseline"

    dt = (doctype or "").strip().lower()
    if active_rev is not None and dt:
        # Exact match on entity_id or alias in the active OntologyRevision
        matched_spec = active_rev.entities.get(dt)
        if matched_spec is None:
            for spec in active_rev.entities.values():
                if dt in (a.lower() for a in spec.aliases):
                    matched_spec = spec
                    break
        if matched_spec is None:
            candidates = []
            for spec in active_rev.entities.values():
                for tok in (spec.entity_id.lower(), *(a.lower() for a in spec.aliases)):
                    if tok and tok in dt:
                        candidates.append((len(tok), spec.source_priority, spec))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                matched_spec = candidates[0][2]
        if matched_spec is not None:
            for edge in active_rev.edges:
                if edge.source_id == matched_spec.entity_id and edge.relation in (
                    "SUPERSEDES",
                    "SPLIT_FROM",
                ):
                    prio = (
                        edge.priority_rank
                        if edge.priority_rank is not None
                        else matched_spec.source_priority
                    )
                    return prio, rev_id, edge.edge_id, active_rev
            return (
                matched_spec.source_priority,
                rev_id,
                f"ontology:{matched_spec.entity_id}:priority-{matched_spec.source_priority}",
                active_rev,
            )

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
        return 40, rev_id, "ontology:tier-40-override", active_rev
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
        return 30, rev_id, "ontology:tier-30-agreement", active_rev
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
        return 20, rev_id, "ontology:tier-20-quote", active_rev
    return 10, rev_id, None, active_rev


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
    suffix_pos = match.end()
    rest = cleaned[suffix_pos:].strip().upper()
    if rest.startswith(("M", "MILLION")):
        val *= Decimal("1000000")
    elif rest.startswith(("K", "THOUSAND")):
        val *= Decimal("1000")
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def source_priority_for_doctype(doctype: str) -> int:
    """Deterministic source authority hierarchy resolved via the Zone 6 Ontology Graph.

    Higher number wins when effective dates are equal:
    - endorsement / amendment / addendum / closing-disclosure / fee-override: 40
    - bound policy / binder / credit-agreement / commitment-letter / loan-agreement / promissory-note: 30
    - quote / certificate / term-sheet / appraisal / rent-roll: 20
    - application / bill / statement / id / default: 10
    """
    prio, _, _, _ = _resolve_ontology_graph_context(doctype)
    return prio


def _is_ontology_monetary_field(field_name: str, active_rev: Any) -> bool:
    """Return True if a field is monetary in the active Ontology Graph or _MONEY_KEYS."""
    leaf = (field_name or "").strip().lower()
    if leaf in _MONEY_KEYS:
        return True
    if active_rev is not None:
        for spec in active_rev.entities.values():
            fspec = spec.fields.get(leaf)
            if fspec is not None and fspec.monetary:
                return True
    return False


def _resolve_field_metric_bucket(
    leaf: str,
    doctype: str,
    active_rev: Any,
) -> tuple[str | None, str | None]:
    """Resolve the target financial summary bucket and edge ID from [:FEEDS_METRIC] edges."""
    if active_rev is not None:
        for edge in active_rev.edges:
            if edge.relation == "FEEDS_METRIC" and leaf in (
                f.lower() for f in edge.on_fields
            ):
                return edge.target_id.split(".")[-1], edge.edge_id
        for spec in active_rev.entities.values():
            fspec = spec.fields.get(leaf)
            if fspec and fspec.feeds_metric:
                return fspec.feeds_metric, f"ontology:{spec.entity_id}:{leaf}"
    if leaf in ("premium", "annual_debt_service"):
        return "total_premium", "ontology:fallback:total_premium"
    if leaf in ("deductible", "origination_fee", "closing_fee", "rate_lock_fee"):
        return "total_deductible", "ontology:fallback:total_deductible"
    if leaf in (
        "coverage_limit",
        "coverage_limits",
        "sublimit",
        "loan_amount",
        "principal_amount",
        "facility_limit",
    ):
        return "total_coverage_limit", "ontology:fallback:total_coverage_limit"
    if leaf in (
        "insured_value",
        "total_insured_value",
        "appraised_value",
        "collateral_value",
    ):
        return "total_insured_value", "ontology:fallback:total_insured_value"
    return None, None


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
    priority, ont_rev_id, dt_edge_id, active_rev = _resolve_ontology_graph_context(
        entry.doctype
    )
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
        is_money = _is_ontology_monetary_field(k, active_rev)
        dec = parse_decimal_money(v) if is_money else None
        _, metric_edge_id = _resolve_field_metric_bucket(
            k.lower(), entry.doctype, active_rev
        )
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
                ontology_revision_id=ont_rev_id,
                consolidation_edge_id=metric_edge_id or dt_edge_id,
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
                is_money = _is_ontology_monetary_field(col, active_rev)
                dec = parse_decimal_money(val) if is_money else None
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
                        ontology_revision_id=ont_rev_id,
                        consolidation_edge_id=dt_edge_id,
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
        _, _, _, active_rev = _resolve_ontology_graph_context("")

        for f in facts:
            currency = f.currency or currency
            if f.numeric_value is None:
                continue
            val = Decimal(f.numeric_value)
            leaf = f.field_path.split(".")[-1].lower()
            bucket, _ = _resolve_field_metric_bucket(leaf, f.doctype, active_rev)
            if bucket == "total_premium":
                total_premium += val
            elif bucket == "total_deductible":
                total_deductible += val
            elif bucket == "total_coverage_limit":
                total_limit += val
            elif bucket == "total_insured_value":
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
