"""3-Tier Synthetic Stress Testing & Quality Report Card Gate mixin for Agent Gym."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from bridge.agent_gym.models import (
    _FORBIDDEN_MATH_TOKENS,
    REASONING_MODEL,
    WORKHORSE_MODEL,
    ExpertFeedbackSignal,
    GymReportCard,
    TribalKnowledgeRule,
    TribalRuleCategory,
    TribalRuleStatus,
    _strip_legal_suffix,
    generate_synthetic_stress_suite,
)
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    CorridorPoint,
    dispose,
    threshold_corridor,
)
from bridge.operational_store.consolidation_rules import parse_decimal_money
from bridge.operational_store.models import _MONEY_KEYS
from bridge.telemetry import trace_step
from contract.enums import Gate

_PCT_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*%\s*$")
_ISO_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _validate_extraction_hint_target(
    rule: TribalKnowledgeRule, signal: ExpertFeedbackSignal, target: str
) -> list[str]:
    before = (rule.before_value or "").strip()
    if not before or before != signal.before_value.strip():
        return ["invalid_before_value:missing_or_mismatch"]

    leaf = rule.field_path.split(".")[-1].strip().lower()
    if leaf in _MONEY_KEYS or parse_decimal_money(before) is not None:
        return (
            []
            if parse_decimal_money(target) is not None
            else ["invalid_canonical_target:non_monetary_target"]
        )
    if _PCT_RE.match(before):
        return (
            []
            if _PCT_RE.match(target)
            else ["invalid_canonical_target:non_percentage_target"]
        )
    if _ISO_DATE_RE.match(before):
        return (
            []
            if _ISO_DATE_RE.match(target)
            else ["invalid_canonical_target:non_date_target"]
        )
    target_lower = target.lower()
    if target_lower in signal.expert_memo.lower():
        return []
    context_tokens = set(_TOKEN_RE.findall(f"{before} {rule.field_path}".lower()))
    target_tokens = set(_TOKEN_RE.findall(target_lower))
    return (
        []
        if (context_tokens & target_tokens)
        else ["invalid_canonical_target:unrelated_target"]
    )


def _validate_rule_target(
    rule: TribalKnowledgeRule, signal: ExpertFeedbackSignal
) -> list[str]:
    """Validate that a candidate rule's canonical target is compatible with its field and signal."""
    target = (rule.canonical_target or "").strip()
    if not target:
        return ["invalid_canonical_target:empty_target"]
    if rule.category == TribalRuleCategory.ENTITY_ALIAS:
        expected_alias = _strip_legal_suffix(signal.after_value)
        return (
            []
            if target == expected_alias
            else ["invalid_canonical_target:mismatch_with_signal"]
        )
    if target != signal.after_value.strip():
        return ["invalid_canonical_target:mismatch_with_signal"]
    if rule.category != TribalRuleCategory.EXTRACTION_HINT:
        return []
    return _validate_extraction_hint_target(rule, signal, target)


def _check_tier1_and_tier3(
    rule: TribalKnowledgeRule,
    signal: ExpertFeedbackSignal,
    *,
    math_firewall_intact: bool,
) -> tuple[bool, bool, list[str]]:
    doctype_for_gate = rule.doctype if rule.doctype != "*" else "quote"
    verdict_clean = dispose(
        doctype=doctype_for_gate,
        candidate_doctypes=[doctype_for_gate],
        legible=True,
        overall_confidence=0.98,
        flagged_fields=[],
        thresholds=DEFAULT_THRESHOLDS,
    )
    verdict_flagged = dispose(
        doctype=doctype_for_gate,
        candidate_doctypes=[doctype_for_gate],
        legible=True,
        overall_confidence=0.98,
        flagged_fields=[rule.field_path],
        thresholds=DEFAULT_THRESHOLDS,
    )
    target_issues = _validate_rule_target(rule, signal)
    tier1_ok = (
        math_firewall_intact
        and not target_issues
        and verdict_clean.gate == Gate.auto_approve
        and verdict_flagged.gate == Gate.hitl_review
    )
    corridor = threshold_corridor(
        (
            CorridorPoint(
                record_id="cal:clean",
                overall_confidence=0.95,
                legible=True,
                in_label_space=True,
                flagged=False,
                expected_gate=Gate.auto_approve,
            ),
            CorridorPoint(
                record_id="cal:flagged",
                overall_confidence=0.95,
                legible=True,
                in_label_space=True,
                flagged=True,
                expected_gate=Gate.hitl_review,
            ),
            CorridorPoint(
                record_id="cal:low_conf",
                overall_confidence=0.40,
                legible=True,
                in_label_space=True,
                flagged=False,
                expected_gate=Gate.resubmit,
            ),
        )
    )
    return (
        tier1_ok,
        (corridor.feasible and corridor.contains(DEFAULT_THRESHOLDS)),
        target_issues,
    )


