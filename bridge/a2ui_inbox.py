"""A2UI v0.9 Action Progress & Agent Inbox with Google ADK (Zone 5 Extension).

Unites three open-source layers so internal enterprise teams (in Gemini App / Ops Inbox) and
external clients/carriers (in Custom Web Portal) can both **monitor real-time progress
of multi-step actions** (`submitted` -> `working` -> `input-required` -> `completed`)
and **resolve human-in-the-loop (HITL) interrupts** via native declarative widgets:

1. **Google ADK Pause & Resume (`google-adk`):**
   Supports `ToolContext.request_confirmation` (`adk_request_confirmation`) and
   `LongRunningFunctionTool` checkpoints with `ResumabilityConfig(is_resumable=True)`.
2. **A2A Task Lifecycle & Envelope (`a2a-protocol.org`):**
   Packages declarative UI messages inside an A2A `DataPart` with MIME type
   `application/a2ui+json` across `submitted`, `working`, `input-required`, and
   `completed` task states.
3. **A2UI v0.9 Declarative Surfaces (`a2ui.org`):**
   Separates component structure (`createSurface`, `updateComponents`, `deleteSurface`)
   from live JSON Pointer state (`updateDataModel`), enabling zero-flicker step progress
   streaming over SSE and two-way bound governance actions (`accept`, `edit_and_approve`,
   `respond`, `reject`, `escalate`) with RFC 6902 JSON Patch audit trails.

Isolation: imports `contract/` + `bridge.*` + `google.adk` / `google.genai` + stdlib;
NEVER `agents/`. No pixel/CSS/style fields in any A2UI model.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from bridge.knowledge_catalog import (
    CatalogRole,
    DeliveryChannel,
    authorize_access,
)
from bridge.operational_store import FactConflict
from bridge.telemetry import trace_step
from contract.extraction import SourceAnchor

A2UI_V09_VERSION = "v0.9"
A2UI_V09_MIME_TYPE = "application/a2ui+json"

INBOX_CATALOG_ID = (
    "https://docubridge.io/a2ui/v0_9/catalogs/enterprise_inbox_catalog.json"
)
PORTAL_CATALOG_ID = (
    "https://docubridge.io/a2ui/v0_9/catalogs/portal_customer_catalog.json"
)

TaskLifecycleState = Literal[
    "submitted",
    "working",
    "input-required",
    "completed",
    "failed",
]

ActionStepStatus = Literal[
    "pending",
    "working",
    "input-required",
    "completed",
    "failed",
]

GovernanceDecision = Literal[
    "accept",
    "edit",
    "respond",
    "reject",
    "escalate",
]

RiskTier = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


# --------------------------------------------------------------------------- #
# 1. Governed A2UI v0.9 Catalogs (Internal Enterprise vs External Portal)     #
# --------------------------------------------------------------------------- #

ENTERPRISE_INBOX_CATALOG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "catalogId": INBOX_CATALOG_ID,
    "description": (
        "Enterprise Agent Inbox & Action Progress Catalog v0.9 extending the A2UI "
        "Basic Catalog with live step progress, provenance, and governance widgets."
    ),
    "allOf": [
        {"$ref": "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"}
    ],
    "components": {
        "ActionProgressCard": {
            "type": "object",
            "description": (
                "Live action progress container bound to JSON Pointer step state "
                "(/progress/percent, /progress/steps) across submitted, working, "
                "input-required, and completed states."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "ActionProgressCard"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "taskState": {
                    "type": "string",
                    "enum": [
                        "submitted",
                        "working",
                        "input-required",
                        "completed",
                        "failed",
                    ],
                },
                "progressPath": {"type": "string"},
                "stepsPath": {"type": "string"},
                "children": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "id",
                "component",
                "title",
                "taskState",
                "progressPath",
                "stepsPath",
            ],
        },
        "ApprovalCard": {
            "type": "object",
            "description": (
                "Top-level governance container displaying risk tier, confidence badge, "
                "policy subtitle, and child components when an action pauses."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "ApprovalCard"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "riskLevel": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                },
                "confidenceScore": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "children": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "component", "title", "riskLevel", "children"],
        },
        "FactConflictTable": {
            "type": "object",
            "description": (
                "Multi-row comparison table bound to a JSON Pointer array of "
                "cross-document discrepancies."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "FactConflictTable"},
                "rowsPath": {"type": "string"},
            },
            "required": ["id", "component", "rowsPath"],
        },
        "SourceCitationPill": {
            "type": "object",
            "description": (
                "Clickable provenance badge that opens the document viewer to a "
                "specific source PDF page and highlight."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "SourceCitationPill"},
                "documentTitle": {"type": "string"},
                "docId": {"type": "string"},
                "pageNumber": {"type": "integer", "minimum": 1},
                "excerpt": {"type": "string"},
                "viewerHref": {"type": "string"},
            },
            "required": ["id", "component", "documentTitle", "pageNumber"],
        },
    },
}

PORTAL_CUSTOMER_CATALOG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "catalogId": PORTAL_CATALOG_ID,
    "description": (
        "External Client & Carrier Portal Catalog v0.9 — restricted to external-safe "
        "ActionProgressCard, SourceCitationPill, and self-service inputs (excludes "
        "internal FactConflictTable, confidence scores, and escalation controls)."
    ),
    "components": {
        "ActionProgressCard": ENTERPRISE_INBOX_CATALOG_SCHEMA["components"][
            "ActionProgressCard"
        ],
        "SourceCitationPill": ENTERPRISE_INBOX_CATALOG_SCHEMA["components"][
            "SourceCitationPill"
        ],
    },
}


# --------------------------------------------------------------------------- #
# 2. Data Models (Zero Pixel / Style Fields)                                  #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# 3. Deterministic RFC 6902 JSON Patch Diff Calculator                        #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# 4. Deterministic A2UI v0.9 Surface & Delta Builders (Internal vs External)  #
# --------------------------------------------------------------------------- #


def _compute_progress_percent(steps: list[ActionProgressStep]) -> int:
    if not steps:
        return 0
    completed = sum(1 for s in steps if s.status == "completed")
    return round((completed / len(steps)) * 100)


def _sanitize_data_model_for_channel(
    item: ActionInboxItem,
    *,
    channel: DeliveryChannel,
    allow_raw_vault_uri: bool,
) -> dict[str, Any]:
    """Project `current_data_model` with live `/progress` state and channel filtering."""
    is_internal = channel == "gemini_app"
    steps_payload: list[dict[str, Any]] = []
    for step in item.steps:
        step_citations = []
        for cit in step.citations:
            href = (
                f"{cit.vault_uri}#page={cit.page_number}"
                if (allow_raw_vault_uri and cit.vault_uri)
                else f"/portal/source-pdf/{item.entity_id}/{cit.doc_id}?page={cit.page_number}&field={cit.field_name}"
            )
            step_citations.append(
                {
                    "docId": cit.doc_id,
                    "documentTitle": cit.document_title,
                    "fieldName": cit.field_name,
                    "pageNumber": cit.page_number,
                    "viewerHref": href,
                }
            )
        steps_payload.append(
            {
                "stepId": step.step_id,
                "stepNumber": step.step_number,
                "label": step.label if is_internal else step.external_label,
                "zone": step.zone,
                "status": step.status,
                "detail": step.detail if is_internal else step.external_detail,
                "updatedAt": step.updated_at.isoformat(),
                "citations": step_citations,
            }
        )

    active_step = next(
        (
            s.step_number
            for s in item.steps
            if s.status in ("working", "input-required", "pending")
        ),
        len(item.steps),
    )
    progress_block = {
        "taskState": item.task_state,
        "percent": _compute_progress_percent(item.steps),
        "currentStep": active_step,
        "totalSteps": len(item.steps),
        "steps": steps_payload,
    }

    base = copy.deepcopy(item.current_data_model)
    base["progress"] = progress_block
    if not is_internal:
        # Strip internal discrepancy tables, internal cost centers, and policy thresholds
        base.pop("discrepancy", None)
        base.pop("internalPolicy", None)
    return base


def build_action_progress_surface(
    item: ActionInboxItem,
    *,
    role: CatalogRole,
    channel: DeliveryChannel,
) -> list[dict[str, Any]]:
    """Build the A2UI v0.9 message sequence (`createSurface`, `updateComponents`,
    `updateDataModel`) for monitoring action progress and resolving paused steps."""
    decision = authorize_access(
        role=role,
        channel=channel,
        target_view=item.target_view,
    )
    if not decision.allowed:
        raise PermissionError(decision.reason)

    is_internal = channel == "gemini_app"
    catalog_id = INBOX_CATALOG_ID if is_internal else PORTAL_CATALOG_ID
    title = item.title if is_internal else item.external_title
    subtitle = item.hint if is_internal else item.external_hint

    components: list[dict[str, Any]] = []
    progress_children: list[str] = []

    # Collect all citations across steps for quick provenance badges
    citation_ids: list[str] = []
    for step in item.steps:
        for idx, cit in enumerate(step.citations):
            pill_id = f"cite_{step.step_id}_{idx}"
            citation_ids.append(pill_id)
            viewer_href = (
                f"{cit.vault_uri}#page={cit.page_number}"
                if (decision.allow_raw_vault_uri and cit.vault_uri)
                else f"/portal/source-pdf/{item.entity_id}/{cit.doc_id}?page={cit.page_number}&field={cit.field_name}"
            )
            pill: dict[str, Any] = {
                "id": pill_id,
                "component": "SourceCitationPill",
                "documentTitle": cit.document_title,
                "docId": cit.doc_id,
                "pageNumber": cit.page_number,
                "viewerHref": viewer_href,
            }
            if cit.excerpt and is_internal:
                pill["excerpt"] = cit.excerpt
            components.append(pill)

    if citation_ids:
        components.append(
            {
                "id": "citations_row",
                "component": "Row",
                "children": citation_ids,
            }
        )
        progress_children.append("citations_row")

    # When paused in `input-required`, mount interactive resolution & governance controls
    if item.task_state == "input-required":
        approval_children: list[str] = []
        discrepancy_rows = (
            item.current_data_model.get("discrepancy", {}).get("rows", [])
            if is_internal and decision.allow_internal_conflict_notes
            else []
        )
        if discrepancy_rows:
            components.append(
                {
                    "id": "conflict_table",
                    "component": "FactConflictTable",
                    "rowsPath": "/discrepancy/rows",
                }
            )
            approval_children.append("conflict_table")

        resolution_options = item.current_data_model.get("resolutionOptions", [])
        if resolution_options:
            components.append(
                {
                    "id": "resolution_picker",
                    "component": "ChoicePicker",
                    "label": "Select Authoritative Value",
                    "variant": "mutuallyExclusive",
                    "options": resolution_options,
                    "value": {"path": "/resolution/selectedValue"},
                }
            )
            approval_children.append("resolution_picker")

        if "transfer" in item.current_data_model and is_internal:
            max_amt = int(
                item.max_approval_amount
                or item.current_data_model["transfer"].get("requestedAmount", 100000)
            )
            components.append(
                {
                    "id": "amount_slider",
                    "component": "Slider",
                    "label": "Approved Amount (USD)",
                    "min": 0,
                    "max": max_amt,
                    "step": 500,
                    "value": {"path": "/transfer/approvedAmount"},
                }
            )
            approval_children.append("amount_slider")

        components.append(
            {
                "id": "reviewer_memo",
                "component": "TextField",
                "label": (
                    "Reviewer Audit Memo / Steering Feedback"
                    if is_internal
                    else "Confirmation Notes"
                ),
                "value": {"path": "/review/memo"},
            }
        )
        approval_children.append("reviewer_memo")

        action_buttons = ["btn_approve", "btn_steer", "btn_reject"]
        components.extend(
            [
                {
                    "id": "btn_approve",
                    "component": "Button",
                    "label": (
                        "Approve & Resume Action"
                        if is_internal
                        else "Confirm & Continue"
                    ),
                    "variant": "primary",
                    "action": {
                        "name": "edit_and_approve",
                        "context": {
                            "decision": "edit",
                            "resolution": {"path": "/resolution"},
                            "transfer": {"path": "/transfer"},
                            "review": {"path": "/review"},
                        },
                    },
                },
                {
                    "id": "btn_steer",
                    "component": "Button",
                    "label": "Respond / Steer Agent",
                    "variant": "secondary",
                    "action": {
                        "name": "respond",
                        "context": {
                            "decision": "respond",
                            "review": {"path": "/review"},
                        },
                    },
                },
                {
                    "id": "btn_reject",
                    "component": "Button",
                    "label": "Reject",
                    "variant": "danger",
                    "action": {
                        "name": "reject",
                        "context": {
                            "decision": "reject",
                            "review": {"path": "/review"},
                        },
                    },
                },
            ]
        )
        if is_internal:
            action_buttons.append("btn_escalate")
            components.append(
                {
                    "id": "btn_escalate",
                    "component": "Button",
                    "label": "Escalate to Senior Underwriter",
                    "variant": "borderless",
                    "action": {
                        "name": "escalate",
                        "context": {
                            "decision": "escalate",
                            "targetRole": "underwriter",
                            "review": {"path": "/review"},
                        },
                    },
                }
            )

        components.append(
            {
                "id": "actions_row",
                "component": "Row",
                "children": action_buttons,
            }
        )
        approval_children.append("actions_row")

        if is_internal:
            components.append(
                {
                    "id": "approval_gate",
                    "component": "ApprovalCard",
                    "title": f"Action Paused for Review: {title}",
                    "subtitle": subtitle,
                    "riskLevel": item.risk_level,
                    "confidenceScore": item.confidence_score,
                    "children": approval_children,
                }
            )
            progress_children.append("approval_gate")
        else:
            progress_children.extend(approval_children)

    root_card: dict[str, Any] = {
        "id": "root",
        "component": "ActionProgressCard",
        "title": title,
        "subtitle": subtitle,
        "taskState": item.task_state,
        "progressPath": "/progress/percent",
        "stepsPath": "/progress/steps",
        "children": progress_children,
    }
    components.insert(0, root_card)

    sanitized_data = _sanitize_data_model_for_channel(
        item,
        channel=channel,
        allow_raw_vault_uri=decision.allow_raw_vault_uri,
    )

    return [
        {
            "version": A2UI_V09_VERSION,
            "createSurface": {
                "surfaceId": item.surface_id,
                "catalogId": catalog_id,
            },
        },
        {
            "version": A2UI_V09_VERSION,
            "updateComponents": {
                "surfaceId": item.surface_id,
                "components": components,
            },
        },
        {
            "version": A2UI_V09_VERSION,
            "updateDataModel": {
                "surfaceId": item.surface_id,
                "path": "/",
                "value": sanitized_data,
            },
        },
    ]


def build_update_data_model_delta(
    surface_id: str,
    *,
    path: str = "/progress",
    value: Any,
) -> dict[str, Any]:
    """Build a lightweight A2UI v0.9 `updateDataModel` delta message for SSE streaming."""
    return {
        "version": A2UI_V09_VERSION,
        "updateDataModel": {
            "surfaceId": surface_id,
            "path": path,
            "value": value,
        },
    }


def build_delete_surface_msg(surface_id: str) -> dict[str, Any]:
    """Build an A2UI v0.9 `deleteSurface` message when an inbox item is dismissed."""
    return {
        "version": A2UI_V09_VERSION,
        "deleteSurface": {
            "surfaceId": surface_id,
        },
    }


def wrap_a2ui_in_a2a_data_part(messages: list[dict[str, Any]]) -> A2aDataPart:
    """Package A2UI v0.9 messages inside an A2A Protocol `DataPart` (`application/a2ui+json`)."""
    return A2aDataPart(
        kind="data",
        mime_type=A2UI_V09_MIME_TYPE,
        data=messages,
    )


# --------------------------------------------------------------------------- #
# 5. ADK `request_confirmation` & `FunctionResponse` Integration              #
# --------------------------------------------------------------------------- #


def request_a2ui_inbox_confirmation(
    tool_context: Any,
    *,
    item: ActionInboxItem,
    role: CatalogRole = "broker",
    channel: DeliveryChannel = "gemini_app",
) -> dict[str, Any]:
    """Pause an ADK tool via `tool_context.request_confirmation`, attaching the A2UI v0.9
    surface messages inside the confirmation payload."""
    item.task_state = "input-required"
    a2ui_messages = build_action_progress_surface(item, role=role, channel=channel)
    payload = {
        "item_id": item.item_id,
        "surface_id": item.surface_id,
        "mime_type": A2UI_V09_MIME_TYPE,
        "a2ui_messages": a2ui_messages,
    }
    if tool_context is not None and hasattr(tool_context, "request_confirmation"):
        tool_context.request_confirmation(
            hint=item.hint,
            payload=payload,
        )
    return {
        "status": "PENDING_INBOX_APPROVAL",
        "item_id": item.item_id,
        "surface_id": item.surface_id,
        "task_state": item.task_state,
    }


def build_adk_confirmation_response(
    item: ActionInboxItem,
    *,
    confirmed: bool,
    payload: dict[str, Any],
) -> types.Content:
    """Build the ADK `types.Content` carrying `FunctionResponse(name="adk_request_confirmation")`
    to resume a paused `Runner.run_async` invocation."""
    call_id = item.confirmation_call_id or f"call_{item.item_id}"
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                    response={
                        "confirmed": confirmed,
                        "payload": payload,
                    },
                )
            )
        ],
    )


# --------------------------------------------------------------------------- #
# 6. Action Progress & Agent Inbox Store                                      #
# --------------------------------------------------------------------------- #


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


class ActionInboxStore:
    """Protocol/base class for tracking A2UI v0.9 Action Progress & Agent Inbox items."""

    def create_action(
        self,
        *,
        entity_id: str,
        context_id: str | None = None,
        session_id: str | None = None,
        invocation_id: str | None = None,
        confirmation_call_id: str | None = None,
        title: str,
        external_title: str | None = None,
        hint: str,
        external_hint: str | None = None,
        assigned_role: CatalogRole = "broker",
        target_view: str = "v_broker_placement",
        risk_level: RiskTier = "MEDIUM",
        confidence_score: float = 0.95,
        steps: list[ActionProgressStep] | None = None,
        data_model: dict[str, Any] | None = None,
        conflicts: list[FactConflict] | None = None,
        pause_for_review: bool = False,
        max_approval_amount: float | None = None,
    ) -> ActionInboxItem:
        """Create a new human-in-the-loop action inbox item and record its initial audit event."""
        raise NotImplementedError

    def get_item(self, item_id: str) -> ActionInboxItem | None:
        """Retrieve an action inbox item by its unique action ID, or None if not found."""
        raise NotImplementedError

    def list_items(
        self,
        *,
        entity_id: str | None = None,
        task_state: TaskLifecycleState | None = None,
    ) -> list[ActionInboxItem]:
        """List action inbox items filtered by status, assigned role, domain, or entity ID."""
        raise NotImplementedError

    def advance_step(
        self,
        item_id: str,
        *,
        step_id: str,
        status: ActionStepStatus,
        detail: str | None = None,
        external_detail: str | None = None,
        hint: str | None = None,
    ) -> tuple[ActionInboxItem, dict[str, Any]]:
        """Advance the workflow step of an active action inbox item and append an audit event."""
        raise NotImplementedError

    def resolve_action(
        self,
        item_id: str,
        *,
        action_request: A2uiClientActionRequest,
        actor_principal: str,
        actor_role: CatalogRole,
        channel: DeliveryChannel,
        agent_spiffe_id: str | None = None,
        principal_max_approval_amount: float | None = None,
    ) -> dict[str, Any]:
        """Resolve an action inbox item with an operator decision, rationale, and signature."""
        raise NotImplementedError

    def list_audit_records(self, item_id: str | None = None) -> list[InboxAuditRecord]:
        """Return immutable audit log records, optionally filtered by action ID."""
        raise NotImplementedError


class InMemoryActionInboxStore(ActionInboxStore):
    """In-memory store for A2UI v0.9 Action Progress & Agent Inbox items and audit logs."""

    def __init__(self) -> None:
        self._items: dict[str, ActionInboxItem] = {}
        self._audits: list[InboxAuditRecord] = []

    def create_action(
        self,
        *,
        entity_id: str,
        context_id: str | None = None,
        session_id: str | None = None,
        invocation_id: str | None = None,
        confirmation_call_id: str | None = None,
        title: str,
        external_title: str | None = None,
        hint: str,
        external_hint: str | None = None,
        assigned_role: CatalogRole = "broker",
        target_view: str = "v_broker_placement",
        risk_level: RiskTier = "MEDIUM",
        confidence_score: float = 0.95,
        steps: list[ActionProgressStep] | None = None,
        data_model: dict[str, Any] | None = None,
        conflicts: list[FactConflict] | None = None,
        pause_for_review: bool = False,
        max_approval_amount: float | None = None,
    ) -> ActionInboxItem:
        """Create a new human-in-the-loop action inbox item in memory and record its audit event."""
        with trace_step(
            "docubridge.a2ui.create_action_item",
            {
                "docubridge.zone": "5_user_interaction_a2ui",
                "docubridge.entity_id": entity_id,
                "docubridge.assigned_role": assigned_role,
                "docubridge.target_view": target_view,
                "docubridge.risk_level": risk_level,
                "docubridge.pause_for_review": pause_for_review,
            },
        ) as span:
            item_id = f"act_{uuid.uuid4().hex[:10]}"
            surface_id = f"surface_{item_id}"
            resolved_steps = (
                copy.deepcopy(steps)
                if steps is not None
                else default_submission_progress_steps()
            )

            initial_model: dict[str, Any] = (
                copy.deepcopy(data_model) if data_model else {}
            )
            initial_model.setdefault("resolution", {"selectedValue": ""})
            initial_model.setdefault("review", {"memo": ""})

            if conflicts:
                rows = []
                options = []
                for c in conflicts:
                    field_short = c.field_path.split(".")[-1]
                    rows.append(
                        {
                            "field": field_short,
                            "sourceA": {
                                "label": f"{c.winning_value} ({c.winning_doc_id})",
                                "docId": c.winning_doc_id,
                                "pageNumber": 1,
                            },
                            "sourceB": {
                                "label": f"{c.conflicting_value} ({c.conflicting_doc_id})",
                                "docId": c.conflicting_doc_id,
                                "pageNumber": 1,
                            },
                        }
                    )
                    options.extend(
                        [
                            {
                                "value": c.winning_value,
                                "label": f"Keep {c.winning_doc_id}: {c.winning_value}",
                            },
                            {
                                "value": c.conflicting_value,
                                "label": f"Use {c.conflicting_doc_id}: {c.conflicting_value}",
                            },
                        ]
                    )
                initial_model["discrepancy"] = {"rows": rows}
                initial_model["resolutionOptions"] = options
                if options and not initial_model["resolution"].get("selectedValue"):
                    initial_model["resolution"]["selectedValue"] = options[0]["value"]

            task_state: TaskLifecycleState = (
                "input-required" if (pause_for_review or bool(conflicts)) else "working"
            )
            if task_state == "input-required":
                for s in resolved_steps:
                    if s.status == "working":
                        s.status = "input-required"
                        break

            item = ActionInboxItem(
                item_id=item_id,
                surface_id=surface_id,
                entity_id=entity_id,
                context_id=context_id or entity_id,
                session_id=session_id or f"sess_{uuid.uuid4().hex[:8]}",
                invocation_id=invocation_id or f"inv_{uuid.uuid4().hex[:8]}",
                confirmation_call_id=confirmation_call_id
                or f"call_{uuid.uuid4().hex[:8]}",
                title=title,
                external_title=external_title or title,
                hint=hint,
                external_hint=external_hint or "Your submission is being processed.",
                task_state=task_state,
                risk_level=risk_level,
                confidence_score=confidence_score,
                assigned_role=assigned_role,
                target_view=target_view,
                steps=resolved_steps,
                initial_data_model=copy.deepcopy(initial_model),
                current_data_model=copy.deepcopy(initial_model),
                max_approval_amount=max_approval_amount,
            )
            # Seed initial progress delta
            initial_delta = build_update_data_model_delta(
                surface_id,
                path="/progress",
                value=_sanitize_data_model_for_channel(
                    item, channel="gemini_app", allow_raw_vault_uri=False
                )["progress"],
            )
            item.delta_history.append(initial_delta)
            self._items[item_id] = item
            span.set_attribute("docubridge.item_id", item_id)
            span.set_attribute("docubridge.surface_id", surface_id)
            span.set_attribute("docubridge.task_state", task_state)
            return item

    def get_item(self, item_id: str) -> ActionInboxItem | None:
        """Retrieve an action inbox item from memory by its unique action ID."""
        return self._items.get(item_id)

    def list_items(
        self,
        *,
        entity_id: str | None = None,
        task_state: TaskLifecycleState | None = None,
    ) -> list[ActionInboxItem]:
        """List in-memory action inbox items filtered by status, assigned role, domain, or entity ID."""
        items = list(self._items.values())
        if entity_id is not None:
            items = [i for i in items if i.entity_id == entity_id]
        if task_state is not None:
            items = [i for i in items if i.task_state == task_state]
        return sorted(items, key=lambda i: i.created_at, reverse=True)

    def advance_step(
        self,
        item_id: str,
        *,
        step_id: str,
        status: ActionStepStatus,
        detail: str | None = None,
        external_detail: str | None = None,
        hint: str | None = None,
    ) -> tuple[ActionInboxItem, dict[str, Any]]:
        """Advance the workflow step of an in-memory action inbox item and append an audit event."""
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"Unknown action inbox item '{item_id}'.")

        matched = False
        for step in item.steps:
            if step.step_id == step_id:
                step.status = status
                step.updated_at = datetime.now(UTC)
                if detail is not None:
                    step.detail = detail
                if external_detail is not None:
                    step.external_detail = external_detail
                matched = True
                break
        if not matched:
            raise KeyError(f"Unknown step_id '{step_id}' on item '{item_id}'.")

        if hint is not None:
            item.hint = hint
        item.updated_at = datetime.now(UTC)

        # Derive top-level task_state from step statuses
        if any(s.status == "failed" for s in item.steps):
            item.task_state = "failed"
        elif any(s.status == "input-required" for s in item.steps):
            item.task_state = "input-required"
        elif all(s.status == "completed" for s in item.steps):
            item.task_state = "completed"
        else:
            item.task_state = "working"

        delta = build_update_data_model_delta(
            item.surface_id,
            path="/progress",
            value=_sanitize_data_model_for_channel(
                item, channel="gemini_app", allow_raw_vault_uri=False
            )["progress"],
        )
        item.delta_history.append(delta)
        return item, delta

    def resolve_action(
        self,
        item_id: str,
        *,
        action_request: A2uiClientActionRequest,
        actor_principal: str,
        actor_role: CatalogRole,
        channel: DeliveryChannel,
        agent_spiffe_id: str | None = None,
        principal_max_approval_amount: float | None = None,
    ) -> dict[str, Any]:
        """Resolve an in-memory action inbox item with an operator decision and signature."""
        with trace_step(
            "docubridge.a2ui.resolve_inbox_action",
            {
                "docubridge.zone": "5_user_interaction_a2ui",
                "docubridge.item_id": item_id,
                "docubridge.actor_principal": actor_principal,
                "docubridge.actor_role": actor_role,
                "docubridge.channel": channel,
                "docubridge.agent_spiffe_id": agent_spiffe_id,
            },
        ) as span:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Unknown action inbox item '{item_id}'.")
            span.set_attribute("docubridge.entity_id", item.entity_id)

            # Verify caller (and optional SPIFFE agent) has access to the item's view & channel
            access = authorize_access(
                role=actor_role,
                channel=channel,
                target_view=item.target_view,
                agent_spiffe_id=agent_spiffe_id,
            )
            if not access.allowed:
                raise PermissionError(access.reason)

            action_obj = action_request.action
            action_name = str(action_obj.get("name", "accept"))
            action_ctx = action_obj.get("context", {}) or {}
            raw_decision = str(action_ctx.get("decision", action_name)).lower()

            if raw_decision in ("approve", "edit_and_approve", "edit"):
                decision: GovernanceDecision = (
                    "edit" if action_name == "edit_and_approve" else "accept"
                )
            elif raw_decision in ("accept", "respond", "reject", "escalate"):
                decision = raw_decision  # type: ignore[assignment]
            else:
                raise ValueError(f"Unsupported governance decision '{raw_decision}'.")

            # Apply client-submitted edits into a candidate data model
            candidate_model = copy.deepcopy(item.current_data_model)
            if isinstance(action_ctx.get("resolution"), dict):
                candidate_model.setdefault("resolution", {}).update(
                    action_ctx["resolution"]
                )
            if isinstance(action_ctx.get("transfer"), dict):
                candidate_model.setdefault("transfer", {}).update(
                    action_ctx["transfer"]
                )
            if isinstance(action_ctx.get("review"), dict):
                candidate_model.setdefault("review", {}).update(action_ctx["review"])

            # Server-side numeric bounds verification (Section 6.1 + Principal Authority)
            effective_max_limit = item.max_approval_amount
            if principal_max_approval_amount is not None:
                effective_max_limit = (
                    min(effective_max_limit, principal_max_approval_amount)
                    if effective_max_limit is not None
                    else principal_max_approval_amount
                )
            if "transfer" in candidate_model and effective_max_limit is not None:
                approved_amt = float(
                    candidate_model["transfer"].get("approvedAmount", 0.0)
                )
                if approved_amt > effective_max_limit:
                    raise ValueError(
                        f"Approved amount ({approved_amt}) exceeds maximum authority limit "
                        f"({effective_max_limit})."
                    )
                if approved_amt < 0:
                    raise ValueError("Approved amount cannot be negative.")

            patches = compute_rfc6902_patches(item.initial_data_model, candidate_model)
            if decision == "edit" and not patches:
                decision = "accept"
            elif decision == "accept" and patches:
                decision = "edit"

            notes = str(candidate_model.get("review", {}).get("memo", ""))
            item.current_data_model = candidate_model
            item.updated_at = datetime.now(UTC)
            span.set_attribute("docubridge.decision", decision)
            span.set_attribute("docubridge.patch_count", len(patches))

            # Handle 5th Enterprise Verb: Escalate (keeps ADK session paused in input-required)
            if decision == "escalate":
                target_role = str(action_ctx.get("targetRole", "underwriter"))
                if target_role in (
                    "broker",
                    "claims",
                    "underwriter",
                    "executive",
                ):
                    item.assigned_role = target_role  # type: ignore[assignment]
                audit = InboxAuditRecord(
                    audit_id=f"aud_{uuid.uuid4().hex[:10]}",
                    item_id=item.item_id,
                    entity_id=item.entity_id,
                    session_id=item.session_id,
                    invocation_id=item.invocation_id,
                    decision="escalate",
                    actor_principal=actor_principal,
                    actor_role=actor_role,
                    agent_spiffe_id=agent_spiffe_id,
                    channel=channel,
                    initial_data_model=item.initial_data_model,
                    resolved_data_model=candidate_model,
                    patches=patches,
                    notes=notes,
                    escalated_to=item.assigned_role,
                )
                self._audits.append(audit)
                span.set_attribute("docubridge.audit_id", audit.audit_id)
                span.set_attribute("docubridge.task_state", "input-required")
                return {
                    "item_id": item.item_id,
                    "task_state": "input-required",
                    "decision": "escalate",
                    "escalated_to": item.assigned_role,
                    "agent_spiffe_id": agent_spiffe_id,
                    "patches": [p.model_dump(mode="json") for p in patches],
                    "audit_id": audit.audit_id,
                }

            # Handle Respond / Steer: keeps workflow active for agent replanning
            if decision == "respond":
                for s in item.steps:
                    if s.status == "input-required":
                        s.status = "working"
                        s.detail = f"Agent replanning with reviewer steering: {notes}"
                item.task_state = "working"
                adk_resume = build_adk_confirmation_response(
                    item,
                    confirmed=False,
                    payload={"decision": "respond", "notes": notes, **action_ctx},
                )
            elif decision == "reject":
                for s in item.steps:
                    if s.status in ("input-required", "working"):
                        s.status = "failed"
                        s.detail = f"Rejected by {actor_role}: {notes or 'Rejected'}"
                item.task_state = "failed"
                adk_resume = build_adk_confirmation_response(
                    item,
                    confirmed=False,
                    payload={"decision": "reject", "notes": notes, **action_ctx},
                )
            else:
                # Accept or Edit & Approve -> complete remaining steps
                for s in item.steps:
                    if s.status in ("input-required", "working", "pending"):
                        s.status = "completed"
                        s.updated_at = datetime.now(UTC)
                item.task_state = "completed"
                adk_resume = build_adk_confirmation_response(
                    item,
                    confirmed=True,
                    payload={
                        "decision": decision,
                        "data": candidate_model,
                        "patches": [p.model_dump(mode="json") for p in patches],
                    },
                )

            delta = build_update_data_model_delta(
                item.surface_id,
                path="/progress",
                value=_sanitize_data_model_for_channel(
                    item,
                    channel=channel,
                    allow_raw_vault_uri=access.allow_raw_vault_uri,
                )["progress"],
            )
            item.delta_history.append(delta)

            audit = InboxAuditRecord(
                audit_id=f"aud_{uuid.uuid4().hex[:10]}",
                item_id=item.item_id,
                entity_id=item.entity_id,
                session_id=item.session_id,
                invocation_id=item.invocation_id,
                decision=decision,
                actor_principal=actor_principal,
                actor_role=actor_role,
                agent_spiffe_id=agent_spiffe_id,
                channel=channel,
                initial_data_model=item.initial_data_model,
                resolved_data_model=candidate_model,
                patches=patches,
                notes=notes,
            )
            self._audits.append(audit)
            span.set_attribute("docubridge.audit_id", audit.audit_id)
            span.set_attribute("docubridge.task_state", item.task_state)

            fn_resp = adk_resume.parts[0].function_response
            return {
                "item_id": item.item_id,
                "task_state": item.task_state,
                "decision": decision,
                "agent_spiffe_id": agent_spiffe_id,
                "patches": [p.model_dump(mode="json") for p in patches],
                "audit_id": audit.audit_id,
                "adk_function_response": {
                    "id": fn_resp.id if fn_resp else item.confirmation_call_id,
                    "name": fn_resp.name
                    if fn_resp
                    else REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                    "response": fn_resp.response if fn_resp else {},
                },
                "progress_delta_msg": delta,
                "resolved_surface_msg": build_delete_surface_msg(item.surface_id)
                if item.task_state in ("completed", "failed")
                else None,
            }

    def list_audit_records(self, item_id: str | None = None) -> list[InboxAuditRecord]:
        """Return immutable audit log records from memory, optionally filtered by action ID."""
        if item_id is None:
            return list(self._audits)
        return [a for a in self._audits if a.item_id == item_id]


_DEFAULT_INBOX_STORE: InMemoryActionInboxStore | None = None


def action_inbox_store_from_env(
    inbox_store: ActionInboxStore | None = None,
) -> ActionInboxStore:
    """Return an explicit `ActionInboxStore` or the process singleton behind an env check."""
    global _DEFAULT_INBOX_STORE
    if inbox_store is not None:
        return inbox_store
    if os.environ.get("BRIDGE_INBOX_STORE_MODE", "memory") == "memory":
        if _DEFAULT_INBOX_STORE is None:
            _DEFAULT_INBOX_STORE = InMemoryActionInboxStore()
        return _DEFAULT_INBOX_STORE
    return InMemoryActionInboxStore()


def format_a2ui_sse_frames(
    item: ActionInboxItem,
    *,
    role: CatalogRole,
    channel: DeliveryChannel,
) -> str:
    """Render an SSE stream containing the initial A2UI v0.9 surface followed by any
    recorded `updateDataModel` progress deltas."""
    initial_messages = build_action_progress_surface(item, role=role, channel=channel)
    frames: list[str] = []
    initial_envelope = {
        "item_id": item.item_id,
        "task_state": item.task_state,
        "a2a_data_part": wrap_a2ui_in_a2a_data_part(initial_messages).model_dump(
            mode="json"
        ),
    }
    frames.append(f"event: a2ui.surface\ndata: {json.dumps(initial_envelope)}\n\n")
    for delta in item.delta_history:
        frames.append(f"event: a2ui.delta\ndata: {json.dumps(delta)}\n\n")
    return "".join(frames)
