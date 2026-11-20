"""Deterministic collection-strategy evaluator (ADR-14 Sense A′).

Zero LLM calls participate. Evaluates ordering, counts, distinct-issuer
constraints, recency/expiry windows, ontology field presence, alternatives,
caller-facing waiver recommendations, and ``CollectRequest.requirements`` caller
overrides (caller overrides always win). DocuBridge never mints ``done`` and
never emits ``RequirementStatus.waived`` in ``SufficiencyVerdict.outstanding``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from bridge.classification import canonicalize_issuer
from bridge.strategy.loader import load_strategy
from bridge.strategy.models import CollectionStrategy, StrategyAlternative, StrategyRule
from contract.enums import Disposition, RequirementStatus
from contract.wire import (
    LedgerEntry,
    Requirement,
    RequirementsList,
    SufficiencyEvidence,
    SufficiencyVerdict,
    WaiverRecommendation,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _norm_key(value: str) -> str:
    return _SLUG_RE.sub("", value.strip().lower())


def _norm_value(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw or not raw.strip():
        return None
    token = raw.strip()
    try:
        return date.fromisoformat(token[:10])
    except ValueError:
        return None


def _resolve_as_of(as_of: date | datetime | None) -> date:
    if as_of is None:
        return datetime.now(UTC).date()
    if isinstance(as_of, datetime):
        return as_of.date()
    return as_of


def _canonical_issuer(entry: LedgerEntry) -> str:
    return canonicalize_issuer(entry.issuer) or ""


def _rule_directly_matches_requirement(rule: StrategyRule, req: Requirement) -> bool:
    """Return True when ``req`` unambiguously names ``rule`` by id, label, or doctype."""
    if req.party is not None:
        return False
    req_slug = _norm_key(req.item)
    return req_slug in {
        _norm_key(rule.id),
        _norm_key(rule.item),
        _norm_key(rule.doctype),
    }


def _req_targets_doctype(req: Requirement, doctype: str) -> bool:
    return req.doctype_hint == doctype or _norm_key(req.item) == _norm_key(doctype)


def _claim_rule_overrides(
    rules: Sequence[StrategyRule], caller_reqs: Sequence[Requirement]
) -> tuple[dict[str, Requirement], set[int], set[str]]:
    """Map unambiguous caller overrides onto strategy rules without dropping collisions.

    First claims direct item/id/doctype slug matches (never overwriting an already-claimed
    rule). Falls back to ``doctype_hint`` only when ``req.party is None`` and ``req`` is
    the sole caller requirement targeting that doctype. Any remaining or colliding caller
    requirements stay unclaimed and are evaluated independently as extra caller items.
    """
    rule_overrides: dict[str, Requirement] = {}
    claimed_indices: set[int] = set()
    forced_required: set[str] = set()

    for rule in rules:
        for idx, req in enumerate(caller_reqs):
            if idx in claimed_indices or rule.id in rule_overrides:
                continue
            if _rule_directly_matches_requirement(rule, req):
                rule_overrides[rule.id] = req
                claimed_indices.add(idx)
                if req.status is RequirementStatus.required:
                    forced_required.add(rule.id)

    for rule in rules:
        if rule.id in rule_overrides:
            continue
        doctype_matches = [
            idx
            for idx, req in enumerate(caller_reqs)
            if _req_targets_doctype(req, rule.doctype)
        ]
        if len(doctype_matches) != 1:
            continue
        idx = doctype_matches[0]
        if idx in claimed_indices:
            continue
        req = caller_reqs[idx]
        if req.party is None and req.doctype_hint == rule.doctype:
            rule_overrides[rule.id] = req
            claimed_indices.add(idx)
            if req.status is RequirementStatus.required:
                forced_required.add(rule.id)

    return rule_overrides, claimed_indices, forced_required


def _rule_matches_recommendation(rule: StrategyRule, rec: WaiverRecommendation) -> bool:
    rec_slug = _norm_key(rec.item)
    if rec_slug in {_norm_key(rule.id), _norm_key(rule.item), _norm_key(rule.doctype)}:
        return True
    return bool(rec.doctype_hint and rec.doctype_hint == rule.doctype)


def _extra_req_matches_recommendation(
    req: Requirement, slug: str, rec: WaiverRecommendation
) -> bool:
    rec_slug = _norm_key(rec.item)
    if rec_slug == slug:
        return True
    return bool(
        req.party is None
        and req.doctype_hint is not None
        and rec.doctype_hint == req.doctype_hint
        and rec_slug == _norm_key(req.doctype_hint)
    )


def _check_entry_eligibility(
    rule: StrategyRule,
    entry: LedgerEntry,
    *,
    ref_date: date,
    forbidden_issuers: set[str],
) -> tuple[bool, str]:
    for field_name in rule.required_fields:
        val = entry.key_fields.get(field_name)
        if val is None or not str(val).strip():
            return False, f"doc {entry.doc_id} missing required field '{field_name}'"

    if rule.not_expired_field:
        raw_exp = entry.key_fields.get(rule.not_expired_field)
        exp_date = _parse_iso_date(raw_exp)
        if exp_date is None:
            return (
                False,
                f"doc {entry.doc_id} missing/invalid '{rule.not_expired_field}' date",
            )
        if exp_date < ref_date:
            return (
                False,
                f"doc {entry.doc_id} expired ({rule.not_expired_field}={exp_date.isoformat()} < {ref_date.isoformat()})",
            )

    if rule.recency_days is not None and rule.recency_field:
        raw_dt = entry.key_fields.get(rule.recency_field)
        doc_date = _parse_iso_date(raw_dt)
        if doc_date is None:
            return (
                False,
                f"doc {entry.doc_id} missing/invalid '{rule.recency_field}' date",
            )
        age_days = (ref_date - doc_date).days
        if age_days > rule.recency_days:
            return (
                False,
                f"doc {entry.doc_id} stale ({rule.recency_field}={doc_date.isoformat()}, {age_days}d > {rule.recency_days}d window)",
            )
        if age_days < 0:
            return (
                False,
                f"doc {entry.doc_id} future-dated ({rule.recency_field}={doc_date.isoformat()} > {ref_date.isoformat()})",
            )

    if rule.distinct_issuer_from:
        issuer = _canonical_issuer(entry)
        if not issuer:
            return (
                False,
                f"doc {entry.doc_id} missing canonical issuer for cross-document independence check",
            )
        if issuer in forbidden_issuers:
            return (
                False,
                f"doc {entry.doc_id} issuer '{issuer}' collides with {', '.join(rule.distinct_issuer_from)}",
            )

    return True, ""


def _select_best_cohort(
    rule: StrategyRule, eligible: list[LedgerEntry]
) -> tuple[list[LedgerEntry], bool]:
    """Select the strongest qualifying cohort respecting distinct_issuers and same_value_fields."""
    if not eligible:
        return [], False

    def _dedupe_issuers(group: list[LedgerEntry]) -> list[LedgerEntry]:
        if not rule.distinct_issuers:
            return list(group)
        seen: set[str] = set()
        picked: list[LedgerEntry] = []
        for entry in group:
            issuer = _canonical_issuer(entry)
            if not issuer or issuer in seen:
                continue
            seen.add(issuer)
            picked.append(entry)
        return picked

    if not rule.same_value_fields or rule.min_count <= 1:
        return _dedupe_issuers(eligible), False

    buckets: dict[tuple[str, ...], list[LedgerEntry]] = {}
    for entry in eligible:
        vals = [entry.key_fields.get(f, "") for f in rule.same_value_fields]
        if any(not str(v).strip() for v in vals):
            continue
        key = tuple(_norm_value(str(v)) for v in vals)
        buckets.setdefault(key, []).append(entry)

    if not buckets:
        return [], True

    best: list[LedgerEntry] = []
    for group in buckets.values():
        candidate = _dedupe_issuers(group)
        if len(candidate) > len(best):
            best = candidate
    mismatch = len(buckets) > 1 and len(best) < rule.min_count
    return best, mismatch


@dataclass(frozen=True)
class _RuleOutcome:
    rule: StrategyRule
    effective_required: bool
    evidence: SufficiencyEvidence
    outstanding_requirement: Requirement | None
    order_key: tuple[int, int, int, int]


def _order_key_for(
    strategy: CollectionStrategy,
    alt: StrategyAlternative,
    rule: StrategyRule,
    declared_idx: int,
) -> tuple[int, int, int, int]:
    ordering = strategy.ordering
    pos = len(ordering)
    for idx, token in enumerate(ordering):
        if token in (rule.id, rule.doctype):
            pos = idx
            break
    return (pos, alt.priority, rule.priority, declared_idx)


def _evaluate_single_rule(
    strategy: CollectionStrategy,
    alt: StrategyAlternative,
    rule: StrategyRule,
    declared_idx: int,
    *,
    entries: Sequence[LedgerEntry],
    accepted_by_doctype: dict[str, list[LedgerEntry]],
    caller_override: Requirement | None,
    matched_recs: list[WaiverRecommendation],
    ref_date: date,
) -> _RuleOutcome:
    order_key = _order_key_for(strategy, alt, rule, declared_idx)
    field_refs = rule.referenced_fields()
    has_waiver_rec = bool(matched_recs)
    effective_required = rule.required

    if caller_override is not None:
        if caller_override.status is RequirementStatus.waived:
            reason_tag = caller_override.reason_code or "caller_override"
            return _RuleOutcome(
                rule=rule,
                effective_required=False,
                evidence=SufficiencyEvidence(
                    rule_id=rule.id,
                    satisfied=True,
                    doc_ids=[],
                    issuers=[],
                    reason=f"Waived by calling agent override ({caller_override.item}: {reason_tag}).",
                    field_refs=field_refs,
                    waiver_recommended=has_waiver_rec,
                ),
                outstanding_requirement=None,
                order_key=order_key,
            )
        if caller_override.status is RequirementStatus.satisfied:
            return _RuleOutcome(
                rule=rule,
                effective_required=False,
                evidence=SufficiencyEvidence(
                    rule_id=rule.id,
                    satisfied=True,
                    doc_ids=[],
                    issuers=[],
                    reason=f"Marked satisfied by calling agent override ({caller_override.item}).",
                    field_refs=field_refs,
                    waiver_recommended=has_waiver_rec,
                ),
                outstanding_requirement=None,
                order_key=order_key,
            )
        if caller_override.status is RequirementStatus.optional:
            effective_required = False
        elif caller_override.status is RequirementStatus.required:
            effective_required = True

    forbidden_issuers: set[str] = set()
    for other_dt in rule.distinct_issuer_from:
        for other_entry in accepted_by_doctype.get(other_dt, ()):
            iss = _canonical_issuer(other_entry)
            if iss:
                forbidden_issuers.add(iss)

    accepted_candidates = accepted_by_doctype.get(rule.doctype, [])
    eligible: list[LedgerEntry] = []
    disqualified_notes: list[str] = []
    for entry in accepted_candidates:
        ok, why = _check_entry_eligibility(
            rule, entry, ref_date=ref_date, forbidden_issuers=forbidden_issuers
        )
        if ok:
            eligible.append(entry)
        else:
            disqualified_notes.append(why)

    picked, same_val_mismatch = _select_best_cohort(rule, eligible)
    satisfied = len(picked) >= rule.min_count
    doc_ids = [e.doc_id for e in picked]
    issuers = sorted({iss for e in picked if (iss := _canonical_issuer(e))})

    if satisfied:
        issuer_note = (
            f" from distinct issuers ({', '.join(issuers)})"
            if rule.distinct_issuers and issuers
            else ""
        )
        reason = (
            f"Satisfied: {len(picked)}/{rule.min_count} accepted {rule.doctype} "
            f"document(s){issuer_note}."
        )
        return _RuleOutcome(
            rule=rule,
            effective_required=effective_required,
            evidence=SufficiencyEvidence(
                rule_id=rule.id,
                satisfied=True,
                doc_ids=doc_ids,
                issuers=issuers,
                reason=reason,
                field_refs=field_refs,
                waiver_recommended=has_waiver_rec,
            ),
            outstanding_requirement=None,
            order_key=order_key,
        )

    non_accepted = [
        e
        for e in entries
        if e.doctype == rule.doctype and e.disposition is not Disposition.accepted
    ]
    details: list[str] = []
    if rule.distinct_issuers:
        details.append(
            f"need {rule.min_count} accepted {rule.doctype} from distinct issuers; "
            f"have {len(picked)}" + (f" ({', '.join(issuers)})" if issuers else "")
        )
    else:
        details.append(
            f"need {rule.min_count} accepted {rule.doctype}; have {len(picked)}"
        )
    if same_val_mismatch:
        details.append(
            f"matching {', '.join(rule.same_value_fields)} required across documents"
        )
    if disqualified_notes:
        details.append("; ".join(disqualified_notes))
    if non_accepted:
        non_acc_summary = ", ".join(
            f"{e.doc_id}={e.disposition.value}" for e in non_accepted
        )
        details.append(f"non-accepted entries ignored ({non_acc_summary})")
    if has_waiver_rec:
        rec_notes = "; ".join(f"{r.reason_code}: {r.reason}" for r in matched_recs)
        details.append(f"waiver recommended for calling agent ({rec_notes})")

    reason_text = "; ".join(details) + "."
    req_status = (
        RequirementStatus.required if effective_required else RequirementStatus.optional
    )
    req_reason_code = (
        "waiver_recommended"
        if has_waiver_rec
        else (
            "optional_unsatisfied"
            if not effective_required
            else "strategy_requirement_unsatisfied"
        )
    )
    base_msg = rule.message.strip() or reason_text
    req_msg = f"{base_msg} ({reason_text})" if rule.message.strip() else reason_text

    return _RuleOutcome(
        rule=rule,
        effective_required=effective_required,
        evidence=SufficiencyEvidence(
            rule_id=rule.id,
            satisfied=False,
            doc_ids=doc_ids,
            issuers=issuers,
            reason=reason_text,
            field_refs=field_refs,
            waiver_recommended=has_waiver_rec,
        ),
        outstanding_requirement=Requirement(
            item=rule.item,
            status=req_status,
            doctype_hint=rule.doctype,
            reason_code=req_reason_code,
            message=req_msg,
        ),
        order_key=order_key,
    )


def _entry_item_affinity(req: Requirement, entry: LedgerEntry) -> int:
    """Count distinguishing alphanumeric tokens from ``req.item`` present on ``entry``."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", req.item.lower()) if len(t) >= 3]
    if not tokens:
        return 0
    haystack = " ".join(
        [entry.doc_id, entry.issuer or "", *(str(v) for v in entry.key_fields.values())]
    ).lower()
    return sum(1 for tok in tokens if tok in haystack)


