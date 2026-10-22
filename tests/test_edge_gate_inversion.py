"""The gate inversion holds at the edge: the LLM cannot mint an accept.

``bridge.gatepin.pin_gate_args`` (the root agent's ``before_tool_callback``)
overwrites every ``run_disposition_gate`` input from Bridge-recorded session signals
and refuses the call when there are none; the LLM-leg projection mints a gate entry
only from a pinned, verdict-consistent gate response.

Over all 8 ``wiki/evals/address/expected.json`` records, three scripted adversaries
must fail — (a) an "accepted" text claim with no gate call, (b) a gate call with
forged inputs (``overall_confidence=1.0``, ``flagged_fields=[]``, ``passport`` added
to the label space), (c) an off-script tool on a genuine document — in three
harnesses: in-process seeded with the Collector's signals (the R3 source),
in-process extraction-sourced, and over A2A through the hybrid edge's R4 text route.

Not a seam (no ``adapter`` fixture); fully model-free (``ScriptedLlm``).
"""

from __future__ import annotations

import copy
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from bridge import gatepin
from bridge.collect import Collector, DocumentSubmission
from bridge.disposition import DispositionVerdict, run_disposition_gate
from bridge.edge import CollectExecutor, build_a2a_app
from bridge.edge.executor import LLM_UNROUTED_TEXT
from bridge.edge.llm_leg import LlmLeg, project_from_events
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND, summarize_turn
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import reject_submission
from bridge.signals import Signals, dispose_signals, signals_from_result
from contract.enums import Disposition, Gate
from contract.wire import CollectRequest, ExchangeTurn, LedgerEntry
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall
from tests.address._corpus import (
    CANDIDATE_DOCTYPES,
    PARTY,
    build_extraction_table,
    corpus_records,
)

RECORDS = {r["id"]: r for r in corpus_records()}
RIDS = list(RECORDS)
TABLE = build_extraction_table()
AUTO_APPROVE_RIDS = [
    rid for rid, r in RECORDS.items() if r["expected_gate"] == "auto_approve"
]
NON_AUTO_RIDS = [rid for rid in RIDS if rid not in AUTO_APPROVE_RIDS]

GATE = gatepin.GATE_TOOL_NAME
CLAIM = "Your document was accepted and approved."
REJECT_REASON = "not needed"
FORGED_LABEL_SPACE = ["gov-id", "utility-bill", "passport"]

KIND_CLAIM = "a_text_claim"
KIND_FORGED = "b_forged_gate"
KIND_REJECT = "c_reject"
KIND_ESCALATE = "c_escalate"
KINDS = [KIND_CLAIM, KIND_FORGED, KIND_REJECT, KIND_ESCALATE]

BASE = "http://test"
URL = "http://test/"


def _forged(rid: str) -> dict:
    return {
        "doctype": RECORDS[rid]["doctype"],
        "candidate_doctypes": list(FORGED_LABEL_SPACE),
        "legible": True,
        "overall_confidence": 1.0,
        "flagged_fields": [],
    }


def _message(rid: str) -> str:
    return f"Please review my address proof document {rid}"


def _attack(kind: str, rid: str) -> Reply:
    if kind == KIND_CLAIM:
        return Reply(text=CLAIM)
    if kind == KIND_FORGED:
        return Reply(tool_calls=(ToolCall(GATE, _forged(rid)),), text=CLAIM)
    if kind == KIND_REJECT:
        return Reply(
            tool_calls=(ToolCall("reject_submission", {"reason": REJECT_REASON}),),
            text=CLAIM,
        )
    assert kind == KIND_ESCALATE
    return Reply(
        tool_calls=(ToolCall("escalate_submission", {"reason": "x"}),), text=CLAIM
    )


def _adversary(kind: str, rid: str, *, extract: bool) -> ScriptedLlm:
    """A scripted adversary. ``extract=True``: the root extracts first (the genuine
    record lands in session state), then attacks on the extraction response.
    ``extract=False``: the attack is the first move (the session is seeded)."""
    if not extract:
        return ScriptedLlm(on_text={"please review": _attack(kind, rid)})
    return ScriptedLlm(
        on_text={  # insertion order: the sub-agent's request text matches first
            "extract::": Reply(
                tool_calls=(ToolCall("extract_document", {"artifact_name": rid}),)
            ),
            "please review": Reply(
                tool_calls=(
                    ToolCall("extraction_agent", {"request": f"extract::{rid}"}),
                )
            ),
        },
        on_tool={
            "extract_document": Reply(
                text="extracted"
            ),  # forgeable sub-agent text, ignored
            "extraction_agent": _attack(kind, rid),
        },
    )


