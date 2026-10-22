"""Seam-parity durability tests for the sessions/task_store seams (P4-1, DUR-1/DUR-2).

Both local adapters must build a **file-backed durable** service/store: write through
it, dispose the engine, re-open on the same DSN, and read the record back. This is the
P4-2 restart-proof in miniature — the record survives an engine lifecycle.

For gcp the adapters build the SAME ADK/a2a ``Database*`` classes over a Cloud SQL
Postgres DSN (P7-2). The gcp params of the roundtrip + cross-replica cases only
execute end-to-end against a provisioned Cloud SQL (P7-9): they are gated on
``BRIDGE_TEST_GCP=1`` by the ``adapter`` fixture AND ``skip`` when no Postgres DSN
env is set (a provisioned DSN is a P7-9 input, not a P7-2 artifact).
"""

from __future__ import annotations

import os

import pytest

from bridge.seams import Seam, SeamMode, get_adapter

# The Postgres DSN env the gcp params of the roundtrip/cross-replica cases read
# (a provisioned Cloud SQL DSN, a P7-9 input). Absent → skip even under
# BRIDGE_TEST_GCP=1; a bare-sync ``postgresql://`` would be rejected by the
# adapter's async-driver guard, so callers must supply ``postgresql+asyncpg://…``.
_GCP_DSN_ENV = "BRIDGE_GCP_TEST_DSN"


def _gcp_dsn_or_skip() -> str:
    dsn = os.environ.get(_GCP_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(
            f"set {_GCP_DSN_ENV}=postgresql+asyncpg://… (a provisioned Cloud SQL "
            "DSN, a P7-9 input) to run the gcp shared-store param"
        )
    return dsn


# --- sessions seam (DUR-1) -------------------------------------------------


@pytest.mark.seam("sessions")
async def test_sessions_durable_roundtrip(adapter, tmp_path, monkeypatch):
    """The sessions adapter builds a durable DatabaseSessionService that survives
    an engine dispose/reopen. local → tmp_path sqlite; gcp → Cloud SQL Postgres
    DSN (same ADK class), skipped without a provisioned DSN."""
    if adapter is SeamMode.gcp:
        dsn = _gcp_dsn_or_skip()
    else:
        monkeypatch.delenv("BRIDGE_SESSION_DB_URL", raising=False)
        dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"

    svc = get_adapter(Seam.sessions, adapter).build_session_service(db_url=dsn)
    await svc.create_session(
        app_name="address", user_id="u1", session_id="sess-1", state={"k": "v"}
    )
    # Dispose the engine — the record must survive a fresh connection pool.
    await svc.db_engine.dispose()

    svc2 = get_adapter(Seam.sessions, adapter).build_session_service(db_url=dsn)
    got = await svc2.get_session(app_name="address", user_id="u1", session_id="sess-1")
    assert got is not None
    assert got.id == "sess-1"
    assert got.state == {"k": "v"}


# --- task_store seam (DUR-2) -----------------------------------------------


@pytest.mark.seam("task_store")
async def test_task_store_durable_roundtrip(adapter, tmp_path, monkeypatch):
    """The task_store adapter builds a durable DatabaseTaskStore that survives an
    engine dispose/reopen. local → tmp_path sqlite; gcp → Cloud SQL Postgres DSN
    (same a2a class), skipped without a provisioned DSN."""
    from a2a.server.context import ServerCallContext, UnauthenticatedUser
    from a2a.types import Task, TaskState, TaskStatus

    if adapter is SeamMode.gcp:
        dsn = _gcp_dsn_or_skip()
    else:
        monkeypatch.delenv("BRIDGE_TASK_DB_URL", raising=False)
        dsn = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    ctx = ServerCallContext(user=UnauthenticatedUser())
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )

    store = get_adapter(Seam.task_store, adapter).build_task_store(db_url=dsn)
    await store.save(task, ctx)
    await _engine_of(store).dispose()

    store2 = get_adapter(Seam.task_store, adapter).build_task_store(db_url=dsn)
    got = await store2.get("task-1", ctx)
    assert got is not None
    assert got.id == "task-1"
    assert got.context_id == "ctx-1"
    assert got.status.state == TaskState.TASK_STATE_COMPLETED


def _engine_of(store):
    """The underlying AsyncEngine — the gcp task_store wraps DatabaseTaskStore in
    the terminal-retry wrapper, so reach through ``_inner`` when present."""
    return getattr(store, "_inner", store).engine


# --- cross-replica read-after-write (the session-affinity trap) ------------


