"""GCP adapters for Zone 3–6 Enterprise Data Architecture ports."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast, override

from ..protocols import (
    ActionInboxPort,
    AgentGymPort,
    AuditVaultPort,
    KnowledgeCatalogPort,
    OperationalStorePort,
)
from .common import DATABASE_URL_ENV, _require_async_store_driver

if TYPE_CHECKING:
    from bridge.a2ui_inbox import SqlActionInboxStore
    from bridge.agent_gym.sql_store import SqlAgentGymEngine
    from bridge.knowledge_catalog.sql_store import SqlKnowledgeCatalog
    from bridge.operational_store import SqlOperationalStore
    from bridge.vault import GcpAuditVaultStore, _GcsClientLike


class GcpAuditVault(AuditVaultPort):
    """GCP `audit_vault` adapter (Zone 3) — Cloud Storage write-once WORM Audit Vault."""

    def __init__(
        self,
        bucket_name: str | None = None,
        *,
        prefix: str | None = None,
        retention_days: int | None = None,
        retention_mode: str | None = None,
        storage_client: object = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._prefix = prefix
        self._retention_days = retention_days
        self._retention_mode = retention_mode
        self._storage_client = storage_client

    @override
    def build_vault(
        self,
        bucket_name: str | None = None,
        *,
        prefix: str | None = None,
        retention_days: int | None = None,
        retention_mode: str | None = None,
        storage_client: object = None,
    ) -> GcpAuditVaultStore:
        """Construct a Cloud Storage-backed :class:`GcpAuditVaultStore` (refuses in-memory fallback)."""
        from bridge.vault import (
            VAULT_BUCKET_ENV,
            VAULT_PREFIX_ENV,
            VAULT_RETENTION_DAYS_ENV,
            VAULT_RETENTION_MODE_ENV,
            GcpAuditVaultStore,
        )

        bucket = (
            bucket_name or self._bucket_name or os.environ.get(VAULT_BUCKET_ENV, "")
        ).strip()
        if not bucket:
            raise RuntimeError(
                f"GCP port mode requires {VAULT_BUCKET_ENV} for AuditVaultStore; "
                "refusing an in-memory fallback (the session-affinity trap)."
            )
        resolved_prefix = prefix if prefix is not None else self._prefix
        if resolved_prefix is None:
            stripped = bucket.removeprefix("gs://").strip("/")
            if "/" not in stripped:
                env_prefix = os.environ.get(VAULT_PREFIX_ENV, "").strip()
                resolved_prefix = env_prefix or None
        resolved_days = (
            retention_days if retention_days is not None else self._retention_days
        )
        if resolved_days is None:
            raw_days = os.environ.get(VAULT_RETENTION_DAYS_ENV, "").strip()
            if raw_days:
                try:
                    resolved_days = int(raw_days)
                except ValueError as exc:
                    raise ValueError(
                        f"{VAULT_RETENTION_DAYS_ENV} must be a positive integer, got {raw_days!r}"
                    ) from exc
        resolved_mode = (
            retention_mode if retention_mode is not None else self._retention_mode
        )
        if resolved_mode is None:
            resolved_mode = os.environ.get(VAULT_RETENTION_MODE_ENV, "").strip() or None
        return GcpAuditVaultStore(
            bucket,
            prefix=resolved_prefix,
            retention_days=resolved_days,
            retention_mode=resolved_mode,
            client=cast(
                "_GcsClientLike | None", storage_client or self._storage_client
            ),
        )


class GcpOperationalStore(OperationalStorePort):
    """GCP `operational_store` adapter (Zone 3–4) — Cloud SQL / AlloyDB bi-temporal store."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url

    def build_operational_store(self, db_url: str | None = None) -> SqlOperationalStore:
        """Construct a durable :class:`SqlOperationalStore` (refuses in-memory fallback)."""
        from bridge.operational_store import (
            _SQL_CACHE_LOCK,
            _SQL_STORE_CACHE,
            OPERATIONAL_DB_ENV,
            SqlOperationalStore,
        )

        dsn = (
            db_url
            or self._db_url
            or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
            or os.environ.get(DATABASE_URL_ENV, "").strip()
        )
        if not dsn:
            raise RuntimeError(
                "GCP port mode requires DATABASE_URL or BRIDGE_OPERATIONAL_DB_URL for "
                "OperationalStore; refusing an in-memory fallback (the session-affinity trap)."
            )
        _require_async_store_driver(dsn)
        with _SQL_CACHE_LOCK:
            cached = _SQL_STORE_CACHE.get(dsn)
            if cached is None:
                cached = SqlOperationalStore(dsn)
                _SQL_STORE_CACHE[dsn] = cached
        return cached