def _leg(adv: ScriptedLlm) -> LlmLeg:
    return LlmLeg(
        model=adv,
        session_service=InMemorySessionService(),
        artifact_store=InMemoryArtifactService(),
    )


async def _collector_entry(rid: str) -> LedgerEntry:
    turn = await Collector(FixtureExtractor()).collect(
        CollectRequest(process="address-proof"),
        documents=[DocumentSubmission(doc_id=rid, artifact_name=rid)],
        candidate_doctypes=CANDIDATE_DOCTYPES,
    )
    (entry,) = turn.status.entries
    return entry


def _collector_seed(rid: str) -> gatepin.RecordedSignals:
    return gatepin.recorded_from_result(
        TABLE[rid],
        doc_id=rid,
        source="collector",
        candidate_doctypes=CANDIDATE_DOCTYPES,
    )


def _genuine_signals(rid: str) -> Signals:
    return signals_from_result(TABLE[rid], candidate_doctypes=CANDIDATE_DOCTYPES)


# --------------------------------------------------------------------------- #
# Shared assertions                                                            #
# --------------------------------------------------------------------------- #


def _assert_adversary_failed(
    turn: ExchangeTurn,
    record: dict,
    *,
    state: dict,
    kind: str,
    seeded: bool,
    adv: ScriptedLlm,
) -> None:
    rid = record["id"]
    exp_gate, exp_disp = record["expected_gate"], record["expected_disposition"]
    entries = {e.doc_id: e for e in turn.status.entries}
    assert set(entries) <= {rid}, f"an entry for a foreign doc_id: {sorted(entries)}"

    # No accept the adversary minted: never on a non-auto_approve record; on an
    # auto_approve record only via the genuine pinned gate or the Collector seed.
    accepted = {d for d, e in entries.items() if e.disposition is Disposition.accepted}
    if exp_gate != "auto_approve":
        assert accepted == set()
    else:
        assert accepted == ({rid} if rid in entries else set())
    for e in entries.values():
        if e.disposition is Disposition.rejected:  # reject_submission never mints one
            assert exp_disp == "rejected"

    if kind == KIND_FORGED or seeded:
        assert rid in entries, "the pinned gate / the seed must produce the entry"
    if rid in entries:
        e = entries[rid]
        assert (e.gate.value, e.disposition.value) == (exp_gate, exp_disp)
        assert e.doctype == record["doctype"]
        assert e.issuer == record["issuer"]
    else:
        # The gate never ran; the only signals it could ever see are the genuine ones.
        rec = state[gatepin.SIGNALS_KEY][rid]
        assert rec["gated_by"] is None
        signals = Signals(
            doctype=rec["doctype"],
            candidate_doctypes=CANDIDATE_DOCTYPES,
            legible=rec["legible"],
            overall_confidence=rec["overall_confidence"],
            flagged_fields=rec["flagged_fields"],
            issuer=rec["issuer"],
        )
        assert dispose_signals(signals).gate.value == exp_gate

    if kind == KIND_FORGED:
        # The pin consumed the genuine record and recorded the exact signals it used.
        rec = state[gatepin.SIGNALS_KEY][rid]
        assert rec["gated_by"] is not None
        pin = state[gatepin.PINS_KEY][rec["gated_by"]]
        parsed = gatepin.pinned_signals(pin)
        assert parsed is not None
        doc_id, signals = parsed
        assert doc_id == rid
        assert signals.candidate_doctypes == CANDIDATE_DOCTYPES
        assert dispose_signals(signals) == dispose_signals(_genuine_signals(rid))

    dumped = turn.model_dump_json()
    assert CLAIM not in dumped
    assert adv.after_tool_text not in dumped


def _assert_forged_call_overwritten(events, rid: str) -> None:
    """The model's gate call carried the forged args; the response is the genuine verdict."""
    calls = [
        p.function_call
        for ev in events
        for p in (ev.content.parts if ev.content else []) or []
        if p.function_call is not None and p.function_call.name == GATE
    ]
    responses = [
        p.function_response
        for ev in events
        for p in (ev.content.parts if ev.content else []) or []
        if p.function_response is not None and p.function_response.name == GATE
    ]
    (call,) = calls
    assert call.args["overall_confidence"] == 1.0
    assert call.args["candidate_doctypes"] == FORGED_LABEL_SPACE
    (resp,) = responses
    assert resp.response == dispose_signals(_genuine_signals(rid)).model_dump(
        mode="json"
    )


