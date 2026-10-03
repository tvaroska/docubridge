"""Agent Console (P6-3) — the ``console_server`` App/Runner host + SSE surface.

The GUI form of the FE-1 CLI driver (``python -m agents.address``): both are
``run_collection`` hosts that fire the opening ``CollectRequest``; neither is an ADK
agent. The console starts a run from a prepared request, indexes it, projects a per-turn
view (ledger + gate rounds + the Sense-B satisfaction check + requirements), and streams
status over SSE. It is SSE observability/control, NOT A2UI — see ``server.py``.
"""

from __future__ import annotations

from agents.console.engine import (
    PREPARED_REQUESTS,
    PreparedRequest,
    doc_refs,
    snapshot,
    sse_frames,
    start_session,
)
from agents.console.server import build_console_app, serve
from agents.console.store import (
    ConsoleSessionRecord,
    ConsoleStore,
    InMemoryConsoleStore,
)

__all__ = [
    "PREPARED_REQUESTS",
    "ConsoleSessionRecord",
    "ConsoleStore",
    "InMemoryConsoleStore",
    "PreparedRequest",
    "build_console_app",
    "doc_refs",
    "serve",
    "snapshot",
    "sse_frames",
    "start_session",
]
