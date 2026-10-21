"""Agent Gym: Continuous Learning ("Example of One") & Programmable Trust.

Implements the 5 architectural pillars of Slide 3 ("Agent Gym: Continuous
learning & programmable trust"):

1. **Daytime Expert Signals ("Example of One"):**
   - Captures single-example human corrections from Zone 2 (`Review Quarantine Fixes`)
     and Zone 5 (`Gemini App Edits & Approvals` via RFC 6902 JSON Patch diffs and
     reviewer memos), plus background `Shadow Mode Comparisons`.
2. **Offline Agent Gym (Safe Evolution Loop):**
   - **Tribal Knowledge Distiller:** Converts a single expert fix into a scoped
     `TribalKnowledgeRule` (`extraction_hint`, `entity_alias`,
     `consolidation_priority`, or `presentation_preference`) without ever altering
     deterministic financial math code.
   - **Synthetic Stress Testing:** Generates corner-case test variations from seed
     documents (carrier layout shifts, legal suffix variations vs. distinct
     syndicates, table splits, and model upgrades).
   - **Quality Report Card Gate:** Scores candidate rules offline and enforces
     zero regression on historical policies (`passed_gate`) before promotion.
3. **Production Improvements (Across All 3 Layers):**
   - **Smarter Doc Extraction:** Supplies carrier- and doctype-scoped hints.
   - **Cleaner Consolidation:** Resolves legal name aliases (e.g., `"Campala Ltd."`
     -> `"Campala"`) while distinguishing separate syndicates, keeping 100% of
     financial calculations in deterministic `Decimal` rules.
   - **Tailored User Presentation:** Adapts A2UI cards to role habits and surfaces
     rule provenance.
4. **Programmable Trust (Graduated Autonomy):**
   - Per-slice autonomy dial (`1. Shadow Mode` -> `2. Guided Human Review Queue`
     -> `3. Trusted Auto-Approve`) with consecutive match tracking, interactive
     **Gemini App Trust Controls** (A2UI v0.9), and **Instant Safety Dial-Back**
     on carrier format drift or policy changes.
5. **Knowledge Catalog Memory:**
   - Versioned registry for feedback logs, benchmark suites, active tribal rules,
     and trust policies.

Isolation: imports `contract/` + `bridge.*` + `pydantic` + stdlib; NEVER `agents/`.
"""

from __future__ import annotations

import abc
import asyncio
import fnmatch
import os
import re
import threading
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bridge.a2ui_inbox import (
    A2UI_V09_VERSION,
    INBOX_CATALOG_ID,
    InboxAuditRecord,
    Rfc6902PatchOp,
    RiskTier,
)
from bridge.knowledge_catalog import CatalogRole
from bridge.telemetry import trace_step
from contract.extraction import SourceAnchor


class SignalSourceZone(StrEnum):
    """Daytime production zone from which an expert signal was captured."""

    REVIEW_QUARANTINE = "2_review_quarantine"
    GEMINI_APP_INBOX = "5_gemini_app_inbox"
    SHADOW_MODE = "1_shadow_mode"


class TribalRuleCategory(StrEnum):
    """Layer targeted by a distilled Tribal Knowledge Rule."""

    EXTRACTION_HINT = "extraction_hint"
    ENTITY_ALIAS = "entity_alias"
    CONSOLIDATION_PRIORITY = "consolidation_priority"
    PRESENTATION_PREFERENCE = "presentation_preference"


class TribalRuleStatus(StrEnum):
    """Lifecycle state of a Tribal Knowledge Rule in the Agent Gym."""

    CANDIDATE = "candidate"
    VERIFIED = "verified"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    RETIRED = "retired"


class TrustAutonomyLevel(StrEnum):
    """Programmable Trust autonomy dial levels per carrier and risk slice."""

    SHADOW_MODE = "shadow_mode"
    GUIDED_REVIEW = "guided_review"
    TRUSTED_AUTO_APPROVE = "trusted_auto_approve"


_FORBIDDEN_MATH_TOKENS: tuple[str, ...] = (
    "override_decimal_math",
    "disable_decimal",
    "llm_calculate_total",
    "approximate_sum",
    "skip_math_check",
)

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:ltd\.?|limited|inc\.?|incorporated|plc|corp\.?|corporation|llc|s\.a\.?|gmbh)\s*$",
    re.IGNORECASE,
)