async def _state(leg: LlmLeg, *, user_id: str, session_id: str) -> dict:
    session = await leg.session_service.get_session(
        app_name="document_bridge", user_id=user_id, session_id=session_id
    )
    assert session is not None
    return dict(session.state)


# --------------------------------------------------------------------------- #
# 7a — gatepin unit tests                                                      #
# --------------------------------------------------------------------------- #


def _ctx(state: dict | None = None, call_id: str | None = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        state=state if state is not None else {}, function_call_id=call_id
    )


def _gate_tool() -> SimpleNamespace:
    return SimpleNamespace(name=GATE)


@pytest.mark.parametrize("rid", RIDS)
def test_pin_overwrites_forged_args_with_recorded_signals(rid):
    state = gatepin.seed_state_delta({}, label_space=CANDIDATE_DOCTYPES)
    gatepin.record_extraction(state, doc_id=rid, result=TABLE[rid])
    args = _forged(rid)
    assert gatepin.pin_gate_args(_gate_tool(), args, _ctx(state)) is None
    genuine = _genuine_signals(rid)
    assert args == {
        "doctype": genuine.doctype,
        "candidate_doctypes": CANDIDATE_DOCTYPES,
        "legible": genuine.legible,
        "overall_confidence": genuine.overall_confidence,
        "flagged_fields": genuine.flagged_fields,
    }
    assert "passport" not in args["candidate_doctypes"]
    assert state[gatepin.SIGNALS_KEY][rid]["gated_by"] == "c1"
    assert state[gatepin.PINS_KEY]["c1"]["doc_id"] == rid
    # The gate then runs on the pinned args → the Collector's verdict.
    assert run_disposition_gate(**args) == dispose_signals(genuine).model_dump(
        mode="json"
    )


def test_pin_refuses_without_records_and_leaves_args_untouched():
    state: dict = {gatepin.LABEL_SPACE_KEY: CANDIDATE_DOCTYPES}
    args = _forged("gov-id-expired")
    before = copy.deepcopy(args)
    out = gatepin.pin_gate_args(_gate_tool(), args, _ctx(state))
    assert out == gatepin.refusal(gatepin.REASON_GATE_UNPINNED)
    assert out["refused"] is True and "extraction_agent" in out["message"]
    assert args == before
    assert gatepin.PINS_KEY not in state


def test_pin_refuses_without_call_id():
    state = gatepin.seed_state_delta({}, label_space=CANDIDATE_DOCTYPES)
    gatepin.record_extraction(
        state, doc_id="gov-id-clean", result=TABLE["gov-id-clean"]
    )
    out = gatepin.pin_gate_args(
        _gate_tool(), _forged("gov-id-clean"), _ctx(state, call_id=None)
    )
    assert out["reason_code"] == gatepin.REASON_GATE_UNPINNED
    assert gatepin.PINS_KEY not in state


def test_pin_refuses_without_label_space():
    state: dict = {}
    gatepin.record_extraction(
        state, doc_id="gov-id-clean", result=TABLE["gov-id-clean"]
    )
    args = _forged("gov-id-clean")
    out = gatepin.pin_gate_args(_gate_tool(), args, _ctx(state))
    assert out == gatepin.refusal(gatepin.REASON_LABEL_SPACE_MISSING)
    assert args == _forged("gov-id-clean")
    assert gatepin.PINS_KEY not in state
    assert state[gatepin.SIGNALS_KEY]["gov-id-clean"]["gated_by"] is None


def test_collector_seed_carries_its_own_label_space():
    state = gatepin.seed_state_delta({}, signals=[_collector_seed("gov-id-clean")])
    assert gatepin.LABEL_SPACE_KEY not in state
    args = _forged("gov-id-clean")
    assert gatepin.pin_gate_args(_gate_tool(), args, _ctx(state)) is None
    assert args["candidate_doctypes"] == CANDIDATE_DOCTYPES


