"""GCP session, memory bank, task store, and exchange store adapters (P7-2)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Protocol, TypeVar, override

from ..protocols import (
    ExchangeStorePort,
    MemoryBankPort,
    SessionsPort,
    TaskStorePort,
)
from .common import (
    _LOG,
    BRIDGE_AGENT_ENGINE_ID_ENV,
    _resolve_dsn,
    parse_agent_engine_resource,
)

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks import PushNotificationConfigStore, TaskStore
    from a2a.types import ListTasksRequest, ListTasksResponse, Task
    from google.adk.memory import BaseMemoryService
    from google.adk.sessions import BaseSessionService

    from bridge.exchange_store import SqlExchangeStore

_TService = TypeVar("_TService", covariant=True)


class _VertexFactory(Protocol[_TService]):
    def __call__(
        self, *, project: str | None, location: str | None, agent_engine_id: str
    ) -> _TService: ...


class GcpSessions(SessionsPort):
    """GCP sessions adapter (P7-2 / Agent Engine Option A).

    Supports two managed backends behind the same `SessionsPort`:
    1. **Gemini platform Agent Engine** (`VertexAiSessionService`) when `agent_engine_id`
       or `BRIDGE_AGENT_ENGINE_ID` env is set (and no explicit `db_url` override
       is passed).
    2. **Cloud SQL Postgres** (`DatabaseSessionService`) over the `DATABASE_URL`
       secret env or `db_url` arg.

    Refuses an in-memory fallback when neither is configured (the session-affinity trap).
    """

    def __init__(
        self,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self._agent_engine_id = agent_engine_id
        self._project = project
        self._location = location

    @override
    def build_session_service(
        self,
        db_url: str | None = None,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
        vertex_factory: _VertexFactory[BaseSessionService] | None = None,
    ) -> BaseSessionService:
        """Construct the ADK session service backed by Agent Engine Sessions or Cloud SQL."""
        raw_engine_id = (
            agent_engine_id
            or self._agent_engine_id
            or os.environ.get(BRIDGE_AGENT_ENGINE_ID_ENV, "")
        ).strip()
        if raw_engine_id and db_url is None:
            proj, loc, eid = parse_agent_engine_resource(
                raw_engine_id,
                project=project or self._project,
                location=location or self._location,
            )
            if vertex_factory is not None:
                return vertex_factory(project=proj, location=loc, agent_engine_id=eid)
            from google.adk.sessions import VertexAiSessionService

            return VertexAiSessionService(
                project=proj, location=loc, agent_engine_id=eid
            )

        from google.adk.sessions import DatabaseSessionService

        from bridge.sql_runtime import enable_iam_auth, is_iam_auth_enabled

        svc = DatabaseSessionService(db_url=_resolve_dsn(db_url))
        if is_iam_auth_enabled() and hasattr(svc, "db_engine"):
            enable_iam_auth(svc.db_engine)
        return svc


class GcpMemoryBank(MemoryBankPort):
    """GCP `memory_bank` adapter — Agent Engine `VertexAiMemoryBankService`.

    Resolves `(project, location, agent_engine_id)` from args or
    `BRIDGE_AGENT_ENGINE_ID` / `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`
    envs. Refuses a silent in-memory fallback when `BRIDGE_AGENT_ENGINE_ID` is unset.
    `vertex_factory` is injectable for credential-free unit tests without
    `google-cloud-aiplatform` installed.
    """

    def __init__(
        self,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self._agent_engine_id = agent_engine_id
        self._project = project
        self._location = location

    @override
    def build_memory_service(
        self,
        db_path: str | None = None,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
        vertex_factory: _VertexFactory[BaseMemoryService] | None = None,
    ) -> BaseMemoryService:
        """Construct the ADK memory service backed by Agent Engine Memory Bank."""
        del (
            db_path
        )  # local-only SQLite path parameter; unused on Agent Engine Memory Bank
        proj, loc, eid = parse_agent_engine_resource(
            agent_engine_id or self._agent_engine_id,
            project=project or self._project,
            location=location or self._location,
        )
        if vertex_factory is not None:
            return vertex_factory(project=proj, location=loc, agent_engine_id=eid)
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=proj, location=loc, agent_engine_id=eid
        )


class GcpTaskStore(TaskStorePort):
    """GCP `task_store` adapter (P7-2) — Cloud SQL `DatabaseTaskStore` + retry.

    Builds the same a2a-sdk `DatabaseTaskStore` the local adapter builds, over an
    `AsyncEngine` on the Cloud SQL DSN, wrapped in `_TerminalRetryTaskStore` so
    terminal-state writes retry with loud logging (`design/ports.md`).
    """

    def build_task_store(self, db_url: str | None = None) -> TaskStore:
        """Construct the durable A2A task store backed by Cloud SQL Postgres with terminal-write retry."""
        from a2a.server.tasks import DatabaseTaskStore

        from bridge.sql_runtime import create_async_engine_with_iam

        inner = DatabaseTaskStore(
            engine=create_async_engine_with_iam(_resolve_dsn(db_url))
        )
        return _TerminalRetryTaskStore(inner)

    def build_push_config_store(
        self, db_url: str | None = None
    ) -> PushNotificationConfigStore:
        """Build the Cloud SQL `DatabasePushNotificationConfigStore`.

        Mirrors the local adapter (rides the same DSN); the terminal-write retry
        concern is the task store, so this is not wrapped.
        """
        from a2a.server.tasks import DatabasePushNotificationConfigStore

        from bridge.sql_runtime import create_async_engine_with_iam

        return DatabasePushNotificationConfigStore(
            engine=create_async_engine_with_iam(_resolve_dsn(db_url)), create_table=True
        )


class GcpExchangeStore(ExchangeStorePort):
    """GCP `exchange_store` adapter (S0-arch-14) — Cloud SQL `SqlExchangeStore`.

    The same `SqlExchangeStore` the local adapter builds, over the Cloud SQL
    Postgres DSN (`DATABASE_URL` — the same database the `sessions` / `task_store`
    ports use, its own `bridge_exchange_records` table). Refuses an in-memory
    fallback (`_resolve_dsn`): per-replica records would split an RFP across
    Cloud Run instances. The compare-and-set write keeps replicas honest. Nothing
    (engine, asyncpg) is opened or imported until the first operation.
    """

    def build_exchange_store(self, db_url: str | None = None) -> SqlExchangeStore:
        """Construct the durable exchange store backed by Cloud SQL Postgres (`SqlExchangeStore`)."""
        from bridge.exchange_store import SqlExchangeStore

        return SqlExchangeStore(_resolve_dsn(db_url))


async def _save_with_terminal_retry(
    inner: TaskStore,
    task: Task,
    context: ServerCallContext,
    *,
    max_attempts: int,
    log: logging.Logger,
) -> None:
    from a2a.server.agent_execution.active_task import TERMINAL_TASK_STATES

    terminal = task.status.state in TERMINAL_TASK_STATES
    attempts = max_attempts if terminal else 1
    last: Exception | None = None
    for i in range(attempts):
        try:
            await inner.save(task, context)
            return
        except Exception as e:  # noqa: BLE001 - intentional retry boundary for terminal task persistence
            last = e
            if terminal:
                log.warning(
                    "terminal task %s save failed (attempt %d/%d): %r",
                    task.id,
                    i + 1,
                    attempts,
                    e,
                )
    if terminal:
        log.error(
            "terminal task %s save exhausted %d retries: %r",
            task.id,
            attempts,
            last,
        )
    if last is None:
        raise RuntimeError("terminal task save failed without exception")
    raise last


def _terminal_retry_task_store() -> type[TaskStore]:
    """Lazily build the `_TerminalRetryTaskStore` class over the a2a-sdk
    `TaskStore` base.

    `TaskStore` is imported at call time (not module import) so importing this
    package stays dep-free (mirrors the local pkg's lazy imports).
    """
    from a2a.server.tasks import TaskStore

    class _TerminalRetryStore(TaskStore):
        """A `TaskStore` wrapper: terminal-state saves retry with loud logging.

        "A lost terminal write hangs the poller to its deadline, so it fails far
        louder than a dropped intermediate `working` write" (`design/ports.md`).
        Only terminal saves (completed/failed/canceled/rejected — the a2a-sdk
        `TERMINAL_TASK_STATES` set, never hardcoded) retry; non-terminal saves
        pass straight through (a single attempt). `get`/`delete`/`list`
        delegate unchanged. Unit-testable without GCP: inject a fake inner store.
        """

        def __init__(
            self,
            inner: TaskStore,
            *,
            attempts: int = 3,
            log: logging.Logger = _LOG,
        ) -> None:
            self._inner = inner
            self._attempts = attempts
            self._log = log

        async def save(self, task: Task, context: ServerCallContext) -> None:
            await _save_with_terminal_retry(
                self._inner,
                task,
                context,
                max_attempts=self._attempts,
                log=self._log,
            )

        async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
            return await self._inner.get(task_id, context)

        async def delete(self, task_id: str, context: ServerCallContext) -> None:
            await self._inner.delete(task_id, context)

        async def list(
            self,
            params: ListTasksRequest,
            context: ServerCallContext,
        ) -> ListTasksResponse:
            return await self._inner.list(params, context)

    return _TerminalRetryStore


def _TerminalRetryTaskStore(
    inner: TaskStore,
    *,
    attempts: int = 3,
    log: logging.Logger = _LOG,
) -> TaskStore:
    """Construct the terminal-retry task-store wrapper around `inner`.

    A thin factory over the lazily-built `_TerminalRetryStore` class so the
    a2a-sdk import stays inside a method (package import pulls no heavy deps).
    """
    cls = _terminal_retry_task_store()
    return cls(inner, attempts=attempts, log=log)  # type: ignore[call-arg]
