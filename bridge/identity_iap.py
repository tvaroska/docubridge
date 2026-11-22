"""Auth-enforcement predicates, SVID algorithm resolution, and ES256 Cloud IAP JWT verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Any, TypeGuard, cast

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

from bridge.identity_models import (
    A2UI_REQUIRE_AUTH_ENV,
    ALLOWED_SVID_ALGORITHMS,
    DEFAULT_DEV_SVID_SECRET,
    IAP_ISSUER,
    IAP_JWK_CERTS_URL,
    SPIFFE_PUBLIC_KEY_PEM_ENV,
    SPIFFE_REQUIRE_SVID_ENV,
    SPIFFE_SVID_ALG_ENV,
    SPIFFE_SVID_SECRET_ENV,
    USER_JWT_SECRET_ENV,
    SpiffeAuthenticationError,
    SvidAlgorithmLiteral,
)

_IAP_JWK_CACHE_TTL_S = 300.0
_IAP_JWK_REFRESH_COOLDOWN_S = 10.0
_IAP_JWK_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_IAP_JWK_LAST_REFRESH: dict[str, float] = {}
_IAP_JWK_CACHE_LOCK = threading.Lock()


def _b64url_decode_bytes(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:
        raise SpiffeAuthenticationError("Malformed JWT base64url segment.") from exc


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


def is_dev_secret_refused(*, require_auth: bool = False) -> bool:
    """Return True when fallback to ``DEFAULT_DEV_SVID_SECRET`` must be refused.

    Refuses the public dev secret whenever auth enforcement is active (``K_SERVICE``,
    ``BRIDGE_A2UI_REQUIRE_AUTH=1``, ``BRIDGE_SPIFFE_REQUIRE_SVID=1``, or explicit
    ``require_auth=True``) OR whenever any managed GCP port mode is set
    (``BRIDGE_PORT_MODE=gcp`` or any per-port ``BRIDGE_PORT_<NAME>=gcp`` override).
    """
    if is_auth_enforced(require_auth):
        return True
    if os.environ.get(SPIFFE_REQUIRE_SVID_ENV, "").strip() == "1":
        return True
    for key, value in os.environ.items():
        if key.startswith("BRIDGE_PORT_") and value.strip().lower() == "gcp":
            return True
    return False


def resolve_svid_signing_secret(
    secret: str | None = None, *, refuse_dev_secret: bool = False
) -> str:
    """Resolve the SPIFFE SVID HMAC signing secret; refuse dev fallback whenever auth is enforced."""
    if secret and secret.strip():
        return secret.strip()
    refuse_dev = is_dev_secret_refused(require_auth=refuse_dev_secret)
    if env_secret := os.environ.get(SPIFFE_SVID_SECRET_ENV, "").strip():
        if refuse_dev and hmac.compare_digest(env_secret, DEFAULT_DEV_SVID_SECRET):
            raise SpiffeAuthenticationError(
                f"{SPIFFE_SVID_SECRET_ENV} must not use DEFAULT_DEV_SVID_SECRET when auth is enforced."
            )
        return env_secret
    if refuse_dev:
        raise SpiffeAuthenticationError(
            f"{SPIFFE_SVID_SECRET_ENV} must be configured when auth is enforced or a GCP port is enabled; "
            "refusing fallback to DEFAULT_DEV_SVID_SECRET."
        )
    return DEFAULT_DEV_SVID_SECRET


def resolve_user_jwt_signing_secret(
    secret: str | None = None, *, refuse_dev_secret: bool = False
) -> str:
    """Resolve the user/session JWT HMAC key, domain-separated from the SPIFFE SVID key."""
    if secret and secret.strip():
        return secret.strip()
    refuse_dev = is_dev_secret_refused(require_auth=refuse_dev_secret)
    if env_user := os.environ.get(USER_JWT_SECRET_ENV, "").strip():
        if refuse_dev and hmac.compare_digest(env_user, DEFAULT_DEV_SVID_SECRET):
            raise SpiffeAuthenticationError(
                f"{USER_JWT_SECRET_ENV} must not use DEFAULT_DEV_SVID_SECRET when auth is enforced."
            )
        return env_user
    svid_key = resolve_svid_signing_secret(refuse_dev_secret=refuse_dev)
    return hmac.new(
        svid_key.encode("utf-8"),
        b"docubridge:user-jwt:v1",
        hashlib.sha256,
    ).hexdigest()


def resolve_expected_svid_algorithm(
    *,
    public_key_pem: str | None = None,
) -> tuple[SvidAlgorithmLiteral, frozenset[str]]:
    """Resolve the pinned JWT-SVID signing algorithm and singleton allowed-algorithms set.

    Precedence:
    1. Explicit ``BRIDGE_SPIFFE_SVID_ALG`` (must be ``HS256``, ``RS256``, or ``ES256``).
    2. Key type of configured ``public_key_pem`` / ``BRIDGE_SPIFFE_PUBLIC_KEY_PEM``
       (``ES256`` for EC P-256, ``RS256`` for RSA) — prevents HS256 downgrade when
       asymmetric verification is configured.
    3. ``HS256`` when using shared-secret HMAC verification.
    """
    configured_alg = os.environ.get(SPIFFE_SVID_ALG_ENV, "").strip().upper()
    if configured_alg:
        if configured_alg not in ALLOWED_SVID_ALGORITHMS:
            raise SpiffeAuthenticationError(
                f"Invalid {SPIFFE_SVID_ALG_ENV} '{configured_alg}' "
                f"(expected one of {', '.join(sorted(ALLOWED_SVID_ALGORITHMS))})."
            )
        alg = cast(SvidAlgorithmLiteral, configured_alg)
        return alg, frozenset({alg})

    resolved_pub = (
        public_key_pem or os.environ.get(SPIFFE_PUBLIC_KEY_PEM_ENV, "").strip()
    )
    if resolved_pub:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        try:
            pub = serialization.load_pem_public_key(resolved_pub.encode("utf-8"))
        except Exception as exc:
            raise SpiffeAuthenticationError("Invalid SPIFFE public key PEM.") from exc
        if isinstance(pub, ec.EllipticCurvePublicKey):
            if not isinstance(pub.curve, ec.SECP256R1):
                raise SpiffeAuthenticationError(
                    f"Unsupported EC curve in {SPIFFE_PUBLIC_KEY_PEM_ENV} (expected P-256 for ES256)."
                )
            return "ES256", frozenset({"ES256"})
        if isinstance(pub, rsa.RSAPublicKey):
            return "RS256", frozenset({"RS256"})
        raise SpiffeAuthenticationError(
            f"Unsupported public key type in {SPIFFE_PUBLIC_KEY_PEM_ENV}."
        )

    return "HS256", frozenset({"HS256"})


def _fetch_iap_jwk_keys(
    certs_url: str,
    *,
    request_transport: object = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    now = time.monotonic()
    if request_transport is None:
        with _IAP_JWK_CACHE_LOCK:
            cached = _IAP_JWK_CACHE.get(certs_url)
            if cached is not None:
                if not force_refresh and (now - cached[0]) < _IAP_JWK_CACHE_TTL_S:
                    return list(cached[1])
                last_refresh = _IAP_JWK_LAST_REFRESH.get(certs_url, -1e9)
                if force_refresh and (now - last_refresh) < _IAP_JWK_REFRESH_COOLDOWN_S:
                    return list(cached[1])

    transport: Any = request_transport
    if transport is None:
        from google.auth.transport import requests as google_requests

        transport = google_requests.Request()
    response = transport(certs_url, method="GET")
    status = getattr(response, "status", None)
    if status != 200:
        raise SpiffeAuthenticationError(
            f"Failed to fetch IAP JWK set from {certs_url} (HTTP {status})."
        )
    raw_data = getattr(response, "data", b"")
    text = raw_data.decode("utf-8") if isinstance(raw_data, bytes) else str(raw_data)
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise SpiffeAuthenticationError("Invalid IAP JWK set JSON payload.") from exc
    keys = parsed.get("keys") if isinstance(parsed, dict) else None
    if not isinstance(keys, list):
        raise SpiffeAuthenticationError("Invalid IAP JWK set: missing 'keys' list.")
    dict_keys = [k for k in keys if isinstance(k, dict)]
    if request_transport is None:
        with _IAP_JWK_CACHE_LOCK:
            _IAP_JWK_CACHE[certs_url] = (now, dict_keys)
            _IAP_JWK_LAST_REFRESH[certs_url] = now
    return dict_keys


def _select_es256_public_key(
    keys: list[dict[str, Any]], kid: str
) -> EllipticCurvePublicKey | None:
    from cryptography.hazmat.primitives.asymmetric import ec

    for jwk in keys:
        if jwk.get("kid") != kid:
            continue
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
            raise SpiffeAuthenticationError(
                f"IAP JWK '{kid}' must be an EC P-256 key for ES256 verification."
            )
        use = jwk.get("use")
        if use is not None and use != "sig":
            raise SpiffeAuthenticationError(f"IAP JWK '{kid}' has invalid use '{use}'.")
        jwk_alg = jwk.get("alg")
        if jwk_alg is not None and jwk_alg != "ES256":
            raise SpiffeAuthenticationError(
                f"IAP JWK '{kid}' declares algorithm '{jwk_alg}' (expected 'ES256')."
            )
        x_raw = jwk.get("x")
        y_raw = jwk.get("y")
        if not isinstance(x_raw, str) or not isinstance(y_raw, str):
            raise SpiffeAuthenticationError(f"IAP JWK '{kid}' missing x/y coordinates.")
        x_int = int.from_bytes(_b64url_decode_bytes(x_raw), "big")
        y_int = int.from_bytes(_b64url_decode_bytes(y_raw), "big")
        try:
            return ec.EllipticCurvePublicNumbers(
                x_int, y_int, ec.SECP256R1()
            ).public_key()
        except Exception as exc:
            raise SpiffeAuthenticationError(
                f"IAP JWK '{kid}' has invalid EC P-256 curve coordinates."
            ) from exc
    return None


def _is_valid_epoch_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _validate_iap_claims(
    claims: dict[str, Any],
    *,
    expected_audience: str,
    now: int,
    clock_skew_seconds: int,
) -> None:
    if claims.get("iss") != IAP_ISSUER:
        raise SpiffeAuthenticationError(
            f"IAP assertion issuer '{claims.get('iss')}' does not match '{IAP_ISSUER}'."
        )
    aud = claims.get("aud")
    aud_values = (
        [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
    )
    if expected_audience not in aud_values:
        raise SpiffeAuthenticationError(
            f"IAP assertion audience mismatch (expected '{expected_audience}')."
        )
    exp = claims.get("exp")
    if not _is_valid_epoch_number(exp) or now > float(exp) + clock_skew_seconds:
        raise SpiffeAuthenticationError("IAP assertion has expired.")
    iat = claims.get("iat")
    if iat is not None and (
        not _is_valid_epoch_number(iat) or float(iat) > now + clock_skew_seconds
    ):
        raise SpiffeAuthenticationError("IAP assertion 'iat' claim is invalid.")
    nbf = claims.get("nbf")
    if nbf is not None and (
        not _is_valid_epoch_number(nbf) or float(nbf) > now + clock_skew_seconds
    ):
        raise SpiffeAuthenticationError("IAP assertion is not yet valid ('nbf').")


def _parse_iap_jwt(
    token: str,
) -> tuple[str, str, str, dict[str, Any], dict[str, Any], str]:
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) != 3:
        raise SpiffeAuthenticationError("Malformed IAP JWT: expected 3 segments.")
    h_b64, p_b64, s_b64 = parts
    try:
        header = json.loads(_b64url_decode_bytes(h_b64).decode("utf-8"))
        claims = json.loads(_b64url_decode_bytes(p_b64).decode("utf-8"))
    except SpiffeAuthenticationError:
        raise
    except Exception as exc:
        raise SpiffeAuthenticationError("Invalid IAP JWT JSON payload.") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise SpiffeAuthenticationError("Invalid IAP JWT structure.")
    if header.get("alg") != "ES256":
        raise SpiffeAuthenticationError(
            f"Unsupported IAP JWT algorithm '{header.get('alg')}' (expected 'ES256')."
        )
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise SpiffeAuthenticationError("IAP JWT header is missing 'kid'.")
    return h_b64, p_b64, s_b64, header, claims, kid


def verify_iap_assertion(  # noqa: PLR0913 - public IAP assertion verifier contract
    token: str,
    *,
    expected_audience: str,
    certs_url: str = IAP_JWK_CERTS_URL,
    request_transport: object = None,
    now_epoch: int | None = None,
    clock_skew_seconds: int = 30,
) -> dict[str, Any]:
    """Verify a Cloud IAP ``X-Goog-IAP-JWT-Assertion`` signed with ES256 against Google's IAP JWK set."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    audience = (expected_audience or "").strip()
    if not audience:
        raise SpiffeAuthenticationError(
            "expected_audience (BRIDGE_IAP_AUDIENCE) is mandatory for IAP assertion verification."
        )
    h_b64, p_b64, s_b64, _header, claims, kid = _parse_iap_jwt(token)

    keys = _fetch_iap_jwk_keys(certs_url, request_transport=request_transport)
    pub_key = _select_es256_public_key(keys, kid)
    if pub_key is None and request_transport is None:
        keys = _fetch_iap_jwk_keys(
            certs_url, request_transport=None, force_refresh=True
        )
        pub_key = _select_es256_public_key(keys, kid)
    if pub_key is None:
        raise SpiffeAuthenticationError(
            f"IAP signing key '{kid}' not found in JWK set ({certs_url})."
        )

    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig_bytes = _b64url_decode_bytes(s_b64)
    if len(sig_bytes) != 64:
        raise SpiffeAuthenticationError(
            "Invalid IAP ES256 signature length (expected 64-byte raw R||S)."
        )
    der_sig = utils.encode_dss_signature(
        int.from_bytes(sig_bytes[:32], "big"),
        int.from_bytes(sig_bytes[32:], "big"),
    )
    try:
        pub_key.verify(der_sig, signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception as exc:
        raise SpiffeAuthenticationError(
            "Invalid IAP JWT cryptographic signature."
        ) from exc

    now = int(now_epoch if now_epoch is not None else time.time())
    _validate_iap_claims(
        claims,
        expected_audience=audience,
        now=now,
        clock_skew_seconds=clock_skew_seconds,
    )
    return dict(claims)


def verify_google_oidc_token(
    token: str,
    *,
    expected_audience: str | None = None,
    request_transport: object = None,
) -> dict[str, Any]:
    """Verify a Google Cloud OIDC ID token (Cloud Run / Cloud Scheduler / Workload Identity)."""
    raw = token.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise SpiffeAuthenticationError("Empty Google OIDC token.")
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        transport = request_transport or google_requests.Request()
        claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            raw,
            transport,
            audience=expected_audience,
        )
        return dict(claims)
    except Exception as exc:
        raise SpiffeAuthenticationError(
            f"Google OIDC token verification failed: {exc}"
        ) from exc


__all__ = [
    "is_auth_enforced",
    "is_dev_secret_refused",
    "resolve_expected_svid_algorithm",
    "resolve_svid_signing_secret",
    "resolve_user_jwt_signing_secret",
    "verify_google_oidc_token",
    "verify_iap_assertion",
]
