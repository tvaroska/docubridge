"""Local adapters for Zone 3–6 Enterprise Data Architecture ports."""

from __future__ import annotations

import os
from typing import Any

from ..protocols import (
    ActionInboxPort,
    AgentGymPort,
    AuditVaultPort,
    KnowledgeCatalogPort,
    OperationalStorePort,
)
from .stores import require_async_driver


class LocalAuditVault(AuditVaultPort):
    """Local `audit_vault` adapter (Zone 3): in-memory WORM vault or GCS when bucket is supplied."""

    def build_vault(self, bucket_name: str | None = None) -> Any:
        """Construct an in-memory Audit Vault (or GCS vault when a bucket is explicitly configured)."""
        from bridge.vault import (
            VAULT_BUCKET_ENV,
            GcpAuditVaultStore,
            InMemoryAuditVaultStore,
        )

        bucket = (bucket_name or os.environ.get(VAULT_BUCKET_ENV, "")).strip()
        if bucket:
            return GcpAuditVaultStore(bucket)
        return InMemoryAuditVaultStore()


class LocalOperationalStore(OperationalStorePort):
    """Local `operational_store` adapter (Zone 3–4): in-memory or SQLite-backed bi-temporal store."""

    def build_operational_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed OperationalStore for local execution."""
        from bridge.operational_store import (
            _SQL_CACHE_LOCK,
            _SQL_STORE_CACHE,
            OPERATIONAL_DB_ENV,
            InMemoryOperationalStore,
            SqlOperationalStore,
        )

        dsn = (
            db_url
            or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        )
        if not dsn:
            return InMemoryOperationalStore()
        require_async_driver(dsn)
        with _SQL_CACHE_LOCK:
            cached = _SQL_STORE_CACHE.get(dsn)
            if cached is None:
                cached = SqlOperationalStore(dsn)
                _SQL_STORE_CACHE[dsn] = cached
        return cached


class LocalActionInbox(ActionInboxPort):
    """Local `action_inbox` adapter (Zone 5): in-memory singleton or SQLite-backed inbox store."""

    def build_action_inbox_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed ActionInboxStore for local execution."""
        import bridge.a2ui_inbox as inbox_mod
        from bridge.a2ui_inbox import (
            _SQL_INBOX_CACHE,
            _SQL_INBOX_LOCK,
            INBOX_DB_ENV,
            InMemoryActionInboxStore,
            SqlActionInboxStore,
        )

        mode = os.environ.get("BRIDGE_INBOX_STORE_MODE", "").strip().lower()
        if mode == "memory" and not db_url:
            if inbox_mod._DEFAULT_INBOX_STORE is None:
                inbox_mod._DEFAULT_INBOX_STORE = InMemoryActionInboxStore()
            return inbox_mod._DEFAULT_INBOX_STORE

        dsn = (
            db_url
            or os.environ.get(INBOX_DB_ENV, "").strip()
            or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        )
        if dsn:
            require_async_driver(dsn)
            with _SQL_INBOX_LOCK:
                cached = _SQL_INBOX_CACHE.get(dsn)
                if cached is None:
                    cached = SqlActionInboxStore(dsn)
                    _SQL_INBOX_CACHE[dsn] = cached
            return cached

        if inbox_mod._DEFAULT_INBOX_STORE is None:
            inbox_mod._DEFAULT_INBOX_STORE = InMemoryActionInboxStore()
        return inbox_mod._DEFAULT_INBOX_STORE


class LocalAgentGym(AgentGymPort):
    """Local `agent_gym` adapter (Zone 6): in-memory singleton or SQLite-backed Agent Gym engine."""

    def build_agent_gym(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed AgentGymEngine for local execution."""
        import bridge.agent_gym.sql_store as gym_sql
        from bridge.agent_gym.engine import InMemoryAgentGymEngine
        from bridge.agent_gym.models import (
            DATABASE_URL_ENV,
            GYM_DB_ENV,
            OPERATIONAL_DB_ENV,
        )
        from bridge.agent_gym.sql_store import (
            _SQL_GYM_CACHE,
            _SQL_GYM_LOCK,
            SqlAgentGymEngine,
        )

        dsn = (
            db_url
            or os.environ.get(GYM_DB_ENV, "").strip()
            or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
            or os.environ.get(DATABASE_URL_ENV, "").strip()
        )
        if dsn:
            require_async_driver(dsn)
            with _SQL_GYM_LOCK:
                cached = _SQL_GYM_CACHE.get(dsn)
                if cached is None:
                    cached = SqlAgentGymEngine(dsn)
                    _SQL_GYM_CACHE[dsn] = cached
            return cached

        if gym_sql._DEFAULT_GYM_ENGINE is None:
            gym_sql._DEFAULT_GYM_ENGINE = InMemoryAgentGymEngine()
        return gym_sql._DEFAULT_GYM_ENGINE


class LocalKnowledgeCatalog(KnowledgeCatalogPort):
    """Local `knowledge_catalog` adapter (Zone 6): in-memory or SQLite-backed Knowledge Catalog & TBox Ontology Graph."""

    def build_knowledge_catalog(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed KnowledgeCatalog for local execution."""
        import bridge.knowledge_catalog.sql_store as cat_sql
        from bridge.knowledge_catalog.catalog import InMemoryKnowledgeCatalog
        from bridge.knowledge_catalog.sql_store import (
            _SQL_CATALOG_CACHE,
            _SQL_CATALOG_LOCK,
            CATALOG_DB_ENV,
            SqlKnowledgeCatalog,
        )

        dsn = (
            db_url
            or os.environ.get(CATALOG_DB_ENV, "").strip()
            or os.environ.get("BRIDGE_REFINERY_DB_URL", "").strip()
            or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        )
        if dsn:
            require_async_driver(dsn)
            with _SQL_CATALOG_LOCK:
                cached = _SQL_CATALOG_CACHE.get(dsn)
                if cached is None or cached._closed:
                    cached = SqlKnowledgeCatalog(dsn)
                    _SQL_CATALOG_CACHE[dsn] = cached
            return cached

        if cat_sql._DEFAULT_CATALOG is None:
            cat_sql._DEFAULT_CATALOG = InMemoryKnowledgeCatalog()
        return cat_sql._DEFAULT_CATALOG
