"""Zone 5 User Interaction: A2UI envelope models, caller role resolution & SPIFFE compound identity."""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from bridge.identity import (
    ALLOWED_ENTITIES_HEADER,
    CARRIER_SLUG_HEADER,
    DEFAULT_SPIFFE_AGENT_POLICIES,
    MAX_APPROVAL_HEADER,
    SPIFFE_ID_HEADER,
    SPIFFE_REQUIRE_SVID_ENV,
    SPIFFE_SVID_HEADER,
    TENANT_ID_HEADER,
    USER_TOKEN_HEADER,
    CompoundIdentity,
    ShadowAgentError,
    SpiffeAgentPolicy,
    SpiffeAuthenticationError,
    SpiffeId,
    UserPrincipal,
    parse_decimal_header,
    verify_google_oidc_token,
    verify_jwt_svid,
    verify_user_token,
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
IAP_JWT_ASSERTION_HEADER = "x-goog-iap-jwt-assertion"
IAP_EMAIL_HEADER = "x-goog-authenticated-user-email"
IAP_AUDIENCE_ENV = "BRIDGE_IAP_AUDIENCE"
SESSION_COOKIE_NAME = "bridge_session"
READ_ONLY_SCOPE = "read_only"
INTERACTIVE_SCOPE = "interactive"
_VALID_ROLES: frozenset[str] = frozenset(_DEFAULT_VIEW_BY_ROLE.keys())


class AuthenticationRequiredError(PermissionError):
    """Raised when require_auth is enabled and no authenticated principal is present (HTTP 401)."""


def encode_a2ui_envelope(envelope: A2uiDeliveryEnvelope) -> dict[str, Any]:
    """Serialize an ``A2uiDeliveryEnvelope`` for A2UI transport (JSON-mode dump)."""
    return envelope.model_dump(mode="json")


def is_auth_enforced(require_auth: bool = False) -> bool:
    """Return True when cryptographically verified authentication is required.

    Enforced unconditionally whenever ``K_SERVICE`` is set (Cloud Run), regardless
    of ``BRIDGE_PORT_MODE``, or when ``require_auth=True`` / ``BRIDGE_A2UI_REQUIRE_AUTH=1``.
    """
    if require_auth:
        return True
    if os.environ.get(A2UI_REQUIRE_AUTH_ENV, "").strip() == "1":
        return True
    return bool(os.environ.get("K_SERVICE", "").strip())


_is_auth_enforced = is_auth_enforced


def _resolve_app_oidc_verifier(
    request: Request,
) -> Callable[..., dict[str, Any]] | None:
    scope = getattr(request, "scope", None)
    app = scope.get("app") if isinstance(scope, dict) else None
    state = getattr(app, "state", None)
    verifier = getattr(state, "oidc_verifier", None)
    return verifier if callable(verifier) else None


def _verify_iap_request(
    request: Request,
    *,
    oidc_verifier: Callable[..., dict[str, Any]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Verify ``X-Goog-IAP-JWT-Assertion`` cryptographically before trusting IAP email headers."""
    iap_assertion = request.headers.get(IAP_JWT_ASSERTION_HEADER, "").strip()
    iap_email_raw = request.headers.get(IAP_EMAIL_HEADER, "").strip()
    iap_email_from_hdr = (
        iap_email_raw.split(":", 1)[1].strip()
        if ":" in iap_email_raw
        else iap_email_raw
    )
    if not iap_assertion:
        return "", "", {}

    verifier = oidc_verifier or _resolve_app_oidc_verifier(request)
    expected_audience = os.environ.get(IAP_AUDIENCE_ENV, "").strip() or None
    try:
        if verifier is not None:
            sig = inspect.signature(verifier)
            accepts_aud = "expected_audience" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            iap_claims = (
                dict(verifier(iap_assertion, expected_audience=expected_audience))
                if accepts_aud
                else dict(verifier(iap_assertion))
            )
        else:
            iap_claims = dict(
                verify_google_oidc_token(
                    iap_assertion, expected_audience=expected_audience
                )
            )
    except Exception as exc:
        raise AuthenticationRequiredError(
            f"Invalid X-Goog-IAP-JWT-Assertion: {exc}"
        ) from exc

    verified_email = str(iap_claims.get("email") or "").strip()
    if not verified_email:
        raise AuthenticationRequiredError(
            "Verified X-Goog-IAP-JWT-Assertion is missing an 'email' claim."
        )
    if iap_email_from_hdr and iap_email_from_hdr.lower() != verified_email.lower():
        raise AuthenticationRequiredError(
            "X-Goog-Authenticated-User-Email does not match verified X-Goog-IAP-JWT-Assertion."
        )
    return verified_email, iap_email_raw, iap_claims


def _extract_user_token_claims(
    request: Request,
    *,
    enforce_auth: bool,
) -> tuple[dict[str, Any], Literal["jwt", "cookie"] | None]:
    """Verify signed HS256 user JWT from ``X-Bridge-User-Token`` header or ``bridge_session`` cookie."""
    user_token_header = request.headers.get(USER_TOKEN_HEADER, "").strip()
    if user_token_header:
        try:
            return verify_user_token(user_token_header), "jwt"
        except SpiffeAuthenticationError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc

    session_cookie = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if session_cookie:
        try:
            claims = verify_user_token(session_cookie)
        except SpiffeAuthenticationError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc
        if (
            not enforce_auth
            and str(claims.get("scope") or "").strip() == READ_ONLY_SCOPE
        ):
            return {}, None
        return claims, "cookie"

    return {}, None


@dataclass(frozen=True)
class _RoleLookupContext:
    principal_name: str
    iap_email: str
    iap_email_raw: str
    principal_roles: dict[str, CatalogRole] | None
    user: object
    is_scope_authed: bool
    header_role: str


@dataclass(frozen=True)
class _SvidContext:
    agent_spiffe_id: str | None
    svid_verified: bool
    obo_claims: dict[str, Any]
    audience: str | None


@dataclass(frozen=True)
class _PrincipalAttributes:
    tenant_id: str | None
    allowed_entities: tuple[str, ...]
    carrier_slug: str | None
    max_approval: Decimal | None


def _lookup_mapped_role(ctx: _RoleLookupContext) -> CatalogRole | None:
    if ctx.principal_roles:
        for candidate_key in (ctx.principal_name, ctx.iap_email, ctx.iap_email_raw):
            if candidate_key and candidate_key in ctx.principal_roles:
                return ctx.principal_roles[candidate_key]
    scope_role = getattr(ctx.user, "role", None) if ctx.is_scope_authed else None
    if isinstance(scope_role, str) and scope_role in _VALID_ROLES:
        return cast(CatalogRole, scope_role)
    if ctx.header_role in _VALID_ROLES:
        return cast(CatalogRole, ctx.header_role)
    if ctx.principal_name.startswith("role:"):
        candidate = ctx.principal_name.split(":", 1)[1].strip()
        if candidate in _VALID_ROLES:
            return cast(CatalogRole, candidate)
    return None


def resolve_caller_role(  # noqa: PLR0913 - public caller role resolution contract
    request: Request,
    *,
    query_role: CatalogRole | None = None,
    default_role: CatalogRole | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    oidc_verifier: Callable[..., dict[str, Any]] | None = None,
) -> CatalogRole:
    """Resolve and verify the caller's ``CatalogRole`` from authenticated identity.

    When ``require_auth``, ``BRIDGE_A2UI_REQUIRE_AUTH=1``, or ``K_SERVICE`` is active,
    raw unverified ``X-Bridge-Principal``, ``X-Bridge-Role``, and unverified
    ``X-Goog-Authenticated-User-Email`` headers are NOT trusted; only
    ``request.scope["user"]``, cryptographically verified ``X-Goog-IAP-JWT-Assertion``,
    or a signed ``X-Bridge-User-Token`` / ``bridge_session`` cookie / SPIFFE SVID is
    accepted. When an authenticated principal is present, a conflicting ``query_role``
    is rejected with ``PermissionError`` (HTTP 403).
    """
    enforce_auth = is_auth_enforced(require_auth)
    user = request.scope.get("user")
    is_scope_authed = bool(
        user is not None and getattr(user, "is_authenticated", False)
    )
    iap_email, iap_email_raw, iap_claims = _verify_iap_request(
        request, oidc_verifier=oidc_verifier
    )
    user_token_claims, _token_source = _extract_user_token_claims(
        request, enforce_auth=enforce_auth
    )

    header_principal = (
        "" if enforce_auth else request.headers.get("x-bridge-principal", "").strip()
    )
    header_role = (
        "" if enforce_auth else request.headers.get("x-bridge-role", "").strip().lower()
    )

    token_sub = str(user_token_claims.get("sub") or "").strip()
    token_role_raw = str(user_token_claims.get("role") or "").strip().lower()
    token_role: CatalogRole | None = (
        cast(CatalogRole, token_role_raw) if token_role_raw in _VALID_ROLES else None
    )
    iap_claim_role_raw = str(iap_claims.get("role") or "").strip().lower()
    iap_claim_role: CatalogRole | None = (
        cast(CatalogRole, iap_claim_role_raw)
        if iap_claim_role_raw in _VALID_ROLES
        else None
    )

    principal_name = (
        (getattr(user, "display_name", None) or getattr(user, "username", None) or "")
        if is_scope_authed
        else (token_sub or iap_email or header_principal)
    )
    has_auth_identity = bool(
        is_scope_authed
        or token_sub
        or token_role
        or iap_email
        or header_principal
        or header_role
    )

    verified_role: CatalogRole | None = token_role or iap_claim_role
    if has_auth_identity and verified_role is None:
        verified_role = _lookup_mapped_role(
            _RoleLookupContext(
                principal_name=principal_name,
                iap_email=iap_email,
                iap_email_raw=iap_email_raw,
                principal_roles=principal_roles,
                user=user,
                is_scope_authed=is_scope_authed,
                header_role=header_role,
            )
        )

    if verified_role is not None:
        if query_role is not None and query_role != verified_role:
            raise PermissionError(
                f"Authenticated principal '{principal_name or verified_role}' has role "
                f"'{verified_role}', cannot impersonate role '{query_role}'."
            )
        return verified_role

    if enforce_auth:
        raise AuthenticationRequiredError(
            "Authenticated principal with a mapped CatalogRole is required."
        )

    if query_role is not None:
        return query_role
    if default_role is not None:
        return default_role

    raise PermissionError(
        "Missing caller role: supply an authenticated principal, X-Bridge-Role header, or role parameter."
    )


def _resolve_workload_svid_role(
    agent_spiffe_id: str,
    *,
    query_role: CatalogRole | None,
    default_role: CatalogRole | None,
) -> tuple[CatalogRole, SpiffeAgentPolicy]:
    policy = DEFAULT_SPIFFE_AGENT_POLICIES.get(agent_spiffe_id)
    if policy is None:
        raise ShadowAgentError(
            f"Unregistered SPIFFE agent identity '{agent_spiffe_id}' blocked by Knowledge Catalog "
            "(Shadow AI protection)."
        )
    if query_role is not None:
        if query_role not in policy.allowed_roles:
            raise PermissionError(
                f"SPIFFE agent '{agent_spiffe_id}' is not authorized to act on behalf of role "
                f"'{query_role}' (allowed: {', '.join(policy.allowed_roles)})."
            )
        return query_role, policy
    if default_role is not None and default_role in policy.allowed_roles:
        return default_role, policy
    return policy.allowed_roles[0], policy


def _resolve_spiffe_svid_context(
    request: Request,
    *,
    enforce_auth: bool,
    enforce_svid: bool,
) -> _SvidContext:
    svid_header = request.headers.get(SPIFFE_SVID_HEADER, "").strip()
    spiffe_id_header = request.headers.get(SPIFFE_ID_HEADER, "").strip()
    if svid_header:
        try:
            claims = verify_jwt_svid(svid_header)
        except SpiffeAuthenticationError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc
        aud_claim = claims.get("aud")
        audience = (
            aud_claim[0]
            if isinstance(aud_claim, list) and aud_claim
            else (str(aud_claim) if aud_claim else None)
        )
        obo_raw = claims.get("obo")
        return _SvidContext(
            agent_spiffe_id=str(claims["sub"]),
            svid_verified=True,
            obo_claims=dict(obo_raw) if isinstance(obo_raw, dict) else {},
            audience=audience,
        )
    if spiffe_id_header:
        if enforce_svid or enforce_auth:
            raise AuthenticationRequiredError(
                "Cryptographically signed SPIFFE JWT-SVID header 'X-Bridge-Spiffe-Svid' "
                "is required when auth is enforced."
            )
        try:
            parsed = SpiffeId.parse(spiffe_id_header)
        except SpiffeAuthenticationError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc
        return _SvidContext(
            agent_spiffe_id=parsed.uri,
            svid_verified=False,
            obo_claims={},
            audience=None,
        )
    if enforce_svid:
        raise AuthenticationRequiredError(
            "Missing SPIFFE JWT-SVID header 'X-Bridge-Spiffe-Svid' "
            "(BRIDGE_SPIFFE_REQUIRE_SVID=1)."
        )
    return _SvidContext(
        agent_spiffe_id=None,
        svid_verified=False,
        obo_claims={},
        audience=None,
    )


def _extract_principal_attributes(
    request: Request,
    *,
    enforce_auth: bool,
    obo_claims: dict[str, Any],
    user_token_claims: dict[str, Any],
) -> _PrincipalAttributes:
    tenant_id = (
        str(obo_claims.get("tenant_id") or user_token_claims.get("tenant_id") or "")
        or ("" if enforce_auth else request.headers.get(TENANT_ID_HEADER, "").strip())
        or None
    )
    raw_entities_header = (
        "" if enforce_auth else request.headers.get(ALLOWED_ENTITIES_HEADER, "").strip()
    )
    header_entities = tuple(
        e.strip() for e in raw_entities_header.split(",") if e.strip()
    )
    obo_entities = (
        tuple(str(e) for e in obo_claims["allowed_entities"])
        if isinstance(obo_claims.get("allowed_entities"), list)
        else (
            tuple(str(e) for e in user_token_claims["allowed_entities"])
            if isinstance(user_token_claims.get("allowed_entities"), list)
            else ()
        )
    )
    carrier_slug = (
        str(
            obo_claims.get("carrier_slug")
            or user_token_claims.get("carrier_slug")
            or ""
        )
        or (
            "" if enforce_auth else request.headers.get(CARRIER_SLUG_HEADER, "").strip()
        )
        or None
    )
    if "max_approval_amount" in obo_claims:
        max_approval_raw = str(obo_claims["max_approval_amount"])
    elif "max_approval_amount" in user_token_claims:
        max_approval_raw = str(user_token_claims["max_approval_amount"])
    else:
        max_approval_raw = (
            "" if enforce_auth else request.headers.get(MAX_APPROVAL_HEADER, "").strip()
        )
    try:
        max_approval = parse_decimal_header(max_approval_raw)
    except SpiffeAuthenticationError as exc:
        raise AuthenticationRequiredError(str(exc)) from exc
    return _PrincipalAttributes(
        tenant_id=tenant_id,
        allowed_entities=obo_entities or header_entities,
        carrier_slug=carrier_slug,
        max_approval=max_approval,
    )


def resolve_compound_identity(  # noqa: PLR0913 - public compound identity resolution contract
    request: Request,
    *,
    query_role: CatalogRole | None = None,
    default_role: CatalogRole | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    require_svid: bool = False,
    require_mutating: bool = False,
    oidc_verifier: Callable[..., dict[str, Any]] | None = None,
) -> CompoundIdentity:
    """Resolve and verify both the Human Principal (`sub`) and SPIFFE Agent (`act`)."""
    enforce_auth = is_auth_enforced(require_auth)
    enforce_svid = require_svid or (
        os.environ.get(SPIFFE_REQUIRE_SVID_ENV, "").strip() == "1"
    )
    svid = _resolve_spiffe_svid_context(
        request, enforce_auth=enforce_auth, enforce_svid=enforce_svid
    )
    agent_policy: SpiffeAgentPolicy | None = None

    user_token_claims, auth_source = _extract_user_token_claims(
        request, enforce_auth=enforce_auth
    )
    if (
        svid.svid_verified
        and auth_source == "cookie"
        and (
            svid.obo_claims
            or str(user_token_claims.get("scope") or "").strip() == READ_ONLY_SCOPE
        )
    ):
        user_token_claims = {}
        auth_source = None

    iap_email, _iap_email_raw, _iap_claims = _verify_iap_request(
        request, oidc_verifier=oidc_verifier
    )

    obo_role_raw = svid.obo_claims.get("role") or user_token_claims.get("role")
    obo_role: CatalogRole | None = (
        cast(CatalogRole, obo_role_raw)
        if isinstance(obo_role_raw, str) and obo_role_raw in _VALID_ROLES
        else None
    )
    if obo_role is not None and query_role is not None and query_role != obo_role:
        raise PermissionError(
            f"OBO principal in token/SVID has role '{obo_role}', cannot impersonate "
            f"role '{query_role}'."
        )

    if obo_role is not None:
        resolved_role = obo_role
    elif (
        svid.svid_verified
        and svid.agent_spiffe_id is not None
        and not user_token_claims
        and not iap_email
    ):
        resolved_role, agent_policy = _resolve_workload_svid_role(
            svid.agent_spiffe_id,
            query_role=query_role,
            default_role=default_role,
        )
    else:
        resolved_role = resolve_caller_role(
            request,
            query_role=query_role,
            default_role=default_role,
            principal_roles=principal_roles,
            require_auth=require_auth,
            oidc_verifier=oidc_verifier,
        )

    token_scope = (
        str(
            svid.obo_claims.get("scope") or user_token_claims.get("scope") or ""
        ).strip()
        or None
    )
    if enforce_auth and require_mutating and token_scope == READ_ONLY_SCOPE:
        raise PermissionError(
            "Read-only viewer session cannot invoke mutating endpoints. "
            "Authenticate via POST /v1/auth/demo-session or a signed token header."
        )

    header_principal = (
        "" if enforce_auth else request.headers.get("x-bridge-principal", "").strip()
    )
    subject = (
        str(svid.obo_claims.get("sub") or user_token_claims.get("sub") or "")
        or iap_email
        or (
            svid.agent_spiffe_id
            if (svid.svid_verified and agent_policy is not None)
            else ""
        )
        or header_principal
        or f"role:{resolved_role}"
    )
    attrs = _extract_principal_attributes(
        request,
        enforce_auth=enforce_auth,
        obo_claims=svid.obo_claims,
        user_token_claims=user_token_claims,
    )

    via: Literal["jwt", "cookie", "iap", "header", "query_dev"]
    if (
        svid.svid_verified and (svid.obo_claims or agent_policy is not None)
    ) or auth_source == "jwt":
        via = "jwt"
    elif auth_source == "cookie":
        via = "cookie"
    elif iap_email:
        via = "iap"
    elif header_principal:
        via = "header"
    else:
        via = "query_dev"

    return CompoundIdentity(
        user=UserPrincipal(
            subject=subject,
            role=resolved_role,
            tenant_id=attrs.tenant_id,
            allowed_entities=attrs.allowed_entities,
            carrier_slug=attrs.carrier_slug,
            max_approval_amount=attrs.max_approval,
            authenticated_via=via,
            scope=token_scope,
        ),
        agent_spiffe_id=svid.agent_spiffe_id,
        agent_policy=agent_policy,
        svid_verified=svid.svid_verified,
        audience=svid.audience,
    )
