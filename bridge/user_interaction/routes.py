"""Zone 5 User Interaction: FastAPI router and standalone ASGI application."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from bridge.a2ui_inbox import (
    ENTERPRISE_INBOX_CATALOG_SCHEMA,
    PORTAL_CUSTOMER_CATALOG_SCHEMA,
    A2uiClientActionRequest,
    ActionInboxStore,
    TaskLifecycleState,
    action_inbox_store_from_env,
    build_action_progress_surface,
    default_submission_progress_steps,
    format_a2ui_sse_frames,
    wrap_a2ui_in_a2a_data_part,
)
from bridge.agent_gym import (
    AgentGymEngine,
    TribalRuleCategory,
    TribalRuleStatus,
    agent_gym_from_env,
    build_a2ui_trust_readiness_surface,
)
from bridge.identity import (
    TenantIsolationError,
    authorize_entity_access,
    authorize_spiffe_agent,
)
from bridge.knowledge_catalog import (
    CatalogRole,
    DeliveryChannel,
    KnowledgeCatalog,
    knowledge_catalog_from_env,
)
from bridge.operational_store import OperationalStore, operational_store_from_env
from bridge.telemetry import (
    OtelAsgiMiddleware,
    clear_recorded_spans,
    compute_sql_trace_evaluation,
    compute_trace_analytics,
    get_recorded_spans,
)
from bridge.user_interaction.auth import (
    _DEFAULT_VIEW_BY_ROLE,
    A2uiDeliveryEnvelope,
    AuthenticationRequiredError,
    resolve_compound_identity,
)
from bridge.user_interaction.demo_seed import seed_demo_scenarios
from bridge.user_interaction.schemas import (
    AdvanceActionStepRequest,
    CompileSkillRequest,
    CreateActionProgressRequest,
    CreateBindingRequest,
    DialTrustPolicyRequest,
    DistillInboxAuditRequest,
    DistillQuarantineFixRequest,
    EvaluateGatewayRouteRequest,
    IngestSubmissionRequest,
    RecordShadowComparisonRequest,
    RegisterServiceRequest,
    RollbackSkillRequest,
)
from bridge.user_interaction.surfaces import (
    ingest_accepted_submission,
    render_domain_agent_a2ui,
)
from contract.enums import Disposition, Gate
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry


def build_user_interaction_router(
    *,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    inbox_store: ActionInboxStore | None = None,
    agent_gym: AgentGymEngine | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> APIRouter:
    """Create a FastAPI router exposing Zone 5 A2UI, A2UI v0.9 Action Progress/Inbox,
    Agent Gym continuous learning, and Zone 6 Knowledge Catalog endpoints."""
    store = operational_store or operational_store_from_env()
    catalog = knowledge_catalog or knowledge_catalog_from_env(operational_store=store)
    if hasattr(catalog, "bind_operational_store"):
        catalog.bind_operational_store(store)
    inbox = action_inbox_store_from_env(inbox_store)
    gym = agent_gym_from_env(agent_gym)
    router = APIRouter()

    @router.post("/v1/intake/ingest")
    def ingest_submission_endpoint(
        request: Request,
        body: IngestSubmissionRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Ingest a document submission across WORM Vault, Operational Store, Knowledge Catalog, and Agent Gym."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role or "underwriter",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            authorize_entity_access(compound.user, body.entity_id)
            rec_at = body.recorded_at or datetime.now(UTC)
            eff_from = body.effective_from or rec_at
            initial_disp = (
                Disposition.pending
                if body.status in ("parked", "pending")
                else Disposition(body.status)
            )
            initial_gate = (
                Gate.hitl_review
                if initial_disp == Disposition.pending
                else Gate.auto_approve
            )
            entry = LedgerEntry(
                doc_id=body.doc_id,
                doctype=body.doctype,
                issuer=body.issuer,
                disposition=initial_disp,
                gate=initial_gate,
                overall_confidence=body.confidence,
                message=body.summary,
                key_fields={k: str(v) for k, v in body.key_fields.items()},
                source_anchors=body.source_anchors,
                recorded_at=rec_at,
                effective_from=eff_from,
            )
            res = ingest_accepted_submission(
                entry,
                entity_id=body.entity_id,
                context_id=body.context_id,
                raw_document_bytes=f"%PDF-1.7 {body.doc_id} {body.doctype}".encode(),
                filename=body.filename or f"{body.doc_id}.pdf",
                version=body.version,
                operational_store=store,
                knowledge_catalog=catalog,
                agent_gym=gym,
                recorded_at=rec_at,
                effective_from=eff_from,
            )
            return {
                "disposition": res.entry.disposition.value,
                "redacted_fields": dict(res.entry.key_fields),
                "applied_extraction_hints": list(res.applied_extraction_hints),
                "entry": res.entry.model_dump(mode="json"),
                "vault_record": res.vault_record.model_dump(mode="json"),
                "facts": [f.model_dump(mode="json") for f in res.facts],
                "conflicts": [c.model_dump(mode="json") for c in res.conflicts],
                "lineage_traces": [
                    t.model_dump(mode="json") for t in res.lineage_traces
                ],
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/bigquery/sync")
    def get_bigquery_iceberg_sync_endpoint(
        entity_id: str | None = None,
        since_recorded_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Export an incremental watermarked BigQuery Open Iceberg CDC sync batch."""
        batch = store.export_bigquery_sync_batch(
            entity_id=entity_id,
            since_recorded_at=since_recorded_at,
        )
        return batch.model_dump(mode="json")

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

    # ------------------------------------------------------------------ #
    # Agent Gym: Continuous Learning ("Example of One") & Trust Dial     #
    # ------------------------------------------------------------------ #

    @router.post("/v1/gym/signals/from-quarantine")
    def distill_from_quarantine_endpoint(
        request: Request,
        body: DistillQuarantineFixRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Distill a candidate Tribal Knowledge Rule from a single Zone 2 Review Quarantine correction."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot submit Agent Gym quarantine rules."
                )
            anchor = (
                SourceAnchor(
                    page_number=body.page_number,
                    bounding_box=body.bounding_box,
                )
                if body.page_number is not None
                else None
            )
            signal, rule = gym.distill_from_quarantine_correction(
                entity_id=body.entity_id,
                doc_id=body.doc_id,
                doctype=body.doctype,
                issuer=body.issuer,
                field_path=body.field_path,
                before_value=body.before_value,
                after_value=body.after_value,
                expert_memo=body.expert_memo,
                actor_principal=compound.user.subject,
                actor_role=compound.user.role,
                agent_spiffe_id=compound.agent_spiffe_id,
                source_anchor=anchor,
                category=body.category,
                distinct_entities=tuple(body.distinct_entities),
                citation_verified=body.citation_verified,
                review_dwell_ms=body.review_dwell_ms,
            )
            return {
                "signal": signal.model_dump(mode="json"),
                "candidate_rule": rule.model_dump(mode="json"),
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/signals/from-inbox/{item_id}")
    def distill_from_inbox_endpoint(
        request: Request,
        item_id: str,
        body: DistillInboxAuditRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Distill a candidate Tribal Knowledge Rule from a Zone 5 A2UI Inbox RFC 6902 patch audit."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot distill Agent Gym rules."
                )
            audits = inbox.list_audit_records(item_id)
            if not audits:
                raise HTTPException(
                    status_code=404,
                    detail=f"No InboxAuditRecord found for action item '{item_id}'.",
                )
            latest_audit = audits[-1]
            signal, rule = gym.distill_from_inbox_audit(
                latest_audit,
                doc_id=body.doc_id,
                doctype=body.doctype,
                issuer=body.issuer,
                field_path=body.field_path,
                category=body.category,
                distinct_entities=tuple(body.distinct_entities),
                citation_verified=body.citation_verified,
                review_dwell_ms=body.review_dwell_ms,
            )
            return {
                "signal": signal.model_dump(mode="json"),
                "candidate_rule": rule.model_dump(mode="json"),
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/rules/{rule_id}/evaluate")
    def evaluate_gym_rule_endpoint(
        request: Request,
        rule_id: str,
        auto_promote: bool = True,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Run the 3-tier Agent Gym evaluation gate on a candidate rule and promote on zero regression."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot evaluate or promote Agent Gym rules."
                )
            report = gym.evaluate_candidate_rule(rule_id, auto_promote=auto_promote)
            rule = next(r for r in gym.list_rules() if r.rule_id == rule_id)
            return {
                "report_card": report.model_dump(mode="json"),
                "rule": rule.model_dump(mode="json"),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/compile-skill")
    def compile_gym_skill_endpoint(
        request: Request,
        body: CompileSkillRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Compile promoted Tribal Knowledge Rules into an immutable versioned SKILL.md revision via gemini-4-argon."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot compile Agent Gym skills."
                )
            return gym.compile_rules_to_skill(
                doctype=body.doctype,
                issuer_pattern=body.issuer_pattern,
            )
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/skills/{skill_name}/rollback")
    def rollback_gym_skill_endpoint(
        skill_name: str,
        request: Request,
        body: RollbackSkillRequest | None = None,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Roll back a doctype skill defaultRevision pointer to its parent or target revision."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot roll back SkillRevisions."
                )
            req = body or RollbackSkillRequest()
            rev = gym.rollback_skill_revision(
                skill_name,
                target_revision_id=req.target_revision_id,
                reason=req.reason,
            )
            return rev.model_dump(mode="json")
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/gym/skills/{skill_name}/revisions")
    def list_gym_skill_revisions_endpoint(skill_name: str) -> dict[str, Any]:
        """List the active defaultRevision and full immutable revision history for a skill."""
        active = gym.get_active_skill_revision(skill_name)
        revs = gym.list_skill_revisions(skill_name)
        return {
            "skill_name": skill_name,
            "active_revision": active.model_dump(mode="json"),
            "revisions": [r.model_dump(mode="json") for r in revs]
            if revs
            else [active.model_dump(mode="json")],
        }

    @router.get("/v1/registry/catalog")
    def get_registry_catalog_endpoint() -> dict[str, Any]:
        """Return the unified Gemini platform Agent Registry, Skill Registry, and Agent Gateway catalog."""
        return gym.get_registry_catalog()

    @router.get("/v1/registry/agents")
    def list_registry_agents_endpoint() -> list[dict[str, Any]]:
        """List all registered agents in the Gemini platform Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["agents"]

    @router.get("/v1/registry/mcp-servers")
    def list_registry_mcp_servers_endpoint() -> list[dict[str, Any]]:
        """List all registered MCP servers and tool specs in the Gemini platform Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["mcp_servers"]

    @router.get("/v1/registry/endpoints")
    def list_registry_endpoints_endpoint() -> list[dict[str, Any]]:
        """List all registered service endpoints in the Gemini platform Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["endpoints"]

    @router.get("/v1/registry/skills")
    def list_registry_skills_endpoint() -> dict[str, Any]:
        """Return the Gemini platform Skill Registry catalog and active skill containers."""
        return gym.get_registry_catalog()["skill_registry"]

    @router.get("/v1/registry/bindings")
    def list_registry_bindings_endpoint() -> list[dict[str, Any]]:
        """List all explicit source-to-target authentication bindings in the Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["bindings"]

    @router.post("/v1/registry/services")
    def register_registry_service_endpoint(
        request: Request,
        body: RegisterServiceRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Register a writable Service resource in Agent Registry projecting onto AGENT, MCP_SERVER, or ENDPOINT."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot register Agent Registry services."
                )
            return gym.register_registry_service(
                service_id=body.service_id,
                display_name=body.display_name,
                component_type=body.component_type,
                spec_type=body.spec_type,
                target_uri=body.target_uri,
                location=body.location,
                agent_principal=body.agent_principal,
                tools=body.tools,
                data_resources=body.data_resources,
            )
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/registry/bindings")
    def create_registry_binding_endpoint(
        request: Request,
        body: CreateBindingRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Create an explicit Agent Registry Binding enforcing hierarchical resource names over URNs."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot create Agent Registry bindings."
                )
            return gym.create_registry_binding(
                binding_id=body.binding_id,
                source_agent=body.source_agent,
                target_resource=body.target_resource,
                target_type=body.target_type,
                auth_provider=body.auth_provider,
                delegation_mode=body.delegation_mode,
                location=body.location,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/gateway/status")
    def get_gateway_status_endpoint() -> dict[str, Any]:
        """Return the Gemini platform Agent Gateway and IAM v3 Unified Access Policy control-plane status."""
        return gym.get_gateway_catalog()

    @router.post("/v1/gateway/evaluate-route")
    def evaluate_gateway_route_endpoint(
        body: EvaluateGatewayRouteRequest,
    ) -> dict[str, Any]:
        """Evaluate a CLIENT_TO_AGENT or AGENT_TO_ANYWHERE route through Model Armor, Cloud DLP, and IAM v3 UAP CEL."""
        return gym.evaluate_gateway_route(
            governed_access_path=body.governed_access_path,
            source_principal=body.source_principal,
            target_resource=body.target_resource,
            protocol=body.protocol,
            mcp_tool_name=body.mcp_tool_name,
            payload_text=body.payload_text,
        )

    @router.get("/v1/registry/lineage/{entity_or_context_id}")
    def get_process_skill_lineage_endpoint(
        entity_or_context_id: str,
    ) -> dict[str, Any]:
        """Return bi-temporal process-instance and consolidated-fact skill revision lineage for an entity or context."""
        by_entity = gym.list_process_skill_lineage(entity_id=entity_or_context_id)
        by_context = gym.list_process_skill_lineage(context_id=entity_or_context_id)
        seen_ids: set[str] = set()
        merged = []
        for rec in [*by_entity, *by_context]:
            if rec.lineage_id not in seen_ids:
                seen_ids.add(rec.lineage_id)
                merged.append(rec)
        merged.sort(key=lambda r: (r.recorded_at, r.document_version))

        # Also include bi-temporal ConsolidatedFact skill lineage for this entity if present
        facts, _ = store.query_as_of(entity_or_context_id)
        fact_skill_lineage = [
            {
                "fact_id": f.fact_id,
                "field_path": f.field_path,
                "value": f.field_value,
                "doc_id": f.doc_id,
                "doctype": f.doctype,
                "document_version": f.version,
                "skill_version": f.skill_version,
                "skill_revision_id": f.skill_revision_id,
                "skill_urn": f.skill_urn,
                "process_skill_revision_id": f.process_skill_revision_id,
                "compiled_rule_ids": list(f.compiled_rule_ids),
                "recorded_at": f.recorded_at.isoformat(),
                "effective_from": f.effective_from.isoformat(),
            }
            for f in facts
        ]
        return {
            "query_id": entity_or_context_id,
            "process_skill_executions": [r.model_dump(mode="json") for r in merged],
            "consolidated_fact_skill_lineage": fact_skill_lineage,
        }

    @router.get("/v1/gym/rules")
    def list_gym_rules_endpoint(
        category: TribalRuleCategory | None = None,
        status: TribalRuleStatus | None = None,
        issuer: str | None = None,
        doctype: str | None = None,
    ) -> list[dict[str, Any]]:
        """List Tribal Knowledge Rules filtered by category, status, issuer, or doctype."""
        return [
            r.model_dump(mode="json")
            for r in gym.list_rules(
                category=category,
                status=status,
                issuer=issuer,
                doctype=doctype,
            )
        ]

    @router.get("/v1/gym/extraction-hints")
    def get_gym_extraction_hints_endpoint(
        issuer: str,
        doctype: str = "quote",
    ) -> list[dict[str, Any]]:
        """Return promoted Stage 1 extraction hint rules for a given issuer and doctype."""
        return [
            r.model_dump(mode="json")
            for r in gym.get_extraction_hints(issuer=issuer, doctype=doctype)
        ]

    @router.get("/v1/gym/trust-policies")
    def list_gym_trust_policies_endpoint() -> list[dict[str, Any]]:
        """List all Programmable Trust slice policies with readiness status and A2UI v0.9 controls."""
        results: list[dict[str, Any]] = []
        for p in gym.list_trust_policies():
            dumped = p.model_dump(mode="json")
            dumped["accuracy_rate"] = p.accuracy_rate
            dumped["ready_for_promotion"] = p.ready_for_promotion
            dumped["recommended_next_level"] = p.recommended_next_level.value
            dumped["a2ui_messages"] = build_a2ui_trust_readiness_surface(p)
            results.append(dumped)
        return results

    @router.post("/v1/gym/trust-policies/{slice_id}/shadow")
    def record_shadow_comparison_endpoint(
        slice_id: str,
        body: RecordShadowComparisonRequest,
    ) -> dict[str, Any]:
        """Record a Shadow Mode AI vs. human comparison and auto-demote autonomy on drift."""
        record, policy = gym.record_shadow_comparison(
            slice_id=slice_id,
            entity_id=body.entity_id,
            doc_id=body.doc_id,
            issuer=body.issuer,
            doctype=body.doctype,
            field_path=body.field_path,
            ai_value=body.ai_value,
            human_value=body.human_value,
            risk_level=body.risk_level,
        )
        return {
            "comparison": record.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "ready_for_promotion": policy.ready_for_promotion,
            "a2ui_messages": build_a2ui_trust_readiness_surface(policy),
        }

    @router.post("/v1/gym/trust-policies/{slice_id}/dial")
    def dial_trust_policy_endpoint(
        request: Request,
        slice_id: str,
        body: DialTrustPolicyRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Promote or dial back a Programmable Trust slice autonomy level."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            policy = gym.dial_trust_policy(
                slice_id,
                target_level=body.target_level,
                actor_principal=compound.user.subject,
                actor_role=compound.user.role,
                reason=body.reason,
                agent_spiffe_id=compound.agent_spiffe_id,
                force=body.force,
            )
            return {
                "policy": policy.model_dump(mode="json"),
                "ready_for_promotion": policy.ready_for_promotion,
                "a2ui_messages": build_a2ui_trust_readiness_surface(policy),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/trust-policies/reset")
    def reset_gym_trust_policies_endpoint(
        clear_rules: bool = False,
        reseed_demo: bool = False,
    ) -> list[dict[str, Any]]:
        """Reset Programmable Trust policies (and optionally clear rules or reseed demo scenarios)."""
        if reseed_demo:
            seed_demo_scenarios(
                operational_store=store,
                knowledge_catalog=catalog,
                agent_gym=gym,
            )
        results: list[dict[str, Any]] = []
        for p in gym.reset_trust_policies(clear_rules=clear_rules):
            dumped = p.model_dump(mode="json")
            dumped["accuracy_rate"] = p.accuracy_rate
            dumped["ready_for_promotion"] = p.ready_for_promotion
            dumped["recommended_next_level"] = p.recommended_next_level.value
            dumped["a2ui_messages"] = build_a2ui_trust_readiness_surface(p)
            results.append(dumped)
        return results

    @router.post("/v1/demo/seed")
    def seed_demo_scenarios_endpoint() -> dict[str, Any]:
        """Seed baseline bi-temporal facts and 6-hop lineage across Banking, Mortgage, and Insurance scenarios."""
        return seed_demo_scenarios(
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
        )

    @router.get("/demo", response_class=HTMLResponse)
    @router.get("/console", response_class=HTMLResponse)
    def get_interactive_enterprise_console() -> HTMLResponse:
        """Serve the 5-scene interactive Enterprise Showcase HTML console (/demo and /console)."""
        demo_path = (
            Path(__file__).resolve().parents[2] / "demo" / "enterprise-demo.html"
        )
        if not demo_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Interactive console (demo/enterprise-demo.html) not found.",
            )
        return HTMLResponse(content=demo_path.read_text(encoding="utf-8"))

    @router.get("/demo/enterprise-demo.css")
    def get_interactive_enterprise_console_css() -> Response:
        """Serve the modular stylesheet for the Enterprise Showcase console."""
        css_path = Path(__file__).resolve().parents[2] / "demo" / "enterprise-demo.css"
        if not css_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Stylesheet (demo/enterprise-demo.css) not found.",
            )
        return Response(
            content=css_path.read_text(encoding="utf-8"),
            media_type="text/css; charset=utf-8",
        )

    @router.get("/demo/enterprise-demo.js")
    def get_interactive_enterprise_console_js() -> Response:
        """Serve the modular JavaScript controller for the Enterprise Showcase console."""
        js_path = Path(__file__).resolve().parents[2] / "demo" / "enterprise-demo.js"
        if not js_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Script (demo/enterprise-demo.js) not found.",
            )
        return Response(
            content=js_path.read_text(encoding="utf-8"),
            media_type="application/javascript; charset=utf-8",
        )

    @router.get("/v1/catalog/spiffe-agents")
    def get_catalog_spiffe_agents(
        security_zone: Literal["internal", "external"] | None = None,
    ) -> list[dict[str, Any]]:
        """List registered SPIFFE agent identities and least-privilege policies from Zone 6 Knowledge Catalog."""
        return [
            p.model_dump(mode="json")
            for p in catalog.list_spiffe_agents(security_zone=security_zone)
        ]

    @router.get("/v1/catalog/assets")
    def get_catalog_assets(
        external_safe_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List governed data assets registered across the 6 architecture zones."""
        return [
            a.model_dump(mode="json")
            for a in catalog.list_assets(external_safe_only=external_safe_only)
        ]

    @router.get("/v1/catalog/glossary")
    def get_catalog_glossary(
        domain: str | None = None,
        include_confidential: bool = True,
    ) -> list[dict[str, Any]]:
        """List business glossary terms and deterministic quality rules from Zone 6 Knowledge Catalog."""
        return [
            t.model_dump(mode="json")
            for t in catalog.list_glossary_terms(
                domain=domain, include_confidential=include_confidential
            )
        ]

    @router.get("/v1/catalog/lineage/{entity_id}/{field_name}")
    def get_field_lineage(
        entity_id: str,
        field_name: str,
    ) -> dict[str, Any]:
        """Trace 6-hop end-to-end field lineage from user view back to the source PDF page and WORM vault."""
        trace = catalog.trace_lineage(entity_id, field_name)
        if trace is None:
            raise HTTPException(
                status_code=404,
                detail=f"No lineage found for {entity_id}.{field_name}",
            )
        return trace.model_dump(mode="json")

    @router.get("/v1/telemetry/spans")
    def list_telemetry_spans_endpoint(
        trace_id: str | None = None,
        name_prefix: str | None = None,
        entity_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List recent OpenTelemetry spans from the in-memory ring buffer."""
        spans = get_recorded_spans(
            trace_id=trace_id,
            name_prefix=name_prefix,
            entity_id=entity_id,
            limit=limit,
        )
        return {
            "count": len(spans),
            "spans": spans,
        }

    @router.post("/v1/telemetry/spans/clear")
    def clear_telemetry_spans_endpoint() -> dict[str, str]:
        """Clear the in-memory OpenTelemetry span ring buffer."""
        clear_recorded_spans()
        return {"status": "cleared"}

    @router.get("/v1/telemetry/analytics")
    def get_telemetry_analytics_endpoint(
        trace_id: str | None = None,
        entity_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Compute p50/p95/p99 step latency analytics, trace waterfall, and multi-model FinOps split."""
        return compute_trace_analytics(
            trace_id=trace_id,
            entity_id=entity_id,
            limit=limit,
        )

    @router.get("/v1/telemetry/sql-eval")
    @router.post("/v1/telemetry/sql-eval/run")
    def run_telemetry_sql_eval_endpoint(
        trace_id: str | None = None,
        entity_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Execute BigQuery-compatible Rule 0 SQL trajectory evaluation, selective AI.GENERATE_TABLE
        judge sampling, and Rogan-Gladen bias correction over recorded OpenTelemetry spans."""
        return compute_sql_trace_evaluation(
            trace_id=trace_id,
            entity_id=entity_id,
            limit=limit,
        )

    return router


def build_user_interaction_app(
    *,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    inbox_store: ActionInboxStore | None = None,
    agent_gym: AgentGymEngine | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> FastAPI:
    """Build a standalone FastAPI application for Zone 5 A2UI and Zone 6 Catalog."""
    app = FastAPI(
        title="DocuBridge Enterprise Data & A2UI Delivery Service",
        version="1.5.0",
    )
    app.add_middleware(OtelAsgiMiddleware, service_component="user_interaction")
    app.include_router(
        build_user_interaction_router(
            operational_store=operational_store,
            knowledge_catalog=knowledge_catalog,
            inbox_store=inbox_store,
            agent_gym=agent_gym,
            principal_roles=principal_roles,
            require_auth=require_auth,
        )
    )
    return app
