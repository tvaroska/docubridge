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
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from bridge.identity_iap import (
    is_auth_enforced as is_auth_enforced,
)
from bridge.identity_iap import (
    is_dev_secret_refused as is_dev_secret_refused,
)
from bridge.identity_iap import (
    resolve_expected_svid_algorithm as resolve_expected_svid_algorithm,
)
from bridge.identity_iap import (
    resolve_svid_signing_secret as _resolve_svid_signing_secret,
)
from bridge.identity_iap import (
    resolve_user_jwt_signing_secret as _resolve_user_jwt_signing_secret,
)
from bridge.identity_iap import (
    verify_google_oidc_token as verify_google_oidc_token,
)
from bridge.identity_iap import (
    verify_iap_assertion as verify_iap_assertion,
)
from bridge.identity_models import (
    A2UI_REQUIRE_AUTH_ENV as A2UI_REQUIRE_AUTH_ENV,
)
from bridge.identity_models import (
    ALLOWED_ENTITIES_HEADER as ALLOWED_ENTITIES_HEADER,
)
from bridge.identity_models import (
    ALLOWED_SVID_ALGORITHMS as ALLOWED_SVID_ALGORITHMS,
)
from bridge.identity_models import (
    CARRIER_SLUG_HEADER as CARRIER_SLUG_HEADER,
)
from bridge.identity_models import (
    DEFAULT_DEV_SVID_SECRET as DEFAULT_DEV_SVID_SECRET,
)
from bridge.identity_models import (
    DEFAULT_ROLE_APPROVAL_LIMITS as DEFAULT_ROLE_APPROVAL_LIMITS,
)
from bridge.identity_models import (
    DEFAULT_SPIFFE_AGENT_POLICIES as DEFAULT_SPIFFE_AGENT_POLICIES,
)
from bridge.identity_models import (
    DEFAULT_SPIFFE_TRUST_DOMAIN as DEFAULT_SPIFFE_TRUST_DOMAIN,
)
from bridge.identity_models import (
    EXTERNAL_COUNTERPARTY_TRUST_DOMAINS as EXTERNAL_COUNTERPARTY_TRUST_DOMAINS,
)
from bridge.identity_models import (
    IAP_AUDIENCE_ENV as IAP_AUDIENCE_ENV,
)
from bridge.identity_models import (
    IAP_ISSUER as IAP_ISSUER,
)
from bridge.identity_models import (
    IAP_JWK_CERTS_URL as IAP_JWK_CERTS_URL,
)
from bridge.identity_models import (
    MAX_APPROVAL_HEADER as MAX_APPROVAL_HEADER,
)
from bridge.identity_models import (
    SPIFFE_ID_HEADER as SPIFFE_ID_HEADER,
)
from bridge.identity_models import (
    SPIFFE_PRIVATE_KEY_PEM_ENV as SPIFFE_PRIVATE_KEY_PEM_ENV,
)
from bridge.identity_models import (
    SPIFFE_PUBLIC_KEY_PEM_ENV as SPIFFE_PUBLIC_KEY_PEM_ENV,
)
from bridge.identity_models import (
    SPIFFE_REQUIRE_SVID_ENV as SPIFFE_REQUIRE_SVID_ENV,
)
from bridge.identity_models import (
    SPIFFE_SVID_ALG_ENV as SPIFFE_SVID_ALG_ENV,
)
from bridge.identity_models import (
    SPIFFE_SVID_HEADER as SPIFFE_SVID_HEADER,
)
from bridge.identity_models import (
    SPIFFE_SVID_SECRET_ENV as SPIFFE_SVID_SECRET_ENV,
)
from bridge.identity_models import (
    SPIFFE_TRUST_DOMAIN_ENV as SPIFFE_TRUST_DOMAIN_ENV,
)
from bridge.identity_models import (
    TENANT_ID_HEADER as TENANT_ID_HEADER,
)
from bridge.identity_models import (
    USER_JWT_SECRET_ENV as USER_JWT_SECRET_ENV,
)
from bridge.identity_models import (
    USER_TOKEN_HEADER as USER_TOKEN_HEADER,
)
from bridge.identity_models import (
    AuthViaLiteral as AuthViaLiteral,
)
from bridge.identity_models import (
    CatalogRoleLiteral as CatalogRoleLiteral,
)
from bridge.identity_models import (
    CompoundIdentity as CompoundIdentity,
)
from bridge.identity_models import (
    DeliveryChannelLiteral as DeliveryChannelLiteral,
)
from bridge.identity_models import (
    ShadowAgentError as ShadowAgentError,
)
from bridge.identity_models import (
    SpiffeAgentPolicy as SpiffeAgentPolicy,
)
from bridge.identity_models import (
    SpiffeAuthenticationError as SpiffeAuthenticationError,
)
from bridge.identity_models import (
    SpiffeId as SpiffeId,
)
from bridge.identity_models import (
    SvidAlgorithmLiteral as SvidAlgorithmLiteral,
)
from bridge.identity_models import (
    TenantIsolationError as TenantIsolationError,
)
from bridge.identity_models import (
    UserPrincipal as UserPrincipal,
)
from bridge.telemetry import trace_step


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:
        raise SpiffeAuthenticationError("Malformed JWT base64url segment.") from exc


