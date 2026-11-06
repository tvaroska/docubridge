"""Tests for Hexagonal Ports & Adapters (`bridge/ports/`) across all 13 boundaries and Cloud SQL IAM auth."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
from bridge.extraction.derivations import (
    apply_field_derivations,
    apply_result_derivations,
)
from bridge.knowledge_catalog import (
    InMemoryKnowledgeCatalog,
    KnowledgeCatalog,
    SqlKnowledgeCatalog,
    knowledge_catalog_from_env,
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
    KnowledgeCatalogPort,
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
    GcpKnowledgeCatalog,
    GcpOperationalStore,
)
from bridge.ports.local import (
    LocalActionInbox,
    LocalAgentGym,
    LocalAuditVault,
    LocalKnowledgeCatalog,
    LocalOperationalStore,
)
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


def test_ports_package_unifies_all_fourteen_boundaries():
    """Verify `bridge.ports` defines all 14 managed-service boundaries and `bridge/seams` is removed."""
    repo_root = Path(__file__).resolve().parents[1]
    assert not (repo_root / "bridge" / "seams").exists()

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
        "knowledge_catalog",
    }
    assert expected == PORT_NAMES
    assert len(Port) == 14
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


def test_port_env_var_precedence(monkeypatch):
    """Verify `BRIDGE_PORT_<NAME>` -> `BRIDGE_PORT_MODE` -> `local` precedence."""
    for p in Port:
        monkeypatch.delenv(port_env_var(p), raising=False)
    monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)

    assert port_env_var(Port.operational_store) == "BRIDGE_PORT_OPERATIONAL_STORE"
    assert resolve_port_mode(Port.operational_store) is PortMode.local

    # Global port mode applies when per-port override is unset
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    assert resolve_port_mode(Port.operational_store) is PortMode.gcp

    # Per-port override wins over global
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


@pytest.mark.asyncio
async def test_collect_executor_and_build_a2a_app_enterprise_store_binding():
    """Verify `CollectExecutor` and `build_a2a_app` bind Zone 3–6 stores (`vault_store`, `operational_store`, `knowledge_catalog`)."""
    from bridge.collect import DocumentSubmission
    from bridge.edge.executor import CollectExecutor
    from bridge.edge.server import build_a2a_app
    from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
    from contract.wire import ArtifactRef, CollectRequest

    vault = InMemoryAuditVaultStore()
    op_store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=op_store)

    executor = CollectExecutor()
    app = build_a2a_app(
        executor=executor,
        vault_store=vault,
        operational_store=op_store,
        knowledge_catalog=catalog,
    )
    assert app is not None
    assert executor._collector._vault_store is vault
    assert executor._collector._operational_store is op_store
    assert executor._collector._knowledge_catalog is catalog

    turn = await executor._collector.collect(
        CollectRequest(
            process="address-change",
            context_id="ctx-edge-ent-1",
            party="PARTY-ENT-001",
        ),
        documents=[
            DocumentSubmission(
                doc_id="doc-ent-1",
                artifact_name="bill-powerco-clean.pdf",
                artifact=ArtifactRef(uri="memory://bill-powerco-clean.pdf"),
            )
        ],
        candidate_doctypes=["utility-bill", "bank-statement"],
    )
    assert len(turn.status.entries) == 1
    assert vault.get_record("ctx-edge-ent-1", "doc-ent-1", 1) is not None
    assert len(op_store.list_facts("PARTY-ENT-001")) > 0


@pytest.mark.port("knowledge_catalog")
def test_knowledge_catalog_port_local_gcp_and_cross_replica_read_after_write(
    monkeypatch, tmp_path
):
    """Verify `Port.knowledge_catalog` local/GCP adapters and cross-replica TBox graph persistence (`SqlKnowledgeCatalog`)."""
    local_adapter = get_port_adapter(Port.knowledge_catalog, PortMode.local)
    gcp_adapter = get_port_adapter(Port.knowledge_catalog, PortMode.gcp)
    assert isinstance(local_adapter, KnowledgeCatalogPort)
    assert isinstance(local_adapter, LocalKnowledgeCatalog)
    assert isinstance(gcp_adapter, KnowledgeCatalogPort)
    assert isinstance(gcp_adapter, GcpKnowledgeCatalog)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGE_OPERATIONAL_DB_URL", raising=False)
    monkeypatch.delenv("BRIDGE_CATALOG_DB_URL", raising=False)
    monkeypatch.setenv("BRIDGE_PORT_MODE", "local")
    in_mem = knowledge_catalog_from_env()
    assert isinstance(in_mem, KnowledgeCatalog)
    assert isinstance(in_mem, InMemoryKnowledgeCatalog)

    # GCP mode refuses in-memory fallback without a DSN
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        knowledge_catalog_from_env()

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'catalog_port.sqlite'}"
    monkeypatch.setenv("BRIDGE_CATALOG_DB_URL", dsn)
    replica_a = knowledge_catalog_from_env()
    replica_b = SqlKnowledgeCatalog(dsn)
    try:
        assert isinstance(replica_a, SqlKnowledgeCatalog)
        baseline = replica_a.get_active_ontology_revision()
        assert baseline.revision_id == "ont-rev-v1-1-baseline"
        promoted = baseline.model_copy(
            update={
                "revision_id": "ont-rev-v1-2-port-test",
                "version": "1.2.0",
                "parent_revision_id": baseline.revision_id,
            }
        )
        replica_a.register_ontology_revision(promoted)

        # Second independent replica handle sees the promoted revision and edges immediately
        active_on_b = replica_b.get_active_ontology_revision()
        assert active_on_b.revision_id == "ont-rev-v1-2-port-test"
        assert active_on_b.version == "1.2.0"
        assert len(active_on_b.edges) == len(baseline.edges)
    finally:
        replica_a.close()
        replica_b.close()


@pytest.mark.asyncio
async def test_widened_extraction_port_and_stage_b_derivations():
    """Verify `ExtractionPort` scope kwargs, `ExtractedRecord`, and Stage-B `FieldDerivation` receipts."""
    from contract.extraction import (
        ExtractedRecord,
        ExtractionFields,
        ExtractionResult,
        SourceAnchor,
    )

    # 1. Stage-B Coinsurance Polarity Inversion (100% - 20% Member -> 80% Employer)
    fields = ExtractionFields(
        doctype="benefits-quote",
        issuer="Carrier Alpha",
        key_fields={
            "member_coinsurance_pct": "20%",
            "deductible": "$500",
        },
        records=[
            ExtractedRecord(
                entity_id="plan-option-1-in-network",
                entity_type="medical-plan-tier",
                page_range=(1, 2),
                key_fields={"member_coinsurance_pct": "20%", "deductible": "$500"},
                source_anchors={
                    "member_coinsurance_pct": SourceAnchor(
                        page_number=2,
                        bounding_box=(0.12, 0.30, 0.45, 0.36),
                        snippet="Member Pays 20% after deductible",
                    )
                },
            )
        ],
    )
    derived_fields, _ = apply_field_derivations(fields)
    assert derived_fields.key_fields["employer_coinsurance_pct"] == "80%"
    deriv = derived_fields.derivations["employer_coinsurance_pct"]
    assert deriv.polarity == "MEMBER_PAYS"
    assert deriv.raw_value == "20%"
    assert deriv.normalized_value == "80%"
    assert deriv.derivation_rule == "coinsurance-polarity-inversion"
    assert deriv.receipt_id.startswith("rcpt-")
    assert derived_fields.records[0].entity_id == "plan-option-1-in-network"
    assert derived_fields.records[0].key_fields["employer_coinsurance_pct"] == "80%"
    assert (
        derived_fields.records[0]
        .derivations["employer_coinsurance_pct"]
        .derivation_rule
        == "coinsurance-polarity-inversion"
    )

    # 2. Stage-B Paid-in-Full Wire -> Remaining Liability Balance ($0.00)
    wire_result = ExtractionResult(
        fields=ExtractionFields(
            doctype="consolidation-payoff",
            issuer="EquipLease Financial Corp",
            key_fields={
                "wire_reference": "FINAL PAYOFF - LOAN #8841 - PAID IN FULL",
            },
        ),
        overall_confidence=0.98,
        legible=True,
    )
    final_wire = apply_result_derivations(
        wire_result, ontology_revision_id="ont-rev-v1-2-consol"
    )
    assert final_wire.fields.key_fields["remaining_liability_balance"] == "$0.00"
    assert (
        final_wire.fields.derivations["remaining_liability_balance"].derivation_rule
        == "paid-in-full-wire-zero-balance"
    )
    assert final_wire.ontology_revision_id == "ont-rev-v1-2-consol"

    # 3. FixtureExtractor / LocalExtraction supports requested_fields and ontology_revision
    local_ext = get_port_adapter(Port.extraction, PortMode.local)
    scoped_res = await local_ext.extract(
        "gov-id-clean",
        target_doctype="gov-id",
        requested_fields=["name"],
        ontology_revision="ont-rev-v1-1-baseline",
    )
    assert list(scoped_res.fields.key_fields.keys()) == ["name"]
    assert scoped_res.ontology_revision_id == "ont-rev-v1-1-baseline"