_LIVE_JUDGE_REJECT_RE = re.compile(
    r"(?:(?<!no )(?<!zero )\b(?:fail|fails|failed|reject|rejects|rejected|block|blocks|blocked|deny|denies|denied|unsafe)\b"
    r"|\b(?:not|never|cannot|do\s+not|don't)\s+(?:pass|passed|approve|approved|accept|accepted|ok|verified)\b)",
    re.IGNORECASE,
)
_LIVE_JUDGE_PASS_RE = re.compile(
    r"\b(pass|passed|approve|approved|accept|accepted|ok|verified)\b",
    re.IGNORECASE,
)
_LIVE_JUDGE_NAMED_SCORE_RE = re.compile(
    r"\bscore\s*[:=]\s*(0(?:\.\d+)?|1(?:\.0+)?)\b",
    re.IGNORECASE,
)
_LIVE_JUDGE_DECIMAL_SCORE_RE = re.compile(r"\b(0\.\d+|1\.0+)\b")


def _parse_live_judge_verdict(
    judge_text: str | None,
) -> tuple[bool, float | None, str | None]:
    """Parse a live judge model response; fail closed on reject, low score, or unparseable output."""
    if judge_text is None:
        return True, None, None
    cleaned = judge_text.strip()
    if not cleaned:
        return False, 0.0, "live_judge_unparseable_verdict"
    if _LIVE_JUDGE_REJECT_RE.search(cleaned):
        return False, 0.0, "live_judge_rejected"
    if _LIVE_JUDGE_PASS_RE.search(cleaned):
        named = _LIVE_JUDGE_NAMED_SCORE_RE.search(cleaned)
        dec = _LIVE_JUDGE_DECIMAL_SCORE_RE.search(cleaned)
        score = (
            float(named.group(1)) if named else (float(dec.group(1)) if dec else 1.0)
        )
        if score < 0.5:
            return False, round(score, 4), "live_judge_rejected"
        return True, round(score, 4), None
    return False, 0.0, "live_judge_unparseable_verdict"


def _rules_conflict(existing: TribalKnowledgeRule, rule: TribalKnowledgeRule) -> bool:
    if (
        existing.rule_id == rule.rule_id
        or existing.status != TribalRuleStatus.PROMOTED
        or existing.category != rule.category
        or existing.doctype != rule.doctype
        or existing.issuer_pattern != rule.issuer_pattern
        or existing.field_path != rule.field_path
        or existing.canonical_target == rule.canonical_target
    ):
        return False
    if rule.category == TribalRuleCategory.EXTRACTION_HINT:
        return (existing.before_value or "").strip() == (
            rule.before_value or ""
        ).strip()
    return True


