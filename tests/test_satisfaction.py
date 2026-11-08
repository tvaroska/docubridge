"""S0-arch-16 — the Bridge's best-effort satisfaction proposal (COLL-2).

Unit tests for ``bridge.satisfaction`` (proposal, fallback, ``looks_done``, the
opt-in resolver) and ``bridge.satisfaction_llm`` (request shape over a JSON
``BaseLlm`` double, malformed output), the skill read
(``bridge.edge.skills.satisfaction_for``), and the A2A edge end to end: the proposal
rides ``status.outstanding`` of Collector and Path-A turns when an assessor is bound
through ``build_a2a_app(satisfaction=...)``; the default edge stays model-free and
byte-identical; the app's gate (``agents.address.gate.is_satisfied``) always owns
done. One live test (real Gemini) is gated on ``BRIDGE_TEST_GEMINI=1``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from google.adk.models import BaseLlm, LlmCapabilities, LlmRequest, LlmResponse
from google.genai import types
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
from pydantic import Field

from bridge.collect import Collector, project_outstanding
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.llm_leg import LlmLeg
from bridge.edge.skills import satisfaction_for, skill_meta_from_dir
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND, summarize_turn
from bridge.extraction.fixture import FixtureExtractor
from bridge.satisfaction import (
    MAX_ITEMS,
    MAX_MESSAGE_CHARS,
    PROPOSAL_REASON_CODE,
    SATISFACTION_ENV,
    ProposedItem,
    looks_done,
    propose_outstanding,
    resolve_assessor,
)
from bridge.satisfaction_llm import ASSESSOR_INSTRUCTION, LlmSatisfactionAssessor
from contract.enums import Disposition, Gate, RequirementStatus
from contract.wire import (
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
)
from tests._scripted_llm import ScriptedLlm


def is_satisfied(status: CollectionStatus) -> bool:
    """Sense-B completeness check for address-proof."""
    accepted = [e for e in status.entries if e.disposition is Disposition.accepted]
    if any(e.doctype == "gov-id" for e in accepted):
        return True
    issuers = {e.issuer for e in accepted if e.doctype == "utility-bill" and e.issuer}
    return len(issuers) >= 2


BASE = "http://test"
URL = "http://test/"
PROCESS = "address-proof"
LABELS = ["gov-id", "utility-bill"]
SKILLS = Path(__file__).resolve().parents[1] / "skills"

GOV_ID = ("gov-id-clean", "images/gov-id-clean.svg")
POWERCO = ("bill-powerco-clean", "images/bill-powerco-clean.svg")
POWERCO_2 = ("bill-powerco-clean-2", "images/bill-powerco-clean-2.svg")
BLURRY = ("bill-aquautil-blurry", "images/bill-aquautil-blurry.svg")


@pytest.fixture(autouse=True)
def _satisfaction_env_unset(monkeypatch):
    """Every test starts from the default posture (no ambient opt-in)."""
    monkeypatch.delenv(SATISFACTION_ENV, raising=False)


# --------------------------------------------------------------------------- #
# Doubles                                                                      #
# --------------------------------------------------------------------------- #
class FakeAssessor:
    """Records calls; returns canned items, raises, or sleeps."""

    def __init__(
        self,
        items: Sequence[ProposedItem] = (),
        *,
        exc: Exception | None = None,
        sleep: float = 0.0,
    ) -> None:
        self.items = list(items)
        self.exc = exc
        self.sleep = sleep
        self.calls: list[dict] = []

    async def assess(self, *, rule, entries, candidate_doctypes):
        self.calls.append(
            {
                "rule": rule,
                "entries": list(entries),
                "candidate_doctypes": list(candidate_doctypes),
            }
        )
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.exc is not None:
            raise self.exc
        return list(self.items)


class JsonLlm(BaseLlm):
    """Model-free ``BaseLlm`` replying with one fixed text (the JSON body)."""

    # Must NOT look like a gemini name (see tests/_scripted_llm.py).
    model: str = "json-llm"
    reply: str = '{"items": []}'
    requests: list[LlmRequest] = Field(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)])
        )


def _entry(
    doc_id: str,
    doctype: str,
    disposition: Disposition = Disposition.accepted,
    *,
    issuer: str | None = None,
    key_fields: dict[str, str] | None = None,
) -> LedgerEntry:
    accepted = disposition is Disposition.accepted
    return LedgerEntry(
        doc_id=doc_id,
        doctype=doctype,
        disposition=disposition,
        gate=Gate.auto_approve if accepted else Gate.resubmit,
        issuer=issuer,
        key_fields=key_fields or {},
        reason_code=None if accepted else "illegible",
        message=None if accepted else "The document could not be read.",
    )


RULE = "Satisfied by one accepted ID, OR two accepted bills from distinct issuers."
REQUIRED_BILL = ProposedItem(
    item="proof of address",
    status="required",
    doctype_hint="utility-bill",
    message="One more utility bill from a different company.",
)
SATISFIED = ProposedItem(item="proof of address", status="satisfied")


# --------------------------------------------------------------------------- #
# Unit — propose_outstanding / looks_done / resolve_assessor                   #
# --------------------------------------------------------------------------- #
async def test_no_rule_falls_back_to_per_entry():
    entries = [_entry("a", "utility-bill", Disposition.rejected)]
    fake = FakeAssessor([SATISFIED])
    out = await propose_outstanding(
        entries, rule="  ", candidate_doctypes=LABELS, assessor=fake
    )
    assert out == project_outstanding(entries)
    assert fake.calls == []


async def test_no_assessor_falls_back():
    entries = [_entry("a", "utility-bill", Disposition.rejected)]
    out = await propose_outstanding(
        entries, rule=RULE, candidate_doctypes=LABELS, assessor=None
    )
    assert out == project_outstanding(entries)


async def test_proposal_replaces_per_entry_and_never_mints_done():
    entries = [
        _entry("bill-1", "utility-bill", issuer="power-co"),
        _entry("bill-2", "utility-bill", Disposition.rejected),
    ]
    fake = FakeAssessor([REQUIRED_BILL])
    out = await propose_outstanding(
        entries, rule=RULE, candidate_doctypes=LABELS, assessor=fake
    )
    assert out.done is False
    assert out.requirements == [
        Requirement(
            item="proof of address",
            status=RequirementStatus.required,
            doctype_hint="utility-bill",
            reason_code=PROPOSAL_REASON_CODE,
            message="One more utility bill from a different company.",
        )
    ]
    # The rejected entry's per-entry requirement is replaced (its reason stays on
    # the ledger entry itself).
    assert all(r.reason_code == PROPOSAL_REASON_CODE for r in out.requirements)
    assert fake.calls == [
        {"rule": RULE, "entries": entries, "candidate_doctypes": LABELS}
    ]


async def test_satisfied_proposal_still_never_done():
    out = await propose_outstanding(
        [_entry("g", "gov-id")],
        rule=RULE,
        candidate_doctypes=LABELS,
        assessor=FakeAssessor([SATISFIED]),
    )
    assert looks_done(out) is True
    assert out.done is False


async def test_hint_outside_label_space_dropped():
    item = ProposedItem(item="photo id", status="required", doctype_hint="passport")
    out = await propose_outstanding(
        [], rule=RULE, candidate_doctypes=LABELS, assessor=FakeAssessor([item])
    )
    assert out.requirements[0].doctype_hint is None


async def test_sanitize_truncates_and_caps():
    items = [
        ProposedItem(item=f"item {i}", status="optional", message="x" * 1000)
        for i in range(MAX_ITEMS + 5)
    ]
    out = await propose_outstanding(
        [], rule=RULE, candidate_doctypes=LABELS, assessor=FakeAssessor(items)
    )
    assert len(out.requirements) == MAX_ITEMS
    assert all(len(r.message or "") == MAX_MESSAGE_CHARS for r in out.requirements)


@pytest.mark.parametrize(
    "fake",
    [
        pytest.param(FakeAssessor(exc=RuntimeError("model down")), id="raises"),
        pytest.param(FakeAssessor([SATISFIED], sleep=1.0), id="timeout"),
        pytest.param(FakeAssessor([]), id="empty"),
        pytest.param(
            FakeAssessor([ProposedItem(item="   ", status="satisfied")]), id="blank"
        ),
    ],
)
async def test_failure_modes_fall_back(fake):
    entries = [
        _entry("g", "gov-id"),
        _entry("b", "utility-bill", Disposition.rejected),
    ]
    out = await propose_outstanding(
        entries, rule=RULE, candidate_doctypes=LABELS, assessor=fake, timeout=0.01
    )
    assert out == project_outstanding(entries)
    assert len(fake.calls) == 1


async def test_failure_never_logs_ledger_contents(caplog):
    entries = [_entry("g", "gov-id", key_fields={"name": "Jordan Lee"})]
    with caplog.at_level("WARNING", logger="bridge.satisfaction"):
        await propose_outstanding(
            entries,
            rule=RULE,
            candidate_doctypes=LABELS,
            assessor=FakeAssessor(exc=RuntimeError("boom Jordan Lee")),
        )
    assert "RuntimeError" in caplog.text
    assert "Jordan Lee" not in caplog.text


def _reqs(*statuses: RequirementStatus) -> RequirementsList:
    return RequirementsList(
        requirements=[
            Requirement(item=f"i{n}", status=s) for n, s in enumerate(statuses)
        ]
    )


def test_looks_done():
    s, o, r = (
        RequirementStatus.satisfied,
        RequirementStatus.optional,
        RequirementStatus.required,
    )
    assert looks_done(_reqs(s)) is True
    assert looks_done(_reqs(s, o)) is True
    assert looks_done(_reqs(s, r)) is False
    assert looks_done(_reqs(r)) is False
    assert looks_done(_reqs()) is False
    assert looks_done(None) is False


def test_resolve_assessor(monkeypatch):
    assert resolve_assessor() is None
    monkeypatch.setenv(SATISFACTION_ENV, "  ")
    assert resolve_assessor() is None
    monkeypatch.setenv(SATISFACTION_ENV, "off")
    assert resolve_assessor() is None
    monkeypatch.setenv(SATISFACTION_ENV, "llm")
    # Built without network: the model is resolved lazily on first assess().
    assert isinstance(resolve_assessor(), LlmSatisfactionAssessor)
    # Explicit wins over env.
    assert resolve_assessor("off") is None
    fake = FakeAssessor()
    assert resolve_assessor(fake) is fake
    for bad in ("LLM", "bogus"):
        monkeypatch.setenv(SATISFACTION_ENV, bad)
        with pytest.raises(ValueError, match="expected 'off' or 'llm'"):
            resolve_assessor()
    with pytest.raises(ValueError, match=SATISFACTION_ENV):
        resolve_assessor("on")


def test_llm_assessor_resolves_bridge_model(monkeypatch):
    monkeypatch.setenv("BRIDGE_MODEL", "gemini-test-model")
    assert LlmSatisfactionAssessor().model == "gemini-test-model"


# --------------------------------------------------------------------------- #
# Unit — the model-backed assessor (JSON double)                               #
# --------------------------------------------------------------------------- #
async def test_llm_assessor_request_shape():
    llm = JsonLlm(
        reply=json.dumps(
            {
                "items": [
                    {
                        "item": "proof of address",
                        "status": "required",
                        "doctype_hint": "utility-bill",
                        "message": "a second bill from a different company",
                    }
                ]
            }
        )
    )
    entries = [
        _entry(
            "bill-1",
            "utility-bill",
            issuer="power-co",
            key_fields={"name": "Jordan Lee", "service_address": "14 Elm Row"},
        ),
        _entry("bill-2", "utility-bill", Disposition.rejected, issuer="aqua-util"),
    ]
    items = await LlmSatisfactionAssessor(model=llm).assess(
        rule=RULE, entries=entries, candidate_doctypes=LABELS
    )
    assert items == [
        ProposedItem(
            item="proof of address",
            status="required",
            doctype_hint="utility-bill",
            message="a second bill from a different company",
        )
    ]
    assert len(llm.requests) == 1
    req = llm.requests[0]
    assert req.config.response_mime_type == "application/json"
    assert req.config.response_schema is not None
    assert req.config.system_instruction == ASSESSOR_INSTRUCTION
    text = "".join(p.text or "" for c in req.contents for p in c.parts or [])
    payload = json.loads(text)
    assert payload["satisfaction"] == RULE
    assert payload["candidate_doctypes"] == LABELS
    assert [
        (e["doc_id"], e["doctype"], e["disposition"], e["issuer"])
        for e in payload["ledger"]
    ] == [
        ("bill-1", "utility-bill", "accepted", "power-co"),
        ("bill-2", "utility-bill", "rejected", "aqua-util"),
    ]
    # Party data minimization: key fields never reach the assessor model.
    assert "Jordan Lee" not in text
    assert "14 Elm Row" not in text
    assert "key_fields" not in text


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param("not json", id="not-json"),
        pytest.param('{"items": [{"item": "x", "status": "waived"}]}', id="bad-status"),
        pytest.param('{"other": 1}', id="wrong-shape"),
        pytest.param("", id="empty-text"),
    ],
)
async def test_llm_assessor_malformed_json_falls_back(reply):
    llm = JsonLlm(reply=reply)
    entries = [_entry("b", "utility-bill", Disposition.rejected)]
    out = await propose_outstanding(
        entries,
        rule=RULE,
        candidate_doctypes=LABELS,
        assessor=LlmSatisfactionAssessor(model=llm),
    )
    assert out == project_outstanding(entries)
    assert len(llm.requests) == 1


# --------------------------------------------------------------------------- #
# Skills                                                                       #
# --------------------------------------------------------------------------- #
def test_skill_satisfaction_read():
    meta = skill_meta_from_dir(SKILLS / PROCESS)
    assert meta.satisfaction
    assert "distinct issuers" in meta.satisfaction
    assert satisfaction_for(PROCESS) == meta.satisfaction
    assert satisfaction_for("no-such-process") == ""
    assert skill_meta_from_dir(SKILLS / "gov-id").satisfaction == ""


# --------------------------------------------------------------------------- #
# Edge — in-process A2A                                                        #
# --------------------------------------------------------------------------- #
def _app(*, satisfaction=None, mode: str = "deterministic", llm=None):
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    leg = LlmLeg(model=llm) if mode == "hybrid" else None
    return build_a2a_app(
        url=URL,
        executor=executor,
        edge_mode=mode,
        llm_leg=leg,
        satisfaction=satisfaction,
    )


@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        yield await ClientFactory(config).create_from_url(URL)


def _url_doc_part(doc_id: str, uri: str):
    part = ph.new_url_part(uri, media_type="image/svg+xml")
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    return part


def _collect_message(docs) -> Message:
    parts = encode_collect_request(CollectRequest(process=PROCESS))
    parts += [_url_doc_part(doc_id, uri) for doc_id, uri in docs]
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


def _path_a_message() -> Message:
    parts = encode_collect_request(CollectRequest(process=PROCESS))
    parts.append(
        ph.new_data_part(
            {
                "fields": {
                    "doctype": "gov-id",
                    "key_fields": {
                        "name": "Jordan Lee",
                        "address": "14 Elm Row, Springfield",
                        "doc_number": "DL-8392047",
                        "issuing_authority": "State DMV",
                        "expiry": "2030-01-01",
                    },
                },
                "overall_confidence": 0.96,
                "legible": True,
                "flagged_fields": [],
            }
        )
    )
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


def _turn(task) -> ExchangeTurn:
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return ExchangeTurn.model_validate(MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact")


async def _run(app, message: Message):
    """message/send → (task, turn, final status text)."""
    async with _client(app) as client:
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=message)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id
        assert task_id is not None
        task = await client.get_task(GetTaskRequest(id=task_id))
    text = " ".join(ph.get_text_parts(task.status.message.parts))
    return task, _turn(task), text


def _sense_a(turn: ExchangeTurn) -> list[dict]:
    """Ledger entries minus the per-run artifact URI (context-scoped)."""
    return [e.model_dump(exclude={"artifact"}) for e in turn.status.entries]


async def test_default_edge_is_unchanged_and_model_free():
    task, turn, text = await _run(_app(), _collect_message([GOV_ID, BLURRY]))
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert turn.status.outstanding == project_outstanding(turn.status.entries)
    assert text == summarize_turn(turn) == "collected 2 documents; 1 outstanding"


async def test_proposal_rides_the_turn():
    fake = FakeAssessor([REQUIRED_BILL])
    _task0, baseline, _ = await _run(_app(), _collect_message([POWERCO]))
    task, turn, text = await _run(_app(satisfaction=fake), _collect_message([POWERCO]))

    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert turn.terminal is True
    out = turn.status.outstanding
    assert out is not None and out.done is False
    assert [r.reason_code for r in out.requirements] == [PROPOSAL_REASON_CODE]
    assert out.requirements[0].status is RequirementStatus.required
    assert out.requirements[0].doctype_hint == "utility-bill"
    # Advisory never changes sense A: entries/dispositions/gates are identical.
    assert _sense_a(turn) == _sense_a(baseline)
    assert [e.disposition for e in turn.status.entries] == [Disposition.accepted]
    # The deterministic run had nothing outstanding (one accepted bill).
    assert baseline.status.outstanding.requirements == []
    # One call with the skill's prose and the Bridge-decided label space.
    assert len(fake.calls) == 1
    assert fake.calls[0]["rule"] == satisfaction_for(PROCESS)
    assert "distinct issuers" in fake.calls[0]["rule"]
    assert fake.calls[0]["candidate_doctypes"] == LABELS
    assert text == "collected 1 document; 1 outstanding"


async def test_app_can_always_override():
    """A "looks done" proposal on two same-issuer bills: the app's gate says no."""
    fake = FakeAssessor([SATISFIED])
    task, turn, text = await _run(
        _app(satisfaction=fake), _collect_message([POWERCO, POWERCO_2])
    )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert {e.issuer for e in turn.status.entries} == {"power-co"}
    assert looks_done(turn.status.outstanding) is True
    assert turn.status.outstanding.done is False
    assert is_satisfied(turn.status) is False
    # A satisfied item is not "outstanding" in the Bridge-authored summary.
    assert text == "collected 2 documents; 0 outstanding"


