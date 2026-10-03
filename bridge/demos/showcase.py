"""The showcase runner (S0-arch-20) — one core with Address + Benefits + RFP loaded.

One process, one composed ASGI app, one Agent Card, one dashboard. The demo suite asks
for a single launch that boots the core with every demo skill loaded and exercises them
concurrently over shared state (``spec/demos/demo-suite.md``, ``spec/demos/rfp-demo.md``).
This host composes the existing builders and injects ONE set of shared sources into all
of them:

* ``/ops``    — ``build_dashboard_app(store=, board=, rfps=)``: ``/exchanges`` (Address
  portal sessions + RFPs) + SSE, ``/queues/*``, ``/programs`` (the Benefits program
  comparison) and the RFP read API ``/rfps`` / ``/rfps/{id}``;
* ``/portal`` — ``build_portal_app(store=, rfps=)``: the Path-B Address sessions and the
  carrier RFP checklist;
* ``/``       — ``build_a2a_app(rfp_board=)``: the A2A edge. The card stays at
  ``/.well-known/agent-card.json`` and JSON-RPC at ``/``. The card lists one skill per
  process skill in the registry (``address-proof``, ``benefits-procurement``,
  ``benefits-rfp``).

The edge is mounted LAST (``Mount("")`` matches every path), so ``/ops`` and ``/portal``
win their prefixes.

Shared-store wiring (built once, injected everywhere):

* one ``PortalStore`` → the dashboard and the portal;
* one ``RfpBoard`` (``benefits-rfp``, over ``exchange_store_from_env()`` unless injected)
  → the edge, the dashboard and the portal. A ``CollectRequest(process="benefits-rfp")``
  sent over A2A therefore opens onto the SAME board ``/ops/rfps`` reads;
* one ``ProgramBoard`` (``benefits-procurement`` standard) → the dashboard.

The edge's task store stays seam-resolved (in-memory, or durable with
``BRIDGE_TASK_DB_URL``).

Seeds (synthetic, deterministic):

* Address: two portal sessions. ``bill-powerco-clean`` is accepted (``done``);
  ``bill-aquautil-clear`` is parked for HITL review (``in_flight``, on the HITL queue).
  Both read the Address fixture corpus.
* Benefits: program ``acme-2026`` fanned out to four carriers, v0 quotes normalized from
  the benefits corpus, negotiation opened. carrier-b routes to ``negotiate`` (the
  ``no_orthodontics_exclusion`` gap).
* RFP: ``acme-vb-2027`` via ``bridge.demos.rfp_demo.seeded_rfp_board`` (seeded only when
  the exchange store does not already hold it).

Run recipe (``scripts/run-showcase.sh`` does all of this; ``docs/local-dev.md``)::

    BRIDGE_PUBLIC_URL=http://127.0.0.1:8011/ \\
        uv run uvicorn --factory bridge.demos.showcase:build_showcase_app --port 8011
    VITE_DASHBOARD_URL=http://localhost:8011/ops \\
        pnpm --filter ops-dashboard dev -- --port 5175 --strictPort
    VITE_PORTAL_URL=http://localhost:8011/portal \\
        pnpm --filter provider-portal dev -- --port 5173 --strictPort

Email intake mirrors ``bridge.demos.rfp_demo``: with ``BRIDGE_EMAIL_*`` set, the processor
drains onto the shared RFP board, and the periodic drain runs in THIS app's lifespan.

Known residuals:

* ``/exchanges`` joins portal sessions and RFPs, not programs. Programs are on
  ``/programs`` of the same host.
* No A2A path lands a Benefits program on a ``ProgramBoard``; this host seeds it. The
  ``ProgramBoard`` is in-memory.
* Three backing stores (PortalStore, ExchangeStore, ProgramBoard), each shared by every
  surface in this process. They are not one physical store.
* Address exchanges driven over A2A land in the edge task store, not on ``/exchanges``
  (the dashboard projects Path-B portal sessions).
* The live use-case-add variant (CUJ-9's Driver) is not part of this host.

Host glue for a local demo — not a deployed service. It lives in the fenced demo-host
package ``bridge.demos`` (S0-arch-15): it names its demo skills, seeds synthetic data and
reads the ``wiki/evals/`` corpus; the core never imports this module
(``tests/test_isolation_guard.py``).

Isolation: imports ``bridge.*`` + ``contract.*`` + ``starlette`` + stdlib only; NEVER
``agents/``. ``bridge.edge`` is imported lazily inside the builder (the fresh-process
import order stays acyclic).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.routing import Mount

from bridge.dashboard.board import ProgramBoard
from bridge.dashboard.server import build_dashboard_app
from bridge.demos.rfp_demo import DEMO_RFP_ID, run_sync, seeded_rfp_board
from bridge.extraction.fixture import FixtureExtractor
from bridge.negotiate import open_negotiation
from bridge.normalize import normalize_from_extraction
from bridge.portal import session as portal_session
from bridge.portal.server import build_portal_app
from bridge.portal.store import InMemoryPortalStore, PortalStore
from bridge.program import fan_out
from bridge.standard import load_program_standard

if TYPE_CHECKING:
    from bridge.edge.email_ingress import EmailProcessor
    from bridge.edge.executor import CollectExecutor
    from bridge.exchange_store import ExchangeStore

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[2] / "wiki/evals/benefits/expected.json"
)
# The demo process skills — named HERE, never in the core.
ADDRESS_PROCESS = "address-proof"
BENEFITS_PROCESS = "benefits-procurement"
SHOWCASE_PROGRAM_ID = "acme-2026"
# carrier → its v0 quote in the benefits corpus (wiki/evals/benefits/expected.json).
SHOWCASE_CARRIER_DOCS = {
    "carrier-a": "benefits-carrier-a-usd-en",
    "carrier-b": "benefits-carrier-b-usd-en",
    "carrier-c": "benefits-carrier-c-eur-fr",
    "carrier-d": "benefits-carrier-d-gbp-de",
}
# Address corpus artifacts (wiki/evals/address/expected.json): an accept, a HITL park.
ADDRESS_ACCEPT_ARTIFACT = "bill-powerco-clean"
ADDRESS_HITL_ARTIFACT = "bill-aquautil-clear"


async def _seed_address(store: PortalStore) -> tuple[str, ...]:
    """Two Path-B portal sessions: one accepted (done), one parked for HITL review."""
    extractor = FixtureExtractor()  # the Address corpus — never the ambient engine
    session_ids: list[str] = []
    for artifact in (ADDRESS_ACCEPT_ARTIFACT, ADDRESS_HITL_ARTIFACT):
        rec = portal_session.start_session(store, process=ADDRESS_PROCESS)
        await portal_session.upload(
            store,
            rec.session_id,
            doc_id="d1",
            artifact_name=artifact,
            extractor=extractor,
        )
        session_ids.append(rec.session_id)
    return tuple(session_ids)


async def _seed_program(board: ProgramBoard) -> None:
    """Fan out the four-carrier program, normalize the v0 quotes, open the negotiation."""
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    program = fan_out(SHOWCASE_PROGRAM_ID, list(SHOWCASE_CARRIER_DOCS))
    legs = [
        (
            leg,
            normalize_from_extraction(
                leg.party, await extractor.extract(SHOWCASE_CARRIER_DOCS[leg.party])
            ),
        )
        for leg in program.legs
    ]
    board.put(open_negotiation(SHOWCASE_PROGRAM_ID, legs, board.standard))


def build_showcase_app(
    *,
    url: str | None = None,
    now: datetime | None = None,
    executor: CollectExecutor | None = None,
    store: PortalStore | None = None,
    exchange_store: ExchangeStore | None = None,
    email: EmailProcessor | None = None,
    email_drain_interval: float | None = None,
    max_ticks: int = 2000,
) -> Starlette:
    """The showcase host: the A2A edge at ``/``, ``/ops`` and ``/portal`` over shared
    stores, with the Address, Benefits and RFP demos seeded.

    ``url`` is the edge's card url / executor base url (``None`` → ``build_a2a_app``'s
    default, which honors ``BRIDGE_PUBLIC_URL``). ``now`` (tz-aware) pins the RFP board
    clock; ``None`` uses the wall clock (an RFP opened over A2A must be due after it).
    ``executor`` passes through to the edge. ``store`` is the shared PortalStore;
    ``exchange_store`` the record store under the RFP board (``None`` →
    ``exchange_store_from_env()``). ``email`` / ``email_drain_interval`` mirror
    ``build_rfp_demo_app``; the periodic drain runs in THIS app's lifespan (Starlette runs
    no mounted sub-app's lifespan). ``max_ticks`` bounds the dashboard SSE stream.
    """
    from bridge.edge.email_ingress import email_drain_loop, email_processor_from_env
    from bridge.edge.server import build_a2a_app

    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError(f"now must be timezone-aware; got naive {now.isoformat()}")
    clock = (lambda: now) if now is not None else (lambda: datetime.now(UTC))

    # The shared sources — built once, injected into every surface.
    portal_store: PortalStore = store or InMemoryPortalStore()
    rfps, extraction = seeded_rfp_board(clock=clock, exchange_store=exchange_store)
    programs = ProgramBoard(standard=load_program_standard(BENEFITS_PROCESS))
    address_session_ids = run_sync(_seed_address(portal_store))
    run_sync(_seed_program(programs))

    processor = (
        email
        if email is not None
        else email_processor_from_env(rfps, extraction=extraction)
    )
    dashboard = build_dashboard_app(
        store=portal_store,
        board=programs,
        rfps=rfps,
        email=processor,
        email_drain_interval=email_drain_interval,
        max_ticks=max_ticks,
    )
    portal = build_portal_app(
        store=portal_store,
        rfps=rfps,
        intake=processor.intake if processor is not None else None,
        extraction=extraction,
    )
    edge_kwargs = {} if url is None else {"url": url}
    edge = build_a2a_app(executor=executor, rfp_board=rfps, **edge_kwargs)

    # The dashboard resolved + validated the interval once; the loop runs here because
    # Starlette never runs a mounted sub-app's lifespan (exactly one loop per process).
    interval: float = dashboard.state.email_drain_interval
    lifespan = None
    if processor is not None and interval > 0:

        def lifespan(_app: Starlette):
            return email_drain_loop(processor, interval)

    app = Starlette(
        # Order matters: Mount("") matches everything, so the edge goes last.
        routes=[
            Mount("/ops", app=dashboard),
            Mount("/portal", app=portal),
            Mount("", app=edge),
        ],
        lifespan=lifespan,
    )
    app.state.rfps = rfps
    app.state.rfp_board = rfps
    app.state.rfp_id = DEMO_RFP_ID
    app.state.programs = programs
    app.state.program_id = SHOWCASE_PROGRAM_ID
    app.state.portal_store = portal_store
    app.state.address_session_ids = address_session_ids
    app.state.email_processor = processor
    return app
