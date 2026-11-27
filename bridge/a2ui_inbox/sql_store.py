"""AlloyDB / Cloud SQL Postgres / SQLite durable A2UI v0.9 Action Inbox store."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from bridge.a2ui_inbox.catalogs import (
    ActionStepStatus,
    RiskTier,
    TaskLifecycleState,
)
from bridge.a2ui_inbox.memory_store import (
    TERMINAL_INBOX_STATES,
    ActionInboxStore,
    InboxItemAlreadyResolvedError,
    InMemoryActionInboxStore,
)
from bridge.a2ui_inbox.models import (
    A2uiClientActionRequest,
    ActionInboxItem,
    ActionProgressStep,
    InboxAuditRecord,
)
from bridge.knowledge_catalog import CatalogRole, DeliveryChannel
from bridge.operational_store import FactConflict
from bridge.telemetry.outcome import close_outcome

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncEngine

    from bridge.sql_runtime import DsnSqlRuntime

INBOX_DB_ENV = "BRIDGE_INBOX_DB_URL"
INBOX_ITEMS_TABLE_NAME = "bridge_a2ui_inbox_items"
INBOX_AUDITS_TABLE_NAME = "bridge_a2ui_inbox_audits"
_INBOX_OP_TIMEOUT_S = 30.0


class SqlActionInboxStore(InMemoryActionInboxStore):
    """AlloyDB / Cloud SQL Postgres / SQLite durable A2UI v0.9 Action Inbox store."""

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
        self._resolve_lock = threading.Lock()
        self._tls = threading.local()
        self._runtime: DsnSqlRuntime | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: AsyncEngine | None = None
        self._closed = False
        self._items_table: Table | None = None
        self._audits_table: Table | None = None

    def _record_audit(self, audit: InboxAuditRecord) -> None:
        """Capture audit transiently on thread-local state without retaining RAM history."""
        self._tls.captured_audit = audit

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

            async def _create(engine: AsyncEngine) -> None:
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

    def _ready_handles(self) -> tuple[Table, Table, AsyncEngine]:
        self._ensure_ready()
        if (
            self._items_table is None
            or self._audits_table is None
            or self._engine is None
        ):
            raise RuntimeError("SqlActionInboxStore runtime is not initialized")
        return self._items_table, self._audits_table, self._engine

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

    def _run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        self._ensure_ready()
        if self._runtime is None:
            raise RuntimeError("SqlActionInboxStore runtime is not initialized")
        return self._runtime.run(coro, timeout_s=_INBOX_OP_TIMEOUT_S)

    def _save_item_sql(self, item: ActionInboxItem) -> None:
        table, _, engine = self._ready_handles()

        async def _op() -> None:
            from sqlalchemy import select

            async with engine.begin() as conn:
                existing = await conn.execute(
                    select(table.c.item_id).where(table.c.item_id == item.item_id)
                )
                if existing.first() is not None:
                    await conn.execute(
                        table.update()
                        .where(table.c.item_id == item.item_id)
                        .values(
                            entity_id=item.entity_id,
                            task_state=item.task_state,
                            payload_json=item.model_dump_json(),
                        )
                    )
                else:
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

    def _load_item_sql(self, item_id: str) -> ActionInboxItem | None:
        table, _, engine = self._ready_handles()

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

    def create_action(  # noqa: PLR0913 - public ActionInboxStore contract
        self,
        *,
        entity_id: str,
        context_id: str | None = None,
        session_id: str | None = None,
        invocation_id: str | None = None,
        confirmation_call_id: str | None = None,
        title: str,
        external_title: str | None = None,
        hint: str,
        external_hint: str | None = None,
        assigned_role: CatalogRole = "broker",
        target_view: str = "v_broker_placement",
        risk_level: RiskTier = "MEDIUM",
        confidence_score: float = 0.95,
        steps: list[ActionProgressStep] | None = None,
        data_model: dict[str, Any] | None = None,
        conflicts: list[FactConflict] | None = None,
        pause_for_review: bool = False,
        max_approval_amount: Decimal | str | int | None = None,
        blind_pre_commitment: bool = False,
        outcome_id: str | None = None,
    ) -> ActionInboxItem:
        """Create a new human-in-the-loop action inbox item and persist it in SQL."""
        item = super().create_action(
            entity_id=entity_id,
            context_id=context_id,
            session_id=session_id,
            invocation_id=invocation_id,
            confirmation_call_id=confirmation_call_id,
            title=title,
            external_title=external_title,
            hint=hint,
            external_hint=external_hint,
            assigned_role=assigned_role,
            target_view=target_view,
            risk_level=risk_level,
            confidence_score=confidence_score,
            steps=steps,
            data_model=data_model,
            conflicts=conflicts,
            pause_for_review=pause_for_review,
            max_approval_amount=max_approval_amount,
            blind_pre_commitment=blind_pre_commitment,
            outcome_id=outcome_id,
        )
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
        table, _, engine = self._ready_handles()

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

    def advance_step(  # noqa: PLR0913 - public ActionInboxStore contract
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
        items_table, _, engine = self._ready_handles()
        with self._resolve_lock:
            db_item = self._load_item_sql(item_id)
            if db_item is None:
                raise KeyError(f"Unknown action inbox item '{item_id}'.")
            if db_item.task_state in TERMINAL_INBOX_STATES:
                raise InboxItemAlreadyResolvedError(
                    f"Action inbox item '{item_id}' is already resolved in terminal state '{db_item.task_state}'."
                )
            expected_payload = db_item.model_dump_json()
            snapshot = db_item.model_copy(deep=True)
            self._items[item_id] = db_item.model_copy(deep=True)
            item, delta = super().advance_step(
                item_id,
                step_id=step_id,
                status=status,
                detail=detail,
                external_detail=external_detail,
                hint=hint,
            )

            async def _cas_advance() -> None:
                async with engine.begin() as conn:
                    upd = await conn.execute(
                        items_table.update()
                        .where(
                            (items_table.c.item_id == item_id)
                            & (items_table.c.payload_json == expected_payload)
                            & items_table.c.task_state.notin_(
                                tuple(TERMINAL_INBOX_STATES)
                            )
                        )
                        .values(
                            entity_id=item.entity_id,
                            task_state=item.task_state,
                            payload_json=item.model_dump_json(),
                        )
                    )
                    if upd.rowcount != 1:
                        raise InboxItemAlreadyResolvedError(
                            f"Action inbox item '{item_id}' was concurrently resolved or modified."
                        )

            try:
                self._run(_cas_advance())
            except Exception:
                self._items[item_id] = snapshot
                self._load_item_sql(item_id)
                raise
            return item, delta

    def _persist_resolved_item_and_audit(
        self,
        *,
        item_id: str,
        expected_state: str,
        expected_payload: str,
        updated_item: ActionInboxItem,
        captured_audit: InboxAuditRecord,
    ) -> None:
        items_table, audits_table, engine = self._ready_handles()

        async def _cas_and_audit() -> None:
            async with engine.begin() as conn:
                upd = await conn.execute(
                    items_table.update()
                    .where(
                        (items_table.c.item_id == item_id)
                        & (items_table.c.task_state == expected_state)
                        & (items_table.c.payload_json == expected_payload)
                        & items_table.c.task_state.notin_(tuple(TERMINAL_INBOX_STATES))
                    )
                    .values(
                        entity_id=updated_item.entity_id,
                        task_state=updated_item.task_state,
                        payload_json=updated_item.model_dump_json(),
                    )
                )
                if upd.rowcount != 1:
                    raise InboxItemAlreadyResolvedError(
                        f"Action inbox item '{item_id}' was concurrently resolved or modified."
                    )
                await conn.execute(
                    audits_table.insert().values(
                        audit_id=captured_audit.audit_id,
                        item_id=captured_audit.item_id,
                        payload_json=captured_audit.model_dump_json(),
                    )
                )

        self._run(_cas_and_audit())

    @staticmethod
    def _close_resolved_outcome(updated_item: ActionInboxItem, decision: str) -> None:
        if not updated_item.outcome_id:
            return
        if decision in ("accept", "edit"):
            close_outcome(
                "succeeded",
                subject=updated_item.entity_id,
                outcome_id=updated_item.outcome_id,
            )
        elif decision == "reject":
            close_outcome(
                "failed",
                subject=updated_item.entity_id,
                outcome_id=updated_item.outcome_id,
            )

    def resolve_action(  # noqa: PLR0913 - public ActionInboxStore contract
        self,
        item_id: str,
        *,
        action_request: A2uiClientActionRequest,
        actor_principal: str,
        actor_role: CatalogRole,
        channel: DeliveryChannel,
        agent_spiffe_id: str | None = None,
        principal_max_approval_amount: Decimal | str | int | None = None,
        close_outcome_on_resolve: bool = True,
    ) -> dict[str, Any]:
        """Atomically CAS-transition the inbox item and insert its audit record in one SQL transaction."""
        self._ready_handles()
        with self._resolve_lock:
            db_item = self._load_item_sql(item_id)
            if db_item is None:
                raise KeyError(f"Unknown action inbox item '{item_id}'.")
            if db_item.task_state in TERMINAL_INBOX_STATES:
                raise InboxItemAlreadyResolvedError(
                    f"Action inbox item '{item_id}' is already resolved in terminal state '{db_item.task_state}'."
                )
            expected_state = db_item.task_state
            expected_payload = db_item.model_dump_json()
            snapshot = db_item.model_copy(deep=True)
            self._items[item_id] = db_item.model_copy(deep=True)
            self._tls.captured_audit = None
            try:
                res = super().resolve_action(
                    item_id,
                    action_request=action_request,
                    actor_principal=actor_principal,
                    actor_role=actor_role,
                    channel=channel,
                    agent_spiffe_id=agent_spiffe_id,
                    principal_max_approval_amount=principal_max_approval_amount,
                    close_outcome_on_resolve=False,
                )
                updated_item = self._items[item_id]
                captured_audit: InboxAuditRecord | None = getattr(
                    self._tls, "captured_audit", None
                )
            except Exception:
                self._items[item_id] = snapshot
                self._tls.captured_audit = None
                raise

            if captured_audit is None:
                self._items[item_id] = snapshot
                raise RuntimeError("Inbox resolution produced no audit record")

            try:
                self._persist_resolved_item_and_audit(
                    item_id=item_id,
                    expected_state=expected_state,
                    expected_payload=expected_payload,
                    updated_item=updated_item,
                    captured_audit=captured_audit,
                )
            except Exception:
                self._items[item_id] = snapshot
                self._load_item_sql(item_id)
                raise
            finally:
                self._tls.captured_audit = None

            if close_outcome_on_resolve:
                self._close_resolved_outcome(updated_item, str(res.get("decision", "")))
            return res

    def list_audit_records(self, item_id: str | None = None) -> list[InboxAuditRecord]:
        """Return immutable audit log records from SQL, optionally filtered by action ID."""
        _, table, engine = self._ready_handles()

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
