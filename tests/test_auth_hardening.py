"""P10-3 fail-closed K_SERVICE auth, Option 2B signed cookies, mutating-route coverage & P10-6 Decimal limits."""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.a2ui_inbox import (
    A2uiClientActionRequest,
    ActionInboxItem,
    InMemoryActionInboxStore,
    SqlActionInboxStore,
)
from bridge.agent_gym import InMemoryAgentGymEngine
from bridge.dashboard.rfp_procurement import mount_rfp_procurement_routes
from bridge.identity import (
    SPIFFE_SVID_SECRET_ENV,
    USER_JWT_SECRET_ENV,
    SpiffeAuthenticationError,
    mint_jwt_svid,
    mint_user_token,
    verify_jwt_svid,
    verify_user_token,
)
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.user_interaction import (
    A2UI_REQUIRE_AUTH_ENV,
    DEMO_PASSCODE_ENV,
    READ_ONLY_SCOPE,
    SESSION_COOKIE_NAME,
    CreateActionProgressRequest,
    build_user_interaction_app,
    is_auth_enforced,
)
from contract.enums import Disposition, Gate
from contract.wire import LedgerEntry

TEST_SECRET = "p10-3-hardening-test-signing-key-2026"
TEST_PASSCODE = "p10-3-demo-unlock-passcode"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_repo_skills_and_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep skill-compilation routes in tests isolated from the tracked skills/ tree."""
    skills_copy = tmp_path / "skills"
    shutil.copytree(REPO_ROOT / "skills", skills_copy)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(skills_copy))
    monkeypatch.setenv("BRIDGE_CLOUD_TRACE_EXPORT", "0")
    monkeypatch.setenv("BRIDGE_DISABLE_CLOUD_TRACE", "1")


def _sample_entry(doc_id: str = "doc-auth-test") -> LedgerEntry:
    return LedgerEntry(
        doc_id=doc_id,
        doctype="carrier-quote",
        issuer="Chubb",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        overall_confidence=0.98,
        message="Accepted test quote",
        key_fields={"premium": "$100,000.00", "deductible": "$25,000.00"},
    )


def _build_full_test_app(
    *,
    principal_roles: dict[str, Any] | None = None,
    require_auth: bool = False,
    oidc_verifier: Any = None,
) -> tuple[
    FastAPI, InMemoryOperationalStore, InMemoryActionInboxStore, InMemoryAgentGymEngine
]:
    store = InMemoryOperationalStore()
    store.ingest_entry(
        _sample_entry(),
        entity_id="LOAN-MERIDIAN-001",
        context_id="ctx-auth-hardening",
    )
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    inbox = InMemoryActionInboxStore()
    gym = InMemoryAgentGymEngine()
    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
        inbox_store=inbox,
        agent_gym=gym,
        principal_roles=principal_roles,
        require_auth=require_auth,
        oidc_verifier=oidc_verifier,
    )
    mount_rfp_procurement_routes(
        app,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )
    return app, store, inbox, gym


def _enable_cloud_run_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str | None = TEST_SECRET,
    passcode: str | None = TEST_PASSCODE,
) -> None:
    monkeypatch.setenv("K_SERVICE", "bridge-meridian")
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    monkeypatch.setenv("BRIDGE_DISABLE_CLOUD_TRACE", "1")
    monkeypatch.setenv("BRIDGE_CLOUD_TRACE_EXPORT", "0")
    monkeypatch.delenv(A2UI_REQUIRE_AUTH_ENV, raising=False)
    monkeypatch.delenv(USER_JWT_SECRET_ENV, raising=False)
    if secret is None:
        monkeypatch.delenv(SPIFFE_SVID_SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(SPIFFE_SVID_SECRET_ENV, secret)
    if passcode is None:
        monkeypatch.delenv(DEMO_PASSCODE_ENV, raising=False)
    else:
        monkeypatch.setenv(DEMO_PASSCODE_ENV, passcode)


def _https_client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def test_k_service_closes_port_mode_local_bypass_and_refuses_dev_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K_SERVICE unconditionally enforces auth and refuses DEFAULT_DEV_SVID_SECRET even when BRIDGE_PORT_MODE=local."""
    _enable_cloud_run_env(monkeypatch, secret=None, passcode=None)

    assert is_auth_enforced(False) is True

    with pytest.raises(SpiffeAuthenticationError, match="DEFAULT_DEV_SVID_SECRET"):
        mint_jwt_svid("spiffe://agents.docubridge.io/internal/broker-agent")
    with pytest.raises(SpiffeAuthenticationError, match="DEFAULT_DEV_SVID_SECRET"):
        mint_user_token("usr:alice@docubridge.io", "broker")

    dev_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/broker-agent",
        secret="explicit-other-secret",
    )
    with pytest.raises(SpiffeAuthenticationError, match="DEFAULT_DEV_SVID_SECRET"):
        verify_jwt_svid(dev_svid)

    dev_user_jwt = mint_user_token(
        "usr:alice@docubridge.io",
        "broker",
        secret="explicit-other-secret",
    )
    with pytest.raises(SpiffeAuthenticationError, match="DEFAULT_DEV_SVID_SECRET"):
        verify_user_token(dev_user_jwt)

    app, *_ = _build_full_test_app(require_auth=False)
    client = TestClient(app)

    assert client.get("/v1/a2ui/gemini_app/LOAN-MERIDIAN-001").status_code == 401
    assert (
        client.get(
            "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
            headers={
                "X-Bridge-Role": "underwriter",
                "X-Bridge-Principal": "spoof@docubridge.io",
            },
        ).status_code
        == 401
    )
    assert client.post("/v1/demo/seed").status_code == 401


