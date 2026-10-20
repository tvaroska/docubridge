"""Deployed RFP board host (S0-arch-17) — the internal-zone service that owns the RFP board.

The A2A runtime (``bridge.edge.server.serve_container``) holds at most an opener-only RFP
board (S0-arch-19: it opens RFPs onto the shared store; no processor, no read API), so
nothing there drains the email landing bucket. This host does: ONE ``RfpBoard`` over the
durable shared ``exchange_store`` (Cloud SQL ``bridge_exchange_records`` on gcp), the Ops
Dashboard app mounted at the root over it — the consumer RFP read API (``GET /rfps``,
``GET /rfps/{rfp_id}``, ``POST /rfps/{rfp_id}/chase``) and the email drain
(``POST /email/drain``, ``GET /email/outcomes/{key}``) — and the processor bound from env.

* **Internal zone, its own service.** The outcome markers carry sender / subject / RFP /
  carrier and the read API is operator-facing (``design/zones.md``,
  ``design/rfp-views.md``), so it is NOT mounted on the Gateway-public A2A edge. Deployed
  as ``<service_name>-rfp-board`` on the same image, running as ``bridge-runtime``
  (internal-only ingress, ``roles/run.invoker`` for that SA alone).
* **Scheduler-driven drain.** Cloud Scheduler POSTs ``/email/drain`` every minute with an
  OIDC token; deployed sets ``BRIDGE_EMAIL_DRAIN_INTERVAL=0`` (request-based Cloud Run CPU
  starves a background loop between requests). Locally a non-zero interval runs the
  poll loop in THIS app's lifespan (Starlette never runs a mounted sub-app's lifespan —
  exactly one loop per process).
* **Binding is safe because the board is the source of truth** (S0-bug-3 #2, refined):
  this board is a read-through view of the durable record every RFP writer uses, so a
  mail that correlates to nothing here correlates to nothing anywhere. Hence the
  refusal: a processor never binds over an ``InMemoryExchangeStore`` (a per-process
  board no RFP opens onto would permanently quarantine the queue as
  ``no_exchange_token``). No email env → no processor → in-memory is allowed (read API).
* **The home process skill comes from env** (``BRIDGE_RFP_PROCESS``; S0-arch-15: the core
  names no skill), loaded once at build. The board is live (S4-usecase-4): it also serves
  every emergent-Collect process installed in the skill registry later
  (``bridge.edge.skills.rfp_policy_for``, read through the ``skill_registry`` seam per
  RFP, per read), so a live-added use case's RFPs list, checklist and file by email with
  no restart. Each RFP is projected with its OWN process's policy.
* **Cross-replica safety** lives in the processor (CAS retry + re-file guard,
  ``bridge.edge.email_ingress``); an exhausted conflict is a 409 (the dashboard's
  ``StaleExchangeWrite`` handler) and the next Scheduler tick retries.
* **Shutdown closes the store** (``ExchangeStore.close``, idempotent).

Isolation: imports ``bridge.*`` + ``contract.*`` + ``fastapi`` + stdlib; NEVER
``agents/``, never ``bridge.demos``. ``bridge.edge.email_ingress`` and
``bridge.edge.skills`` are imported lazily inside the builder (the S0-bug-3 #7
circular-import trap).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from bridge.dashboard.board import RfpBoard
from bridge.dashboard.server import build_dashboard_app
from bridge.exchange_store import (
    ExchangeStore,
    InMemoryExchangeStore,
    exchange_store_from_env,
)
from bridge.rfp import RFP_PROCESS_ENV, load_rfp_policy
from bridge.telemetry import OtelAsgiMiddleware

if TYPE_CHECKING:
    from bridge.outbound import A2AClient


def build_rfp_board_app(
    *,
    process: str | None = None,
    exchange_store: ExchangeStore | None = None,
    clock: Callable[[], datetime] | None = None,
    extraction: Any = None,
    email_drain_interval: float | None = None,
    outbound: A2AClient | None = None,
    max_ticks: int = 2000,
) -> FastAPI:
    """Build the RFP board host: the dashboard app at ``/`` over one durable board.

    ``process`` names the board's HOME emergent-Collect process skill (``None`` →
    ``BRIDGE_RFP_PROCESS``; blank → ``RuntimeError``); a skill that is not one fails at
    build (``load_rfp_policy``'s ``ValueError``). The board is live
    (``live_policy=rfp_policy_for``): emergent-Collect processes installed in the skill
    registry after build are served too, each RFP with its own process's policy. ``exchange_store`` is the record store
    under the board (``None`` → ``exchange_store_from_env()``: local
    ``BRIDGE_EXCHANGE_DB_URL``, gcp ``DATABASE_URL``). ``clock`` pins the board clock.

    The email processor is built from env onto THIS board
    (``email_processor_from_env(board, extraction=extraction)``; ``None`` when email
    intake / a landing location is not configured — ``extraction`` injects the engine,
    default the extraction seam). A bound processor over an in-memory store →
    ``RuntimeError``. ``email_drain_interval`` overrides
    ``BRIDGE_EMAIL_DRAIN_INTERVAL`` (deployed: ``0``, Scheduler-driven); ``outbound`` is
    the chase client; ``max_ticks`` bounds the dashboard SSE stream. The host seeds
    nothing: it serves whatever RFPs the shared store holds.
    """
    from bridge.edge.email_ingress import email_drain_loop, email_processor_from_env
    from bridge.edge.skills import rfp_policy_for

    process = (process or os.environ.get(RFP_PROCESS_ENV, "")).strip()
    if not process:
        raise RuntimeError(
            f"the RFP board host requires {RFP_PROCESS_ENV} "
            "(the emergent-Collect process skill)"
        )
    board = RfpBoard(
        policy=load_rfp_policy(process),
        clock=clock,
        store=exchange_store or exchange_store_from_env(),
        live_policy=rfp_policy_for,
    )
    processor = email_processor_from_env(board, extraction=extraction)
    if processor is not None and isinstance(board.store, InMemoryExchangeStore):
        raise RuntimeError(
            "the RFP board host binds the email processor only over the durable shared "
            "exchange store (set BRIDGE_EXCHANGE_DB_URL locally / DATABASE_URL on gcp): "
            "over an in-memory board no RFP opens onto, every landed mail would be "
            "quarantined no_exchange_token"
        )

    dashboard = build_dashboard_app(
        rfps=board,
        email=processor,
        email_drain_interval=email_drain_interval,
        outbound=outbound,
        max_ticks=max_ticks,
    )
    # The dashboard resolved + validated the interval once; the loop runs HERE because
    # Starlette never runs a mounted sub-app's lifespan (exactly one loop per process).
    interval: float = dashboard.state.email_drain_interval

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            if processor is not None and interval > 0:
                async with email_drain_loop(processor, interval):
                    yield
            else:
                yield
        finally:
            await asyncio.to_thread(board.store.close)

    app = FastAPI(
        title="RFP board host (internal zone): RFP read API + email drain",
        lifespan=lifespan,
    )
    app.add_middleware(OtelAsgiMiddleware, service_component="rfp_board")
    app.mount("", dashboard)  # routes stay at /rfps, /email/drain, /exchanges, …
    app.state.rfps = board
    app.state.email_processor = processor
    return app


def serve_rfp_board_container() -> None:  # pragma: no cover
    """Cloud Run entrypoint for the internal RFP board service (S0-arch-17).

    Everything from env: the process skill (``BRIDGE_RFP_PROCESS``), the durable store
    (``BRIDGE_SEAM_MODE=gcp`` → Cloud SQL via ``DATABASE_URL``), the processor
    (``BRIDGE_EMAIL_MAILBOX`` + ``BRIDGE_EMAIL_BUCKET`` + ``BRIDGE_EMAIL_TOKEN_SECRET``)
    and ``BRIDGE_EMAIL_DRAIN_INTERVAL=0`` (Cloud Scheduler drives ``POST /email/drain``).
    Binds ``0.0.0.0:$PORT`` like ``bridge.edge.server.serve_container``.
    """
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    # 0.0.0.0 is required by Cloud Run; the service is internal-only ingress and
    # Cloud Run IAM (run.invoker for bridge-runtime alone) gates every request.
    uvicorn.run(build_rfp_board_app(), host="0.0.0.0", port=port)


__all__ = ["RFP_PROCESS_ENV", "build_rfp_board_app", "serve_rfp_board_container"]
