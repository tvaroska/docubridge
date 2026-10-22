"""Zone 5 User Interaction: A2UI envelope models, caller role resolution & SPIFFE compound identity."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

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
    UserPrincipal,
    parse_decimal_header,
    verify_jwt_svid,
)
from bridge.knowledge_catalog import AccessDecision, CatalogRole, DeliveryChannel


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
    default_role: CatalogRole | None = None,
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
        if (
            verified_role is None
            and is_scope_authed
            and hasattr(user, "role")
            and user.role in _VALID_ROLES
        ):
            verified_role = cast(CatalogRole, user.role)
        if verified_role is None and header_role in _VALID_ROLES:
            verified_role = cast(CatalogRole, header_role)
        if verified_role is None and principal_name.startswith("role:"):
            candidate = principal_name.split(":", 1)[1].strip()
            if candidate in _VALID_ROLES:
                verified_role = cast(CatalogRole, candidate)
        if verified_role is None and iap_email:
            lowered = iap_email.lower()
            if "credit" in lowered:
                verified_role = "underwriter"
            else:
                for r in ("underwriter", "claims", "broker", "carrier", "client"):
                    if r in lowered:
                        verified_role = cast(CatalogRole, r)
                        break
            if verified_role is None and lowered.endswith("@docubridge.io"):
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
    if default_role is not None:
        return default_role

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
            query_role=query_role,
            default_role=default_role,
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
