"""Authentication backend and ASGI enforcement middleware for the A2A edge (P14-1)."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
    SimpleUser,
)
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from bridge.identity import (
    IAP_AUDIENCE_ENV,
    SPIFFE_SVID_HEADER,
    USER_TOKEN_HEADER,
    SpiffeAuthenticationError,
    is_auth_enforced,
    resolve_expected_svid_algorithm,
    verify_google_oidc_token,
    verify_iap_assertion,
    verify_jwt_svid,
    verify_user_token,
)
from bridge.identity_models import (
    DEFAULT_SPIFFE_AGENT_POLICIES,
    OIDC_ALLOWED_EMAILS_ENV,
    RUNTIME_SA_EMAIL_ENV,
    VIEWER_SCOPES,
    SpiffeAgentPolicy,
    resolve_allowed_oidc_emails,
)

IAP_JWT_ASSERTION_HEADER = "x-goog-iap-jwt-assertion"
IAP_EMAIL_HEADER = "x-goog-authenticated-user-email"
PUBLIC_EDGE_PATHS: frozenset[str] = frozenset(
    {"/.well-known/agent-card.json", "/.well-known/agent.json"}
)


def _jwt_header_typ(token: str) -> str:
    parts = token.strip().split(".")
    if len(parts) != 3:
        return ""
    seg = parts[0]
    padding = "=" * (-len(seg) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(seg + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, TypeError):
        return ""
    return str(header.get("typ") or "").strip() if isinstance(header, dict) else ""


def authenticated_principal(request_or_scope: object) -> str | None:
    """Return the authenticated principal name from a Starlette request or ASGI scope."""
    scope = (
        request_or_scope
        if isinstance(request_or_scope, dict)
        else getattr(request_or_scope, "scope", None)
    )
    if not isinstance(scope, dict):
        return None
    user = scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    name = (
        getattr(user, "display_name", None)
        or getattr(user, "username", None)
        or getattr(user, "user_name", None)
        or ""
    )
    cleaned = str(name).strip()
    return cleaned or None


def edge_auth_error(_conn: HTTPConnection, exc: Exception) -> Response:
    """Convert Starlette ``AuthenticationError`` into an HTTP 401 JSON response."""
    return JSONResponse({"detail": str(exc)}, status_code=401)


class EdgeAuthBackend(AuthenticationBackend):
    """Cryptographically verifies callers on the A2A edge (SVID, user JWT, IAP, or OIDC).

    Never trusts raw identity headers. Missing credentials yield an unauthenticated
    caller (blocked by :class:`EdgeAuthEnforcementMiddleware` when auth is enforced);
    any invalid presented credential fails immediately with HTTP 401.
    """

    def __init__(  # noqa: PLR0913 - keyword-only auth configuration hooks
        self,
        *,
        oidc_verifier: Callable[..., dict[str, Any]] | None = None,
        iap_cert_transport: object = None,
        expected_oidc_audience: str | None = None,
        require_auth: bool = False,
        allowed_oidc_emails: Iterable[str] | str | None = None,
        spiffe_policies: Mapping[str, SpiffeAgentPolicy] | None = None,
    ) -> None:
        self._oidc_verifier = oidc_verifier
        self._iap_cert_transport = iap_cert_transport
        self._expected_oidc_audience = expected_oidc_audience
        self._require_auth = require_auth
        self._allowed_oidc_emails = allowed_oidc_emails
        self._spiffe_policies = spiffe_policies

    def _verify_svid(self, token: str) -> tuple[AuthCredentials, BaseUser]:
        expected_alg, allowed_algs = resolve_expected_svid_algorithm()
        claims = verify_jwt_svid(
            token,
            expected_alg=expected_alg,
            allowed_algorithms=allowed_algs,
            refuse_dev_secret=self._require_auth,
        )
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise AuthenticationError("Verified SPIFFE SVID is missing 'sub'.")
        policies = (
            self._spiffe_policies
            if self._spiffe_policies is not None
            else DEFAULT_SPIFFE_AGENT_POLICIES
        )
        if subject not in policies:
            raise AuthenticationError(
                f"Unregistered SPIFFE agent '{subject}' is not permitted on the A2A edge."
            )
        return AuthCredentials(["authenticated", "svid"]), SimpleUser(subject)

    def _verify_iap(
        self, conn: HTTPConnection, token: str
    ) -> tuple[AuthCredentials, BaseUser]:
        expected_audience = os.environ.get(IAP_AUDIENCE_ENV, "").strip()
        if not expected_audience:
            raise AuthenticationError(
                f"{IAP_AUDIENCE_ENV} must be configured to verify {IAP_JWT_ASSERTION_HEADER}."
            )
        claims = verify_iap_assertion(
            token,
            expected_audience=expected_audience,
            request_transport=self._iap_cert_transport,
        )
        email = str(claims.get("email") or "").strip()
        if not email:
            raise AuthenticationError("Verified IAP assertion is missing 'email'.")
        raw_hdr = conn.headers.get(IAP_EMAIL_HEADER, "").strip()
        hdr_email = raw_hdr.split(":", 1)[1].strip() if ":" in raw_hdr else raw_hdr
        if hdr_email and hdr_email.lower() != email.lower():
            raise AuthenticationError(
                "X-Goog-Authenticated-User-Email does not match verified IAP assertion."
            )
        return AuthCredentials(["authenticated", "iap"]), SimpleUser(email)

    def _authorize_oidc_email(self, claims: dict[str, Any]) -> str:
        if claims.get("email_verified") is not True:
            raise AuthenticationError("OIDC token email is not verified.")
        email = str(claims.get("email") or "").strip()
        if not email:
            raise AuthenticationError("Verified OIDC token is missing 'email'.")
        allowed = resolve_allowed_oidc_emails(self._allowed_oidc_emails)
        if not allowed:
            raise AuthenticationError(
                f"OIDC caller allowlist is empty; configure {OIDC_ALLOWED_EMAILS_ENV} "
                f"or {RUNTIME_SA_EMAIL_ENV}."
            )
        if email.lower() not in allowed:
            raise AuthenticationError(
                f"OIDC caller '{email}' is not authorized for the A2A edge."
            )
        return email

    def _verify_bearer(self, bearer_token: str) -> tuple[AuthCredentials, BaseUser]:
        if _jwt_header_typ(bearer_token) == "JWT+SVID":
            return self._verify_svid(bearer_token)
        audience = (
            self._expected_oidc_audience or os.environ.get("BRIDGE_PUBLIC_URL", "")
        ).strip()
        if not audience:
            raise AuthenticationError(
                "BRIDGE_PUBLIC_URL (or expected_oidc_audience) must be configured "
                "to verify OIDC Bearer tokens."
            )
        if self._oidc_verifier is not None:
            claims = dict(self._oidc_verifier(bearer_token, expected_audience=audience))
        else:
            claims = verify_google_oidc_token(bearer_token, expected_audience=audience)
        email = self._authorize_oidc_email(claims)
        return AuthCredentials(["authenticated", "oidc"]), SimpleUser(email)

    def _verify_user_jwt(self, token: str) -> tuple[AuthCredentials, BaseUser]:
        claims = verify_user_token(token, refuse_dev_secret=self._require_auth)
        token_scope = str(claims.get("scope") or "").strip()
        if token_scope in VIEWER_SCOPES:
            raise AuthenticationError(
                "Read-only or showcase viewer token cannot authenticate to the A2A edge."
            )
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise AuthenticationError("Verified user token is missing 'sub'.")
        return AuthCredentials(["authenticated", "user_jwt"]), SimpleUser(subject)

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        """Authenticate an inbound A2A edge request from verified credentials."""
        try:
            if svid := conn.headers.get(SPIFFE_SVID_HEADER, "").strip():
                return self._verify_svid(svid)
            if user_token := conn.headers.get(USER_TOKEN_HEADER, "").strip():
                return self._verify_user_jwt(user_token)
            if iap_assertion := conn.headers.get(IAP_JWT_ASSERTION_HEADER, "").strip():
                return self._verify_iap(conn, iap_assertion)
            if auth_header := conn.headers.get("authorization", "").strip():
                scheme, _, credential = auth_header.partition(" ")
                if scheme.lower() != "bearer" or not credential.strip():
                    raise AuthenticationError("Authorization header must use Bearer.")
                return self._verify_bearer(credential.strip())
        except AuthenticationError:
            raise
        except SpiffeAuthenticationError as exc:
            raise AuthenticationError(str(exc)) from exc
        except Exception as exc:
            raise AuthenticationError(f"Edge authentication failed: {exc}") from exc
        return None


class EdgeAuthEnforcementMiddleware:
    """Require an authenticated principal on non-public A2A edge routes when enforced."""

    def __init__(self, app: ASGIApp, *, require_auth: bool = False) -> None:
        self._app = app
        self._require_auth = require_auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            path = str(scope.get("path") or "")
            method = str(scope.get("method") or "").upper()
            is_public = method == "GET" and path in PUBLIC_EDGE_PATHS
            if not is_public and is_auth_enforced(self._require_auth):
                if authenticated_principal(scope) is None:
                    response = JSONResponse(
                        {"detail": "Authentication required for A2A edge endpoints."},
                        status_code=401,
                    )
                    await response(scope, receive, send)
                    return
        await self._app(scope, receive, send)


__all__ = [
    "PUBLIC_EDGE_PATHS",
    "EdgeAuthBackend",
    "EdgeAuthEnforcementMiddleware",
    "authenticated_principal",
    "edge_auth_error",
]
