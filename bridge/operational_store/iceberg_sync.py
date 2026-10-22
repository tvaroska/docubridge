"""Zone 4 Governed SQL View Definitions & BigQuery Open Iceberg CDC Sync."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from bridge.operational_store.models import (
    BigQuerySyncBatch,
    BigQuerySyncRecord,
    ConsolidatedFact,
)

_ACTIVE_FACTS_CTE: str = (
    "WITH active_facts AS ("
    "SELECT *, ROW_NUMBER() OVER ("
    "PARTITION BY entity_id, field_path "
    "ORDER BY source_priority DESC, effective_from DESC, recorded_at DESC, version DESC"
    ") AS rn FROM bridge_operational_facts"
    ") "
)

# Governed SQL definitions for the three Zone 4 Role-Based Views & BigQuery Iceberg DDL.
SQL_VIEW_DEFINITIONS: dict[str, str] = {
    "v_broker_placement": (
        "CREATE OR REPLACE VIEW v_broker_placement AS "
        + _ACTIVE_FACTS_CTE
        + "SELECT entity_id, issuer, doc_id, doctype, field_path, field_value, "
        "numeric_value, currency, effective_from, recorded_at, vault_uri, "
        "source_anchor_json FROM active_facts WHERE rn = 1;"
    ),
    "v_claims_coverage": (
        "CREATE OR REPLACE VIEW v_claims_coverage AS "
        + _ACTIVE_FACTS_CTE
        + "SELECT entity_id, issuer, doc_id, doctype, field_path, field_value, "
        "numeric_value, currency, source_priority, effective_from, recorded_at, "
        "vault_uri, source_anchor_json FROM active_facts WHERE rn = 1;"
    ),
    "v_underwriter_exposure": (
        "CREATE OR REPLACE VIEW v_underwriter_exposure AS "
        + _ACTIVE_FACTS_CTE
        + "SELECT entity_id, doc_id, doctype, field_path, field_value, numeric_value, "
        "currency, flagged_for_review, effective_from, recorded_at, vault_uri, "
        "source_anchor_json FROM active_facts WHERE rn = 1;"
    ),
}

BIGQUERY_ICEBERG_DDL: str = (
    "CREATE TABLE IF NOT EXISTS `docubridge_lakehouse.bq_operational_facts_iceberg` (\n"
    "  sync_id STRING NOT NULL,\n"
    "  entity_id STRING NOT NULL,\n"
    "  fact_id STRING NOT NULL,\n"
    "  doc_id STRING NOT NULL,\n"
    "  doctype STRING NOT NULL,\n"
    "  issuer STRING,\n"
    "  field_path STRING NOT NULL,\n"
    "  field_value STRING NOT NULL,\n"
    "  numeric_value NUMERIC,\n"
    "  currency STRING NOT NULL,\n"
    "  recorded_at TIMESTAMP NOT NULL,\n"
    "  effective_from TIMESTAMP NOT NULL,\n"
    "  vault_uri STRING,\n"
    "  page_number INT64\n"
    ")\n"
    "WITH CONNECTION `us.lakehouse-connection`\n"
    "OPTIONS (file_format = 'PARQUET', table_format = 'ICEBERG');"
)


def build_bigquery_sync_batch(
    facts: list[ConsolidatedFact],
    *,
    since_recorded_at: datetime | None = None,
) -> BigQuerySyncBatch:
    """Build a BigQuerySyncBatch from filtered ConsolidatedFact records."""
    records = [
        BigQuerySyncRecord(
            sync_id=f"sync-{f.fact_id}",
            entity_id=f.entity_id,
            fact_id=f.fact_id,
            doc_id=f.doc_id,
            doctype=f.doctype,
            issuer=f.issuer,
            field_path=f.field_path,
            field_value=f.field_value,
            numeric_value=f.numeric_value,
            currency=f.currency,
            recorded_at=f.recorded_at,
            effective_from=f.effective_from,
            vault_uri=f.vault_uri,
            page_number=f.source_anchor.page_number if f.source_anchor else 1,
        )
        for f in facts
    ]
    watermark = max((r.recorded_at for r in records), default=since_recorded_at)
    return BigQuerySyncBatch(
        batch_id=uuid.uuid4().hex,
        exported_at=datetime.now(UTC),
        row_count=len(records),
        watermark_recorded_at=watermark,
        records=records,
    )
