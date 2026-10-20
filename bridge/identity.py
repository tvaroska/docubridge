"""SPIFFE Workload Identity (SVID) & Human On-Behalf-Of (OBO) Compound Identity.

Implements enterprise identity and multi-tenant isolation across Zones 4, 5, and 6:

1. **Cryptographic Workload / Agent Identity via SPIFFE (`act`)**:
   - Validates SPIFFE IDs (`spiffe://<trust_domain>/<zone>/<agent_name>`) against
     the governed Knowledge Catalog Agent Registry (`DEFAULT_SPIFFE_AGENT_POLICIES`).
   - Supports signed **JWT-SVID** verification (`X-Bridge-Spiffe-Svid: Bearer <jwt>`)
     and explicit SPIFFE header binding (`X-Bridge-Spiffe-Id`), blocking unregistered
     "Shadow AI" agents (`ShadowAgentError` -> HTTP 403) and cross-zone privilege
     escalation.
2. **Human & Multi-Tenant Principal (`sub`)**:
   - Represents the authenticated human caller (`UserPrincipal`) with role
     (`broker`, `claims`, `underwriter`, `executive`, `client`, `carrier`),
     organization/tenant scope (`tenant_id`, `allowed_entities`), carrier
     affiliation (`carrier_slug`), and financial approval authority limit
     (`max_approval_amount`).
   - Supports signed OIDC/User JWT tokens (`X-Bridge-User-Token: Bearer <jwt>`),
     Cloud Run IAP headers (`X-Goog-Authenticated-User-Email`), and scoped tenant/
     carrier headers (`X-Bridge-Tenant-Id`, `X-Bridge-Allowed-Entities`,
     `X-Bridge-Carrier-Slug`, `X-Bridge-Max-Approval`).
3. **Compound Identity (`sub` + `act`, RFC 8693 Token Exchange)**:
   - Enforces **Least-Privilege Intersection**:
     `Effective Access = Agent(SPIFFE Policy) ∩ Human(Principal Policy) ∩ Tenant Scope`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from bridge.telemetry import trace_step

CatalogRoleLiteral = Literal[
    "broker",
    "claims",
    "underwriter",
    "executive",
    "client",
    "carrier",
]
DeliveryChannelLiteral = Literal["gemini_app", "custom_web"]

DEFAULT_SPIFFE_TRUST_DOMAIN = "agents.docubridge.io"
SPIFFE_TRUST_DOMAIN_ENV = "BRIDGE_SPIFFE_TRUST_DOMAIN"
SPIFFE_REQUIRE_SVID_ENV = "BRIDGE_SPIFFE_REQUIRE_SVID"
SPIFFE_SVID_SECRET_ENV = "BRIDGE_SPIFFE_SVID_SECRET"
USER_JWT_SECRET_ENV = "BRIDGE_USER_JWT_SECRET"
DEFAULT_DEV_SVID_SECRET = "docubridge-spiffe-svid-signing-key-v1"

SPIFFE_SVID_HEADER = "x-bridge-spiffe-svid"
SPIFFE_ID_HEADER = "x-bridge-spiffe-id"
USER_TOKEN_HEADER = "x-bridge-user-token"
TENANT_ID_HEADER = "x-bridge-tenant-id"
ALLOWED_ENTITIES_HEADER = "x-bridge-allowed-entities"
CARRIER_SLUG_HEADER = "x-bridge-carrier-slug"
MAX_APPROVAL_HEADER = "x-bridge-max-approval"

_SPIFFE_URI_RE = re.compile(
    r"^spiffe://(?P<trust_domain>[a-z0-9.-]+)/(?P<path>[a-z0-9._/-]+)$"
)


class SpiffeAuthenticationError(PermissionError):
    """Raised when a SPIFFE SVID or user token is missing or invalid (HTTP 401)."""


class ShadowAgentError(PermissionError):
    """Raised when an unregistered or untrusted SPIFFE agent attempts access (HTTP 403)."""


class TenantIsolationError(PermissionError):
    """Raised when a tenant/client or carrier attempts cross-tenant access (HTTP 403)."""


class SpiffeId(BaseModel):
    """Parsed, validated SPIFFE ID (`spiffe://<trust_domain>/<path>`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str
    trust_domain: str
    path: str

    @classmethod
    def parse(cls, raw_uri: str) -> SpiffeId:
        cleaned = raw_uri.strip()
        match = _SPIFFE_URI_RE.fullmatch(cleaned)
        if match is None or "//" in match.group("path") or match.group("path").endswith("/"):
            raise SpiffeAuthenticationError(
                f"Malformed SPIFFE ID '{raw_uri}'. Expected 'spiffe://<trust-domain>/<path>'."
            )
        return cls(
            uri=cleaned,
            trust_domain=match.group("trust_domain"),
            path=match.group("path"),
        )


class SpiffeAgentPolicy(BaseModel):
    """Zone 6 Knowledge Catalog registration entry for a vetted Domain Agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spiffe_id: str
    display_name: str
    security_zone: Literal["internal", "external"]
    allowed_channels: tuple[DeliveryChannelLiteral, ...]
    allowed_roles: tuple[CatalogRoleLiteral, ...]
    allowed_views: tuple[str, ...]
    allow_vault_promotion: bool = False

    @model_validator(mode="after")
    def _validate_spiffe(self) -> SpiffeAgentPolicy:
        SpiffeId.parse(self.spiffe_id)
        return self


DEFAULT_SPIFFE_AGENT_POLICIES: dict[str, SpiffeAgentPolicy] = {
    "spiffe://agents.docubridge.io/internal/credit-officer-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/internal/credit-officer-agent",
        display_name="Internal Credit & Loan Underwriting Agent",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("underwriter", "broker", "executive"),
        allowed_views=(
            "v_underwriter_exposure",
            "v_broker_placement",
            "v_claims_coverage",
        ),
        allow_vault_promotion=True,
    ),
    "spiffe://agents.docubridge.io/internal/broker-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/internal/broker-agent",
        display_name="Internal Commercial Placement & Deal Desk Agent",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("broker", "executive"),
        allowed_views=(
            "v_broker_placement",
            "v_claims_coverage",
            "v_underwriter_exposure",
        ),
        allow_vault_promotion=True,
    ),
    "spiffe://agents.docubridge.io/internal/claims-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/internal/claims-agent",
        display_name="Internal Covenant & Claims Review Agent",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("claims", "broker", "executive"),
        allowed_views=("v_claims_coverage", "v_broker_placement"),
        allow_vault_promotion=False,
    ),
    "spiffe://agents.docubridge.io/internal/underwriter-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/internal/underwriter-agent",
        display_name="Internal Technical Underwriting & Risk Agent",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("underwriter", "broker", "executive"),
        allowed_views=(
            "v_underwriter_exposure",
            "v_broker_placement",
            "v_claims_coverage",
        ),
        allow_vault_promotion=True,
    ),
    "spiffe://agents.docubridge.io/portal/external-portal-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/portal/external-portal-agent",
        display_name="External Client, Borrower & Partner Portal Agent",
        security_zone="external",
        allowed_channels=("custom_web",),
        allowed_roles=("client", "carrier"),
        allowed_views=("v_broker_placement", "v_claims_coverage"),
        allow_vault_promotion=False,
    ),
    "spiffe://agents.docubridge.io/ingest/docubridge-collector": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/ingest/docubridge-collector",
        display_name="DocuBridge Intake & Consolidation Collector",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("broker", "claims", "underwriter", "executive"),
        allowed_views=(
            "v_broker_placement",
            "v_claims_coverage",
            "v_underwriter_exposure",
        ),
        allow_vault_promotion=True,
    ),
}

DEFAULT_ROLE_APPROVAL_LIMITS: dict[CatalogRoleLiteral, Decimal] = {
    "broker": Decimal("10000000.00"),
    "claims": Decimal("5000000.00"),
    "underwriter": Decimal("25000000.00"),
    "executive": Decimal("100000000.00"),
    "client": Decimal("0.00"),
    "carrier": Decimal("0.00"),
}


class UserPrincipal(BaseModel):
    """Authenticated human principal (`sub`) with tenant & carrier row-level scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    role: CatalogRoleLiteral
    tenant_id: str | None = None
    allowed_entities: tuple[str, ...] = ()
    carrier_slug: str | None = None
    max_approval_amount: Decimal | None = None
    authenticated_via: Literal["jwt", "iap", "header", "query_dev"] = "query_dev"

    def effective_approval_limit(self) -> Decimal:
        if self.max_approval_amount is not None:
            return self.max_approval_amount
        return DEFAULT_ROLE_APPROVAL_LIMITS.get(self.role, Decimal("0.00"))


class CompoundIdentity(BaseModel):
    """RFC 8693 Compound Identity combining Human Principal (`sub`) + Agent SPIFFE (`act`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: UserPrincipal
    agent_spiffe_id: str | None = None
    agent_policy: SpiffeAgentPolicy | None = None
    svid_verified: bool = False
    audience: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:
        raise SpiffeAuthenticationError("Malformed JWT base64url segment.") from exc


def mint_jwt_svid(
    spiffe_id: str,
    *,
    audience: str = "docubridge",
    obo_subject: str | None = None,
    obo_role: CatalogRoleLiteral | None = None,
    tenant_id: str | None = None,
    allowed_entities: tuple[str, ...] = (),
    carrier_slug: str | None = None,
    max_approval_amount: Decimal | str | None = None,
    ttl_seconds: int = 3600,
    secret: str | None = None,
    now_epoch: int | None = None,
) -> str:
    """Mint a signed SPIFFE JWT-SVID (with optional RFC 8693 OBO `act`/`obo` claims)."""
    parsed = SpiffeId.parse(spiffe_id)
    signing_secret = (
        secret
        or os.environ.get(SPIFFE_SVID_SECRET_ENV, "").strip()
        or DEFAULT_DEV_SVID_SECRET
    )
    now = int(now_epoch if now_epoch is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT+SVID", "kid": "spiffe-root-v1"}
    payload: dict[str, Any] = {
        "iss": f"https://{parsed.trust_domain}",
        "sub": parsed.uri,
        "aud": [audience],
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if obo_subject or obo_role or tenant_id or allowed_entities or carrier_slug or max_approval_amount is not None:
        obo_block: dict[str, Any] = {}
        if obo_subject:
            obo_block["sub"] = obo_subject
        if obo_role:
            obo_block["role"] = obo_role
        if tenant_id:
            obo_block["tenant_id"] = tenant_id
        if allowed_entities:
            obo_block["allowed_entities"] = list(allowed_entities)
        if carrier_slug:
            obo_block["carrier_slug"] = carrier_slug
        if max_approval_amount is not None:
            obo_block["max_approval_amount"] = str(max_approval_amount)
        payload["obo"] = obo_block
        payload["act"] = {"sub": parsed.uri}

    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(
        signing_secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


def verify_jwt_svid(
    token: str,
    *,
    expected_trust_domain: str | None = None,
    expected_audience: str | None = "docubridge",
    secret: str | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Cryptographically verify a SPIFFE JWT-SVID and return its verified claims."""
    with trace_step(
        "docubridge.identity.verify_jwt_svid",
        {
            "docubridge.zone": "6_spiffe_workload_identity",
            "docubridge.expected_audience": expected_audience,
        },
    ) as span:
        raw = token.strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        parts = raw.split(".")
        if len(parts) != 3:
            raise SpiffeAuthenticationError("Malformed JWT-SVID: expected 3 segments.")

        h_b64, p_b64, s_b64 = parts
        signing_secret = (
            secret
            or os.environ.get(SPIFFE_SVID_SECRET_ENV, "").strip()
            or DEFAULT_DEV_SVID_SECRET
        )
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        expected_sig = hmac.new(
            signing_secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(s_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise SpiffeAuthenticationError("Invalid JWT-SVID cryptographic signature.")

        try:
            header = json.loads(_b64url_decode(h_b64).decode("utf-8"))
            claims = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        except Exception as exc:
            raise SpiffeAuthenticationError("Invalid JWT-SVID JSON payload.") from exc

        if header.get("alg") != "HS256":
            raise SpiffeAuthenticationError(
                f"Unsupported JWT-SVID algorithm '{header.get('alg')}'."
            )

        sub = claims.get("sub")
        if not isinstance(sub, str):
            raise SpiffeAuthenticationError("JWT-SVID missing 'sub' SPIFFE ID claim.")
        parsed_id = SpiffeId.parse(sub)
        span.set_attribute("docubridge.agent_spiffe_id", parsed_id.uri)
        span.set_attribute("docubridge.trust_domain", parsed_id.trust_domain)

        trust_domain = (
            expected_trust_domain
            or os.environ.get(SPIFFE_TRUST_DOMAIN_ENV, "").strip()
            or DEFAULT_SPIFFE_TRUST_DOMAIN
        )
        if parsed_id.trust_domain != trust_domain:
            raise ShadowAgentError(
                f"Untrusted SPIFFE trust domain '{parsed_id.trust_domain}' "
                f"(expected '{trust_domain}')."
            )

        now = int(now_epoch if now_epoch is not None else time.time())
        exp = claims.get("exp")
        if not isinstance(exp, int) or now > exp + 30:
            raise SpiffeAuthenticationError("JWT-SVID has expired.")

        if expected_audience is not None:
            aud = claims.get("aud")
            aud_list = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
            if expected_audience not in aud_list:
                raise SpiffeAuthenticationError(
                    f"JWT-SVID audience mismatch (expected '{expected_audience}')."
                )

        obo = claims.get("obo")
        if isinstance(obo, dict):
            if obo.get("sub"):
                span.set_attribute("docubridge.obo_subject", str(obo["sub"]))
            if obo.get("role"):
                span.set_attribute("docubridge.obo_role", str(obo["role"]))
        span.set_attribute("docubridge.svid_verified", True)
        return claims


def authorize_spiffe_agent(
    spiffe_id: str,
    *,
    channel: DeliveryChannelLiteral,
    role: CatalogRoleLiteral,
    target_view: str,
    agent_policies: dict[str, SpiffeAgentPolicy] | None = None,
    expected_trust_domain: str | None = None,
) -> SpiffeAgentPolicy:
    """Verify that a SPIFFE workload identity is registered and authorized for the request."""
    with trace_step(
        "docubridge.identity.authorize_spiffe_agent",
        {
            "docubridge.zone": "6_spiffe_workload_identity",
            "docubridge.agent_spiffe_id": spiffe_id,
            "docubridge.channel": channel,
            "docubridge.role": role,
            "docubridge.target_view": target_view,
        },
    ) as span:
        parsed = SpiffeId.parse(spiffe_id)
        trust_domain = (
            expected_trust_domain
            or os.environ.get(SPIFFE_TRUST_DOMAIN_ENV, "").strip()
            or DEFAULT_SPIFFE_TRUST_DOMAIN
        )
        if parsed.trust_domain != trust_domain:
            raise ShadowAgentError(
                f"SPIFFE ID '{spiffe_id}' belongs to untrusted domain '{parsed.trust_domain}' "
                f"(expected '{trust_domain}')."
            )

        registry = (
            agent_policies
            if agent_policies is not None
            else DEFAULT_SPIFFE_AGENT_POLICIES
        )
        policy = registry.get(parsed.uri)
        if policy is None:
            raise ShadowAgentError(
                f"Unregistered SPIFFE agent identity '{parsed.uri}' blocked by Knowledge Catalog "
                "(Shadow AI protection)."
            )

        if channel not in policy.allowed_channels:
            raise PermissionError(
                f"SPIFFE agent '{parsed.uri}' (zone={policy.security_zone}) is not permitted "
                f"on channel '{channel}' (allowed: {', '.join(policy.allowed_channels)})."
            )
        if role not in policy.allowed_roles:
            raise PermissionError(
                f"SPIFFE agent '{parsed.uri}' is not authorized to act on behalf of role "
                f"'{role}' (allowed: {', '.join(policy.allowed_roles)})."
            )
        if target_view not in policy.allowed_views:
            raise PermissionError(
                f"SPIFFE agent '{parsed.uri}' is not authorized to query view '{target_view}' "
                f"(allowed: {', '.join(policy.allowed_views)})."
            )
        span.set_attribute("docubridge.security_zone", policy.security_zone)
        span.set_attribute("docubridge.authorized", True)
        return policy


def authorize_entity_access(
    principal: UserPrincipal,
    entity_id: str,
) -> None:
    """Enforce row-level tenant isolation on ``entity_id`` for external principals."""
    with trace_step(
        "docubridge.identity.authorize_entity_access",
        {
            "docubridge.zone": "6_spiffe_workload_identity",
            "docubridge.subject": principal.subject,
            "docubridge.role": principal.role,
            "docubridge.tenant_id": principal.tenant_id,
            "docubridge.entity_id": entity_id,
        },
    ) as span:
        if principal.role == "client":
            if principal.allowed_entities and entity_id not in principal.allowed_entities:
                raise TenantIsolationError(
                    f"Tenant isolation violation: client principal '{principal.subject}' "
                    f"(tenant='{principal.tenant_id or 'restricted'}') is not authorized "
                    f"to access entity '{entity_id}'."
                )
            if (
                not principal.allowed_entities
                and principal.tenant_id
                and principal.tenant_id.lower() not in entity_id.lower()
            ):
                raise TenantIsolationError(
                    f"Tenant isolation violation: entity '{entity_id}' does not belong to "
                    f"client tenant '{principal.tenant_id}'."
                )
        elif principal.allowed_entities and entity_id not in principal.allowed_entities:
            raise TenantIsolationError(
                f"Principal '{principal.subject}' is not authorized to access entity '{entity_id}'."
            )
        span.set_attribute("docubridge.tenant_access_allowed", True)


def filter_quotes_for_principal(
    quotes: tuple[Any, ...] | list[Any],
    principal: UserPrincipal,
    *,
    allow_competitor_quotes: bool,
) -> list[Any]:
    """Filter carrier quotes by the caller's carrier affiliation or competitor policy."""
    items = list(quotes)
    if principal.role == "carrier" and principal.carrier_slug:
        slug = principal.carrier_slug.strip().lower()
        return [
            q
            for q in items
            if slug in getattr(q, "issuer", "").lower()
            or slug in getattr(q, "doc_id", "").lower()
        ]
    if not allow_competitor_quotes:
        return items[:1]
    return items


def parse_decimal_header(raw: str | None) -> Decimal | None:
    if not raw or not raw.strip():
        return None
    try:
        return Decimal(raw.strip())
    except InvalidOperation as exc:
        raise SpiffeAuthenticationError(
            f"Invalid numeric approval limit '{raw}'."
        ) from exc


__all__ = [
    "ALLOWED_ENTITIES_HEADER",
    "CARRIER_SLUG_HEADER",
    "DEFAULT_DEV_SVID_SECRET",
    "DEFAULT_ROLE_APPROVAL_LIMITS",
    "DEFAULT_SPIFFE_AGENT_POLICIES",
    "DEFAULT_SPIFFE_TRUST_DOMAIN",
    "MAX_APPROVAL_HEADER",
    "SPIFFE_ID_HEADER",
    "SPIFFE_REQUIRE_SVID_ENV",
    "SPIFFE_SVID_HEADER",
    "SPIFFE_SVID_SECRET_ENV",
    "SPIFFE_TRUST_DOMAIN_ENV",
    "TENANT_ID_HEADER",
    "USER_JWT_SECRET_ENV",
    "USER_TOKEN_HEADER",
    "CompoundIdentity",
    "ShadowAgentError",
    "SpiffeAgentPolicy",
    "SpiffeAuthenticationError",
    "SpiffeId",
    "TenantIsolationError",
    "UserPrincipal",
    "authorize_entity_access",
    "authorize_spiffe_agent",
    "filter_quotes_for_principal",
    "mint_jwt_svid",
    "parse_decimal_header",
    "verify_jwt_svid",
]
