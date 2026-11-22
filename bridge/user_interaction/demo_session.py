"""Option 2B signed-cookie sessions, read-only viewer cookies, and passcode exchange routes."""

from __future__ import annotations

import hmac
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.params import Depends as DependsParam

from bridge.identity import (
    DEFAULT_ROLE_APPROVAL_LIMITS,
    DEFAULT_SPIFFE_AGENT_POLICIES,
    CompoundIdentity,
    ShadowAgentError,
    SpiffeAuthenticationError,
    mint_user_token,
    verify_google_oidc_token,
    verify_user_token,
)
from bridge.knowledge_catalog import CatalogRole
from bridge.user_interaction.auth import (
    _VALID_ROLES,
    INTERACTIVE_SCOPE,
    READ_ONLY_SCOPE,
    SESSION_COOKIE_NAME,
    SHOWCASE_VIEWER_SCOPE,
    AuthenticationRequiredError,
    is_auth_enforced,
    resolve_compound_identity,
)
from bridge.user_interaction.schemas import DemoPersonaLiteral, DemoSessionRequest

DEMO_PASSCODE_ENV = "BRIDGE_DEMO_PASSCODE"
RUNTIME_SA_EMAIL_ENV = "BRIDGE_RUNTIME_SA_EMAIL"
MIN_DEMO_PASSCODE_LENGTH = 16
PASSCODE_BACKOFF_THRESHOLD = 3
PASSCODE_BACKOFF_BASE_S = 2.0
PASSCODE_BACKOFF_MAX_S = 300.0
VIEWER_SESSION_TTL_SECONDS = 3600
DEMO_SESSION_TTL_SECONDS = 28800
_VIEWER_SCOPES = frozenset({READ_ONLY_SCOPE, SHOWCASE_VIEWER_SCOPE})


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
    """Auto-issue a signed showcase-only viewer cookie unless an interactive cookie is active.

    The minted cookie carries ``scope="showcase_read"``, which is accepted only on
    public showcase read routes (``/v1/demo/*``) and refused (HTTP 403) on operator
    read routes and all mutating endpoints.
    """
    existing = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if existing:
        try:
            claims = verify_user_token(existing)
            if str(claims.get("scope") or "").strip() not in _VIEWER_SCOPES:
                return False
        except SpiffeAuthenticationError:
            pass
    try:
        token = mint_user_token(
            "viewer:demo",
            "broker",
            max_approval_amount=Decimal("0"),
            scope=SHOWCASE_VIEWER_SCOPE,
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


def _validate_spiffe_mutating_policy(
    compound: CompoundIdentity, *, allow_external_roles: bool
) -> None:
    if compound.agent_spiffe_id is None:
        return
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


def require_mutating_identity(  # noqa: PLR0913 - public mutating route authorization gate
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
        _validate_spiffe_mutating_policy(
            compound, allow_external_roles=allow_external_roles
        )
        return compound
    except AuthenticationRequiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def verified_mutating_identity(request: Request) -> CompoundIdentity:
    """Return the ``CompoundIdentity`` already resolved by ``mutating_auth_dependency``."""
    return cast(CompoundIdentity, request.state.mutating_compound_identity)


def mutating_auth_dependency(  # noqa: PLR0913 - public mutating auth dependency builder
    *,
    default_role: CatalogRole | None = "broker",
    default_role_fn: Callable[[Request], CatalogRole | None] | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    allow_external_roles: bool = True,
    external_role_error: str | None = None,
) -> DependsParam:
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

    return cast(DependsParam, Depends(_gate))


def read_auth_dependency(
    *,
    default_role: CatalogRole | None = "broker",
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    allow_external_roles: bool = True,
    allow_showcase_viewer: bool = False,
) -> DependsParam:
    """FastAPI route dependency enforcing read-route auth when enforcement is active."""

    def _gate(request: Request) -> CompoundIdentity:
        raw_role = (request.query_params.get("role") or "").strip().lower()
        query_role: CatalogRole | None = (
            cast(CatalogRole, raw_role) if raw_role in _VALID_ROLES else None
        )
        try:
            compound = resolve_compound_identity(
                request,
                query_role=query_role,
                default_role=default_role,
                principal_roles=principal_roles,
                require_auth=require_auth,
                require_mutating=False,
                allow_showcase_viewer=allow_showcase_viewer,
            )
            if not allow_external_roles and compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' is not permitted on this internal read endpoint."
                )
            return compound
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return cast(DependsParam, Depends(_gate))


def _resolve_email_oidc_verifier(
    request: Request,
) -> Callable[..., dict[str, Any]] | None:
    scope = getattr(request, "scope", None)
    app = scope.get("app") if isinstance(scope, dict) else None
    state = getattr(app, "state", None)
    verifier = getattr(state, "oidc_verifier", None)
    return verifier if callable(verifier) else None


def _verify_email_scheduler_oidc(request: Request, auth_header: str) -> dict[str, Any]:
    scheme, _, credential = auth_header.partition(" ")
    token = credential.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Authorization header must use Bearer OIDC token.",
        )
    verifier = _resolve_email_oidc_verifier(request)
    audience = (
        os.environ.get("BRIDGE_BOARD_PUBLIC_URL", "").strip()
        or os.environ.get("BRIDGE_PUBLIC_URL", "").strip()
    )
    if not audience:
        raise HTTPException(
            status_code=401,
            detail=(
                "BRIDGE_BOARD_PUBLIC_URL or BRIDGE_PUBLIC_URL must be configured "
                "to verify scheduler OIDC tokens."
            ),
        )
    try:
        claims = (
            dict(verifier(token, expected_audience=audience))
            if verifier is not None
            else verify_google_oidc_token(token, expected_audience=audience)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid scheduler OIDC token: {exc}",
        ) from exc
    if claims.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="OIDC token email is not verified.")
    expected_sa = os.environ.get(RUNTIME_SA_EMAIL_ENV, "").strip()
    actual_email = str(claims.get("email") or "").strip()
    if not expected_sa or actual_email.lower() != expected_sa.lower():
        raise HTTPException(
            status_code=403,
            detail=(
                f"Caller identity '{actual_email or 'unknown'}' is not authorized "
                f"for email drain routes (expected {RUNTIME_SA_EMAIL_ENV})."
            ),
        )
    return claims


