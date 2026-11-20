"""Option 2B signed-cookie sessions, read-only viewer cookies, and passcode exchange routes."""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from bridge.identity import (
    DEFAULT_ROLE_APPROVAL_LIMITS,
    DEFAULT_SPIFFE_AGENT_POLICIES,
    CompoundIdentity,
    ShadowAgentError,
    SpiffeAuthenticationError,
    mint_user_token,
    verify_user_token,
)
from bridge.knowledge_catalog import CatalogRole
from bridge.user_interaction.auth import (
    _VALID_ROLES,
    INTERACTIVE_SCOPE,
    READ_ONLY_SCOPE,
    SESSION_COOKIE_NAME,
    AuthenticationRequiredError,
    is_auth_enforced,
    resolve_compound_identity,
)
from bridge.user_interaction.schemas import DemoPersonaLiteral, DemoSessionRequest

DEMO_PASSCODE_ENV = "BRIDGE_DEMO_PASSCODE"
VIEWER_SESSION_TTL_SECONDS = 3600
DEMO_SESSION_TTL_SECONDS = 28800


@dataclass(frozen=True)
class DemoPersonaProfile:
    """Server-defined persona profile minted by ``POST /v1/auth/demo-session``."""

    persona: DemoPersonaLiteral
    role: CatalogRole
    subject: str
    max_approval_amount: Decimal
    display_name: str


DEMO_PERSONA_PROFILES: dict[DemoPersonaLiteral, DemoPersonaProfile] = {
    "client": DemoPersonaProfile(
        persona="client",
        role="client",
        subject="demo:client@borrower.example",
        max_approval_amount=Decimal("0.00"),
        display_name="External Borrower / Client",
    ),
    "broker": DemoPersonaProfile(
        persona="broker",
        role="broker",
        subject="demo:broker@docubridge.io",
        max_approval_amount=DEFAULT_ROLE_APPROVAL_LIMITS["broker"],
        display_name="Commercial Broker / Deal Desk",
    ),
    "underwriter": DemoPersonaProfile(
        persona="underwriter",
        role="underwriter",
        subject="demo:underwriter@docubridge.io",
        max_approval_amount=DEFAULT_ROLE_APPROVAL_LIMITS["underwriter"],
        display_name="Senior Credit Officer / Underwriter",
    ),
}


def is_secure_cookie_request(request: Request) -> bool:
    """Return True when the ``Secure`` cookie attribute must be enforced."""
    if os.environ.get("K_SERVICE", "").strip():
        return True
    forwarded_proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    )
    if forwarded_proto == "https":
        return True
    return request.url.scheme == "https"


