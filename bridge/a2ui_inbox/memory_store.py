"""ActionInboxStore protocol and InMemoryActionInboxStore implementation."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)

from bridge.a2ui_inbox.catalogs import (
    ActionStepStatus,
    GovernanceDecision,
    RiskTier,
    TaskLifecycleState,
)
from bridge.a2ui_inbox.models import (
    A2uiClientActionRequest,
    ActionInboxItem,
    ActionProgressStep,
    InboxAuditRecord,
    compute_rfc6902_patches,
    default_submission_progress_steps,
)
from bridge.a2ui_inbox.service import (
    _sanitize_data_model_for_channel,
    build_adk_confirmation_response,
    build_delete_surface_msg,
    build_update_data_model_delta,
)
from bridge.knowledge_catalog import (
    CatalogRole,
    DeliveryChannel,
    authorize_access,
)
from bridge.operational_store import FactConflict
from bridge.telemetry import (
    close_outcome,
    get_current_outcome_id,
    get_current_outcome_owner,
    resume_outcome,
    start_outcome,
    trace_step,
)


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
        blind_pre_commitment: bool = False,
        outcome_id: str | None = None,
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
        close_outcome_on_resolve: bool = True,
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
        blind_pre_commitment: bool = False,
        outcome_id: str | None = None,
    ) -> ActionInboxItem:
        """Create a new human-in-the-loop action inbox item in memory and record its audit event."""
        resolved_outcome_id = outcome_id or get_current_outcome_id() or start_outcome()
        with trace_step(
            "docubridge.a2ui.create_action_item",
            {
                "docubridge.zone": "5_user_interaction_a2ui",
                "docubridge.entity_id": entity_id,
                "docubridge.assigned_role": assigned_role,
                "docubridge.target_view": target_view,
                "docubridge.risk_level": risk_level,
                "docubridge.pause_for_review": pause_for_review,
                "docubridge.blind_pre_commitment": blind_pre_commitment,
                "outcome.id": resolved_outcome_id,
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
                if (
                    options
                    and not blind_pre_commitment
                    and not initial_model["resolution"].get("selectedValue")
                ):
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
                outcome_id=resolved_outcome_id,
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
                blind_pre_commitment=blind_pre_commitment,
            )
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
        close_outcome_on_resolve: bool = True,
    ) -> dict[str, Any]:
        """Resolve an in-memory action inbox item with an operator decision and signature."""
        existing_item = self._items.get(item_id)
        if existing_item is not None and existing_item.outcome_id:
            resume_outcome(
                existing_item.outcome_id,
                owner=get_current_outcome_owner(),
            )
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
            review_block = candidate_model.get("review", {}) or {}
            raw_dwell = action_ctx.get(
                "review_dwell_ms",
                action_ctx.get(
                    "reviewDwellMs",
                    review_block.get(
                        "review_dwell_ms", review_block.get("dwellMs", 4200)
                    ),
                ),
            )
            review_dwell_ms = max(0, int(raw_dwell if raw_dwell is not None else 4200))
            raw_citation = action_ctx.get(
                "citation_opened",
                action_ctx.get(
                    "citationOpened",
                    action_ctx.get(
                        "citation_verified",
                        review_block.get("citation_opened", True),
                    ),
                ),
            )
            citation_opened = bool(True if raw_citation is None else raw_citation)
            raw_pre_commit = action_ctx.get(
                "pre_committed",
                action_ctx.get(
                    "preCommitted",
                    review_block.get("pre_committed", item.blind_pre_commitment),
                ),
            )
            pre_committed = bool(raw_pre_commit)

            item.current_data_model = candidate_model
            item.updated_at = datetime.now(UTC)
            span.set_attribute("docubridge.decision", decision)
            span.set_attribute("docubridge.patch_count", len(patches))
            span.set_attribute("docubridge.review_dwell_ms", review_dwell_ms)
            span.set_attribute("docubridge.citation_opened", citation_opened)
            span.set_attribute("docubridge.pre_committed", pre_committed)

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
                    review_dwell_ms=review_dwell_ms,
                    citation_opened=citation_opened,
                    pre_committed=pre_committed,
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
                review_dwell_ms=review_dwell_ms,
                citation_opened=citation_opened,
                pre_committed=pre_committed,
            )
            self._audits.append(audit)
            span.set_attribute("docubridge.audit_id", audit.audit_id)
            span.set_attribute("docubridge.task_state", item.task_state)
            if close_outcome_on_resolve and item.outcome_id:
                if decision in ("accept", "edit_and_approve"):
                    close_outcome(
                        "succeeded",
                        subject=item.entity_id,
                        outcome_id=item.outcome_id,
                    )
                elif decision == "reject":
                    close_outcome(
                        "failed",
                        subject=item.entity_id,
                        outcome_id=item.outcome_id,
                    )
                elif decision == "escalate":
                    close_outcome(
                        "escalated",
                        subject=item.entity_id,
                        outcome_id=item.outcome_id,
                    )

            fn_resp = adk_resume.parts[0].function_response
            return {
                "item_id": item.item_id,
                "outcome_id": item.outcome_id,
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