def test_iap_requires_cryptographic_oidc_assertion_and_blocks_email_substring_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spoofed IAP email headers fail closed (401) and email substrings never grant roles."""
    _enable_cloud_run_env(monkeypatch)

    def _verifier(token: str) -> dict[str, Any]:
        if token == "valid-unmapped-credit-jwt":
            return {"email": "credit.underwriter@docubridge.io"}
        if token == "valid-mapped-broker-jwt":
            return {"email": "alex@partner.example"}
        raise SpiffeAuthenticationError("signature mismatch")

    app, *_ = _build_full_test_app(
        principal_roles={"alex@partner.example": "broker"},
        require_auth=False,
        oidc_verifier=_verifier,
    )
    client = TestClient(app)

    # Spoofed x-goog-authenticated-user-email without X-Goog-IAP-JWT-Assertion -> 401
    r_missing = client.get(
        "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
        headers={
            "X-Goog-Authenticated-User-Email": "accounts.google.com:credit.underwriter@docubridge.io"
        },
    )
    assert r_missing.status_code == 401

    # Invalid X-Goog-IAP-JWT-Assertion -> 401
    r_bad_sig = client.get(
        "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
        headers={
            "X-Goog-IAP-JWT-Assertion": "forged.jwt.sig",
            "X-Goog-Authenticated-User-Email": "accounts.google.com:alex@partner.example",
        },
    )
    assert r_bad_sig.status_code == 401

    # Valid assertion but email contains 'credit'/'underwriter'/'@docubridge.io' with NO mapping -> 401 (no substring escalation)
    r_substring = client.get(
        "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
        headers={
            "X-Goog-IAP-JWT-Assertion": "valid-unmapped-credit-jwt",
            "X-Goog-Authenticated-User-Email": "accounts.google.com:credit.underwriter@docubridge.io",
        },
    )
    assert r_substring.status_code == 401

    # Valid assertion but mismatched email header -> 401
    r_mismatch = client.get(
        "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
        headers={
            "X-Goog-IAP-JWT-Assertion": "valid-mapped-broker-jwt",
            "X-Goog-Authenticated-User-Email": "accounts.google.com:other@partner.example",
        },
    )
    assert r_mismatch.status_code == 401

    # Valid assertion with explicit principal_roles mapping -> 200; spoofing ?role=underwriter -> 403
    r_ok = client.get(
        "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
        headers={
            "X-Goog-IAP-JWT-Assertion": "valid-mapped-broker-jwt",
            "X-Goog-Authenticated-User-Email": "accounts.google.com:alex@partner.example",
        },
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["role"] == "broker"

    r_impersonate = client.get(
        "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001?role=underwriter",
        headers={"X-Goog-IAP-JWT-Assertion": "valid-mapped-broker-jwt"},
    )
    assert r_impersonate.status_code == 403


def test_viewer_cookie_auto_issued_on_get_and_head_demo_console_ebc_and_blocks_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET/HEAD /demo, /console, /ebc issue signed read-only HttpOnly Secure SameSite=Strict viewer cookies."""
    _enable_cloud_run_env(monkeypatch)
    app, *_ = _build_full_test_app()

    for path in ("/demo", "/console", "/ebc"):
        for method in ("GET", "HEAD"):
            client = _https_client(app)
            resp = client.request(method, path)
            assert resp.status_code == 200, (method, path)
            set_cookie = resp.headers.get("set-cookie", "")
            assert f"{SESSION_COOKIE_NAME}=" in set_cookie
            assert "httponly" in set_cookie.lower()
            assert "samesite=strict" in set_cookie.lower()
            assert "secure" in set_cookie.lower()

            token = resp.cookies.get(SESSION_COOKIE_NAME)
            assert token
            claims = verify_user_token(token)
            assert claims["scope"] == READ_ONLY_SCOPE
            assert claims["max_approval_amount"] == "0"

            # Read-only viewer cookie works for GET A2UI but is blocked (403) on mutating POSTs
            get_surface = client.get("/v1/a2ui/gemini_app/LOAN-MERIDIAN-001")
            assert get_surface.status_code == 200
            assert client.post("/v1/demo/seed").status_code == 403

    # Local HTTP (no K_SERVICE) omits Secure flag so local browser dev still stores the cookie
    monkeypatch.delenv("K_SERVICE", raising=False)
    local_client = TestClient(app)
    local_resp = local_client.get("/demo")
    assert local_resp.status_code == 200
    local_cookie_hdr = local_resp.headers.get("set-cookie", "").lower()
    assert "httponly" in local_cookie_hdr
    assert "samesite=strict" in local_cookie_hdr
    assert "secure" not in local_cookie_hdr


