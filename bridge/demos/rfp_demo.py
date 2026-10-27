"""Local RFP demo host (S0-frontend-1) — one shared board behind the dashboard + the portal.

No live path opens an RFP onto a board yet (the broker's agent sends the ``CollectRequest``;
tests and fixtures call ``open_rfp``), so this small host seeds one synthetic RFP and serves
both views off ONE shared ``RfpBoard`` (+ one shared ``PortalStore``):

* ``/ops``    — ``build_dashboard_app(rfps=board)``: ``/exchanges`` + SSE (the RFP matrix),
  the consumer read API ``/rfps`` / ``/rfps/{id}`` and ``POST /rfps/{id}/chase``;
* ``/portal`` — ``build_portal_app(rfps=board)``: the carrier checklist
  ``/rfp/legs/{context_id}`` + its upload-into-a-row.

The seed runs the **real** path (``bridge.portal.rfp_checklist.upload_quote`` over the
fixture engine reading the benefits conformance corpus, read-only): RFP ``acme-vb-2027``,
carriers ``carrier-a`` / ``carrier-b`` / ``carrier-c`` x ``CRITICAL_ILLNESS`` /
``HOSPITAL_INDEMNITY``, due one day after ``now``:

* carrier-a CRITICAL_ILLNESS ← ``benefits-conformance-ci-usd`` (received);
* carrier-a HOSPITAL_INDEMNITY ← ``benefits-conformance-hi-usd-flagged`` (incomplete, the
  gate's ``flagged_fields`` with the ``fields.tables.rates[1].frequency`` field review);
* carrier-b acknowledged; carrier-c invited.

Synthetic data only. The board and the seeded ids ride on ``app.state`` (``rfps``,
``rfp_id``) so a driver can move the board clock (``app.state.rfps.clock``).

Run recipe (mounted sub-apps keep their own CORS middleware)::

    uv run uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010
    VITE_DASHBOARD_URL=http://localhost:8010/ops pnpm --filter ops-dashboard dev
    VITE_PORTAL_URL=http://localhost:8010/portal \\
        pnpm --filter provider-portal dev -- --port 5174

Set ``BRIDGE_EMAIL_MAILBOX`` + ``BRIDGE_EMAIL_TOKEN_SECRET`` to show each leg's reply
address. Add a landing directory (``BRIDGE_EMAIL_DIR``) and this host also binds the
email processor onto the SAME board (S0-bug-3): ``/ops/email/drain`` +
``/ops/email/outcomes/{key}``, plus a periodic drain every
``BRIDGE_EMAIL_DRAIN_INTERVAL`` seconds (default 30; ``0`` = only on demand). The
processor reads attachments with the demo's fixture engine, so a carrier can mail the
conformance PDFs::

    export BRIDGE_EMAIL_DIR=$(mktemp -d) BRIDGE_EMAIL_MAILBOX=rfp-quotes@broker.example \
           BRIDGE_EMAIL_TOKEN_SECRET=dev-only BRIDGE_EMAIL_DRAIN_INTERVAL=0
    uv run uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010
    # mail a reply to a leg's reply_address (GET /ops/rfps/acme-vb-2027), save it as .eml
    uv run python -m bridge.email_ingress land reply.eml
    uv run python -m bridge.email_ingress drain --url http://127.0.0.1:8010/ops

The board persists through the ``exchange_store`` port (S0-arch-14): unset, the demo RFP
lives in memory and is re-seeded on every start; point ``BRIDGE_EXCHANGE_DB_URL`` at an
async sqlite file and the RFP (uploads, chases, the audit trail) survives a host restart —
the seed runs only when the store does not already hold ``acme-vb-2027``::

    export BRIDGE_EXCHANGE_DB_URL=sqlite+aiosqlite:////tmp/rfp-demo.db
    uv run uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010

Host glue for a local demo — not a deployed service. It lives in the fenced demo-host
package ``bridge.demos`` (S0-arch-15): it names its demo skill (``DEMO_PROCESS``), seeds
synthetic data and reads the ``wiki/evals/`` corpus; the core learns everything
domain-specific from the process skill (``bridge.rfp.load_rfp_policy``) and never imports
this module (``tests/test_isolation_guard.py``).

Isolation: imports ``bridge.*`` + ``contract.*`` + ``fastapi`` + stdlib only; NEVER
``agents/``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from bridge.dashboard.board import RfpBoard
from bridge.dashboard.server import build_dashboard_app
from bridge.exchange_store import ExchangeStore, exchange_store_from_env
from bridge.extraction.fixture import FixtureExtractor
from bridge.portal.rfp_checklist import upload_quote
from bridge.portal.server import build_portal_app
from bridge.portal.store import InMemoryPortalStore, PortalStore
from bridge.rfp import acknowledge, load_rfp_policy, open_rfp
from contract.enums import RequirementStatus
from contract.wire import CollectRequest, Requirement, RequirementsList

if TYPE_CHECKING:
    from bridge.edge.email_ingress import EmailProcessor
    from bridge.email_ingress.correlation import IntakeAddressing
    from bridge.outbound import A2AClient

CONFORMANCE_CORPUS = (
    Path(__file__).resolve().parents[2] / "wiki/evals/benefits/conformance.json"
)
# The demo's emergent-Collect process skill — named HERE, never in the core.
DEMO_PROCESS = "benefits-rfp"
DEMO_RFP_ID = "acme-vb-2027"
DEMO_CARRIERS = ("carrier-a", "carrier-b", "carrier-c")
DEMO_LINES = ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY")


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a seed coroutine to completion from sync code, inside or outside a running
    loop (``uvicorn --factory`` builds the app on its loop; a test builds it off-loop)."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _seed(board: RfpBoard, extraction: Any, *, now: datetime) -> None:
    policy = board.policy
    request = CollectRequest(
        process=policy.process,
        party="acme-broker",
        context_id=f"{DEMO_RFP_ID}-broker",
        due_at=now + timedelta(days=1),
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item=line,
                    party=party,
                    status=RequirementStatus.required,
                    doctype_hint=policy.doctype_hint,
                )
                for party in DEMO_CARRIERS
                for line in DEMO_LINES
            ]
        ),
    )
    board.put(open_rfp(request, now=now - timedelta(days=1), rfp_id=DEMO_RFP_ID))
    ctx_a = board.rfps[DEMO_RFP_ID].leg_for("carrier-a").context_id
    for doc_id, artifact, line in (
        ("demo-a-ci-1", "benefits-conformance-ci-usd", "CRITICAL_ILLNESS"),
        ("demo-a-hi-1", "benefits-conformance-hi-usd-flagged", "HOSPITAL_INDEMNITY"),
    ):
        run_sync(
            upload_quote(
                board,
                ctx_a,
                doc_id=doc_id,
                artifact_name=artifact,
                product_line=line,
                extraction=extraction,
                intake=None,
            )
        )
    board.put(acknowledge(board.rfps[DEMO_RFP_ID], "carrier-b", now=now))


def seeded_rfp_board(
    *, clock: Callable[[], datetime], exchange_store: ExchangeStore | None
) -> tuple[RfpBoard, FixtureExtractor]:
    """The demo's ``RfpBoard`` (``DEMO_PROCESS``) with ``DEMO_RFP_ID`` seeded, plus the
    conformance fixture engine the seed (and the host's uploads / email) read with.

    ``clock`` is the board clock; ``exchange_store`` the record store under the board
    (``None`` → ``exchange_store_from_env()``). Shared by every demo host that serves the
    seeded RFP (this module, ``bridge.demos.showcase``).
    """
    board = RfpBoard(
        policy=load_rfp_policy(DEMO_PROCESS),
        clock=clock,
        store=exchange_store
        if exchange_store is not None
        else exchange_store_from_env(),
    )
    extraction = FixtureExtractor(corpus_path=CONFORMANCE_CORPUS)
    # Seed only when absent: over a durable store a restart must keep (not re-open) the
    # RFP — re-opening would be a create-only put on an existing record.
    if DEMO_RFP_ID not in board.rfps:
        _seed(board, extraction, now=clock())
    return board, extraction


def build_rfp_demo_app(
    *,
    now: datetime | None = None,
    store: PortalStore | None = None,
    exchange_store: ExchangeStore | None = None,
    intake: IntakeAddressing | None = None,
    outbound: A2AClient | None = None,
    email: EmailProcessor | None = None,
    email_drain_interval: float | None = None,
    max_ticks: int = 2000,
) -> FastAPI:
    """The local RFP demo host: ``/ops`` (dashboard) + ``/portal`` over one seeded board.

    ``now`` (tz-aware) pins the board clock (tests, a reproducible demo); ``None`` uses the
    wall clock. ``store`` is the shared PortalStore both hosts read; ``exchange_store`` is
    the record store under the RFP board (``None`` → ``exchange_store_from_env()``; the demo
    RFP is seeded only when that store does not already hold it); ``intake`` /
    ``outbound`` pass through to the dashboard (and ``intake`` to the portal);
    ``max_ticks`` bounds the dashboard SSE stream.

    ``email`` is the email processor draining onto the seeded board (``None`` →
    ``email_processor_from_env(board, extraction=<the demo fixture engine>)``, itself
    ``None`` when email intake / a landing location is not configured);
    ``email_drain_interval`` overrides ``BRIDGE_EMAIL_DRAIN_INTERVAL``. The periodic
    drain runs in THIS app's lifespan — the mounted dashboard's own never runs.
    """
    from bridge.edge.email_ingress import email_drain_loop, email_processor_from_env

    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError(f"now must be timezone-aware; got naive {now.isoformat()}")
    clock = (lambda: now) if now is not None else (lambda: datetime.now(UTC))
    board, extraction = seeded_rfp_board(clock=clock, exchange_store=exchange_store)

    processor = (
        email
        if email is not None
        else email_processor_from_env(board, extraction=extraction)
    )

    shared_store: PortalStore = store or InMemoryPortalStore()
    dashboard = build_dashboard_app(
        store=shared_store,
        rfps=board,
        intake=intake,
        outbound=outbound,
        email=processor,
        email_drain_interval=email_drain_interval,
        max_ticks=max_ticks,
    )
    # The dashboard resolved + validated the interval once; the loop runs here because
    # Starlette never runs a mounted sub-app's lifespan (exactly one loop per process).
    interval: float = dashboard.state.email_drain_interval
    lifespan = None
    if processor is not None and interval > 0:

        def lifespan(_app: FastAPI) -> Any:
            return email_drain_loop(processor, interval)

    app = FastAPI(
        title="RFP demo host (Ops Dashboard + Provider Portal, one board)",
        lifespan=lifespan,
    )
    app.mount("/ops", dashboard)
    # The portal shows the same reply addressing the processor correlates on.
    portal_intake = intake
    if portal_intake is None and processor is not None:
        portal_intake = processor.intake
    app.mount(
        "/portal",
        build_portal_app(
            store=shared_store, rfps=board, intake=portal_intake, extraction=extraction
        ),
    )
    app.state.rfps = board
    app.state.rfp_id = DEMO_RFP_ID
    app.state.email_processor = processor
    return app
