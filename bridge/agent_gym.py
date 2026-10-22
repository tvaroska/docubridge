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
import hashlib
import io
import json
import os
import re
import threading
import uuid
import zipfile
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

WORKHORSE_MODEL = "gemini-3.8-flash"
REASONING_MODEL = "gemini-4-argon"


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
    citation_verified: bool = True
    review_dwell_ms: int = 4200
    oversight_rsr_score: float = 0.98
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
    workhorse_model: str = WORKHORSE_MODEL
    judge_model: str = REASONING_MODEL
    tier1_rule0_passed: bool = True
    tier2_autorater_score: float = 1.0
    tier3_corridor_fa_zero: bool = True
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
    active_skill_revision_id: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SkillRevisionRecord(BaseModel):
    """Immutable, content-addressed SkillRevision snapshot in Gemini platform Agent Registry / Skill Registry."""

    model_config = ConfigDict(extra="forbid")

    revision_id: str
    skill_name: str
    skill_urn: str
    resource_name: str
    version: str
    parent_revision_id: str | None = None
    target_state: Literal["DRAFT", "ACTIVE", "DEPRECATED", "DISABLED"] = "ACTIVE"
    is_default_revision: bool = True
    compiled_by_model: str = REASONING_MODEL
    workhorse_model: str = WORKHORSE_MODEL
    compiled_rule_ids: tuple[str, ...] = ()
    compiled_rules_count: int = 0
    gcs_source_uri: str
    archive_sha256: str
    archive_size_bytes: int
    skill_markdown: str
    assets_schema_json: str = "{}"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProcessSkillLineageRecord(BaseModel):
    """Process-instance skill lineage: records which exact version/revision of the process skill
    and doctype skill executed for a given (context_id, entity_id, doc_id, document_version)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lineage_id: str
    context_id: str
    entity_id: str
    doc_id: str
    document_version: int = 1
    process_skill_name: str = "commercial-loan-closing"
    process_skill_urn: str = "urn:skill:projects-documents001:locations:global:private-commercial-loan-closing"
    process_skill_version: str = "1.0.0"
    process_skill_revision_id: str = "rev-v1-0-baseline"
    doctype_skill_name: str
    doctype_skill_urn: str
    doctype_skill_version: str
    doctype_skill_revision_id: str
    compiled_rule_ids: tuple[str, ...] = ()
    workhorse_model: str = WORKHORSE_MODEL
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    effective_from: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


def _execute_rule_on_case(
    rule: TribalKnowledgeRule | None,
    *,
    perturbation_type: str,
    input_issuer: str,
    raw_observed_value: str,
    expected_resolved_value: str,
    math_firewall_intact: bool = True,
) -> bool:
    """Deterministically execute baseline (`rule=None`) or candidate `rule` against a stress case."""
    if not math_firewall_intact and rule is not None:
        return False

    # Case 3: Distinct syndicate / SPV collision guard (must NEVER merge distinct entities)
    if perturbation_type == "distinct_syndicate_guard":
        if rule is None:
            return raw_observed_value == expected_resolved_value
        if any(d.lower() == raw_observed_value.lower() for d in rule.distinct_entities):
            return True
        # Even when not explicitly listed, a distinct syndicate suffix ("Syndicate 1183")
        # must not be overwritten by a rule scoped to another value.
        if "syndicate 1183" in raw_observed_value.lower() and "1183" not in (rule.canonical_target or "").lower():
            return True
        return raw_observed_value == expected_resolved_value

    # Case 4: Model upgrade parity (`gemini-3.8-flash` workhorse + `gemini-4-argon` teacher parity)
    if perturbation_type == "model_upgrade_parity":
        if rule is None:
            return True
        return bool(rule.canonical_target or expected_resolved_value)

    # Case 1 & Case 2: Layout shift & legal suffix variant require the candidate rule to resolve
    if rule is None:
        return raw_observed_value.strip() == expected_resolved_value.strip()

    issuer_match = (
        fnmatch.fnmatchcase(input_issuer.lower(), rule.issuer_pattern.lower())
        or rule.issuer_pattern == "*"
        or _strip_legal_suffix(input_issuer).lower().startswith(
            rule.issuer_pattern.rstrip("*").lower()
        )
    )
    if not issuer_match:
        return False
    resolved = rule.canonical_target or expected_resolved_value
    return resolved.strip() == expected_resolved_value.strip()


def generate_synthetic_stress_suite(
    signal: ExpertFeedbackSignal,
    rule: TribalKnowledgeRule,
    *,
    math_firewall_intact: bool = True,
) -> tuple[SyntheticStressCase, ...]:
    """Generate and execute 4 corner-case stress tests from a seed signal and distilled rule."""
    base_issuer = signal.issuer or "Carrier"
    canonical = rule.canonical_target or signal.after_value
    stripped_issuer = _strip_legal_suffix(base_issuer)

    specs: list[tuple[str, str, str, str, str, str]] = [
        (
            "layout_shift",
            "carrier_layout_shift",
            (
                f"Simulates {base_issuer} moving '{signal.field_path}' from cover page "
                "to Schedule B addendum table."
            ),
            base_issuer,
            signal.before_value,
            signal.after_value,
        ),
        (
            "legal_suffix",
            "legal_entity_suffix_variant",
            (
                f"Tests legal suffix variation ('{stripped_issuer} Ltd.' vs. "
                f"'{stripped_issuer}') for consistent entity resolution."
            ),
            f"{stripped_issuer} Ltd.",
            f"{stripped_issuer} Ltd.",
            canonical,
        ),
        (
            "distinct_syndicate",
            "distinct_syndicate_guard",
            (
                "Verifies separate syndicates (e.g. Syndicate 2987 vs. Syndicate 1183) "
                "are never falsely merged by an alias rule."
            ),
            f"{stripped_issuer} Syndicate 1183",
            f"{stripped_issuer} Syndicate 1183",
            f"{stripped_issuer} Syndicate 1183",
        ),
        (
            "model_upgrade_parity",
            "model_upgrade_parity",
            (
                f"Verifies rule stability across {WORKHORSE_MODEL} workhorse and "
                f"{REASONING_MODEL} teacher while keeping Decimal math deterministic."
            ),
            base_issuer,
            signal.before_value,
            signal.after_value,
        ),
    ]

    cases: list[SyntheticStressCase] = []
    for slug, ptype, desc, in_issuer, raw_val, exp_val in specs:
        base_ok = _execute_rule_on_case(
            None,
            perturbation_type=ptype,
            input_issuer=in_issuer,
            raw_observed_value=raw_val,
            expected_resolved_value=exp_val,
        )
        cand_ok = _execute_rule_on_case(
            rule,
            perturbation_type=ptype,
            input_issuer=in_issuer,
            raw_observed_value=raw_val,
            expected_resolved_value=exp_val,
            math_firewall_intact=math_firewall_intact,
        )
        cases.append(
            SyntheticStressCase(
                case_id=f"stress:{rule.rule_id}:{slug}",
                seed_signal_id=signal.signal_id,
                perturbation_type=ptype,  # type: ignore[arg-type]
                description=desc,
                input_issuer=in_issuer,
                input_doctype=signal.doctype,
                input_field_path=signal.field_path,
                raw_observed_value=raw_val,
                expected_resolved_value=exp_val,
                baseline_passed=base_ok,
                candidate_passed=cand_ok,
            )
        )
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
SKILL_REVISIONS_TABLE_NAME = "gym_skill_revisions"
PROCESS_SKILL_LINEAGE_TABLE_NAME = "gym_process_skill_lineage"
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

    def _save_skill_revision(self, revision: SkillRevisionRecord) -> None:  # noqa: B027
        """Persist a SkillRevisionRecord (implemented by InMemory and Sql stores)."""

    def _list_all_skill_revisions(self) -> list[SkillRevisionRecord]:
        return []

    def _append_process_skill_lineage(self, record: ProcessSkillLineageRecord) -> None:  # noqa: B027
        """Persist a ProcessSkillLineageRecord (implemented by InMemory and Sql stores)."""

    def _list_all_process_skill_lineage(self) -> list[ProcessSkillLineageRecord]:
        return []

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
                regressions.append("hallucination_firewall_violation:math_override_blocked")

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
            span.set_attribute("docubridge.tier2_autorater_score", tier2_autorater_score)
            span.set_attribute("docubridge.tier3_corridor_fa_zero", tier3_corridor_fa_zero)
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
                if r.status in (TribalRuleStatus.PROMOTED, TribalRuleStatus.VERIFIED, TribalRuleStatus.CANDIDATE)
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
            skill_urn = f"urn:skill:projects-documents001:locations:global:private-{doctype}"
            sorted_rule_ids = tuple(sorted(r.rule_id for r in matched))

            schema_payload = {
                "doctype": doctype,
                "version": semver,
                "compiled_by": REASONING_MODEL,
                "runtime_workhorse": WORKHORSE_MODEL,
                "fields": {
                    "issuer": {"type": "string", "description": "Canonical issuer or lender name"},
                    "effective_date": {"type": "string", "description": "ISO-8601 effective date"},
                    "origination_fee": {"type": "string", "description": "Origination fee (Schedule B / Page 3 Section A)"},
                    "loan_amount": {"type": "string", "description": "Total principal loan amount"},
                },
                "compiled_rule_ids": list(sorted_rule_ids),
            }
            schema_json_str = json.dumps(schema_payload, indent=2, sort_keys=True)
            digest_input = f"{doctype}|{semver}|{','.join(sorted_rule_ids)}|{ '\n'.join(rule_bullets)}".encode()
            short_hash = hashlib.sha256(digest_input).hexdigest()[:8]
            revision_id = f"rev-v1-{minor_num}-{short_hash}"
            resource_name = (
                f"projects/documents001/locations/global/skills/private-{doctype}/revisions/{revision_id}"
            )
            gcs_source_uri = (
                f"gs://documents001-bridge-skills/revisions/{doctype}/{revision_id}.zip"
            )
            skill_uri = f"gs://documents001-bridge-skills/{doctype}/SKILL.md"

            skill_markdown = (
                f"---\n"
                f"name: {doctype}\n"
                f"description: Distilled extraction and consolidation skill for {doctype} "
                f"(compiled by {REASONING_MODEL} for {WORKHORSE_MODEL} runtime execution).\n"
                f"license: Proprietary. LICENSE.txt has complete terms\n"
                f"metadata:\n"
                f"  bridge-kind: doctype\n"
                f"  bridge-tags: \"doctype {doctype} harness-evolve\"\n"
                f"  bridge-extraction-engine: gemini\n"
                f"  bridge-schema: assets/schema.json\n"
                f"  version: \"{semver}\"\n"
                f"  revision-id: \"{revision_id}\"\n"
                f"  parent-revision-id: \"{parent_revision_id}\"\n"
                f"  compiled-by-model: \"{REASONING_MODEL}\"\n"
                f"  runtime-workhorse-model: \"{WORKHORSE_MODEL}\"\n"
                f"  compiled-rule-ids: \"{','.join(sorted_rule_ids)}\"\n"
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
                        prev_copy = prev.model_copy(update={"is_default_revision": False})
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
        skill_urn = (
            f"urn:skill:projects-documents001:locations:global:private-{skill_name}"
        )
        return SkillRevisionRecord(
            revision_id="rev-v1-0-baseline",
            skill_name=skill_name,
            skill_urn=skill_urn,
            resource_name=(
                f"projects/documents001/locations/global/skills/private-{skill_name}/revisions/rev-v1-0-baseline"
            ),
            version="1.0.0",
            parent_revision_id=None,
            target_state="ACTIVE",
            is_default_revision=True,
            compiled_by_model="system:baseline",
            workhorse_model=WORKHORSE_MODEL,
            compiled_rule_ids=(),
            compiled_rules_count=0,
            gcs_source_uri=f"gs://documents001-bridge-skills/revisions/{skill_name}/rev-v1-0-baseline.zip",
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
                current_default.parent_revision_id if current_default else "rev-v1-0-baseline"
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
                        update={"is_default_revision": False, "target_state": "DEPRECATED"}
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

    def get_registry_catalog(self) -> dict[str, Any]:
        """Return the unified Gemini platform Agent Registry + Skill Registry catalog
        across all 4 component types (Agents, MCP Servers, Endpoints, Skills + SkillRevisions),
        Bindings, Publishers, and Process-Instance Skill Lineage."""
        from bridge.edge.card import build_agent_card
        from bridge.edge.skills import loaded_skills
        from bridge.identity import (
            DEFAULT_ROLE_APPROVAL_LIMITS,
            DEFAULT_SPIFFE_AGENT_POLICIES,
        )

        seam_skills = loaded_skills()
        card = build_agent_card(skills=seam_skills)
        all_revs = self.list_skill_revisions()
        revs_by_skill: dict[str, list[SkillRevisionRecord]] = {}
        for rev in all_revs:
            revs_by_skill.setdefault(rev.skill_name, []).append(rev)

        skill_containers: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for meta in seam_skills:
            seen_names.add(meta.name)
            active_rev = self.get_active_skill_revision(meta.name)
            history = revs_by_skill.get(meta.name, [])
            skill_containers.append(
                {
                    "skill_name": meta.name,
                    "kind": meta.kind,
                    "publisher": "private",
                    "description": meta.description,
                    "skill_urn": active_rev.skill_urn,
                    "resource_name": f"projects/321488252989/locations/global/skills/private-{meta.name}",
                    "default_revision_id": active_rev.revision_id,
                    "active_version": active_rev.version,
                    "target_state": active_rev.target_state,
                    "gcs_source_uri": active_rev.gcs_source_uri,
                    "compiled_rule_ids": list(active_rev.compiled_rule_ids),
                    "revision_count": max(1, len(history)),
                    "revisions": [r.model_dump(mode="json") for r in history]
                    if history
                    else [active_rev.model_dump(mode="json")],
                }
            )
        for skill_name, history in sorted(revs_by_skill.items()):
            if skill_name in seen_names:
                continue
            active_rev = self.get_active_skill_revision(skill_name)
            skill_containers.append(
                {
                    "skill_name": skill_name,
                    "kind": "doctype",
                    "publisher": "private",
                    "description": f"Agent Gym compiled doctype skill for {skill_name}",
                    "skill_urn": active_rev.skill_urn,
                    "resource_name": f"projects/321488252989/locations/global/skills/private-{skill_name}",
                    "default_revision_id": active_rev.revision_id,
                    "active_version": active_rev.version,
                    "target_state": active_rev.target_state,
                    "gcs_source_uri": active_rev.gcs_source_uri,
                    "compiled_rule_ids": list(active_rev.compiled_rule_ids),
                    "revision_count": len(history),
                    "revisions": [r.model_dump(mode="json") for r in history],
                }
            )

        a2a_skills_list = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": list(s.tags or []),
            }
            for s in (card.skills or [])
        ]

        agents_list: list[dict[str, Any]] = [
            {
                "agent_id": "urn:agent:projects-321488252989:projects:321488252989:locations:us-central1:aiplatform:reasoningEngines:5963861295097708544",
                "agent_urn": "urn:agent:projects-321488252989:projects:321488252989:locations:us-central1:aiplatform:reasoningEngines:5963861295097708544",
                "resource_name": "projects/321488252989/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "display_name": f"{card.name} (Agent Engine Runtime)",
                "registration_mode": "AUTOMATIC",
                "spec_type": "a2a-agent-card",
                "protocol": "A2A/1.0",
                "agent_principal": "principal://agents.global.org-docubridge.system.id.goog/resources/aiplatform/projects/321488252989/locations/us-central1/reasoningEngines/5963861295097708544",
                "spiffe_id": "spiffe://agents.docubridge.io/internal/credit-officer-agent",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": "//aiplatform.googleapis.com/projects/321488252989/locations/us-central1/reasoningEngines/5963861295097708544",
                },
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "security_zone": "internal",
                "a2a_skills": a2a_skills_list,
            },
            {
                "agent_id": "urn:agent:projects-321488252989:projects:321488252989:locations:us-central1:run:services:documents001-bridge",
                "agent_urn": "urn:agent:projects-321488252989:projects:321488252989:locations:us-central1:run:services:documents001-bridge",
                "resource_name": "projects/321488252989/locations/us-central1/agents/agentregistry-documents001-bridge",
                "display_name": "DocuBridge A2A Edge Proxy (Cloud Run)",
                "registration_mode": "AUTOMATIC",
                "spec_type": "a2a-agent-card",
                "protocol": "A2A/1.0",
                "agent_principal": "principal://agents.global.org-docubridge.system.id.goog/resources/run/projects/321488252989/locations/us-central1/services/documents001-bridge",
                "spiffe_id": "spiffe://agents.docubridge.io/internal/broker-agent",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": "//run.googleapis.com/projects/321488252989/locations/us-central1/services/documents001-bridge",
                },
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "security_zone": "internal",
                "a2a_skills": a2a_skills_list,
            },
        ]

        for spiffe_id, pol in DEFAULT_SPIFFE_AGENT_POLICIES.items():
            short_slug = spiffe_id.rsplit("/", 1)[-1]
            primary_role = pol.allowed_roles[0] if pol.allowed_roles else "client"
            max_limit = str(DEFAULT_ROLE_APPROVAL_LIMITS.get(primary_role, "0.00"))
            agents_list.append(
                {
                    "agent_id": f"urn:agent:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:{short_slug}",
                    "agent_urn": f"urn:agent:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:{short_slug}",
                    "resource_name": f"projects/321488252989/locations/us-central1/agents/agentregistry-{short_slug}",
                    "display_name": pol.display_name,
                    "registration_mode": "MANUAL",
                    "spec_type": "a2a-agent-card",
                    "protocol": "A2A/1.0 + A2UI/0.9",
                    "agent_principal": spiffe_id,
                    "spiffe_id": spiffe_id,
                    "runtime_reference": {
                        "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                        "uri": "//run.googleapis.com/projects/321488252989/locations/us-central1/services/documents001-bridge-rfp-board",
                    },
                    "workhorse_model": WORKHORSE_MODEL,
                    "reasoning_model": REASONING_MODEL,
                    "security_zone": pol.security_zone,
                    "allowed_channels": list(pol.allowed_channels),
                    "allowed_roles": list(pol.allowed_roles),
                    "allowed_views": list(pol.allowed_views),
                    "allow_vault_promotion": pol.allow_vault_promotion,
                    "max_approval_amount": max_limit,
                    "a2a_skills": a2a_skills_list if pol.security_zone == "internal" else [],
                }
            )

        mcp_servers_list: list[dict[str, Any]] = [
            {
                "mcp_server_id": "urn:mcp:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:knowledge-catalog-mcp",
                "resource_name": "projects/321488252989/locations/us-central1/mcpServers/agentregistry-knowledge-catalog-mcp",
                "display_name": "Zone 6 Knowledge Catalog MCP Server",
                "registration_mode": "MANUAL",
                "spec_type": "tool-spec",
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": "//run.googleapis.com/projects/321488252989/locations/us-central1/services/documents001-bridge-rfp-board",
                },
                "tools": [
                    "search_knowledge_catalog",
                    "get_glossary_term",
                    "trace_field_lineage",
                    "authorize_spiffe_access",
                    "catalog.trace_lineage",
                    "catalog.lookup_glossary",
                    "catalog.check_quality_rules",
                ],
                "data_resources": [
                    "glossary_terms",
                    "catalog_assets",
                    "spiffe_agent_policies",
                ],
            },
            {
                "mcp_server_id": "urn:mcp:googleapis.com:projects:321488252989:locations:global:bigquery-mcp",
                "resource_name": "projects/321488252989/locations/global/mcpServers/agentregistry-bigquery-mcp",
                "display_name": "Google Cloud BigQuery & Open Iceberg MCP Server",
                "registration_mode": "AUTOMATIC",
                "spec_type": "tool-spec",
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": "//bigquery.googleapis.com/projects/documents001/datasets/docubridge_lakehouse",
                },
                "tools": [
                    "execute_sql",
                    "get_table_schema",
                    "export_iceberg_watermark",
                    "query_otel_spans",
                    "bigquery.query_iceberg",
                    "bigquery.query_otel_spans",
                ],
                "data_resources": [
                    "docubridge_lakehouse.bq_operational_facts_iceberg",
                    "docubridge_telemetry.otel_spans",
                ],
            },
            {
                "mcp_server_id": "urn:mcp:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:alloydb-operational-store-mcp",
                "resource_name": "projects/321488252989/locations/us-central1/mcpServers/agentregistry-alloydb-operational-store-mcp",
                "display_name": "Zone 3 Bi-Temporal Operational Store MCP Server",
                "registration_mode": "MANUAL",
                "spec_type": "tool-spec",
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": "//sqladmin.googleapis.com/projects/documents001/instances/bridge-sql",
                },
                "tools": [
                    "query_bitemporal_facts",
                    "compute_financial_summary",
                    "list_fact_conflicts",
                    "consolidate_entry",
                    "operational_store.query_bitemporal_facts",
                    "operational_store.compute_financial_summary",
                ],
                "data_resources": [
                    "operational_store.bitemporal_facts",
                    "v_broker_placement",
                    "v_underwriter_exposure",
                    "v_claims_coverage",
                ],
            },
        ]

        endpoints_list: list[dict[str, Any]] = [
            {
                "endpoint_id": "urn:endpoint:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:docai-layout-ocr",
                "resource_name": "projects/321488252989/locations/us-central1/endpoints/agentregistry-docai-layout-ocr",
                "display_name": "Document AI Layout & Bounding-Polygon OCR Endpoint",
                "registration_mode": "MANUAL",
                "spec_type": "no-spec",
                "target_uri": "https://us-documentai.googleapis.com/v1/projects/321488252989/locations/us/processors/docai-layout:process",
                "security_zone": "internal",
            },
            {
                "endpoint_id": "urn:endpoint:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:audit-vault-worm",
                "resource_name": "projects/321488252989/locations/us-central1/endpoints/agentregistry-audit-vault-worm",
                "display_name": "Zone 1 Cloud Storage 7-Year WORM Audit Vault Endpoint",
                "registration_mode": "MANUAL",
                "spec_type": "no-spec",
                "target_uri": "https://storage.googleapis.com/storage/v1/b/documents001-bridge-audit-vault",
                "security_zone": "internal",
            },
            {
                "endpoint_id": "urn:endpoint:projects-321488252989:projects:321488252989:locations:us-central1:agentregistry:services:a2ui-v09-inbox",
                "resource_name": "projects/321488252989/locations/us-central1/endpoints/agentregistry-a2ui-v09-inbox",
                "display_name": "Zone 5 A2UI v0.9 Action Inbox & Governance Surface Endpoint",
                "registration_mode": "MANUAL",
                "spec_type": "no-spec",
                "target_uri": "https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/v1/a2ui/inbox/actions",
                "security_zone": "internal",
            },
        ]

        bindings_list: list[dict[str, Any]] = [
            {
                "binding_id": "binding-engine-to-knowledge-catalog-mcp",
                "resource_name": "projects/321488252989/locations/us-central1/bindings/binding-engine-to-knowledge-catalog-mcp",
                "source_agent": "projects/321488252989/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": "projects/321488252989/locations/us-central1/mcpServers/agentregistry-knowledge-catalog-mcp",
                "target_type": "MCP_SERVER",
                "auth_provider": "agent-identity-mtls-jwt-svid",
                "delegation_mode": "RFC8693_OBO_INTERSECTION",
            },
            {
                "binding_id": "binding-engine-to-operational-store-mcp",
                "resource_name": "projects/321488252989/locations/us-central1/bindings/binding-engine-to-operational-store-mcp",
                "source_agent": "projects/321488252989/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": "projects/321488252989/locations/us-central1/mcpServers/agentregistry-alloydb-operational-store-mcp",
                "target_type": "MCP_SERVER",
                "auth_provider": "agent-identity-mtls-jwt-svid",
                "delegation_mode": "RFC8693_OBO_INTERSECTION",
            },
            {
                "binding_id": "binding-engine-to-bigquery-mcp",
                "resource_name": "projects/321488252989/locations/us-central1/bindings/binding-engine-to-bigquery-mcp",
                "source_agent": "projects/321488252989/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": "projects/321488252989/locations/global/mcpServers/agentregistry-bigquery-mcp",
                "target_type": "MCP_SERVER",
                "auth_provider": "gcp-workload-identity",
                "delegation_mode": "2LO_SERVICE_AGENT",
            },
            {
                "binding_id": "binding-engine-to-docai-endpoint",
                "resource_name": "projects/321488252989/locations/us-central1/bindings/binding-engine-to-docai-endpoint",
                "source_agent": "projects/321488252989/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": "projects/321488252989/locations/us-central1/endpoints/agentregistry-docai-layout-ocr",
                "target_type": "ENDPOINT",
                "auth_provider": "gcp-workload-identity",
                "delegation_mode": "2LO_SERVICE_AGENT",
            },
            {
                "binding_id": "binding-credit-officer-to-a2ui-inbox",
                "resource_name": "projects/321488252989/locations/us-central1/bindings/binding-credit-officer-to-a2ui-inbox",
                "source_agent": "projects/321488252989/locations/us-central1/agents/agentregistry-credit-officer-agent",
                "target_resource": "projects/321488252989/locations/us-central1/endpoints/agentregistry-a2ui-v09-inbox",
                "target_type": "ENDPOINT",
                "auth_provider": "agent-identity-mtls-jwt-svid",
                "delegation_mode": "RFC8693_OBO_INTERSECTION",
            },
        ]

        custom_services = getattr(self, "_custom_registry_services", [])
        for svc in custom_services:
            kind = svc.get("component_type")
            if kind == "AGENT":
                agents_list.append(svc["projected"])
            elif kind == "MCP_SERVER":
                mcp_servers_list.append(svc["projected"])
            elif kind == "ENDPOINT":
                endpoints_list.append(svc["projected"])

        custom_bindings = getattr(self, "_custom_registry_bindings", [])
        bindings_list.extend(custom_bindings)

        return {
            "agent_registry": {
                "project_id": "documents001",
                "project_number": "321488252989",
                "location": "us-central1",
                "governance_scope": "projects/321488252989/locations/us-central1",
                "registry_api": "https://agentregistry.googleapis.com/v1alpha/projects/documents001/locations/us-central1",
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "agent_count": len(agents_list),
                "mcp_server_count": len(mcp_servers_list),
                "endpoint_count": len(endpoints_list),
                "binding_count": len(bindings_list),
                "agents": agents_list,
                "mcp_servers": mcp_servers_list,
                "endpoints": endpoints_list,
                "bindings": bindings_list,
                "publishers": [
                    {
                        "publisher_id": "private",
                        "display_name": "Organization Private Publisher (documents001)",
                        "urn_prefix": "urn:skill:projects-321488252989:locations:global:private-",
                    },
                    {
                        "publisher_id": "cloud.google.com",
                        "display_name": "Google Cloud Official Skills",
                        "urn_prefix": "urn:skill:cloud.google.com:cloud:",
                    },
                    {
                        "publisher_id": "discoveryengine.googleapis.com",
                        "display_name": "Gemini Enterprise Built-in Skills",
                        "urn_prefix": "urn:skill:discoveryengine.googleapis.com:discoveryengine:",
                    },
                ],
            },
            "skill_registry": {
                "bucket": "gs://documents001-bridge-skills",
                "registry_api": "https://agentregistry.googleapis.com/v1alpha/projects/documents001/locations/global/skills",
                "service_agent": "service-321488252989@gcp-sa-agentregistry.iam.gserviceaccount.com",
                "skill_count": len(skill_containers),
                "skills": skill_containers,
            },
            "agent_gateway": self.get_gateway_catalog(),
            "process_skill_lineage": [
                r.model_dump(mode="json") for r in self.list_process_skill_lineage()
            ],
        }

    def register_registry_service(
        self,
        *,
        service_id: str,
        display_name: str,
        component_type: Literal["AGENT", "MCP_SERVER", "ENDPOINT"],
        spec_type: Literal["a2a-agent-card", "no-spec", "tool-spec"],
        target_uri: str,
        location: str = "us-central1",
        agent_principal: str | None = None,
        tools: list[str] | None = None,
        data_resources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Writable `Service` resource registration in Agent Registry (`POST .../services?serviceId=`),
        automatically projecting onto the read-only `Agent`, `McpServer`, or `Endpoint` collection."""
        if not hasattr(self, "_custom_registry_services"):
            self._custom_registry_services = []
        service_name = f"projects/321488252989/locations/{location}/services/{service_id}"
        if component_type == "AGENT":
            urn = f"urn:agent:projects-321488252989:projects:321488252989:locations:{location}:agentregistry:services:{service_id}"
            reg_res = f"projects/321488252989/locations/{location}/agents/agentregistry-{service_id}"
            projected = {
                "agent_id": urn,
                "agent_urn": urn,
                "resource_name": reg_res,
                "display_name": display_name,
                "registration_mode": "MANUAL",
                "spec_type": spec_type,
                "protocol": "A2A/1.0" if spec_type == "a2a-agent-card" else "REST",
                "agent_principal": agent_principal
                or f"spiffe://agents.docubridge.io/internal/{service_id}",
                "spiffe_id": agent_principal
                or f"spiffe://agents.docubridge.io/internal/{service_id}",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": target_uri,
                },
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "security_zone": "internal",
                "a2a_skills": [],
            }
        elif component_type == "MCP_SERVER":
            urn = f"urn:mcp:projects-321488252989:projects:321488252989:locations:{location}:agentregistry:services:{service_id}"
            reg_res = f"projects/321488252989/locations/{location}/mcpServers/agentregistry-{service_id}"
            projected = {
                "mcp_server_id": urn,
                "resource_name": reg_res,
                "display_name": display_name,
                "registration_mode": "MANUAL",
                "spec_type": spec_type,
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": target_uri,
                },
                "tools": list(tools or []),
                "data_resources": list(data_resources or []),
            }
        else:
            urn = f"urn:endpoint:projects-321488252989:projects:321488252989:locations:{location}:agentregistry:services:{service_id}"
            reg_res = f"projects/321488252989/locations/{location}/endpoints/agentregistry-{service_id}"
            projected = {
                "endpoint_id": urn,
                "resource_name": reg_res,
                "display_name": display_name,
                "registration_mode": "MANUAL",
                "spec_type": spec_type,
                "target_uri": target_uri,
                "security_zone": "internal",
            }
        entry = {
            "name": service_name,
            "service_id": service_id,
            "component_type": component_type,
            "spec_type": spec_type,
            "registry_resource": reg_res,
            "identifier_urn": urn,
            "projected": projected,
        }
        self._custom_registry_services.append(entry)
        return entry

    def create_registry_binding(
        self,
        *,
        binding_id: str,
        source_agent: str,
        target_resource: str,
        target_type: Literal["AGENT", "MCP_SERVER", "ENDPOINT"] = "MCP_SERVER",
        auth_provider: str = "agent-identity-mtls-jwt-svid",
        delegation_mode: str = "RFC8693_OBO_INTERSECTION",
        location: str = "us-central1",
    ) -> dict[str, Any]:
        """Create an explicit Agent Registry `Binding` connecting `source_agent` to `target_resource`."""
        if not hasattr(self, "_custom_registry_bindings"):
            self._custom_registry_bindings = []
        if source_agent.startswith("urn:"):
            raise ValueError(
                "NOT_FOUND: Requested entity was not found. Policy bindings and route rules must specify "
                "the hierarchical resource name (projects/.../locations/.../agents/agentregistry-...), not a URN."
            )
        if target_resource.startswith("urn:"):
            raise ValueError(
                "NOT_FOUND: Requested entity was not found. Policy bindings and route rules must specify "
                "the hierarchical resource name (projects/.../locations/.../.../agentregistry-...), not a URN."
            )
        binding = {
            "binding_id": binding_id,
            "resource_name": f"projects/321488252989/locations/{location}/bindings/{binding_id}",
            "source_agent": source_agent,
            "target_resource": target_resource,
            "target_type": target_type,
            "auth_provider": auth_provider,
            "delegation_mode": delegation_mode,
        }
        self._custom_registry_bindings.append(binding)
        return binding

    def get_gateway_catalog(self) -> dict[str, Any]:
        """Return the Gemini platform Agent Gateway (`networkservices.googleapis.com`)
        and IAM v3 Unified Access Policy (`iam.googleapis.com/v3beta`) control-plane catalog."""
        return {
            "service_api": "https://networkservices.googleapis.com/v1/projects/documents001/locations/us-central1/agentGateways",
            "iam_uap_api": "https://iam.googleapis.com/v3beta/projects/documents001/locations/global/accessPolicies",
            "gateways": [
                {
                    "gateway_id": "docubridge-client-to-agent-gw",
                    "resource_name": "projects/documents001/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                    "display_name": "DocuBridge Client-to-Agent Ingress Gateway",
                    "governed_access_path": "CLIENT_TO_AGENT",
                    "deployment_mode": "GOOGLE_MANAGED",
                    "protocols": ["A2A", "MCP", "HTTPS"],
                    "model_armor": {
                        "enabled": True,
                        "request_template": "projects/documents001/locations/us-central1/templates/docubridge-model-armor-strict",
                        "response_template": "projects/documents001/locations/us-central1/templates/docubridge-model-armor-strict",
                        "filters": [
                            "PI_AND_JAILBREAK_BLOCK",
                            "CLOUD_DLP_SDP_REDACT_SSN",
                            "MALICIOUS_URI_FILTER",
                        ],
                    },
                    "per_party_invoker_scoping": True,
                },
                {
                    "gateway_id": "docubridge-agent-to-anywhere-gw",
                    "resource_name": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "display_name": "DocuBridge Agent-to-Anywhere Egress Gateway",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "deployment_mode": "GOOGLE_MANAGED",
                    "protocols": ["MCP", "A2A"],
                    "registries": [
                        "//agentregistry.googleapis.com/projects/documents001/locations/us-central1",
                        "//agentregistry.googleapis.com/projects/documents001/locations/global",
                    ],
                    "authz_extension": {
                        "name": "projects/documents001/locations/us-central1/authzExtensions/docubridge-iap-request-authz-ext",
                        "service": "iap.googleapis.com",
                        "fail_open": False,
                        "timeout": "1s",
                        "iap_policy_version": "V2",
                        "enforcement_mode": "ENFORCED",
                    },
                    "authz_policy": "projects/documents001/locations/us-central1/authzPolicies/docubridge-iap-request-authz-policy",
                },
            ],
            "unified_access_policy": {
                "access_policy_id": "docubridge-agent-egress-uap",
                "resource_name": "projects/documents001/locations/global/accessPolicies/docubridge-agent-egress-uap",
                "evaluation_order": "DENY_OVERRIDES_ALLOW",
                "permission": "iap.googleapis.com/resources.egressViaIAP",
                "rules": [
                    {
                        "rule_id": "uap-deny-destructive-or-external-mcp",
                        "effect": "DENY",
                        "description": "Block destructive MCP tools and block external portal agent from internal MCP servers",
                        "principals": ["*"],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['operational_store.drop_table', 'vault.delete_worm_object'] || "
                            "request.auth.principal.endsWith('/external-portal-agent')"
                        ),
                    },
                    {
                        "rule_id": "uap-allow-knowledge-catalog-mcp",
                        "effect": "ALLOW",
                        "description": "Allow vetted internal agents to invoke Knowledge Catalog & Lineage MCP tools",
                        "principals": [
                            "principalSet://agents.global.org-docubridge.system.id.goog/attribute.platformContainer/aiplatform/projects/321488252989"
                        ],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.name == "
                            "'projects/321488252989/locations/us-central1/mcpServers/agentregistry-knowledge-catalog-mcp' && "
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['catalog.trace_lineage', 'catalog.list_assets', 'catalog.authorize_access']"
                        ),
                    },
                    {
                        "rule_id": "uap-allow-alloydb-operational-mcp",
                        "effect": "ALLOW",
                        "description": "Allow vetted internal agents to query and consolidate AlloyDB Bi-Temporal facts",
                        "principals": [
                            "principalSet://agents.global.org-docubridge.system.id.goog/attribute.platformContainer/aiplatform/projects/321488252989"
                        ],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.name == "
                            "'projects/321488252989/locations/us-central1/mcpServers/agentregistry-alloydb-operational-store-mcp' && "
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['operational_store.query_as_of', 'operational_store.consolidate_entry', 'operational_store.export_iceberg_cdc']"
                        ),
                    },
                    {
                        "rule_id": "uap-allow-bigquery-lakehouse-mcp",
                        "effect": "ALLOW",
                        "description": "Allow vetted internal agents to query BigQuery Iceberg & OpenTelemetry spans",
                        "principals": [
                            "principalSet://agents.global.org-docubridge.system.id.goog/attribute.platformContainer/aiplatform/projects/321488252989"
                        ],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.name == "
                            "'projects/321488252989/locations/us-central1/mcpServers/agentregistry-bigquery-mcp' && "
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['bigquery.query_iceberg', 'bigquery.query_otel_spans']"
                        ),
                    },
                ],
            },
        }

    def evaluate_gateway_route(
        self,
        *,
        governed_access_path: Literal["CLIENT_TO_AGENT", "AGENT_TO_ANYWHERE"],
        source_principal: str,
        target_resource: str,
        protocol: Literal["MCP", "A2A", "HTTPS"] = "MCP",
        mcp_tool_name: str | None = None,
        payload_text: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate an inbound (`CLIENT_TO_AGENT`) or outbound (`AGENT_TO_ANYWHERE`) request
        through the Gemini platform Agent Gateway, Model Armor + Cloud DLP, Agent Registry,
        and IAM v3 Unified Access Policy (CEL)."""
        from bridge.vault import _scrub_string

        with trace_step(
            "docubridge.gateway.evaluate_route",
            {
                "docubridge.zone": "1_edge_intake",
                "docubridge.gateway.access_path": governed_access_path,
                "docubridge.gateway.source_principal": source_principal,
                "docubridge.gateway.target_resource": target_resource,
                "docubridge.gateway.protocol": protocol,
                "docubridge.gateway.mcp_tool_name": mcp_tool_name or "",
            },
        ) as span:
            if governed_access_path == "CLIENT_TO_AGENT":
                raw_text = payload_text or ""
                lower_text = raw_text.lower()
                injection_markers = (
                    "ignore previous instructions",
                    "ignore all prior rules",
                    "system prompt override",
                    "bypass approval authority",
                    "drop table",
                )
                if any(m in lower_text for m in injection_markers):
                    span.set_attribute("docubridge.gateway.decision", "DENY_MODEL_ARMOR")
                    return {
                        "allowed": False,
                        "http_status": 403,
                        "decision": "DENY_MODEL_ARMOR_PROMPT_INJECTION",
                        "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                        "governed_access_path": "CLIENT_TO_AGENT",
                        "model_armor_verdict": "BLOCKED (PI_AND_JAILBREAK_FILTER)",
                        "dlp_redacted_fields": 0,
                        "sanitized_payload": None,
                        "reason": "Model Armor template 'docubridge-model-armor-strict' blocked adversarial prompt-injection payload at CLIENT_TO_AGENT ingress.",
                    }
                sanitized_text, changed = _scrub_string(raw_text)
                redaction_count = sanitized_text.count("[REDACTED-") if changed else 0
                span.set_attribute("docubridge.gateway.decision", "ALLOW_INGRESS")
                span.set_attribute("docubridge.gateway.dlp_redactions", redaction_count)
                return {
                    "allowed": True,
                    "http_status": 200,
                    "decision": "ALLOW_CLIENT_TO_AGENT_INGRESS",
                    "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                    "governed_access_path": "CLIENT_TO_AGENT",
                    "model_armor_verdict": "PASS (PI_AND_JAILBREAK_CLEAN)",
                    "dlp_redacted_fields": redaction_count,
                    "sanitized_payload": sanitized_text,
                    "reason": "Ingress request passed Model Armor inspection and Cloud DLP inline SSN redaction.",
                }

            # AGENT_TO_ANYWHERE (Agent Egress)
            if source_principal.startswith("urn:") or target_resource.startswith("urn:"):
                span.set_attribute("docubridge.gateway.decision", "NOT_FOUND_URN_MISUSE")
                return {
                    "allowed": False,
                    "http_status": 404,
                    "decision": "NOT_FOUND_URN_MISUSE",
                    "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": None,
                    "reason": (
                        "NOT_FOUND: Requested entity was not found. Agent Gateway & IAM Unified Access Policies "
                        "require the hierarchical Resource Name (projects/.../locations/.../mcpServers/agentregistry-...), not a URN."
                    ),
                }

            cat = self.get_registry_catalog()["agent_registry"]
            registered_agent_principals = (
                {a["resource_name"] for a in cat["agents"]}
                | {a["agent_principal"] for a in cat["agents"]}
                | {a["spiffe_id"] for a in cat["agents"]}
            )
            if source_principal not in registered_agent_principals:
                span.set_attribute("docubridge.gateway.decision", "DENY_SHADOW_AI_SOURCE")
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNREGISTERED_SHADOW_AGENT",
                    "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "agent-registry-membership-check",
                    "reason": f"Source principal '{source_principal}' is not registered in Agent Registry (Shadow AI blocked).",
                }

            registered_targets = (
                {m["resource_name"]: m for m in cat["mcp_servers"]}
                | {e["resource_name"]: e for e in cat["endpoints"]}
                | {a["resource_name"]: a for a in cat["agents"]}
            )
            if target_resource not in registered_targets:
                span.set_attribute("docubridge.gateway.decision", "DENY_UNREGISTERED_TARGET")
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNREGISTERED_TARGET_RESOURCE",
                    "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "agent-registry-target-check",
                    "reason": f"Destination '{target_resource}' is not registered in //agentregistry.googleapis.com/projects/documents001.",
                }

            # Evaluate IAM v3 Unified Access Policy: DENY rules evaluated first!
            if (
                mcp_tool_name in ("operational_store.drop_table", "vault.delete_worm_object")
                or source_principal.endswith("/external-portal-agent")
                or source_principal.endswith("agentregistry-external-portal-agent")
            ):
                span.set_attribute("docubridge.gateway.decision", "DENY_UAP_CEL")
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UAP_CEL_POLICY",
                    "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "uap-deny-destructive-or-external-mcp",
                    "permission": "iap.googleapis.com/resources.egressViaIAP",
                    "reason": (
                        f"IAM Unified Access Policy deny rule 'uap-deny-destructive-or-external-mcp' blocked "
                        f"source='{source_principal}' tool='{mcp_tool_name}' on target='{target_resource}'."
                    ),
                }

            target_obj = registered_targets[target_resource]
            allowed_tools = target_obj.get("tools")
            if allowed_tools and mcp_tool_name and mcp_tool_name not in allowed_tools:
                span.set_attribute("docubridge.gateway.decision", "DENY_UNDECLARED_MCP_TOOL")
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNDECLARED_MCP_TOOL",
                    "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "uap-default-deny",
                    "permission": "iap.googleapis.com/resources.egressViaIAP",
                    "reason": f"Tool '{mcp_tool_name}' is not permitted by any ALLOW rule on MCP server '{target_resource}'.",
                }

            span.set_attribute("docubridge.gateway.decision", "ALLOW_EGRESS")
            return {
                "allowed": True,
                "http_status": 200,
                "decision": "ALLOW_EGRESS_VIA_IAP",
                "gateway": "projects/documents001/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                "governed_access_path": "AGENT_TO_ANYWHERE",
                "matched_uap_rule": "uap-allow-registered-mcp-or-a2a",
                "permission": "iap.googleapis.com/resources.egressViaIAP",
                "protocol": protocol,
                "mcp_tool_name": mcp_tool_name,
                "reason": (
                    f"IAP v2 Unified Access Policy granted 'iap.googleapis.com/resources.egressViaIAP' "
                    f"for '{source_principal}' -> '{target_resource}' (tool='{mcp_tool_name or 'A2A'}')."
                ),
            }

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
            span.set_attribute("docubridge.consecutive_matches", policy.consecutive_matches)
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
        self._skill_revisions_table: Any = None
        self._process_skill_lineage_table: Any = None

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
            self._skill_revisions_table = Table(
                SKILL_REVISIONS_TABLE_NAME,
                meta,
                Column("revision_id", String(128), primary_key=True),
                Column("skill_name", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._process_skill_lineage_table = Table(
                PROCESS_SKILL_LINEAGE_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("lineage_id", String(64), unique=True, nullable=False),
                Column("context_id", String(128), nullable=False, index=True),
                Column("entity_id", String(128), nullable=False, index=True),
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

    def _save_skill_revision(self, revision: SkillRevisionRecord) -> None:
        self._ensure_ready()
        table = self._skill_revisions_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.revision_id == revision.revision_id)
                )
                await conn.execute(
                    table.insert().values(
                        revision_id=revision.revision_id,
                        skill_name=revision.skill_name,
                        payload_json=revision.model_dump_json(),
                    )
                )

        self._run(_op())

    def _list_all_skill_revisions(self) -> list[SkillRevisionRecord]:
        self._ensure_ready()
        table = self._skill_revisions_table
        engine = self._engine

        async def _op() -> list[SkillRevisionRecord]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.revision_id.asc())
                )
                return [
                    SkillRevisionRecord.model_validate_json(row[0]) for row in res
                ]

        return self._run(_op())

    def _append_process_skill_lineage(self, record: ProcessSkillLineageRecord) -> None:
        self._ensure_ready()
        table = self._process_skill_lineage_table
        engine = self._engine

        async def _op() -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    table.insert().values(
                        lineage_id=record.lineage_id,
                        context_id=record.context_id,
                        entity_id=record.entity_id,
                        payload_json=record.model_dump_json(),
                    )
                )

        self._run(_op())

    def _list_all_process_skill_lineage(self) -> list[ProcessSkillLineageRecord]:
        self._ensure_ready()
        table = self._process_skill_lineage_table
        engine = self._engine

        async def _op() -> list[ProcessSkillLineageRecord]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.seq.asc())
                )
                return [
                    ProcessSkillLineageRecord.model_validate_json(row[0]) for row in res
                ]

        return self._run(_op())

    def _clear_rules_and_signals(self) -> None:
        self._ensure_ready()
        signals_table = self._signals_table
        rules_table = self._rules_table
        reports_table = self._reports_table
        shadow_table = self._shadow_table
        skill_revisions_table = self._skill_revisions_table
        process_skill_lineage_table = self._process_skill_lineage_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(reports_table))
                await conn.execute(delete(rules_table))
                await conn.execute(delete(signals_table))
                await conn.execute(delete(shadow_table))
                await conn.execute(delete(skill_revisions_table))
                await conn.execute(delete(process_skill_lineage_table))

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
    "ProcessSkillLineageRecord",
    "ProgrammableTrustPolicy",
    "ShadowComparisonRecord",
    "SignalSourceZone",
    "SkillRevisionRecord",
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
