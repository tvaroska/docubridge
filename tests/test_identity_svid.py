"""P14-10 SVID algorithm pinning tests (`expected_alg`, `allowed_algorithms`, and HTTP wiring)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from bridge.identity import (
    SPIFFE_PUBLIC_KEY_PEM_ENV,
    SPIFFE_SVID_ALG_ENV,
    SPIFFE_SVID_HEADER,
    SPIFFE_SVID_SECRET_ENV,
    SpiffeAuthenticationError,
    mint_jwt_svid,
    resolve_expected_svid_algorithm,
    verify_jwt_svid,
)
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.user_interaction import build_user_interaction_app
from contract.enums import Disposition, Gate
from contract.wire import LedgerEntry

_AGENT_ID = "spiffe://agents.docubridge.io/internal/broker-agent"
_TEST_SECRET = "svid-pin-test-hmac-secret-2026-xyz"


def _gen_es256_pem_pair() -> tuple[str, str]:
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return priv_pem, pub_pem


def _gen_rs256_pem_pair() -> tuple[str, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return priv_pem, pub_pem


def test_expected_alg_mismatch_is_refused() -> None:
    """`verify_jwt_svid(..., expected_alg=...)` refuses valid tokens signed with a different alg."""
    es_priv, es_pub = _gen_es256_pem_pair()
    rs_priv, rs_pub = _gen_rs256_pem_pair()

    hs_token = mint_jwt_svid(_AGENT_ID, algorithm="HS256", secret=_TEST_SECRET)
    es_token = mint_jwt_svid(_AGENT_ID, algorithm="ES256", private_key_pem=es_priv)
    rs_token = mint_jwt_svid(_AGENT_ID, algorithm="RS256", private_key_pem=rs_priv)

    assert (
        verify_jwt_svid(hs_token, secret=_TEST_SECRET, expected_alg="HS256")["sub"]
        == _AGENT_ID
    )
    assert (
        verify_jwt_svid(es_token, public_key_pem=es_pub, expected_alg="ES256")["sub"]
        == _AGENT_ID
    )
    assert (
        verify_jwt_svid(rs_token, public_key_pem=rs_pub, expected_alg="RS256")["sub"]
        == _AGENT_ID
    )

    with pytest.raises(SpiffeAuthenticationError, match="algorithm"):
        verify_jwt_svid(
            hs_token,
            secret=_TEST_SECRET,
            public_key_pem=es_pub,
            expected_alg="ES256",
        )
    with pytest.raises(SpiffeAuthenticationError, match="algorithm"):
        verify_jwt_svid(
            es_token,
            secret=_TEST_SECRET,
            public_key_pem=es_pub,
            expected_alg="HS256",
        )
    with pytest.raises(SpiffeAuthenticationError, match="algorithm"):
        verify_jwt_svid(
            rs_token,
            public_key_pem=rs_pub,
            expected_alg="ES256",
        )


def test_allowed_algorithms_excludes_others() -> None:
    """`allowed_algorithms` restricts verification strictly to the declared set."""
    es_priv, es_pub = _gen_es256_pem_pair()
    hs_token = mint_jwt_svid(_AGENT_ID, algorithm="HS256", secret=_TEST_SECRET)
    es_token = mint_jwt_svid(_AGENT_ID, algorithm="ES256", private_key_pem=es_priv)

    assert (
        verify_jwt_svid(
            es_token,
            public_key_pem=es_pub,
            allowed_algorithms=frozenset({"ES256"}),
        )["sub"]
        == _AGENT_ID
    )
    with pytest.raises(SpiffeAuthenticationError, match="algorithm"):
        verify_jwt_svid(
            hs_token,
            secret=_TEST_SECRET,
            allowed_algorithms=frozenset({"ES256"}),
        )
    with pytest.raises(SpiffeAuthenticationError, match="algorithm"):
        verify_jwt_svid(
            es_token,
            public_key_pem=es_pub,
            allowed_algorithms=frozenset({"HS256"}),
        )


def test_http_svid_verification_pins_algorithm_to_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production HTTP SVID verification pins algorithm via `resolve_expected_svid_algorithm()`."""
    es_priv, es_pub = _gen_es256_pem_pair()
    monkeypatch.setenv("K_SERVICE", "bridge-meridian")
    monkeypatch.setenv(SPIFFE_SVID_SECRET_ENV, _TEST_SECRET)
    monkeypatch.setenv(SPIFFE_PUBLIC_KEY_PEM_ENV, es_pub)
    monkeypatch.delenv(SPIFFE_SVID_ALG_ENV, raising=False)

    assert resolve_expected_svid_algorithm() == ("ES256", frozenset({"ES256"}))

    store = InMemoryOperationalStore()
    store.ingest_entry(
        LedgerEntry(
            doc_id="doc-svid-pin",
            doctype="carrier-quote",
            issuer="Chubb",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.98,
            message="ok",
            key_fields={"premium": "$10,000.00"},
        ),
        entity_id="ENT-SVID-PIN",
        context_id="ctx-svid-pin",
    )
    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=InMemoryKnowledgeCatalog(operational_store=store),
        require_auth=True,
    )
    client = TestClient(app)

    es_token = mint_jwt_svid(_AGENT_ID, algorithm="ES256", private_key_pem=es_priv)
    hs_token = mint_jwt_svid(_AGENT_ID, algorithm="HS256", secret=_TEST_SECRET)

    r_es = client.get(
        "/v1/a2ui/gemini_app/ENT-SVID-PIN",
        headers={SPIFFE_SVID_HEADER: es_token},
    )
    assert r_es.status_code == 200

    # HS256 downgrade must be rejected with 401 when ES256 public key is configured
    r_hs = client.get(
        "/v1/a2ui/gemini_app/ENT-SVID-PIN",
        headers={SPIFFE_SVID_HEADER: hs_token},
    )
    assert r_hs.status_code == 401

    # Explicit BRIDGE_SPIFFE_SVID_ALG override enforces its algorithm
    monkeypatch.setenv(SPIFFE_SVID_ALG_ENV, "HS256")
    assert resolve_expected_svid_algorithm() == ("HS256", frozenset({"HS256"}))
    assert (
        client.get(
            "/v1/a2ui/gemini_app/ENT-SVID-PIN",
            headers={SPIFFE_SVID_HEADER: hs_token},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/v1/a2ui/gemini_app/ENT-SVID-PIN",
            headers={SPIFFE_SVID_HEADER: es_token},
        ).status_code
        == 401
    )
