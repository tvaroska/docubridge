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
IAM_AUTH_ENV = "BRIDGE_DB_IAM_AUTH"


def is_iam_auth_enabled() -> bool:
    """Return ``True`` when Cloud SQL IAM database authentication is enabled via ``BRIDGE_DB_IAM_AUTH``."""
    return os.environ.get(IAM_AUTH_ENV, "").strip().lower() in ("1", "true", "yes")


def fetch_cloud_sql_iam_token() -> str:
    """Mint a short-lived Cloud SQL IAM OAuth2 access token from Application Default Credentials."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/sqlservice.admin"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    if not creds.token:
        raise RuntimeError(
            "failed to mint Cloud SQL IAM token from Application Default Credentials"
        )
    return str(creds.token)


def enable_iam_auth(
    engine: Any,
    *,
    token_fetcher: Callable[[], str] | None = None,
) -> Any:
    """Attach a SQLAlchemy ``do_connect`` hook that injects a short-lived Cloud SQL IAM token.

    When ``cparams`` has no password (or an empty password), injects ``cparams['password']``
    from ``token_fetcher`` (defaults to :func:`fetch_cloud_sql_iam_token`).
    """
    from sqlalchemy import event

    fetcher = token_fetcher or fetch_cloud_sql_iam_token
    sync_engine = getattr(engine, "sync_engine", engine)

    @event.listens_for(sync_engine, "do_connect")
    def _inject_iam_token(
        dialect: Any, conn_rec: Any, cargs: Any, cparams: dict[str, Any]
    ) -> None:
        del dialect, conn_rec, cargs
        if not cparams.get("password"):
            cparams["password"] = fetcher()

    return engine


def _engine_kwargs_for_dsn(dsn: str) -> dict[str, Any]:
    """Return production-safe SQLAlchemy ``create_async_engine`` kwargs for ``dsn``."""
    kwargs: dict[str, Any] = {}
    lowered = dsn.lower()
    if lowered.startswith("postgresql") or lowered.startswith("postgres"):
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = int(os.environ.get("BRIDGE_SQL_POOL_SIZE", "5"))
        kwargs["max_overflow"] = int(os.environ.get("BRIDGE_SQL_MAX_OVERFLOW", "10"))
    return kwargs


def create_async_engine_with_iam(
    dsn: str,
    *,
    token_fetcher: Callable[[], str] | None = None,
    **kwargs: Any,
) -> Any:
    """Create a SQLAlchemy ``AsyncEngine`` and attach Cloud SQL IAM auth when enabled."""
    from sqlalchemy.ext.asyncio import create_async_engine

    merged = {**_engine_kwargs_for_dsn(dsn), **kwargs}
    engine = create_async_engine(dsn, **merged)
    lowered = dsn.lower()
    if (lowered.startswith("postgresql") or lowered.startswith("postgres")) and (
        token_fetcher is not None or is_iam_auth_enabled()
    ):
        enable_iam_auth(engine, token_fetcher=token_fetcher)
    return engine


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
            self.engine: Any = create_async_engine_with_iam(dsn)
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
