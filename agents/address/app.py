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
``bridge.ports.local.LocalSessions.build_session_service`` — parity is by DSN +
ADK class, not a shared object (§1.4). Keep the two identical.

# P7: the local↔GCP swap for the Address consumer's sessions (this factory vs the
# GCP sessions adapter) must be reconciled at deploy — see the P7 phase in PLAN.md.
"""

from __future__ import annotations

import os
from typing import Any

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

    NOTE: duplicated identically in ``bridge/ports/local/__init__.py`` (§1.4 —
    ``agents/`` must not import ``bridge/``).
    """
    if dsn.startswith("sqlite:///") or dsn == "sqlite://":
        raise ValueError(
            f"DatabaseSessionService/DatabaseTaskStore need an async driver; "
            f"use 'sqlite+aiosqlite:///…' not {dsn!r}"
        )
    return dsn


def _parse_agent_engine_resource(
    raw: str | None,
    *,
    project: str | None = None,
    location: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Parse an Agent Engine ID or full resource name (duplicated from
    ``bridge.ports.gcp`` so ``agents/`` never imports ``bridge/``)."""
    proj = (project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip() or None
    loc = (location or os.environ.get("GOOGLE_CLOUD_LOCATION", "")).strip() or None
    val = (
        raw if raw is not None else os.environ.get("BRIDGE_AGENT_ENGINE_ID", "")
    ).strip()
    if not val:
        return proj, loc, None
    parts = val.strip("/").split("/")
    if (
        len(parts) == 6
        and parts[0] == "projects"
        and parts[2] == "locations"
        and parts[4] == "reasoningEngines"
    ):
        return parts[1] or proj, parts[3] or loc, parts[5] or None
    return proj, loc, val


def build_durable_session_service(
    db_url: str | None = None,
    *,
    agent_engine_id: str | None = None,
    project: str | None = None,
    location: str | None = None,
    vertex_factory: Any = None,
) -> Any:
    """Build the Address consumer's session service.

    Precedence:
    1. Explicit ``db_url`` argument → ``DatabaseSessionService(db_url=db_url)``.
    2. ``agent_engine_id`` argument or ``BRIDGE_AGENT_ENGINE_ID`` env →
       ``VertexAiSessionService(project=..., location=..., agent_engine_id=...)``.
    3. ``BRIDGE_SESSION_DB_URL`` or ``DATABASE_URL`` env →
       ``DatabaseSessionService(db_url=dsn)``.
    4. Else ``InMemorySessionService()`` (keeps the default local driver file-free).
    """
    if db_url:
        require_async_driver(db_url)
        from google.adk.sessions import DatabaseSessionService
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if db_url.startswith("sqlite") else {}
        return DatabaseSessionService(db_url=db_url, **kwargs)

    proj, loc, eid = _parse_agent_engine_resource(
        agent_engine_id, project=project, location=location
    )
    if eid:
        if vertex_factory is not None:
            return vertex_factory(project=proj, location=loc, agent_engine_id=eid)
        from google.adk.sessions import VertexAiSessionService

        return VertexAiSessionService(project=proj, location=loc, agent_engine_id=eid)

    dsn = (
        os.environ.get("BRIDGE_SESSION_DB_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not dsn:
        from google.adk.sessions import InMemorySessionService

        return InMemorySessionService()
    require_async_driver(dsn)
    from google.adk.sessions import DatabaseSessionService
    from sqlalchemy.pool import NullPool

    kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
    return DatabaseSessionService(db_url=dsn, **kwargs)


def build_address_app(
    card_url: str,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    httpx_client: Any = None,
    collect_node: Any = None,
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
        httpx_client: Test port forwarded to the collect node (in-process mock).
        collect_node: Test port — inject a stub collect node instead of the live
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
