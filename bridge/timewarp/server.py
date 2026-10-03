"""Time-warp ASGI host (P6-5, FE-5) — local-only, plain REST.

A standalone ``FastAPI`` host (internal zone, **local-only**, not deployed —
``design/frontend.md``: "plain REST, runs locally, not deployed") that exposes the
``TimewarpHost`` engine over REST so the presenter stage can drive the SLA ladder:
arm a session's ladder, fast-forward N ticks, single-step, get state, pause/resume, and
replay/reset. The clock advances only by these explicit calls — deterministic stage time.

**Shared store (§2b).** ``build_timewarp_app`` takes the ``PortalStore`` by injection (like
``build_dashboard_app``). A wired dev deployment passes ONE shared store into portal +
dashboard + timewarp so a fast-forward is visible on the dashboard SSE stream; a standalone
manual ``serve()`` builds its own ``InMemoryPortalStore``.

**Not a seam (§2a).** It composes existing primitives (``VirtualClock``, ``FollowupLadder``,
``PortalStore``). No new ``Seam`` enum entry, no GCP adapter here (Cloud Tasks is P7).

Isolation: imports ``contract/`` + ``bridge.*`` + ``fastapi``/``starlette``/``pydantic`` +
stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from bridge.portal.store import InMemoryPortalStore, PortalStore
from bridge.scheduler import Scheduler
from bridge.timewarp.host import TimewarpHost, TimewarpPaused

# The Vite dev server origins — one per frontend app so ``scripts/run-all.sh`` can bring the
# surfaces up side by side (docs/local-dev.md). Real per-party Gateway/Agent-Identity scoping
# is deploy-only (P7); locally CORS just lets the dev UI in (mirrors ``bridge/dashboard/server.py``).
VITE_DEV_ORIGINS = [f"http://localhost:{port}" for port in range(5173, 5178)]


class ArmBody(BaseModel):
    """POST /timewarp/arm request body."""

    model_config = ConfigDict(extra="forbid")

    session_id: str


class AdvanceBody(BaseModel):
    """POST /timewarp/advance request body (fast-forward by ``ticks``, or scrub ``to``)."""

    model_config = ConfigDict(extra="forbid")

    ticks: int | None = None
    to: int | None = None


def build_timewarp_app(*, store: PortalStore | None = None, clock: Scheduler | None = None) -> FastAPI:
    """Build the time-warp FastAPI host.

    ``store`` is the **shared PortalStore** the running Provider Portal writes to and the
    Ops Dashboard reads. Defaults to an in-memory store for standalone manual runs; tests
    inject a fresh shared store per case. ``clock`` lets a test inject a pre-seeded clock.
    """
    active_store: PortalStore = store or InMemoryPortalStore()
    host = TimewarpHost(active_store, clock=clock)
    app = FastAPI(title="Time-warp host (local-only, plain REST)")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=VITE_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/timewarp/arm")
    def post_arm(body: ArmBody) -> dict:
        try:
            return host.arm(body.session_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"unknown session {body.session_id!r}") from e

    @app.post("/timewarp/advance")
    def post_advance(body: AdvanceBody) -> dict:
        try:
            if body.to is not None:
                return host.advance_to(body.to)
            return host.advance(body.ticks if body.ticks is not None else 1)
        except TimewarpPaused as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            # VirtualClock raises ValueError on backward/negative moves -> 400, not 500.
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/timewarp/step")
    def post_step() -> dict:
        try:
            return host.step()
        except TimewarpPaused as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/timewarp/pause")
    def post_pause() -> dict:
        return host.pause()

    @app.post("/timewarp/resume")
    def post_resume() -> dict:
        return host.resume()

    @app.post("/timewarp/reset")
    def post_reset() -> dict:
        return host.reset()

    @app.get("/timewarp/state")
    def get_state() -> dict:
        return host.state()

    return app


def serve(host: str = "127.0.0.1", port: int = 8004) -> None:  # pragma: no cover
    """Run the time-warp host with uvicorn for manual runs (mirrors dashboard/portal/console).

    A standalone manual run builds its own ``InMemoryPortalStore``; a real shared-store dev
    deployment wires ONE store into the portal, dashboard, and time-warp hosts (§2b).
    """
    import uvicorn

    uvicorn.run(build_timewarp_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    serve()
