"""Tests for Hexagonal Ports & Adapters (`bridge/ports/`) across all 13 boundaries and Cloud SQL IAM auth."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridge.a2ui_inbox import (
    ActionInboxStore,
    InMemoryActionInboxStore,
    SqlActionInboxStore,
    action_inbox_store_from_env,
)
from bridge.agent_gym import (
    AgentGymEngine,
    InMemoryAgentGymEngine,
    SqlAgentGymEngine,
    agent_gym_from_env,
)
from bridge.operational_store import (
    ConsolidatedFact,
    InMemoryOperationalStore,
    OperationalStore,
    SqlOperationalStore,
    operational_store_from_env,
)
from bridge.ports import (
    GCP_ADAPTERS,
    LOCAL_ADAPTERS,
    PORT_NAMES,
    ActionInboxPort,
    AdapterMode,
    AgentGymPort,
    AuditVaultPort,
    OperationalStorePort,
    Port,
    PortABC,
    PortMode,
    get_port_adapter,
    is_valid_port,
    port_env_var,
    resolve_port_mode,
)
from bridge.ports.gcp import (
    GcpActionInbox,
    GcpAgentGym,
    GcpAuditVault,
    GcpOperationalStore,
)
from bridge.ports.local import (
    LocalActionInbox,
    LocalAgentGym,
    LocalAuditVault,
    LocalOperationalStore,
)
from bridge.seams import Seam, SeamMode, seam_env_var
from bridge.sql_runtime import (
    IAM_AUTH_ENV,
    create_async_engine_with_iam,
    enable_iam_auth,
    is_iam_auth_enabled,
)
from bridge.vault import (
    AuditVaultStore,
    GcpAuditVaultStore,
    InMemoryAuditVaultStore,
    vault_from_env,
)


def test_ports_package_unifies_all_thirteen_boundaries():
    """Verify `bridge.ports` defines all 13 managed-service boundaries and aliases `bridge.seams`."""
    expected = {
        "sessions",
        "task_store",
        "exchange_store",
        "skill_registry",
        "memory_bank",
        "scheduler",
        "a2a_client",
        "extraction",
        "email_ingress",
        "audit_vault",
        "operational_store",
        "action_inbox",
        "agent_gym",
    }
    assert expected == PORT_NAMES
    assert len(Port) == 13
    assert Port is Seam
    assert PortMode is SeamMode
    assert AdapterMode is PortMode

    for port in Port:
        assert is_valid_port(port.value)
        assert port in LOCAL_ADAPTERS
        assert port in GCP_ADAPTERS
        local_inst = get_port_adapter(port, PortMode.local)
        gcp_inst = get_port_adapter(port, PortMode.gcp)
        assert isinstance(local_inst, PortABC)
        assert isinstance(gcp_inst, PortABC)
        assert local_inst.port is port
        assert gcp_inst.port is port


def test_port_env_var_precedence_and_backwards_compatibility(monkeypatch):
    """Verify `BRIDGE_PORT_<NAME>` -> `BRIDGE_SEAM_<NAME>` -> `BRIDGE_PORT_MODE` -> `BRIDGE_SEAM_MODE` -> `local`."""
    for p in Port:
        monkeypatch.delenv(port_env_var(p), raising=False)
        monkeypatch.delenv(seam_env_var(p), raising=False)
    monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)
    monkeypatch.delenv("BRIDGE_SEAM_MODE", raising=False)

    assert port_env_var(Port.operational_store) == "BRIDGE_PORT_OPERATIONAL_STORE"
    assert resolve_port_mode(Port.operational_store) is PortMode.local

    # Legacy global fallback
    monkeypatch.setenv("BRIDGE_SEAM_MODE", "gcp")
    assert resolve_port_mode(Port.operational_store) is PortMode.gcp

    # Primary global wins over legacy global
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    assert resolve_port_mode(Port.operational_store) is PortMode.local

    # Legacy per-seam override wins over global
    monkeypatch.setenv("BRIDGE_SEAM_OPERATIONAL_STORE", "gcp")
    assert resolve_port_mode(Port.operational_store) is PortMode.gcp

    # Primary per-port override wins over legacy per-seam override
    monkeypatch.setenv("BRIDGE_PORT_OPERATIONAL_STORE", "local")
    assert resolve_port_mode(Port.operational_store) is PortMode.local


@pytest.mark.port("audit_vault")
def test_audit_vault_port_local_and_gcp_adapters(monkeypatch):
    """Verify `Port.audit_vault` local/GCP adapters, WORM lock roundtrip, and `vault_from_env()` wiring."""
    local_adapter = get_port_adapter(Port.audit_vault, PortMode.local)
    gcp_adapter = get_port_adapter(Port.audit_vault, PortMode.gcp)
    assert isinstance(local_adapter, AuditVaultPort)
    assert isinstance(local_adapter, LocalAuditVault)
    assert isinstance(gcp_adapter, AuditVaultPort)
    assert isinstance(gcp_adapter, GcpAuditVault)

    monkeypatch.delenv("BRIDGE_VAULT_BUCKET", raising=False)
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    vault = vault_from_env()
    assert isinstance(vault, AuditVaultStore)
    assert isinstance(vault, InMemoryAuditVaultStore)

    rec = vault.lock_document(
        context_id="ctx-port-1",
        doc_id="doc-port-1",
        version=1,
        filename="closing.pdf",
        data=b"%PDF-1.7 port-test",
    )
    assert rec.retention_locked is True
    assert vault.read_bytes("ctx-port-1", "doc-port-1", 1) == b"%PDF-1.7 port-test"

    # GCP mode refuses in-memory fallback when BRIDGE_VAULT_BUCKET is unset
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        vault_from_env()

    # GCP mode builds GcpAuditVaultStore when BRIDGE_VAULT_BUCKET is configured
    monkeypatch.setenv("BRIDGE_VAULT_BUCKET", "docubridge-test-vault-bucket")
    gcp_vault = vault_from_env()
    assert isinstance(gcp_vault, GcpAuditVaultStore)
    assert gcp_vault.bucket_name == "docubridge-test-vault-bucket"


@pytest.mark.port("operational_store")
def test_operational_store_port_local_and_gcp_adapters(monkeypatch, tmp_path):
    """Verify `Port.operational_store` local/GCP adapters, SQLite roundtrip, and `operational_store_from_env()`."""
    local_adapter = get_port_adapter(Port.operational_store, PortMode.local)
    gcp_adapter = get_port_adapter(Port.operational_store, PortMode.gcp)
    assert isinstance(local_adapter, OperationalStorePort)
    assert isinstance(local_adapter, LocalOperationalStore)
    assert isinstance(gcp_adapter, OperationalStorePort)
    assert isinstance(gcp_adapter, GcpOperationalStore)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGE_OPERATIONAL_DB_URL", raising=False)
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    in_mem = operational_store_from_env()
    assert isinstance(in_mem, OperationalStore)
    assert isinstance(in_mem, InMemoryOperationalStore)

    # GCP mode refuses in-memory fallback without a DSN
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        operational_store_from_env()

    # Sync Postgres DSN is rejected by GcpOperationalStore
    with pytest.raises(ValueError, match="async Postgres driver"):
        gcp_adapter.build_operational_store(
            db_url="postgresql://user:pass@localhost/db"
        )

    # Durable SQLite DSN works through GcpOperationalStore and operational_store_from_env()
    dsn = f"sqlite+aiosqlite:///{tmp_path / 'op_port.sqlite'}"
    monkeypatch.setenv("BRIDGE_OPERATIONAL_DB_URL", dsn)
    sql_store = operational_store_from_env()
    try:
        assert isinstance(sql_store, SqlOperationalStore)
        now = datetime.now(UTC)
        fact = ConsolidatedFact(
            fact_id="fact-port-1",
            entity_id="LOAN-PORT-001",
            context_id="ctx-port-1",
            doc_id="doc-1",
            doctype="credit-agreement",
            issuer="Meridian Bank",
            field_path="fields.key_fields.loan_amount",
            field_value="$10,000,000.00",
            numeric_value="10000000.00",
            currency="USD",
            source_priority=30,
            recorded_at=now,
            effective_from=now,
            version=1,
        )
        assert sql_store.append_facts([fact]) == 1
        facts = sql_store.list_facts("LOAN-PORT-001")
        assert len(facts) == 1
        assert facts[0].numeric_value == "10000000.00"
    finally:
        sql_store.close()


@pytest.mark.port("action_inbox")
def test_action_inbox_port_local_and_gcp_adapters(monkeypatch, tmp_path):
    """Verify `Port.action_inbox` local/GCP adapters, SQLite roundtrip, and `action_inbox_store_from_env()`."""
    local_adapter = get_port_adapter(Port.action_inbox, PortMode.local)
    gcp_adapter = get_port_adapter(Port.action_inbox, PortMode.gcp)
    assert isinstance(local_adapter, ActionInboxPort)
    assert isinstance(local_adapter, LocalActionInbox)
    assert isinstance(gcp_adapter, ActionInboxPort)
    assert isinstance(gcp_adapter, GcpActionInbox)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGE_OPERATIONAL_DB_URL", raising=False)
    monkeypatch.delenv("BRIDGE_INBOX_DB_URL", raising=False)
    monkeypatch.delenv("BRIDGE_INBOX_STORE_MODE", raising=False)
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    in_mem = action_inbox_store_from_env()
    assert isinstance(in_mem, ActionInboxStore)
    assert isinstance(in_mem, InMemoryActionInboxStore)

    # GCP mode refuses in-memory fallback without a DSN
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        action_inbox_store_from_env()

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'inbox_port.sqlite'}"
    monkeypatch.setenv("BRIDGE_INBOX_DB_URL", dsn)
    sql_inbox = action_inbox_store_from_env()
    try:
        assert isinstance(sql_inbox, SqlActionInboxStore)
        item = sql_inbox.create_action(
            entity_id="LOAN-PORT-001",
            context_id="ctx-port-1",
            title="Approve Port Test Covenant",
            hint="Port parity test",
            data_model={"loan_amount": "10000000.00"},
        )
        fetched = sql_inbox.get_item(item.item_id)
        assert fetched is not None
        assert fetched.title == "Approve Port Test Covenant"
    finally:
        sql_inbox.close()


@pytest.mark.port("agent_gym")
def test_agent_gym_port_local_and_gcp_adapters(monkeypatch, tmp_path):
    """Verify `Port.agent_gym` local/GCP adapters, SQLite roundtrip, and `agent_gym_from_env()`."""
    local_adapter = get_port_adapter(Port.agent_gym, PortMode.local)
    gcp_adapter = get_port_adapter(Port.agent_gym, PortMode.gcp)
    assert isinstance(local_adapter, AgentGymPort)
    assert isinstance(local_adapter, LocalAgentGym)
    assert isinstance(gcp_adapter, AgentGymPort)
    assert isinstance(gcp_adapter, GcpAgentGym)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGE_OPERATIONAL_DB_URL", raising=False)
    monkeypatch.delenv("BRIDGE_GYM_DB_URL", raising=False)
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    in_mem = agent_gym_from_env()
    assert isinstance(in_mem, AgentGymEngine)
    assert isinstance(in_mem, InMemoryAgentGymEngine)

    # GCP mode refuses in-memory fallback without a DSN
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        agent_gym_from_env()

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'gym_port.sqlite'}"
    monkeypatch.setenv("BRIDGE_GYM_DB_URL", dsn)
    sql_gym = agent_gym_from_env()
    try:
        assert isinstance(sql_gym, SqlAgentGymEngine)
        signal, rule = sql_gym.distill_from_quarantine_correction(
            entity_id="LOAN-PORT-001",
            doc_id="doc-port-1",
            doctype="closing-disclosure",
            issuer="Meridian Bank",
            field_path="origination_fee",
            before_value="$4,250.00",
            after_value="$8,500.00",
            actor_principal="credit.lead@docubridge.io",
            expert_memo="Rate-lock addendum overrides Section A origination fee.",
            citation_verified=True,
            review_dwell_ms=4200,
        )
        assert signal.signal_id
        assert rule.rule_id
    finally:
        sql_gym.close()


def test_cloud_sql_iam_auth_do_connect_token_injection(monkeypatch):
    """Verify `enable_iam_auth` and `create_async_engine_with_iam` inject short-lived OAuth2 tokens on `do_connect`."""
    from sqlalchemy import event

    monkeypatch.delenv(IAM_AUTH_ENV, raising=False)
    assert is_iam_auth_enabled() is False
    monkeypatch.setenv(IAM_AUTH_ENV, "1")
    assert is_iam_auth_enabled() is True

    minted_tokens: list[str] = []

    def _fake_token_fetcher() -> str:
        tok = f"fake-cloudsql-iam-token-{len(minted_tokens) + 1}"
        minted_tokens.append(tok)
        return tok

    engine = create_async_engine_with_iam(
        "sqlite+aiosqlite:///:memory:",
        token_fetcher=None,
    )
    # Explicitly attach IAM auth with our fake token fetcher to verify the SQLAlchemy do_connect hook
    enable_iam_auth(engine, token_fetcher=_fake_token_fetcher)
    assert event.contains(engine.sync_engine, "do_connect", lambda *a: None) is False

    # Simulate SQLAlchemy dialect firing do_connect with empty password vs explicit password
    cparams_empty: dict[str, str] = {"user": "bridge-runtime@project.iam"}
    engine.sync_engine.dialect.dispatch.do_connect(None, None, (), cparams_empty)
    assert cparams_empty["password"] == "fake-cloudsql-iam-token-1"
    assert len(minted_tokens) == 1

    # Explicit password in cparams is preserved without calling the IAM token fetcher
    cparams_explicit: dict[str, str] = {
        "user": "bridge",
        "password": "explicit-secret-password",
    }
    engine.sync_engine.dialect.dispatch.do_connect(None, None, (), cparams_explicit)
    assert cparams_explicit["password"] == "explicit-secret-password"
    assert len(minted_tokens) == 1