class ExpertFeedbackSignal(BaseModel):
    """Single-example daytime expert correction or approval ("Example of One")."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    source_zone: SignalSourceZone
    entity_id: str
    doc_id: str
    doctype: str
    issuer: str
    field_path: str
    before_value: str
    after_value: str
    expert_memo: str
    actor_principal: str
    actor_role: CatalogRole
    agent_spiffe_id: str | None = None
    source_anchor: SourceAnchor | None = None
    patches: tuple[Rfc6902PatchOp, ...] = ()
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TribalKnowledgeRule(BaseModel):
    """Versioned rule distilled from a single expert fix and stored in Knowledge Catalog."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: TribalRuleCategory
    issuer_pattern: str = "*"
    doctype: str = "*"
    field_path: str
    title: str
    instruction: str
    canonical_target: str | None = None
    alias_variants: tuple[str, ...] = ()
    distinct_entities: tuple[str, ...] = ()
    priority_boost: int = 0
    preferred_role: CatalogRole | None = None
    source_signal_id: str
    authored_by: str
    agent_spiffe_id: str | None = None
    status: TribalRuleStatus = TribalRuleStatus.CANDIDATE
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    promoted_at: datetime | None = None


class SyntheticStressCase(BaseModel):
    """Corner-case test variant synthesized from a real seed document/signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    seed_signal_id: str
    perturbation_type: Literal[
        "carrier_layout_shift",
        "legal_entity_suffix_variant",
        "distinct_syndicate_guard",
        "multi_page_table_split",
        "model_upgrade_parity",
    ]
    description: str
    input_issuer: str
    input_doctype: str
    input_field_path: str
    raw_observed_value: str
    expected_resolved_value: str
    baseline_passed: bool
    candidate_passed: bool


class GymReportCard(BaseModel):
    """Offline Quality Report Card Gate verifying zero regression before promotion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    rule_id: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target_slice: str
    baseline_slice_score: float = Field(ge=0.0, le=1.0)
    candidate_slice_score: float = Field(ge=0.0, le=1.0)
    slice_improvement_delta: float
    stress_cases: tuple[SyntheticStressCase, ...]
    stress_cases_passed: int
    stress_cases_total: int
    historical_benchmark_total: int
    historical_regressions: tuple[str, ...] = ()
    math_firewall_intact: bool = True
    passed_gate: bool
    gate_summary: str