def _authorize_email_operator(
    request: Request,
    *,
    principal_roles: dict[str, CatalogRole] | None,
    require_auth: bool,
    allow_operator_read: bool,
) -> CompoundIdentity:
    if not allow_operator_read:
        return require_mutating_identity(
            request,
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=False,
        )
    try:
        compound = resolve_compound_identity(
            request,
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            require_mutating=False,
            allow_showcase_viewer=False,
        )
        if compound.user.role in ("client", "carrier"):
            raise PermissionError(
                f"External role '{compound.user.role}' is not permitted on email outcome routes."
            )
        return compound
    except AuthenticationRequiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def email_drain_auth_dependency(
    *,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    allow_operator_read: bool = False,
) -> DependsParam:
    """Gate ``POST /email/drain`` and ``GET /email/outcomes/{key}`` under auth enforcement."""

    def _gate(request: Request) -> object:
        if not is_auth_enforced(require_auth):
            return None
        auth_header = request.headers.get("authorization", "").strip()
        if auth_header:
            return _verify_email_scheduler_oidc(request, auth_header)
        return _authorize_email_operator(
            request,
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_operator_read=allow_operator_read,
        )

    return cast(DependsParam, Depends(_gate))


class _PasscodeBackoffTracker:
    """Thread-safe exponential back-off tracker for failed demo passcode exchanges."""

    _MAX_TRACKED_CLIENTS = 1024
    _MAX_EXPONENT = 20

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
        client = getattr(request, "client", None)
        host = getattr(client, "host", None)
        return str(host or "unknown")

    def _evict_if_full(self, now: float) -> None:
        if len(self._state) < self._MAX_TRACKED_CLIENTS:
            return
        expired = [
            k
            for k, (_fails, unlock_at) in self._state.items()
            if k != "__global__" and unlock_at <= now
        ]
        for key in expired:
            self._state.pop(key, None)
        while len(self._state) >= self._MAX_TRACKED_CLIENTS:
            victim = next((k for k in self._state if k != "__global__"), None)
            if victim is None:
                break
            self._state.pop(victim, None)

    def check_locked(self, request: Request) -> int | None:
        now = time.monotonic()
        client_key = self._client_key(request)
        with self._lock:
            retry = 0
            for key in (client_key, "__global__"):
                entry = self._state.get(key)
                if entry is not None and entry[1] > now:
                    retry = max(retry, max(1, math.ceil(entry[1] - now)))
            return retry if retry > 0 else None

    def record_failure(self, request: Request) -> None:
        now = time.monotonic()
        client_key = self._client_key(request)
        with self._lock:
            if client_key not in self._state:
                self._evict_if_full(now)
            for key in (client_key, "__global__"):
                failures, _ = self._state.get(key, (0, 0.0))
                failures += 1
                unlock_at = 0.0
                if failures >= PASSCODE_BACKOFF_THRESHOLD:
                    exponent = min(
                        failures - PASSCODE_BACKOFF_THRESHOLD, self._MAX_EXPONENT
                    )
                    delay = min(
                        PASSCODE_BACKOFF_MAX_S,
                        PASSCODE_BACKOFF_BASE_S * (2**exponent),
                    )
                    unlock_at = now + delay
                self._state[key] = (failures, unlock_at)

    def reset(self, request: Request) -> None:
        client_key = self._client_key(request)
        with self._lock:
            self._state.pop(client_key, None)
            self._state.pop("__global__", None)