def _evaluate_extra_caller_requirement(
    req: Requirement,
    idx: int,
    *,
    accepted_entries: Sequence[LedgerEntry],
    used_doc_ids: set[str],
    waiver_recommendations: Sequence[WaiverRecommendation],
) -> tuple[SufficiencyEvidence, Requirement | None, bool]:
    """Evaluate a caller-supplied requirement that does not map to a strategy rule."""
    slug = _norm_key(req.item) or f"item-{idx}"
    rule_id = f"caller:{slug}"
    matched_recs = [
        r
        for r in waiver_recommendations
        if _extra_req_matches_recommendation(req, slug, r)
    ]
    has_rec = bool(matched_recs)

    if req.status is RequirementStatus.waived:
        return (
            SufficiencyEvidence(
                rule_id=rule_id,
                satisfied=True,
                doc_ids=[],
                issuers=[],
                reason=f"Caller-added requirement {req.item!r} waived by calling agent.",
                waiver_recommended=has_rec,
            ),
            None,
            True,
        )
    if req.status is RequirementStatus.satisfied:
        return (
            SufficiencyEvidence(
                rule_id=rule_id,
                satisfied=True,
                doc_ids=[],
                issuers=[],
                reason=f"Caller-added requirement {req.item!r} marked satisfied by calling agent.",
                waiver_recommended=has_rec,
            ),
            None,
            True,
        )

    target_doctype = req.doctype_hint or req.item
    target_party = canonicalize_issuer(req.party) if req.party else None
    candidates: list[LedgerEntry] = []
    for entry in accepted_entries:
        if entry.doc_id in used_doc_ids:
            continue
        if entry.doctype != target_doctype and _norm_key(entry.doctype) != _norm_key(
            target_doctype
        ):
            continue
        if target_party is not None:
            entry_iss = _canonical_issuer(entry)
            if (
                entry_iss != target_party
                and req.party.lower() not in (entry.issuer or "").lower()
            ):
                continue
        candidates.append(entry)

    if candidates:
        candidates.sort(key=lambda e: _entry_item_affinity(req, e), reverse=True)
        chosen = candidates[0]
        used_doc_ids.add(chosen.doc_id)
        issuers = [iss] if (iss := _canonical_issuer(chosen)) else []
        return (
            SufficiencyEvidence(
                rule_id=rule_id,
                satisfied=True,
                doc_ids=[chosen.doc_id],
                issuers=issuers,
                reason=f"Caller-added requirement {req.item!r} satisfied by accepted document {chosen.doc_id}.",
                waiver_recommended=has_rec,
            ),
            None,
            True,
        )

    is_required = req.status is RequirementStatus.required
    party_note = f" from party {req.party!r}" if req.party else ""
    reason = f"Caller-added requirement {req.item!r}{party_note} has 0 accepted matching documents."
    if has_rec:
        reason += " Waiver recommended for calling agent."
    out_req = Requirement(
        item=req.item,
        status=req.status,
        party=req.party,
        doctype_hint=req.doctype_hint,
        reason_code="waiver_recommended"
        if has_rec
        else (req.reason_code or "caller_requirement_unsatisfied"),
        message=req.message or reason,
    )
    return (
        SufficiencyEvidence(
            rule_id=rule_id,
            satisfied=False,
            doc_ids=[],
            issuers=[],
            reason=reason,
            waiver_recommended=has_rec,
        ),
        out_req,
        not is_required,
    )


