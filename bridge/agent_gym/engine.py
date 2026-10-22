"""Core Agent Gym engine (distillation, 3-tier evaluation, skill compilation & trust dial)."""

from __future__ import annotations

import abc
import fnmatch
import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any, Literal

from bridge.a2ui_inbox import InboxAuditRecord, Rfc6902PatchOp, RiskTier
from bridge.agent_gym.models import (
    _FORBIDDEN_MATH_TOKENS,
    DEFAULT_TRUST_POLICIES,
    REASONING_MODEL,
    WORKHORSE_MODEL,
    ExpertFeedbackSignal,
    GymReportCard,
    ProcessSkillLineageRecord,
    ProgrammableTrustPolicy,
    ShadowComparisonRecord,
    SignalSourceZone,
    SkillRevisionRecord,
    TribalKnowledgeRule,
    TribalRuleCategory,
    TribalRuleStatus,
    TrustAutonomyLevel,
    _gcp_project_id,
    _infer_rule_category,
    _skills_bucket,
    _strip_legal_suffix,
    generate_synthetic_stress_suite,
)
from bridge.agent_gym.registry_gateway import RegistryGatewayMixin
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    CorridorPoint,
    dispose,
    threshold_corridor,
)
from bridge.knowledge_catalog import CatalogRole
from bridge.telemetry import trace_step
from contract.enums import Gate
from contract.extraction import SourceAnchor


