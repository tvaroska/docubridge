"""Ops Dashboard ASGI host (P6-4) — the internal-zone Bridge-side read-model.

A standalone ``FastAPI`` host (Bridge-side, internal zone) that:
- reads a **shared, injectable PortalStore** (the P6-2 Path-B session store) as its live
  read-model source — the store the running Provider Portal writes to;
- projects each ``PortalSessionRecord`` into an operator ``OpsExchange`` view + derives
  the **HITL queue** (records with an active HITL park) and the **escalation queue**
  (ledger entries carrying ``REASON_ESCALATED``);
- serves the cross-exchange list, per-exchange ledger, the two queues, and a
  **cross-exchange SSE stream** (reusing the P6-3 diff-poll ``sse_frames`` pattern);
- offers a **resolve** control (``POST /exchanges/{sid}/resolve``) that calls the
  **existing** ``bridge.portal.session.resolve`` on the shared store — the operator
  "resolves the mid-confidence bill" by reusing the P6-2 engine, minting no disposition;
- serves the **consumer RFP read API** + the manual **chase** over an injected
  ``RfpBoard`` (S0-frontend-1, ``bridge.dashboard.rfp_api``): ``GET /rfps``,
  ``GET /rfps/{rfp_id}``, ``POST /rfps/{rfp_id}/chase``;
- serves the **email drain** onto that same board (``POST /email/drain``,
  ``GET /email/outcomes/{key}``) + a periodic drain when email intake is configured
  (S0-bug-3, ``bridge.edge.email_ingress``): the processor binds on the host that owns
  the RFP board, never on one no RFP opens onto;
- maps an RFP compare-and-set conflict (``StaleExchangeWrite``, e.g. a chase racing a
  write on another replica) to **409** — the client retries (S0-arch-17).

**The dashboard is Bridge-side (``bridge/dashboard/``), NOT agent-side.** It imports
``bridge.*`` + ``contract.*`` + ``fastapi`` + stdlib; NEVER ``agents/`` (isolation-guard
walks ``bridge/**`` and will catch a stray ``agents`` import).

Isolation: imports ``contract/`` + ``bridge.*`` + ``fastapi``/``starlette``/``pydantic`` +
stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from bridge.dashboard import readmodel
from bridge.dashboard.rfp_api import mount_rfp_routes
from bridge.email_ingress.correlation import IntakeAddressing
from bridge.exchange_store import StaleExchangeWrite
from bridge.portal import session as portal_session
from bridge.portal.session import NoActivePark
from bridge.portal.store import InMemoryPortalStore, PortalSessionRecord, PortalStore
from bridge.telemetry import OtelAsgiMiddleware
from bridge.user_interaction import build_user_interaction_router
from contract.enums import Disposition

if TYPE_CHECKING:
    from bridge.dashboard.board import ProgramBoard, RfpBoard
    from bridge.edge.email_ingress import EmailProcessor
    from bridge.knowledge_catalog import KnowledgeCatalog
    from bridge.operational_store import OperationalStore
    from bridge.outbound import A2AClient

# The Vite dev server origins. One per frontend app so ``scripts/run-all.sh`` can bring
# the surfaces up side by side (docs/local-dev.md); real per-party Gateway/Agent-Identity
# scoping is deploy-only (P7, ``design/zones.md``) — locally CORS just lets the dev UI in.
VITE_DEV_ORIGINS = [f"http://localhost:{port}" for port in range(5173, 5178)]


def _stale_exchange_write_409(_request: Request, exc: Exception) -> JSONResponse:
    """A compare-and-set conflict on an RFP (S0-arch-17): 409, the client retries."""
    ref = getattr(exc, "ref", None)
    return JSONResponse(
        {"detail": f"RFP {ref!r} changed concurrently; retry"}, status_code=409
    )


class ResolveBody(BaseModel):
    """POST /exchanges/{sid}/resolve request body (the reviewer's resolution)."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["accepted", "rejected"]
    message: str


def build_dashboard_app(
    *,
    store: PortalStore | None = None,
    board: ProgramBoard | None = None,
    rfps: RfpBoard | None = None,
    intake: IntakeAddressing | None = None,
    outbound: A2AClient | None = None,
    email: EmailProcessor | None = None,
    email_drain_interval: float | None = None,
    max_ticks: int = 2000,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
) -> FastAPI:
    """Build the Ops Dashboard FastAPI host.

    ``store`` is the **shared PortalStore** the running Provider Portal writes to. Defaults
    to an in-memory store for standalone manual runs; tests inject a fresh shared store per
    case. ``board`` is the optional **injectable program board** (S1-benefits-7): when set,
    the ``GET /programs`` routes serve the recomputed program comparisons and the SSE
    snapshot carries a ``programs`` key. ``rfps`` is the optional **injectable RFP board**
    (S0-arch-11): when set, RFP collections join ``/exchanges`` (+ ``/exchanges/{id}``, the
    escalation queue and the SSE snapshot) with their per-carrier x product matrix under an
    ``rfp`` key, and the consumer RFP API (``GET /rfps``, ``GET /rfps/{rfp_id}``,
    ``POST /rfps/{rfp_id}/chase``) serves them (S0-frontend-1). ``intake`` is the legs'
    reply addressing (``None`` → ``IntakeAddressing.from_env()``, itself ``None`` when email
    intake is not enabled); ``outbound`` is the chase client (``None`` → the
    ``a2a_client`` seam adapter, built lazily on the first chase). ``max_ticks`` bounds the
    SSE stream (testability; §2d).

    ``email`` is the inbound-email processor that drains landed mail onto ``rfps``
    (S0-bug-3). ``None`` → ``email_processor_from_env(rfps)``: bound only when
    ``BRIDGE_EMAIL_MAILBOX`` and a landing location (``BRIDGE_EMAIL_DIR`` locally /
    ``BRIDGE_EMAIL_BUCKET`` on gcp) are set, then ``BRIDGE_EMAIL_TOKEN_SECRET`` is
    required (``RuntimeError``). It drains onto ``rfps`` only: no ``rfps`` → no processor
    and no email routes (``email=`` without ``rfps=`` or with another board →
    ``ValueError``). When bound, the host serves ``POST /email/drain`` +
    ``GET /email/outcomes/{key}``, shows the processor's own reply addressing (unless
    ``intake`` is injected), and its lifespan drains every ``email_drain_interval``
    seconds (``None`` → ``BRIDGE_EMAIL_DRAIN_INTERVAL``, default 30; ``0`` → on demand
    only; malformed → ``ValueError``). A host that MOUNTS this app must run the loop in
    its own lifespan (Starlette runs no mounted sub-app's lifespan) —
    ``app.state.email_processor`` / ``app.state.email_drain_interval`` carry what it needs.
    """
    from bridge.edge.email_ingress import (
        drain_interval_from_env,
        email_drain_loop,
        email_processor_from_env,
        mount_email_drain_routes,
    )

    active_store: PortalStore = store or InMemoryPortalStore()
    processor: EmailProcessor | None = None
    if rfps is not None:
        processor = email if email is not None else email_processor_from_env(rfps)
        if processor is not None and processor.board is not rfps:
            raise ValueError(
                "build_dashboard_app(email=...) must drain onto this host's rfps board"
            )
    elif email is not None:
        raise ValueError("build_dashboard_app(email=...) requires rfps= (its board)")
    if intake is not None:
        reply_intake = intake
    elif processor is not None:
        reply_intake = processor.intake
    else:
        reply_intake = IntakeAddressing.from_env()
    interval = 0.0
    if processor is not None:
        interval = (
            email_drain_interval
            if email_drain_interval is not None
            else drain_interval_from_env()
        )
        if not math.isfinite(interval) or interval < 0:
            raise ValueError(
                f"email_drain_interval must be finite and >= 0; got {interval!r}"
            )

    lifespan = None
    if processor is not None and interval > 0:

        def lifespan(_app: FastAPI):
            return email_drain_loop(processor, interval)

    app = FastAPI(
        title="Ops Dashboard (Bridge-side read-model, SSE)", lifespan=lifespan
    )
    app.add_exception_handler(StaleExchangeWrite, _stale_exchange_write_409)
    app.add_middleware(OtelAsgiMiddleware, service_component="dashboard")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=VITE_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _load(sid: str) -> PortalSessionRecord:
        try:
            return active_store.get(sid)
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown session {sid!r}"
            ) from e

    @app.get("/exchanges")
    def get_exchanges() -> list[dict]:
        # Newest-first for the UI (store keeps insertion order, so reverse).
        return list(reversed(readmodel.list_exchanges(active_store, rfps=rfps)))

    @app.get("/exchanges/{sid}")
    def get_exchange(sid: str) -> dict:
        if rfps is not None and sid in rfps.rfps:
            rfp = rfps.rfps[sid]
            return readmodel.project_rfp_exchange(rfp, rfps.get(sid))
        rec = _load(sid)  # 404 for an unknown session
        return readmodel.project_exchange(rec)

    @app.get("/queues/hitl")
    def get_hitl_queue() -> list[dict]:
        return readmodel.hitl_queue(active_store, rfps=rfps)

    @app.get("/queues/escalation")
    def get_escalation_queue() -> list[dict]:
        return readmodel.escalation_queue(active_store, rfps=rfps)

    @app.post("/exchanges/{sid}/resolve")
    def post_resolve(sid: str, body: ResolveBody) -> dict:
        _load(sid)  # 404 for an unknown session before touching the graph
        try:
            rec = portal_session.resolve(
                active_store,
                sid,
                resolution=Disposition(body.resolution),
                message=body.message,
            )
        except NoActivePark as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return readmodel.project_exchange(rec)

    @app.get("/programs")
    def get_programs() -> list[dict]:
        # Recompute-on-read (S1-benefits-7): a landed revision surfaces here immediately.
        if board is None:
            return []
        return [readmodel.project_comparison(c) for c in board.comparisons()]

    @app.get("/programs/{program_id}")
    def get_program(program_id: str) -> dict:
        if board is None:
            raise HTTPException(status_code=404, detail="no program board configured")
        try:
            comparison = board.get(program_id)
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown program {program_id!r}"
            ) from e
        return readmodel.project_comparison(comparison)

    mount_rfp_routes(app, rfps=rfps, intake=reply_intake, outbound=outbound)
    if processor is not None:
        mount_email_drain_routes(app, processor)
    app.include_router(
        build_user_interaction_router(
            operational_store=operational_store,
            knowledge_catalog=knowledge_catalog,
        )
    )
    app.state.email_processor = processor
    app.state.email_drain_interval = interval

    @app.get("/events")
    def stream_events() -> StreamingResponse:
        # Cross-exchange stream is always valid (no 404).
        return StreamingResponse(
            readmodel.sse_frames(
                active_store, board=board, rfps=rfps, max_ticks=max_ticks
            ),
            media_type="text/event-stream",
        )

    return app


def serve(host: str = "127.0.0.1", port: int = 8003) -> None:  # pragma: no cover
    """Run the dashboard host with uvicorn for manual runs (mirrors portal/edge/console).

    A standalone manual run builds its own ``InMemoryPortalStore``; a real shared-store dev
    deployment wires one store into both the portal and the dashboard (§2a).
    """
    import uvicorn

    uvicorn.run(build_dashboard_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    serve()
