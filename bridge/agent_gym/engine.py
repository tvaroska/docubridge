"""Core Agent Gym engine (distillation, 3-tier evaluation, skill compilation & trust dial)."""

from __future__ import annotations

import abc
import contextlib
import os
import uuid
from typing import Any

from bridge.a2ui_inbox import InboxAuditRecord, Rfc6902PatchOp
from bridge.agent_gym.compiler import SkillCompilerMixin
from bridge.agent_gym.models import (
    DEFAULT_TRUST_POLICIES,
    REASONING_MODEL,
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
    _gcp_project_id,
    _infer_rule_category,
    _strip_legal_suffix,
    issuer_matches_pattern,
)
from bridge.agent_gym.registry_gateway import RegistryGatewayMixin
from bridge.agent_gym.rule_evaluator import RuleEvaluatorMixin as RuleEvaluatorMixin
from bridge.knowledge_catalog import CatalogRole
from bridge.telemetry import trace_step
from contract.extraction import SourceAnchor


class AgentGymEngine(
    SkillCompilerMixin, RegistryGatewayMixin, RuleEvaluatorMixin, abc.ABC
):
    """Base engine for Agent Gym distillation, stress-testing, and Programmable Trust.

    Subclasses implement the durable storage hooks (`_save_signal`, `_get_signal`,
    `_save_rule`, `_get_rule`, `_list_all_rules`, `_save_report`, `_save_trust_policy`,
    `_get_trust_policy`, `_list_all_trust_policies`, `_append_shadow_record`):

    - :class:`SqlAgentGymEngine`: AlloyDB / Cloud SQL Postgres / SQLite durable store
      shared across all Cloud Run replicas and Gemini platform Agent Engine (`reasoningEngines`)
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

    def _list_all_signals(self) -> list[ExpertFeedbackSignal]:
        """Return all persisted ExpertFeedbackSignal records (implemented by InMemory and Sql stores)."""
        return []

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

    @staticmethod
    def _compute_oversight_rsr(
        *,
        citation_verified: bool,
        review_dwell_ms: int,
        pre_committed: bool = False,
        historical_outcomes: tuple[bool, ...] = (),
    ) -> float:
        """Compute reviewer scrutiny score from citation, dwell time, and observed outcomes.

        Uses only the observed single-event behavioral signals (plus observed
        ``historical_outcomes`` when supplied) without fabricating synthetic prior trials.
        """
        dwell_ratio = min(1.0, max(0.0, float(review_dwell_ms) / 1500.0))
        citation_score = 1.0 if citation_verified else 0.0
        pre_commit_bonus = 0.02 if pre_committed else 0.0

        if citation_verified and review_dwell_ms >= 1500:
            behavioral_score = min(0.99, 0.96 + 0.02 * dwell_ratio + pre_commit_bonus)
        elif citation_verified or review_dwell_ms >= 1500:
            behavioral_score = min(
                0.78,
                0.52 + 0.10 * max(citation_score, dwell_ratio) + pre_commit_bonus,
            )
        else:
            behavioral_score = max(0.18, 0.30 + 0.12 * dwell_ratio + pre_commit_bonus)

        if historical_outcomes:
            observed_rate = sum(1 for ok in historical_outcomes if ok) / len(
                historical_outcomes
            )
            return round(0.5 * behavioral_score + 0.5 * observed_rate, 2)
        return round(behavioral_score, 2)

    def _invoke_reasoning_model_if_enabled(
        self,
        *,
        prompt: str,
    ) -> tuple[str | None, int | None, int | None]:
        """Invoke live `gemini-4-argon` when `BRIDGE_GYM_LIVE_LLM=true` or `_genai_client` is set.

        Records real `usage_metadata` token counts when live, or returns `(None, None, None)`
        when offline or when the live call fails (never fabricating token counts).
        """
        client = getattr(self, "_genai_client", None)
        live_enabled = os.environ.get("BRIDGE_GYM_LIVE_LLM", "").strip().lower() in {
            "1",
            "true",
        }
        if client is None and not live_enabled:
            return None, None, None

        with contextlib.suppress(Exception):
            if client is None:
                from google import genai

                vertex_location = os.environ.get("BRIDGE_VERTEX_LOCATION", "").strip()
                if not vertex_location:
                    vertex_location = (
                        "global"
                        if REASONING_MODEL.startswith(("gemini-3", "gemini-4"))
                        else os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
                    )
                client = genai.Client(
                    vertexai=True,
                    project=_gcp_project_id(),
                    location=vertex_location,
                )
            resp = client.models.generate_content(
                model=REASONING_MODEL,
                contents=prompt,
            )
            usage = getattr(resp, "usage_metadata", None)
            raw_in = getattr(usage, "prompt_token_count", None)
            raw_out = getattr(usage, "candidates_token_count", None)
            in_tok = int(raw_in) if raw_in is not None else None
            out_tok = int(raw_out) if raw_out is not None else None
            text = getattr(resp, "text", None)
            return (str(text).strip() if text else None), in_tok, out_tok
        return None, None, None

    def distill_from_quarantine_correction(  # noqa: PLR0913 - keyword-only feedback signal fields
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
        citation_verified: bool = False,
        review_dwell_ms: int = 0,
        pre_committed: bool = False,
    ) -> tuple[ExpertFeedbackSignal, TribalKnowledgeRule]:
        """Capture a Zone 2 Review Quarantine fix and distill a candidate Tribal Rule via gemini-4-argon."""
        oversight_rsr = self._compute_oversight_rsr(
            citation_verified=citation_verified,
            review_dwell_ms=review_dwell_ms,
            pre_committed=pre_committed,
        )
        llm_text, in_tok, out_tok = self._invoke_reasoning_model_if_enabled(
            prompt=(
                f"Distill TribalKnowledgeRule from quarantine correction: "
                f"issuer={issuer} doctype={doctype} field={field_path} "
                f"before={before_value} after={after_value} memo={expert_memo}"
            ),
        )
        span_attrs: dict[str, Any] = {
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
            "docubridge.oversight.rsr_score": oversight_rsr,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "agent_gym_teacher",
            "docubridge.finops.traffic_type": "agent_gym_eval",
        }
        if llm_text is not None:
            span_attrs["gen_ai.request.model"] = REASONING_MODEL
        if in_tok is not None:
            span_attrs["gen_ai.usage.input_tokens"] = in_tok
        if out_tok is not None:
            span_attrs["gen_ai.usage.output_tokens"] = out_tok
        with trace_step("docubridge.agent_gym.distill_signal", span_attrs) as span:
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
                pre_committed=pre_committed,
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
            span.set_attribute("docubridge.rule_status", rule.status.value)
            return signal, rule

    def distill_from_inbox_audit(  # noqa: PLR0913 - keyword-only inbox audit overrides
        self,
        audit_record: InboxAuditRecord,
        *,
        doc_id: str = "doc-inbox",
        doctype: str = "quote",
        issuer: str = "Carrier",
        field_path: str | None = None,
        category: TribalRuleCategory | None = None,
        distinct_entities: tuple[str, ...] = (),
        citation_verified: bool | None = None,
        review_dwell_ms: int | None = None,
        pre_committed: bool | None = None,
    ) -> tuple[ExpertFeedbackSignal, TribalKnowledgeRule]:
        """Distill a candidate Tribal Knowledge Rule from a single Zone 5 A2UI Inbox edit via gemini-4-argon."""
        resolved_citation = (
            audit_record.citation_opened
            if citation_verified is None
            else citation_verified
        )
        resolved_dwell = (
            audit_record.review_dwell_ms if review_dwell_ms is None else review_dwell_ms
        )
        resolved_pre_commit = (
            audit_record.pre_committed if pre_committed is None else pre_committed
        )
        oversight_rsr = self._compute_oversight_rsr(
            citation_verified=resolved_citation,
            review_dwell_ms=resolved_dwell,
            pre_committed=resolved_pre_commit,
        )
        llm_text, in_tok, out_tok = self._invoke_reasoning_model_if_enabled(
            prompt=(
                f"Distill TribalKnowledgeRule from A2UI inbox audit: "
                f"entity={audit_record.entity_id} issuer={issuer} doctype={doctype} "
                f"decision={audit_record.decision} notes={audit_record.notes}"
            ),
        )
        span_attrs: dict[str, Any] = {
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
            "docubridge.citation_verified": resolved_citation,
            "docubridge.review_dwell_ms": resolved_dwell,
            "docubridge.pre_committed": resolved_pre_commit,
            "docubridge.oversight_rsr_score": oversight_rsr,
            "docubridge.oversight.rsr_score": oversight_rsr,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "agent_gym_teacher",
            "docubridge.finops.traffic_type": "agent_gym_eval",
        }
        if llm_text is not None:
            span_attrs["gen_ai.request.model"] = REASONING_MODEL
        if in_tok is not None:
            span_attrs["gen_ai.usage.input_tokens"] = in_tok
        if out_tok is not None:
            span_attrs["gen_ai.usage.output_tokens"] = out_tok
        with trace_step("docubridge.agent_gym.distill_signal", span_attrs) as span:
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
                citation_verified=resolved_citation,
                review_dwell_ms=resolved_dwell,
                pre_committed=resolved_pre_commit,
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
            span.set_attribute("docubridge.rule_status", rule.status.value)
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
            before_value=signal.before_value,
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
                if signal.oversight_rsr_score < 0.80
                else TribalRuleStatus.CANDIDATE
            ),
        )
        self._save_rule(rule)
        return rule

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
                r for r in results if issuer_matches_pattern(issuer, r.issuer_pattern)
            ]
        if doctype is not None:
            results = [r for r in results if r.doctype in ("*", doctype)]
        return results

    def get_extraction_hints(
        self, *, issuer: str | None = None, doctype: str | None = None
    ) -> list[TribalKnowledgeRule]:
        """Return promoted Stage 1 extraction hints for `(issuer, doctype)`."""
        resolved_issuer = "" if issuer == "*" else issuer
        return self.list_rules(
            category=TribalRuleCategory.EXTRACTION_HINT,
            status=TribalRuleStatus.PROMOTED,
            issuer=resolved_issuer,
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
        self._registry_services: dict[str, dict[str, Any]] = {}
        self._registry_bindings: dict[str, dict[str, Any]] = {}
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
        self._registry_services.clear()
        self._registry_bindings.clear()

    def _save_signal(self, signal: ExpertFeedbackSignal) -> None:
        self._signals[signal.signal_id] = signal

    def _get_signal(self, signal_id: str) -> ExpertFeedbackSignal | None:
        return self._signals.get(signal_id)

    def _list_all_signals(self) -> list[ExpertFeedbackSignal]:
        return list(self._signals.values())

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

    def _save_registry_service(self, entry: dict[str, Any]) -> None:
        self._registry_services[str(entry["service_id"])] = dict(entry)

    def _list_registry_services(self) -> list[dict[str, Any]]:
        return list(self._registry_services.values())

    def _save_registry_binding(self, binding: dict[str, Any]) -> None:
        self._registry_bindings[str(binding["binding_id"])] = dict(binding)

    def _list_registry_bindings(self) -> list[dict[str, Any]]:
        return list(self._registry_bindings.values())


__all__ = [
    "AgentGymEngine",
    "InMemoryAgentGymEngine",
    "RuleEvaluatorMixin",
]
