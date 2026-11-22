"""Consolidation Rules Engine: 100% deterministic Decimal math, source priority, and active fact resolution."""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

from bridge.operational_store.models import (
    _CURRENCY_SYMBOL_TO_ISO,
    _ISO_CURRENCY_CODES,
    _MONEY_KEYS,
    _NUMBER_RE,
    _PARENS_MONEY_RE,
    ConsolidatedFact,
    FactConflict,
    FinancialSummary,
    _utc_dt,
)
from bridge.telemetry import trace_step
from contract.enums import Disposition
from contract.wire import LedgerEntry

if TYPE_CHECKING:
    from contract.refinery import OntologyEntitySpec, OntologyRevision

_TIER_KEYWORDS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        40,
        "ontology:tier-40-override",
        (
            "endorsement",
            "amendment",
            "addendum",
            "closing-disclosure",
            "fee-override",
        ),
    ),
    (
        30,
        "ontology:tier-30-agreement",
        (
            "policy",
            "binder",
            "credit-agreement",
            "commitment-letter",
            "loan-agreement",
            "promissory-note",
        ),
    ),
    (
        20,
        "ontology:tier-20-quote",
        ("quote", "cert", "term-sheet", "appraisal", "rent-roll"),
    ),
)

_FALLBACK_METRIC_BUCKETS: dict[str, tuple[str, str]] = {
    **dict.fromkeys(
        ("premium", "annual_debt_service"),
        ("total_premium", "ontology:fallback:total_premium"),
    ),
    **dict.fromkeys(
        ("deductible", "origination_fee", "closing_fee", "rate_lock_fee"),
        ("total_deductible", "ontology:fallback:total_deductible"),
    ),
    **dict.fromkeys(
        (
            "coverage_limit",
            "coverage_limits",
            "sublimit",
            "loan_amount",
            "principal_amount",
            "facility_limit",
        ),
        ("total_coverage_limit", "ontology:fallback:total_coverage_limit"),
    ),
    **dict.fromkeys(
        (
            "insured_value",
            "total_insured_value",
            "appraised_value",
            "collateral_value",
        ),
        ("total_insured_value", "ontology:fallback:total_insured_value"),
    ),
}