def generate_ephemeral_svid_keypair(
    algorithm: Literal["RS256", "ES256"] = "ES256",
) -> tuple[str, str]:
    """Generate an asymmetric `(private_key_pem, public_key_pem)` keypair for `RS256` or `ES256` JWT-SVIDs."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    if algorithm == "ES256":
        priv: Any = ec.generate_private_key(ec.SECP256R1())
    elif algorithm == "RS256":
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        raise ValueError(f"Unsupported asymmetric algorithm '{algorithm}'.")

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return priv_pem, pub_pem


def _sign_asymmetric_jws(
    signing_input: bytes,
    *,
    algorithm: Literal["RS256", "ES256"],
    private_key_pem: str,
) -> bytes:
    """Sign `signing_input` using an RSA (`RS256`) or ECDSA P-256 (`ES256`, RFC 7518 R||S) private key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
    except Exception as exc:
        raise SpiffeAuthenticationError("Invalid SPIFFE private key PEM.") from exc

    if algorithm == "RS256":
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SpiffeAuthenticationError("RS256 requires an RSA private key.")
        return key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    if algorithm == "ES256":
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise SpiffeAuthenticationError(
                "ES256 requires an ECDSA P-256 private key."
            )
        der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r_int, s_int = utils.decode_dss_signature(der_sig)
        return r_int.to_bytes(32, "big") + s_int.to_bytes(32, "big")

    raise SpiffeAuthenticationError(f"Unsupported algorithm '{algorithm}'.")