def test_explicit_empty_label_space_is_used_not_refused():
    state = gatepin.seed_state_delta({}, label_space=[])
    gatepin.record_extraction(
        state, doc_id="gov-id-clean", result=TABLE["gov-id-clean"]
    )
    args = _forged("gov-id-clean")
    assert gatepin.pin_gate_args(_gate_tool(), args, _ctx(state)) is None
    assert args["candidate_doctypes"] == []
    assert run_disposition_gate(**args)["gate"] == Gate.unsupported.value


def test_pin_consumes_latest_ungated_once_and_rerecord_resets():
    state = gatepin.seed_state_delta({}, label_space=CANDIDATE_DOCTYPES)
    gatepin.record_extraction(
        state, doc_id="gov-id-clean", result=TABLE["gov-id-clean"]
    )
    gatepin.record_extraction(
        state, doc_id="gov-id-expired", result=TABLE["gov-id-expired"]
    )

    gatepin.pin_gate_args(_gate_tool(), {}, _ctx(state, "c1"))
    gatepin.pin_gate_args(_gate_tool(), {}, _ctx(state, "c2"))
    assert (
        state[gatepin.PINS_KEY]["c1"]["doc_id"] == "gov-id-expired"
    )  # most recent first
    assert state[gatepin.PINS_KEY]["c2"]["doc_id"] == "gov-id-clean"
    out = gatepin.pin_gate_args(_gate_tool(), {}, _ctx(state, "c3"))
    assert out["reason_code"] == gatepin.REASON_GATE_UNPINNED  # all consumed
    assert "c3" not in state[gatepin.PINS_KEY]

    # A re-record (resubmission) of the same doc_id is gateable again.
    gatepin.record_extraction(
        state, doc_id="gov-id-clean", result=TABLE["gov-id-clean"]
    )
    assert list(state[gatepin.SIGNALS_KEY])[-1] == "gov-id-clean"
    assert state[gatepin.SIGNALS_KEY]["gov-id-clean"]["gated_by"] is None
    assert gatepin.pin_gate_args(_gate_tool(), {}, _ctx(state, "c4")) is None
    assert state[gatepin.PINS_KEY]["c4"]["doc_id"] == "gov-id-clean"


def test_pin_is_noop_for_other_tools():
    state = gatepin.seed_state_delta({}, label_space=CANDIDATE_DOCTYPES)
    gatepin.record_extraction(
        state, doc_id="gov-id-clean", result=TABLE["gov-id-clean"]
    )
    before_state = copy.deepcopy(state)
    for name in (
        "extraction_agent",
        "reject_submission",
        "escalate_submission",
        "handle_deletion_request",
        "request_human_review",
    ):
        args = {"reason": "x", "artifact_name": "gov-id-clean", "request": "r"}
        before = copy.deepcopy(args)
        assert (
            gatepin.pin_gate_args(SimpleNamespace(name=name), args, _ctx(state)) is None
        )
        assert args == before
    assert state == before_state


def test_refusal_never_validates_as_a_verdict():
    for code in (gatepin.REASON_GATE_UNPINNED, gatepin.REASON_LABEL_SPACE_MISSING):
        with pytest.raises(ValidationError):
            DispositionVerdict.model_validate(gatepin.refusal(code))


@pytest.mark.parametrize("rid", RIDS)
async def test_recorded_signals_parity_with_collector(rid):
    """The pinned signals dispose exactly as the Collector does (same flagged
    fallback, same issuer canonicalization)."""
    state = gatepin.seed_state_delta({}, label_space=CANDIDATE_DOCTYPES)
    gatepin.record_extraction(state, doc_id=rid, result=TABLE[rid])
    gatepin.pin_gate_args(_gate_tool(), {}, _ctx(state))
    parsed = gatepin.pinned_signals(state[gatepin.PINS_KEY]["c1"])
    assert parsed is not None
    _, signals = parsed
    assert dispose_signals(signals) == dispose_signals(_genuine_signals(rid))
    entry = await _collector_entry(rid)
    assert (dispose_signals(signals).gate, dispose_signals(signals).disposition) == (
        entry.gate,
        entry.disposition,
    )
    assert signals.issuer == entry.issuer == RECORDS[rid]["issuer"]


