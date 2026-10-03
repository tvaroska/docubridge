"""Address ``Workflow`` wiring + routing + ceiling, plus one live path-a pass.

Covers (P3-1):

- **Construction/validation** — the exact four-edge conditional cycle constructs
  without a graph-validation error and carries the expected routes.
- **Routing + round ceiling (no network)** — a stub collect ``FunctionNode`` that
  emits a *non-satisfying* turn each run drives the ``again`` loop until the round
  ceiling forces ``done`` and the run terminates at ``present``. A *satisfying*
  stub ends in one cycle. This proves the loop-back re-runs the completed collect
  node and that the ceiling terminates a non-terminating scenario.
- **One live integration pass** — the real ``build_bridge_remote_agent`` resolves
  the in-process mock's card over an ``ASGITransport`` client and runs the
  ``path-a-instant`` scenario: the terminal gov-id ``ExchangeTurn`` reaches the
  ``present`` output, read off the shared session events (AGENT-7).

Multi-turn *live* park/resume (path-b / resubmit) is **not** exercised here — a
non-terminal turn parks the collect node as a ``LongRunningFunctionTool`` and
interrupts the invocation; resuming needs P3-2 (``RequestInterceptor``) + P4
(durable session). Those golden runs land in P3-5.
"""
from __future__ import annotations

import json

import httpx
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import START, FunctionNode, Workflow
from google.genai import types

from agents.address.wire import extract_exchange_turn
from agents.address.workflow import build_address_workflow
from bridge.edge import build_mock_bridge_app
from contract.enums import Disposition, Gate
from contract.wire import CollectionStatus, ExchangeTurn, LedgerEntry

# The A2A client requires a loopback host for http (non-loopback http is rejected),
# so the in-process mock is served under a loopback base URL.
BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH

# The A2A→genai converter wraps a generic data part's JSON in these tags as an
# inline text/plain blob (verified live). The stub mimics that persisted shape so
# the interim reader's primary (session-events) path is exercised without a network.
_TAG_START = "<a2a_datapart_json>"
_TAG_END = "</a2a_datapart_json>"


def _turn_content(turn: ExchangeTurn) -> types.Content:
    blob = (_TAG_START + json.dumps(turn.model_dump(mode="json")) + _TAG_END).encode()
    return types.Content(
        role="model",
        parts=[types.Part(inline_data=types.Blob(data=blob, mime_type="text/plain"))],
    )


def _entry(doc_id, doctype, *, disposition, issuer=None, gate=Gate.auto_approve):
    return LedgerEntry(
        doc_id=doc_id,
        doctype=doctype,
        disposition=disposition,
        gate=gate,
        issuer=issuer,
    )


def _stub_collect(turn: ExchangeTurn) -> FunctionNode:
    """A collect ``FunctionNode`` that emits ``turn`` as a session-events content."""

    def collect(ctx):
        return _turn_content(turn)

    return FunctionNode(func=collect, name="collect")


async def _run(wf: Workflow):
    """Run a workflow via ``Runner(node=...)`` with a seed user message.

    Returns ``(final_output, session)``.
    """
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="address", user_id="u", session_id="s"
    )
    runner = Runner(
        node=wf,
        session_service=session_service,
        app_name="address",
        artifact_service=InMemoryArtifactService(),
    )
    # Text carries BOTH the process token (real edge resolves it) and the scenario
    # marker (mock keys off it) — parity design; parse_text_request no longer defaults
    # to a sole process skill now that benefits-procurement is also loaded.
    seed = types.Content(
        role="user",
        parts=[types.Part(text="address-proof scenario:path-a-instant")],
    )
    final_output = None
    async for event in runner.run_async(
        user_id="u", session_id="s", new_message=seed
    ):
        if getattr(event, "output", None) is not None:
            final_output = event.output
    session = await session_service.get_session(
        app_name="address", user_id="u", session_id="s"
    )
    return final_output, session


# --------------------------------------------------------------------------- #
# Construction / validation                                                    #
# --------------------------------------------------------------------------- #
def test_build_address_workflow_has_four_edge_conditional_cycle():
    wf = build_address_workflow(CARD_URL)
    assert isinstance(wf, Workflow)
    assert wf.name == "address"

    def name_of(node):
        return getattr(node, "name", node)

    edges = {
        (name_of(e.from_node), name_of(e.to_node)): e.route for e in wf.edges
    }
    start = name_of(START)
    assert edges[(start, "collect")] is None
    assert edges[("collect", "gate")] is None
    assert edges[("gate", "collect")] == "again"
    assert edges[("gate", "present")] == "done"
    assert len(wf.edges) == 4


# --------------------------------------------------------------------------- #
# Routing + round ceiling (stub collect, no network)                          #
# --------------------------------------------------------------------------- #
async def test_round_ceiling_forces_done_and_terminates_at_present():
    """A non-satisfying stub loops via ``again`` until the ceiling forces done."""
    non_satisfying = ExchangeTurn(
        context_id="ctx-loop",
        terminal=False,
        status=CollectionStatus(
            entries=[
                _entry(
                    "bill-1",
                    "utility-bill",
                    disposition=Disposition.accepted,
                    issuer="power-co",
                )
            ]
        ),
    )
    wf = build_address_workflow(
        CARD_URL, max_rounds=3, collect_node=_stub_collect(non_satisfying)
    )
    final_output, session = await _run(wf)

    # Ran exactly max_rounds collect/gate cycles, then the ceiling routed done.
    assert session.state.get("gate_rounds") == 3
    # Terminated at present with the (still non-satisfying) latest turn as output.
    assert final_output is not None
    assert final_output["status"]["entries"][0]["doc_id"] == "bill-1"


