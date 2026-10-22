"""Exchange store (S0-arch-14) — materialized Exchange records behind the ``exchange_store`` seam.

By default an Exchange is a derived view over its tasks (``design/aggregate-model.md``).
It **materializes** into a stored record only when exchange-only state appears — an
agent-owned Requirements artifact (emergent Collect: the RFP aggregate), a skill binding,
program membership, reopen state. This module is the store for those records.

**Record shape.** An :class:`ExchangeRecord` is a JSON document plus an optimistic-
concurrency ``version``, keyed by ``(kind, ref)`` (e.g. ``("rfp", rfp_id)``). It carries
the exchange's A2A ``context_id`` as an indexed, **non-unique** column — identity stays
the A2A context token (a record never mints its own id), but the context is not the key:
one broker context may own several records. The store is aggregate-agnostic; the caller
owns the document schema (``bridge.dashboard.board.RfpBoard`` stores
``RfpCollection.model_dump(mode="json")``).

**The CAS contract.** ``put(..., expected_version=N)``: ``N == 0`` is **create-only**
(the record must not exist); ``N > 0`` updates only if the stored version is still ``N``.
Either mismatch raises :class:`StaleExchangeWrite` and leaves the stored record unchanged;
success returns the new version (``1`` on create, ``N + 1`` on update). There is no
unconditional upsert — a write derived from a superseded read (another replica, board or
thread wrote since) is refused instead of silently lost.

**JSON parity.** Both implementations JSON round-trip the document on write and on read,
so a non-JSON-serializable document fails the same way everywhere (``TypeError``; a
non-finite float is a ``ValueError``, as Postgres ``json`` would refuse it), and a
caller can never mutate stored state through a returned dict.

**Sync facade, private loop thread.** The API is synchronous: every caller is sync and
the write sites rely on "fresh read → apply → put with no ``await`` in between". The
only installed database drivers are async (aiosqlite; asyncpg on GCP), so
:class:`SqlExchangeStore` runs a SQLAlchemy ``AsyncEngine`` on its own event loop in a
daemon thread and blocks the caller on each operation. The thread, loop, engine and
table are created lazily on the FIRST operation — never in ``__init__`` — so building an
adapter needs no env, and building a GCP store does not import asyncpg. Trade-off: a
read blocks the calling event loop for one database round-trip (fine at RFP scale).

Implementations: :class:`InMemoryExchangeStore` (the local default with no DSN) and
:class:`SqlExchangeStore` (sqlite+aiosqlite locally, Cloud SQL Postgres+asyncpg on GCP —
one implementation, one table ``bridge_exchange_records``). Hosts resolve one through
the seam with :func:`exchange_store_from_env`.

Isolation: stdlib-only top-level imports (SQLAlchemy and ``bridge.seams`` are imported
lazily); NEVER ``agents/``.
"""

from __future__ import annotations

import abc
import asyncio
import functools
import json
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

TABLE_NAME = "bridge_exchange_records"

# Upper bound on one blocking store operation (a hung database must not hang a caller
# forever).
_OP_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ExchangeRecord:
    """One materialized Exchange record: ``(kind, ref)`` key, A2A context, version, doc."""

    kind: str
    ref: str
    context_id: str
    version: int
    doc: dict[str, Any]


class StaleExchangeWrite(Exception):
    """A ``put`` whose ``expected_version`` no longer matches the stored record."""

    def __init__(self, kind: str, ref: str, expected_version: int) -> None:
        self.kind = kind
        self.ref = ref
        self.expected_version = expected_version
        what = (
            "already exists (create-only put at version 0)"
            if expected_version == 0
            else f"is no longer at version {expected_version}"
        )
        super().__init__(f"exchange record {kind}/{ref} {what}")


def _json_roundtrip(doc: Any) -> dict[str, Any]:
    """The doc as the database would hand it back (``TypeError`` if not JSON-serializable)."""
    if not isinstance(doc, dict):
        raise TypeError(
            f"an exchange record doc must be a dict, got {type(doc).__name__}"
        )
    return json.loads(json.dumps(doc, allow_nan=False))


def _check_key(kind: str, ref: str) -> None:
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"exchange record kind must be a non-empty str, got {kind!r}")
    if not isinstance(ref, str) or not ref:
        raise ValueError(f"exchange record ref must be a non-empty str, got {ref!r}")


def _check_put(kind: str, ref: str, expected_version: int) -> None:
    _check_key(kind, ref)
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 0
    ):
        raise ValueError(
            f"expected_version must be an int >= 0, got {expected_version!r}"
        )


