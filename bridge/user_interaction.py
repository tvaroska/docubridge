"""Zone 5 — User Interaction Layer: Domain Agents & A2UI Delivery.

Connects Zone 4 (Role-Based Business Views & BigQuery Analytics) and Zone 6
(Knowledge Catalog governance & lineage) to end users via declarative **A2UI**
(`Agent-to-UI`) components streamed to two isolated delivery channels:

1. **Gemini App (Internal)** (`channel="gemini_app"`):
   Conversational workspace for internal Aon `broker`, `claims`,
   `underwriter`, and `executive` roles with interactive charts, side-by-side
   comparisons, cross-document conflict alerts, bi-temporal time-travel, and
   direct source PDF page highlights (`Click value -> source PDF`).
2. **Custom Web (External)** (`channel="custom_web"`):
   Branded external web portal for `client` and `carrier` users powered by the
   same governed views and A2UI components, with strict security isolation
   (internal conflict flags, confidential glossary rules, and raw vault URIs
   are automatically stripped).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from bridge.a2ui_inbox import (
    A2UI_V09_MIME_TYPE,
    A2UI_V09_VERSION,
    ENTERPRISE_INBOX_CATALOG_SCHEMA,
    INBOX_CATALOG_ID,
    PORTAL_CATALOG_ID,
    PORTAL_CUSTOMER_CATALOG_SCHEMA,
    A2aDataPart,
    A2uiClientActionRequest,
    ActionCitation,
    ActionInboxItem,
    ActionInboxStore,
    ActionProgressStep,
    ActionStepStatus,
    GovernanceDecision,
    InboxAuditRecord,
    InMemoryActionInboxStore,
    Rfc6902PatchOp,
    RiskTier,
    TaskLifecycleState,
    action_inbox_store_from_env,
    build_action_progress_surface,
    build_adk_confirmation_response,
    build_delete_surface_msg,
    build_update_data_model_delta,
    compute_rfc6902_patches,
    default_submission_progress_steps,
    format_a2ui_sse_frames,
    request_a2ui_inbox_confirmation,
    wrap_a2ui_in_a2a_data_part,
)
from bridge.agent_gym import (
    AgentGymEngine,
    TribalRuleCategory,
    TribalRuleStatus,
    TrustAutonomyLevel,
    agent_gym_from_env,
    build_a2ui_trust_readiness_surface,
)
from bridge.identity import (
    ALLOWED_ENTITIES_HEADER,
    CARRIER_SLUG_HEADER,
    MAX_APPROVAL_HEADER,
    SPIFFE_ID_HEADER,
    SPIFFE_REQUIRE_SVID_ENV,
    SPIFFE_SVID_HEADER,
    TENANT_ID_HEADER,
    CompoundIdentity,
    SpiffeAuthenticationError,
    SpiffeId,
    TenantIsolationError,
    UserPrincipal,
    authorize_entity_access,
    authorize_spiffe_agent,
    filter_quotes_for_principal,
    parse_decimal_header,
    verify_jwt_svid,
)
from bridge.knowledge_catalog import (
    AccessDecision,
    CatalogRole,
    DeliveryChannel,
    KnowledgeCatalog,
    LineageTrace,
    knowledge_catalog_from_env,
)
from bridge.operational_store import (
    BrokerView,
    ClaimsView,
    ConsolidatedFact,
    FactConflict,
    OperationalStore,
    SourceTrace,
    UnderwriterView,
    operational_store_from_env,
)
from bridge.vault import (
    AuditVaultStore,
    VaultRecord,
    promote_accepted_to_vault,
    vault_from_env,
)
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry


class A2uiSourceLink(BaseModel):
    """One-click citation link from an A2UI value back to the source PDF page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    doc_id: str
    field_name: str
    doctype: str
    page_number: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float] | None = None
    snippet: str | None = None
    citation_label: str
    viewer_href: str


