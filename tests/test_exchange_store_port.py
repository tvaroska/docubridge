"""Port parity for the exchange_store (S0-arch-14): materialized Exchange records.

The shared ``@pytest.mark.port("exchange_store")`` cases run against both adapters:
local → a file-backed ``sqlite+aiosqlite`` store under ``tmp_path``; gcp → the Cloud SQL
Postgres DSN in ``BRIDGE_GCP_TEST_DSN`` (gated on ``BRIDGE_TEST_GCP=1`` by the ``adapter``
fixture AND skipped without a provisioned DSN — the ``test_durable_ports.py`` pattern).
Each case writes under a uuid-suffixed ``kind`` so reruns against a persistent Cloud SQL
never collide.

Covered: durable roundtrip across close/reopen, the compare-and-set contract (create-only
at version 0; a stale update raises and leaves the record unchanged), ``list`` in creation
order with kind isolation, cross-replica read-after-write (two independent handles, no
close in between — design/ports.md), and JSON parity. Plain tests below pin the local
adapter's resolution (in-memory default, env DSN, async-driver guard) and run the CAS /
ordering / JSON cases against the in-memory store too.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator

import pytest

from bridge.exchange_store import (
    ExchangeStore,
    InMemoryExchangeStore,
    SqlExchangeStore,
    StaleExchangeWrite,
)
from bridge.ports import Port, PortMode, get_adapter
from bridge.ports.local import EXCHANGE_DB_URL_ENV
from tests.test_durable_ports import _gcp_dsn_or_skip


def _kind() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def open_store(adapter, tmp_path, monkeypatch) -> Iterator[Callable[[], ExchangeStore]]:
    """A factory of INDEPENDENT store handles over one backing store; all closed after."""
    if adapter is PortMode.gcp:
        dsn = _gcp_dsn_or_skip()
    else:
        monkeypatch.delenv(EXCHANGE_DB_URL_ENV, raising=False)
        dsn = f"sqlite+aiosqlite:///{tmp_path}/x.db"
    handles: list[ExchangeStore] = []

    def make() -> ExchangeStore:
        store = get_adapter(Port.exchange_store, adapter).build_exchange_store(
            db_url=dsn
        )
        assert isinstance(store, SqlExchangeStore)
        handles.append(store)
        return store

    yield make
    for store in handles:
        store.close()


# --- shared port cases (local always, gcp gated) -----------------------------


@pytest.mark.port("exchange_store")
def test_exchange_store_durable_roundtrip(open_store):
    kind = _kind()
    a = open_store()
    assert (
        a.put(kind, "r1", context_id="ctx-1", doc={"k": [1, "v"]}, expected_version=0)
        == 1
    )
    a.close()  # the "process dies"

    b = open_store()
    got = b.get(kind, "r1")
    assert got is not None
    assert (got.kind, got.ref, got.context_id, got.version) == (kind, "r1", "ctx-1", 1)
    assert got.doc == {"k": [1, "v"]}


@pytest.mark.port("exchange_store")
def test_exchange_store_version_check(open_store):
    _check_cas(open_store())


@pytest.mark.port("exchange_store")
def test_exchange_store_list_creation_order_and_kind_isolation(open_store):
    _check_listing(open_store())


@pytest.mark.port("exchange_store")
def test_exchange_store_cross_replica_read_after_write(open_store):
    """Write through handle A, read through an independent handle B (no close between)."""
    kind = _kind()
    a, b = open_store(), open_store()
    assert a.put(kind, "rfp-1", context_id="ctx", doc={"n": 1}, expected_version=0) == 1

    seen = b.get(kind, "rfp-1")
    assert seen is not None and seen.doc == {"n": 1} and seen.version == 1
    assert [r.ref for r in b.list(kind)] == ["rfp-1"]

    # B updates; A's write derived from its (now stale) version-1 read is refused.
    assert b.put(kind, "rfp-1", context_id="ctx", doc={"n": 2}, expected_version=1) == 2
    with pytest.raises(StaleExchangeWrite):
        a.put(kind, "rfp-1", context_id="ctx", doc={"n": 99}, expected_version=1)

    reread = a.get(kind, "rfp-1")
    assert reread is not None and reread.doc == {"n": 2} and reread.version == 2
    assert a.put(kind, "rfp-1", context_id="ctx", doc={"n": 3}, expected_version=2) == 3
    assert b.get(kind, "rfp-1").doc == {"n": 3}


@pytest.mark.port("exchange_store")
def test_exchange_store_json_parity(open_store):
    _check_json(open_store())


# --- the shared case bodies (also run against the in-memory store) -----------


def _check_cas(store: ExchangeStore) -> None:
    kind = _kind()
    assert store.put(kind, "r", context_id="c", doc={"v": 1}, expected_version=0) == 1
    with pytest.raises(StaleExchangeWrite, match="already exists"):
        store.put(kind, "r", context_id="c", doc={"v": "dup"}, expected_version=0)
    assert store.put(kind, "r", context_id="c", doc={"v": 2}, expected_version=1) == 2
    with pytest.raises(StaleExchangeWrite, match="no longer at version 1"):
        store.put(kind, "r", context_id="c", doc={"v": "stale"}, expected_version=1)
    got = store.get(kind, "r")
    assert got is not None and got.doc == {"v": 2} and got.version == 2
    # Updating a record that does not exist is stale too (never an implicit create).
    with pytest.raises(StaleExchangeWrite):
        store.put(kind, "absent", context_id="c", doc={}, expected_version=3)
    assert store.get(kind, "absent") is None
    with pytest.raises(ValueError, match="expected_version"):
        store.put(kind, "r", context_id="c", doc={}, expected_version=-1)
    with pytest.raises(ValueError, match="kind"):
        store.put("", "r", context_id="c", doc={}, expected_version=0)
    with pytest.raises(ValueError, match="ref"):
        store.put(kind, "", context_id="c", doc={}, expected_version=0)


def _check_listing(store: ExchangeStore) -> None:
    kind, other = _kind(), _kind()
    for ref in ("b", "a", "c"):
        store.put(
            kind, ref, context_id="shared-ctx", doc={"ref": ref}, expected_version=0
        )
    store.put(other, "z", context_id="shared-ctx", doc={}, expected_version=0)
    # An update does not reorder.
    store.put(kind, "b", context_id="shared-ctx", doc={"ref": "b2"}, expected_version=1)

    rows = store.list(kind)
    assert [r.ref for r in rows] == ["b", "a", "c"]
    assert [r.doc["ref"] for r in rows] == ["b2", "a", "c"]
    assert {r.kind for r in rows} == {kind}
    # The context token is non-unique: several records may share it.
    assert {r.context_id for r in rows} == {"shared-ctx"}
    assert [r.ref for r in store.list(other)] == ["z"]
    assert store.list(_kind()) == []
    assert store.get(kind, "nope") is None
    assert store.get(other, "b") is None


def _check_json(store: ExchangeStore) -> None:
    kind = _kind()
    with pytest.raises(TypeError):
        store.put(kind, "bad", context_id="c", doc={"x": object()}, expected_version=0)
    assert store.get(kind, "bad") is None and store.list(kind) == []

    store.put(kind, "r", context_id="c", doc={"nested": {"a": [1]}}, expected_version=0)
    got = store.get(kind, "r")
    got.doc["nested"]["a"].append(2)
    got.doc["added"] = True
    assert store.get(kind, "r").doc == {"nested": {"a": [1]}}


# --- local-only resolution + the in-memory store -----------------------------


def test_local_adapter_defaults_to_in_memory(monkeypatch):
    monkeypatch.delenv(EXCHANGE_DB_URL_ENV, raising=False)
    store = get_adapter(Port.exchange_store, PortMode.local).build_exchange_store()
    assert isinstance(store, InMemoryExchangeStore)


def test_local_adapter_honors_env_dsn(monkeypatch, tmp_path):
    dsn = f"sqlite+aiosqlite:///{tmp_path}/env.db"
    monkeypatch.setenv(EXCHANGE_DB_URL_ENV, dsn)
    store = get_adapter(Port.exchange_store, PortMode.local).build_exchange_store()
    try:
        assert isinstance(store, SqlExchangeStore) and store.dsn == dsn
        assert not (tmp_path / "env.db").exists()  # nothing opened before first use
        store.put("k", "r", context_id="c", doc={}, expected_version=0)
        assert (tmp_path / "env.db").exists()
    finally:
        store.close()


def test_local_adapter_arg_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv(EXCHANGE_DB_URL_ENV, f"sqlite+aiosqlite:///{tmp_path}/env.db")
    arg = f"sqlite+aiosqlite:///{tmp_path}/arg.db"
    store = get_adapter(Port.exchange_store, PortMode.local).build_exchange_store(
        db_url=arg
    )
    assert store.dsn == arg
    store.close()


def test_local_adapter_rejects_sync_sqlite(monkeypatch):
    monkeypatch.delenv(EXCHANGE_DB_URL_ENV, raising=False)
    with pytest.raises(ValueError, match="async driver"):
        get_adapter(Port.exchange_store, PortMode.local).build_exchange_store(
            db_url="sqlite:///x.db"
        )


def test_exchange_store_from_env_resolves_the_port(monkeypatch, tmp_path):
    from bridge.exchange_store import exchange_store_from_env

    monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)
    monkeypatch.delenv("BRIDGE_PORT_EXCHANGE_STORE", raising=False)
    monkeypatch.delenv(EXCHANGE_DB_URL_ENV, raising=False)
    assert isinstance(exchange_store_from_env(), InMemoryExchangeStore)

    monkeypatch.setenv(EXCHANGE_DB_URL_ENV, f"sqlite+aiosqlite:///{tmp_path}/e.db")
    store = exchange_store_from_env()
    assert isinstance(store, SqlExchangeStore)
    store.close()

    monkeypatch.setenv("BRIDGE_PORT_EXCHANGE_STORE", "gcp")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="session-affinity"):
        exchange_store_from_env()


@pytest.mark.parametrize("check", [_check_cas, _check_listing, _check_json])
def test_in_memory_store_honors_the_contract(check):
    check(InMemoryExchangeStore())


def test_sql_store_works_from_a_thread_running_its_own_loop(tmp_path):
    """The sync facade is callable from inside a running event loop (a FastAPI route)."""
    import asyncio

    store = SqlExchangeStore(f"sqlite+aiosqlite:///{tmp_path}/loop.db")
    try:

        async def route() -> int:
            store.put("k", "r", context_id="c", doc={"a": 1}, expected_version=0)
            return store.get("k", "r").version

        assert asyncio.run(route()) == 1
    finally:
        store.close()


def test_sql_store_close_is_idempotent_and_final(tmp_path):
    never_used = SqlExchangeStore(f"sqlite+aiosqlite:///{tmp_path}/never.db")
    never_used.close()
    never_used.close()
    assert not (tmp_path / "never.db").exists()

    store = SqlExchangeStore(f"sqlite+aiosqlite:///{tmp_path}/c.db")
    store.put("k", "r", context_id="c", doc={}, expected_version=0)
    thread = store._thread
    assert thread is not None and thread.is_alive()
    store.close()
    store.close()
    assert not thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        store.get("k", "r")


def test_sql_store_refuses_a_reentrant_call_from_its_loop_thread(tmp_path):
    store = SqlExchangeStore(f"sqlite+aiosqlite:///{tmp_path}/re.db")
    try:
        store.put("k", "r", context_id="c", doc={}, expected_version=0)
        errors: list[BaseException] = []

        def inside() -> None:
            try:
                store.get("k", "r")
            except RuntimeError as exc:
                errors.append(exc)

        done = threading.Event()
        store._loop.call_soon_threadsafe(lambda: (inside(), done.set()))
        assert done.wait(5)
        assert errors and "re-entrant" in str(errors[0])
        assert store.get("k", "r").version == 1  # the loop stayed healthy
    finally:
        store.close()
