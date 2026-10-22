"""Zone 5 A2UI v0.9 Action Inbox, SSE Progress Stream, and Role-Based Delivery Surface routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from bridge.a2ui_inbox import (
    ENTERPRISE_INBOX_CATALOG_SCHEMA,
    PORTAL_CUSTOMER_CATALOG_SCHEMA,
    A2uiClientActionRequest,
    ActionInboxStore,
    TaskLifecycleState,
    build_action_progress_surface,
    default_submission_progress_steps,
    format_a2ui_sse_frames,
    wrap_a2ui_in_a2a_data_part,
)
from bridge.agent_gym import AgentGymEngine
from bridge.identity import (
    TenantIsolationError,
    authorize_entity_access,
    authorize_spiffe_agent,
)
from bridge.knowledge_catalog import (
    CatalogRole,
    DeliveryChannel,
    KnowledgeCatalog,
)
from bridge.operational_store import OperationalStore
from bridge.user_interaction.auth import (
    _DEFAULT_VIEW_BY_ROLE,
    A2uiDeliveryEnvelope,
    AuthenticationRequiredError,
    resolve_compound_identity,
)
from bridge.user_interaction.schemas import (
    AdvanceActionStepRequest,
    CreateActionProgressRequest,
)
from bridge.user_interaction.surfaces import render_domain_agent_a2ui
from contract.extraction import SourceAnchor


def register_a2ui_routes(
    router: APIRouter,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore,
    gym: AgentGymEngine,
    principal_roles: dict[str, CatalogRole] | None,
    require_auth: bool,
) -> None:
    """Register A2UI v0.9 catalog, Action Inbox, SSE, and role-based surface routes."""

    @router.get("/v1/a2ui/catalogs/{catalog_name}")
    def get_a2ui_catalog_schema(catalog_name: str) -> dict[str, Any]:
        """Return the security-zone-isolated A2UI v0.9 widget catalog schema (enterprise or portal)."""
        if catalog_name in ("enterprise_inbox_catalog.json", "enterprise"):
            return ENTERPRISE_INBOX_CATALOG_SCHEMA
        if catalog_name in ("portal_customer_catalog.json", "portal"):
            return PORTAL_CUSTOMER_CATALOG_SCHEMA
        raise HTTPException(
            status_code=404, detail=f"Unknown A2UI catalog '{catalog_name}'."
        )

    @router.post("/v1/a2ui/inbox/actions")
    def create_tracked_action(
        request: Request,
        body: CreateActionProgressRequest,
        channel: DeliveryChannel = "gemini_app",
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Create a tracked A2UI v0.9 action item in the Action Inbox with step-level progress."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role or body.assigned_role,
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            resolved_role = compound.user.role
            authorize_entity_access(compound.user, body.entity_id)
            if compound.agent_spiffe_id is not None:
                authorize_spiffe_agent(
                    compound.agent_spiffe_id,
                    channel=channel,
                    role=resolved_role,
                    target_view=body.target_view,
                )
            facts, conflicts = store.query_as_of(body.entity_id)
            anchors = {}
            for f in facts:
                short_name = f.field_path.split(".")[-1]
                tr = f.trace()
                anchors[short_name] = SourceAnchor(
                    page_number=tr.page_number,
                    bounding_box=tr.bounding_box,
                    snippet=tr.snippet,
                    artifact_uri=tr.vault_uri,
                )
            steps = default_submission_progress_steps(
                doc_id=facts[0].doc_id if facts else "doc-submission",
                document_title=facts[0].doctype if facts else "Submission",
                anchors=anchors,
            )
            item = inbox.create_action(
                entity_id=body.entity_id,
                title=body.title,
                external_title=body.external_title,
                hint=body.hint,
                external_hint=body.external_hint,
                assigned_role=body.assigned_role,
                target_view=body.target_view,
                risk_level=body.risk_level,
                confidence_score=body.confidence_score,
                steps=steps,
                data_model=body.data_model,
                conflicts=list(conflicts),
                pause_for_review=body.pause_for_review,
                max_approval_amount=body.max_approval_amount,
            )
            messages = build_action_progress_surface(
                item, role=resolved_role, channel=channel
            )
            return {
                "item_id": item.item_id,
                "surface_id": item.surface_id,
                "entity_id": item.entity_id,
                "task_state": item.task_state,
                "session_id": item.session_id,
                "invocation_id": item.invocation_id,
                "confirmation_call_id": item.confirmation_call_id,
                "agent_spiffe_id": compound.agent_spiffe_id,
                "hint": item.hint if channel == "gemini_app" else item.external_hint,
                "a2a_data_part": wrap_a2ui_in_a2a_data_part(messages).model_dump(
                    mode="json"
                ),
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/a2ui/inbox/items")
    def list_inbox_action_items(
        request: Request,
        channel: DeliveryChannel = "gemini_app",
        role: CatalogRole | None = None,
        entity_id: str | None = None,
        task_state: TaskLifecycleState | None = None,
    ) -> list[dict[str, Any]]:
        """List A2UI v0.9 Action Inbox items visible to the caller principal and security zone."""
        try:
            default_role: CatalogRole = (
                "broker" if channel == "gemini_app" else "client"
            )
            compound = resolve_compound_identity(
                request,
                query_role=role or default_role,
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            resolved_role = compound.user.role
            if entity_id is not None:
                authorize_entity_access(compound.user, entity_id)
            if compound.agent_spiffe_id is not None:
                authorize_spiffe_agent(
                    compound.agent_spiffe_id,
                    channel=channel,
                    role=resolved_role,
                    target_view=_DEFAULT_VIEW_BY_ROLE[resolved_role],
                )
            items = inbox.list_items(entity_id=entity_id, task_state=task_state)
            results: list[dict[str, Any]] = []
            for item in items:
                try:
                    authorize_entity_access(compound.user, item.entity_id)
                except TenantIsolationError:
                    continue
                messages = build_action_progress_surface(
                    item, role=resolved_role, channel=channel
                )
                results.append(
                    {
                        "item_id": item.item_id,
                        "surface_id": item.surface_id,
                        "entity_id": item.entity_id,
                        "task_state": item.task_state,
                        "session_id": item.session_id,
                        "hint": (
                            item.hint if channel == "gemini_app" else item.external_hint
                        ),
                        "a2a_data_part": wrap_a2ui_in_a2a_data_part(
                            messages
                        ).model_dump(mode="json"),
                    }
                )
            return results
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/a2ui/inbox/items/{item_id}")
    def get_inbox_action_item(
        request: Request,
        item_id: str,
        channel: DeliveryChannel = "gemini_app",
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Fetch a single A2UI v0.9 Action Inbox item with its RFC 6902 audit trail."""
        item = inbox.get_item(item_id)
        if item is None:
            raise HTTPException(
                status_code=404, detail=f"Action item '{item_id}' not found."
            )
        try:
            default_role: CatalogRole = (
                "broker" if channel == "gemini_app" else "client"
            )
            compound = resolve_compound_identity(
                request,
                query_role=role or default_role,
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            resolved_role = compound.user.role
            authorize_entity_access(compound.user, item.entity_id)
            if compound.agent_spiffe_id is not None:
                authorize_spiffe_agent(
                    compound.agent_spiffe_id,
                    channel=channel,
                    role=resolved_role,
                    target_view=item.target_view,
                )
            messages = build_action_progress_surface(
                item, role=resolved_role, channel=channel
            )
            audits = (
                [a.model_dump(mode="json") for a in inbox.list_audit_records(item_id)]
                if channel == "gemini_app"
                else []
            )
            return {
                "item_id": item.item_id,
                "surface_id": item.surface_id,
                "entity_id": item.entity_id,
                "task_state": item.task_state,
                "session_id": item.session_id,
                "assigned_role": item.assigned_role,
                "hint": (item.hint if channel == "gemini_app" else item.external_hint),
                "a2a_data_part": wrap_a2ui_in_a2a_data_part(messages).model_dump(
                    mode="json"
                ),
                "audit_trail": audits,
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/a2ui/inbox/items/{item_id}/progress")
    def update_inbox_action_step(
        item_id: str,
        body: AdvanceActionStepRequest,
    ) -> dict[str, Any]:
        """Advance a single milestone step on an active Action Inbox item and emit an updateDataModel delta."""
        try:
            item, delta = inbox.advance_step(
                item_id,
                step_id=body.step_id,
                status=body.status,
                detail=body.detail,
                external_detail=body.external_detail,
                hint=body.hint,
            )
            return {
                "item_id": item.item_id,
                "surface_id": item.surface_id,
                "task_state": item.task_state,
                "delta": delta,
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/v1/a2ui/inbox/items/{item_id}/events")
    def stream_inbox_action_events(
        request: Request,
        item_id: str,
        channel: DeliveryChannel = "gemini_app",
        role: CatalogRole | None = None,
    ) -> PlainTextResponse:
        """Stream A2UI v0.9 Server-Sent Events (SSE) progress frames for an Action Inbox item."""
        item = inbox.get_item(item_id)
        if item is None:
            raise HTTPException(
                status_code=404, detail=f"Action item '{item_id}' not found."
            )
        try:
            default_role: CatalogRole = (
                "broker" if channel == "gemini_app" else "client"
            )
            compound = resolve_compound_identity(
                request,
                query_role=role or default_role,
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            resolved_role = compound.user.role
            authorize_entity_access(compound.user, item.entity_id)
            if compound.agent_spiffe_id is not None:
                authorize_spiffe_agent(
                    compound.agent_spiffe_id,
                    channel=channel,
                    role=resolved_role,
                    target_view=item.target_view,
                )
            sse_payload = format_a2ui_sse_frames(
                item, role=resolved_role, channel=channel
            )
            return PlainTextResponse(
                content=sse_payload,
                media_type="text/event-stream",
            )
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/a2ui/inbox/items/{item_id}/action")
    def submit_inbox_governance_action(
        request: Request,
        item_id: str,
        body: A2uiClientActionRequest,
        channel: DeliveryChannel = "gemini_app",
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Submit a 4+1 human governance verb (accept, edit_and_approve, respond, reject, escalate) on an inbox item."""
        try:
            default_role: CatalogRole = (
                "broker" if channel == "gemini_app" else "client"
            )
            compound = resolve_compound_identity(
                request,
                query_role=role or default_role,
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            resolved_role = compound.user.role
            item = inbox.get_item(item_id)
            if item is not None:
                authorize_entity_access(compound.user, item.entity_id)
            principal_max = (
                float(compound.user.max_approval_amount)
                if compound.user.max_approval_amount is not None
                else None
            )
            return inbox.resolve_action(
                item_id,
                action_request=body,
                actor_principal=compound.user.subject,
                actor_role=resolved_role,
                channel=channel,
                agent_spiffe_id=compound.agent_spiffe_id,
                principal_max_approval_amount=principal_max,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get(
        "/v1/a2ui/{channel}/{entity_id}",
        response_model=A2uiDeliveryEnvelope,
    )
    def get_a2ui_surface(
        request: Request,
        channel: DeliveryChannel,
        entity_id: str,
        role: CatalogRole | None = None,
        view: str | None = None,
        as_of_learned: datetime | None = None,
        effective_on: datetime | None = None,
    ) -> A2uiDeliveryEnvelope:
        """Render a governed role-based A2UI delivery envelope for Gemini App or Custom Web."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            return render_domain_agent_a2ui(
                entity_id,
                role=compound.user.role,
                channel=channel,
                target_view=view,
                as_of_learned=as_of_learned,
                effective_on=effective_on,
                operational_store=store,
                knowledge_catalog=catalog,
                agent_gym=gym,
                principal=compound.user,
                agent_spiffe_id=compound.agent_spiffe_id,
                svid_verified=compound.svid_verified,
            )
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