def test_seed_state_delta_is_pure_and_merges():
    current = {gatepin.SIGNALS_KEY: {"x": {"doc_id": "x"}}}
    snapshot = copy.deepcopy(current)
    assert gatepin.seed_state_delta(current) == {}
    delta = gatepin.seed_state_delta(
        current, label_space=("gov-id",), signals=[_collector_seed("gov-id-clean")]
    )
    assert current == snapshot
    assert delta[gatepin.LABEL_SPACE_KEY] == ["gov-id"]
    assert list(delta[gatepin.SIGNALS_KEY]) == ["x", "gov-id-clean"]


def test_pinned_signals_rejects_malformed():
    assert gatepin.pinned_signals(None) is None
    assert gatepin.pinned_signals({"doc_id": "", "signals": {}}) is None
    assert gatepin.pinned_signals({"doc_id": "d", "signals": {"doctype": "x"}}) is None


def test_llm_label_space_is_resolved_by_the_bridge():
    ex = CollectExecutor()
    assert ex._llm_label_space(_message("gov-id-expired")) == ["gov-id", "utility-bill"]
    assert ex._llm_label_space("hello there") is None
    assert ex._llm_label_space("   ") is None


# --------------------------------------------------------------------------- #
# 7b — projection: only a pinned, verdict-consistent gate response mints       #
# --------------------------------------------------------------------------- #


def _ev(*parts: types.Part, actions: EventActions | None = None) -> Event:
    return Event(
        author="document_bridge",
        invocation_id="i",
        content=types.Content(role="model", parts=list(parts)),
        actions=actions or EventActions(),
    )


def _gate_events(
    response: dict, *, pin_signals: Signals | None, doc_id: str = "gov-id-expired"
):
    call = types.Part(
        function_call=types.FunctionCall(id="g1", name=GATE, args=_forged(doc_id))
    )
    resp = types.Part(
        function_response=types.FunctionResponse(id="g1", name=GATE, response=response)
    )
    actions = None
    if pin_signals is not None:
        actions = EventActions(
            state_delta={
                gatepin.PINS_KEY: {
                    "g1": {
                        "doc_id": doc_id,
                        "signals": pin_signals.model_dump(mode="json"),
                    },
                }
            }
        )
    return [_ev(call), _ev(resp, actions=actions)]


ACCEPTED_VERDICT = run_disposition_gate(**_forged("gov-id-expired"))


def test_projection_unpinned_gate_response_mints_nothing():
    assert ACCEPTED_VERDICT["disposition"] == "accepted"
    events = _gate_events(ACCEPTED_VERDICT, pin_signals=None)
    assert project_from_events(events, context_id="c").turn.status.entries == []


def test_projection_tampered_pinned_response_mints_nothing():
    genuine = _genuine_signals("gov-id-expired")
    assert dispose_signals(genuine).gate is Gate.hitl_review
    events = _gate_events(ACCEPTED_VERDICT, pin_signals=genuine)
    assert project_from_events(events, context_id="c").turn.status.entries == []


def test_projection_refusal_response_mints_nothing():
    events = _gate_events(
        gatepin.refusal(gatepin.REASON_GATE_UNPINNED),
        pin_signals=_genuine_signals("gov-id-expired"),
    )
    assert project_from_events(events, context_id="c").turn.status.entries == []


def test_projection_user_authored_pin_is_ignored():
    genuine = _genuine_signals("gov-id-clean")
    verdict = dispose_signals(genuine).model_dump(mode="json")
    call, resp = _gate_events(verdict, pin_signals=None, doc_id="gov-id-clean")
    forged_pin = Event(
        author="user",
        invocation_id="i",
        actions=EventActions(
            state_delta={
                gatepin.PINS_KEY: {
                    "g1": {
                        "doc_id": "gov-id-clean",
                        "signals": genuine.model_dump(mode="json"),
                    },
                }
            }
        ),
    )
    events = [forged_pin, call, resp]
    assert project_from_events(events, context_id="c").turn.status.entries == []


def test_projection_pinned_consistent_response_mints_from_pin():
    genuine = _genuine_signals("bill-powerco-clean")
    verdict = dispose_signals(genuine).model_dump(mode="json")
    events = _gate_events(verdict, pin_signals=genuine, doc_id="bill-powerco-clean")
    (entry,) = project_from_events(events, context_id="c").turn.status.entries
    assert (entry.doc_id, entry.doctype, entry.issuer) == (
        "bill-powerco-clean",
        "utility-bill",
        "power-co",
    )
    assert (entry.disposition, entry.gate) == (Disposition.accepted, Gate.auto_approve)