def _match_ontology_spec(
    active_rev: OntologyRevision, dt: str
) -> OntologyEntitySpec | None:
    matched_spec = active_rev.entities.get(dt)
    if matched_spec is not None:
        return matched_spec
    for spec in active_rev.entities.values():
        if dt in (a.lower() for a in spec.aliases):
            return spec
    candidates = [
        (len(tok), spec.source_priority, spec)
        for spec in active_rev.entities.values()
        for tok in (spec.entity_id.lower(), *(a.lower() for a in spec.aliases))
        if tok and tok in dt
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _resolve_ontology_graph_context(
    doctype: str,
) -> tuple[int, str, str | None, OntologyRevision | None]:
    """Query the Zone 6 Ontology Graph for doctype priority, revision ID, and edge ID."""
    from bridge.knowledge_catalog.catalog import _ensure_shared_ontology

    _, active_rev = _ensure_shared_ontology()
    rev_id = active_rev.revision_id

    dt = (doctype or "").strip().lower()
    if active_rev is not None and dt:
        matched_spec = _match_ontology_spec(active_rev, dt)
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

    for tier_prio, tier_edge, keywords in _TIER_KEYWORDS:
        if any(tok in dt for tok in keywords):
            return tier_prio, rev_id, tier_edge, active_rev
    return 10, rev_id, None, active_rev


_MAX_DECIMAL_EXPONENT = 18

_MONEY_MULTIPLIERS: dict[str, Decimal] = {
    "K": Decimal("1000"),
    "THOUSAND": Decimal("1000"),
    "M": Decimal("1000000"),
    "MM": Decimal("1000000"),
    "MILLION": Decimal("1000000"),
    "B": Decimal("1000000000"),
    "BN": Decimal("1000000000"),
    "BILLION": Decimal("1000000000"),
}

_TABLE_PATH_RE = re.compile(r"^fields\.tables\.([^.\[]+)\[\d+\]\.(.+)$")


_DOLLAR_ISO_CODES: frozenset[str] = frozenset(
    {"USD", "CAD", "AUD", "NZD", "HKD", "SGD"}
)


def _resolve_currency_tokens(
    *,
    outer_iso: str | None,
    outer_sym: str | None,
    iso: str | None,
    sym: str | None,
    trail_iso: str | None,
) -> tuple[bool, str | None]:
    iso_codes = [c for c in (outer_iso, iso, trail_iso) if c is not None]
    symbols = [s for s in (outer_sym, sym) if s is not None]
    invalid = (
        len(iso_codes) > 1
        or len(symbols) > 1
        or any(c not in _ISO_CURRENCY_CODES for c in iso_codes)
        or any(s not in _CURRENCY_SYMBOL_TO_ISO for s in symbols)
    )
    if invalid:
        return False, None
    if iso_codes and symbols:
        code, symbol = iso_codes[0], symbols[0]
        compatible = (symbol == "$" and code in _DOLLAR_ISO_CODES) or (
            _CURRENCY_SYMBOL_TO_ISO[symbol] == code
        )
        return (True, code) if compatible else (False, None)
    if iso_codes:
        return True, iso_codes[0]
    return True, (_CURRENCY_SYMBOL_TO_ISO[symbols[0]] if symbols else None)


def _unwrap_money_parens(
    cleaned: str,
) -> tuple[bool, str | None, str | None, str | None, str]:
    paren_match = _PARENS_MONEY_RE.fullmatch(cleaned)
    if paren_match is None:
        return False, None, None, None, cleaned
    return (
        True,
        paren_match.group("outer_iso"),
        paren_match.group("outer_sym"),
        paren_match.group("trail_iso"),
        paren_match.group("inner").strip(),
    )


def _quantize_money_match(
    match: re.Match[str], *, accounting_neg: bool, explicit_signs: list[str]
) -> Decimal | None:
    exp_part = match.group("exp") or ""
    if exp_part and abs(int(exp_part[1:])) > _MAX_DECIMAL_EXPONENT:
        return None
    sign = "-" if (accounting_neg or explicit_signs == ["-"]) else ""
    digits = match.group("digits").replace(",", "")
    try:
        val = Decimal(f"{sign}{digits}{exp_part}")
        if not val.is_finite():
            return None
        mult_tok = match.group("mult")
        if mult_tok:
            val *= _MONEY_MULTIPLIERS[mult_tok.upper()]
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parse_money_amount(raw: str | None) -> tuple[Decimal, str | None] | None:
    """Strictly parse a closed-grammar monetary string into ``(Decimal(0.01), iso_currency)``.

    Uses ``fullmatch`` against signs, optional currency symbol/ISO code, properly
    grouped digits, and exact multiplier tokens (``K``/``M``/``B``) or bounded
    scientific notation (``|exp| <= 18``). Returns ``None`` on prose, conflicting
    signs or currencies, European comma decimals, or overflow.
    """
    if not raw or not isinstance(raw, str) or not raw.strip():
        return None
    accounting_neg, outer_iso, outer_sym, outer_trail_iso, inner = _unwrap_money_parens(
        raw.strip()
    )
    match = _NUMBER_RE.fullmatch(inner)
    if match is None:
        return None
    explicit_signs = [
        s
        for s in (
            match.group("outer_sign") or "",
            match.group("inner_sign") or "",
            match.group("trail_sign") or "",
        )
        if s
    ]
    trail_iso = match.group("trail_iso")
    if (
        len(explicit_signs) > 1
        or (accounting_neg and explicit_signs)
        or (outer_trail_iso and trail_iso)
    ):
        return None
    ccy_ok, currency = _resolve_currency_tokens(
        outer_iso=outer_iso,
        outer_sym=outer_sym,
        iso=match.group("iso"),
        sym=match.group("sym"),
        trail_iso=outer_trail_iso or trail_iso,
    )
    if not ccy_ok:
        return None
    quantized = _quantize_money_match(
        match, accounting_neg=accounting_neg, explicit_signs=explicit_signs
    )
    return (quantized, currency) if quantized is not None else None


def parse_decimal_money(raw: str | None) -> Decimal | None:
    """Deterministically parse a monetary string into ``Decimal(0.01)`` (or ``None``)."""
    parsed = parse_money_amount(raw)
    return parsed[0] if parsed is not None else None


def resolve_entry_effective_from(
    entry: LedgerEntry,
    *,
    effective_from: datetime | None = None,
    fallback_recorded_at: datetime,
) -> tuple[datetime, bool]:
    """Resolve effective timestamp from explicit arg, entry field, or ``effective_date``.

    Returns ``(effective_ts, unreadable_date_flagged)`` so an unreadable non-ISO
    ``effective_date`` is explicitly flagged instead of silently discarded.
    """
    if effective_from is not None:
        return _utc_dt(effective_from), False
    if entry.effective_from is not None:
        return _utc_dt(entry.effective_from), False
    raw_doc_date = entry.key_fields.get("effective_date")
    if raw_doc_date is not None and raw_doc_date.strip():
        try:
            parsed_d = datetime.fromisoformat(raw_doc_date.strip())
            return _utc_dt(parsed_d), False
        except ValueError:
            return _utc_dt(fallback_recorded_at), True
    return _utc_dt(fallback_recorded_at), False


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


def _is_ontology_monetary_field(
    field_name: str, active_rev: OntologyRevision | None
) -> bool:
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
    active_rev: OntologyRevision | None,
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
    return _FALLBACK_METRIC_BUCKETS.get(leaf, (None, None))


def consolidate_entry(  # noqa: PLR0913 - public keyword-only consolidation API
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
    raw_rec = (
        recorded_at
        if recorded_at is not None
        else (entry.recorded_at if entry.recorded_at is not None else datetime.now(UTC))
    )
    rec_ts = _utc_dt(raw_rec)
    eff_ts, bad_effective_date = resolve_entry_effective_from(
        entry,
        effective_from=effective_from,
        fallback_recorded_at=rec_ts,
    )

    default_currency = entry.key_fields.get("currency", "USD").upper()
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
        parsed_money = parse_money_amount(v) if is_money else None
        dec = parsed_money[0] if parsed_money is not None else None
        fact_currency = (
            parsed_money[1]
            if parsed_money is not None and parsed_money[1] is not None
            else default_currency
        )
        unparseable_money = is_money and bool(v.strip()) and parsed_money is None
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
                currency=fact_currency,
                source_priority=priority,
                recorded_at=rec_ts,
                effective_from=eff_ts,
                version=version,
                source_anchor=anchor,
                vault_uri=vault_uri,
                flagged_for_review=(
                    path in flagged_paths
                    or k in flagged_paths
                    or unparseable_money
                    or (bad_effective_date and k == "effective_date")
                ),
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
                parsed_money = parse_money_amount(val) if is_money else None
                dec = parsed_money[0] if parsed_money is not None else None
                fact_currency = (
                    parsed_money[1]
                    if parsed_money is not None and parsed_money[1] is not None
                    else default_currency
                )
                unparseable_money = (
                    is_money and bool(val.strip()) and parsed_money is None
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
                        field_value=val,
                        numeric_value=str(dec) if dec is not None else None,
                        currency=fact_currency,
                        source_priority=priority,
                        recorded_at=rec_ts,
                        effective_from=eff_ts,
                        version=version,
                        source_anchor=anchor,
                        vault_uri=vault_uri,
                        flagged_for_review=(path in flagged_paths or unparseable_money),
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


def _winning_key(fact: ConsolidatedFact) -> tuple[datetime, int, datetime, int]:
    """Deterministic ordering for active fact resolution (ADR-16):
    (effective_from, source_priority, recorded_at, version)."""
    return (
        _utc_dt(fact.effective_from),
        fact.source_priority,
        _utc_dt(fact.recorded_at),
        fact.version,
    )


def _resolve_scalar_winner(
    field_path: str, group: list[ConsolidatedFact]
) -> tuple[ConsolidatedFact, FactConflict | None]:
    candidates = sorted(group, key=_winning_key, reverse=True)
    winner = candidates[0]
    for other in candidates[1:]:
        if other.doc_id != winner.doc_id and other.field_value != winner.field_value:
            why = (
                f"{winner.doctype} ({winner.doc_id}) asserts {winner.field_value!r} "
                f"(effective {winner.effective_from.isoformat()}, priority {winner.source_priority}, "
                f"learned {winner.recorded_at.isoformat()}), "
                f"superseding {other.doctype} ({other.doc_id}) value {other.field_value!r}"
            )
            return winner, FactConflict(
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
    return winner, None


def _resolve_table_winner(
    tbl_name: str, by_doc: dict[tuple[str, int], list[ConsolidatedFact]]
) -> tuple[list[ConsolidatedFact], FactConflict | None]:
    doc_groups = sorted(
        by_doc.values(),
        key=lambda rows: max(_winning_key(r) for r in rows),
        reverse=True,
    )
    winning_rows = sorted(doc_groups[0], key=lambda r: r.field_path)
    win_sig = [(r.field_path, r.field_value) for r in winning_rows]
    win_rep = winning_rows[0]
    for other_rows in doc_groups[1:]:
        other_sorted = sorted(other_rows, key=lambda r: r.field_path)
        other_rep = other_sorted[0]
        if (
            other_rep.doc_id != win_rep.doc_id
            and [(r.field_path, r.field_value) for r in other_sorted] != win_sig
        ):
            why = (
                f"{win_rep.doctype} ({win_rep.doc_id}) supersedes table "
                f"'{tbl_name}' from {other_rep.doctype} ({other_rep.doc_id})"
            )
            return winning_rows, FactConflict(
                entity_id=win_rep.entity_id,
                field_path=f"fields.tables.{tbl_name}",
                winning_fact_id=win_rep.fact_id,
                winning_value=f"{len(winning_rows)} cells",
                winning_doc_id=win_rep.doc_id,
                conflicting_fact_id=other_rep.fact_id,
                conflicting_value=f"{len(other_sorted)} cells",
                conflicting_doc_id=other_rep.doc_id,
                reason=why,
                winning_trace=win_rep.trace(),
                conflicting_trace=other_rep.trace(),
            )
    return winning_rows, None


def resolve_active_facts(
    facts: list[ConsolidatedFact],
    *,
    learned_as_of: datetime | None = None,
    effective_on: datetime | None = None,
) -> tuple[list[ConsolidatedFact], list[FactConflict]]:
    """Filter facts by bi-temporal window and resolve winners + conflicts per field/table."""
    learned_bound = _utc_dt(learned_as_of) if learned_as_of is not None else None
    effective_bound = _utc_dt(effective_on) if effective_on is not None else None
    eligible = [
        f
        for f in facts
        if (learned_bound is None or _utc_dt(f.recorded_at) <= learned_bound)
        and (effective_bound is None or _utc_dt(f.effective_from) <= effective_bound)
    ]

    by_scalar_field: dict[str, list[ConsolidatedFact]] = {}
    by_table: dict[str, dict[tuple[str, int], list[ConsolidatedFact]]] = {}
    for f in eligible:
        tbl_match = _TABLE_PATH_RE.match(f.field_path)
        if tbl_match is not None:
            by_table.setdefault(tbl_match.group(1), {}).setdefault(
                (f.doc_id, f.version), []
            ).append(f)
        else:
            by_scalar_field.setdefault(f.field_path, []).append(f)

    winners: list[ConsolidatedFact] = []
    conflicts: list[FactConflict] = []
    for field_path in sorted(by_scalar_field):
        winner, conflict = _resolve_scalar_winner(
            field_path, by_scalar_field[field_path]
        )
        winners.append(winner)
        if conflict is not None:
            conflicts.append(conflict)

    for tbl_name in sorted(by_table):
        winning_rows, conflict = _resolve_table_winner(tbl_name, by_table[tbl_name])
        winners.extend(winning_rows)
        if conflict is not None:
            conflicts.append(conflict)

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
        totals: dict[str, Decimal] = {
            "total_premium": Decimal("0.00"),
            "total_deductible": Decimal("0.00"),
            "total_coverage_limit": Decimal("0.00"),
            "total_insured_value": Decimal("0.00"),
        }
        fallback_currency = "USD"
        bucket_currencies: set[str] = set()
        _, _, _, active_rev = _resolve_ontology_graph_context("")

        for f in facts:
            if f.currency:
                fallback_currency = f.currency
            if f.numeric_value is None:
                continue
            leaf = f.field_path.split(".")[-1].lower()
            bucket, _ = _resolve_field_metric_bucket(leaf, f.doctype, active_rev)
            if bucket in totals:
                bucket_currencies.add((f.currency or "USD").upper())
                totals[bucket] += Decimal(f.numeric_value)

        if len(bucket_currencies) > 1:
            raise ValueError(
                f"Cannot sum financial summary across mixed currencies: "
                f"{sorted(bucket_currencies)}"
            )
        currency = (
            next(iter(bucket_currencies)) if bucket_currencies else fallback_currency
        )
        total_premium = totals["total_premium"]
        total_deductible = totals["total_deductible"]
        total_limit = totals["total_coverage_limit"]
        total_tiv = totals["total_insured_value"]

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
