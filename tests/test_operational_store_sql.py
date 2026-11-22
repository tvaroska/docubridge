"""SQL Operational Store round-trip and schema migration tests (P13-4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bridge.operational_store.models import ConsolidatedFact
from bridge.operational_store.sql_store import SqlOperationalStore


def test_sql_store_round_trips_lineage_fields(tmp_path: Path) -> None:
    """SqlOperationalStore persists and reloads ontology_revision_id and consolidation_edge_id."""
    db_file = tmp_path / "op_lineage.sqlite"
    dsn = f"sqlite+aiosqlite:///{db_file}"
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    fact = ConsolidatedFact(
        fact_id="fact-lineage-1",
        entity_id="ENT-LIN-1",
        context_id="ctx-lin-1",
        doc_id="doc-lin-1",
        doctype="wind-deductible-endorsement",
        issuer="Acme",
        field_path="fields.key_fields.deductible",
        field_value="$100,000.00",
        numeric_value="100000.00",
        currency="USD",
        source_priority=40,
        recorded_at=now,
        effective_from=now,
        version=1,
        ontology_revision_id="ont-rev-v1-2-refinery",
        consolidation_edge_id="edge-wind-endorsement-feeds-deductible",
    )

    writer = SqlOperationalStore(dsn)
    try:
        assert writer.append_facts([fact]) == 1
    finally:
        writer.close()

    reader = SqlOperationalStore(dsn)
    try:
        loaded = reader.list_facts("ENT-LIN-1")
        assert len(loaded) == 1
        got = loaded[0]
        assert got.ontology_revision_id == "ont-rev-v1-2-refinery"
        assert got.consolidation_edge_id == "edge-wind-endorsement-feeds-deductible"

        trace = got.trace()
        assert trace.ontology_revision_id == "ont-rev-v1-2-refinery"
        assert trace.consolidation_edge_id == "edge-wind-endorsement-feeds-deductible"

        batch = reader.export_bigquery_sync_batch("ENT-LIN-1")
        assert batch.row_count == 1
        assert batch.records[0].ontology_revision_id == "ont-rev-v1-2-refinery"
        assert (
            batch.records[0].consolidation_edge_id
            == "edge-wind-endorsement-feeds-deductible"
        )
    finally:
        reader.close()


def test_sql_store_migrates_legacy_schema_without_lineage_columns(
    tmp_path: Path,
) -> None:
    """Opening SqlOperationalStore against a pre-P13 bridge_operational_facts table runs ALTER TABLE."""
    import sqlite3

    from bridge.operational_store.models import FACTS_TABLE_NAME

    assert FACTS_TABLE_NAME == "bridge_operational_facts"
    db_file = tmp_path / "legacy_op.sqlite"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            """
            CREATE TABLE bridge_operational_facts (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id VARCHAR(64) UNIQUE NOT NULL,
                entity_id VARCHAR(128) NOT NULL,
                context_id VARCHAR(128) NOT NULL,
                doc_id VARCHAR(128) NOT NULL,
                doctype VARCHAR(128) NOT NULL,
                issuer VARCHAR(128),
                field_path VARCHAR(256) NOT NULL,
                field_value TEXT NOT NULL,
                numeric_value VARCHAR(64),
                currency VARCHAR(16) NOT NULL,
                source_priority INTEGER NOT NULL,
                recorded_at VARCHAR(64) NOT NULL,
                effective_from VARCHAR(64) NOT NULL,
                version INTEGER NOT NULL,
                source_anchor_json TEXT,
                vault_uri TEXT,
                flagged_for_review BOOLEAN NOT NULL DEFAULT 0,
                skill_version VARCHAR(64) NOT NULL DEFAULT '1.0.0',
                skill_revision_id VARCHAR(128) NOT NULL DEFAULT 'rev-v1-0-baseline',
                skill_urn VARCHAR(256),
                process_skill_revision_id VARCHAR(128) NOT NULL DEFAULT 'rev-v1-0-baseline',
                compiled_rule_ids_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bridge_operational_facts (
                fact_id, entity_id, context_id, doc_id, doctype, issuer,
                field_path, field_value, numeric_value, currency,
                source_priority, recorded_at, effective_from, version
            ) VALUES (
                'fact-legacy-0', 'ENT-MIG', 'ctx-mig', 'doc-legacy', 'policy', 'Acme',
                'fields.key_fields.deductible', '$5,000.00', '5000.00', 'USD',
                30, '2026-04-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00', 1
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    dsn = f"sqlite+aiosqlite:///{db_file}"
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    fact = ConsolidatedFact(
        fact_id="fact-mig-1",
        entity_id="ENT-MIG",
        context_id="ctx-mig",
        doc_id="doc-mig",
        doctype="endorsement",
        field_path="fields.key_fields.premium",
        field_value="$25,000.00",
        numeric_value="25000.00",
        currency="USD",
        source_priority=40,
        recorded_at=now,
        effective_from=now,
        version=1,
        ontology_revision_id="ont-rev-v1-3-migrated",
        consolidation_edge_id="edge-migrated",
    )
    store = SqlOperationalStore(dsn)
    try:
        assert store.append_facts([fact]) == 1
        reloaded = {f.fact_id: f for f in store.list_facts("ENT-MIG")}
        assert set(reloaded) == {"fact-legacy-0", "fact-mig-1"}
        assert reloaded["fact-legacy-0"].ontology_revision_id == "ont-rev-v1-1-baseline"
        assert reloaded["fact-legacy-0"].consolidation_edge_id is None
        assert reloaded["fact-mig-1"].ontology_revision_id == "ont-rev-v1-3-migrated"
        assert reloaded["fact-mig-1"].consolidation_edge_id == "edge-migrated"
    finally:
        store.close()