def evaluate_strategy(
    strategy: CollectionStrategy,
    entries: Sequence[LedgerEntry],
    *,
    caller_requirements: RequirementsList | None = None,
    waiver_recommendations: Sequence[WaiverRecommendation] = (),
    as_of: date | datetime | None = None,
) -> SufficiencyVerdict:
    """Deterministically evaluate ``entries`` against ``strategy`` and caller overrides.

    Zero models are called. ``CollectRequest.requirements`` caller overrides always
    take precedence over strategy defaults. Waiver recommendations are surfaced
    to the calling agent without mutating any requirement status to ``waived``.
    """
    ref_date = _resolve_as_of(as_of)
    accepted_entries = [e for e in entries if e.disposition is Disposition.accepted]
    accepted_by_doctype: dict[str, list[LedgerEntry]] = {}
    for entry in accepted_entries:
        accepted_by_doctype.setdefault(entry.doctype, []).append(entry)

    caller_reqs = list(caller_requirements.requirements) if caller_requirements else []
    rule_overrides, claimed_caller_indices, caller_forced_required_rules = (
        _claim_rule_overrides(strategy.all_rules(), caller_reqs)
    )

    alt_outcomes: list[tuple[StrategyAlternative, list[_RuleOutcome], bool]] = []
    all_evidence: list[SufficiencyEvidence] = []
    declared_idx = 0

    for alt in sorted(strategy.effective_alternatives(), key=lambda a: a.priority):
        outcomes: list[_RuleOutcome] = []
        for rule in alt.rules:
            matched_recs = [
                rec
                for rec in waiver_recommendations
                if _rule_matches_recommendation(rule, rec)
            ]
            outcome = _evaluate_single_rule(
                strategy,
                alt,
                rule,
                declared_idx,
                entries=entries,
                accepted_by_doctype=accepted_by_doctype,
                caller_override=rule_overrides.get(rule.id),
                matched_recs=matched_recs,
                ref_date=ref_date,
            )
            declared_idx += 1
            outcomes.append(outcome)
            all_evidence.append(outcome.evidence)
        required_outcomes = [o for o in outcomes if o.effective_required]
        if required_outcomes:
            alt_satisfied = all(o.evidence.satisfied for o in required_outcomes)
        else:
            alt_satisfied = bool(outcomes) and any(
                o.evidence.satisfied for o in outcomes
            )
        alt_outcomes.append((alt, outcomes, alt_satisfied))

    if strategy.match == "any_of":
        base_satisfied = any(alt_ok for _, _, alt_ok in alt_outcomes)
    else:
        base_satisfied = all(alt_ok for _, _, alt_ok in alt_outcomes)

    forced_rules_ok = True
    forced_outstanding: list[_RuleOutcome] = []
    for _, outcomes, _ in alt_outcomes:
        for outcome in outcomes:
            if (
                outcome.rule.id in caller_forced_required_rules
                and not outcome.evidence.satisfied
            ):
                forced_rules_ok = False
                forced_outstanding.append(outcome)

    extra_outstanding: list[Requirement] = []
    extra_required_ok = True
    used_extra_doc_ids: set[str] = set()
    for idx, req in enumerate(caller_reqs):
        if idx in claimed_caller_indices:
            continue
        ev, out_req, ok = _evaluate_extra_caller_requirement(
            req,
            idx,
            accepted_entries=accepted_entries,
            used_doc_ids=used_extra_doc_ids,
            waiver_recommendations=waiver_recommendations,
        )
        all_evidence.append(ev)
        if not ok:
            extra_required_ok = False
        if out_req is not None:
            extra_outstanding.append(out_req)

    overall_satisfied = base_satisfied and forced_rules_ok and extra_required_ok

    outstanding_outcomes: list[_RuleOutcome] = []
    if not base_satisfied:
        for _, outcomes, _ in alt_outcomes:
            for outcome in outcomes:
                if outcome.outstanding_requirement is not None:
                    outstanding_outcomes.append(outcome)
    else:
        seen_ids: set[str] = set()
        for outcome in forced_outstanding:
            if outcome.outstanding_requirement is not None:
                outstanding_outcomes.append(outcome)
                seen_ids.add(outcome.rule.id)
        for _, outcomes, alt_ok in alt_outcomes:
            if strategy.match == "any_of" and not alt_ok:
                continue
            for outcome in outcomes:
                if (
                    outcome.rule.id not in seen_ids
                    and not outcome.effective_required
                    and outcome.outstanding_requirement is not None
                ):
                    outstanding_outcomes.append(outcome)

    outstanding_outcomes.sort(key=lambda o: o.order_key)
    outstanding = [
        o.outstanding_requirement
        for o in outstanding_outcomes
        if o.outstanding_requirement is not None
    ]
    outstanding.extend(extra_outstanding)

    return SufficiencyVerdict(
        satisfied=overall_satisfied,
        strategy_revision=strategy.revision or strategy.version,
        evidence=all_evidence,
        outstanding=outstanding,
        waiver_recommendations=list(waiver_recommendations),
    )