async def test_assessor_failure_keeps_per_entry_on_the_wire():
    fake = FakeAssessor(exc=RuntimeError("model down"))
    task, turn, _ = await _run(
        _app(satisfaction=fake), _collect_message([GOV_ID, BLURRY])
    )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert turn.status.outstanding == project_outstanding(turn.status.entries)
    assert len(fake.calls) == 1


def test_invalid_env_fails_app_start(monkeypatch):
    monkeypatch.setenv(SATISFACTION_ENV, "bogus")
    with pytest.raises(ValueError, match="expected 'off' or 'llm'"):
        build_a2a_app(url=URL, executor=CollectExecutor())


def test_llm_env_binds_the_model_assessor_without_network(monkeypatch):
    monkeypatch.setenv(SATISFACTION_ENV, "llm")
    executor = CollectExecutor()
    build_a2a_app(url=URL, executor=executor)
    assert isinstance(executor.assessor, LlmSatisfactionAssessor)


def test_bare_executor_has_no_assessor():
    assert CollectExecutor().assessor is None


async def test_path_a_turn_gets_proposal():
    fake = FakeAssessor([SATISFIED])
    task, turn, _ = await _run(_app(satisfaction=fake), _path_a_message())
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert [e.disposition for e in turn.status.entries] == [Disposition.accepted]
    out = turn.status.outstanding
    assert [r.reason_code for r in out.requirements] == [PROPOSAL_REASON_CODE]
    assert looks_done(out) is True and out.done is False
    assert len(fake.calls) == 1
    assert fake.calls[0]["candidate_doctypes"] == LABELS


