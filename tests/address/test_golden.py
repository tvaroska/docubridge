"""TEST-3 golden runs — the P3 exit gate ("the demo's heart").

Drives the Address consumer loop (``run_collection`` → the collect→gate→present
``Workflow``) to its **terminal outcome** against **both** the mock Bridge
(``build_mock_bridge_app``) and the **real edge** (``build_a2a_app`` over the
fixture extractor), proving the mock→real swap is *only a card-URL change*.

**The mock→real asymmetry (the whole shape of P3-5).** The two edges consume
different inputs and have different turn cardinality:

- the **mock** is script-driven — it keys off the ``scenario:<name>`` marker in the
  message text and ignores submissions (multi-turn: it can park then resume);
- the **real edge** extracts **documents** off the message (``decode_submissions``,
  fixture-extracted) and ignores the ``scenario:`` marker — and is **single-turn**
  (the P1-4 ``Collector`` always returns ``terminal=True``).

So one opening message carries BOTH the marker *and* the by-reference document
parts (see ``agents.address.driver._seed_content`` /
``agents.address.wire.encode_doc_parts``). Each edge reads what it understands and
ignores the rest → the **same terminal ledger outcome** on both, for the runs that
terminate on pre-P4 machinery.

**Path taken for the real-edge document path (§4.1 of the plan):** the
**through-driver** path. A genai ``file_data`` ``Part`` carrying ``doc_id`` in
``part_metadata`` survives ``RemoteA2aAgent``'s genai→a2a conversion
(``convert_genai_part_to_a2a_part`` maps it to an a2a url part and copies the
metadata), so the edge's ``decode_submissions`` reads it back with the right
``doc_id``. Verified empirically before these assertions were written; no
ADK-internal changes or direct-A2A-client fallback were needed.

**Single-turn vs multi-turn.** Single-turn runs terminate on both edges via the
plain ``Runner(node=...)`` path. Live multi-turn park/resume (the mock's
``path-b-two-bills`` / ``resubmit``) terminates as of **P4-2**: the graduated
mock-side runs drive ``run_collection(durable=True)``'s park→resume loop (App path +
durable ``DatabaseSessionService``), resuming a parked ``LongRunningFunctionTool``
call on a fresh ``Runner`` over the same DB to surface the fresh terminal turn.
The Gemini extraction adapter (TEST-3's other half) is P7; this suite runs the real
edge on the **fixture** extractor only.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.sessions import InMemorySessionService

from agents.address.app import build_durable_session_service
from agents.address.driver import run_collection
from agents.address.gate import (
    DEFAULT_MAX_ROUNDS,
    STATE_GATE_ROUNDS,
    is_satisfied,
)
from agents.address.wire import DocRef
from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, build_mock_bridge_app
from bridge.edge.llm_leg import LlmLeg
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import REASON_ESCALATED
from contract.enums import Disposition, Gate, LifecycleStatus
from tests._scripted_llm import ScriptedLlm
from tests.address._corpus import corpus_records

BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH


# --------------------------------------------------------------------------- #
# Corpus-grounded document inputs (ids + artifact refs from expected.json).    #
# --------------------------------------------------------------------------- #
def _doc(doc_id: str) -> DocRef:
    """Build a ``DocRef`` from the corpus record (grounds ids/uris, not literals)."""
    for record in corpus_records():
        if record["id"] == doc_id:
            return DocRef(doc_id=doc_id, uri=record["artifact"])
    raise AssertionError(f"corpus has no document {doc_id!r}")


GOV_ID = _doc("gov-id-clean")
BILL_POWERCO = _doc("bill-powerco-clean")
BILL_POWERCO_2 = _doc("bill-powerco-clean-2")
BILL_AQUAUTIL = _doc("bill-aquautil-clean")
BILL_BLURRY = _doc("bill-aquautil-blurry")


# --------------------------------------------------------------------------- #
# Card-URL-parametrized harness (same run under both edges where it terminates).#
# --------------------------------------------------------------------------- #
def _mock_app():
    return build_mock_bridge_app(url=BASE + "/")


def _real_edge_app(mode: str, llm: ScriptedLlm):
    """The real edge in ``mode``; a hybrid edge runs its LLM leg on ``llm``."""
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    leg = LlmLeg(model=llm) if mode == "hybrid" else None
    return build_a2a_app(url=BASE + "/", executor=executor, edge_mode=mode, llm_leg=leg)


@pytest.fixture(params=["deterministic", "hybrid"])
def real_app(request):
    """A factory for the real edge in both routing modes (S3-edge-3).

    The Address driver's real-edge messages are always text + by-reference files, so
    the hybrid edge routes them to the Collector: the LLM double fails loud on its
    first call (``max_calls=0``) and teardown asserts it was called 0 times.
    """
    llm = ScriptedLlm(max_calls=0)

    def factory():
        return _real_edge_app(request.param, llm)

    yield factory
    assert llm.call_count == 0, f"the Address driver reached the LLM leg ({request.param})"


@asynccontextmanager
async def _driver_client(app):
    """Yield an httpx client over the in-process app (loopback base; caller owns it)."""
    transport = httpx.ASGITransport(app=app)
    hc = httpx.AsyncClient(transport=transport, base_url=BASE)
    try:
        yield hc
    finally:
        await hc.aclose()


async def _run(app, *, durable=False, session_db=None, **kwargs):
    """Drive one collection through ``run_collection``; return ``(turn, gate_rounds)``.

    Threads a shared session service so the gate's round counter can be read off the
    terminal session state (the ceiling / satisfaction authority — a model never
    mints ``done``). The injected httpx client is owned here (not closed inside
    ``run_collection``).

    Single-turn runs (default) use a plain ``Runner(node=...)`` over an
    ``InMemorySessionService``. Multi-turn mock scenarios opt into ``durable=True``
    (App path + ``ResumabilityConfig`` + durable session) with a ``session_db`` DSN
    so ``run_collection``'s P4-2 park→resume loop drives them to terminal on a fresh
    ``Runner`` per turn over one durable DB.
    """
    if durable:
        session_service = build_durable_session_service(db_url=session_db)
    else:
        session_service = InMemorySessionService()
    session_id = "golden"
    async with _driver_client(app) as hc:
        turn = await run_collection(
            CARD_URL,
            httpx_client=hc,
            session_service=session_service,
            session_id=session_id,
            durable=durable,
            **kwargs,
        )
    session = await session_service.get_session(
        app_name="address", user_id="address-driver", session_id=session_id
    )
    gate_rounds = session.state.get(STATE_GATE_ROUNDS)
    return turn, gate_rounds


def _accepted(turn):
    return [e for e in turn.status.entries if e.disposition is Disposition.accepted]


# --------------------------------------------------------------------------- #
# CUJ-1 — Path-A instant accept: the core mock→real parity proof.              #
# --------------------------------------------------------------------------- #
async def _assert_cuj1(app):
    """The ONE CUJ-1 assertion body, shared by the mock and the real edge."""
    turn, gate_rounds = await _run(app, scenario="path-a-instant", documents=[GOV_ID])

    assert turn is not None
    assert turn.terminal is True
    accepted = _accepted(turn)
    assert len(accepted) == 1
    entry = accepted[0]
    assert entry.doc_id == "gov-id-clean"
    assert entry.doctype == "gov-id"
    assert entry.gate is Gate.auto_approve
    assert is_satisfied(turn.status) is True
    assert gate_rounds == 1


@pytest.mark.parametrize(
    "target", ["mock", "deterministic", "hybrid"],
    ids=["mock", "real-deterministic", "real-hybrid"],
)
async def test_cuj1_path_a_instant_parity_mock_and_real(target):
    """CUJ-1 reaches the SAME terminal outcome on the mock and the real edge.

    One opening message carries both the ``scenario:path-a-instant`` marker (mock)
    and the ``gov-id-clean`` document (real edge). "Parity = terminal outcome" is
    literally one assertion body (:func:`_assert_cuj1`) over the card URLs; the real
    edge runs in both routing modes (S3-edge-3), its LLM leg called 0 times.
    """
    if target == "mock":
        await _assert_cuj1(_mock_app())
        return
    llm = ScriptedLlm(max_calls=0)
    await _assert_cuj1(_real_edge_app(target, llm))
    assert llm.call_count == 0, f"the Address driver reached the LLM leg ({target})"


# --------------------------------------------------------------------------- #
# CUJ-10 — distinct-issuer accept.                                             #
# --------------------------------------------------------------------------- #
async def test_cuj10_distinct_issuer_accept_real_edge(real_app):
    """Real edge, both bills at once → one terminal turn, two distinct issuers."""
    turn, gate_rounds = await _run(
        real_app(), documents=[BILL_POWERCO, BILL_AQUAUTIL]
    )

    assert turn is not None
    assert turn.terminal is True
    accepted = _accepted(turn)
    issuers = {e.issuer for e in accepted if e.doctype == "utility-bill"}
    assert issuers == {"power-co", "aqua-util"}
    assert is_satisfied(turn.status) is True
    assert gate_rounds == 1


async def test_cuj10_distinct_issuer_accept_mock(tmp_path):
    """Mock ``path-b-two-bills`` — a scripted multi-turn park then resume.

    Graduated in P4-2: driven through ``run_collection``'s durable park→resume loop
    (App path + durable ``DatabaseSessionService``), the scripted park resumes on a
    fresh ``Runner`` over the same DB and surfaces the two-issuer terminal turn.
    """
    turn, _gate_rounds = await _run(
        _mock_app(),
        scenario="path-b-two-bills",
        durable=True,
        session_db=f"sqlite+aiosqlite:///{tmp_path}/session.db",
    )

    assert turn is not None
    accepted = _accepted(turn)
    issuers = {e.issuer for e in accepted if e.doctype == "utility-bill"}
    assert issuers == {"power-co", "aqua-util"}
    assert is_satisfied(turn.status) is True


async def test_cuj10_same_issuer_reject_real_edge(real_app):
    """Real edge, two SAME-issuer bills → the gate never mints satisfaction.

    Both bills accept, but they share the ``power-co`` issuer, so Sense-B's
    distinct-issuer rule stays unsatisfied → the gate loops ``again`` until the
    round ceiling (``max_rounds=2``) forces ``done`` at ``present``. This pins the
    CUJ-10 reject half: ``is_satisfied`` is False and there are ``< 2`` distinct
    accepted issuers, so a model can never mint ``done`` off a same-issuer pair.
    """
    turn, gate_rounds = await _run(
        real_app(), documents=[BILL_POWERCO, BILL_POWERCO_2], max_rounds=2
    )

    assert turn is not None
    # Reached the ceiling (the again-loop ran max_rounds cycles).
    assert gate_rounds == 2
    accepted = _accepted(turn)
    # Both entries accepted, but the same issuer.
    assert len(accepted) == 2
    issuers = {e.issuer for e in accepted if e.doctype == "utility-bill"}
    assert issuers == {"power-co"}
    assert len(issuers) < 2
    # The gate never minted satisfaction.
    assert is_satisfied(turn.status) is False


# --------------------------------------------------------------------------- #
# CUJ-4 — resubmission.                                                        #
# --------------------------------------------------------------------------- #
async def test_cuj4_resubmit_first_turn_reject_real_edge(real_app):
    """Real edge, blurry bill → a first-turn reject/resubmit disposition.

    ``max_rounds=1`` so the run terminates at the ceiling in one round (the blurry
    bill never satisfies the gate). Pins the reject disposition: one rejected entry
    with ``gate=resubmit``, a non-empty ``reason_code``, an outstanding requirement,
    and ``is_satisfied`` False.
    """
    turn, gate_rounds = await _run(
        real_app(),
        scenario="resubmit",
        documents=[BILL_BLURRY],
        max_rounds=1,
    )

    assert turn is not None
    assert gate_rounds == 1
    rejected = [
        e for e in turn.status.entries if e.disposition is Disposition.rejected
    ]
    assert len(rejected) == 1
    entry = rejected[0]
    assert entry.doc_id == "bill-aquautil-blurry"
    assert entry.gate is Gate.resubmit
    assert entry.reason_code
    # An outstanding requirement surfaces (the clear copy that is still needed).
    assert turn.status.outstanding is not None
    assert len(turn.status.outstanding.requirements) >= 1
    assert is_satisfied(turn.status) is False


async def test_cuj4_resubmit_full_loop(tmp_path):
    """Mock ``resubmit`` — the full blurry→clear acceptance loop.

    Graduated in P4-2: the blurry-bill reject parks on INPUT_REQUIRED; the durable
    resume loop feeds an id-matched ``FunctionResponse`` on a fresh ``Runner`` and
    the clear copy arrives on the next turn, accepted.

    Empirical note (P4-2 §2.3): the scripted ``resubmit`` scenario surfaces a single
    clean utility bill, which does NOT satisfy the distinct-issuer Sense-B rule, so
    the gate loops to its round ceiling and ``is_satisfied`` is ``False``. The real
    outcome oracle is the *accepted clean bill*; the stale ``is_satisfied True``
    assertion (which assumed an accumulated ledger) is corrected here.
    """
    turn, _gate_rounds = await _run(
        _mock_app(),
        scenario="resubmit",
        durable=True,
        session_db=f"sqlite+aiosqlite:///{tmp_path}/session.db",
    )

    assert turn is not None
    assert turn.terminal is True
    accepted = _accepted(turn)
    assert any(
        e.doc_id == "bill-aquautil-clean" and e.doctype == "utility-bill"
        for e in accepted
    )
    # A single clean bill does not meet the distinct-issuer rule (see docstring).
    assert is_satisfied(turn.status) is False


async def test_cuj4_resubmit_to_escalate_boundary_mock(tmp_path):
    """Mock ``resubmit-escalate`` — the A2A journey crosses the ≤3 cap → escalate.

    S0-debt-1: ``bridge.pathb`` §2b unit-tests the ``attempts <= cap`` counting, but the
    cap→escalate transition was never exercised through the **agent-to-agent journey**.
    This drives the durable park→resume loop across four turns — three blurry-bill
    resubmit parks (attempts 1,2,3 at the cap) then a terminal escalate (attempts 4
    exceeds the cap) — over the same A2A wire the real edge serves.

    Escalation is **not** rejection (§2a): the terminal entry is ``PENDING`` with
    ``REASON_ESCALATED`` and an ``escalated`` lifecycle — the document was never judged
    wrong, the Bridge just could not read it, so it routes to a human.

    The escalate is reached through the **mediation ladder**, not by content forced past
    the cap: the mock's ``ScriptedBridgeExecutor`` advances one scripted step per A2A
    send and only emits the escalate step (``steps[3]``) after traversing the three
    resubmit parks (``steps[0..2]``). So the escalate *content* on the terminal turn is
    structural proof that the ≤3 cap was crossed over the wire — reaching it is
    impossible without the four A2A sends. Run under the default ``DEFAULT_MAX_ROUNDS``
    ceiling so no bespoke small ceiling is doing the terminating.
    """
    turn, _gate_rounds = await _run(
        _mock_app(),
        scenario="resubmit-escalate",
        durable=True,
        session_db=f"sqlite+aiosqlite:///{tmp_path}/session.db",
        max_rounds=DEFAULT_MAX_ROUNDS,
    )

    assert turn is not None
    assert turn.terminal is True
    assert turn.lifecycle is LifecycleStatus.escalated

    # Escalate ≠ reject (§2a): the terminal entry is PENDING (route-to-human), never
    # rejected — the document was not judged wrong, it just could not be read.
    assert not [
        e for e in turn.status.entries if e.disposition is Disposition.rejected
    ]
    pending = [
        e for e in turn.status.entries if e.disposition is Disposition.pending
    ]
    assert len(pending) == 1
    assert pending[0].reason_code == REASON_ESCALATED
    # Only reachable after the three resubmit parks → the cap was crossed on the wire.
    assert is_satisfied(turn.status) is False