def test_passcode_demo_session_persona_switching_and_no_false_403_traps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST/DELETE /v1/auth/demo-session exchanges passcode for interactive persona cookies without false 403s."""
    _enable_cloud_run_env(monkeypatch, passcode=None)
    app, *_ = _build_full_test_app()
    client = _https_client(app)

    # Disabled when BRIDGE_DEMO_PASSCODE is unset -> 503
    r_disabled = client.post(
        "/v1/auth/demo-session",
        json={"passcode": "anything", "persona": "underwriter"},
    )
    assert r_disabled.status_code == 503

    monkeypatch.setenv(DEMO_PASSCODE_ENV, TEST_PASSCODE)

    # Wrong passcode -> 401
    r_wrong = client.post(
        "/v1/auth/demo-session",
        json={"passcode": "wrong-code", "persona": "underwriter"},
    )
    assert r_wrong.status_code == 401

    # Unlock as broker -> POST /v1/intake/ingest WITHOUT ?role= succeeds (no false 403 against default_role=underwriter)
    r_broker = client.post(
        "/v1/auth/demo-session",
        json={"passcode": TEST_PASSCODE, "persona": "broker"},
    )
    assert r_broker.status_code == 200
    assert r_broker.json()["role"] == "broker"
    assert r_broker.json()["max_approval_amount"] == "10000000.00"
    assert "httponly" in r_broker.headers.get("set-cookie", "").lower()
    assert "samesite=strict" in r_broker.headers.get("set-cookie", "").lower()
    assert "secure" in r_broker.headers.get("set-cookie", "").lower()

    r_ingest = client.post(
        "/v1/intake/ingest",
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "doc_id": "doc-broker-ingest-1",
            "doctype": "carrier-quote",
            "issuer": "Chubb",
            "key_fields": {"premium": "$110,000.00"},
        },
    )
    assert r_ingest.status_code == 200

    # Real impersonation attempt (?role=underwriter with broker cookie) still returns 403
    r_impersonate = client.post(
        "/v1/intake/ingest?role=underwriter",
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "doc_id": "doc-broker-spoof",
            "doctype": "carrier-quote",
            "issuer": "Chubb",
            "key_fields": {"premium": "$110,000.00"},
        },
    )
    assert r_impersonate.status_code == 403

    # Switch persona to underwriter -> create & resolve inbox action WITHOUT ?role= succeeds (no false 403 against default_role=broker)
    r_uw = client.post(
        "/v1/auth/demo-session",
        json={"passcode": TEST_PASSCODE, "persona": "underwriter"},
    )
    assert r_uw.status_code == 200
    assert r_uw.json()["role"] == "underwriter"
    assert r_uw.json()["max_approval_amount"] == "25000000.00"

    # Reloading /demo preserves the interactive cookie instead of downgrading to read-only viewer
    r_reload = client.get("/demo")
    assert r_reload.status_code == 200
    status_after_reload = client.get("/v1/auth/demo-session").json()
    assert status_after_reload["read_only"] is False
    assert status_after_reload["role"] == "underwriter"

    r_create_item = client.post(
        "/v1/a2ui/inbox/actions?channel=gemini_app",
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "assigned_role": "broker",
            "pause_for_review": True,
            "data_model": {"transfer": {"approvedAmount": "50000.00"}},
        },
    )
    assert r_create_item.status_code == 200
    item_id = r_create_item.json()["item_id"]

    assert client.get("/v1/a2ui/inbox/items?channel=gemini_app").status_code == 200
    assert (
        client.get(f"/v1/a2ui/inbox/items/{item_id}?channel=gemini_app").status_code
        == 200
    )
    assert (
        client.get(
            f"/v1/a2ui/inbox/items/{item_id}/events?channel=gemini_app"
        ).status_code
        == 200
    )

    r_resolve = client.post(
        f"/v1/a2ui/inbox/items/{item_id}/action?channel=gemini_app",
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": "45000.00"},
                    "review": {"memo": "Approved by underwriter cookie"},
                },
            },
        },
    )
    assert r_resolve.status_code == 200

    # Switch persona to client -> allowed on borrower upload-pdf, rejected on internal Agent Gym
    r_client_persona = client.post(
        "/v1/auth/demo-session",
        json={"passcode": TEST_PASSCODE, "persona": "client"},
    )
    assert r_client_persona.status_code == 200
    assert r_client_persona.json()["role"] == "client"
    assert r_client_persona.json()["max_approval_amount"] == "0.00"
    assert (
        client.post(
            "/v1/demo/consolidation-collab/upload-pdf",
            json={"sample_ids": ["meridian-financials"], "mode": "cached"},
        ).status_code
        == 200
    )
    assert client.post("/v1/gym/trust-policies/reset").status_code == 403

    # DELETE /v1/auth/demo-session logs out -> subsequent mutating POST returns 401
    r_logout = client.delete("/v1/auth/demo-session")
    assert r_logout.status_code == 200
    assert r_logout.json()["authenticated"] is False
    assert client.post("/v1/demo/seed").status_code == 401


def test_every_mutating_post_route_is_gated_under_k_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every mutating POST route returns 401 unauthenticated, 403 with viewer cookie, and allows authenticated callers."""
    _enable_cloud_run_env(monkeypatch)
    app, _store, inbox, gym = _build_full_test_app()

    # Seed one inbox item and one gym candidate rule directly on stores for path-param routes
    seeded_item = inbox.create_action(
        entity_id="LOAN-MERIDIAN-001",
        title="Hardening test item",
        hint="Test hint",
        pause_for_review=True,
    )
    _, seeded_rule = gym.distill_from_quarantine_correction(
        entity_id="LOAN-MERIDIAN-001",
        doc_id="doc-hardening",
        doctype="closing-disclosure",
        issuer="Meridian",
        field_path="origination_fee",
        before_value="$10,000.00",
        after_value="$12,000.00",
        expert_memo="Verified against page 2 schedule.",
        actor_principal="demo:underwriter@docubridge.io",
        actor_role="underwriter",
    )

    mutating_cases: list[tuple[str, dict[str, Any] | None]] = [
        (
            "/v1/intake/ingest",
            {
                "entity_id": "LOAN-MERIDIAN-001",
                "doc_id": "doc-post-gate",
                "doctype": "carrier-quote",
                "issuer": "Chubb",
            },
        ),
        (
            "/v1/a2ui/inbox/actions",
            {"entity_id": "LOAN-MERIDIAN-001", "title": "Test"},
        ),
        (
            f"/v1/a2ui/inbox/items/{seeded_item.item_id}/progress",
            {"step_id": "step_consolidation_gate", "status": "completed"},
        ),
        (
            f"/v1/a2ui/inbox/items/{seeded_item.item_id}/action",
            {
                "version": "v0.9",
                "action": {"name": "accept", "context": {"decision": "accept"}},
            },
        ),
        (
            "/v1/gym/signals/from-quarantine",
            {
                "entity_id": "LOAN-MERIDIAN-001",
                "doc_id": "doc-q",
                "doctype": "quote",
                "issuer": "Chubb",
                "field_path": "deductible",
                "before_value": "100",
                "after_value": "200",
                "expert_memo": "Verified",
            },
        ),
        (
            f"/v1/gym/signals/from-inbox/{seeded_item.item_id}",
            {"doc_id": "doc-inbox"},
        ),
        (f"/v1/gym/rules/{seeded_rule.rule_id}/evaluate", None),
        ("/v1/gym/compile-skill", {"doctype": "closing-disclosure"}),
        ("/v1/gym/skills/closing-disclosure/rollback", {}),
        (
            "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/shadow",
            {
                "entity_id": "LOAN-MERIDIAN-001",
                "doc_id": "doc-s",
                "issuer": "AIG",
                "field_path": "deductible",
                "ai_value": "100",
                "human_value": "100",
            },
        ),
        (
            "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial",
            {"target_level": "guided_review", "force": True},
        ),
        ("/v1/gym/trust-policies/reset", None),
        ("/v1/refinery/assay", {"domain": "insurance"}),
        (
            "/v1/refinery/onboard",
            {"entity_id": "test-doctype-ent", "samples": [{"a": "1"}]},
        ),
        (
            "/v1/refinery/onboard-use-case",
            {
                "process_name": "test-auth-proc",
                "process_description": "Auth hardening test process",
                "doctypes": [],
                "install_skills": False,
            },
        ),
        ("/v1/refinery/promote", {"proposal_id": "missing-prop"}),
        ("/v1/refinery/reset", None),
        ("/v1/telemetry/spans/clear", None),
        ("/v1/telemetry/sql-eval/run", None),
        (
            "/v1/registry/services",
            {
                "service_id": "svc-auth-test",
                "display_name": "Auth Test Agent",
                "target_uri": "https://agent.example/a2a",
            },
        ),
        (
            "/v1/registry/bindings",
            {
                "binding_id": "bind-auth-test",
                "source_agent": "missing-agent",
                "target_resource": "missing-mcp",
            },
        ),
        (
            "/v1/gateway/evaluate-route",
            {
                "source_principal": "spiffe://agents.docubridge.io/internal/broker-agent",
                "target_resource": "mcp://operational-store",
            },
        ),
        ("/v1/demo/seed", None),
        ("/v1/demo/reset", None),
        ("/v1/demo/scenarios/seed", None),
        ("/v1/demo/scenarios/reload", None),
        ("/v1/demo/scenarios/loan-meridian-consol-2026/advance", {"beat": 1}),
        ("/v1/demo/scenarios/loan-meridian-consol-2026/reset", None),
        (
            "/v1/demo/scenarios/loan-meridian-consol-2026/upload-pdf",
            {"sample_ids": ["meridian-financials"], "mode": "cached"},
        ),
        (
            "/v1/demo/scenarios/loan-meridian-consol-2026/approve-waiver",
            {"mode": "cached"},
        ),
        (
            "/v1/demo/scenarios/loan-meridian-consol-2026/distill-skill",
            {"mode": "cached"},
        ),
        (
            "/v1/demo/scenarios/loan-meridian-consol-2026/request-a2a-payoff",
            {"mode": "cached"},
        ),
        ("/v1/demo/scenarios/loan-meridian-consol-2026/promote-skill", {}),
        (
            "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
            {
                "case_id": "fleet-gate-1",
                "deal_entity_id": "LOAN-FLEET-1",
                "borrower_name": "Meridian Test",
                "mode": "cached",
            },
        ),
        ("/v1/demo/consolidation-collab/advance", {"beat": 1}),
        ("/v1/demo/consolidation-collab/reset", None),
        (
            "/v1/demo/consolidation-collab/upload-pdf",
            {"sample_ids": ["meridian-financials"], "mode": "cached"},
        ),
        ("/v1/demo/consolidation-collab/approve-waiver", {"mode": "cached"}),
        ("/v1/demo/consolidation-collab/distill-skill", {"mode": "cached"}),
        ("/v1/demo/consolidation-collab/request-a2a-payoff", {"mode": "cached"}),
        ("/v1/demo/consolidation-collab/promote-skill", {}),
        ("/v1/demo/schema-evolution/advance", {"beat": 1, "domain": "insurance"}),
        ("/v1/demo/schema-evolution/reset", None),
        (
            "/v1/rfp/run-procurement",
            {
                "case_id": "rfp-gate-1",
                "mode": "cached",
                "carrier_a_fallback": {},
            },
        ),
    ]

    anon_client = _https_client(app)
    viewer_client = _https_client(app)
    assert viewer_client.get("/demo").status_code == 200

    authed_client = _https_client(app)
    assert (
        authed_client.post(
            "/v1/auth/demo-session",
            json={"passcode": TEST_PASSCODE, "persona": "underwriter"},
        ).status_code
        == 200
    )

    for path, body in mutating_cases:
        r_anon = (
            anon_client.post(path, json=body)
            if body is not None
            else anon_client.post(path)
        )
        assert r_anon.status_code == 401, (
            f"Unauthenticated POST {path} returned {r_anon.status_code}"
        )

        r_viewer = (
            viewer_client.post(path, json=body)
            if body is not None
            else viewer_client.post(path)
        )
        assert r_viewer.status_code == 403, (
            f"Read-only viewer POST {path} returned {r_viewer.status_code}"
        )

        r_auth = (
            authed_client.post(path, json=body)
            if body is not None
            else authed_client.post(path)
        )
        assert r_auth.status_code not in (401, 403), (
            f"Authenticated underwriter POST {path} unexpectedly returned {r_auth.status_code}: {r_auth.text}"
        )

    # Pre-body dependency check: even an empty body `{}` returns 401 unauthenticated / 403 viewer (never leaks 422)
    pre_body_routes = [
        "/v1/intake/ingest",
        "/v1/a2ui/inbox/actions",
        f"/v1/a2ui/inbox/items/{seeded_item.item_id}/progress",
        f"/v1/a2ui/inbox/items/{seeded_item.item_id}/action",
        "/v1/gym/signals/from-quarantine",
        f"/v1/gym/signals/from-inbox/{seeded_item.item_id}",
        f"/v1/gym/rules/{seeded_rule.rule_id}/evaluate",
        "/v1/gym/compile-skill",
        "/v1/gym/skills/closing-disclosure/rollback",
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/shadow",
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial",
        "/v1/gym/trust-policies/reset",
        "/v1/refinery/onboard-use-case",
        "/v1/registry/services",
        "/v1/registry/bindings",
        "/v1/gateway/evaluate-route",
        "/v1/rfp/run-procurement",
    ]
    for path in pre_body_routes:
        assert anon_client.post(path, json={}).status_code == 401, path
        assert viewer_client.post(path, json={}).status_code == 403, path