async def test_satisfying_turn_routes_done_in_one_cycle():
    satisfying = ExchangeTurn(
        context_id="ctx-fast",
        terminal=True,
        status=CollectionStatus(
            entries=[
                _entry("gov-id-clean", "gov-id", disposition=Disposition.accepted)
            ]
        ),
    )
    wf = build_address_workflow(
        CARD_URL, max_rounds=6, collect_node=_stub_collect(satisfying)
    )
    final_output, session = await _run(wf)

    assert session.state.get("gate_rounds") == 1
    assert final_output is not None
    assert final_output["status"]["entries"][0]["doc_id"] == "gov-id-clean"


# --------------------------------------------------------------------------- #
# One live integration pass (path-a-instant against the in-process mock)       #
# --------------------------------------------------------------------------- #
async def test_live_path_a_instant_reaches_present_with_terminal_turn():
    app = build_mock_bridge_app(url=BASE + "/")
    transport = httpx.ASGITransport(app=app)
    hc = httpx.AsyncClient(transport=transport, base_url=BASE)
    try:
        wf = build_address_workflow(CARD_URL, httpx_client=hc)
        final_output, session = await _run(wf)
    finally:
        await hc.aclose()

    # The gate ran once and routed done (no loop) on the instant-accept path.
    assert session.state.get("gate_rounds") == 1

    # The terminal gov-id ExchangeTurn reached the present output.
    assert final_output is not None
    turn = ExchangeTurn.model_validate(final_output)
    assert turn.terminal is True
    assert len(turn.status.entries) == 1
    entry = turn.status.entries[0]
    assert entry.doc_id == "gov-id-clean"
    assert entry.doctype == "gov-id"
    assert entry.disposition is Disposition.accepted
    assert entry.gate is Gate.auto_approve


# --------------------------------------------------------------------------- #
# Regression pin — last_chunk=True → persisted non-partial artifact (AGENT-7)   #
# --------------------------------------------------------------------------- #
async def test_collected_artifact_event_is_nonpartial_and_decoded_off_session():
    """Pin the ``last_chunk=True`` → non-partial → persisted invariant end to end.

    The Bridge emits the collected artifact ``last_chunk=True``, so
    ``RemoteA2aAgent`` relays it as a **non-partial** event that persists to the
    shared session. The gate's *primary* path (``ctx.session.events``, not the
    state fallback) must therefore find and decode the terminal turn off that
    persisted event.
    """
    app = build_mock_bridge_app(url=BASE + "/")
    transport = httpx.ASGITransport(app=app)
    hc = httpx.AsyncClient(transport=transport, base_url=BASE)
    try:
        wf = build_address_workflow(CARD_URL, httpx_client=hc)
        _final, session = await _run(wf)
    finally:
        await hc.aclose()

    # Find the persisted (non-partial) event whose content parts decode to the
    # collected ExchangeTurn — the terminal turn the gate reads.
    decoded = None
    saw_partial_flag = False
    for event in session.events:
        if getattr(event, "partial", None) is not None:
            saw_partial_flag = True
        if getattr(event, "partial", False):
            continue  # a partial (streamed-only) event is never persisted terminal
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        turn = extract_exchange_turn(parts)
        if turn is not None:
            decoded = turn

    assert saw_partial_flag, "expected at least one event to carry a partial flag"
    assert decoded is not None, "terminal turn was not decoded off a non-partial event"
    assert decoded.terminal is True
    assert decoded.status.entries[0].doctype == "gov-id"


# --------------------------------------------------------------------------- #
# Live park→resume (AGENT-6/5) — attempted; App-path workflow resume is P4.     #
# --------------------------------------------------------------------------- #
def _parked_function_call(session):
    """The most-recent function-call on the session (the parked LRFT input call)."""
    for event in reversed(session.events):
        calls = event.get_function_calls() or []
        if calls:
            return calls[0]
    return None


async def test_live_path_b_park_then_resume_same_task(tmp_path):
    """Turn 1 parks on INPUT_REQUIRED; the durable resume reaches the terminal
    two-issuer turn on the SAME task/context and routes the gate to ``present``.

    Graduated in P4-2: the resume runs on the durable App path with a **fresh
    ``Runner`` per turn over one durable ``DatabaseSessionService``** (the
    process-restart shape), driven by ``run_collection(durable=True)``. The old
    same-``Runner`` + ``InMemorySessionService`` construction replayed the parked
    step (that path never rebuilds the outbound intake); the durable fresh-Runner
    path surfaces the freshly-fetched terminal turn.
    """
    from agents.address.app import build_durable_session_service
    from agents.address.driver import run_collection

    dsn = f"sqlite+aiosqlite:///{tmp_path}/session.db"
    session_service = build_durable_session_service(db_url=dsn)

    app_mock = build_mock_bridge_app(url=BASE + "/")
    transport = httpx.ASGITransport(app=app_mock)
    hc = httpx.AsyncClient(transport=transport, base_url=BASE)
    try:
        turn = await run_collection(
            CARD_URL,
            scenario="path-b-two-bills",
            httpx_client=hc,
            durable=True,
            session_service=session_service,
            session_id="s",
            max_rounds=3,
        )
    finally:
        await hc.aclose()

    session = await session_service.get_session(
        app_name="address", user_id="address-driver", session_id="s"
    )

    # The gate runs once on the working path: turn 1 parks in the collect node
    # *before* the gate, so the gate first executes only on the resume turn and
    # immediately routes ``done`` (empirically 1, not the stale pre-P4 count of 2).
    assert session.state.get("gate_rounds") == 1

    # The peer resumed the SAME task: terminal turn with two distinct issuers.
    assert turn is not None
    assert turn.terminal is True
    issuers = {
        e.issuer for e in turn.status.entries if e.disposition is Disposition.accepted
    }
    assert issuers == {"power-co", "aqua-util"}