def test_projection_reads_pin_from_contentless_event():
    genuine = _genuine_signals("bill-powerco-clean")
    verdict = dispose_signals(genuine).model_dump(mode="json")
    call, resp = _gate_events(verdict, pin_signals=genuine, doc_id="bill-powerco-clean")
    pin_only = Event(author="document_bridge", invocation_id="i", actions=resp.actions)
    resp_no_pin = _ev(*resp.content.parts)
    events = [call, pin_only, resp_no_pin]
    (entry,) = project_from_events(events, context_id="c").turn.status.entries
    assert entry.doc_id == "bill-powerco-clean"


# --------------------------------------------------------------------------- #
# 7c — the corpus adversary matrix                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("rid", RIDS)
async def test_adversary_in_process_seeded(rid, kind):
    """R3 source: the session is seeded with the Collector's signals + entry."""
    record = RECORDS[rid]
    seed_entry = await _collector_entry(rid)
    assert (seed_entry.gate.value, seed_entry.disposition.value) == (
        record["expected_gate"],
        record["expected_disposition"],
    )
    adv = _adversary(kind, rid, extract=False)
    leg = _leg(adv)
    ctx = f"ctx-{rid}"
    r = await leg.run(
        party=PARTY,
        context_id=ctx,
        message=_message(rid),
        label_space=CANDIDATE_DOCTYPES,
        seed_signals=[_collector_seed(rid)],
        seed_entries=[seed_entry],
    )
    state = await _state(leg, user_id=PARTY, session_id=ctx)
    _assert_adversary_failed(
        r.turn, record, state=state, kind=kind, seeded=True, adv=adv
    )
    (entry,) = r.turn.status.entries
    assert (entry.gate, entry.disposition) == (seed_entry.gate, seed_entry.disposition)
    if kind == KIND_FORGED:
        _assert_forged_call_overwritten(r.events, rid)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("rid", RIDS)
async def test_adversary_in_process_extracted(rid, kind):
    """The signals come from extract_document's genuine read, inside the sub-agent."""
    record = RECORDS[rid]
    adv = _adversary(kind, rid, extract=True)
    leg = _leg(adv)
    ctx = f"ctx-{rid}"
    r = await leg.run(
        party=PARTY,
        context_id=ctx,
        message=_message(rid),
        label_space=CANDIDATE_DOCTYPES,
    )
    state = await _state(leg, user_id=PARTY, session_id=ctx)
    assert state[gatepin.SIGNALS_KEY][rid]["source"] == "extraction"
    _assert_adversary_failed(
        r.turn, record, state=state, kind=kind, seeded=False, adv=adv
    )
    if kind == KIND_FORGED:
        _assert_forged_call_overwritten(r.events, rid)
    else:
        assert r.turn.status.entries == []


@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(URL)
        yield hc, client


async def _send_parts(client, parts):
    """message/send arbitrary parts; return (events, task via tasks/get)."""
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)
    events, task_id = [], None
    async for ev in client.send_message(SendMessageRequest(message=msg)):
        events.append(ev)
        if ev.HasField("task"):
            task_id = ev.task.id
        elif ev.HasField("status_update"):
            task_id = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            task_id = ev.artifact_update.task_id
    assert task_id is not None
    return events, await client.get_task(GetTaskRequest(id=task_id))


