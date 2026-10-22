"""AlloyDB / Cloud SQL Postgres / SQLite bi-temporal operational store."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from bridge.operational_store.memory_store import OperationalStore
from bridge.operational_store.models import (
    _OP_TIMEOUT_S,
    FACTS_TABLE_NAME,
    ConsolidatedFact,
    _utc_iso,
)
from contract.extraction import SourceAnchor


class SqlOperationalStore(OperationalStore):
    """AlloyDB / Cloud SQL Postgres / SQLite bi-temporal operational store.

    Uses the same lazy daemon-thread + async SQLAlchemy engine pattern as
    ``bridge.exchange_store.SqlExchangeStore`` so callers stay synchronous while
    riding ``sqlite+aiosqlite`` locally and ``postgresql+asyncpg`` on GCP.
    """

    def __init__(self, dsn: str) -> None:
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("SqlOperationalStore requires a non-empty database DSN")
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlOperationalStore refuses an in-memory SQLite DSN; use "
                "InMemoryOperationalStore or a file/Postgres DSN"
            )
        self.dsn = cleaned
        self._init_lock = threading.Lock()
        self._runtime: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._table: Any = None
        self._closed = False

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._closed:
                raise RuntimeError("the operational store is closed")
            if self._engine is not None:
                return
            from sqlalchemy import (
                Boolean,
                Column,
                Index,
                Integer,
                MetaData,
                String,
                Table,
                Text,
            )

            from bridge.sql_runtime import acquire_dsn_runtime

            meta = MetaData()
            self._table = Table(
                FACTS_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("fact_id", String(64), unique=True, nullable=False, index=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("context_id", String(128), nullable=False, index=True),
                Column("doc_id", String(128), nullable=False),
                Column("doctype", String(128), nullable=False),
                Column("issuer", String(128), nullable=True),
                Column("field_path", String(256), nullable=False, index=True),
                Column("field_value", Text, nullable=False),
                Column("numeric_value", String(64), nullable=True),
                Column("currency", String(16), nullable=False),
                Column("source_priority", Integer, nullable=False),
                Column("recorded_at", String(64), nullable=False, index=True),
                Column("effective_from", String(64), nullable=False, index=True),
                Column("version", Integer, nullable=False),
                Column("source_anchor_json", Text, nullable=True),
                Column("vault_uri", Text, nullable=True),
                Column("flagged_for_review", Boolean, nullable=False, default=False),
                Column("skill_version", String(64), nullable=False, default="1.0.0"),
                Column(
                    "skill_revision_id",
                    String(128),
                    nullable=False,
                    default="rev-v1-0-baseline",
                ),
                Column("skill_urn", String(256), nullable=True),
                Column(
                    "process_skill_revision_id",
                    String(128),
                    nullable=False,
                    default="rev-v1-0-baseline",
                ),
                Column("compiled_rule_ids_json", Text, nullable=True),
                Index(
                    "ix_operational_facts_bitemporal",
                    "entity_id",
                    "field_path",
                    "effective_from",
                    "recorded_at",
                ),
            )

            async def _create(engine: Any) -> None:
                from sqlalchemy import inspect, text

                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)
                    existing_cols = await conn.run_sync(
                        lambda sync_conn: {
                            c["name"]
                            for c in inspect(sync_conn).get_columns(FACTS_TABLE_NAME)
                        }
                    )
                    migrations = (
                        (
                            "skill_version",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN skill_version VARCHAR(64) NOT NULL DEFAULT '1.0.0'",
                        ),
                        (
                            "skill_revision_id",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN skill_revision_id VARCHAR(128) NOT NULL DEFAULT 'rev-v1-0-baseline'",
                        ),
                        (
                            "skill_urn",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN skill_urn VARCHAR(256)",
                        ),
                        (
                            "process_skill_revision_id",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN process_skill_revision_id VARCHAR(128) NOT NULL DEFAULT 'rev-v1-0-baseline'",
                        ),
                        (
                            "compiled_rule_ids_json",
                            f"ALTER TABLE {FACTS_TABLE_NAME} ADD COLUMN compiled_rule_ids_json TEXT",
                        ),
                    )
                    for col_name, ddl in migrations:
                        if col_name not in existing_cols:
                            await conn.execute(text(ddl))

            runtime = acquire_dsn_runtime(
                self.dsn,
                init_coro_factory=_create,
                thread_name="bridge-operational-store",
                timeout_s=_OP_TIMEOUT_S,
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

        release_dsn_runtime(self.dsn, runtime, timeout_s=_OP_TIMEOUT_S)
        _SQL_STORE_CACHE.pop(self.dsn, None)

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._runtime is not None
        return self._runtime.run(coro, timeout_s=_OP_TIMEOUT_S)

    def append_facts(self, facts: list[ConsolidatedFact]) -> int:
        """Persist extracted canonical fact rows into the SQL operational store."""
        if not facts:
            return 0
        self._ensure_ready()
        table = self._table
        engine = self._engine

        rows = [
            {
                "fact_id": f.fact_id,
                "entity_id": f.entity_id,
                "context_id": f.context_id,
                "doc_id": f.doc_id,
                "doctype": f.doctype,
                "issuer": f.issuer,
                "field_path": f.field_path,
                "field_value": f.field_value,
                "numeric_value": f.numeric_value,
                "currency": f.currency,
                "source_priority": f.source_priority,
                "recorded_at": _utc_iso(f.recorded_at),
                "effective_from": _utc_iso(f.effective_from),
                "version": f.version,
                "source_anchor_json": (
                    json.dumps(f.source_anchor.model_dump(mode="json"))
                    if f.source_anchor is not None
                    else None
                ),
                "vault_uri": f.vault_uri,
                "flagged_for_review": f.flagged_for_review,
                "skill_version": f.skill_version,
                "skill_revision_id": f.skill_revision_id,
                "skill_urn": f.skill_urn,
                "process_skill_revision_id": f.process_skill_revision_id,
                "compiled_rule_ids_json": (
                    json.dumps(list(f.compiled_rule_ids))
                    if f.compiled_rule_ids
                    else None
                ),
            }
            for f in facts
        ]

        async def _insert() -> int:
            async with engine.begin() as conn:
                await conn.execute(table.insert(), rows)
            return len(rows)

        return self._run(_insert())

    def list_facts(
        self,
        entity_id: str | None = None,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
        since_recorded_at: datetime | None = None,
    ) -> list[ConsolidatedFact]:
        """Query canonical fact rows from SQL filtered by entity, domain, document, or key."""
        self._ensure_ready()
        from sqlalchemy import select

        table = self._table
        engine = self._engine

        async def _query() -> list[ConsolidatedFact]:
            stmt = select(table).order_by(table.c.seq.asc())
            if entity_id is not None:
                stmt = stmt.where(table.c.entity_id == entity_id)
            if learned_as_of is not None:
                stmt = stmt.where(table.c.recorded_at <= _utc_iso(learned_as_of))
            if effective_on is not None:
                stmt = stmt.where(table.c.effective_from <= _utc_iso(effective_on))
            if since_recorded_at is not None:
                stmt = stmt.where(table.c.recorded_at > _utc_iso(since_recorded_at))
            async with engine.connect() as conn:
                res = await conn.execute(stmt)
                out: list[ConsolidatedFact] = []
                for row in res.mappings():
                    anchor_raw = row["source_anchor_json"]
                    anchor = (
                        SourceAnchor.model_validate(json.loads(anchor_raw))
                        if anchor_raw
                        else None
                    )
                    out.append(
                        ConsolidatedFact(
                            fact_id=row["fact_id"],
                            entity_id=row["entity_id"],
                            context_id=row["context_id"],
                            doc_id=row["doc_id"],
                            doctype=row["doctype"],
                            issuer=row["issuer"],
                            field_path=row["field_path"],
                            field_value=row["field_value"],
                            numeric_value=row["numeric_value"],
                            currency=row["currency"],
                            source_priority=row["source_priority"],
                            recorded_at=datetime.fromisoformat(row["recorded_at"]),
                            effective_from=datetime.fromisoformat(
                                row["effective_from"]
                            ),
                            version=row["version"],
                            source_anchor=anchor,
                            vault_uri=row["vault_uri"],
                            flagged_for_review=bool(row["flagged_for_review"]),
                            skill_version=row.get("skill_version") or "1.0.0",
                            skill_revision_id=(
                                row.get("skill_revision_id") or "rev-v1-0-baseline"
                            ),
                            skill_urn=row.get("skill_urn"),
                            process_skill_revision_id=(
                                row.get("process_skill_revision_id")
                                or "rev-v1-0-baseline"
                            ),
                            compiled_rule_ids=(
                                tuple(json.loads(row["compiled_rule_ids_json"]))
                                if row.get("compiled_rule_ids_json")
                                else ()
                            ),
                        )
                    )
                return out

        return self._run(_query())


_SQL_STORE_CACHE: dict[str, SqlOperationalStore] = {}
_SQL_CACHE_LOCK = threading.Lock()


def operational_store_from_env() -> OperationalStore:
    """Resolve the OperationalStore via the ``Port.operational_store`` port adapter."""
    from bridge.ports import Port, get_adapter, resolve_mode

    mode = resolve_mode(Port.operational_store)
    adapter = get_adapter(Port.operational_store, mode)
    return adapter.build_operational_store()
