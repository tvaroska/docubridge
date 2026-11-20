"""SPIFFE workload identity and human On-Behalf-Of (OBO) data models and policy constants."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

CatalogRoleLiteral = Literal[
    "broker",
    "claims",
    "underwriter",
    "executive",
    "client",
    "carrier",
]
DeliveryChannelLiteral = Literal["gemini_app", "custom_web"]
AuthViaLiteral = Literal["jwt", "cookie", "iap", "header", "query_dev"]

DEFAULT_SPIFFE_TRUST_DOMAIN = "agents.docubridge.io"
SPIFFE_TRUST_DOMAIN_ENV = "BRIDGE_SPIFFE_TRUST_DOMAIN"
SPIFFE_REQUIRE_SVID_ENV = "BRIDGE_SPIFFE_REQUIRE_SVID"
SPIFFE_SVID_SECRET_ENV = "BRIDGE_SPIFFE_SVID_SECRET"  # noqa: S105 - env var name
SPIFFE_PRIVATE_KEY_PEM_ENV = "BRIDGE_SPIFFE_PRIVATE_KEY_PEM"
SPIFFE_PUBLIC_KEY_PEM_ENV = "BRIDGE_SPIFFE_PUBLIC_KEY_PEM"
USER_JWT_SECRET_ENV = "BRIDGE_USER_JWT_SECRET"  # noqa: S105 - env var name
DEFAULT_DEV_SVID_SECRET = "docubridge-spiffe-svid-signing-key-v1"  # noqa: S105 - dev-only fallback refused under K_SERVICE
SvidAlgorithmLiteral = Literal["HS256", "RS256", "ES256"]
ALLOWED_SVID_ALGORITHMS: frozenset[str] = frozenset({"HS256", "RS256", "ES256"})
EXTERNAL_COUNTERPARTY_TRUST_DOMAINS: frozenset[str] = frozenset(
    {"agents.fnb.example", "agents.fnb.example.com", "external.docubridge.io"}
)

SPIFFE_SVID_HEADER = "x-bridge-spiffe-svid"
SPIFFE_ID_HEADER = "x-bridge-spiffe-id"
USER_TOKEN_HEADER = "x-bridge-user-token"  # noqa: S105 - HTTP header name
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
        """Parse and validate a `spiffe://<trust-domain>/<path>` URI string."""
        cleaned = raw_uri.strip()
        match = _SPIFFE_URI_RE.fullmatch(cleaned)
        if (
            match is None
            or "//" in match.group("path")
            or match.group("path").endswith("/")
        ):
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


_ALL_INTERNAL_VIEWS: tuple[str, ...] = (
    "v_underwriter_exposure",
    "v_broker_placement",
    "v_claims_coverage",
)

DEFAULT_SPIFFE_AGENT_POLICIES: dict[str, SpiffeAgentPolicy] = {
    "spiffe://agents.docubridge.io/internal/credit-officer-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/internal/credit-officer-agent",
        display_name="Internal Credit & Loan Underwriting Agent",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("underwriter", "broker", "executive"),
        allowed_views=_ALL_INTERNAL_VIEWS,
        allow_vault_promotion=True,
    ),
    "spiffe://agents.docubridge.io/internal/broker-agent": SpiffeAgentPolicy(
        spiffe_id="spiffe://agents.docubridge.io/internal/broker-agent",
        display_name="Internal Commercial Placement & Deal Desk Agent",
        security_zone="internal",
        allowed_channels=("gemini_app",),
        allowed_roles=("broker", "executive"),
        allowed_views=_ALL_INTERNAL_VIEWS,
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
        allowed_views=_ALL_INTERNAL_VIEWS,
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
        allowed_views=_ALL_INTERNAL_VIEWS,
        allow_vault_promotion=True,
    ),
    **{
        f"spiffe://{td}/external/fnb-payoff-agent": SpiffeAgentPolicy(
            spiffe_id=f"spiffe://{td}/external/fnb-payoff-agent",
            display_name="FNB External Counterparty Payoff Agent",
            security_zone="external",
            allowed_channels=("custom_web",),
            allowed_roles=("client", "carrier"),
            allowed_views=("v_broker_placement",),
            allow_vault_promotion=False,
        )
        for td in ("agents.fnb.example", "agents.fnb.example.com")
    },
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
    authenticated_via: AuthViaLiteral = "query_dev"
    scope: str | None = None

    def effective_approval_limit(self) -> Decimal:
        """Return the maximum monetary approval limit granted to this user principal."""
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


__all__ = [
    "ALLOWED_ENTITIES_HEADER",
    "ALLOWED_SVID_ALGORITHMS",
    "CARRIER_SLUG_HEADER",
    "DEFAULT_DEV_SVID_SECRET",
    "DEFAULT_ROLE_APPROVAL_LIMITS",
    "DEFAULT_SPIFFE_AGENT_POLICIES",
    "DEFAULT_SPIFFE_TRUST_DOMAIN",
    "EXTERNAL_COUNTERPARTY_TRUST_DOMAINS",
    "MAX_APPROVAL_HEADER",
    "SPIFFE_ID_HEADER",
    "SPIFFE_PRIVATE_KEY_PEM_ENV",
    "SPIFFE_PUBLIC_KEY_PEM_ENV",
    "SPIFFE_REQUIRE_SVID_ENV",
    "SPIFFE_SVID_HEADER",
    "SPIFFE_SVID_SECRET_ENV",
    "SPIFFE_TRUST_DOMAIN_ENV",
    "TENANT_ID_HEADER",
    "USER_JWT_SECRET_ENV",
    "USER_TOKEN_HEADER",
    "AuthViaLiteral",
    "CatalogRoleLiteral",
    "CompoundIdentity",
    "DeliveryChannelLiteral",
    "ShadowAgentError",
    "SpiffeAgentPolicy",
    "SpiffeAuthenticationError",
    "SpiffeId",
    "SvidAlgorithmLiteral",
    "TenantIsolationError",
    "UserPrincipal",
]
