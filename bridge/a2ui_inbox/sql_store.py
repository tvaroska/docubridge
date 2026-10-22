"""AlloyDB / Cloud SQL Postgres / SQLite durable A2UI v0.9 Action Inbox store."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

from bridge.a2ui_inbox.catalogs import (
    ActionStepStatus,
    TaskLifecycleState,
)
from bridge.a2ui_inbox.memory_store import (
    ActionInboxStore,
    InMemoryActionInboxStore,
)
from bridge.a2ui_inbox.models import (
    A2uiClientActionRequest,
    ActionInboxItem,
    InboxAuditRecord,
)
from bridge.knowledge_catalog import CatalogRole, DeliveryChannel

INBOX_DB_ENV = "BRIDGE_INBOX_DB_URL"
INBOX_ITEMS_TABLE_NAME = "bridge_a2ui_inbox_items"
INBOX_AUDITS_TABLE_NAME = "bridge_a2ui_inbox_audits"
_INBOX_OP_TIMEOUT_S = 30.0


class SqlActionInboxStore(InMemoryActionInboxStore):
    """AlloyDB / Cloud SQL Postgres / SQLite durable A2UI v0.9 Action Inbox store.

    Synchronizes ``ActionInboxItem`` state and immutable ``InboxAuditRecord`` entries
    with shared SQL tables so multi-replica Cloud Run services maintain a single
    source of truth across instances and restarts.
    """

    def __init__(self, dsn: str) -> None:
        super().__init__()
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("SqlActionInboxStore requires a non-empty database DSN")
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlActionInboxStore refuses an in-memory SQLite DSN; use "
                "InMemoryActionInboxStore or a file/Postgres DSN"
            )
        self.dsn = cleaned
        self._init_lock = threading.Lock()
        self._runtime: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._closed = False
        self._items_table: Any = None
        self._audits_table: Any = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._closed:
                raise RuntimeError("the action inbox store is closed")
            if self._engine is not None:
                return
            from sqlalchemy import (
                Column,
                Integer,
                MetaData,
                String,
                Table,
                Text,
            )

            from bridge.sql_runtime import acquire_dsn_runtime

            meta = MetaData()
            self._items_table = Table(
                INBOX_ITEMS_TABLE_NAME,
                meta,
                Column("item_id", String(64), primary_key=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("task_state", String(64), nullable=False, index=True),
                Column("created_at", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._audits_table = Table(
                INBOX_AUDITS_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("audit_id", String(64), unique=True, nullable=False, index=True),
                Column("item_id", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )

            async def _create(engine: Any) -> None:
                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)

            runtime = acquire_dsn_runtime(
                self.dsn,
                init_coro_factory=_create,
                thread_name="bridge-a2ui-inbox-store",
                timeout_s=_INBOX_OP_TIMEOUT_S,
            )
            self._runtime = runtime
            self._loop = runtime.loop
            self._thread = runtime.thread
            self._engine = runtime.engine

    def close(self) -> None:
        """Release the underlying per-DSN SQLAlchemy runtime lease."""
        with self._init_lock:
            if self._closed:
                return
            self._closed = True
            runtime = self._runtime
            self._runtime = self._loop = self._thread = self._engine = None
        if runtime is None:
            return
        from bridge.sql_runtime import release_dsn_runtime

        release_dsn_runtime(self.dsn, runtime, timeout_s=_INBOX_OP_TIMEOUT_S)
        _SQL_INBOX_CACHE.pop(self.dsn, None)

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._runtime is not None
        return self._runtime.run(coro, timeout_s=_INBOX_OP_TIMEOUT_S)

    def _save_item_sql(self, item: ActionInboxItem) -> None:
        self._ensure_ready()
        table = self._items_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(table).where(table.c.item_id == item.item_id))
                await conn.execute(
                    table.insert().values(
                        item_id=item.item_id,
                        entity_id=item.entity_id,
                        task_state=item.task_state,
                        created_at=item.created_at.isoformat(),
                        payload_json=item.model_dump_json(),
                    )
                )

        self._run(_op())

    def _save_audit_sql(self, audit: InboxAuditRecord) -> None:
        self._ensure_ready()
        table = self._audits_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.audit_id == audit.audit_id)
                )
                await conn.execute(
                    table.insert().values(
                        audit_id=audit.audit_id,
                        item_id=audit.item_id,
                        payload_json=audit.model_dump_json(),
                    )
                )

        self._run(_op())

    def _load_item_sql(self, item_id: str) -> ActionInboxItem | None:
        self._ensure_ready()
        table = self._items_table
        engine = self._engine

        async def _op() -> ActionInboxItem | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.item_id == item_id)
                )
                row = res.first()
                if row is None:
                    return None
                return ActionInboxItem.model_validate_json(row[0])

        item = self._run(_op())
        if item is not None:
            self._items[item.item_id] = item
        return item

    def create_action(self, **kwargs: Any) -> ActionInboxItem:
        """Create a new human-in-the-loop action inbox item and persist it in SQL."""
        item = super().create_action(**kwargs)
        self._save_item_sql(item)
        return item

    def get_item(self, item_id: str) -> ActionInboxItem | None:
        """Retrieve an action inbox item from SQL by its unique action ID."""
        return self._load_item_sql(item_id)

    def list_items(
        self,
        *,
        entity_id: str | None = None,
        task_state: TaskLifecycleState | None = None,
    ) -> list[ActionInboxItem]:
        """List SQL-backed action inbox items filtered by entity ID or task state."""
        self._ensure_ready()
        table = self._items_table
        engine = self._engine

        async def _op() -> list[ActionInboxItem]:
            from sqlalchemy import select

            stmt = select(table.c.payload_json).order_by(table.c.created_at.desc())
            if entity_id is not None:
                stmt = stmt.where(table.c.entity_id == entity_id)
            if task_state is not None:
                stmt = stmt.where(table.c.task_state == task_state)
            async with engine.connect() as conn:
                res = await conn.execute(stmt)
                items = [ActionInboxItem.model_validate_json(r[0]) for r in res]
                for it in items:
                    self._items[it.item_id] = it
                return items

        return self._run(_op())

    def advance_step(
        self,
        item_id: str,
        *,
        step_id: str,
        status: ActionStepStatus,
        detail: str | None = None,
        external_detail: str | None = None,
        hint: str | None = None,
    ) -> tuple[ActionInboxItem, dict[str, Any]]:
        """Advance the workflow step of a SQL-backed action inbox item."""
        self._load_item_sql(item_id)
        item, delta = super().advance_step(
            item_id,
            step_id=step_id,
            status=status,
            detail=detail,
            external_detail=external_detail,
            hint=hint,
        )
        self._save_item_sql(item)
        return item, delta

    def resolve_action(
        self,
        item_id: str,
        *,
        action_request: A2uiClientActionRequest,
        actor_principal: str,
        actor_role: CatalogRole,
        channel: DeliveryChannel,
        agent_spiffe_id: str | None = None,
        principal_max_approval_amount: float | None = None,
    ) -> dict[str, Any]:
        """Resolve a SQL-backed action inbox item and persist both the item and audit record."""
        self._load_item_sql(item_id)
        prev_audit_len = len(self._audits)
        res = super().resolve_action(
            item_id,
            action_request=action_request,
            actor_principal=actor_principal,
            actor_role=actor_role,
            channel=channel,
            agent_spiffe_id=agent_spiffe_id,
            principal_max_approval_amount=principal_max_approval_amount,
        )
        updated_item = self._items.get(item_id)
        if updated_item is not None:
            self._save_item_sql(updated_item)
        for audit in self._audits[prev_audit_len:]:
            self._save_audit_sql(audit)
        return res

    def list_audit_records(self, item_id: str | None = None) -> list[InboxAuditRecord]:
        """Return immutable audit log records from SQL, optionally filtered by action ID."""
        self._ensure_ready()
        table = self._audits_table
        engine = self._engine

        async def _op() -> list[InboxAuditRecord]:
            from sqlalchemy import select

            stmt = select(table.c.payload_json).order_by(table.c.seq.asc())
            if item_id is not None:
                stmt = stmt.where(table.c.item_id == item_id)
            async with engine.connect() as conn:
                res = await conn.execute(stmt)
                return [InboxAuditRecord.model_validate_json(r[0]) for r in res]

        return self._run(_op())


_DEFAULT_INBOX_STORE: InMemoryActionInboxStore | None = None
_SQL_INBOX_CACHE: dict[str, SqlActionInboxStore] = {}
_SQL_INBOX_LOCK = threading.Lock()


def action_inbox_store_from_env(
    inbox_store: ActionInboxStore | None = None,
) -> ActionInboxStore:
    """Resolve `ActionInboxStore` via the ``Port.action_inbox`` port adapter."""
    if inbox_store is not None:
        return inbox_store
    from bridge.ports import Port, get_adapter, resolve_mode

    mode = resolve_mode(Port.action_inbox)
    adapter = get_adapter(Port.action_inbox, mode)
    return adapter.build_action_inbox_store()