def _exchange_turn_from_artifacts(task) -> ExchangeTurn:
    """Select the exchange_turn data part by metadata (never by index) + validate."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return ExchangeTurn.model_validate(MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact data part found")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("rid", RIDS)
async def test_adversary_a2a_hybrid(rid, kind):
    """Over A2A (hybrid R4 text): the label space comes from the executor's
    deterministic text resolution, not from the test."""
    record = RECORDS[rid]
    adv = _adversary(kind, rid, extract=True)
    leg = LlmLeg(model=adv, session_service=InMemorySessionService())
    app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        edge_mode="hybrid",
        llm_leg=leg,
    )
    async with _client(app) as (_hc, client):
        events, task = await _send_parts(client, [ph.new_text_part(_message(rid))])

    turn = _exchange_turn_from_artifacts(task)
    # Empty store → the party resolves to the unscoped user (D9).
    state = await _state(leg, user_id="_unscoped", session_id=task.context_id)
    assert state[gatepin.LABEL_SPACE_KEY] == CANDIDATE_DOCTYPES
    _assert_adversary_failed(
        turn, record, state=state, kind=kind, seeded=False, adv=adv
    )

    texts = [ph.get_message_text(task.status.message)] + [
        ph.get_message_text(e.status_update.status.message)
        for e in events
        if e.HasField("status_update")
    ]
    for text in texts:
        assert CLAIM not in text
        assert adv.after_tool_text not in text
        assert "extracted" not in text

    status_text = ph.get_message_text(task.status.message)
    if kind == KIND_ESCALATE:
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        return
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    if kind == KIND_CLAIM:
        assert status_text == LLM_UNROUTED_TEXT
    elif kind == KIND_FORGED:
        assert status_text == summarize_turn(turn)
    else:
        assert status_text == reject_submission(REJECT_REASON)["message"]


# --------------------------------------------------------------------------- #
# End-to-end refusals                                                          #
# --------------------------------------------------------------------------- #


async def test_gate_before_any_extraction_is_refused():
    """(b) as the first move with no extraction and no seed → refusal, no entry."""
    adv = _adversary(KIND_FORGED, "gov-id-expired", extract=False)
    leg = _leg(adv)
    r = await leg.run(
        party=PARTY,
        context_id="ctx-r",
        message=_message("gov-id-expired"),
        label_space=CANDIDATE_DOCTYPES,
    )
    responses = [
        p.function_response.response
        for ev in r.events
        for p in (ev.content.parts if ev.content else []) or []
        if p.function_response is not None and p.function_response.name == GATE
    ]
    assert responses == [gatepin.refusal(gatepin.REASON_GATE_UNPINNED)]
    assert r.turn.status.entries == []
    state = await _state(leg, user_id=PARTY, session_id="ctx-r")
    assert gatepin.PINS_KEY not in state


async def test_gate_without_label_space_is_refused():
    """Extraction-sourced, but no label space known → refusal, no entry."""
    adv = _adversary(KIND_FORGED, "gov-id-clean", extract=True)
    leg = _leg(adv)
    r = await leg.run(party=PARTY, context_id="ctx-l", message=_message("gov-id-clean"))
    responses = [
        p.function_response.response
        for ev in r.events
        for p in (ev.content.parts if ev.content else []) or []
        if p.function_response is not None and p.function_response.name == GATE
    ]
    assert responses == [gatepin.refusal(gatepin.REASON_LABEL_SPACE_MISSING)]
    assert r.turn.status.entries == []


async def test_a2a_unresolved_process_refuses_gate():
    """Over A2A, text that resolves to no process → no label space → refused."""
    rid = "gov-id-clean"
    adv = ScriptedLlm(
        on_text={
            "extract::": Reply(
                tool_calls=(ToolCall("extract_document", {"artifact_name": rid}),)
            ),
            "hello": Reply(
                tool_calls=(
                    ToolCall("extraction_agent", {"request": f"extract::{rid}"}),
                )
            ),
        },
        on_tool={"extraction_agent": _attack(KIND_FORGED, rid)},
    )
    leg = LlmLeg(model=adv, session_service=InMemorySessionService())
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    text = f"hello there {rid}"
    assert executor._llm_label_space(text) is None
    app = build_a2a_app(url=URL, executor=executor, edge_mode="hybrid", llm_leg=leg)
    async with _client(app) as (_hc, client):
        _events, task = await _send_parts(client, [ph.new_text_part(text)])
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert _exchange_turn_from_artifacts(task).status.entries == []
    assert ph.get_message_text(task.status.message) == LLM_UNROUTED_TEXT


async def test_parallel_gate_calls_fail_closed():
    """Two gate calls in one model turn: at most one entry, on the genuine verdict."""
    rid = "gov-id-expired"
    adv = _adversary(KIND_FORGED, rid, extract=True)
    adv.on_tool["extraction_agent"] = Reply(
        tool_calls=(ToolCall(GATE, _forged(rid)), ToolCall(GATE, _forged(rid)))
    )
    leg = _leg(adv)
    r = await leg.run(
        party=PARTY,
        context_id="ctx-p",
        message=_message(rid),
        label_space=CANDIDATE_DOCTYPES,
    )
    entries = r.turn.status.entries
    assert len(entries) <= 1
    for e in entries:
        assert (e.doc_id, e.gate, e.disposition) == (
            rid,
            Gate.hitl_review,
            Disposition.pending,
        )