def _exchange_demo_passcode(
    request: Request,
    response: Response,
    body: DemoSessionRequest,
    backoff: _PasscodeBackoffTracker,
) -> dict[str, Any]:
    configured = os.environ.get(DEMO_PASSCODE_ENV, "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Demo passcode authentication is disabled "
                f"({DEMO_PASSCODE_ENV} is not configured)."
            ),
        )
    if len(configured) < MIN_DEMO_PASSCODE_LENGTH:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{DEMO_PASSCODE_ENV} does not meet minimum length "
                f"({MIN_DEMO_PASSCODE_LENGTH} characters)."
            ),
        )
    retry_after = backoff.check_locked(request)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many failed demo passcode attempts; retry later.",
            headers={"Retry-After": str(retry_after)},
        )
    provided = body.passcode.strip()
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), configured.encode("utf-8")
    ):
        backoff.record_failure(request)
        raise HTTPException(status_code=401, detail="Invalid demo session passcode.")
    backoff.reset(request)

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


def register_demo_session_routes(
    router: APIRouter,
    *,
    require_auth: bool = False,
) -> None:
    """Register Option 2B ``/v1/auth/demo-session`` passcode exchange and logout endpoints."""
    backoff = _PasscodeBackoffTracker()

    @router.get("/v1/auth/demo-session")
    def get_demo_session_status(request: Request) -> dict[str, Any]:
        """Report non-sensitive session state (auth enforcement, active persona, read-only vs interactive)."""
        enforced = is_auth_enforced(require_auth)
        configured = os.environ.get(DEMO_PASSCODE_ENV, "").strip()
        passcode_configured = len(configured) >= MIN_DEMO_PASSCODE_LENGTH
        raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
        if raw_cookie:
            try:
                claims = verify_user_token(raw_cookie)
                scope = str(claims.get("scope") or INTERACTIVE_SCOPE)
                return {
                    "auth_enforced": enforced,
                    "passcode_configured": passcode_configured,
                    "authenticated": True,
                    "read_only": scope in _VIEWER_SCOPES,
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
        return _exchange_demo_passcode(request, response, body, backoff)

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
    "MIN_DEMO_PASSCODE_LENGTH",
    "PASSCODE_BACKOFF_BASE_S",
    "PASSCODE_BACKOFF_MAX_S",
    "PASSCODE_BACKOFF_THRESHOLD",
    "RUNTIME_SA_EMAIL_ENV",
    "VIEWER_SESSION_TTL_SECONDS",
    "DemoPersonaProfile",
    "email_drain_auth_dependency",
    "is_secure_cookie_request",
    "issue_viewer_cookie",
    "mutating_auth_dependency",
    "read_auth_dependency",
    "register_demo_session_routes",
    "require_mutating_identity",
    "set_session_cookie",
    "verified_mutating_identity",
]
