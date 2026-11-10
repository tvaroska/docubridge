"""Consolidation Rules Engine, AlloyDB Bi-Temporal Store, Role-Based Views & BigQuery Sync.

Implements Zone 3 (Consolidation Rules Engine + AlloyDB Operational Store) and Zone 4
(Role-Based Business Views + BigQuery Analytics Sync) from the Enterprise Data
Architecture.
"""

from __future__ import annotations

from bridge.knowledge_catalog.query_validator import (
    UncertifiedMetricQueryError,
    validate_consolidation_view,
    validate_metric_query,
)
from bridge.operational_store.consolidation_rules import (
    _winning_key,
    compute_financial_summary,
    consolidate_entry,
    parse_decimal_money,
    resolve_active_facts,
    source_priority_for_doctype,
)
from bridge.operational_store.iceberg_sync import (
    _ACTIVE_FACTS_CTE,
    BIGQUERY_ICEBERG_DDL,
    SQL_VIEW_DEFINITIONS,
    build_bigquery_sync_batch,
)
from bridge.operational_store.memory_store import (
    InMemoryOperationalStore,
    OperationalStore,
)
from bridge.operational_store.models import (
    _MONEY_KEYS,
    _NUMBER_RE,
    _OP_TIMEOUT_S,
    FACTS_TABLE_NAME,
    OPERATIONAL_DB_ENV,
    BigQuerySyncBatch,
    BigQuerySyncRecord,
    BrokerQuoteRow,
    BrokerView,
    ClaimsActiveCoverage,
    ClaimsView,
    ConsolidatedFact,
    FactConflict,
    FinancialSummary,
    SourceTrace,
    UnderwriterExposureItem,
    UnderwriterView,
    _utc_dt,
    _utc_iso,
)
from bridge.operational_store.sql_store import (
    _SQL_CACHE_LOCK,
    _SQL_STORE_CACHE,
    SqlOperationalStore,
    operational_store_from_env,
)

__all__ = [
    "BIGQUERY_ICEBERG_DDL",
    "FACTS_TABLE_NAME",
    "OPERATIONAL_DB_ENV",
    "SQL_VIEW_DEFINITIONS",
    "_ACTIVE_FACTS_CTE",
    "_MONEY_KEYS",
    "_NUMBER_RE",
    "_OP_TIMEOUT_S",
    "_SQL_CACHE_LOCK",
    "_SQL_STORE_CACHE",
    "BigQuerySyncBatch",
    "BigQuerySyncRecord",
    "BrokerQuoteRow",
    "BrokerView",
    "ClaimsActiveCoverage",
    "ClaimsView",
    "ConsolidatedFact",
    "FactConflict",
    "FinancialSummary",
    "InMemoryOperationalStore",
    "OperationalStore",
    "SourceTrace",
    "SqlOperationalStore",
    "UncertifiedMetricQueryError",
    "UnderwriterExposureItem",
    "UnderwriterView",
    "_utc_dt",
    "_utc_iso",
    "_winning_key",
    "build_bigquery_sync_batch",
    "compute_financial_summary",
    "consolidate_entry",
    "operational_store_from_env",
    "parse_decimal_money",
    "resolve_active_facts",
    "source_priority_for_doctype",
    "validate_consolidation_view",
    "validate_metric_query",
]