def evaluate_process_sufficiency(
    process: str,
    entries: Sequence[LedgerEntry],
    *,
    caller_requirements: RequirementsList | None = None,
    waiver_recommendations: Sequence[WaiverRecommendation] = (),
    as_of: date | datetime | None = None,
    skills_dir: Path | None = None,
    use_registry: bool = True,
) -> SufficiencyVerdict | None:
    """Load ``process``'s declared ``assets/strategy.yaml`` and evaluate ``entries``.

    Returns ``None`` when ``process`` carries no strategy asset; raises
    :class:`~bridge.strategy.models.StrategyLoadError` if the asset is malformed.
    """
    strategy = load_strategy(process, skills_dir=skills_dir, use_registry=use_registry)
    if strategy is None:
        return None
    return evaluate_strategy(
        strategy,
        entries,
        caller_requirements=caller_requirements,
        waiver_recommendations=waiver_recommendations,
        as_of=as_of,
    )


def waiver_recommendations_from_advisories(
    advisories: Sequence[Any],
    *,
    recommended_by: str = "counterparty_reply_table",
) -> list[WaiverRecommendation]:
    """Convert waiver-eligible reply-table advisories into caller-facing recommendations."""
    recommendations: list[WaiverRecommendation] = []
    for adv in advisories:
        if getattr(adv, "recommended_requirement_status", None) != "waiver_recommended":
            continue
        raw_status = str(getattr(adv, "raw_status", "") or "").strip()
        note = str(getattr(adv, "note", "") or "").strip()
        reason = " — ".join(p for p in (raw_status, note) if p) or "Waiver recommended."
        recommendations.append(
            WaiverRecommendation(
                item=str(getattr(adv, "item_key", "")),
                reason_code=str(getattr(adv, "kind", "waiver_recommended")),
                reason=reason,
                citation=getattr(adv, "source_envelope_id", None),
                recommended_by=recommended_by,
            )
        )
    return recommendations
