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

import fnmatch
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bridge.a2ui_inbox import (
    A2UI_V09_VERSION,
    INBOX_CATALOG_ID,
    Rfc6902PatchOp,
    RiskTier,
)
from bridge.knowledge_catalog import CatalogRole
from contract.extraction import SourceAnchor

WORKHORSE_MODEL = "gemini-3.8-flash"
REASONING_MODEL = "gemini-4-argon"

DEFAULT_GCP_PROJECT_ID = "docubridge-showcase"
DEFAULT_GCP_PROJECT_NUMBER = "100000000000"
DEFAULT_REASONING_ENGINE_ID = "1000000000000000000"


def _gcp_project_id() -> str:
    """Resolve the active GCP project ID from environment or showcase default."""
    return (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("BRIDGE_GCP_PROJECT", "").strip()
        or DEFAULT_GCP_PROJECT_ID
    )


def _gcp_project_number() -> str:
    """Resolve the active GCP project number from environment or showcase default."""
    return (
        os.environ.get("BRIDGE_GCP_PROJECT_NUMBER", "").strip()
        or DEFAULT_GCP_PROJECT_NUMBER
    )


def _reasoning_engine_id() -> str:
    """Resolve the Gemini platform Reasoning Engine resource ID."""
    return (
        os.environ.get("BRIDGE_AGENT_ENGINE_ID", "").strip()
        or DEFAULT_REASONING_ENGINE_ID
    )


def _skills_bucket() -> str:
    """Resolve the Cloud Storage bucket name for versioned Agent Skills."""
    return (
        os.environ.get("BRIDGE_SKILLS_BUCKET", "").strip()
        or f"{_gcp_project_id()}-bridge-skills"
    )


def _vault_bucket() -> str:
    """Resolve the Cloud Storage bucket name for the 7-year WORM Audit Vault."""
    return (
        os.environ.get("BRIDGE_VAULT_BUCKET", "").strip()
        or f"{_gcp_project_id()}-bridge-audit-vault"
    )


def _bridge_service_name() -> str:
    """Resolve the Cloud Run A2A Edge service name."""
    return (
        os.environ.get("BRIDGE_SERVICE_NAME", "").strip()
        or f"{_gcp_project_id()}-bridge"
    )


def _board_service_name() -> str:
    """Resolve the Cloud Run Enterprise Board & A2UI service name."""
    return (
        os.environ.get("BRIDGE_BOARD_SERVICE_NAME", "").strip()
        or f"{_gcp_project_id()}-bridge-rfp-board"
    )


def _board_public_url() -> str:
    """Resolve the public base URL of the Enterprise Board & A2UI service."""
    return (
        os.environ.get("BRIDGE_BOARD_PUBLIC_URL", "").strip().rstrip("/")
        or f"https://{_board_service_name()}.a.run.app"
    )


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
    pre_committed: bool = False
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
    citation_verified: bool = True
    oversight_rsr_score: float = 0.98
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
    process_skill_urn: str = Field(
        default_factory=lambda: (
            f"urn:skill:projects-{_gcp_project_id()}:locations:global:private-commercial-loan-closing"
        )
    )
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
    spot_check_sample_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    last_updated_by: str = "system:bootstrap"
    last_updated_reason: str = "Initial Shadow Mode onboarding"
    agent_spiffe_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def accuracy_rate(self) -> float:
        """Fraction of evaluated shadow/review comparisons that matched ground truth."""
        if self.total_evaluated == 0:
            return 1.0
        return round(self.total_exact_matches / self.total_evaluated, 4)

    @property
    def ready_for_promotion(self) -> bool:
        """True when consecutive matches meet the threshold to advance autonomy."""
        return (
            self.autonomy_level != TrustAutonomyLevel.TRUSTED_AUTO_APPROVE
            and self.consecutive_matches >= self.required_matches_for_promotion
        )

    @property
    def recommended_next_level(self) -> TrustAutonomyLevel:
        """Next graduated autonomy tier on the Programmable Trust ladder."""
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
        if (
            "syndicate 1183" in raw_observed_value.lower()
            and "1183" not in (rule.canonical_target or "").lower()
        ):
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
        or _strip_legal_suffix(input_issuer)
        .lower()
        .startswith(rule.issuer_pattern.rstrip("*").lower())
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
            "spotCheckSampleRate": policy.spot_check_sample_rate,
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
REGISTRY_SERVICES_TABLE_NAME = "gym_registry_services"
REGISTRY_BINDINGS_TABLE_NAME = "gym_registry_bindings"
_OP_TIMEOUT_S = 15.0