async def test_hybrid_structured_traffic_still_zero_leg_calls():
    """The assessor is not the leg: structured traffic never reaches the LLM leg."""
    llm = ScriptedLlm(max_calls=0)
    fake = FakeAssessor([REQUIRED_BILL])
    task, turn, _ = await _run(
        _app(satisfaction=fake, mode="hybrid", llm=llm), _collect_message([POWERCO])
    )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert turn.status.outstanding.requirements[0].reason_code == PROPOSAL_REASON_CODE
    assert len(fake.calls) == 1
    assert llm.call_count == 0


# --------------------------------------------------------------------------- #
# Live (real Gemini) — gated                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("BRIDGE_TEST_GEMINI") != "1",
    reason="set BRIDGE_TEST_GEMINI=1 (+ Vertex creds) to run the live assessor",
)
async def test_live_assessor_reads_the_address_prose():
    rule = satisfaction_for(PROCESS)
    assessor = LlmSatisfactionAssessor()

    async def propose(entries):
        return await propose_outstanding(
            entries, rule=rule, candidate_doctypes=LABELS, assessor=assessor
        )

    gov = await propose([_entry("gov-id-clean", "gov-id")])
    assert all(r.reason_code == PROPOSAL_REASON_CODE for r in gov.requirements)
    assert looks_done(gov) is True

    one_bill = await propose([_entry("bill-1", "utility-bill", issuer="power-co")])
    required = [
        r for r in one_bill.requirements if r.status is RequirementStatus.required
    ]
    assert required
    assert all(r.doctype_hint in ("utility-bill", None) for r in required)

    same_issuer = await propose(
        [
            _entry("bill-1", "utility-bill", issuer="power-co"),
            _entry("bill-2", "utility-bill", issuer="power-co"),
        ]
    )
    assert looks_done(same_issuer) is False

    distinct = await propose(
        [
            _entry("bill-1", "utility-bill", issuer="power-co"),
            _entry("bill-2", "utility-bill", issuer="aqua-util"),
        ]
    )
    assert looks_done(distinct) is True
    for out in (gov, one_bill, same_issuer, distinct):
        assert out.done is False
