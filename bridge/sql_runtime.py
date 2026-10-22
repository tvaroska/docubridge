"""Shared per-DSN async SQLAlchemy engine and event-loop runtime for Bridge SQL stores.

Consolidates the background event-loop thread and ``AsyncEngine`` connection pool
across ``SqlExchangeStore``, ``SqlOperationalStore``, ``SqlAgentGymEngine``, and
``SqlActionInboxStore``. When multiple SQL-backed stores in the same process point at
the same database DSN (for example ``DATABASE_URL`` on Cloud Run or a shared SQLite
file in multi-store tests), they lease a single refcounted ``DsnSqlRuntime`` — one
daemon event-loop thread and one SQLAlchemy ``AsyncEngine`` connection pool per DSN —
instead of spawning four separate event loops and four separate connection pools.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

DEFAULT_OP_TIMEOUT_S = 30.0


def _engine_kwargs_for_dsn(dsn: str) -> dict[str, Any]:
    """Return production-safe SQLAlchemy ``create_async_engine`` kwargs for ``dsn``."""
    kwargs: dict[str, Any] = {}
    lowered = dsn.lower()
    if lowered.startswith("postgresql") or lowered.startswith("postgres"):
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = int(os.environ.get("BRIDGE_SQL_POOL_SIZE", "5"))
        kwargs["max_overflow"] = int(os.environ.get("BRIDGE_SQL_MAX_OVERFLOW", "10"))
    return kwargs


class DsnSqlRuntime:
    """Refcounted event-loop thread and ``AsyncEngine`` shared by stores on one DSN."""

    def __init__(
        self,
        dsn: str,
        *,
        thread_name: str = "bridge-sql-runtime",
    ) -> None:
        self.dsn = dsn
        self.refcount = 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever,
            name=thread_name,
            daemon=True,
        )
        self.thread.start()
        try:
            from sqlalchemy.ext.asyncio import create_async_engine

            self.engine: Any = create_async_engine(dsn, **_engine_kwargs_for_dsn(dsn))
        except BaseException:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5.0)
            self.loop.close()
            raise

    def run(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        timeout_s: float = DEFAULT_OP_TIMEOUT_S,
    ) -> Any:
        """Execute ``coro`` on this runtime's event loop and block for its result."""
        if threading.current_thread() is self.thread:
            coro.close()
            raise RuntimeError(
                "re-entrant exchange-store call from its own loop thread would deadlock"
            )
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=timeout_s)
        except TimeoutError:
            fut.cancel()
            raise

    def dispose_and_stop(self, *, timeout_s: float = DEFAULT_OP_TIMEOUT_S) -> None:
        """Dispose the shared ``AsyncEngine`` and stop the daemon event-loop thread."""
        if not self.thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.engine.dispose(), self.loop).result(
                timeout=timeout_s
            )
        except Exception:
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=timeout_s)
            if not self.loop.is_running() and not self.loop.is_closed():
                self.loop.close()


_REGISTRY_LOCK = threading.Lock()
_RUNTIMES: dict[str, DsnSqlRuntime] = {}


def acquire_dsn_runtime(
    dsn: str,
    *,
    init_coro_factory: Callable[[Any], Coroutine[Any, Any, None]] | None = None,
    thread_name: str = "bridge-sql-runtime",
    timeout_s: float = DEFAULT_OP_TIMEOUT_S,
) -> DsnSqlRuntime:
    """Acquire a refcounted ``DsnSqlRuntime`` for ``dsn`` and run ``init_coro_factory``."""
    with _REGISTRY_LOCK:
        runtime = _RUNTIMES.get(dsn)
        if runtime is None or not runtime.thread.is_alive():
            runtime = DsnSqlRuntime(dsn, thread_name=thread_name)
            _RUNTIMES[dsn] = runtime
        runtime.refcount += 1

    if init_coro_factory is not None:
        try:
            runtime.run(init_coro_factory(runtime.engine), timeout_s=timeout_s)
        except BaseException:
            release_dsn_runtime(dsn, runtime, timeout_s=timeout_s)
            raise
    return runtime


def release_dsn_runtime(
    dsn: str,
    runtime: DsnSqlRuntime | None,
    *,
    timeout_s: float = DEFAULT_OP_TIMEOUT_S,
) -> None:
    """Decrement the lease count on ``runtime`` and dispose it when ``refcount == 0``."""
    if runtime is None:
        return
    should_dispose = False
    with _REGISTRY_LOCK:
        current = _RUNTIMES.get(dsn)
        if current is runtime:
            runtime.refcount -= 1
            if runtime.refcount <= 0:
                _RUNTIMES.pop(dsn, None)
                should_dispose = True
        else:
            runtime.refcount = max(0, runtime.refcount - 1)
            if runtime.refcount == 0:
                should_dispose = True
    if should_dispose:
        runtime.dispose_and_stop(timeout_s=timeout_s)


def get_active_dsn_runtime(dsn: str) -> DsnSqlRuntime | None:
    """Return the active ``DsnSqlRuntime`` for ``dsn`` if one is currently leased."""
    with _REGISTRY_LOCK:
        return _RUNTIMES.get(dsn)


def active_dsn_runtime_count() -> int:
    """Return the total number of distinct DSN runtimes currently active in-process."""
    with _REGISTRY_LOCK:
        return len(_RUNTIMES)


def _shutdown_all_runtimes() -> None:
    """Cleanly dispose any remaining SQL runtimes at interpreter shutdown."""
    with _REGISTRY_LOCK:
        runtimes = list(_RUNTIMES.values())
        _RUNTIMES.clear()
    for rt in runtimes:
        rt.dispose_and_stop(timeout_s=5.0)


atexit.register(_shutdown_all_runtimes)