def _collect_rule_regressions(
    existing_rules: list[TribalKnowledgeRule],
    rule: TribalKnowledgeRule,
    *,
    initial_issues: list[str],
    math_firewall_intact: bool,
    stress_cases: tuple[Any, ...],
) -> list[str]:
    regressions = list(initial_issues)
    for existing in existing_rules:
        if _rules_conflict(existing, rule):
            regressions.append(f"conflict_with_promoted_rule:{existing.rule_id}")
    if not math_firewall_intact:
        regressions.append("hallucination_firewall_violation:math_override_blocked")
    for case in stress_cases:
        if case.baseline_passed and not case.candidate_passed:
            regressions.append(f"held_out_regression:{case.case_id}")
    return regressions


def _build_eval_span_attrs(
    *,
    rule_id: str,
    auto_promote: bool,
    judge_text: str | None,
    in_tok: int | None,
    out_tok: int | None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "docubridge.zone": "6_knowledge_catalog_agent_gym",
        "docubridge.rule_id": rule_id,
        "docubridge.auto_promote": auto_promote,
        "gen_ai.system": "gemini_platform",
        "gen_ai.operation.name": "evaluate_rule",
        "gen_ai.agent.name": "agent_gym_autorater",
        "docubridge.finops.traffic_type": "agent_gym_eval",
    }
    if judge_text is not None:
        attrs["gen_ai.request.model"] = REASONING_MODEL
    if in_tok is not None:
        attrs["gen_ai.usage.input_tokens"] = in_tok
    if out_tok is not None:
        attrs["gen_ai.usage.output_tokens"] = out_tok
    return attrs