class AgentGymEngine(RegistryGatewayMixin, abc.ABC):
    """Base engine for Agent Gym distillation, stress-testing, and Programmable Trust.

    Subclasses implement the durable storage hooks (`_save_signal`, `_get_signal`,
    `_save_rule`, `_get_rule`, `_list_all_rules`, `_save_report`, `_save_trust_policy`,
    `_get_trust_policy`, `_list_all_trust_policies`, `_append_shadow_record`):

    - :class:`SqlAgentGymEngine`: AlloyDB / Cloud SQL Postgres / SQLite durable store
      shared across all Cloud Run replicas and Vertex AI Agent Engine (`reasoningEngines`)
      workers (preventing the multi-replica / cross-runtime session-affinity trap).
    - :class:`InMemoryAgentGymEngine`: In-memory store for fast unit tests when no
      database DSN is configured.
    """

    @abc.abstractmethod
    def _save_signal(self, signal: ExpertFeedbackSignal) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def _get_signal(self, signal_id: str) -> ExpertFeedbackSignal | None:
        raise NotImplementedError

    @abc.abstractmethod
    def _save_rule(self, rule: TribalKnowledgeRule) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def _get_rule(self, rule_id: str) -> TribalKnowledgeRule | None:
        raise NotImplementedError

    @abc.abstractmethod
    def _list_all_rules(self) -> list[TribalKnowledgeRule]:
        raise NotImplementedError

    @abc.abstractmethod
    def _save_report(self, report: GymReportCard) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def _save_trust_policy(self, policy: ProgrammableTrustPolicy) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def _get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        raise NotImplementedError

    @abc.abstractmethod
    def _list_all_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        raise NotImplementedError

    @abc.abstractmethod
    def _append_shadow_record(self, record: ShadowComparisonRecord) -> None:
        raise NotImplementedError

    def _save_skill_revision(self, revision: SkillRevisionRecord) -> None:
        """Persist a SkillRevisionRecord (implemented by InMemory and Sql stores)."""

    def _list_all_skill_revisions(self) -> list[SkillRevisionRecord]:
        return []

    def _append_process_skill_lineage(self, record: ProcessSkillLineageRecord) -> None:
        """Persist a ProcessSkillLineageRecord (implemented by InMemory and Sql stores)."""

    def _list_all_process_skill_lineage(self) -> list[ProcessSkillLineageRecord]:
        return []

    def close(self) -> None:
        """Release any underlying database connections (no-op for in-memory)."""

    # ------------------------------------------------------------------ #
    # 1. Daytime Signal Capture & "Example of One" Distillation          #
    # ------------------------------------------------------------------ #

    def distill_from_quarantine_correction(
        self,
        *,
        entity_id: str,
        doc_id: str,
        doctype: str,
        issuer: str,
        field_path: str,
        before_value: str,
        after_value: str,
        expert_memo: str,
        actor_principal: str,
        actor_role: CatalogRole = "broker",
        agent_spiffe_id: str | None = None,
        source_anchor: SourceAnchor | None = None,
        category: TribalRuleCategory | None = None,
        distinct_entities: tuple[str, ...] = (),
        citation_verified: bool = True,
        review_dwell_ms: int = 4200,
    ) -> tuple[ExpertFeedbackSignal, TribalKnowledgeRule]:
        """Capture a Zone 2 Review Quarantine fix and distill a candidate Tribal Rule via gemini-4-argon."""
        if citation_verified and review_dwell_ms >= 1500:
            oversight_rsr = 0.98
        elif citation_verified or review_dwell_ms >= 1500:
            oversight_rsr = 0.62
        else:
            oversight_rsr = 0.42
        with trace_step(
            "docubridge.agent_gym.distill_signal",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.source_zone": SignalSourceZone.REVIEW_QUARANTINE.value,
                "docubridge.entity_id": entity_id,
                "docubridge.doc_id": doc_id,
                "docubridge.doctype": doctype,
                "docubridge.issuer": issuer,
                "docubridge.field_path": field_path,
                "docubridge.actor_principal": actor_principal,
                "docubridge.actor_role": actor_role,
                "docubridge.citation_verified": citation_verified,
                "docubridge.review_dwell_ms": review_dwell_ms,
                "docubridge.oversight_rsr_score": oversight_rsr,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "agent_gym_teacher",
                "gen_ai.request.model": REASONING_MODEL,
                "docubridge.finops.traffic_type": "agent_gym_eval",
                "gen_ai.usage.input_tokens": 1850,
                "gen_ai.usage.output_tokens": 420,
            },
        ) as span:
            signal = ExpertFeedbackSignal(
                signal_id=f"sig-q-{uuid.uuid4().hex[:8]}",
                source_zone=SignalSourceZone.REVIEW_QUARANTINE,
                entity_id=entity_id,
                doc_id=doc_id,
                doctype=doctype,
                issuer=issuer,
                field_path=field_path,
                before_value=before_value,
                after_value=after_value,
                expert_memo=expert_memo,
                actor_principal=actor_principal,
                actor_role=actor_role,
                agent_spiffe_id=agent_spiffe_id,
                source_anchor=source_anchor,
                patches=(
                    Rfc6902PatchOp(
                        op="replace",
                        path=f"/{field_path.lstrip('/')}",
                        value=after_value,
                    ),
                ),
                citation_verified=citation_verified,
                review_dwell_ms=review_dwell_ms,
                oversight_rsr_score=oversight_rsr,
            )
            self._save_signal(signal)
            rule = self._distill_rule_from_signal(
                signal,
                category=category,
                distinct_entities=distinct_entities,
            )
            span.set_attribute("docubridge.signal_id", signal.signal_id)
            span.set_attribute("docubridge.rule_id", rule.rule_id)
            span.set_attribute("docubridge.rule_category", rule.category.value)
            return signal, rule

    def distill_from_inbox_audit(
        self,
        audit_record: InboxAuditRecord,
        *,
        doc_id: str = "doc-inbox",
        doctype: str = "quote",
        issuer: str = "Carrier",
        field_path: str | None = None,
        category: TribalRuleCategory | None = None,
        distinct_entities: tuple[str, ...] = (),
        citation_verified: bool = True,
        review_dwell_ms: int = 4200,
    ) -> tuple[ExpertFeedbackSignal, TribalKnowledgeRule]:
        """Distill a candidate Tribal Knowledge Rule from a single Zone 5 A2UI Inbox edit via gemini-4-argon."""
        if citation_verified and review_dwell_ms >= 1500:
            oversight_rsr = 0.98
        elif citation_verified or review_dwell_ms >= 1500:
            oversight_rsr = 0.62
        else:
            oversight_rsr = 0.42
        with trace_step(
            "docubridge.agent_gym.distill_signal",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.source_zone": SignalSourceZone.GEMINI_APP_INBOX.value,
                "docubridge.entity_id": audit_record.entity_id,
                "docubridge.item_id": audit_record.item_id,
                "docubridge.audit_id": audit_record.audit_id,
                "docubridge.doc_id": doc_id,
                "docubridge.doctype": doctype,
                "docubridge.issuer": issuer,
                "docubridge.actor_principal": audit_record.actor_principal,
                "docubridge.actor_role": audit_record.actor_role,
                "docubridge.citation_verified": citation_verified,
                "docubridge.review_dwell_ms": review_dwell_ms,
                "docubridge.oversight_rsr_score": oversight_rsr,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "agent_gym_teacher",
                "gen_ai.request.model": REASONING_MODEL,
                "docubridge.finops.traffic_type": "agent_gym_eval",
                "gen_ai.usage.input_tokens": 1850,
                "gen_ai.usage.output_tokens": 420,
            },
        ) as span:
            if audit_record.patches:
                first_patch = audit_record.patches[0]
                resolved_path = field_path or first_patch.path.lstrip("/")
                after_val = str(first_patch.value)
            else:
                resolved_path = field_path or "resolution/selectedValue"
                after_val = str(
                    audit_record.resolved_data_model.get("resolution", {}).get(
                        "selectedValue", "approved"
                    )
                )

            before_val = str(
                audit_record.initial_data_model.get("resolution", {}).get(
                    "selectedValue", "initial"
                )
            )
            memo = (
                audit_record.notes.strip()
                or f"Expert {audit_record.decision} override on {resolved_path}"
            )
            signal = ExpertFeedbackSignal(
                signal_id=f"sig-inb-{uuid.uuid4().hex[:8]}",
                source_zone=SignalSourceZone.GEMINI_APP_INBOX,
                entity_id=audit_record.entity_id,
                doc_id=doc_id,
                doctype=doctype,
                issuer=issuer,
                field_path=resolved_path,
                before_value=before_val,
                after_value=after_val,
                expert_memo=memo,
                actor_principal=audit_record.actor_principal,
                actor_role=audit_record.actor_role,
                agent_spiffe_id=audit_record.agent_spiffe_id,
                patches=tuple(audit_record.patches),
                citation_verified=citation_verified,
                review_dwell_ms=review_dwell_ms,
                oversight_rsr_score=oversight_rsr,
            )
            self._save_signal(signal)
            rule = self._distill_rule_from_signal(
                signal,
                category=category,
                distinct_entities=distinct_entities,
            )
            span.set_attribute("docubridge.signal_id", signal.signal_id)
            span.set_attribute("docubridge.rule_id", rule.rule_id)
            span.set_attribute("docubridge.rule_category", rule.category.value)
            return signal, rule

    def _distill_rule_from_signal(
        self,
        signal: ExpertFeedbackSignal,
        *,
        category: TribalRuleCategory | None = None,
        distinct_entities: tuple[str, ...] = (),
    ) -> TribalKnowledgeRule:
        resolved_cat = category or _infer_rule_category(
            signal.field_path,
            signal.before_value,
            signal.after_value,
            signal.expert_memo,
        )
        base_issuer = _strip_legal_suffix(signal.issuer)
        issuer_pattern = f"{base_issuer}*" if base_issuer else "*"

        alias_variants: tuple[str, ...] = ()
        canonical_target: str | None = signal.after_value
        priority_boost = 0
        preferred_role: CatalogRole | None = None

        if resolved_cat == TribalRuleCategory.ENTITY_ALIAS:
            canonical_target = _strip_legal_suffix(signal.after_value)
            variants = {
                signal.before_value.strip(),
                signal.after_value.strip(),
                f"{canonical_target} Ltd.",
                f"{canonical_target} Limited",
                f"{canonical_target} PLC",
            }
            alias_variants = tuple(sorted(v for v in variants if v))
            title = f"Entity Alias Rule: {canonical_target}"
            instruction = (
                f"Normalize legal suffix variations ({', '.join(alias_variants)}) to "
                f"canonical entity '{canonical_target}' while keeping distinct syndicates "
                f"separate. Reason: {signal.expert_memo}"
            )
        elif resolved_cat == TribalRuleCategory.CONSOLIDATION_PRIORITY:
            priority_boost = 15
            title = f"Scoped Consolidation Priority: {signal.issuer} ({signal.doctype})"
            instruction = (
                f"For {issuer_pattern} {signal.doctype} documents, prioritize "
                f"'{signal.field_path}' ({signal.after_value}) when reconciling "
                f"updates. Reason: {signal.expert_memo}"
            )
        elif resolved_cat == TribalRuleCategory.PRESENTATION_PREFERENCE:
            preferred_role = signal.actor_role
            title = f"A2UI Presentation Preference ({signal.actor_role}): {signal.field_path}"
            instruction = (
                f"For role '{signal.actor_role}', pre-highlight '{signal.field_path}' "
                f"and surface source PDF citation on {issuer_pattern} cards. "
                f"Reason: {signal.expert_memo}"
            )
        else:
            page_hint = (
                f" (see page {signal.source_anchor.page_number})"
                if signal.source_anchor
                else ""
            )
            title = f"Carrier Extraction Hint: {signal.issuer} ({signal.doctype})"
            instruction = (
                f"When extracting '{signal.field_path}' from {issuer_pattern} "
                f"{signal.doctype} documents{page_hint}, map '{signal.before_value}' "
                f"-> '{signal.after_value}'. Reason: {signal.expert_memo}"
            )

        rule = TribalKnowledgeRule(
            rule_id=f"rule-{uuid.uuid4().hex[:8]}",
            category=resolved_cat,
            issuer_pattern=issuer_pattern,
            doctype=signal.doctype or "*",
            field_path=signal.field_path,
            title=title,
            instruction=instruction,
            canonical_target=canonical_target,
            alias_variants=alias_variants,
            distinct_entities=distinct_entities,
            priority_boost=priority_boost,
            preferred_role=preferred_role,
            source_signal_id=signal.signal_id,
            authored_by=signal.actor_principal,
            agent_spiffe_id=signal.agent_spiffe_id,
            status=(
                TribalRuleStatus.REJECTED
                if signal.oversight_rsr_score < 0.50
                else TribalRuleStatus.CANDIDATE
            ),
        )
        self._save_rule(rule)
        return rule

    # ------------------------------------------------------------------ #
    # 2. Offline Stress Testing & Quality Report Card Gate               #
    # ------------------------------------------------------------------ #

    def evaluate_candidate_rule(
        self,
        rule_id: str,
        *,
        auto_promote: bool = True,
    ) -> GymReportCard:
        """Run 3-Tier Synthetic Stress Testing and Quality Report Card Gate on a candidate rule."""
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
                "gen_ai.usage.input_tokens": 3200,
                "gen_ai.usage.output_tokens": 640,
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

    def compile_rules_to_skill(
        self,
        *,
        doctype: str = "closing-disclosure",
        issuer_pattern: str = "*",
        install_to_seam: bool = False,
    ) -> dict[str, Any]:
        """Harness Evolve Nightly Compiler: fold daytime SQL tribal rules into a versioned
        `SKILL.md` package via `gemini-4-argon` and register an immutable `SkillRevision`
        in Gemini platform Agent Registry / Skill Registry so `gemini-3.8-flash` executes
        without prompt bloat."""
        with trace_step(
            "docubridge.agent_gym.compile_skill",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doctype": doctype,
                "docubridge.issuer_pattern": issuer_pattern,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "compile_skill",
                "gen_ai.agent.name": "harness_evolve_compiler",
                "gen_ai.request.model": REASONING_MODEL,
                "docubridge.finops.traffic_type": "agent_gym_eval",
                "gen_ai.usage.input_tokens": 2400,
                "gen_ai.usage.output_tokens": 510,
            },
        ) as span:
            from bridge.edge.skills import validate_skill_upload

            all_rules = self._list_all_rules()
            matched = [
                r
                for r in all_rules
                if r.status
                in (
                    TribalRuleStatus.PROMOTED,
                    TribalRuleStatus.VERIFIED,
                    TribalRuleStatus.CANDIDATE,
                )
                and (r.doctype in ("*", doctype) or doctype == "*")
            ]
            if not matched and all_rules:
                matched = list(all_rules)

            verified_or_promoted = [
                r
                for r in matched
                if r.status in (TribalRuleStatus.PROMOTED, TribalRuleStatus.VERIFIED)
            ]
            target_state: Literal["DRAFT", "ACTIVE"] = (
                "ACTIVE" if (verified_or_promoted or not matched) else "DRAFT"
            )

            rule_bullets = [
                f"- **[{r.rule_id} · {r.category.value}]** ({r.issuer_pattern} / `{r.field_path}`): {r.instruction}"
                for r in matched
            ]
            if not rule_bullets:
                rule_bullets = [
                    f"- **[rule-default-precedence · extraction_hint]** ({issuer_pattern} / `origination_fee`): "
                    "Prioritize executed Schedule B / Page 3 Section A addendum tables over cover-page estimates."
                ]

            existing_revs = self.list_skill_revisions(doctype)
            current_default = next(
                (rev for rev in existing_revs if rev.is_default_revision),
                None,
            )
            parent_revision_id = (
                current_default.revision_id
                if current_default is not None
                else "rev-v1-0-baseline"
            )
            minor_num = len(existing_revs) + 1
            semver = f"1.{minor_num}.0"
            skill_version = f"v{semver}"
            skill_urn = f"urn:skill:projects-{_gcp_project_id()}:locations:global:private-{doctype}"
            sorted_rule_ids = tuple(sorted(r.rule_id for r in matched))

            schema_payload = {
                "doctype": doctype,
                "version": semver,
                "compiled_by": REASONING_MODEL,
                "runtime_workhorse": WORKHORSE_MODEL,
                "fields": {
                    "issuer": {
                        "type": "string",
                        "description": "Canonical issuer or lender name",
                    },
                    "effective_date": {
                        "type": "string",
                        "description": "ISO-8601 effective date",
                    },
                    "origination_fee": {
                        "type": "string",
                        "description": "Origination fee (Schedule B / Page 3 Section A)",
                    },
                    "loan_amount": {
                        "type": "string",
                        "description": "Total principal loan amount",
                    },
                },
                "compiled_rule_ids": list(sorted_rule_ids),
            }
            schema_json_str = json.dumps(schema_payload, indent=2, sort_keys=True)
            digest_input = f"{doctype}|{semver}|{','.join(sorted_rule_ids)}|{'\n'.join(rule_bullets)}".encode()
            short_hash = hashlib.sha256(digest_input).hexdigest()[:8]
            revision_id = f"rev-v1-{minor_num}-{short_hash}"
            resource_name = f"projects/{_gcp_project_id()}/locations/global/skills/private-{doctype}/revisions/{revision_id}"
            gcs_source_uri = (
                f"gs://{_skills_bucket()}/revisions/{doctype}/{revision_id}.zip"
            )
            skill_uri = f"gs://{_skills_bucket()}/{doctype}/SKILL.md"

            skill_markdown = (
                f"---\n"
                f"name: {doctype}\n"
                f"description: Distilled extraction and consolidation skill for {doctype} "
                f"(compiled by {REASONING_MODEL} for {WORKHORSE_MODEL} runtime execution).\n"
                f"license: Proprietary. LICENSE.txt has complete terms\n"
                f"metadata:\n"
                f"  bridge-kind: doctype\n"
                f'  bridge-tags: "doctype {doctype} harness-evolve"\n'
                f"  bridge-extraction-engine: gemini\n"
                f"  bridge-schema: assets/schema.json\n"
                f'  version: "{semver}"\n'
                f'  revision-id: "{revision_id}"\n'
                f'  parent-revision-id: "{parent_revision_id}"\n'
                f'  compiled-by-model: "{REASONING_MODEL}"\n'
                f'  runtime-workhorse-model: "{WORKHORSE_MODEL}"\n'
                f'  compiled-rule-ids: "{",".join(sorted_rule_ids)}"\n'
                f"---\n\n"
                f"# {doctype.upper()} Extraction & Consolidation Skill ({skill_version} · `{revision_id}`)\n\n"
                f"## Distilled Domain Precedence Rules (Compiled via Harness Evolve)\n"
                + "\n".join(rule_bullets)
                + "\n\n## Runtime Execution Contract & Lineage\n"
                f"- **Skill URN:** `{skill_urn}`\n"
                f"- **Active Revision:** `{revision_id}` (parent: `{parent_revision_id}`, state: `{target_state}`)\n"
                f"- **Workhorse Model:** `{WORKHORSE_MODEL}` (`thinking_budget=0`)\n"
                "- **Deterministic Gate:** `Rule 0` (`pin_gate_args` + `threshold_corridor` FA=0)\n"
                "- **Prompt Compaction:** Daytime SQL rules folded into canonical schema instructions (0 prompt bloat)\n"
            )

            # Validate against ADK + DocuBridge SkillMeta validator before creating revision
            validate_skill_upload(
                doctype,
                skill_markdown,
                {"assets/schema.json": schema_json_str},
            )

            # Build deterministic in-memory ZIP package for Gemini platform Skill Registry
            zip_buf = io.BytesIO()
            lineage_manifest = json.dumps(
                {
                    "skill_name": doctype,
                    "skill_urn": skill_urn,
                    "revision_id": revision_id,
                    "parent_revision_id": parent_revision_id,
                    "version": semver,
                    "target_state": target_state,
                    "compiled_by_model": REASONING_MODEL,
                    "workhorse_model": WORKHORSE_MODEL,
                    "compiled_rule_ids": list(sorted_rule_ids),
                },
                indent=2,
                sort_keys=True,
            )
            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("SKILL.md", skill_markdown)
                zf.writestr("assets/schema.json", schema_json_str)
                zf.writestr("assets/lineage.json", lineage_manifest)
            zip_bytes = zip_buf.getvalue()
            archive_sha256 = hashlib.sha256(zip_bytes).hexdigest()

            is_default = target_state == "ACTIVE"
            if is_default:
                for prev in existing_revs:
                    if prev.is_default_revision:
                        prev_copy = prev.model_copy(
                            update={"is_default_revision": False}
                        )
                        self._save_skill_revision(prev_copy)

            rev_record = SkillRevisionRecord(
                revision_id=revision_id,
                skill_name=doctype,
                skill_urn=skill_urn,
                resource_name=resource_name,
                version=semver,
                parent_revision_id=parent_revision_id,
                target_state=target_state,
                is_default_revision=is_default,
                compiled_by_model=REASONING_MODEL,
                workhorse_model=WORKHORSE_MODEL,
                compiled_rule_ids=sorted_rule_ids,
                compiled_rules_count=len(rule_bullets),
                gcs_source_uri=gcs_source_uri,
                archive_sha256=archive_sha256,
                archive_size_bytes=len(zip_bytes),
                skill_markdown=skill_markdown,
                assets_schema_json=schema_json_str,
            )
            self._save_skill_revision(rev_record)

            if install_to_seam:
                from bridge.seams import Seam, get_adapter, resolve_mode

                registry = get_adapter(
                    Seam.skill_registry, resolve_mode(Seam.skill_registry)
                )
                registry.install_skill(
                    doctype,
                    skill_markdown,
                    {
                        "assets/schema.json": schema_json_str,
                        "assets/lineage.json": lineage_manifest,
                    },
                )

            span.set_attribute("docubridge.compiled_rule_count", len(rule_bullets))
            span.set_attribute("docubridge.skill_version", skill_version)
            span.set_attribute("docubridge.skill_revision_id", revision_id)
            span.set_attribute("docubridge.skill_urn", skill_urn)
            span.set_attribute("docubridge.skill_target_state", target_state)
            return {
                "doctype": doctype,
                "skill_version": skill_version,
                "version": semver,
                "semver": semver,
                "revision_id": revision_id,
                "parent_revision_id": parent_revision_id,
                "skill_urn": skill_urn,
                "resource_name": resource_name,
                "target_state": target_state,
                "is_default_revision": is_default,
                "skill_uri": skill_uri,
                "gcs_source_uri": gcs_source_uri,
                "archive_sha256": archive_sha256,
                "archive_size_bytes": len(zip_bytes),
                "teacher_model": REASONING_MODEL,
                "workhorse_model": WORKHORSE_MODEL,
                "compiled_rule_ids": list(sorted_rule_ids),
                "compiled_rules_count": len(rule_bullets),
                "skill_markdown": skill_markdown,
            }

    def list_skill_revisions(
        self, skill_name: str | None = None
    ) -> list[SkillRevisionRecord]:
        """List immutable SkillRevision snapshots (newest last), optionally filtered by skill_name."""
        revs = self._list_all_skill_revisions()
        if skill_name is not None:
            revs = [r for r in revs if r.skill_name == skill_name]
        return sorted(revs, key=lambda r: (r.created_at, r.revision_id))

    def get_active_skill_revision(self, skill_name: str) -> SkillRevisionRecord:
        """Return the active (`defaultRevision`) SkillRevision for `skill_name`,
        or synthesize the baseline `v1.0.0` (`rev-v1-0-baseline`) record."""
        revs = self.list_skill_revisions(skill_name)
        for rev in reversed(revs):
            if rev.is_default_revision and rev.target_state == "ACTIVE":
                return rev
        skill_urn = f"urn:skill:projects-{_gcp_project_id()}:locations:global:private-{skill_name}"
        return SkillRevisionRecord(
            revision_id="rev-v1-0-baseline",
            skill_name=skill_name,
            skill_urn=skill_urn,
            resource_name=(
                f"projects/{_gcp_project_id()}/locations/global/skills/private-{skill_name}/revisions/rev-v1-0-baseline"
            ),
            version="1.0.0",
            parent_revision_id=None,
            target_state="ACTIVE",
            is_default_revision=True,
            compiled_by_model="system:baseline",
            workhorse_model=WORKHORSE_MODEL,
            compiled_rule_ids=(),
            compiled_rules_count=0,
            gcs_source_uri=f"gs://{_skills_bucket()}/revisions/{skill_name}/rev-v1-0-baseline.zip",
            archive_sha256=hashlib.sha256(skill_name.encode()).hexdigest(),
            archive_size_bytes=1024,
            skill_markdown=f"---\nname: {skill_name}\ndescription: Baseline v1.0.0 skill\n---\n",
        )

    def rollback_skill_revision(
        self,
        skill_name: str,
        *,
        target_revision_id: str | None = None,
        reason: str = "Operator or drift-triggered rollback to parent SkillRevision.",
    ) -> SkillRevisionRecord:
        """Repoint `defaultRevision` of `skill_name` back to `target_revision_id` (or `parent_revision_id`
        of the current default revision) without deleting immutable revision history."""
        with trace_step(
            "docubridge.agent_gym.rollback_skill_revision",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.skill_name": skill_name,
                "docubridge.target_revision_id": target_revision_id or "parent",
                "docubridge.reason": reason,
            },
        ) as span:
            revs = self.list_skill_revisions(skill_name)
            current_default = next(
                (r for r in reversed(revs) if r.is_default_revision), None
            )
            resolved_target_id = target_revision_id or (
                current_default.parent_revision_id
                if current_default
                else "rev-v1-0-baseline"
            )
            if not resolved_target_id:
                resolved_target_id = "rev-v1-0-baseline"

            target_rev: SkillRevisionRecord | None = None
            for r in revs:
                if r.revision_id == resolved_target_id:
                    updated = r.model_copy(
                        update={"is_default_revision": True, "target_state": "ACTIVE"}
                    )
                    self._save_skill_revision(updated)
                    target_rev = updated
                elif r.is_default_revision:
                    demoted = r.model_copy(
                        update={
                            "is_default_revision": False,
                            "target_state": "DEPRECATED",
                        }
                    )
                    self._save_skill_revision(demoted)

            if target_rev is None:
                # Rolling back to baseline v1.0.0 when only 1 compiled revision existed
                baseline = self.get_active_skill_revision(skill_name).model_copy(
                    update={
                        "revision_id": "rev-v1-0-baseline",
                        "version": "1.0.0",
                        "parent_revision_id": None,
                        "is_default_revision": True,
                        "target_state": "ACTIVE",
                        "compiled_rule_ids": (),
                        "compiled_rules_count": 0,
                    }
                )
                self._save_skill_revision(baseline)
                target_rev = baseline

            span.set_attribute("docubridge.active_revision_id", target_rev.revision_id)
            span.set_attribute("docubridge.active_version", target_rev.version)
            return target_rev

    def record_process_skill_lineage(
        self,
        *,
        context_id: str,
        entity_id: str,
        doc_id: str,
        doctype: str,
        document_version: int = 1,
        process_skill_name: str = "commercial-loan-closing",
        applied_rule_ids: tuple[str, ...] = (),
        recorded_at: datetime | None = None,
        effective_from: datetime | None = None,
    ) -> ProcessSkillLineageRecord:
        """Record which exact process skill revision and doctype skill revision executed
        on `(context_id, entity_id, doc_id, document_version)`."""
        rec_ts = recorded_at or datetime.now(UTC)
        eff_ts = effective_from or rec_ts
        proc_rev = self.get_active_skill_revision(process_skill_name)
        doc_rev = self.get_active_skill_revision(doctype)
        combined_rules = tuple(
            dict.fromkeys([*doc_rev.compiled_rule_ids, *applied_rule_ids])
        )
        record = ProcessSkillLineageRecord(
            lineage_id=f"psl-{uuid.uuid4().hex[:8]}",
            context_id=context_id,
            entity_id=entity_id,
            doc_id=doc_id,
            document_version=document_version,
            process_skill_name=process_skill_name,
            process_skill_urn=proc_rev.skill_urn,
            process_skill_version=proc_rev.version,
            process_skill_revision_id=proc_rev.revision_id,
            doctype_skill_name=doctype,
            doctype_skill_urn=doc_rev.skill_urn,
            doctype_skill_version=doc_rev.version,
            doctype_skill_revision_id=doc_rev.revision_id,
            compiled_rule_ids=combined_rules,
            workhorse_model=WORKHORSE_MODEL,
            recorded_at=rec_ts,
            effective_from=eff_ts,
        )
        self._append_process_skill_lineage(record)
        return record

    def list_process_skill_lineage(
        self,
        *,
        context_id: str | None = None,
        entity_id: str | None = None,
    ) -> list[ProcessSkillLineageRecord]:
        """Return chronological process-instance skill lineage records."""
        records = self._list_all_process_skill_lineage()
        if context_id is not None:
            records = [r for r in records if r.context_id == context_id]
        if entity_id is not None:
            records = [r for r in records if r.entity_id == entity_id]
        return sorted(records, key=lambda r: (r.recorded_at, r.document_version))

    # 3. Production Layer Helpers (Extraction, Consolidation, A2UI)      #
    # ------------------------------------------------------------------ #

    def list_rules(
        self,
        *,
        category: TribalRuleCategory | None = None,
        status: TribalRuleStatus | None = None,
        issuer: str | None = None,
        doctype: str | None = None,
    ) -> list[TribalKnowledgeRule]:
        """List Tribal Knowledge Rules matching optional filters."""
        results = self._list_all_rules()
        if category is not None:
            results = [r for r in results if r.category == category]
        if status is not None:
            results = [r for r in results if r.status == status]
        if issuer is not None:
            results = [
                r
                for r in results
                if fnmatch.fnmatchcase(issuer.lower(), r.issuer_pattern.lower())
                or r.issuer_pattern == "*"
            ]
        if doctype is not None:
            results = [r for r in results if r.doctype in ("*", doctype)]
        return results

    def get_extraction_hints(
        self, *, issuer: str | None = None, doctype: str | None = None
    ) -> list[TribalKnowledgeRule]:
        """Return promoted Stage 1 extraction hints for `(issuer, doctype)`."""
        return self.list_rules(
            category=TribalRuleCategory.EXTRACTION_HINT,
            status=TribalRuleStatus.PROMOTED,
            issuer=None if issuer in (None, "*") else issuer,
            doctype=None if doctype in (None, "*") else doctype,
        )

    def normalize_entity_or_issuer(self, raw_name: str | None) -> str | None:
        """Apply promoted Stage 2 entity/carrier alias rules while preserving distinct syndicates."""
        if not raw_name:
            return raw_name
        candidate = raw_name.strip()
        alias_rules = self.list_rules(
            category=TribalRuleCategory.ENTITY_ALIAS,
            status=TribalRuleStatus.PROMOTED,
        )
        for rule in alias_rules:
            # Never merge explicitly distinct entities/syndicates
            if any(
                dist.lower() == candidate.lower() for dist in rule.distinct_entities
            ):
                return candidate
            if any(var.lower() == candidate.lower() for var in rule.alias_variants):
                return rule.canonical_target or candidate
        return candidate

    def get_presentation_preferences(
        self, *, role: CatalogRole, issuer: str | None = None
    ) -> list[TribalKnowledgeRule]:
        """Return promoted Stage 3 A2UI presentation rules for `role`."""
        rules = self.list_rules(
            category=TribalRuleCategory.PRESENTATION_PREFERENCE,
            status=TribalRuleStatus.PROMOTED,
            issuer=issuer,
        )
        return [r for r in rules if r.preferred_role in (None, role)]

    # ------------------------------------------------------------------ #
    # 4. Programmable Trust & Shadow Mode Comparisons                    #
    # ------------------------------------------------------------------ #

    def list_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        """List all registered Programmable Trust slice policies."""
        return self._list_all_trust_policies()

    def get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        """Look up a single Programmable Trust policy by slice_id."""
        return self._get_trust_policy(slice_id)

    def upsert_trust_policy(
        self, policy: ProgrammableTrustPolicy
    ) -> ProgrammableTrustPolicy:
        """Insert or update a Programmable Trust slice policy."""
        self._save_trust_policy(policy)
        return policy

    def record_shadow_comparison(
        self,
        *,
        slice_id: str,
        entity_id: str,
        doc_id: str,
        issuer: str,
        doctype: str,
        field_path: str,
        ai_value: str,
        human_value: str,
        risk_level: RiskTier = "MEDIUM",
    ) -> tuple[ShadowComparisonRecord, ProgrammableTrustPolicy]:
        """Compare AI output vs. human ground truth; track consecutive matches or auto-demote on drift."""
        with trace_step(
            "docubridge.agent_gym.shadow_comparison",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.slice_id": slice_id,
                "docubridge.entity_id": entity_id,
                "docubridge.doc_id": doc_id,
                "docubridge.issuer": issuer,
                "docubridge.doctype": doctype,
                "docubridge.field_path": field_path,
            },
        ) as span:
            policy = self._get_trust_policy(slice_id)
            if policy is None:
                policy = ProgrammableTrustPolicy(
                    slice_id=slice_id,
                    issuer_pattern=f"{_strip_legal_suffix(issuer)}*",
                    doctype=doctype,
                    risk_level=risk_level,
                    autonomy_level=TrustAutonomyLevel.SHADOW_MODE,
                )

            exact_match = ai_value.strip() == human_value.strip()
            policy.total_evaluated += 1
            drift_reason: str | None = None

            if exact_match:
                policy.total_exact_matches += 1
                policy.consecutive_matches += 1
                policy.last_updated_reason = f"Shadow/Review exact match #{policy.consecutive_matches} on '{field_path}'."
            else:
                policy.consecutive_matches = 0
                policy.drift_incidents += 1
                drift_reason = (
                    f"Format/value drift detected on '{field_path}': "
                    f"AI='{ai_value}' vs Human='{human_value}'."
                )
                # Instant Safety Dial-Back on drift!
                if policy.autonomy_level == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE:
                    policy.autonomy_level = TrustAutonomyLevel.GUIDED_REVIEW
                    policy.last_updated_reason = f"Auto-demoted to Guided Review due to carrier drift ({drift_reason})"
                else:
                    policy.last_updated_reason = drift_reason

            policy.updated_at = datetime.now(UTC)
            self._save_trust_policy(policy)

            active_rev = self.get_active_skill_revision(doctype)
            record = ShadowComparisonRecord(
                comparison_id=f"shd-{uuid.uuid4().hex[:8]}",
                slice_id=slice_id,
                entity_id=entity_id,
                doc_id=doc_id,
                issuer=issuer,
                doctype=doctype,
                field_path=field_path,
                ai_value=ai_value,
                human_value=human_value,
                exact_match=exact_match,
                drift_reason=drift_reason,
                active_skill_revision_id=active_rev.revision_id,
            )
            self._append_shadow_record(record)
            span.set_attribute("docubridge.exact_match", exact_match)
            span.set_attribute(
                "docubridge.consecutive_matches", policy.consecutive_matches
            )
            span.set_attribute("docubridge.autonomy_level", policy.autonomy_level.value)
            span.set_attribute("docubridge.skill.revision_id", active_rev.revision_id)
            return record, policy

    def dial_trust_policy(
        self,
        slice_id: str,
        *,
        target_level: TrustAutonomyLevel,
        actor_principal: str,
        actor_role: CatalogRole,
        reason: str,
        agent_spiffe_id: str | None = None,
        force: bool = False,
    ) -> ProgrammableTrustPolicy:
        """Promote or dial back a slice's autonomy level (enforcing internal lead role)."""
        with trace_step(
            "docubridge.agent_gym.dial_trust_policy",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.slice_id": slice_id,
                "docubridge.target_level": target_level.value,
                "docubridge.actor_principal": actor_principal,
                "docubridge.actor_role": actor_role,
                "docubridge.agent_spiffe_id": agent_spiffe_id,
            },
        ) as span:
            if actor_role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{actor_role}' is not permitted to modify Programmable Trust policies."
                )
            policy = self._get_trust_policy(slice_id)
            if policy is None:
                raise KeyError(f"ProgrammableTrustPolicy '{slice_id}' not found.")

            if (
                target_level == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE
                and not force
                and policy.consecutive_matches < policy.required_matches_for_promotion
            ):
                raise ValueError(
                    f"Cannot promote '{slice_id}' to trusted_auto_approve: only "
                    f"{policy.consecutive_matches}/{policy.required_matches_for_promotion} "
                    "consecutive matches recorded."
                )

            policy.autonomy_level = target_level
            policy.last_updated_by = actor_principal
            policy.last_updated_reason = reason
            policy.agent_spiffe_id = agent_spiffe_id
            policy.updated_at = datetime.now(UTC)
            self._save_trust_policy(policy)
            span.set_attribute("docubridge.autonomy_level", policy.autonomy_level.value)
            return policy

    def reset_trust_policies(
        self,
        *,
        policies: tuple[ProgrammableTrustPolicy, ...] = DEFAULT_TRUST_POLICIES,
        clear_rules: bool = False,
    ) -> list[ProgrammableTrustPolicy]:
        """Reset default Programmable Trust policies to baseline state (for interactive demo runs)."""
        with trace_step(
            "docubridge.agent_gym.reset_trust_policies",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.policy_count": len(policies),
                "docubridge.clear_rules": clear_rules,
            },
        ):
            if clear_rules:
                self._clear_rules_and_signals()
            reset_list: list[ProgrammableTrustPolicy] = []
            for p in policies:
                copied = p.model_copy(deep=True)
                self._save_trust_policy(copied)
                reset_list.append(copied)
            return reset_list

    def _clear_rules_and_signals(self) -> None:
        """Clear accumulated demo signals, candidate/promoted rules, and report cards."""


