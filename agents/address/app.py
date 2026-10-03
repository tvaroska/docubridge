"""``build_address_app`` — the Address ``Workflow`` on an ADK ``App`` with
``ResumabilityConfig`` (DUR-1), plus a durable session-service factory.

Wrapping the ``Workflow`` in an ``App`` carrying ``ResumabilityConfig(is_resumable=
True)`` is the only path that makes a ``Runner`` resumable: ``Runner(node=wf)``
wraps the node in an ``App`` with **no** resumability config, so the switch never
reaches the Runner. To durably park/resume (P4-2) you must build the ``App`` here
and pass ``Runner(app=app, …)``.

This module must NOT import ``bridge/`` (project convention — ``agents/address``
reaches the Bridge by card URL only; the mock→real swap is a URL change). The
durable session factory below is therefore a small **stock-ADK** duplicate of
``bridge.seams.local.LocalSessions.build_session_service`` — parity is by DSN +
ADK class, not a shared object (§1.4). Keep the two identical.

# P7: the local↔GCP swap for the Address consumer's sessions (this factory vs the
# GCP sessions adapter) must be reconciled at deploy — see the P7 phase in PLAN.md.
"""
from __future__ import annotations

import os

from google.adk.apps.app import App, ResumabilityConfig

from agents.address.gate import DEFAULT_MAX_ROUNDS
from agents.address.workflow import build_address_workflow

# Async sqlite DSN prefix — the async driver is mandatory (see require_async_driver).
DEFAULT_SQLITE_DSN = "sqlite+aiosqlite:///"


def require_async_driver(dsn: str) -> str:
    """Reject a sync sqlite DSN early with a clear message (design gotcha,
    design/long-running.md:39).

    ``DatabaseSessionService`` runs on SQLAlchemy's asyncio extension, which needs
    an async driver; a bare ``sqlite:///`` URL fails cryptically. Use
    ``sqlite+aiosqlite:///…``.

    NOTE: duplicated identically in ``bridge/seams/local/__init__.py`` (§1.4 —
    ``agents/`` must not import ``bridge/``).
    """
    if dsn.startswith("sqlite:///") or dsn == "sqlite://":
        raise ValueError(
            f"DatabaseSessionService/DatabaseTaskStore need an async driver; "
            f"use 'sqlite+aiosqlite:///…' not {dsn!r}"
        )
    return dsn


def build_durable_session_service(db_url: str | None = None):
    """Build the Address consumer's session service.

    Durable ``DatabaseSessionService`` over an async sqlite DSN when one is
    configured (``db_url`` arg or ``BRIDGE_SESSION_DB_URL`` env), else an in-memory
    service (keeps the default driver path file-free). Mirrors the sessions seam's
    local adapter (§1.4).
    """
    dsn = db_url or os.environ.get("BRIDGE_SESSION_DB_URL", "").strip()
    if not dsn:
        from google.adk.sessions import InMemorySessionService

        return InMemorySessionService()
    require_async_driver(dsn)
    from google.adk.sessions import DatabaseSessionService

    return DatabaseSessionService(db_url=dsn)


def build_address_app(
    card_url: str,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    httpx_client=None,
    collect_node=None,
    is_resumable: bool = True,
) -> App:
    """Build the Address ``Workflow`` wrapped in a resumable ADK ``App``.

    The ``App`` carries ``ResumabilityConfig(is_resumable=…)`` so a
    ``Runner(app=app, …)`` exposes ``runner.resumability_config.is_resumable`` —
    the switch P4-2's durable park/resume relies on. ``ResumabilityConfig`` is a
    registered experimental surface (TEST-4); using it here is sanctioned.

    Args:
        card_url: Bridge Agent Card URL for the collect node.
        max_rounds: Round ceiling forwarded to the workflow gate.
        httpx_client: Test seam forwarded to the collect node (in-process mock).
        collect_node: Test seam — inject a stub collect node instead of the live
            ``RemoteA2aAgent``.
        is_resumable: The ``ResumabilityConfig`` switch (default True).

    Returns:
        An ``App`` whose ``root_agent`` is the Address ``Workflow``.
    """
    wf = build_address_workflow(
        card_url,
        max_rounds=max_rounds,
        httpx_client=httpx_client,
        collect_node=collect_node,
    )
    return App(
        name="address",
        root_agent=wf,
        resumability_config=ResumabilityConfig(is_resumable=is_resumable),
    )