class GcpActionInbox(ActionInboxPort):
    """GCP `action_inbox` adapter (Zone 5) — Cloud SQL / AlloyDB A2UI Action Inbox store."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url

    def build_action_inbox_store(
        self, db_url: str | None = None
    ) -> SqlActionInboxStore:
        """Construct a durable :class:`SqlActionInboxStore` (refuses in-memory fallback)."""
        from bridge.a2ui_inbox import (
            _SQL_INBOX_CACHE,
            _SQL_INBOX_LOCK,
            INBOX_DB_ENV,
            SqlActionInboxStore,
        )

        dsn = (
            db_url
            or self._db_url
            or os.environ.get(INBOX_DB_ENV, "").strip()
            or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
            or os.environ.get(DATABASE_URL_ENV, "").strip()
        )
        if not dsn:
            raise RuntimeError(
                "GCP port mode requires DATABASE_URL or BRIDGE_INBOX_DB_URL for "
                "ActionInboxStore; refusing an in-memory fallback (the session-affinity trap)."
            )
        _require_async_store_driver(dsn)
        with _SQL_INBOX_LOCK:
            cached = _SQL_INBOX_CACHE.get(dsn)
            if cached is None:
                cached = SqlActionInboxStore(dsn)
                _SQL_INBOX_CACHE[dsn] = cached
        return cached


class GcpAgentGym(AgentGymPort):
    """GCP `agent_gym` adapter (Zone 6) — Cloud SQL / AlloyDB Agent Gym engine."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url

    def build_agent_gym(self, db_url: str | None = None) -> SqlAgentGymEngine:
        """Construct a durable :class:`SqlAgentGymEngine` (refuses in-memory fallback)."""
        from bridge.agent_gym.models import (
            DATABASE_URL_ENV as GYM_DATABASE_URL_ENV,
        )
        from bridge.agent_gym.models import (
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
            or self._db_url
            or os.environ.get(GYM_DB_ENV, "").strip()
            or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
            or os.environ.get(GYM_DATABASE_URL_ENV, "").strip()
        )
        if not dsn:
            raise RuntimeError(
                "AgentGym in GCP / Agent Engine mode requires a durable SQL DSN "
                "(DATABASE_URL, BRIDGE_OPERATIONAL_DB_URL, or BRIDGE_GYM_DB_URL); "
                "refusing an in-memory fallback (the session-affinity trap)."
            )
        _require_async_store_driver(dsn)
        with _SQL_GYM_LOCK:
            cached = _SQL_GYM_CACHE.get(dsn)
            if cached is None:
                cached = SqlAgentGymEngine(dsn)
                _SQL_GYM_CACHE[dsn] = cached
        return cached


class GcpKnowledgeCatalog(KnowledgeCatalogPort):
    """GCP `knowledge_catalog` adapter (Zone 6) — Cloud SQL / AlloyDB Knowledge Catalog & TBox Ontology Graph."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url

    def build_knowledge_catalog(self, db_url: str | None = None) -> SqlKnowledgeCatalog:
        """Construct a durable :class:`SqlKnowledgeCatalog` (refuses in-memory fallback)."""
        from bridge.knowledge_catalog.sql_store import (
            _SQL_CATALOG_CACHE,
            _SQL_CATALOG_LOCK,
            CATALOG_DB_ENV,
            SqlKnowledgeCatalog,
        )

        dsn = (
            db_url
            or self._db_url
            or os.environ.get(CATALOG_DB_ENV, "").strip()
            or os.environ.get("BRIDGE_REFINERY_DB_URL", "").strip()
            or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
            or os.environ.get(DATABASE_URL_ENV, "").strip()
        )
        if not dsn:
            raise RuntimeError(
                "GCP port mode requires DATABASE_URL, BRIDGE_OPERATIONAL_DB_URL, or "
                "BRIDGE_CATALOG_DB_URL for KnowledgeCatalog; refusing an in-memory "
                "fallback (the session-affinity trap)."
            )
        _require_async_store_driver(dsn)
        with _SQL_CATALOG_LOCK:
            cached = _SQL_CATALOG_CACHE.get(dsn)
            if cached is None or cached._closed:
                cached = SqlKnowledgeCatalog(dsn)
                _SQL_CATALOG_CACHE[dsn] = cached
        return cached