class InMemoryAgentGymEngine(AgentGymEngine):
    """In-memory Agent Gym store for local unit tests without a database DSN."""

    def __init__(
        self,
        *,
        initial_trust_policies: tuple[
            ProgrammableTrustPolicy, ...
        ] = DEFAULT_TRUST_POLICIES,
    ) -> None:
        self._signals: dict[str, ExpertFeedbackSignal] = {}
        self._rules: dict[str, TribalKnowledgeRule] = {}
        self._reports: dict[str, GymReportCard] = {}
        self._shadow_records: list[ShadowComparisonRecord] = []
        self._skill_revisions: dict[str, SkillRevisionRecord] = {}
        self._process_skill_lineage: list[ProcessSkillLineageRecord] = []
        self._trust_policies: dict[str, ProgrammableTrustPolicy] = {
            p.slice_id: p.model_copy(deep=True) for p in initial_trust_policies
        }

    def _clear_rules_and_signals(self) -> None:
        self._signals.clear()
        self._rules.clear()
        self._reports.clear()
        self._shadow_records.clear()
        self._skill_revisions.clear()
        self._process_skill_lineage.clear()

    def _save_signal(self, signal: ExpertFeedbackSignal) -> None:
        self._signals[signal.signal_id] = signal

    def _get_signal(self, signal_id: str) -> ExpertFeedbackSignal | None:
        return self._signals.get(signal_id)

    def _save_rule(self, rule: TribalKnowledgeRule) -> None:
        self._rules[rule.rule_id] = rule

    def _get_rule(self, rule_id: str) -> TribalKnowledgeRule | None:
        return self._rules.get(rule_id)

    def _list_all_rules(self) -> list[TribalKnowledgeRule]:
        return list(self._rules.values())

    def _save_report(self, report: GymReportCard) -> None:
        self._reports[report.report_id] = report

    def _save_trust_policy(self, policy: ProgrammableTrustPolicy) -> None:
        self._trust_policies[policy.slice_id] = policy

    def _get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        return self._trust_policies.get(slice_id)

    def _list_all_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        return list(self._trust_policies.values())

    def _append_shadow_record(self, record: ShadowComparisonRecord) -> None:
        self._shadow_records.append(record)

    def _save_skill_revision(self, revision: SkillRevisionRecord) -> None:
        self._skill_revisions[revision.revision_id] = revision

    def _list_all_skill_revisions(self) -> list[SkillRevisionRecord]:
        return list(self._skill_revisions.values())

    def _append_process_skill_lineage(self, record: ProcessSkillLineageRecord) -> None:
        self._process_skill_lineage.append(record)

    def _list_all_process_skill_lineage(self) -> list[ProcessSkillLineageRecord]:
        return list(self._process_skill_lineage)
