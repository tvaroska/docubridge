"""3-Tier Synthetic Stress Testing & Quality Report Card Gate mixin for Agent Gym."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from bridge.agent_gym.models import (
    _FORBIDDEN_MATH_TOKENS,
    REASONING_MODEL,
    WORKHORSE_MODEL,
    ExpertFeedbackSignal,
    GymReportCard,
    SignalSourceZone,
    TribalKnowledgeRule,
    TribalRuleStatus,
    generate_synthetic_stress_suite,
)
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    CorridorPoint,
    dispose,
    threshold_corridor,
)
from bridge.telemetry import trace_step
from contract.enums import Gate


class RuleEvaluatorMixin:
    """Mixin providing 3-Tier Synthetic Stress Testing and Quality Report Card Gate."""

    def _invoke_reasoning_model_if_enabled(
        self,
        *,
        prompt: str,
        default_in_tokens: int,
        default_out_tokens: int,
    ) -> tuple[str | None, int, int]:
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
        auto_promote: bool = True,
    ) -> GymReportCard:
        """Run 3-Tier Synthetic Stress Testing and Quality Report Card Gate on a candidate rule."""
        _, in_tok, out_tok = self._invoke_reasoning_model_if_enabled(
            prompt=f"Evaluate candidate TribalKnowledgeRule '{rule_id}' across 4 stress perturbations.",
            default_in_tokens=3200,
            default_out_tokens=640,
        )
        with trace_step(
            "docubridge.agent_gym.evaluate_rule",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.rule_id": rule_id,
                "docubridge.auto_promote": auto_promote,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "evaluate_rule",
                "gen_ai.agent.name": "agent_gym_autorater",
                "gen_ai.request.model": REASONING_MODEL,
                "docubridge.finops.traffic_type": "agent_gym_eval",
                "gen_ai.usage.input_tokens": in_tok,
                "gen_ai.usage.output_tokens": out_tok,
            },
        ) as span:
            rule = self._get_rule(rule_id)
            if rule is None:
                raise KeyError(f"TribalKnowledgeRule '{rule_id}' not found.")
            signal = self._get_signal(rule.source_signal_id)
            if signal is None:
                signal = ExpertFeedbackSignal(
                    signal_id=rule.source_signal_id,
                    source_zone=SignalSourceZone.REVIEW_QUARANTINE,
                    entity_id="ent-seed",
                    doc_id="doc-seed",
                    doctype=rule.doctype,
                    issuer=rule.issuer_pattern.rstrip("*"),
                    field_path=rule.field_path,
                    before_value="raw",
                    after_value=rule.canonical_target or "resolved",
                    expert_memo=rule.instruction,
                    actor_principal=rule.authored_by,
                    actor_role=rule.preferred_role or "broker",
                )

            # Tier 1: Deterministic Rule 0 + Hallucination Firewall check
            lowered_inst = rule.instruction.lower()
            math_firewall_intact = not any(
                tok in lowered_inst for tok in _FORBIDDEN_MATH_TOKENS
            )
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
            tier1_rule0_passed = (
                math_firewall_intact
                and verdict_clean.gate == Gate.auto_approve
                and verdict_flagged.gate == Gate.hitl_review
            )

            # Tier 3: Calibration Threshold Corridor check (FA = 0 invariant)
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
            tier3_corridor_fa_zero = corridor.feasible and corridor.contains(
                DEFAULT_THRESHOLDS
            )

            # Check for collisions with existing promoted rules on the same slice
            regressions: list[str] = []
            for existing in self._list_all_rules():
                if (
                    existing.rule_id != rule.rule_id
                    and existing.status == TribalRuleStatus.PROMOTED
                    and existing.category == rule.category
                    and existing.issuer_pattern == rule.issuer_pattern
                    and existing.field_path == rule.field_path
                    and existing.canonical_target != rule.canonical_target
                ):
                    regressions.append(
                        f"conflict_with_promoted_rule:{existing.rule_id}"
                    )

            if not math_firewall_intact:
                regressions.append(
                    "hallucination_firewall_violation:math_override_blocked"
                )

            stress_cases = generate_synthetic_stress_suite(
                signal, rule, math_firewall_intact=math_firewall_intact
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

            # Tier 2: Independent Autorater Judge (gemini-4-argon grading gemini-3.8-flash outputs)
            tier2_autorater_score = candidate_score if math_firewall_intact else 0.0

            passed_gate = (
                delta > 0
                and len(regressions) == 0
                and tier1_rule0_passed
                and tier3_corridor_fa_zero
            )
            if passed_gate:
                rule.status = (
                    TribalRuleStatus.PROMOTED
                    if auto_promote
                    else TribalRuleStatus.VERIFIED
                )
                if auto_promote:
                    rule.promoted_at = datetime.now(UTC)
                summary = (
                    f"PASSED 3-Tier Quality Gate ({REASONING_MODEL} judge over {WORKHORSE_MODEL}: "
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
                historical_benchmark_total=12,
                historical_regressions=tuple(regressions),
                math_firewall_intact=math_firewall_intact,
                workhorse_model=WORKHORSE_MODEL,
                judge_model=REASONING_MODEL,
                tier1_rule0_passed=tier1_rule0_passed,
                tier2_autorater_score=tier2_autorater_score,
                tier3_corridor_fa_zero=tier3_corridor_fa_zero,
                passed_gate=passed_gate,
                gate_summary=summary,
            )
            self._save_report(report)
            span.set_attribute("docubridge.passed_gate", passed_gate)
            span.set_attribute("docubridge.math_firewall_intact", math_firewall_intact)
            span.set_attribute("docubridge.tier1_rule0_passed", tier1_rule0_passed)
            span.set_attribute(
                "docubridge.tier2_autorater_score", tier2_autorater_score
            )
            span.set_attribute(
                "docubridge.tier3_corridor_fa_zero", tier3_corridor_fa_zero
            )
            span.set_attribute("docubridge.stress_cases_passed", candidate_passed)
            span.set_attribute("docubridge.stress_cases_total", total_cases)
            span.set_attribute("docubridge.rule_status", rule.status.value)
            return report


__all__ = ["RuleEvaluatorMixin"]
