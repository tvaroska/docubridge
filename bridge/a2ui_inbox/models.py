"""Pydantic models, RFC 6902 JSON Patch calculator, and default progress steps for A2UI v0.9."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bridge.a2ui_inbox.catalogs import (
    A2UI_V09_MIME_TYPE,
    A2UI_V09_VERSION,
    ActionStepStatus,
    GovernanceDecision,
    RiskTier,
    TaskLifecycleState,
)
from bridge.knowledge_catalog import CatalogRole, DeliveryChannel
from contract.extraction import SourceAnchor


class ActionCitation(BaseModel):
    """Source PDF page citation attached to an action progress step or conflict row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    document_title: str
    field_name: str
    page_number: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float] | None = None
    excerpt: str | None = None
    vault_uri: str | None = None


class ActionProgressStep(BaseModel):
    """One milestone step in an end-to-end tracked agent action."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    step_number: int = Field(ge=1)
    label: str
    external_label: str
    zone: str
    status: ActionStepStatus = "pending"
    detail: str | None = None
    external_detail: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    citations: list[ActionCitation] = Field(default_factory=list)


class A2aDataPart(BaseModel):
    """A2A Protocol DataPart envelope carrying A2UI v0.9 declarative messages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["data"] = "data"
    mime_type: str = A2UI_V09_MIME_TYPE
    data: list[dict[str, Any]]


class Rfc6902PatchOp(BaseModel):
    """Single RFC 6902 JSON Patch operation comparing initial vs. edited data model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["add", "remove", "replace"]
    path: str
    value: Any = None


class InboxAuditRecord(BaseModel):
    """Immutable SOX / SOC2 / EU AI Act Art. 14 audit entry for an Inbox action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: str
    item_id: str
    entity_id: str
    session_id: str
    invocation_id: str | None = None
    decision: GovernanceDecision
    actor_principal: str
    actor_role: CatalogRole
    agent_spiffe_id: str | None = None
    channel: DeliveryChannel
    initial_data_model: dict[str, Any]
    resolved_data_model: dict[str, Any]
    patches: list[Rfc6902PatchOp] = Field(default_factory=list)
    notes: str = ""
    escalated_to: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActionInboxItem(BaseModel):
    """Stateful A2UI v0.9 Action Progress & HITL Inbox item."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    surface_id: str
    entity_id: str
    context_id: str
    session_id: str
    invocation_id: str | None = None
    confirmation_call_id: str | None = None
    title: str
    external_title: str
    hint: str
    external_hint: str
    task_state: TaskLifecycleState = "working"
    risk_level: RiskTier = "MEDIUM"
    confidence_score: float = Field(default=0.95, ge=0.0, le=1.0)
    assigned_role: CatalogRole = "broker"
    target_view: str = "v_broker_placement"
    steps: list[ActionProgressStep] = Field(default_factory=list)
    initial_data_model: dict[str, Any] = Field(default_factory=dict)
    current_data_model: dict[str, Any] = Field(default_factory=dict)
    max_approval_amount: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    delta_history: list[dict[str, Any]] = Field(default_factory=list)


class A2uiClientActionRequest(BaseModel):
    """Client-to-server A2UI v0.9 action payload submitted from the Inbox/Portal UI."""

    model_config = ConfigDict(extra="forbid")

    version: str = A2UI_V09_VERSION
    action: dict[str, Any]


def _escape_json_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def compute_rfc6902_patches(
    initial: dict[str, Any],
    current: dict[str, Any],
    *,
    prefix: str = "",
) -> list[Rfc6902PatchOp]:
    """Compute a deterministic RFC 6902 JSON Patch diff between two dicts."""
    patches: list[Rfc6902PatchOp] = []
    all_keys = sorted(set(initial.keys()) | set(current.keys()))
    for key in all_keys:
        path = f"{prefix}/{_escape_json_pointer(str(key))}"
        if key not in initial:
            patches.append(Rfc6902PatchOp(op="add", path=path, value=current[key]))
        elif key not in current:
            patches.append(Rfc6902PatchOp(op="remove", path=path))
        else:
            val_init = initial[key]
            val_curr = current[key]
            if isinstance(val_init, dict) and isinstance(val_curr, dict):
                patches.extend(compute_rfc6902_patches(val_init, val_curr, prefix=path))
            elif val_init != val_curr:
                patches.append(Rfc6902PatchOp(op="replace", path=path, value=val_curr))
    return patches


def default_submission_progress_steps(
    *,
    doc_id: str = "doc-submission",
    document_title: str = "Carrier Quote Submission",
    anchors: dict[str, SourceAnchor] | None = None,
) -> list[ActionProgressStep]:
    """Create the standard 5-step end-to-end progress pipeline across Zones 1-5."""
    citations: list[ActionCitation] = []
    for fname, anchor in (anchors or {}).items():
        citations.append(
            ActionCitation(
                doc_id=doc_id,
                document_title=document_title,
                field_name=fname,
                page_number=anchor.page_number,
                bounding_box=anchor.bounding_box,
                excerpt=anchor.snippet,
                vault_uri=anchor.artifact_uri,
            )
        )

    return [
        ActionProgressStep(
            step_id="step_quarantine_pii",
            step_number=1,
            label="Zone 1-2: Intake Quarantine & Edge PII Redaction",
            external_label="Step 1: Submission Received & Security Checked",
            zone="zone_2_quarantine",
            status="completed",
            detail="Validated file integrity and stripped personal identifiers at the edge.",
            external_detail="Your document passed security and privacy verification.",
        ),
        ActionProgressStep(
            step_id="step_docai_anchors",
            step_number=2,
            label="Zone 2: Document AI OCR & Source Page Anchoring",
            external_label="Step 2: Extracting Policy Fields & Page Citations",
            zone="zone_2_extraction",
            status="completed",
            detail=f"Extracted {len(citations)} page-anchored field(s) with bounding boxes.",
            external_detail=f"Located {len(citations)} key coverage field(s) in your document.",
            citations=citations,
        ),
        ActionProgressStep(
            step_id="step_audit_vault",
            step_number=3,
            label="Zone 3: Cloud Storage Audit Vault WORM Lock",
            external_label="Step 3: Archiving Official Compliance Copy",
            zone="zone_3_vault",
            status="completed",
            detail="Locked immutable SHA-256 verified document in WORM Audit Vault.",
            external_detail="Archived tamper-proof copy for regulatory record-keeping.",
            citations=citations[:1],
        ),
        ActionProgressStep(
            step_id="step_consolidation_gate",
            step_number=4,
            label="Zone 3: Bi-Temporal Consolidation & Discrepancy Gate",
            external_label="Step 4: Verifying Coverage & Financial Totals",
            zone="zone_3_operational",
            status="working",
            detail="Running Decimal math and cross-document priority rules.",
            external_detail="Checking policy totals and effective dates.",
            citations=citations,
        ),
        ActionProgressStep(
            step_id="step_views_publish",
            step_number=5,
            label="Zone 4-5: Publish to Business Views, BigQuery & A2UI",
            external_label="Step 5: Publishing Final Placement Summary",
            zone="zone_5_a2ui",
            status="pending",
            detail="Awaiting consolidation gate completion before publishing views.",
            external_detail="Final summary will appear once verification completes.",
        ),
    ]