class ExchangeStore(abc.ABC):
    """The materialized-Exchange record store (sync API; see the module docstring)."""

    @abc.abstractmethod
    def get(self, kind: str, ref: str) -> ExchangeRecord | None:
        """The record at ``(kind, ref)``, or ``None`` when absent."""
        raise NotImplementedError

    @abc.abstractmethod
    def list(self, kind: str) -> list[ExchangeRecord]:
        """Every record of ``kind`` in CREATION order (an update never reorders)."""
        raise NotImplementedError

    @abc.abstractmethod
    def put(
        self,
        kind: str,
        ref: str,
        *,
        context_id: str,
        doc: dict[str, Any],
        expected_version: int,
    ) -> int:
        """Compare-and-set write; return the new version.

        ``expected_version == 0`` creates (the record must not exist); ``> 0`` updates
        only if the stored version still equals it. A mismatch raises
        :class:`StaleExchangeWrite` and leaves the store unchanged. ``ValueError`` on a
        negative version or an empty ``kind`` / ``ref``; ``TypeError`` on a doc that is
        not JSON-serializable.
        """
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 — a no-op default is the point
        """Release resources (idempotent). The in-memory store holds none."""


class InMemoryExchangeStore(ExchangeStore):
    """Process-local records (the local default with no DSN) — nothing survives a restart.

    Insertion-ordered dict + a lock; JSON round-trip on put and get for parity with the
    SQL store.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ExchangeRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _copy(rec: ExchangeRecord) -> ExchangeRecord:
        return ExchangeRecord(
            kind=rec.kind,
            ref=rec.ref,
            context_id=rec.context_id,
            version=rec.version,
            doc=_json_roundtrip(rec.doc),
        )

    def get(self, kind: str, ref: str) -> ExchangeRecord | None:
        """Retrieve an exchange state snapshot by exchange ID from memory."""
        _check_key(kind, ref)
        with self._lock:
            rec = self._records.get((kind, ref))
        return None if rec is None else self._copy(rec)

    def list(self, kind: str) -> list[ExchangeRecord]:
        """List all exchange state snapshots stored in memory."""
        with self._lock:
            recs = [r for r in self._records.values() if r.kind == kind]
        return [self._copy(r) for r in recs]

    def put(self, kind, ref, *, context_id, doc, expected_version) -> int:
        """Save or update an exchange state snapshot in memory."""
        _check_put(kind, ref, expected_version)
        stored = _json_roundtrip(doc)
        with self._lock:
            current = self._records.get((kind, ref))
            found = 0 if current is None else current.version
            if found != expected_version:
                raise StaleExchangeWrite(kind, ref, expected_version)
            version = expected_version + 1
            # Re-assigning an existing key keeps its insertion (= creation) position.
            self._records[(kind, ref)] = ExchangeRecord(
                kind=kind,
                ref=ref,
                context_id=context_id,
                version=version,
                doc=stored,
            )
        return version


@functools.cache
def _table():
    """The ``bridge_exchange_records`` table (built lazily: SQLAlchemy is not a top import)."""
    import sqlalchemy as sa

    metadata = sa.MetaData()
    return sa.Table(
        TABLE_NAME,
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("ref", sa.String(256), nullable=False),
        sa.Column("context_id", sa.String(256), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("doc", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "ref", name="uq_bridge_exchange_records_kind_ref"),
    )


def _record(row: Any) -> ExchangeRecord:
    return ExchangeRecord(
        kind=row.kind,
        ref=row.ref,
        context_id=row.context_id,
        version=row.version,
        doc=_json_roundtrip(row.doc),
    )


class SqlExchangeStore(ExchangeStore):
    """Records in one SQL table over a shared per-DSN async SQLAlchemy runtime.

    ``__init__`` stores the DSN only. The first operation leases a shared
    ``DsnSqlRuntime`` for ``self.dsn``, builds the ``AsyncEngine`` (if not already
    running for that DSN), and creates the table if missing; every operation then
    runs on that loop in its own transaction, and the caller (any thread, including
    one running its own loop) blocks for the result.
    ``close()`` releases the store's lease (disposing the engine and stopping the
    thread once the last store on that DSN closes); a closed store refuses further
    operations.
    """

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("SqlExchangeStore needs a non-empty DSN")
        self.dsn = dsn.strip()
        self._lock = threading.Lock()
        self._runtime: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------------

    def _start(self) -> None:
        """Acquire the per-DSN runtime + create table on first use."""
        with self._lock:
            if self._closed:
                raise RuntimeError("the exchange store is closed")
            if self._loop is not None:
                return
            from bridge.sql_runtime import acquire_dsn_runtime

            runtime = acquire_dsn_runtime(
                self.dsn,
                init_coro_factory=self._open,
                thread_name="exchange-store",
                timeout_s=_OP_TIMEOUT_S,
            )
            self._runtime = runtime
            self._loop = runtime.loop
            self._thread = runtime.thread
            self._engine = runtime.engine

    async def _open(self, engine: Any) -> None:
        table = _table()
        try:
            async with engine.begin() as conn:
                await conn.run_sync(table.metadata.create_all)
        except Exception:
            # Another handle/replica may have won the CREATE race; checkfirst
            # makes a second pass a no-op if the table now exists.
            async with engine.begin() as conn:
                await conn.run_sync(table.metadata.create_all)

    def close(self) -> None:
        """Release the underlying per-DSN SQLAlchemy runtime lease."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            runtime = self._runtime
            self._runtime = self._loop = self._thread = self._engine = None
        if runtime is None:
            return
        from bridge.sql_runtime import release_dsn_runtime

        release_dsn_runtime(self.dsn, runtime, timeout_s=_OP_TIMEOUT_S)

    def _call[T](self, make: Callable[[Any], Coroutine[Any, Any, T]]) -> T:
        """Run ``make(engine)`` (a coroutine factory) on the store loop; block for it."""
        if threading.current_thread() is self._thread:
            raise RuntimeError(
                "re-entrant exchange-store call from its own loop thread would deadlock"
            )
        self._start()
        runtime, engine = self._runtime, self._engine
        if runtime is None:
            raise RuntimeError("the exchange store is closed")
        return runtime.run(make(engine), timeout_s=_OP_TIMEOUT_S)

    # -- operations --------------------------------------------------------------

    def get(self, kind: str, ref: str) -> ExchangeRecord | None:
        """Retrieve an exchange state snapshot by exchange ID from SQL."""
        _check_key(kind, ref)
        t = _table()

        async def op(engine):
            import sqlalchemy as sa

            async with engine.begin() as conn:
                row = (
                    await conn.execute(
                        sa.select(t).where(t.c.kind == kind, t.c.ref == ref)
                    )
                ).first()
            return None if row is None else _record(row)

        return self._call(op)

    def list(self, kind: str) -> list[ExchangeRecord]:
        """List all exchange state snapshots stored in the SQL database."""
        t = _table()

        async def op(engine):
            import sqlalchemy as sa

            async with engine.begin() as conn:
                rows = (
                    await conn.execute(
                        sa.select(t).where(t.c.kind == kind).order_by(t.c.id)
                    )
                ).all()
            return [_record(r) for r in rows]

        return self._call(op)

    def put(self, kind, ref, *, context_id, doc, expected_version) -> int:
        """Persist or update an exchange state snapshot in the SQL database."""
        _check_put(kind, ref, expected_version)
        stored = _json_roundtrip(doc)
        t = _table()

        async def op(engine):
            import sqlalchemy as sa
            from sqlalchemy.exc import IntegrityError

            now = datetime.now(UTC)
            if expected_version == 0:
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            sa.insert(t).values(
                                kind=kind,
                                ref=ref,
                                context_id=context_id,
                                version=1,
                                doc=stored,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                except IntegrityError as exc:
                    raise StaleExchangeWrite(kind, ref, expected_version) from exc
                return 1
            async with engine.begin() as conn:
                result = await conn.execute(
                    sa.update(t)
                    .where(
                        t.c.kind == kind,
                        t.c.ref == ref,
                        t.c.version == expected_version,
                    )
                    .values(
                        version=t.c.version + 1,
                        context_id=context_id,
                        doc=stored,
                        updated_at=now,
                    )
                )
            if result.rowcount != 1:
                raise StaleExchangeWrite(kind, ref, expected_version)
            return expected_version + 1

        return self._call(op)


def exchange_store_from_env() -> ExchangeStore:
    """The exchange store the ``exchange_store`` seam resolves for this process's env.

    Local: ``BRIDGE_EXCHANGE_DB_URL`` (an async DSN) → :class:`SqlExchangeStore`, unset →
    :class:`InMemoryExchangeStore`. GCP: the Cloud SQL ``DATABASE_URL`` (required — no
    in-memory fallback). ``bridge.seams`` is imported lazily (it pulls
    ``bridge.extraction``; ``bridge.dashboard`` imports this module).
    """
    from bridge.seams import Seam, get_adapter, resolve_mode

    adapter = get_adapter(Seam.exchange_store, resolve_mode(Seam.exchange_store))
    return adapter.build_exchange_store()  # type: ignore[attr-defined]