class A2uiWidget(BaseModel):
    """Declarative A2UI visual component rendered in Gemini App or Custom Web."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    widget_id: str
    widget_type: Literal[
        "metric_card",
        "comparison_table",
        "conflict_alert",
        "time_travel_timeline",
        "source_pdf_citation",
    ]
    title: str
    subtitle: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    source_links: list[A2uiSourceLink] = Field(default_factory=list)


class A2uiDeliveryEnvelope(BaseModel):
    """Governed A2UI payload delivered to Gemini App (internal) or Custom Web (external)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["a2ui/1.0"] = "a2ui/1.0"
    channel: DeliveryChannel
    role: CatalogRole
    target_view: str
    entity_id: str
    as_of_learned: datetime | None = None
    effective_on: datetime | None = None
    governance: AccessDecision
    summary_headline: str
    widgets: list[A2uiWidget]
    lineage_count: int = 0


_DEFAULT_VIEW_BY_ROLE: dict[CatalogRole, str] = {
    "broker": "v_broker_placement",
    "claims": "v_claims_coverage",
    "underwriter": "v_underwriter_exposure",
    "executive": "v_broker_placement",
    "client": "v_broker_placement",
    "carrier": "v_broker_placement",
}

A2UI_CONTENT_TYPE = "application/vnd.a2ui+json"
A2UI_REQUIRE_AUTH_ENV = "BRIDGE_A2UI_REQUIRE_AUTH"
_VALID_ROLES: frozenset[str] = frozenset(_DEFAULT_VIEW_BY_ROLE.keys())


class AuthenticationRequiredError(PermissionError):
    """Raised when require_auth is enabled and no authenticated principal is present (HTTP 401)."""


def encode_a2ui_envelope(envelope: A2uiDeliveryEnvelope) -> dict[str, Any]:
    """Serialize an ``A2uiDeliveryEnvelope`` for A2UI transport (JSON-mode dump)."""
    return envelope.model_dump(mode="json")


