"""Agent-Console ASGI host (P6-3) — the App/Runner host, streamed over SSE.

A standalone ``FastAPI`` host in the **internal zone** (the mirror of P6-2's external
Provider Portal): it starts a run **from a prepared request** (launches
``agents.address.driver.run_collection`` as a background task over a console-owned
session service), keeps a session registry (the session list), projects a per-turn view
(ledger + gate rounds = "reasoning" + ``is_satisfied`` = the Sense-B satisfaction check +
the returned requirements), and **streams status over SSE** — the first
``text/event-stream`` surface in the repo (§1.7).

This is **SSE observability/control, NOT A2UI** — the service agent does not speak A2UI
(``design/frontend.md``). It renders the contract ``ExchangeTurn`` directly; it does
**not** import ``bridge/edge/a2ui.py`` or emit an ``A2uiView``.

Isolation (§2b): imports ``agents.address.*`` (via the engine) + ``contract.*`` +
``fastapi``/``starlette`` + stdlib only; NEVER ``bridge/`` — the Bridge is reached by
card URL alone (like ``agents/address/driver.py``), so the mock->real swap is a URL
change. The **test** may point ``card_url`` at an in-process ``bridge.edge`` app via an
injected ``httpx_client``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.adk.sessions import InMemorySessionService
from pydantic import BaseModel, ConfigDict

from agents.console import engine
from agents.console.store import (
    ConsoleSessionRecord,
    ConsoleStore,
    InMemoryConsoleStore,
)

# The Vite dev server origins. One per frontend app so ``scripts/run-all.sh`` can bring
# the surfaces up side by side (docs/local-dev.md); real per-party Gateway/Agent-Identity
# scoping is deploy-only (P7, ``design/zones.md``) — locally CORS just lets the dev UI in.
VITE_DEV_ORIGINS = [f"http://localhost:{port}" for port in range(5173, 5178)]

# Default local card URL for the dev server (the mock edge at :8001). Point at the real
# edge (:8000) for the issuer runs — ``BRIDGE_CARD_URL`` overrides, which is how
# ``scripts/run-console.sh`` selects the edge. The mock->real swap is a URL change (§2b).
DEFAULT_CARD_URL = "http://localhost:8001" + AGENT_CARD_WELL_KNOWN_PATH


def default_card_url() -> str:
    """The dev-server card URL: ``BRIDGE_CARD_URL`` if set, else the mock edge."""
    import os

    return os.environ.get("BRIDGE_CARD_URL", "").strip() or DEFAULT_CARD_URL


class StartSessionBody(BaseModel):
    """POST /sessions request body — start a run from a prepared-request key."""

    model_config = ConfigDict(extra="forbid")

    prepared_key: str


def build_console_app(
    *,
    card_url: str | None = None,
    httpx_client: Any = None,
    store: ConsoleStore | None = None,
    session_service: Any = None,
) -> FastAPI:
    """Build the Agent-Console FastAPI host.

    ``store`` defaults to an in-memory registry (native swap, §2c); ``session_service``
    defaults to a shared ``InMemorySessionService`` used across the launch AND the
    readers so the gate-written run state is visible in-process (§2c gotcha). ``card_url``
    is the Bridge edge (default ``default_card_url()``); tests inject an in-process edge
    app via ``httpx_client``.
    """
    card_url = card_url or default_card_url()
    active_store: ConsoleStore = store or InMemoryConsoleStore()
    svc = session_service or InMemorySessionService()
    # The background task handles are kept OUT of the serialized record (P7 durability):
    # a plain in-memory map keyed by console session id (§4.1).
    task_handles: dict[str, asyncio.Task] = {}

    app = FastAPI(title="Agent Console (console_server, SSE)")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=VITE_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _load(sid: str) -> ConsoleSessionRecord:
        try:
            return active_store.get(sid)
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown session {sid!r}"
            ) from e

    @app.get("/prepared")
    def list_prepared() -> list[dict]:
        return [
            {"key": pr.key, "label": pr.label}
            for pr in engine.PREPARED_REQUESTS.values()
        ]

    @app.post("/sessions")
    async def create_session(body: StartSessionBody) -> dict:
        try:
            rec = engine.start_session(
                active_store,
                task_handles,
                prepared_key=body.prepared_key,
                card_url=card_url,
                httpx_client=httpx_client,
                session_service=svc,
            )
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown prepared_key {body.prepared_key!r}"
            ) from e
        return engine.snapshot(rec)

    @app.get("/sessions")
    def list_sessions() -> list[dict]:
        # Newest-first for the UI (store keeps insertion order).
        return [engine.snapshot(rec) for rec in reversed(active_store.list())]

    @app.get("/sessions/{sid}")
    def get_session(sid: str) -> dict:
        return engine.snapshot(_load(sid))

    @app.get("/sessions/{sid}/events")
    def stream_events(sid: str) -> StreamingResponse:
        _load(sid)  # 404 for an unknown session BEFORE opening the stream
        return StreamingResponse(
            engine.sse_frames(active_store, session_id=sid),
            media_type="text/event-stream",
        )

    return app


def serve(host: str = "127.0.0.1", port: int = 8002) -> None:  # pragma: no cover
    """Run the console host with uvicorn for manual runs (mirrors portal/edge server).

    The dev server needs a real ``card_url`` (default the mock edge at :8001, or the real
    edge at :8000) and does NOT inject an ``httpx_client``.
    """
    import uvicorn

    uvicorn.run(build_console_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    serve()
