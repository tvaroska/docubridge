"""Deterministic A2UI v0.9 surface & delta builders, ADK confirmation wrappers, and SSE framing."""

from __future__ import annotations

import copy
import json
from typing import Any

from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)
from google.genai import types

from bridge.a2ui_inbox.catalogs import (
    A2UI_V09_MIME_TYPE,
    A2UI_V09_VERSION,
    INBOX_CATALOG_ID,
    PORTAL_CATALOG_ID,
)
from bridge.a2ui_inbox.models import (
    A2aDataPart,
    ActionInboxItem,
    ActionProgressStep,
)
from bridge.knowledge_catalog import (
    CatalogRole,
    DeliveryChannel,
    authorize_access,
)


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