@pytest.mark.seam("task_store")
async def test_task_store_cross_replica_read_after_write(
    adapter, tmp_path, monkeypatch
):
    """Write a TERMINAL task via one adapter handle, read it back via a SECOND,
    independent handle to the SAME backing store — with NO dispose between.

    Two independent ``build_task_store(db_url=dsn)`` calls = two independent
    engines/pools = the two-replica shape. This is the design's mandated case
    (design/seams.md, "the session-affinity trap") and a P7-9 exit-gate criterion:
    terminal-outcome parity alone passes trivially single-process and would MISS a
    per-replica store. local MUST use a tmp_path FILE sqlite DSN (in-memory sqlite
    is per-connection and would fail); gcp uses the Cloud SQL Postgres DSN.
    """
    from a2a.server.context import ServerCallContext, UnauthenticatedUser
    from a2a.types import Task, TaskState, TaskStatus

    if adapter is SeamMode.gcp:
        dsn = _gcp_dsn_or_skip()
    else:
        monkeypatch.delenv("BRIDGE_TASK_DB_URL", raising=False)
        dsn = f"sqlite+aiosqlite:///{tmp_path}/xrep.db"
    ctx = ServerCallContext(user=UnauthenticatedUser())
    task = Task(
        id="task-xrep",
        context_id="ctx-xrep",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )

    # Handle A (replica 1) writes the terminal task.
    handle_a = get_adapter(Seam.task_store, adapter).build_task_store(db_url=dsn)
    await handle_a.save(task, ctx)

    # Handle B (replica 2) — an INDEPENDENT engine/pool — reads it back, NO
    # dispose between. This is the shared-store proof.
    handle_b = get_adapter(Seam.task_store, adapter).build_task_store(db_url=dsn)
    got = await handle_b.get("task-xrep", ctx)

    assert got is not None
    assert got.id == "task-xrep"
    assert got.context_id == "ctx-xrep"
    assert got.status.state == TaskState.TASK_STATE_COMPLETED

    await _engine_of(handle_a).dispose()
    await _engine_of(handle_b).dispose()


# --- async-driver guard ----------------------------------------------------


def test_sessions_async_driver_guard():
    """A bare sync sqlite DSN is rejected with a clear async-driver message."""
    adapter = get_adapter(Seam.sessions, SeamMode.local)
    with pytest.raises(ValueError, match="async driver"):
        adapter.build_session_service(db_url="sqlite:///x.db")


def test_task_store_async_driver_guard():
    """A bare sync sqlite DSN is rejected with a clear async-driver message."""
    adapter = get_adapter(Seam.task_store, SeamMode.local)
    with pytest.raises(ValueError, match="async driver"):
        adapter.build_task_store(db_url="sqlite:///x.db")


# --- in-memory default (no DSN configured) ---------------------------------


def test_sessions_in_memory_default(monkeypatch):
    """With no DSN set, the sessions adapter returns an InMemorySessionService."""
    from google.adk.sessions import InMemorySessionService

    monkeypatch.delenv("BRIDGE_SESSION_DB_URL", raising=False)
    svc = get_adapter(Seam.sessions, SeamMode.local).build_session_service()
    assert isinstance(svc, InMemorySessionService)


def test_task_store_in_memory_default(monkeypatch):
    """With no DSN set, the task_store adapter returns an InMemoryTaskStore."""
    from a2a.server.tasks import InMemoryTaskStore

    monkeypatch.delenv("BRIDGE_TASK_DB_URL", raising=False)
    store = get_adapter(Seam.task_store, SeamMode.local).build_task_store()
    assert isinstance(store, InMemoryTaskStore)


def test_sessions_env_dsn_builds_durable(monkeypatch, tmp_path):
    """BRIDGE_SESSION_DB_URL drives the durable DatabaseSessionService path."""
    from google.adk.sessions import DatabaseSessionService

    monkeypatch.setenv(
        "BRIDGE_SESSION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/env.db"
    )
    svc = get_adapter(Seam.sessions, SeamMode.local).build_session_service()
    assert isinstance(svc, DatabaseSessionService)


def test_task_store_env_dsn_builds_durable(monkeypatch, tmp_path):
    """BRIDGE_TASK_DB_URL drives the durable DatabaseTaskStore path."""
    from a2a.server.tasks import DatabaseTaskStore

    monkeypatch.setenv("BRIDGE_TASK_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/env.db")
    store = get_adapter(Seam.task_store, SeamMode.local).build_task_store()
    assert isinstance(store, DatabaseTaskStore)


# --- push_config_store (P4-4, DUR-6) ---------------------------------------


def test_push_config_store_in_memory_default(monkeypatch):
    """With no DSN set, build_push_config_store returns an InMemoryPushNotificationConfigStore."""
    from a2a.server.tasks import InMemoryPushNotificationConfigStore

    monkeypatch.delenv("BRIDGE_TASK_DB_URL", raising=False)
    store = get_adapter(Seam.task_store, SeamMode.local).build_push_config_store()
    assert isinstance(store, InMemoryPushNotificationConfigStore)


def test_push_config_store_durable_with_dsn(monkeypatch, tmp_path):
    """BRIDGE_TASK_DB_URL drives the durable DatabasePushNotificationConfigStore path."""
    from a2a.server.tasks import DatabasePushNotificationConfigStore

    monkeypatch.setenv("BRIDGE_TASK_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/push.db")
    store = get_adapter(Seam.task_store, SeamMode.local).build_push_config_store()
    assert isinstance(store, DatabasePushNotificationConfigStore)


def test_push_config_store_async_driver_guard():
    """A bare sync sqlite DSN is rejected with a clear async-driver message."""
    adapter = get_adapter(Seam.task_store, SeamMode.local)
    with pytest.raises(ValueError, match="async driver"):
        adapter.build_push_config_store(db_url="sqlite:///x.db")
