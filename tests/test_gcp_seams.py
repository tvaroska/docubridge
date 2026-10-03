"""GCP shared-store seam adapter unit tests (P7-2).

These run ALWAYS (no ``adapter`` fixture, no BRIDGE_TEST_GCP gate): they exercise
the structural, GCP-credential-free surface of the Cloud SQL adapters — the
terminal-write retry wrapper, the async-Postgres driver guard, and the
refuse-in-memory (session-affinity trap) guard. The end-to-end Cloud SQL roundtrip
is in ``test_durable_seams.py`` (gcp params, P7-9).
"""
from __future__ import annotations

import logging

import pytest

from bridge.seams import Seam, SeamMode, get_adapter
from bridge.seams.gcp import (
    DATABASE_URL_ENV,
    GcpExchangeStore,
    GcpSessions,
    GcpTaskStore,
    _resolve_dsn,
    _TerminalRetryTaskStore,
    require_async_pg_driver,
)


def _task(terminal: bool):
    from a2a.types import Task, TaskState, TaskStatus

    state = (
        TaskState.TASK_STATE_COMPLETED if terminal else TaskState.TASK_STATE_WORKING
    )
    return Task(id="t-1", context_id="c-1", status=TaskStatus(state=state))


class _FakeInner:
    """A minimal TaskStore-shaped double: ``save`` raises ``fail_times`` times then
    succeeds; ``get``/``delete``/``list`` record delegation."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.save_calls = 0
        self.other_calls: list[str] = []

    async def save(self, task, context) -> None:
        self.save_calls += 1
        if self.save_calls <= self.fail_times:
            raise RuntimeError(f"boom {self.save_calls}")

    async def get(self, task_id, context):
        self.other_calls.append(f"get:{task_id}")
        return "got"

    async def delete(self, task_id, context) -> None:
        self.other_calls.append(f"delete:{task_id}")

    async def list(self, params, context):
        self.other_calls.append("list")
        return "listed"


# --- terminal-write retry wrapper -----------------------------------------


async def test_terminal_retry_succeeds_after_one_failure(caplog):
    """A terminal save that fails once then succeeds: one retry, a WARNING logged,
    eventual success (no exception)."""
    inner = _FakeInner(fail_times=1)
    store = _TerminalRetryTaskStore(inner)

    with caplog.at_level(logging.WARNING, logger="bridge.seams.gcp"):
        await store.save(_task(terminal=True), context=None)

    assert inner.save_calls == 2  # failed once, then succeeded
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING on the retried terminal write"
    assert "save failed" in warnings[0].getMessage()


async def test_terminal_retry_exhausts_then_reraises(caplog):
    """A terminal save that always fails: retries are exhausted, an ERROR is
    logged, and the last exception re-raises."""
    inner = _FakeInner(fail_times=99)
    store = _TerminalRetryTaskStore(inner, attempts=3)

    with caplog.at_level(logging.WARNING, logger="bridge.seams.gcp"):  # noqa: SIM117
        with pytest.raises(RuntimeError, match="boom 3"):
            await store.save(_task(terminal=True), context=None)

    assert inner.save_calls == 3  # exactly `attempts` tries
    assert [r for r in caplog.records if r.levelno == logging.WARNING]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR when terminal retries are exhausted"
    assert "exhausted" in errors[0].getMessage()


async def test_terminal_retry_skips_non_terminal_write(caplog):
    """A NON-terminal save that fails is NOT retried (single attempt) and re-raises
    without WARNING/ERROR — only the terminal write is the loud contract."""
    inner = _FakeInner(fail_times=99)
    store = _TerminalRetryTaskStore(inner, attempts=3)

    with caplog.at_level(logging.WARNING, logger="bridge.seams.gcp"):  # noqa: SIM117
        with pytest.raises(RuntimeError, match="boom 1"):
            await store.save(_task(terminal=False), context=None)

    assert inner.save_calls == 1  # single attempt, no retry
    assert not caplog.records  # nothing logged for a non-terminal write


async def test_retry_wrapper_delegates_other_methods():
    """get/delete/list delegate straight through to the inner store."""
    inner = _FakeInner()
    store = _TerminalRetryTaskStore(inner)

    assert await store.get("x", context=None) == "got"
    await store.delete("y", context=None)
    assert await store.list("params", context=None) == "listed"
    assert inner.other_calls == ["get:x", "delete:y", "list"]


# --- async-Postgres driver guard ------------------------------------------


def test_require_async_pg_driver_accepts_asyncpg():
    dsn = "postgresql+asyncpg://u:p@h/db"
    assert require_async_pg_driver(dsn) == dsn


def test_require_async_pg_driver_rejects_sync_postgres():
    with pytest.raises(ValueError, match="async Postgres driver"):
        require_async_pg_driver("postgresql://u:p@h/db")


def test_gcp_task_store_rejects_sync_postgres_dsn():
    """Building a gcp task_store with a sync postgres DSN raises the async-driver
    error (before any create_async_engine — asyncpg need not be installed)."""
    with pytest.raises(ValueError, match="async Postgres driver"):
        get_adapter(Seam.task_store, SeamMode.gcp).build_task_store(
            db_url="postgresql://u:p@h/db"
        )


# --- refuse-in-memory (the session-affinity trap guard) --------------------


def test_gcp_refuses_in_memory_without_dsn(monkeypatch):
    """No db_url + no DATABASE_URL env → RuntimeError. The gcp adapter NEVER falls
    back to an in-memory store (that IS the session-affinity trap)."""
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        get_adapter(Seam.task_store, SeamMode.gcp).build_task_store()


def test_resolve_dsn_reads_secret_env(monkeypatch):
    """The DATABASE_URL secret env is the deployed DSN source when db_url is unset."""
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql+asyncpg://u:p@h/db")
    assert _resolve_dsn() == "postgresql+asyncpg://u:p@h/db"


def test_resolve_dsn_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql+asyncpg://env/db")
    assert _resolve_dsn("postgresql+asyncpg://arg/db") == "postgresql+asyncpg://arg/db"


# --- exchange_store: Cloud SQL records, never in memory (S0-arch-14) -------


def test_gcp_exchange_store_refuses_in_memory_and_sync_pg(monkeypatch):
    """GcpExchangeStore builds a SqlExchangeStore over the Cloud SQL DSN, refuses an
    in-memory fallback (session-affinity trap) and a sync driver, and opens nothing
    (no asyncpg import) until the store's first operation."""
    import sys

    from bridge.exchange_store import SqlExchangeStore

    inst = get_adapter(Seam.exchange_store, SeamMode.gcp)
    assert isinstance(inst, GcpExchangeStore)
    assert inst.seam is Seam.exchange_store

    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match="session-affinity"):
        inst.build_exchange_store()
    with pytest.raises(ValueError, match="async Postgres"):
        inst.build_exchange_store(db_url="postgresql://x/y")

    store = inst.build_exchange_store(db_url="postgresql+asyncpg://u@h/db")
    try:
        assert isinstance(store, SqlExchangeStore)
        assert store.dsn == "postgresql+asyncpg://u@h/db"
        assert "asyncpg" not in sys.modules
    finally:
        store.close()  # never opened → a no-op

    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql+asyncpg://env/db")
    assert inst.build_exchange_store().dsn == "postgresql+asyncpg://env/db"


def test_gcp_sessions_and_task_store_types():
    assert isinstance(get_adapter(Seam.sessions, SeamMode.gcp), GcpSessions)
    assert isinstance(get_adapter(Seam.task_store, SeamMode.gcp), GcpTaskStore)


# --- importing the gcp pkg pulls no heavy deps -----------------------------


def test_importing_gcp_pkg_does_not_import_asyncpg():
    """The gcp pkg lazy-imports a2a/sqlalchemy/asyncpg inside methods; importing it
    must not pull asyncpg (the default non-gcp suite runs without the gcp extra)."""
    import sys

    # bridge.seams.gcp is already imported by this test module's top-level import;
    # the invariant is simply that doing so did not import asyncpg.
    assert "bridge.seams.gcp" in sys.modules
    assert "asyncpg" not in sys.modules
