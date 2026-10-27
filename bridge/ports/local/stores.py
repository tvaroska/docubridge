"""Local session, task, exchange, and memory stores (Ring 2 Ports & Adapters)."""

from __future__ import annotations

import os
from typing import Any

from ..protocols import (
    ExchangeStorePort,
    MemoryBankPort,
    SessionsPort,
    TaskStorePort,
)

# The DSN of the local exchange_store adapter (S0-arch-14). Unset -> in-memory records.
EXCHANGE_DB_URL_ENV = "BRIDGE_EXCHANGE_DB_URL"

# The SQLite path of the local memory_bank adapter. Unset -> in-memory service.
MEMORY_DB_PATH_ENV = "BRIDGE_MEMORY_DB_PATH"

# Default sqlite DSN prefix — the async driver is mandatory (see require_async_driver).
# An empty path after the driver is an in-memory async sqlite database.
DEFAULT_SQLITE_DSN = "sqlite+aiosqlite:///"


def require_async_driver(dsn: str) -> str:
    """Reject a sync sqlite DSN early with a clear message (`design/long-running.md`).

    `DatabaseSessionService`/`DatabaseTaskStore` run on SQLAlchemy's asyncio
    extension, which needs an async driver; a bare `sqlite:///` URL fails with a
    cryptic "the asyncio extension requires an async driver". Use
    `sqlite+aiosqlite:///…`.

    NOTE: duplicated identically in `agents/address/app.py` (`agents/` must not
    import `bridge/`, enforced by `tests/test_isolation_guard.py`; parity is by
    DSN + ADK class, not a shared object).
    """
    if dsn.startswith("sqlite:///") or dsn == "sqlite://":
        raise ValueError(
            f"DatabaseSessionService/DatabaseTaskStore need an async driver; "
            f"use 'sqlite+aiosqlite:///…' not {dsn!r}"
        )
    return dsn


class LocalSessions(SessionsPort):
    """Local sessions adapter (DUR-1).

    Builds a durable `DatabaseSessionService` over an async sqlite DSN when one
    is configured (`db_url` arg or `BRIDGE_SESSION_DB_URL` env), else an
    in-memory service (keeps the default test/dev path file-free).
    """

    def build_session_service(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQLite-backed ADK session service for local execution."""
        dsn = db_url or os.environ.get("BRIDGE_SESSION_DB_URL", "").strip()
        if not dsn:
            from google.adk.sessions import InMemorySessionService

            return InMemorySessionService()
        require_async_driver(dsn)
        from google.adk.sessions import DatabaseSessionService
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
        return DatabaseSessionService(db_url=dsn, **kwargs)


class LocalTaskStore(TaskStorePort):
    """Local task-store adapter (DUR-2).

    Builds a durable `DatabaseTaskStore` over an `AsyncEngine` when a DSN is
    configured (`db_url` arg or `BRIDGE_TASK_DB_URL` env), else an in-memory
    store. Note `DatabaseTaskStore` takes an engine, not a URL.
    """

    def build_task_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQLite-backed A2A task store for local execution."""
        dsn = db_url or os.environ.get("BRIDGE_TASK_DB_URL", "").strip()
        if not dsn:
            from a2a.server.tasks import InMemoryTaskStore

            return InMemoryTaskStore()
        require_async_driver(dsn)
        from a2a.server.tasks import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
        return DatabaseTaskStore(engine=create_async_engine(dsn, **kwargs))

    def build_push_config_store(self, db_url: str | None = None) -> Any:
        """Build a push notification config store (in-memory or durable).

        Mirrors `build_task_store` exactly (Decision 1): both are a2a-sdk
        `Database*` classes over the same DSN (`BRIDGE_TASK_DB_URL`).
        """
        dsn = db_url or os.environ.get("BRIDGE_TASK_DB_URL", "").strip()
        if not dsn:
            from a2a.server.tasks import InMemoryPushNotificationConfigStore

            return InMemoryPushNotificationConfigStore()
        require_async_driver(dsn)
        from a2a.server.tasks import DatabasePushNotificationConfigStore
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
        return DatabasePushNotificationConfigStore(
            engine=create_async_engine(dsn, **kwargs), create_table=True
        )


class LocalExchangeStore(ExchangeStorePort):
    """Local `exchange_store` adapter (S0-arch-14).

    Builds a durable `SqlExchangeStore` over an async sqlite DSN when one is
    configured (`db_url` arg or `BRIDGE_EXCHANGE_DB_URL` env), else an
    `InMemoryExchangeStore` (keeps the default test/dev path file-free — mirrors
    `LocalSessions`). The SQL store opens nothing until its first operation.
    """

    def build_exchange_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQLite-backed exchange store for local execution."""
        from bridge.exchange_store import InMemoryExchangeStore, SqlExchangeStore

        dsn = db_url or os.environ.get(EXCHANGE_DB_URL_ENV, "").strip()
        if not dsn:
            return InMemoryExchangeStore()
        return SqlExchangeStore(require_async_driver(dsn))


class LocalMemoryBank(MemoryBankPort):
    """Local `memory_bank` adapter — ADK `SqliteMemoryService` or `InMemoryMemoryService`.

    Builds a durable `SqliteMemoryService` when a path is configured (`db_path` arg
    or `BRIDGE_MEMORY_DB_PATH` env), else `InMemoryMemoryService` (mirrors
    `LocalSessions`).
    """

    def build_memory_service(self, db_path: str | None = None) -> Any:
        """Construct an in-memory or SQLite-backed ADK memory service for local execution."""
        path = (db_path or os.environ.get(MEMORY_DB_PATH_ENV, "")).strip()
        if not path:
            from google.adk.memory import InMemoryMemoryService

            return InMemoryMemoryService()
        from google.adk.memory import SqliteMemoryService

        return SqliteMemoryService(db_path=path)
