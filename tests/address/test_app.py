"""``build_address_app`` + the durable session factory (P4-1, DUR-1).

Proves the resumability switch reaches the ``Runner`` (the thing P4-2's durable
park/resume relies on) and that the agent-side session factory mirrors the sessions
seam without importing ``bridge/``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.apps.app import App, ResumabilityConfig
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import (
    DatabaseSessionService,
    InMemorySessionService,
)

from agents.address.app import (
    build_address_app,
    build_durable_session_service,
)

BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH


def test_build_address_app_is_resumable():
    """The App carries ResumabilityConfig(is_resumable=True) on the Workflow."""
    app = build_address_app(CARD_URL)
    assert isinstance(app, App)
    assert isinstance(app.resumability_config, ResumabilityConfig)
    assert app.resumability_config.is_resumable is True
    assert app.name == "address"
    assert app.root_agent.name == "address"


def test_build_address_app_resumable_off():
    """is_resumable=False flows through to the App's ResumabilityConfig."""
    app_off = build_address_app(CARD_URL, is_resumable=False)
    assert app_off.resumability_config.is_resumable is False


def test_runner_exposes_resumability():
    """Runner(app=app) surfaces the resumability switch (the P4-2 dependency)."""
    app = build_address_app(CARD_URL)
    runner = Runner(
        app=app,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    assert runner.resumability_config.is_resumable is True


def test_durable_session_service_builds_database(tmp_path, monkeypatch):
    """A configured async DSN builds a DatabaseSessionService."""
    monkeypatch.delenv("BRIDGE_SESSION_DB_URL", raising=False)
    svc = build_durable_session_service(
        db_url=f"sqlite+aiosqlite:///{tmp_path}/s.db"
    )
    assert isinstance(svc, DatabaseSessionService)


def test_durable_session_service_in_memory_default(monkeypatch):
    """No DSN → in-memory session service (keeps the default path file-free)."""
    monkeypatch.delenv("BRIDGE_SESSION_DB_URL", raising=False)
    svc = build_durable_session_service()
    assert isinstance(svc, InMemorySessionService)


def test_durable_session_service_async_driver_guard():
    """A bare sync sqlite DSN is rejected with a clear async-driver message."""
    with pytest.raises(ValueError, match="async driver"):
        build_durable_session_service(db_url="sqlite:///x.db")


def test_app_module_does_not_import_bridge():
    """Convention: agents/address/app.py must not import bridge/ (§1.4)."""
    src = Path(__file__).resolve().parents[2] / "agents" / "address" / "app.py"
    text = src.read_text()
    assert "import bridge" not in text
    assert "from bridge" not in text