def test_critical_review_regressions_iap_header_decimal_header_client_scope_and_spiffe_obo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression coverage for CRITICAL review items 1-4:
    1) unverified IAP email header never escalates even when another principal lacks a role,
    2) parse_decimal_header rejects NaN/Infinity/negative values,
    3) client persona is blocked from internal /v1/demo/* and inbox /progress mutations,
    4) shadow and external SPIFFE SVIDs carrying obo_role='underwriter' are blocked on mutating routes.
    """
    from bridge.identity import parse_decimal_header

    _enable_cloud_run_env(monkeypatch)

    # 1. Token with `sub` but no valid `role` + spoofed X-Goog-Authenticated-User-Email without IAP assertion -> 401
    app, _store, inbox, _gym = _build_full_test_app(
        principal_roles={
            "accounts.google.com:admin@docubridge.io": "underwriter",
            "admin@docubridge.io": "underwriter",
        }
    )
    seeded = inbox.create_action(
        entity_id="LOAN-MERIDIAN-001",
        title="Progress guard item",
        hint="Guard hint",
        pause_for_review=True,
    )
    client = _https_client(app)

    roleless_jwt = mint_user_token(
        "usr:no-role@example.com",
        "not-a-role",  # type: ignore[arg-type]
    )
    r_iap_escalate = client.post(
        "/v1/demo/seed",
        headers={
            "X-Bridge-User-Token": f"Bearer {roleless_jwt}",
            "X-Goog-Authenticated-User-Email": "accounts.google.com:admin@docubridge.io",
        },
    )
    assert r_iap_escalate.status_code == 401

    # 2. parse_decimal_header rejects NaN, Infinity, -Infinity, and negative amounts
    for bad_limit in ("NaN", "Infinity", "-Infinity", "-500.00", "-0.01"):
        with pytest.raises(SpiffeAuthenticationError):
            parse_decimal_header(bad_limit)
    assert parse_decimal_header("0") == Decimal("0")
    assert parse_decimal_header("25000000.00") == Decimal("25000000.00")

    bad_jwt_limit = mint_user_token(
        "usr:badlimit@docubridge.io",
        "underwriter",
        max_approval_amount="NaN",
    )
    assert (
        client.get(
            "/v1/a2ui/gemini_app/LOAN-MERIDIAN-001",
            headers={"X-Bridge-User-Token": f"Bearer {bad_jwt_limit}"},
        ).status_code
        == 401
    )

    # 3. External `client` cookie cannot call internal /v1/demo/* or inbox /progress routes (403)
    client_session = _https_client(app)
    assert (
        client_session.post(
            "/v1/auth/demo-session",
            json={"passcode": TEST_PASSCODE, "persona": "client"},
        ).status_code
        == 200
    )
    assert (
        client_session.post(
            "/v1/demo/consolidation-collab/upload-pdf",
            json={"sample_ids": ["meridian-financials"], "mode": "cached"},
        ).status_code
        == 200
    )
    for internal_path, payload in [
        ("/v1/demo/seed", None),
        ("/v1/demo/reset", None),
        ("/v1/demo/scenarios/seed", None),
        ("/v1/demo/scenarios/reload", None),
        ("/v1/demo/consolidation-collab/reset", None),
        ("/v1/demo/consolidation-collab/approve-waiver", {"mode": "cached"}),
        ("/v1/demo/consolidation-collab/distill-skill", {"mode": "cached"}),
        ("/v1/demo/consolidation-collab/request-a2a-payoff", {"mode": "cached"}),
        ("/v1/demo/consolidation-collab/promote-skill", {}),
        (
            "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
            {
                "case_id": "fleet-client-denied",
                "deal_entity_id": "LOAN-CLIENT-1",
                "borrower_name": "Meridian",
                "mode": "cached",
            },
        ),
        (
            f"/v1/a2ui/inbox/items/{seeded.item_id}/progress",
            {"step_id": "step_consolidation_gate", "status": "completed"},
        ),
    ]:
        r_denied = (
            client_session.post(internal_path, json=payload)
            if payload is not None
            else client_session.post(internal_path)
        )
        assert r_denied.status_code == 403, (
            f"Client allowed on {internal_path}: {r_denied.status_code}"
        )

    # 4. Shadow AI SVID and external-zone SVID carrying obo_role='underwriter' are blocked (403)
    shadow_obo_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/shadow-bot",
        obo_subject="usr:spoofed.uw@docubridge.io",
        obo_role="underwriter",
    )
    external_obo_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/portal/external-portal-agent",
        obo_subject="usr:spoofed.uw@docubridge.io",
        obo_role="underwriter",
    )
    for svid_tok in (shadow_obo_svid, external_obo_svid):
        hdrs = {"X-Bridge-Spiffe-Svid": f"Bearer {svid_tok}"}
        assert client.post("/v1/demo/seed", headers=hdrs).status_code == 403
        assert client.post("/v1/refinery/reset", headers=hdrs).status_code == 403
        assert (
            client.post(
                "/v1/rfp/run-procurement",
                headers=hdrs,
                json={"case_id": "rfp-shadow", "mode": "cached"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
                headers=hdrs,
                json={
                    "case_id": "fleet-shadow",
                    "deal_entity_id": "LOAN-SHADOW-1",
                    "borrower_name": "Meridian",
                    "mode": "cached",
                },
            ).status_code
            == 403
        )


def test_headless_fleet_callers_authorize_with_signed_user_token_or_spiffe_svid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless fleet callers authorize run-fleet-deal and run-procurement via signed JWT or SPIFFE SVID headers."""
    _enable_cloud_run_env(monkeypatch)
    app, *_ = _build_full_test_app()
    client = TestClient(app)

    fleet_payload = {
        "case_id": "fleet-headless-001",
        "case_type": "day1",
        "deal_entity_id": "LOAN-FLEET-HEADLESS-001",
        "borrower_name": "Meridian Fleet Borrower",
        "mode": "cached",
    }
    rfp_payload = {
        "case_id": "rfp-headless-001",
        "mode": "cached",
        "carrier_a_fallback": {
            "fields": {
                "doctype": "benefits-quote",
                "carrier": "Carrier A",
                "product_line": "CRITICAL_ILLNESS",
                "plan_year": 2027,
                "rates": {"EE": "12.50"},
            },
            "overall_confidence": 0.99,
            "legible": True,
        },
    }

    # Unauthenticated -> 401
    assert (
        client.post(
            "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
            json=fleet_payload,
        ).status_code
        == 401
    )
    assert client.post("/v1/rfp/run-procurement", json=rfp_payload).status_code == 401

    # Read-only X-Bridge-User-Token -> 403
    ro_jwt = mint_user_token(
        "viewer:headless", "broker", max_approval_amount="0", scope=READ_ONLY_SCOPE
    )
    assert (
        client.post(
            "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
            headers={"X-Bridge-User-Token": f"Bearer {ro_jwt}"},
            json=fleet_payload,
        ).status_code
        == 403
    )

    # Signed non-read-only X-Bridge-User-Token -> 200 on both fleet endpoints
    fleet_jwt = mint_user_token(
        "svc:fleet-runner@docubridge.io",
        "underwriter",
        max_approval_amount="25000000.00",
    )
    r_fleet_jwt = client.post(
        "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
        headers={"X-Bridge-User-Token": f"Bearer {fleet_jwt}"},
        json=fleet_payload,
    )
    assert r_fleet_jwt.status_code == 200

    r_rfp_jwt = client.post(
        "/v1/rfp/run-procurement",
        headers={"X-Bridge-User-Token": f"Bearer {fleet_jwt}"},
        json=rfp_payload,
    )
    assert r_rfp_jwt.status_code not in (401, 403)

    # Pure workload SPIFFE JWT-SVID (registered internal collector, no OBO block) -> authorized
    workload_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/ingest/docubridge-collector"
    )
    r_fleet_svid = client.post(
        "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {workload_svid}"},
        json=fleet_payload,
    )
    assert r_fleet_svid.status_code == 200

    r_rfp_svid = client.post(
        "/v1/rfp/run-procurement",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {workload_svid}"},
        json=rfp_payload,
    )
    assert r_rfp_svid.status_code not in (401, 403)

    # Unregistered Shadow AI SPIFFE SVID -> 403
    shadow_svid = mint_jwt_svid("spiffe://agents.docubridge.io/internal/shadow-bot")
    r_shadow = client.post(
        "/v1/demo/scenarios/loan-meridian-consol-2026/run-fleet-deal",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {shadow_svid}"},
        json=fleet_payload,
    )
    assert r_shadow.status_code == 403


def test_p10_6_decimal_max_approval_amount_across_models_stores_and_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 0: max_approval_amount and approvedAmount enforce exact Decimal arithmetic across memory, SQL, and HTTP."""
    # Schema and model coerce Decimal, str, int into exact Decimal
    req_str = CreateActionProgressRequest(
        entity_id="ent-dec-1",
        max_approval_amount="50000.00",  # type: ignore[arg-type]
    )
    req_int = CreateActionProgressRequest(
        entity_id="ent-dec-1",
        max_approval_amount=50000,  # type: ignore[arg-type]
    )
    req_dec = CreateActionProgressRequest(
        entity_id="ent-dec-1", max_approval_amount=Decimal("50000.25")
    )
    assert isinstance(req_str.max_approval_amount, Decimal)
    assert req_str.max_approval_amount == Decimal("50000.00")
    assert isinstance(req_int.max_approval_amount, Decimal)
    assert req_int.max_approval_amount == Decimal("50000")
    assert req_dec.max_approval_amount == Decimal("50000.25")

    with pytest.raises(ValueError):
        CreateActionProgressRequest(
            entity_id="ent-dec-1",
            max_approval_amount="-1.00",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        ActionInboxItem(
            item_id="i1",
            surface_id="s1",
            entity_id="e1",
            context_id="c1",
            session_id="se1",
            title="t",
            external_title="et",
            hint="h",
            external_hint="eh",
            max_approval_amount="NaN",  # type: ignore[arg-type]
        )

    # Beyond IEEE-754 float64 exact integer boundary: float64 collapses 9007199254740993.00 and .01!
    limit_str = "9007199254740993.00"
    over_str = "9007199254740993.01"

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'decimal_inbox.db'}"
    sql_store = SqlActionInboxStore(dsn)
    try:
        for store in (InMemoryActionInboxStore(), sql_store):
            item = store.create_action(
                entity_id="ent-precision",
                title="Precision limit check",
                hint="Exact Decimal test",
                pause_for_review=True,
                max_approval_amount=limit_str,
                data_model={
                    "transfer": {
                        "requestedAmount": limit_str,
                        "approvedAmount": limit_str,
                    }
                },
            )
            reloaded = store.get_item(item.item_id)
            assert reloaded is not None
            assert isinstance(reloaded.max_approval_amount, Decimal)
            assert reloaded.max_approval_amount == Decimal(limit_str)

            # 1 cent over limit is rejected even past float64 precision!
            with pytest.raises(ValueError, match="exceeds maximum authority limit"):
                store.resolve_action(
                    item.item_id,
                    action_request=A2uiClientActionRequest(
                        action={
                            "name": "edit_and_approve",
                            "context": {
                                "decision": "edit",
                                "transfer": {"approvedAmount": over_str},
                            },
                        }
                    ),
                    actor_principal="usr:uw@docubridge.io",
                    actor_role="underwriter",
                    channel="gemini_app",
                    principal_max_approval_amount=Decimal(limit_str),
                )

            # Exact limit passes!
            resolved = store.resolve_action(
                item.item_id,
                action_request=A2uiClientActionRequest(
                    action={
                        "name": "edit_and_approve",
                        "context": {
                            "decision": "edit",
                            "transfer": {"approvedAmount": limit_str},
                        },
                    }
                ),
                actor_principal="usr:uw@docubridge.io",
                actor_role="underwriter",
                channel="gemini_app",
                principal_max_approval_amount=Decimal(limit_str),
            )
            assert resolved["task_state"] == "completed"
    finally:
        sql_store.close()

    # HTTP route end-to-end: compound.user.max_approval_amount preserves exact Decimal (no float() cast)
    _enable_cloud_run_env(monkeypatch)
    app, *_ = _build_full_test_app()
    client = TestClient(app)
    jwt_exact = mint_user_token(
        "usr:uw@docubridge.io",
        "underwriter",
        max_approval_amount=limit_str,
    )
    headers = {"X-Bridge-User-Token": f"Bearer {jwt_exact}"}
    created = client.post(
        "/v1/a2ui/inbox/actions?channel=gemini_app",
        headers=headers,
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "pause_for_review": True,
            "data_model": {"transfer": {"approvedAmount": limit_str}},
        },
    )
    assert created.status_code == 200
    created_id = created.json()["item_id"]

    r_over = client.post(
        f"/v1/a2ui/inbox/items/{created_id}/action?channel=gemini_app",
        headers=headers,
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": over_str},
                },
            },
        },
    )
    assert r_over.status_code == 422

    r_exact = client.post(
        f"/v1/a2ui/inbox/items/{created_id}/action?channel=gemini_app",
        headers=headers,
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": limit_str},
                },
            },
        },
    )
    assert r_exact.status_code == 200


def test_sensitive_get_routes_and_dashboard_routes_enforce_auth_and_404_unknown_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sensitive /v1/* GET routes and Ops Dashboard routes reject forged headers with 401 and return 404 on unknown scenarios."""
    from bridge.dashboard.server import build_dashboard_app

    _enable_cloud_run_env(monkeypatch)
    app, *_ = _build_full_test_app()
    forged = {
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": "spoof@docubridge.io",
    }

    anon = _https_client(app)
    sensitive_gets = [
        "/v1/telemetry/spans",
        "/v1/telemetry/analytics",
        "/v1/telemetry/sql-eval",
        "/v1/bigquery/sync",
        "/v1/gym/rules",
        "/v1/gym/export/docai-dataset",
        "/v1/gym/trust-policies",
        "/v1/gym/skills/closing-disclosure/revisions",
        "/v1/registry/catalog",
        "/v1/registry/agents",
        "/v1/registry/mcp-servers",
        "/v1/registry/endpoints",
        "/v1/registry/skills",
        "/v1/registry/bindings",
        "/v1/gateway/status",
        "/v1/registry/lineage/LOAN-MERIDIAN-001",
        "/v1/catalog/spiffe-agents",
        "/v1/catalog/assets",
        "/v1/catalog/glossary",
        "/v1/refinery/revisions",
        "/v1/demo/scenarios",
        "/v1/demo/scenarios/does-not-exist/pdfs",
        "/v1/demo/scenarios/does-not-exist/pdfs.zip",
        "/v1/demo/scenarios/does-not-exist/pdfs/missing.pdf",
    ]
    for path in sensitive_gets:
        assert anon.get(path).status_code == 401, f"anonymous GET {path}"
        assert anon.get(path, headers=forged).status_code == 401, f"forged GET {path}"

    # Signed viewer cookie from GET /demo unlocks read routes and returns 404 (not 500) on unknown scenarios
    viewer = _https_client(app)
    assert viewer.get("/demo").status_code == 200
    for path in (
        "/v1/telemetry/spans",
        "/v1/bigquery/sync",
        "/v1/gym/rules",
        "/v1/registry/catalog",
        "/v1/refinery/revisions",
        "/v1/demo/scenarios",
    ):
        assert viewer.get(path).status_code == 200, f"viewer cookie GET {path}"
    for missing_path in (
        "/v1/demo/scenarios/does-not-exist/pdfs",
        "/v1/demo/scenarios/does-not-exist/pdfs.zip",
        "/v1/demo/scenarios/does-not-exist/pdfs/missing.pdf",
    ):
        assert viewer.get(missing_path).status_code == 404, missing_path

    # Ops Dashboard app enforces auth on /exchanges*, /queues/*, /rfps*, and mutating resolve/chase under K_SERVICE
    dash_app = build_dashboard_app()
    dash_anon = _https_client(dash_app)
    for dash_get in ("/exchanges", "/queues/hitl", "/queues/escalation", "/rfps"):
        assert dash_anon.get(dash_get).status_code == 401, dash_get
        assert dash_anon.get(dash_get, headers=forged).status_code == 401, dash_get
    assert (
        dash_anon.post(
            "/exchanges/s1/resolve",
            json={"resolution": "accepted", "message": "spoof"},
            headers=forged,
        ).status_code
        == 401
    )
    assert dash_anon.post("/rfps/r1/chase", json={}, headers=forged).status_code == 401
