"""Zone 5 User Interaction: FastAPI request Pydantic models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bridge.a2ui_inbox import ActionStepStatus, RiskTier
from bridge.agent_gym import TribalRuleCategory, TrustAutonomyLevel
from bridge.knowledge_catalog import CatalogRole
from contract.extraction import SourceAnchor


class IngestSubmissionRequest(BaseModel):
    """Request body to promote an accepted submission across Vault, Operational Store & Catalog."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    context_id: str = "ctx-enterprise-intake"
    doc_id: str
    doctype: str
    issuer: str | None = None
    status: Literal["accepted", "parked", "pending", "rejected"] = "accepted"
    confidence: float = Field(default=0.98, ge=0.0, le=1.0)
    summary: str = "Accepted enterprise document submission"
    key_fields: dict[str, Any] = Field(default_factory=dict)
    source_anchors: dict[str, SourceAnchor] = Field(default_factory=dict)
    filename: str | None = None
    version: int = 1
    recorded_at: datetime | None = None
    effective_from: datetime | None = None


class CreateActionProgressRequest(BaseModel):
    """Request body to launch or track an end-to-end action in the A2UI Inbox."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    title: str = "Placement & Coverage Verification"
    external_title: str | None = None
    hint: str = "Validating submission across Audit Vault and Operational Store."
    external_hint: str | None = None
    assigned_role: CatalogRole = "broker"
    target_view: str = "v_broker_placement"
    risk_level: RiskTier = "MEDIUM"
    confidence_score: float = Field(default=0.95, ge=0.0, le=1.0)
    pause_for_review: bool = False
    max_approval_amount: float | None = None
    data_model: dict[str, Any] = Field(default_factory=dict)


class AdvanceActionStepRequest(BaseModel):
    """Request body to advance a single milestone step on an active action."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    status: ActionStepStatus
    detail: str | None = None
    external_detail: str | None = None
    hint: str | None = None


class DistillQuarantineFixRequest(BaseModel):
    """Request body to distill a Tribal Knowledge Rule from a Zone 2 Quarantine fix."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    doc_id: str
    doctype: str = "quote"
    issuer: str
    field_path: str
    before_value: str
    after_value: str
    expert_memo: str
    page_number: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    category: TribalRuleCategory | None = None
    distinct_entities: list[str] = Field(default_factory=list)
    citation_verified: bool = True
    review_dwell_ms: int = 4200


class DistillInboxAuditRequest(BaseModel):
    """Request body to distill a Tribal Knowledge Rule from a Zone 5 Inbox audit record."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = "doc-inbox"
    doctype: str = "quote"
    issuer: str = "Carrier"
    field_path: str | None = None
    category: TribalRuleCategory | None = None
    distinct_entities: list[str] = Field(default_factory=list)
    citation_verified: bool = True
    review_dwell_ms: int = 4200


class CompileSkillRequest(BaseModel):
    """Request body to compile promoted Tribal Knowledge Rules into a versioned SKILL.md via gemini-4-argon."""

    model_config = ConfigDict(extra="forbid")

    doctype: str = "closing-disclosure"
    issuer_pattern: str = "*"


class RollbackSkillRequest(BaseModel):
    """Request body to roll back a skill's defaultRevision pointer in the Skill Registry."""

    model_config = ConfigDict(extra="forbid")

    target_revision_id: str | None = None
    reason: str = "Operator or drift-triggered rollback to parent SkillRevision."


class RegisterServiceRequest(BaseModel):
    """Writable `Service` registration request in Gemini platform Agent Registry."""

    model_config = ConfigDict(extra="forbid")

    service_id: str
    display_name: str
    component_type: Literal["AGENT", "MCP_SERVER", "ENDPOINT"] = "AGENT"
    spec_type: Literal["a2a-agent-card", "no-spec", "tool-spec"] = "a2a-agent-card"
    target_uri: str
    location: str = "us-central1"
    agent_principal: str | None = None
    tools: list[str] = Field(default_factory=list)
    data_resources: list[str] = Field(default_factory=list)


class CreateBindingRequest(BaseModel):
    """Request body to create an explicit `Binding` in Gemini platform Agent Registry."""

    model_config = ConfigDict(extra="forbid")

    binding_id: str
    source_agent: str
    target_resource: str
    target_type: Literal["AGENT", "MCP_SERVER", "ENDPOINT"] = "MCP_SERVER"
    auth_provider: str = "agent-identity-mtls-jwt-svid"
    delegation_mode: str = "RFC8693_OBO_INTERSECTION"
    location: str = "us-central1"


class EvaluateGatewayRouteRequest(BaseModel):
    """Request body to evaluate a `CLIENT_TO_AGENT` or `AGENT_TO_ANYWHERE` request through Agent Gateway."""

    model_config = ConfigDict(extra="forbid")

    governed_access_path: Literal["CLIENT_TO_AGENT", "AGENT_TO_ANYWHERE"] = (
        "AGENT_TO_ANYWHERE"
    )
    source_principal: str
    target_resource: str
    protocol: Literal["MCP", "A2A", "HTTPS"] = "MCP"
    mcp_tool_name: str | None = None
    payload_text: str | None = None


class RecordShadowComparisonRequest(BaseModel):
    """Request body to record a Shadow Mode AI vs. Human comparison outcome."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    doc_id: str
    issuer: str
    doctype: str = "quote"
    field_path: str
    ai_value: str
    human_value: str
    risk_level: RiskTier = "MEDIUM"


class DialTrustPolicyRequest(BaseModel):
    """Request body to promote or dial back a slice's Programmable Trust level."""

    model_config = ConfigDict(extra="forbid")

    target_level: TrustAutonomyLevel
    reason: str = "Domain lead updated Programmable Trust dial."
    force: bool = False