def resolve_caller_role(
    request: Request,
    *,
    query_role: CatalogRole | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> CatalogRole:
    """Resolve and verify the caller's ``CatalogRole`` from authenticated identity.

    1. When an authenticated principal is present (via ``request.scope["user"]``,
       Cloud Run IAP ``X-Goog-Authenticated-User-Email``, ``X-Bridge-Principal``, or
       ``X-Bridge-Role``), its bound role is authoritative; a conflicting ``query_role``
       is rejected with ``PermissionError`` (HTTP 403).
    2. When ``require_auth`` (or ``BRIDGE_A2UI_REQUIRE_AUTH=1``) is enabled, any
       unauthenticated request is rejected with ``AuthenticationRequiredError`` (HTTP 401).
    3. In local/dev mode (``require_auth=False``), falls back to ``query_role``.
    """
    enforce_auth = require_auth or (
        os.environ.get(A2UI_REQUIRE_AUTH_ENV, "").strip() == "1"
    )
    user = request.scope.get("user")
    is_scope_authed = bool(
        user is not None and getattr(user, "is_authenticated", False)
    )
    iap_email_raw = request.headers.get("x-goog-authenticated-user-email", "").strip()
    iap_email = (
        iap_email_raw.split(":", 1)[1].strip()
        if ":" in iap_email_raw
        else iap_email_raw
    )
    header_principal = request.headers.get("x-bridge-principal", "").strip()
    header_role = request.headers.get("x-bridge-role", "").strip().lower()

    principal_name = (
        (getattr(user, "display_name", None) or getattr(user, "username", None) or "")
        if is_scope_authed
        else (iap_email or header_principal)
    )
    has_auth_identity = bool(
        is_scope_authed or iap_email or header_principal or header_role
    )

    verified_role: CatalogRole | None = None
    if has_auth_identity:
        if principal_roles:
            if principal_name in principal_roles:
                verified_role = principal_roles[principal_name]
            elif iap_email_raw and iap_email_raw in principal_roles:
                verified_role = principal_roles[iap_email_raw]
        if verified_role is None and is_scope_authed and hasattr(user, "role") and user.role in _VALID_ROLES:
            verified_role = cast(CatalogRole, user.role)
        if verified_role is None and header_role in _VALID_ROLES:
            verified_role = cast(CatalogRole, header_role)
        if verified_role is None and principal_name.startswith("role:"):
            candidate = principal_name.split(":", 1)[1].strip()
            if candidate in _VALID_ROLES:
                verified_role = cast(CatalogRole, candidate)
        if verified_role is None and iap_email:
            lowered = iap_email.lower()
            for r in ("underwriter", "claims", "broker", "carrier", "client"):
                if r in lowered:
                    verified_role = cast(CatalogRole, r)
                    break
            if verified_role is None and lowered.endswith("@aon.com"):
                verified_role = "broker"

    if verified_role is not None:
        if query_role is not None and query_role != verified_role:
            raise PermissionError(
                f"Authenticated principal '{principal_name or verified_role}' has role "
                f"'{verified_role}', cannot impersonate role '{query_role}'."
            )
        return verified_role

    if enforce_auth:
        raise AuthenticationRequiredError(
            "Authenticated principal with a mapped CatalogRole is required "
            "(BRIDGE_A2UI_REQUIRE_AUTH=1)."
        )

    if query_role is not None:
        return query_role

    raise PermissionError(
        "Missing caller role: supply an authenticated principal, X-Bridge-Role header, or role parameter."
    )


def resolve_compound_identity(
    request: Request,
    *,
    query_role: CatalogRole | None = None,
    default_role: CatalogRole | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    require_svid: bool = False,
) -> CompoundIdentity:
    """Resolve and verify both the Human Principal (`sub`) and SPIFFE Agent (`act`)."""
    enforce_svid = require_svid or (
        os.environ.get(SPIFFE_REQUIRE_SVID_ENV, "").strip() == "1"
    )
    svid_header = request.headers.get(SPIFFE_SVID_HEADER, "").strip()
    spiffe_id_header = request.headers.get(SPIFFE_ID_HEADER, "").strip()

    agent_spiffe_id: str | None = None
    svid_verified = False
    obo_claims: dict[str, Any] = {}
    audience: str | None = None

    if svid_header:
        try:
            claims = verify_jwt_svid(svid_header)
        except SpiffeAuthenticationError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc
        agent_spiffe_id = str(claims["sub"])
        svid_verified = True
        aud_claim = claims.get("aud")
        audience = (
            aud_claim[0]
            if isinstance(aud_claim, list) and aud_claim
            else (str(aud_claim) if aud_claim else None)
        )
        if isinstance(claims.get("obo"), dict):
            obo_claims = dict(claims["obo"])
    elif spiffe_id_header:
        if enforce_svid:
            raise AuthenticationRequiredError(
                "Cryptographically signed SPIFFE JWT-SVID header 'X-Bridge-Spiffe-Svid' "
                "is required (BRIDGE_SPIFFE_REQUIRE_SVID=1)."
            )
        try:
            parsed = SpiffeId.parse(spiffe_id_header)
        except SpiffeAuthenticationError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc
        agent_spiffe_id = parsed.uri
    elif enforce_svid:
        raise AuthenticationRequiredError(
            "Missing SPIFFE JWT-SVID header 'X-Bridge-Spiffe-Svid' "
            "(BRIDGE_SPIFFE_REQUIRE_SVID=1)."
        )

    obo_role_raw = obo_claims.get("role")
    obo_role: CatalogRole | None = (
        cast(CatalogRole, obo_role_raw)
        if isinstance(obo_role_raw, str) and obo_role_raw in _VALID_ROLES
        else None
    )
    if obo_role is not None and query_role is not None and query_role != obo_role:
        raise PermissionError(
            f"OBO principal in SPIFFE SVID has role '{obo_role}', cannot impersonate "
            f"role '{query_role}'."
        )

    resolved_role = (
        obo_role
        if obo_role is not None
        else resolve_caller_role(
            request,
            query_role=query_role or default_role,
            principal_roles=principal_roles,
            require_auth=require_auth,
        )
    )

    iap_email = request.headers.get("x-goog-authenticated-user-email", "").strip()
    header_principal = request.headers.get("x-bridge-principal", "").strip()
    subject = (
        str(obo_claims.get("sub") or "")
        or iap_email
        or header_principal
        or f"role:{resolved_role}"
    )

    tenant_id = (
        str(obo_claims.get("tenant_id") or "")
        or request.headers.get(TENANT_ID_HEADER, "").strip()
        or None
    )
    raw_entities_header = request.headers.get(ALLOWED_ENTITIES_HEADER, "").strip()
    header_entities = tuple(
        e.strip() for e in raw_entities_header.split(",") if e.strip()
    )
    obo_entities = (
        tuple(str(e) for e in obo_claims["allowed_entities"])
        if isinstance(obo_claims.get("allowed_entities"), list)
        else ()
    )
    allowed_entities = obo_entities or header_entities

    carrier_slug = (
        str(obo_claims.get("carrier_slug") or "")
        or request.headers.get(CARRIER_SLUG_HEADER, "").strip()
        or None
    )
    max_approval_raw = (
        str(obo_claims["max_approval_amount"])
        if "max_approval_amount" in obo_claims
        else request.headers.get(MAX_APPROVAL_HEADER, "").strip()
    )
    try:
        max_approval = parse_decimal_header(max_approval_raw)
    except SpiffeAuthenticationError as exc:
        raise AuthenticationRequiredError(str(exc)) from exc

    via: Literal["jwt", "iap", "header", "query_dev"] = (
        "jwt"
        if svid_verified and obo_claims
        else ("iap" if iap_email else ("header" if header_principal else "query_dev"))
    )
    user_principal = UserPrincipal(
        subject=subject,
        role=resolved_role,
        tenant_id=tenant_id,
        allowed_entities=allowed_entities,
        carrier_slug=carrier_slug,
        max_approval_amount=max_approval,
        authenticated_via=via,
    )
    return CompoundIdentity(
        user=user_principal,
        agent_spiffe_id=agent_spiffe_id,
        svid_verified=svid_verified,
        audience=audience,
    )


def _to_a2ui_source_link(
    entity_id: str,
    field_name: str,
    trace: SourceTrace,
    *,
    allow_raw_vault_uri: bool,
) -> A2uiSourceLink:
    """Build a channel-appropriate click-to-PDF citation link."""
    if allow_raw_vault_uri and trace.vault_uri:
        viewer_href = f"{trace.vault_uri}#page={trace.page_number}"
    else:
        viewer_href = (
            f"/portal/source-pdf/{entity_id}/{trace.doc_id}"
            f"?page={trace.page_number}&field={field_name}"
        )
    return A2uiSourceLink(
        entity_id=entity_id,
        doc_id=trace.doc_id,
        field_name=field_name,
        doctype=trace.doctype,
        page_number=trace.page_number,
        bounding_box=trace.bounding_box,
        snippet=trace.snippet,
        citation_label=f"Click value -> source PDF (p. {trace.page_number})",
        viewer_href=viewer_href,
    )


def _build_broker_widgets(
    view: BrokerView,
    *,
    allow_raw_vault_uri: bool,
    allow_competitor_quotes: bool,
    principal: UserPrincipal | None = None,
) -> list[A2uiWidget]:
    all_links: list[A2uiSourceLink] = []
    quotes_rows: list[dict[str, Any]] = []
    if principal is not None:
        visible_quotes = filter_quotes_for_principal(
            view.quotes,
            principal,
            allow_competitor_quotes=allow_competitor_quotes,
        )
    else:
        visible_quotes = (
            list(view.quotes) if allow_competitor_quotes else list(view.quotes[:1])
        )
    for q in visible_quotes:
        for fname, tr in q.traces.items():
            all_links.append(
                _to_a2ui_source_link(
                    view.entity_id,
                    fname,
                    tr,
                    allow_raw_vault_uri=allow_raw_vault_uri,
                )
            )
        quotes_rows.append(
            {
                "issuer": q.issuer,
                "doc_id": q.doc_id,
                "doctype": q.doctype,
                "premium": q.premium,
                "deductible": q.deductible,
                "coverage_limits": q.coverage_limits,
                "exclusions": q.exclusions,
            }
        )

    return [
        A2uiWidget(
            widget_id=f"metric:broker:{view.entity_id}",
            widget_type="metric_card",
            title=f"Placement Financial Summary — {view.entity_id}",
            subtitle=f"{len(visible_quotes)} active carrier submission(s)",
            data={
                "currency": view.financials.currency,
                "total_premium": view.financials.total_premium,
                "total_deductible": view.financials.total_deductible,
                "total_coverage_limit": view.financials.total_coverage_limit,
                "total_insured_value": view.financials.total_insured_value,
                "deductible_to_limit_pct": view.financials.deductible_to_limit_pct,
                "coverage_gaps": list(view.coverage_gaps),
            },
            source_links=all_links,
        ),
        A2uiWidget(
            widget_id=f"table:broker:{view.entity_id}",
            widget_type="comparison_table",
            title="Consolidated Carrier Quote & Policy Comparison",
            data={"quotes": quotes_rows},
            source_links=all_links,
        ),
    ]


def _build_claims_widgets(
    view: ClaimsView,
    *,
    allow_raw_vault_uri: bool,
) -> list[A2uiWidget]:
    links = [
        _to_a2ui_source_link(
            view.entity_id,
            item.field_path.split(".")[-1],
            item.trace,
            allow_raw_vault_uri=allow_raw_vault_uri,
        )
        for item in view.active_coverages
    ]
    return [
        A2uiWidget(
            widget_id=f"metric:claims:{view.entity_id}",
            widget_type="metric_card",
            title="Active Coverage on Loss Date",
            subtitle=f"Loss Date: {view.loss_date.isoformat()}",
            data={
                "loss_date": view.loss_date.isoformat(),
                "active_limit_total": view.active_limit_total,
                "active_deductible_total": view.active_deductible_total,
                "endorsement_count": view.endorsement_count,
                "active_coverages": [
                    {
                        "field_path": c.field_path,
                        "field_value": c.field_value,
                        "numeric_value": c.numeric_value,
                        "doctype": c.doctype,
                        "doc_id": c.doc_id,
                        "page_number": c.trace.page_number,
                    }
                    for c in view.active_coverages
                ],
            },
            source_links=links,
        )
    ]


def _build_underwriter_widgets(
    view: UnderwriterView,
    *,
    allow_raw_vault_uri: bool,
) -> list[A2uiWidget]:
    links = [
        _to_a2ui_source_link(
            view.entity_id,
            item.field_path.split(".")[-1],
            item.trace,
            allow_raw_vault_uri=allow_raw_vault_uri,
        )
        for item in view.exposure_items
    ]
    return [
        A2uiWidget(
            widget_id=f"metric:underwriter:{view.entity_id}",
            widget_type="metric_card",
            title="Underwriter Exposure & Risk Ratios",
            subtitle=f"Flagged Items: {view.flagged_items_count}",
            data={
                "total_insured_value": view.financials.total_insured_value,
                "total_coverage_limit": view.financials.total_coverage_limit,
                "total_deductible": view.financials.total_deductible,
                "deductible_to_limit_pct": view.financials.deductible_to_limit_pct,
                "flagged_items_count": view.flagged_items_count,
            },
            source_links=links,
        )
    ]


def render_domain_agent_a2ui(
    entity_id: str,
    *,
    role: CatalogRole,
    channel: DeliveryChannel,
    target_view: str | None = None,
    as_of_learned: datetime | None = None,
    effective_on: datetime | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
    principal: UserPrincipal | None = None,
    agent_spiffe_id: str | None = None,
    svid_verified: bool = False,
) -> A2uiDeliveryEnvelope:
    """Execute a governed Domain Agent query and return an A2UI delivery envelope."""
    store = operational_store or operational_store_from_env()
    catalog = knowledge_catalog or knowledge_catalog_from_env(operational_store=store)
    gym = agent_gym_from_env(agent_gym)
    if hasattr(catalog, "bind_operational_store"):
        catalog.bind_operational_store(store)
    resolved_view = target_view or _DEFAULT_VIEW_BY_ROLE[role]

    if principal is not None:
        authorize_entity_access(principal, entity_id)

    agent_ctx = catalog.build_agent_context(
        role=role,
        channel=channel,
        target_view=resolved_view,
        agent_spiffe_id=agent_spiffe_id,
        svid_verified=svid_verified,
    )
    if not agent_ctx.access_decision.allowed:
        raise PermissionError(agent_ctx.access_decision.reason)

    allow_raw_vault_uri = agent_ctx.access_decision.allow_raw_vault_uri
    allow_competitor_quotes = agent_ctx.access_decision.allow_competitor_quotes

    if resolved_view == "v_broker_placement":
        b_view = store.broker_view(entity_id, learned_as_of=as_of_learned)
        widgets = _build_broker_widgets(
            b_view,
            allow_raw_vault_uri=allow_raw_vault_uri,
            allow_competitor_quotes=allow_competitor_quotes,
            principal=principal,
        )
        headline = f"Placement view for {entity_id} ({len(b_view.quotes)} submission(s))"
    elif resolved_view == "v_claims_coverage":
        loss_dt = effective_on or as_of_learned or datetime.now(UTC)
        c_view = store.claims_view(
            entity_id,
            loss_date=loss_dt,
            learned_as_of=as_of_learned,
        )
        widgets = _build_claims_widgets(
            c_view, allow_raw_vault_uri=allow_raw_vault_uri
        )
        headline = (
            f"Claims coverage view for {entity_id} "
            f"as of {c_view.loss_date.isoformat()}"
        )
    elif resolved_view == "v_underwriter_exposure":
        u_view = store.underwriter_view(entity_id, learned_as_of=as_of_learned)
        widgets = _build_underwriter_widgets(
            u_view, allow_raw_vault_uri=allow_raw_vault_uri
        )
        headline = f"Underwriter exposure view for {entity_id}"
    else:
        raise ValueError(f"Unsupported business view '{resolved_view}'.")

    # Apply Stage 3 Agent Gym presentation preferences for internal Gemini App roles
    if channel == "gemini_app" and widgets:
        prefs = gym.get_presentation_preferences(role=role)
        if prefs:
            primary_data = dict(widgets[0].data)
            primary_data["applied_tribal_rules"] = [
                {
                    "rule_id": r.rule_id,
                    "title": r.title,
                    "field_path": r.field_path,
                    "instruction": r.instruction,
                }
                for r in prefs
            ]
            widgets[0] = widgets[0].model_copy(update={"data": primary_data})

    # Attach bi-temporal history timeline widget when multiple versions exist
    all_history = store.list_facts(entity_id)
    distinct_recordings = sorted({f.recorded_at for f in all_history})
    if len(distinct_recordings) > 1:
        widgets.append(
            A2uiWidget(
                widget_id=f"timeline:{entity_id}",
                widget_type="time_travel_timeline",
                title="Bi-Temporal Version History (Point-in-Time Audit)",
                subtitle=f"{len(distinct_recordings)} recorded states available",
                data={
                    "recorded_timestamps": [ts.isoformat() for ts in distinct_recordings],
                    "total_fact_versions": len(all_history),
                },
            )
        )

    # Attach internal conflict alerts ONLY if permitted by Knowledge Catalog policy
    if agent_ctx.access_decision.allow_internal_conflict_notes:
        _, conflicts = store.query_as_of(
            entity_id, learned_as_of=as_of_learned, effective_on=effective_on
        )
        if conflicts:
            widgets.append(
                A2uiWidget(
                    widget_id=f"conflicts:{entity_id}",
                    widget_type="conflict_alert",
                    title="Cross-Document Discrepancy Alerts",
                    subtitle=f"{len(conflicts)} discrepancy flag(s) detected",
                    data={
                        "conflicts": [
                            {
                                "field_path": c.field_path,
                                "winning_doc_id": c.winning_doc_id,
                                "winning_value": c.winning_value,
                                "conflicting_doc_id": c.conflicting_doc_id,
                                "conflicting_value": c.conflicting_value,
                                "reason": c.reason,
                            }
                            for c in conflicts
                        ]
                    },
                )
            )

    lineage_records = catalog.list_lineage_for_entity(entity_id)
    return A2uiDeliveryEnvelope(
        channel=channel,
        role=role,
        target_view=resolved_view,
        entity_id=entity_id,
        as_of_learned=as_of_learned,
        effective_on=effective_on,
        governance=agent_ctx.access_decision,
        summary_headline=headline,
        widgets=widgets,
        lineage_count=len(lineage_records),
    )


class EnterpriseIngestionResult(BaseModel):
    """Result of promoting an accepted submission across Zones 2, 3, and 6."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: LedgerEntry
    vault_record: VaultRecord
    facts: tuple[ConsolidatedFact, ...]
    conflicts: tuple[FactConflict, ...]
    lineage_traces: tuple[LineageTrace, ...]


def ingest_accepted_submission(
    entry: LedgerEntry,
    *,
    entity_id: str,
    context_id: str,
    raw_document_bytes: bytes = b"%PDF-1.7",
    filename: str | None = None,
    version: int = 1,
    vault_store: AuditVaultStore | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
    recorded_at: datetime | None = None,
    effective_from: datetime | None = None,
) -> EnterpriseIngestionResult:
    """Promote an accepted entry through Vault (WORM), Operational Store, and Catalog."""
    vault = vault_store or vault_from_env()
    store = operational_store or operational_store_from_env()
    catalog = knowledge_catalog or knowledge_catalog_from_env()
    gym = agent_gym_from_env(agent_gym)

    # Apply Stage 2 Agent Gym Entity/Carrier Alias normalization before consolidation
    # while keeping 100% of financial math deterministic in OperationalStore.
    normalized_entry = entry
    if entry.issuer:
        norm_issuer = gym.normalize_entity_or_issuer(entry.issuer)
        if norm_issuer and norm_issuer != entry.issuer:
            new_key_fields = dict(entry.key_fields)
            if "issuer" in new_key_fields:
                new_key_fields["issuer"] = norm_issuer
            normalized_entry = entry.model_copy(
                update={
                    "issuer": norm_issuer,
                    "key_fields": new_key_fields,
                }
            )

    promoted_entry, vault_record = promote_accepted_to_vault(
        vault,
        context_id=context_id,
        entry=normalized_entry,
        data=raw_document_bytes,
        filename=filename,
        version=version,
        recorded_at=recorded_at,
        effective_from=effective_from,
    )
    facts = store.ingest_entry(
        promoted_entry,
        entity_id=entity_id,
        context_id=context_id,
        recorded_at=promoted_entry.recorded_at,
        effective_from=promoted_entry.effective_from,
        version=version,
    )
    _, conflicts = store.query_as_of(entity_id)
    lineage_traces = catalog.register_submission_lineage(promoted_entry, facts)
    return EnterpriseIngestionResult(
        entry=promoted_entry,
        vault_record=vault_record,
        facts=tuple(facts),
        conflicts=tuple(conflicts),
        lineage_traces=tuple(lineage_traces),
    )


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
    category: TribalRuleCategory | None = None
    distinct_entities: list[str] = Field(default_factory=list)


class DistillInboxAuditRequest(BaseModel):
    """Request body to distill a Tribal Knowledge Rule from a Zone 5 Inbox audit record."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = "doc-inbox"
    doctype: str = "quote"
    issuer: str = "Carrier"
    field_path: str | None = None
    category: TribalRuleCategory | None = None
    distinct_entities: list[str] = Field(default_factory=list)


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

    @router.get("/v1/a2ui/catalogs/{catalog_name}")
    def get_a2ui_catalog_schema(catalog_name: str) -> dict[str, Any]:
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
                            item.hint
                            if channel == "gemini_app"
                            else item.external_hint
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
                [
                    a.model_dump(mode="json")
                    for a in inbox.list_audit_records(item_id)
                ]
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
                "hint": (
                    item.hint if channel == "gemini_app" else item.external_hint
                ),
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
                SourceAnchor(page_number=body.page_number)
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
            report = gym.evaluate_candidate_rule(
                rule_id, auto_promote=auto_promote
            )
            rule = next(
                r for r in gym.list_rules() if r.rule_id == rule_id
            )
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

    @router.get("/v1/gym/rules")
    def list_gym_rules_endpoint(
        category: TribalRuleCategory | None = None,
        status: TribalRuleStatus | None = None,
        issuer: str | None = None,
        doctype: str | None = None,
    ) -> list[dict[str, Any]]:
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
        return [
            r.model_dump(mode="json")
            for r in gym.get_extraction_hints(issuer=issuer, doctype=doctype)
        ]

    @router.get("/v1/gym/trust-policies")
    def list_gym_trust_policies_endpoint() -> list[dict[str, Any]]:
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

    @router.get("/v1/catalog/spiffe-agents")
    def get_catalog_spiffe_agents(
        security_zone: Literal["internal", "external"] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            p.model_dump(mode="json")
            for p in catalog.list_spiffe_agents(security_zone=security_zone)
        ]

    @router.get("/v1/catalog/assets")
    def get_catalog_assets(
        external_safe_only: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            a.model_dump(mode="json")
            for a in catalog.list_assets(external_safe_only=external_safe_only)
        ]

    @router.get("/v1/catalog/glossary")
    def get_catalog_glossary(
        domain: str | None = None,
        include_confidential: bool = True,
    ) -> list[dict[str, Any]]:
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
        trace = catalog.trace_lineage(entity_id, field_name)
        if trace is None:
            raise HTTPException(
                status_code=404,
                detail=f"No lineage found for {entity_id}.{field_name}",
            )
        return trace.model_dump(mode="json")

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


__all__ = [
    "A2UI_CONTENT_TYPE",
    "A2UI_REQUIRE_AUTH_ENV",
    "A2UI_V09_MIME_TYPE",
    "A2UI_V09_VERSION",
    "ENTERPRISE_INBOX_CATALOG_SCHEMA",
    "INBOX_CATALOG_ID",
    "PORTAL_CATALOG_ID",
    "PORTAL_CUSTOMER_CATALOG_SCHEMA",
    "A2aDataPart",
    "A2uiClientActionRequest",
    "A2uiDeliveryEnvelope",
    "A2uiSourceLink",
    "A2uiWidget",
    "ActionCitation",
    "ActionInboxItem",
    "ActionInboxStore",
    "ActionProgressStep",
    "ActionStepStatus",
    "AdvanceActionStepRequest",
    "CreateActionProgressRequest",
    "EnterpriseIngestionResult",
    "GovernanceDecision",
    "InMemoryActionInboxStore",
    "InboxAuditRecord",
    "Rfc6902PatchOp",
    "RiskTier",
    "TaskLifecycleState",
    "action_inbox_store_from_env",
    "build_action_progress_surface",
    "build_adk_confirmation_response",
    "build_delete_surface_msg",
    "build_update_data_model_delta",
    "build_user_interaction_app",
    "build_user_interaction_router",
    "compute_rfc6902_patches",
    "default_submission_progress_steps",
    "encode_a2ui_envelope",
    "format_a2ui_sse_frames",
    "ingest_accepted_submission",
    "render_domain_agent_a2ui",
    "request_a2ui_inbox_confirmation",
    "resolve_caller_role",
    "wrap_a2ui_in_a2a_data_part",
]