class RuleEvaluatorMixin:
    """Mixin providing 3-Tier Synthetic Stress Testing and Quality Report Card Gate."""

    def _invoke_reasoning_model_if_enabled(
        self,
        *,
        prompt: str,
    ) -> tuple[str | None, int | None, int | None]:
        raise NotImplementedError

    def _get_rule(self, rule_id: str) -> TribalKnowledgeRule | None:
        raise NotImplementedError

    def _get_signal(self, signal_id: str) -> ExpertFeedbackSignal | None:
        raise NotImplementedError

    def _list_all_rules(self) -> list[TribalKnowledgeRule]:
        raise NotImplementedError

    def _save_rule(self, rule: TribalKnowledgeRule) -> None:
        raise NotImplementedError

    def _save_report(self, report: GymReportCard) -> None:
        raise NotImplementedError

    def evaluate_candidate_rule(
        self,
        rule_id: str,
        *,
        auto_promote: bool = False,
    ) -> GymReportCard:
        """Run 3-Tier Synthetic Stress Testing and Quality Report Card Gate on a candidate rule."""
        rule = self._get_rule(rule_id)
        if rule is None:
            raise KeyError(f"TribalKnowledgeRule '{rule_id}' not found.")
        if rule.status != TribalRuleStatus.CANDIDATE:
            raise ValueError(
                f"TribalKnowledgeRule '{rule_id}' has status '{rule.status.value}'; "
                "only candidate rules can be evaluated or promoted."
            )
        signal = self._get_signal(rule.source_signal_id)
        if signal is None:
            raise KeyError(
                f"Source ExpertFeedbackSignal '{rule.source_signal_id}' not found "
                f"for rule '{rule_id}'."
            )

        judge_text, in_tok, out_tok = self._invoke_reasoning_model_if_enabled(
            prompt=f"Evaluate candidate TribalKnowledgeRule '{rule_id}' across 4 stress perturbations.",
        )
        span_attrs = _build_eval_span_attrs(
            rule_id=rule_id,
            auto_promote=auto_promote,
            judge_text=judge_text,
            in_tok=in_tok,
            out_tok=out_tok,
        )

        with trace_step("docubridge.agent_gym.evaluate_rule", span_attrs) as span:
            math_firewall_intact = not any(
                tok in rule.instruction.lower() for tok in _FORBIDDEN_MATH_TOKENS
            )
            tier1_rule0_passed, tier3_corridor_fa_zero, target_issues = (
                _check_tier1_and_tier3(
                    rule, signal, math_firewall_intact=math_firewall_intact
                )
            )
            stress_cases = generate_synthetic_stress_suite(
                signal, rule, math_firewall_intact=math_firewall_intact
            )
            live_judge_passed, tier2_autorater_score, judge_regression = (
                _parse_live_judge_verdict(judge_text)
            )
            initial_issues = [
                *target_issues,
                *([judge_regression] if judge_regression is not None else []),
            ]
            regressions = _collect_rule_regressions(
                self._list_all_rules(),
                rule,
                initial_issues=initial_issues,
                math_firewall_intact=math_firewall_intact,
                stress_cases=stress_cases,
            )

            baseline_passed = sum(1 for c in stress_cases if c.baseline_passed)
            candidate_passed = (
                sum(1 for c in stress_cases if c.candidate_passed)
                if math_firewall_intact
                else 0
            )
            total_cases = len(stress_cases)
            baseline_score = round(baseline_passed / total_cases, 4)
            candidate_score = round(candidate_passed / total_cases, 4)
            delta = round(candidate_score - baseline_score, 4)

            live_judge_ran = judge_text is not None
            judge_model = REASONING_MODEL if live_judge_ran else None

            passed_gate = (
                delta > 0
                and not regressions
                and tier1_rule0_passed
                and tier3_corridor_fa_zero
                and live_judge_passed
            )
            if passed_gate:
                rule.status = (
                    TribalRuleStatus.PROMOTED
                    if auto_promote
                    else TribalRuleStatus.VERIFIED
                )
                if auto_promote:
                    rule.promoted_at = datetime.now(UTC)
                judge_note = (
                    f"{REASONING_MODEL} judge"
                    if live_judge_ran
                    else "deterministic stress gate (live judge not evaluated)"
                )
                summary = (
                    f"PASSED 3-Tier Quality Gate ({judge_note} over {WORKHORSE_MODEL}: "
                    f"+{int(delta * 100)}% on '{rule.issuer_pattern}:{rule.doctype}', "
                    "Tier 1 Rule 0 & Decimal firewall intact, Tier 3 FA=0 corridor verified)."
                )
            else:
                rule.status = TribalRuleStatus.REJECTED
                summary = (
                    f"BLOCKED by Quality Report Card Gate: regressions={regressions}, "
                    f"math_firewall_intact={math_firewall_intact}."
                )

            self._save_rule(rule)

            report = GymReportCard(
                report_id=f"rep-{uuid.uuid4().hex[:8]}",
                rule_id=rule.rule_id,
                target_slice=f"{rule.issuer_pattern}:{rule.doctype}:{rule.field_path}",
                baseline_slice_score=baseline_score,
                candidate_slice_score=candidate_score,
                slice_improvement_delta=delta,
                stress_cases=stress_cases,
                stress_cases_passed=candidate_passed,
                stress_cases_total=total_cases,
                historical_benchmark_total=None,
                historical_regressions=tuple(regressions),
                math_firewall_intact=math_firewall_intact,
                workhorse_model=WORKHORSE_MODEL,
                judge_model=judge_model,
                tier1_rule0_passed=tier1_rule0_passed,
                tier2_autorater_score=tier2_autorater_score,
                tier3_corridor_fa_zero=tier3_corridor_fa_zero,
                passed_gate=passed_gate,
                gate_summary=summary,
            )
            self._save_report(report)
            for attr_key, attr_val in (
                ("docubridge.passed_gate", passed_gate),
                ("docubridge.math_firewall_intact", math_firewall_intact),
                ("docubridge.tier1_rule0_passed", tier1_rule0_passed),
                ("docubridge.tier3_corridor_fa_zero", tier3_corridor_fa_zero),
                ("docubridge.stress_cases_passed", candidate_passed),
                ("docubridge.stress_cases_total", total_cases),
                ("docubridge.rule_status", rule.status.value),
            ):
                span.set_attribute(attr_key, attr_val)
            if tier2_autorater_score is not None:
                span.set_attribute(
                    "docubridge.tier2_autorater_score", tier2_autorater_score
                )
            return report


__all__ = ["RuleEvaluatorMixin"]