def set_session_cookie(
    response: Response,
    request: Request,
    *,
    token: str,
    max_age: int,
) -> None:
    """Attach the signed ``bridge_session`` cookie with HttpOnly + SameSite=Strict flags."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=is_secure_cookie_request(request),
        samesite="strict",
    )


def issue_viewer_cookie(request: Request, response: Response) -> bool:
    """Auto-issue a signed read-only viewer cookie unless an interactive cookie is already active.

    Returns True when a viewer cookie was set, False if an existing interactive session
    was preserved or signing key material is unavailable under ``K_SERVICE``.
    """
    existing = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if existing:
        try:
            claims = verify_user_token(existing)
            if str(claims.get("scope") or "").strip() != READ_ONLY_SCOPE:
                return False
        except SpiffeAuthenticationError:
            pass
    try:
        token = mint_user_token(
            "viewer:demo",
            "broker",
            max_approval_amount=Decimal("0"),
            scope=READ_ONLY_SCOPE,
            ttl_seconds=VIEWER_SESSION_TTL_SECONDS,
        )
    except SpiffeAuthenticationError:
        return False
    set_session_cookie(
        response,
        request,
        token=token,
        max_age=VIEWER_SESSION_TTL_SECONDS,
    )
    return True


def require_mutating_identity(
    request: Request,
    *,
    query_role: CatalogRole | None = None,
    default_role: CatalogRole | None = "broker",
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    require_svid: bool = False,
    allow_external_roles: bool = True,
    external_role_error: str | None = None,
    oidc_verifier: Callable[..., dict[str, Any]] | None = None,
) -> CompoundIdentity:
    """Gate a mutating HTTP route: reject unauthenticated (401) and unauthorized/read-only (403) callers."""
    try:
        compound = resolve_compound_identity(
            request,
            query_role=query_role,
            default_role=default_role,
            principal_roles=principal_roles,
            require_auth=require_auth,
            require_svid=require_svid,
            require_mutating=True,
            oidc_verifier=oidc_verifier,
        )
        if not allow_external_roles and compound.user.role in ("client", "carrier"):
            raise PermissionError(
                external_role_error
                or f"External role '{compound.user.role}' is not permitted on this mutating endpoint."
            )
        if compound.agent_spiffe_id is not None:
            policy = DEFAULT_SPIFFE_AGENT_POLICIES.get(compound.agent_spiffe_id)
            if policy is None:
                raise ShadowAgentError(
                    f"Unregistered SPIFFE agent identity '{compound.agent_spiffe_id}' "
                    "blocked by Knowledge Catalog (Shadow AI protection)."
                )
            if compound.user.role not in policy.allowed_roles:
                raise PermissionError(
                    f"SPIFFE agent '{compound.agent_spiffe_id}' is not authorized to act "
                    f"on behalf of role '{compound.user.role}' "
                    f"(allowed: {', '.join(policy.allowed_roles)})."
                )
            if not allow_external_roles and policy.security_zone == "external":
                raise PermissionError(
                    f"External-zone SPIFFE agent '{compound.agent_spiffe_id}' is not "
                    "permitted on this internal mutating endpoint."
                )
        return compound
    except AuthenticationRequiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def verified_mutating_identity(request: Request) -> CompoundIdentity:
    """Return the ``CompoundIdentity`` already resolved by ``mutating_auth_dependency``."""
    return cast(CompoundIdentity, request.state.mutating_compound_identity)


def mutating_auth_dependency(
    *,
    default_role: CatalogRole | None = "broker",
    default_role_fn: Callable[[Request], CatalogRole | None] | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    allow_external_roles: bool = True,
    external_role_error: str | None = None,
) -> Any:
    """FastAPI route dependency enforcing mutating-route auth before request body validation."""

    def _gate(request: Request) -> CompoundIdentity:
        raw_role = (request.query_params.get("role") or "").strip().lower()
        query_role: CatalogRole | None = (
            cast(CatalogRole, raw_role) if raw_role in _VALID_ROLES else None
        )
        resolved_default = (
            default_role_fn(request) if default_role_fn is not None else default_role
        )
        compound = require_mutating_identity(
            request,
            query_role=query_role,
            default_role=resolved_default,
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=allow_external_roles,
            external_role_error=external_role_error,
        )
        request.state.mutating_compound_identity = compound
        return compound

    return Depends(_gate)


def register_demo_session_routes(
    router: APIRouter,
    *,
    require_auth: bool = False,
) -> None:
    """Register Option 2B ``/v1/auth/demo-session`` passcode exchange and logout endpoints."""

    @router.get("/v1/auth/demo-session")
    def get_demo_session_status(request: Request) -> dict[str, Any]:
        """Report non-sensitive session state (auth enforcement, active persona, read-only vs interactive)."""
        enforced = is_auth_enforced(require_auth)
        passcode_configured = bool(os.environ.get(DEMO_PASSCODE_ENV, "").strip())
        raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
        if raw_cookie:
            try:
                claims = verify_user_token(raw_cookie)
                scope = str(claims.get("scope") or INTERACTIVE_SCOPE)
                return {
                    "auth_enforced": enforced,
                    "passcode_configured": passcode_configured,
                    "authenticated": True,
                    "read_only": scope == READ_ONLY_SCOPE,
                    "scope": scope,
                    "role": claims.get("role"),
                    "subject": claims.get("sub"),
                    "max_approval_amount": str(claims.get("max_approval_amount", "0")),
                }
            except SpiffeAuthenticationError:
                pass
        return {
            "auth_enforced": enforced,
            "passcode_configured": passcode_configured,
            "authenticated": False,
            "read_only": True,
            "scope": "anonymous",
            "role": None,
            "subject": None,
            "max_approval_amount": "0",
        }

    @router.post("/v1/auth/demo-session")
    def create_demo_session(
        request: Request,
        response: Response,
        body: DemoSessionRequest,
    ) -> dict[str, Any]:
        """Validate ``BRIDGE_DEMO_PASSCODE`` and set a signed HttpOnly ``bridge_session`` cookie."""
        configured = os.environ.get(DEMO_PASSCODE_ENV, "").strip()
        if not configured:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Demo passcode authentication is disabled "
                    f"({DEMO_PASSCODE_ENV} is not configured)."
                ),
            )
        provided = body.passcode.strip()
        if not provided or not hmac.compare_digest(
            provided.encode("utf-8"), configured.encode("utf-8")
        ):
            raise HTTPException(
                status_code=401, detail="Invalid demo session passcode."
            )

        profile = DEMO_PERSONA_PROFILES[body.persona]
        try:
            token = mint_user_token(
                profile.subject,
                profile.role,
                max_approval_amount=profile.max_approval_amount,
                scope=INTERACTIVE_SCOPE,
                ttl_seconds=DEMO_SESSION_TTL_SECONDS,
            )
        except SpiffeAuthenticationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        set_session_cookie(
            response,
            request,
            token=token,
            max_age=DEMO_SESSION_TTL_SECONDS,
        )
        return {
            "authenticated": True,
            "read_only": False,
            "scope": INTERACTIVE_SCOPE,
            "persona": profile.persona,
            "role": profile.role,
            "subject": profile.subject,
            "display_name": profile.display_name,
            "max_approval_amount": str(profile.max_approval_amount),
        }

    @router.delete("/v1/auth/demo-session")
    def delete_demo_session(
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        """Clear the ``bridge_session`` cookie to end an interactive demo session."""
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=is_secure_cookie_request(request),
            samesite="strict",
        )
        return {
            "authenticated": False,
            "read_only": True,
            "scope": "anonymous",
        }


__all__ = [
    "DEMO_PASSCODE_ENV",
    "DEMO_PERSONA_PROFILES",
    "DEMO_SESSION_TTL_SECONDS",
    "VIEWER_SESSION_TTL_SECONDS",
    "DemoPersonaProfile",
    "is_secure_cookie_request",
    "issue_viewer_cookie",
    "mutating_auth_dependency",
    "register_demo_session_routes",
    "require_mutating_identity",
    "set_session_cookie",
    "verified_mutating_identity",
]