def _verify_asymmetric_jws(
    signing_input: bytes,
    signature: bytes,
    *,
    algorithm: Literal["RS256", "ES256"],
    public_key_pem: str,
) -> None:
    """Verify an `RS256` or `ES256` JWS signature using a PEM public key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

    try:
        pub = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except Exception as exc:
        raise SpiffeAuthenticationError("Invalid SPIFFE public key PEM.") from exc

    try:
        if algorithm == "RS256":
            if not isinstance(pub, rsa.RSAPublicKey):
                raise SpiffeAuthenticationError("RS256 requires an RSA public key.")
            pub.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
            return
        if algorithm == "ES256":
            if not isinstance(pub, ec.EllipticCurvePublicKey) or not isinstance(
                pub.curve, ec.SECP256R1
            ):
                raise SpiffeAuthenticationError(
                    "ES256 requires an ECDSA P-256 public key."
                )
            if len(signature) == 64:
                r_int = int.from_bytes(signature[:32], "big")
                s_int = int.from_bytes(signature[32:], "big")
                der_sig = utils.encode_dss_signature(r_int, s_int)
            else:
                der_sig = signature
            pub.verify(der_sig, signing_input, ec.ECDSA(hashes.SHA256()))
            return
    except SpiffeAuthenticationError:
        raise
    except Exception as exc:
        raise SpiffeAuthenticationError(
            "Invalid JWT-SVID cryptographic signature."
        ) from exc


@dataclass(frozen=True)
class _SvidVerifyConfig:
    secret: str | None
    public_key_pem: str | None
    expected_alg: SvidAlgorithmLiteral | None
    allowed_algorithms: frozenset[str]
    refuse_dev_secret: bool = False


@dataclass(frozen=True)
class _SvidClaimPolicy:
    expected_trust_domain: str | None
    allowed_trust_domains: frozenset[str] | None
    expected_audience: str | None
    now_epoch: int | None


def _decode_jwt_segment_dict(segment: str, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_b64url_decode(segment).decode("utf-8"))
    except Exception as exc:
        raise SpiffeAuthenticationError(f"Invalid JWT-SVID JSON {label}.") from exc
    if not isinstance(decoded, dict):
        raise SpiffeAuthenticationError(f"Invalid JWT-SVID JSON {label}.")
    return decoded


def _verify_svid_algorithm_and_signature(
    header: dict[str, Any],
    segments: tuple[str, str, str],
    cfg: _SvidVerifyConfig,
) -> str:
    h_b64, p_b64, s_b64 = segments
    alg = header.get("alg")
    if (
        not isinstance(alg, str)
        or alg.lower() == "none"
        or alg not in cfg.allowed_algorithms
        or alg not in ALLOWED_SVID_ALGORITHMS
    ):
        raise SpiffeAuthenticationError(f"Unsupported JWT-SVID algorithm '{alg}'.")
    if cfg.expected_alg is not None and alg != cfg.expected_alg:
        raise SpiffeAuthenticationError(
            f"JWT-SVID algorithm '{alg}' does not match expected '{cfg.expected_alg}'."
        )
    if cfg.public_key_pem is not None and cfg.secret is None and alg == "HS256":
        raise SpiffeAuthenticationError(
            "Algorithm confusion blocked: refusing HS256 when public_key_pem is configured."
        )
    if (
        cfg.secret is not None
        and cfg.public_key_pem is None
        and alg in ("RS256", "ES256")
    ):
        raise SpiffeAuthenticationError(
            f"Algorithm confusion blocked: refusing {alg} when only HMAC secret is configured."
        )
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    actual_sig = _b64url_decode(s_b64)
    if alg in ("RS256", "ES256"):
        resolved_pub = (
            cfg.public_key_pem or os.environ.get(SPIFFE_PUBLIC_KEY_PEM_ENV, "").strip()
        )
        if not resolved_pub:
            raise SpiffeAuthenticationError(
                f"{alg} JWT-SVID verification requires public_key_pem or {SPIFFE_PUBLIC_KEY_PEM_ENV}."
            )
        _verify_asymmetric_jws(
            signing_input,
            actual_sig,
            algorithm="RS256" if alg == "RS256" else "ES256",
            public_key_pem=resolved_pub,
        )
    else:
        signing_secret = _resolve_svid_signing_secret(
            cfg.secret, refuse_dev_secret=cfg.refuse_dev_secret
        )
        expected_sig = hmac.new(
            signing_secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise SpiffeAuthenticationError("Invalid JWT-SVID cryptographic signature.")
    return alg


def _validate_svid_trust_and_claims(
    claims: dict[str, Any],
    parsed_id: SpiffeId,
    policy: _SvidClaimPolicy,
) -> None:
    trust_domain = (
        policy.expected_trust_domain
        or os.environ.get(SPIFFE_TRUST_DOMAIN_ENV, "").strip()
        or DEFAULT_SPIFFE_TRUST_DOMAIN
    )
    if policy.allowed_trust_domains is not None:
        domain_ok = parsed_id.trust_domain in policy.allowed_trust_domains
    elif (
        policy.expected_trust_domain is None
        and not os.environ.get(SPIFFE_TRUST_DOMAIN_ENV, "").strip()
        and parsed_id.trust_domain in EXTERNAL_COUNTERPARTY_TRUST_DOMAINS
    ):
        if not parsed_id.path.startswith("external/"):
            raise ShadowAgentError(
                f"External counterparty trust domain '{parsed_id.trust_domain}' "
                f"may only assert external/* SPIFFE paths (got '{parsed_id.path}')."
            )
        domain_ok = True
    else:
        domain_ok = parsed_id.trust_domain == trust_domain
    if not domain_ok:
        raise ShadowAgentError(
            f"Untrusted SPIFFE trust domain '{parsed_id.trust_domain}' "
            f"(expected '{trust_domain}')."
        )
    now = int(policy.now_epoch if policy.now_epoch is not None else time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool) or now > exp + 30:
        raise SpiffeAuthenticationError("JWT-SVID has expired.")
    if policy.expected_audience is not None:
        aud = claims.get("aud")
        aud_list = (
            [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        )
        if policy.expected_audience not in aud_list:
            raise SpiffeAuthenticationError(
                f"JWT-SVID audience mismatch (expected '{policy.expected_audience}')."
            )


def mint_jwt_svid(  # noqa: PLR0913 - public SPIFFE JWT-SVID minting contract
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
    algorithm: SvidAlgorithmLiteral = "HS256",
    private_key_pem: str | None = None,
    kid: str = "spiffe-root-v1",
    now_epoch: int | None = None,
) -> str:
    """Mint a signed SPIFFE JWT-SVID (`HS256`, `RS256`, or `ES256` with optional RFC 8693 OBO claims)."""
    parsed = SpiffeId.parse(spiffe_id)
    now = int(now_epoch if now_epoch is not None else time.time())
    header = {"alg": algorithm, "typ": "JWT+SVID", "kid": kid}
    payload: dict[str, Any] = {
        "iss": f"https://{parsed.trust_domain}",
        "sub": parsed.uri,
        "aud": [audience],
        "iat": now,
        "exp": now + ttl_seconds,
    }
    obo_entries = {
        "sub": obo_subject,
        "role": obo_role,
        "tenant_id": tenant_id,
        "allowed_entities": list(allowed_entities) if allowed_entities else None,
        "carrier_slug": carrier_slug,
        "max_approval_amount": (
            str(max_approval_amount) if max_approval_amount is not None else None
        ),
    }
    obo_block = {k: v for k, v in obo_entries.items() if v}
    if obo_block:
        payload["obo"] = obo_block
        payload["act"] = {"sub": parsed.uri}

    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")

    if algorithm in ("RS256", "ES256"):
        resolved_priv = (
            private_key_pem or os.environ.get(SPIFFE_PRIVATE_KEY_PEM_ENV, "").strip()
        )
        if not resolved_priv:
            raise SpiffeAuthenticationError(
                f"{algorithm} JWT-SVID minting requires private_key_pem or {SPIFFE_PRIVATE_KEY_PEM_ENV}."
            )
        sig = _sign_asymmetric_jws(
            signing_input, algorithm=algorithm, private_key_pem=resolved_priv
        )
    else:
        signing_secret = _resolve_svid_signing_secret(secret)
        sig = hmac.new(
            signing_secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


def verify_jwt_svid(  # noqa: PLR0913 - public SPIFFE JWT-SVID verification contract
    token: str,
    *,
    expected_trust_domain: str | None = None,
    expected_audience: str | None = "docubridge",
    secret: str | None = None,
    public_key_pem: str | None = None,
    expected_alg: SvidAlgorithmLiteral | None = None,
    allowed_algorithms: frozenset[str] = ALLOWED_SVID_ALGORITHMS,
    allowed_trust_domains: frozenset[str] | None = None,
    now_epoch: int | None = None,
    refuse_dev_secret: bool = False,
) -> dict[str, Any]:
    """Cryptographically verify a SPIFFE JWT-SVID (`HS256`, `RS256`, or `ES256`) and return its claims."""
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
        segments = (parts[0], parts[1], parts[2])
        header = _decode_jwt_segment_dict(segments[0], "header")
        alg = _verify_svid_algorithm_and_signature(
            header,
            segments,
            _SvidVerifyConfig(
                secret=secret,
                public_key_pem=public_key_pem,
                expected_alg=expected_alg,
                allowed_algorithms=allowed_algorithms,
                refuse_dev_secret=refuse_dev_secret,
            ),
        )
        claims = _decode_jwt_segment_dict(segments[1], "payload")
        sub = claims.get("sub")
        if not isinstance(sub, str):
            raise SpiffeAuthenticationError("JWT-SVID missing 'sub' SPIFFE ID claim.")
        parsed_id = SpiffeId.parse(sub)
        span.set_attribute("docubridge.agent_spiffe_id", parsed_id.uri)
        span.set_attribute("docubridge.trust_domain", parsed_id.trust_domain)
        span.set_attribute("docubridge.jwt_alg", str(alg))

        _validate_svid_trust_and_claims(
            claims,
            parsed_id,
            _SvidClaimPolicy(
                expected_trust_domain=expected_trust_domain,
                allowed_trust_domains=allowed_trust_domains,
                expected_audience=expected_audience,
                now_epoch=now_epoch,
            ),
        )
        obo = claims.get("obo")
        if isinstance(obo, dict):
            if obo.get("sub"):
                span.set_attribute("docubridge.obo_subject", str(obo["sub"]))
            if obo.get("role"):
                span.set_attribute("docubridge.obo_role", str(obo["role"]))
        span.set_attribute("docubridge.svid_verified", True)
        return dict(claims)


def authorize_spiffe_agent(  # noqa: PLR0913 - public SPIFFE authorization contract
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
        is_allowed_external = (
            expected_trust_domain is None
            and not os.environ.get(SPIFFE_TRUST_DOMAIN_ENV, "").strip()
            and parsed.trust_domain in EXTERNAL_COUNTERPARTY_TRUST_DOMAINS
            and parsed.path.startswith("external/")
        )
        if parsed.trust_domain != trust_domain and not is_allowed_external:
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
        if principal.role in ("client", "carrier"):
            if not principal.allowed_entities and not (
                principal.tenant_id and principal.tenant_id.strip()
            ):
                raise TenantIsolationError(
                    f"Tenant isolation violation: external {principal.role} principal "
                    f"'{principal.subject}' has no tenant_id or allowed_entities scope."
                )
            if (
                principal.allowed_entities
                and entity_id not in principal.allowed_entities
            ):
                raise TenantIsolationError(
                    f"Tenant isolation violation: {principal.role} principal '{principal.subject}' "
                    f"(tenant='{principal.tenant_id or 'restricted'}') is not authorized "
                    f"to access entity '{entity_id}'."
                )
            if (
                not principal.allowed_entities
                and principal.tenant_id
                and entity_id.strip().lower() != principal.tenant_id.strip().lower()
            ):
                raise TenantIsolationError(
                    f"Tenant isolation violation: entity '{entity_id}' does not match "
                    f"{principal.role} tenant '{principal.tenant_id}'."
                )
        elif principal.allowed_entities and entity_id not in principal.allowed_entities:
            raise TenantIsolationError(
                f"Principal '{principal.subject}' is not authorized to access entity '{entity_id}'."
            )
        span.set_attribute("docubridge.tenant_access_allowed", True)


def filter_quotes_for_principal[T](
    quotes: Sequence[T],
    principal: UserPrincipal,
    *,
    allow_competitor_quotes: bool,
) -> list[T]:
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
    """Parse an optional HTTP header string into a finite non-negative Decimal amount."""
    if not raw or not raw.strip():
        return None
    msg = f"Invalid numeric approval limit '{raw}'."
    try:
        parsed = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise SpiffeAuthenticationError(msg) from exc
    if not parsed.is_finite() or parsed < Decimal("0"):
        raise SpiffeAuthenticationError(msg)
    return parsed


def mint_user_token(  # noqa: PLR0913 - public user JWT minting contract
    subject: str,
    role: CatalogRoleLiteral,
    *,
    tenant_id: str | None = None,
    allowed_entities: tuple[str, ...] = (),
    carrier_slug: str | None = None,
    max_approval_amount: Decimal | str | None = None,
    scope: str | None = None,
    ttl_seconds: int = 3600,
    secret: str | None = None,
    now_epoch: int | None = None,
) -> str:
    """Mint a signed HS256 user JWT token for `X-Bridge-User-Token` or `bridge_session` cookie."""
    now = int(now_epoch if now_epoch is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if tenant_id:
        payload["tenant_id"] = tenant_id
    if allowed_entities:
        payload["allowed_entities"] = list(allowed_entities)
    if carrier_slug:
        payload["carrier_slug"] = carrier_slug
    if max_approval_amount is not None:
        payload["max_approval_amount"] = str(max_approval_amount)
    if scope:
        payload["scope"] = scope
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    signing_secret = _resolve_user_jwt_signing_secret(secret)
    sig = hmac.new(
        signing_secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


def verify_user_token(
    token: str,
    *,
    secret: str | None = None,
    now_epoch: int | None = None,
    refuse_dev_secret: bool = False,
) -> dict[str, Any]:
    """Verify a signed HS256 user JWT token from `X-Bridge-User-Token`."""
    raw = token.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) != 3:
        raise SpiffeAuthenticationError("Malformed user JWT: expected 3 segments.")
    h_b64, p_b64, s_b64 = parts
    try:
        header = json.loads(_b64url_decode(h_b64).decode("utf-8"))
    except Exception as exc:
        raise SpiffeAuthenticationError("Invalid user JWT header.") from exc
    if not isinstance(header, dict):
        raise SpiffeAuthenticationError("Invalid user JWT header.")
    if header.get("typ") == "JWT+SVID":
        raise SpiffeAuthenticationError(
            "SPIFFE JWT-SVID tokens cannot be presented as human user tokens."
        )
    alg = header.get("alg")
    if alg != "HS256":
        raise SpiffeAuthenticationError(
            f"Unsupported user JWT algorithm '{alg}' (expected 'HS256')."
        )
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    actual_sig = _b64url_decode(s_b64)
    signing_secret = _resolve_user_jwt_signing_secret(
        secret, refuse_dev_secret=refuse_dev_secret
    )
    expected_sig = hmac.new(
        signing_secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise SpiffeAuthenticationError("Invalid user JWT signature.")
    try:
        claims = json.loads(_b64url_decode(p_b64).decode("utf-8"))
    except Exception as exc:
        raise SpiffeAuthenticationError("Invalid user JWT payload.") from exc
    now = int(now_epoch if now_epoch is not None else time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool) or now > exp + 30:
        raise SpiffeAuthenticationError("User JWT has expired.")
    return dict(claims)


__all__ = [
    "A2UI_REQUIRE_AUTH_ENV",
    "ALLOWED_ENTITIES_HEADER",
    "ALLOWED_SVID_ALGORITHMS",
    "CARRIER_SLUG_HEADER",
    "DEFAULT_DEV_SVID_SECRET",
    "DEFAULT_ROLE_APPROVAL_LIMITS",
    "DEFAULT_SPIFFE_AGENT_POLICIES",
    "DEFAULT_SPIFFE_TRUST_DOMAIN",
    "EXTERNAL_COUNTERPARTY_TRUST_DOMAINS",
    "IAP_AUDIENCE_ENV",
    "IAP_ISSUER",
    "IAP_JWK_CERTS_URL",
    "MAX_APPROVAL_HEADER",
    "SPIFFE_ID_HEADER",
    "SPIFFE_PRIVATE_KEY_PEM_ENV",
    "SPIFFE_PUBLIC_KEY_PEM_ENV",
    "SPIFFE_REQUIRE_SVID_ENV",
    "SPIFFE_SVID_ALG_ENV",
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
    "SvidAlgorithmLiteral",
    "TenantIsolationError",
    "UserPrincipal",
    "authorize_entity_access",
    "authorize_spiffe_agent",
    "filter_quotes_for_principal",
    "generate_ephemeral_svid_keypair",
    "is_auth_enforced",
    "is_dev_secret_refused",
    "mint_jwt_svid",
    "mint_user_token",
    "parse_decimal_header",
    "resolve_expected_svid_algorithm",
    "verify_google_oidc_token",
    "verify_iap_assertion",
    "verify_jwt_svid",
    "verify_user_token",
]