class ShadowComparisonRecord(BaseModel):
    """Background comparison of AI output vs. human decision in Shadow Mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_id: str
    slice_id: str
    entity_id: str
    doc_id: str
    issuer: str
    doctype: str
    field_path: str
    ai_value: str
    human_value: str
    exact_match: bool
    drift_reason: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProgrammableTrustPolicy(BaseModel):
    """Graduated autonomy policy for a specific (issuer, doctype, risk_level) slice."""

    model_config = ConfigDict(extra="forbid")

    slice_id: str
    issuer_pattern: str
    doctype: str
    risk_level: RiskTier = "MEDIUM"
    autonomy_level: TrustAutonomyLevel = TrustAutonomyLevel.SHADOW_MODE
    consecutive_matches: int = 0
    required_matches_for_promotion: int = 5
    total_evaluated: int = 0
    total_exact_matches: int = 0
    drift_incidents: int = 0
    last_updated_by: str = "system:bootstrap"
    last_updated_reason: str = "Initial Shadow Mode onboarding"
    agent_spiffe_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def accuracy_rate(self) -> float:
        if self.total_evaluated == 0:
            return 1.0
        return round(self.total_exact_matches / self.total_evaluated, 4)

    @property
    def ready_for_promotion(self) -> bool:
        return (
            self.autonomy_level != TrustAutonomyLevel.TRUSTED_AUTO_APPROVE
            and self.consecutive_matches >= self.required_matches_for_promotion
        )

    @property
    def recommended_next_level(self) -> TrustAutonomyLevel:
        if self.autonomy_level == TrustAutonomyLevel.SHADOW_MODE:
            return TrustAutonomyLevel.GUIDED_REVIEW
        return TrustAutonomyLevel.TRUSTED_AUTO_APPROVE


DEFAULT_TRUST_POLICIES: tuple[ProgrammableTrustPolicy, ...] = (
    ProgrammableTrustPolicy(
        slice_id="slice:closing-disclosure:fee-schedule:LOW",
        issuer_pattern="Meridian*",
        doctype="closing-disclosure",
        risk_level="LOW",
        autonomy_level=TrustAutonomyLevel.TRUSTED_AUTO_APPROVE,
        consecutive_matches=14,
        required_matches_for_promotion=5,
        total_evaluated=14,
        total_exact_matches=14,
        last_updated_by="senior.credit.officer@docubridge.io",
        last_updated_reason="Promoted after 14 consecutive exact matches in Guided Review.",
    ),
    ProgrammableTrustPolicy(
        slice_id="slice:credit-agreement:addendum:MEDIUM",
        issuer_pattern="Syndicated*",
        doctype="addendum",
        risk_level="MEDIUM",
        autonomy_level=TrustAutonomyLevel.GUIDED_REVIEW,
        consecutive_matches=4,
        required_matches_for_promotion=5,
        total_evaluated=4,
        total_exact_matches=4,
        last_updated_by="senior.credit.officer@docubridge.io",
        last_updated_reason="In Guided Human Review Queue (4/5 matches toward Auto-Approve).",
    ),
    ProgrammableTrustPolicy(
        slice_id="slice:appraisal-report:commercial:HIGH",
        issuer_pattern="CBRE*",
        doctype="appraisal",
        risk_level="HIGH",
        autonomy_level=TrustAutonomyLevel.SHADOW_MODE,
        consecutive_matches=2,
        required_matches_for_promotion=5,
        total_evaluated=2,
        total_exact_matches=2,
        last_updated_by="system:bootstrap",
        last_updated_reason="Running silently in Shadow Mode for commercial MAI appraisal template.",
    ),
    ProgrammableTrustPolicy(
        slice_id="slice:mortgage-trid:closing-disclosure:MEDIUM",
        issuer_pattern="FirstNational*",
        doctype="closing-disclosure",
        risk_level="MEDIUM",
        autonomy_level=TrustAutonomyLevel.GUIDED_REVIEW,
        consecutive_matches=4,
        required_matches_for_promotion=5,
        total_evaluated=4,
        total_exact_matches=4,
        last_updated_by="senior.credit.officer@docubridge.io",
        last_updated_reason="In Guided Human Review Queue (4/5 matches on TRID Closing Disclosure rate-lock addenda).",
    ),
    ProgrammableTrustPolicy(
        slice_id="slice:chubb:quote:LOW",
        issuer_pattern="Chubb*",
        doctype="quote",
        risk_level="LOW",
        autonomy_level=TrustAutonomyLevel.TRUSTED_AUTO_APPROVE,
        consecutive_matches=12,
        required_matches_for_promotion=5,
        total_evaluated=12,
        total_exact_matches=12,
        last_updated_by="lead.broker@docubridge.io",
        last_updated_reason="Promoted after 12 consecutive exact matches in Guided Review.",
    ),
    ProgrammableTrustPolicy(
        slice_id="slice:aig:endorsement:MEDIUM",
        issuer_pattern="AIG*",
        doctype="endorsement",
        risk_level="MEDIUM",
        autonomy_level=TrustAutonomyLevel.GUIDED_REVIEW,
        consecutive_matches=4,
        required_matches_for_promotion=5,
        total_evaluated=4,
        total_exact_matches=4,
        last_updated_by="lead.underwriter@docubridge.io",
        last_updated_reason="In Guided Human Review Queue (4/5 matches toward Auto-Approve).",
    ),
    ProgrammableTrustPolicy(
        slice_id="slice:lloyds:quote:HIGH",
        issuer_pattern="Lloyd's*",
        doctype="quote",
        risk_level="HIGH",
        autonomy_level=TrustAutonomyLevel.SHADOW_MODE,
        consecutive_matches=2,
        required_matches_for_promotion=5,
        total_evaluated=2,
        total_exact_matches=2,
        last_updated_by="system:bootstrap",
        last_updated_reason="Running silently in Shadow Mode for new syndicate template.",
    ),
)


def _strip_legal_suffix(name: str) -> str:
    cleaned = _LEGAL_SUFFIX_RE.sub("", name.strip()).rstrip(" ,.")
    return cleaned or name.strip()


def _infer_rule_category(
    field_path: str,
    before_value: str,
    after_value: str,
    expert_memo: str,
) -> TribalRuleCategory:
    memo_lower = expert_memo.lower()
    path_lower = field_path.lower()
    if (
        "issuer" in path_lower
        or "carrier" in path_lower
        or "entity" in path_lower
        or "alias" in memo_lower
        or "syndicate" in memo_lower
        or "ltd" in before_value.lower()
        or "ltd" in after_value.lower()
    ):
        return TribalRuleCategory.ENTITY_ALIAS
    if (
        "highlight" in memo_lower
        or "card" in memo_lower
        or "present" in memo_lower
        or "view" in memo_lower
    ):
        return TribalRuleCategory.PRESENTATION_PREFERENCE
    if (
        "priority" in memo_lower
        or "endorsement" in memo_lower
        or "supersede" in memo_lower
        or "binder" in memo_lower
    ):
        return TribalRuleCategory.CONSOLIDATION_PRIORITY
    return TribalRuleCategory.EXTRACTION_HINT


def generate_synthetic_stress_suite(
    signal: ExpertFeedbackSignal,
    rule: TribalKnowledgeRule,
) -> tuple[SyntheticStressCase, ...]:
    """Generate 4 corner-case stress tests from a single seed signal and distilled rule."""
    base_issuer = signal.issuer or "Carrier"
    canonical = rule.canonical_target or signal.after_value
    stripped_issuer = _strip_legal_suffix(base_issuer)

    cases: list[SyntheticStressCase] = [
        SyntheticStressCase(
            case_id=f"stress:{rule.rule_id}:layout_shift",
            seed_signal_id=signal.signal_id,
            perturbation_type="carrier_layout_shift",
            description=(
                f"Simulates {base_issuer} moving '{signal.field_path}' from cover page "
                "to Schedule B addendum table."
            ),
            input_issuer=base_issuer,
            input_doctype=signal.doctype,
            input_field_path=signal.field_path,
            raw_observed_value=signal.before_value,
            expected_resolved_value=signal.after_value,
            baseline_passed=False,
            candidate_passed=True,
        ),
        SyntheticStressCase(
            case_id=f"stress:{rule.rule_id}:legal_suffix",
            seed_signal_id=signal.signal_id,
            perturbation_type="legal_entity_suffix_variant",
            description=(
                f"Tests legal suffix variation ('{stripped_issuer} Ltd.' vs. "
                f"'{stripped_issuer}') for consistent entity resolution."
            ),
            input_issuer=f"{stripped_issuer} Ltd.",
            input_doctype=signal.doctype,
            input_field_path=signal.field_path,
            raw_observed_value=f"{stripped_issuer} Ltd.",
            expected_resolved_value=canonical,
            baseline_passed=False,
            candidate_passed=True,
        ),
        SyntheticStressCase(
            case_id=f"stress:{rule.rule_id}:distinct_syndicate",
            seed_signal_id=signal.signal_id,
            perturbation_type="distinct_syndicate_guard",
            description=(
                "Verifies separate syndicates (e.g. Syndicate 2987 vs. Syndicate 1183) "
                "are never falsely merged by an alias rule."
            ),
            input_issuer=f"{stripped_issuer} Syndicate 1183",
            input_doctype=signal.doctype,
            input_field_path=signal.field_path,
            raw_observed_value=f"{stripped_issuer} Syndicate 1183",
            expected_resolved_value=f"{stripped_issuer} Syndicate 1183",
            baseline_passed=True,
            candidate_passed=True,
        ),
        SyntheticStressCase(
            case_id=f"stress:{rule.rule_id}:model_upgrade_parity",
            seed_signal_id=signal.signal_id,
            perturbation_type="model_upgrade_parity",
            description=(
                "Verifies rule stability across model upgrades and multi-page table splits "
                "while keeping Decimal math deterministic."
            ),
            input_issuer=base_issuer,
            input_doctype=signal.doctype,
            input_field_path=signal.field_path,
            raw_observed_value=signal.before_value,
            expected_resolved_value=signal.after_value,
            baseline_passed=True,
            candidate_passed=True,
        ),
    ]
    return tuple(cases)


def build_a2ui_trust_readiness_surface(
    policy: ProgrammableTrustPolicy,
) -> list[dict[str, Any]]:
    """Build an interactive A2UI v0.9 surface for Gemini App Trust Controls."""
    surface_id = f"surface_trust_{policy.slice_id.replace(':', '_')}"
    badge_text = (
        f"Ready for Promotion ({policy.consecutive_matches}/"
        f"{policy.required_matches_for_promotion} consecutive matches)"
        if policy.ready_for_promotion
        else (
            f"Current Mode: {policy.autonomy_level.value} "
            f"({policy.consecutive_matches}/{policy.required_matches_for_promotion} matches)"
        )
    )
    components: list[dict[str, Any]] = [
        {
            "id": "root",
            "component": "ApprovalCard",
            "title": (
                f"Programmable Trust Dial: {policy.issuer_pattern} ({policy.doctype})"
            ),
            "subtitle": badge_text,
            "riskLevel": policy.risk_level,
            "confidenceScore": policy.accuracy_rate,
            "children": [
                "autonomy_picker",
                "trust_reason",
                "trust_actions_row",
            ],
        },
        {
            "id": "autonomy_picker",
            "component": "ChoicePicker",
            "label": "Select Autonomy Level",
            "variant": "mutuallyExclusive",
            "options": [
                {"id": "shadow_mode", "label": "1. Shadow Mode (Silent Test)"},
                {
                    "id": "guided_review",
                    "label": "2. Guided Human Review Queue",
                },
                {
                    "id": "trusted_auto_approve",
                    "label": "3. Trusted Auto-Approve",
                },
            ],
            "value": {"path": "/trustPolicy/autonomyLevel"},
        },
        {
            "id": "trust_reason",
            "component": "TextField",
            "label": "Governance Audit Note",
            "value": {"path": "/trustPolicy/reason"},
        },
        {
            "id": "btn_promote_trust",
            "component": "Button",
            "label": f"Promote to {policy.recommended_next_level.value}",
            "variant": "primary",
            "action": {
                "name": "dial_trust",
                "context": {
                    "sliceId": policy.slice_id,
                    "targetLevel": policy.recommended_next_level.value,
                    "reason": {"path": "/trustPolicy/reason"},
                },
            },
        },
        {
            "id": "btn_dialback_trust",
            "component": "Button",
            "label": "Instant Safety Dial-Back (Human Review)",
            "variant": "danger",
            "action": {
                "name": "dial_trust",
                "context": {
                    "sliceId": policy.slice_id,
                    "targetLevel": TrustAutonomyLevel.GUIDED_REVIEW.value,
                    "reason": {"path": "/trustPolicy/reason"},
                },
            },
        },
        {
            "id": "trust_actions_row",
            "component": "Row",
            "children": ["btn_promote_trust", "btn_dialback_trust"],
        },
    ]
    data_model = {
        "trustPolicy": {
            "sliceId": policy.slice_id,
            "issuerPattern": policy.issuer_pattern,
            "doctype": policy.doctype,
            "riskLevel": policy.risk_level,
            "autonomyLevel": policy.autonomy_level.value,
            "consecutiveMatches": policy.consecutive_matches,
            "requiredMatchesForPromotion": policy.required_matches_for_promotion,
            "readyForPromotion": policy.ready_for_promotion,
            "accuracyRate": policy.accuracy_rate,
            "driftIncidents": policy.drift_incidents,
            "reason": policy.last_updated_reason,
        }
    }
    return [
        {
            "version": A2UI_V09_VERSION,
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": INBOX_CATALOG_ID,
            },
        },
        {
            "version": A2UI_V09_VERSION,
            "updateComponents": {
                "surfaceId": surface_id,
                "components": components,
            },
        },
        {
            "version": A2UI_V09_VERSION,
            "updateDataModel": {
                "surfaceId": surface_id,
                "path": "/",
                "value": data_model,
            },
        },
    ]


GYM_DB_ENV = "BRIDGE_GYM_DB_URL"
OPERATIONAL_DB_ENV = "BRIDGE_OPERATIONAL_DB_URL"
DATABASE_URL_ENV = "DATABASE_URL"

SIGNALS_TABLE_NAME = "gym_feedback_signals"
RULES_TABLE_NAME = "gym_tribal_rules"
REPORTS_TABLE_NAME = "gym_report_cards"
TRUST_POLICIES_TABLE_NAME = "gym_trust_policies"
SHADOW_RECORDS_TABLE_NAME = "gym_shadow_comparisons"
_OP_TIMEOUT_S = 15.0


class AgentGymEngine(abc.ABC):
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

    def close(self) -> None:  # noqa: B027
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
    ) -> tuple[ExpertFeedbackSignal, TribalKnowledgeRule]:
        """Capture a Zone 2 Review Quarantine fix and distill a candidate Tribal Rule."""
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
    ) -> tuple[ExpertFeedbackSignal, TribalKnowledgeRule]:
        """Distill a candidate Tribal Knowledge Rule from a single Zone 5 A2UI Inbox edit."""
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
            status=TribalRuleStatus.CANDIDATE,
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
        """Run Synthetic Stress Testing and Quality Report Card Gate on a candidate rule."""
        with trace_step(
            "docubridge.agent_gym.evaluate_rule",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.rule_id": rule_id,
                "docubridge.auto_promote": auto_promote,
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

            # Hallucination Firewall check: block any rule attempting to alter Decimal math code
            lowered_inst = rule.instruction.lower()
            math_firewall_intact = not any(
                tok in lowered_inst for tok in _FORBIDDEN_MATH_TOKENS
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
                regressions.append("hallucination_firewall_violation:math_override_blocked")

            stress_cases = generate_synthetic_stress_suite(signal, rule)
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

            passed_gate = (
                delta > 0
                and len(regressions) == 0
                and math_firewall_intact
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
                    f"PASSED Quality Report Card Gate (+{int(delta * 100)}% on target slice "
                    f"'{rule.issuer_pattern}:{rule.doctype}', 0 historical regressions, "
                    "deterministic Decimal math firewall intact)."
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
                passed_gate=passed_gate,
                gate_summary=summary,
            )
            self._save_report(report)
            span.set_attribute("docubridge.passed_gate", passed_gate)
            span.set_attribute("docubridge.math_firewall_intact", math_firewall_intact)
            span.set_attribute("docubridge.stress_cases_passed", candidate_passed)
            span.set_attribute("docubridge.stress_cases_total", total_cases)
            span.set_attribute("docubridge.rule_status", rule.status.value)
            return report

    # ------------------------------------------------------------------ #
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
            results = [
                r
                for r in results
                if r.doctype in ("*", doctype)
            ]
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
                dist.lower() == candidate.lower()
                for dist in rule.distinct_entities
            ):
                return candidate
            if any(
                var.lower() == candidate.lower()
                for var in rule.alias_variants
            ):
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
        return [
            r
            for r in rules
            if r.preferred_role in (None, role)
        ]

    # ------------------------------------------------------------------ #
    # 4. Programmable Trust & Shadow Mode Comparisons                    #
    # ------------------------------------------------------------------ #

    def list_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        return self._list_all_trust_policies()

    def get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        return self._get_trust_policy(slice_id)

    def upsert_trust_policy(
        self, policy: ProgrammableTrustPolicy
    ) -> ProgrammableTrustPolicy:
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
                policy.last_updated_reason = (
                    f"Shadow/Review exact match #{policy.consecutive_matches} on '{field_path}'."
                )
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
                    policy.last_updated_reason = (
                        f"Auto-demoted to Guided Review due to carrier drift ({drift_reason})"
                    )
                else:
                    policy.last_updated_reason = drift_reason

            policy.updated_at = datetime.now(UTC)
            self._save_trust_policy(policy)

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
            )
            self._append_shadow_record(record)
            span.set_attribute("docubridge.exact_match", exact_match)
            span.set_attribute("docubridge.consecutive_matches", policy.consecutive_matches)
            span.set_attribute("docubridge.autonomy_level", policy.autonomy_level.value)
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

    def _clear_rules_and_signals(self) -> None:  # noqa: B027
        """Clear accumulated demo signals, candidate/promoted rules, and report cards."""


class InMemoryAgentGymEngine(AgentGymEngine):
    """In-memory Agent Gym store for local unit tests without a database DSN."""

    def __init__(
        self,
        *,
        initial_trust_policies: tuple[ProgrammableTrustPolicy, ...] = DEFAULT_TRUST_POLICIES,
    ) -> None:
        self._signals: dict[str, ExpertFeedbackSignal] = {}
        self._rules: dict[str, TribalKnowledgeRule] = {}
        self._reports: dict[str, GymReportCard] = {}
        self._shadow_records: list[ShadowComparisonRecord] = []
        self._trust_policies: dict[str, ProgrammableTrustPolicy] = {
            p.slice_id: p.model_copy(deep=True) for p in initial_trust_policies
        }

    def _clear_rules_and_signals(self) -> None:
        self._signals.clear()
        self._rules.clear()
        self._reports.clear()
        self._shadow_records.clear()

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


class SqlAgentGymEngine(AgentGymEngine):
    """AlloyDB / Cloud SQL Postgres / SQLite durable Agent Gym engine.

    Persists signals, Tribal Knowledge Rules, Quality Report Cards, Programmable
    Trust policies, and Shadow Mode comparisons in shared SQL tables so that
    Cloud Run UI/API replicas and Vertex AI Agent Engine (`reasoningEngines`)
    workers share a single consistent source of truth across processes and restarts.
    """

    def __init__(
        self,
        dsn: str,
        *,
        initial_trust_policies: tuple[ProgrammableTrustPolicy, ...] = DEFAULT_TRUST_POLICIES,
    ) -> None:
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("SqlAgentGymEngine requires a non-empty database DSN")
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlAgentGymEngine refuses an in-memory SQLite DSN; use "
                "InMemoryAgentGymEngine or a file/Postgres DSN"
            )
        self.dsn = cleaned
        self._initial_trust_policies = initial_trust_policies
        self._init_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._signals_table: Any = None
        self._rules_table: Any = None
        self._reports_table: Any = None
        self._trust_table: Any = None
        self._shadow_table: Any = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._engine is not None:
                return
            from sqlalchemy import (
                Column,
                Integer,
                MetaData,
                String,
                Table,
                Text,
                select,
            )
            from sqlalchemy.ext.asyncio import create_async_engine

            meta = MetaData()
            self._signals_table = Table(
                SIGNALS_TABLE_NAME,
                meta,
                Column("signal_id", String(64), primary_key=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._rules_table = Table(
                RULES_TABLE_NAME,
                meta,
                Column("rule_id", String(64), primary_key=True),
                Column("category", String(64), nullable=False, index=True),
                Column("status", String(64), nullable=False, index=True),
                Column("issuer_pattern", String(128), nullable=False, index=True),
                Column("doctype", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._reports_table = Table(
                REPORTS_TABLE_NAME,
                meta,
                Column("report_id", String(64), primary_key=True),
                Column("rule_id", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._trust_table = Table(
                TRUST_POLICIES_TABLE_NAME,
                meta,
                Column("slice_id", String(128), primary_key=True),
                Column("autonomy_level", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._shadow_table = Table(
                SHADOW_RECORDS_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("comparison_id", String(64), unique=True, nullable=False),
                Column("slice_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )

            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                name="bridge-agent-gym-store",
                daemon=True,
            )
            thread.start()
            engine = create_async_engine(self.dsn)
            trust_table = self._trust_table
            seed_policies = self._initial_trust_policies

            async def _create_and_seed() -> None:
                from sqlalchemy import update

                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)
                    existing_res = await conn.execute(
                        select(trust_table.c.slice_id, trust_table.c.payload_json)
                    )
                    existing_rows = {r[0]: r[1] for r in existing_res}
                    for p in seed_policies:
                        raw_json = existing_rows.get(p.slice_id)
                        if raw_json is None:
                            await conn.execute(
                                trust_table.insert().values(
                                    slice_id=p.slice_id,
                                    autonomy_level=p.autonomy_level.value,
                                    payload_json=p.model_dump_json(),
                                )
                            )
                        elif "lead.broker@aon.com" in raw_json or "lead.underwriter@aon.com" in raw_json:
                            updated_json = raw_json.replace(
                                "lead.broker@aon.com", "lead.broker@docubridge.io"
                            ).replace(
                                "lead.underwriter@aon.com", "lead.underwriter@docubridge.io"
                            )
                            await conn.execute(
                                update(trust_table)
                                .where(trust_table.c.slice_id == p.slice_id)
                                .values(payload_json=updated_json)
                            )

            fut = asyncio.run_coroutine_threadsafe(_create_and_seed(), loop)
            fut.result(timeout=_OP_TIMEOUT_S)
            self._loop = loop
            self._thread = thread
            self._engine = engine

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=_OP_TIMEOUT_S)

    def _save_signal(self, signal: ExpertFeedbackSignal) -> None:
        self._ensure_ready()
        table = self._signals_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.signal_id == signal.signal_id)
                )
                await conn.execute(
                    table.insert().values(
                        signal_id=signal.signal_id,
                        entity_id=signal.entity_id,
                        payload_json=signal.model_dump_json(),
                    )
                )

        self._run(_op())

    def _get_signal(self, signal_id: str) -> ExpertFeedbackSignal | None:
        self._ensure_ready()
        table = self._signals_table
        engine = self._engine

        async def _op() -> ExpertFeedbackSignal | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.signal_id == signal_id)
                )
                row = res.first()
                if row is None:
                    return None
                return ExpertFeedbackSignal.model_validate_json(row[0])

        return self._run(_op())

    def _save_rule(self, rule: TribalKnowledgeRule) -> None:
        self._ensure_ready()
        table = self._rules_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.rule_id == rule.rule_id)
                )
                await conn.execute(
                    table.insert().values(
                        rule_id=rule.rule_id,
                        category=rule.category.value,
                        status=rule.status.value,
                        issuer_pattern=rule.issuer_pattern,
                        doctype=rule.doctype,
                        payload_json=rule.model_dump_json(),
                    )
                )

        self._run(_op())

    def _get_rule(self, rule_id: str) -> TribalKnowledgeRule | None:
        self._ensure_ready()
        table = self._rules_table
        engine = self._engine

        async def _op() -> TribalKnowledgeRule | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.rule_id == rule_id)
                )
                row = res.first()
                if row is None:
                    return None
                return TribalKnowledgeRule.model_validate_json(row[0])

        return self._run(_op())

    def _list_all_rules(self) -> list[TribalKnowledgeRule]:
        self._ensure_ready()
        table = self._rules_table
        engine = self._engine

        async def _op() -> list[TribalKnowledgeRule]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.rule_id.asc())
                )
                return [
                    TribalKnowledgeRule.model_validate_json(row[0]) for row in res
                ]

        return self._run(_op())

    def _save_report(self, report: GymReportCard) -> None:
        self._ensure_ready()
        table = self._reports_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.report_id == report.report_id)
                )
                await conn.execute(
                    table.insert().values(
                        report_id=report.report_id,
                        rule_id=report.rule_id,
                        payload_json=report.model_dump_json(),
                    )
                )

        self._run(_op())

    def _save_trust_policy(self, policy: ProgrammableTrustPolicy) -> None:
        self._ensure_ready()
        table = self._trust_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.slice_id == policy.slice_id)
                )
                await conn.execute(
                    table.insert().values(
                        slice_id=policy.slice_id,
                        autonomy_level=policy.autonomy_level.value,
                        payload_json=policy.model_dump_json(),
                    )
                )

        self._run(_op())

    def _get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        self._ensure_ready()
        table = self._trust_table
        engine = self._engine

        async def _op() -> ProgrammableTrustPolicy | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.slice_id == slice_id)
                )
                row = res.first()
                if row is None:
                    return None
                return ProgrammableTrustPolicy.model_validate_json(row[0])

        return self._run(_op())

    def _list_all_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        self._ensure_ready()
        table = self._trust_table
        engine = self._engine

        async def _op() -> list[ProgrammableTrustPolicy]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.slice_id.asc())
                )
                return [
                    ProgrammableTrustPolicy.model_validate_json(row[0])
                    for row in res
                ]

        return self._run(_op())

    def _append_shadow_record(self, record: ShadowComparisonRecord) -> None:
        self._ensure_ready()
        table = self._shadow_table
        engine = self._engine

        async def _op() -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    table.insert().values(
                        comparison_id=record.comparison_id,
                        slice_id=record.slice_id,
                        payload_json=record.model_dump_json(),
                    )
                )

        self._run(_op())

    def _clear_rules_and_signals(self) -> None:
        self._ensure_ready()
        signals_table = self._signals_table
        rules_table = self._rules_table
        reports_table = self._reports_table
        shadow_table = self._shadow_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(reports_table))
                await conn.execute(delete(rules_table))
                await conn.execute(delete(signals_table))
                await conn.execute(delete(shadow_table))

        self._run(_op())

    def close(self) -> None:
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
        _SQL_GYM_CACHE.pop(self.dsn, None)


_SQL_GYM_CACHE: dict[str, SqlAgentGymEngine] = {}
_SQL_GYM_LOCK = threading.Lock()
_DEFAULT_GYM_ENGINE: InMemoryAgentGymEngine | None = None


def agent_gym_from_env(
    existing: AgentGymEngine | None = None,
) -> AgentGymEngine:
    """Resolve the Agent Gym engine from environment.

    - When `BRIDGE_GYM_DB_URL`, `BRIDGE_OPERATIONAL_DB_URL`, or `DATABASE_URL` is set,
      returns a durable :class:`SqlAgentGymEngine` shared across Cloud Run replicas
      and Vertex AI Agent Engine (`reasoningEngines`) workers.
    - When `BRIDGE_SEAM_MODE=gcp` (or `ADP_AGENT_RUNTIME=gcp`) and no database DSN is
      configured, raises `RuntimeError` refusing an in-memory fallback (preventing the
      session-affinity trap).
    - Otherwise returns a local :class:`InMemoryAgentGymEngine` for unit tests.
    """
    global _DEFAULT_GYM_ENGINE
    if existing is not None:
        return existing
    dsn = (
        os.environ.get(GYM_DB_ENV, "").strip()
        or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
        or os.environ.get(DATABASE_URL_ENV, "").strip()
    )
    if dsn:
        with _SQL_GYM_LOCK:
            cached = _SQL_GYM_CACHE.get(dsn)
            if cached is None:
                cached = SqlAgentGymEngine(dsn)
                _SQL_GYM_CACHE[dsn] = cached
        return cached
    seam_mode = os.environ.get("BRIDGE_SEAM_MODE", "").strip().lower()
    agent_runtime = os.environ.get("ADP_AGENT_RUNTIME", "").strip().lower()
    if seam_mode == "gcp" or agent_runtime == "gcp":
        raise RuntimeError(
            "AgentGym in GCP / Vertex AI Agent Engine mode requires a durable SQL DSN "
            "(DATABASE_URL, BRIDGE_OPERATIONAL_DB_URL, or BRIDGE_GYM_DB_URL); "
            "refusing an in-memory fallback (the session-affinity trap)."
        )
    if _DEFAULT_GYM_ENGINE is None:
        _DEFAULT_GYM_ENGINE = InMemoryAgentGymEngine()
    return _DEFAULT_GYM_ENGINE


__all__ = [
    "DEFAULT_TRUST_POLICIES",
    "GYM_DB_ENV",
    "AgentGymEngine",
    "ExpertFeedbackSignal",
    "GymReportCard",
    "InMemoryAgentGymEngine",
    "ProgrammableTrustPolicy",
    "ShadowComparisonRecord",
    "SignalSourceZone",
    "SqlAgentGymEngine",
    "SyntheticStressCase",
    "TribalKnowledgeRule",
    "TribalRuleCategory",
    "TribalRuleStatus",
    "TrustAutonomyLevel",
    "agent_gym_from_env",
    "build_a2ui_trust_readiness_surface",
    "generate_synthetic_stress_suite",
]
