"""HITL park/resume agent-to-agent — the G / R3 / R6 owning test (S3-edge-6).

Driven through the conformant in-process ``a2a-sdk`` client on a **hybrid** edge whose
LLM leg runs on the model-free ``ScriptedLlm``:

- **R3 park:** a Collector ``hitl_review`` verdict under ``address-proof`` (skill
  ``bridge-hitl: park``) is handed to the leg, which calls ``request_human_review``;
  the LongRunningFunctionTool pauses → INPUT_REQUIRED with ``reason_code`` +
  ``review_call_id``, zero compute while parked.
- **G:** any other message on the parked task is refused pre-dispatch (the task is
  unchanged, the model never called, no Runner built).
- **R6 resume:** an authorized reviewer's id-matched ``HumanReviewResolution``
  resumes the paused invocation — across a full rebuild from the same sqlite DSNs —
  and the task reaches COMPLETED on the same task/context ids (the corpus timeline's
  ``hitl_resolved: accepted`` tick for ``bill-aquautil-clear``).
- Refusals (mismatched / already-resolved / cross-task call id → -32602; a
  non-reviewer → ``reviewer_not_authorized``) never change the task.

The reviewer principal comes from a test-only header authentication backend defined
HERE (``X-Test-Principal``) — the edge ships no header-trusting backend. Synthetic
data only (the corpus party ``jordan-lee``).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from a2a.utils.errors import (
    JSON_RPC_ERROR_CODE_MAP,
    A2AError,
    InvalidParamsError,
    InvalidRequestError,
    TaskNotFoundError,
)
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    SimpleUser,
)

from bridge.collect import Collector, DocumentSubmission
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge import executor as executor_mod
from bridge.edge.executor import HITL_HANDOFF_TEXT, PAUSED_REVIEW_TEXT
from bridge.edge.llm_leg import (
    APP_NAME,
    PAUSED_REVIEW_MESSAGE,
    REVIEW_TOOL,
    LlmLeg,
)
from bridge.edge.skills import candidate_doctypes_for
from bridge.edge.wire import (
    KIND_EXCHANGE_TURN,
    KIND_HUMAN_REVIEW_RESOLUTION,
    META_CONTRACT_VERSION,
    META_KIND,
    META_REASON_CODE,
    META_REVIEW_CALL_ID,
    META_REVIEW_SESSION,
    encode_human_review_resolution,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import REASON_HUMAN_REVIEW
from bridge.seams import Seam, get_adapter
from contract.enums import Disposition, Gate
from contract.wire import CollectRequest, ExchangeTurn, HumanReviewResolution
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall

BASE = "http://test"
URL = "http://test/"
PARTY = "jordan-lee"  # synthetic
REVIEWER = "alice"
AQUA = ("bill-aquautil-clear", "images/bill-aquautil-clear.svg")
GOV = ("gov-id-expired", "images/gov-id-expired.svg")
POWER = ("bill-powerco-clean", "images/bill-powerco-clean.svg")
PRINCIPAL_HEADER = "X-Test-Principal"


# --------------------------------------------------------------------------- #
# Test-only authentication (never shipped in bridge/)                          #
# --------------------------------------------------------------------------- #
class _HeaderAuth(AuthenticationBackend):
    """``X-Test-Principal: <name>`` → an authenticated ``SimpleUser(<name>)``."""

    async def authenticate(self, conn):
        name = conn.headers.get(PRINCIPAL_HEADER)
        if not name:
            return None
        return AuthCredentials(["authenticated"]), SimpleUser(name)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _review_llm(*docs: str, **kwargs) -> ScriptedLlm:
    """A model that answers the R3 handoff by parking one review per doc id."""
    return ScriptedLlm(
        on_text={
            HITL_HANDOFF_TEXT: Reply(
                tool_calls=tuple(
                    ToolCall(
                        REVIEW_TOOL,
                        {"reason": f"{d} needs a person", "artifact_name": d},
                    )
                    for d in docs
                )
            )
        },
        **kwargs,
    )


def _app(
    llm: ScriptedLlm,
    *,
    session_service=None,
    task_store=None,
    edge_mode: str = "hybrid",
    reviewers=(REVIEWER,),
):
    leg = LlmLeg(model=llm, session_service=session_service or InMemorySessionService())
    app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        edge_mode=edge_mode,
        llm_leg=leg,
        task_store=task_store,
        reviewers=reviewers,
        auth_backend=_HeaderAuth(),
    )
    return app, leg


@asynccontextmanager
async def _client(app, principal: str | None = None):
    headers = {PRINCIPAL_HEADER: principal} if principal else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE, headers=headers
    ) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(URL)
        yield hc, client


def _message(parts, *, context_id: str = "", task_id: str = "") -> Message:
    return Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=parts,
        context_id=context_id,
        task_id=task_id,
    )


async def _send(client, parts, *, context_id: str = "", task_id: str = ""):
    """message/stream; return (events, task via tasks/get)."""
    events, tid = [], None
    msg = _message(parts, context_id=context_id, task_id=task_id)
    async for ev in client.send_message(SendMessageRequest(message=msg)):
        events.append(ev)
        if ev.HasField("task"):
            tid = ev.task.id
        elif ev.HasField("status_update"):
            tid = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            tid = ev.artifact_update.task_id
    assert tid is not None
    return events, await client.get_task(GetTaskRequest(id=tid))


async def _send_error(client, parts, *, context_id: str, task_id: str) -> A2AError:
    """message/stream that must be refused; return the raised A2A error."""
    with pytest.raises(A2AError) as info:
        await _send(client, parts, context_id=context_id, task_id=task_id)
    return info.value


def _submission(docs, *, ctx: str, process: str = "address-proof"):
    parts = encode_collect_request(
        CollectRequest(process=process, party=PARTY, context_id=ctx)
    )
    for doc_id, uri in docs:
        part = ph.new_url_part(uri, media_type="image/svg+xml", filename=f"{doc_id}.svg")
        part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
        parts.append(part)
    return parts


def _resolution(
    call_id: str,
    *,
    resolution: str = "accepted",
    message: str = "Reviewed by a person: the AquaUtil bill is acceptable.",
    reviewer: str | None = REVIEWER,
):
    return [
        encode_human_review_resolution(
            HumanReviewResolution(
                call_id=call_id,
                resolution=resolution,
                message=message,
                reviewer=reviewer,
            )
        )
    ]


def _turn(task) -> ExchangeTurn:
    """The LAST exchange_turn data part (select by metadata, never by index)."""
    found = None
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                found = ExchangeTurn.model_validate(MessageToDict(part.data))
    assert found is not None, "no exchange_turn artifact data part found"
    return found


def _meta(msg) -> dict:
    return MessageToDict(msg.metadata) if msg.HasField("metadata") else {}


def _entry(task, doc_id: str):
    return next(e for e in _turn(task).status.entries if e.doc_id == doc_id)


def _states(events):
    return [e.status_update.status.state for e in events if e.HasField("status_update")]


def _code(err: A2AError) -> int:
    return JSON_RPC_ERROR_CODE_MAP[type(err)]


async def _park(client, docs=(AQUA,), *, ctx: str):
    events, task = await _send(client, _submission(docs, ctx=ctx), context_id=ctx)
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED, task.status
    return events, task


async def _collector_reason(doc) -> str:
    """The Collector's own reason code for ``doc`` (the report-mode verdict)."""
    doc_id, uri = doc
    turn = await Collector(FixtureExtractor()).collect(
        CollectRequest(process="address-proof", party=PARTY, context_id="ref"),
        documents=[DocumentSubmission(doc_id=doc_id, artifact_name=uri)],
        candidate_doctypes=candidate_doctypes_for("address-proof"),
    )
    (entry,) = turn.status.entries
    assert entry.gate is Gate.hitl_review
    return entry.reason_code


# --------------------------------------------------------------------------- #
# R3 park + G zero compute                                                     #
# --------------------------------------------------------------------------- #
def test_paused_text_constant_matches_leg():
    assert PAUSED_REVIEW_TEXT == PAUSED_REVIEW_MESSAGE


async def test_hitl_park_input_required_with_call_id():
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        events, task = await _park(client, ctx="ctx-park")

    status_meta = _meta(task.status.message)
    assert status_meta[META_REASON_CODE] == REASON_HUMAN_REVIEW == "awaiting_human_review"
    call_id = status_meta[META_REVIEW_CALL_ID]
    assert call_id
    assert ph.get_message_text(task.status.message) == PAUSED_REVIEW_MESSAGE
    # server-authored task metadata scopes the review to THIS task
    session = MessageToDict(task.metadata)[META_REVIEW_SESSION]
    assert session == {"user_id": PARTY, "session_id": "ctx-park", "call_ids": [call_id]}

    turn = _turn(task)
    assert turn.terminal is False
    entry = _entry(task, AQUA[0])
    assert entry.disposition is Disposition.pending
    assert entry.gate is Gate.hitl_review
    assert entry.reason_code == REASON_HUMAN_REVIEW

    states = _states(events)
    assert states[-2:] == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_INPUT_REQUIRED]
    final = events[-1].status_update.status
    assert _meta(final.message)[META_REVIEW_CALL_ID] == call_id
    # D6: model text never reaches the wire
    assert llm.default_text not in turn.model_dump_json()
    assert llm.call_count == 1


async def test_parked_task_zero_compute(monkeypatch):
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-zero")
        calls = llm.call_count

        def _no_runner(self):
            raise AssertionError("a Runner was built while the task is parked")

        monkeypatch.setattr(LlmLeg, "runner", _no_runner)
        before = await client.get_task(GetTaskRequest(id=task.id))
        again = await client.get_task(GetTaskRequest(id=task.id))
        assert again == before
        err = await _send_error(
            client,
            [ph.new_text_part("hello? can I add another document")],
            context_id=task.context_id,
            task_id=task.id,
        )
        assert isinstance(err, InvalidRequestError)
        assert "awaiting_human_review" in str(err)
        after = await client.get_task(GetTaskRequest(id=task.id))
    assert after == before  # G refusal: the task is byte-for-byte unchanged
    assert after.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert llm.call_count == calls


# --------------------------------------------------------------------------- #
# R6 resume across a full rebuild (durable sessions + tasks)                   #
# --------------------------------------------------------------------------- #
async def test_resume_after_rebuild_reaches_timeline_resolution(tmp_path):
    session_dsn = f"sqlite+aiosqlite:///{tmp_path}/sessions.db"
    task_dsn = f"sqlite+aiosqlite:///{tmp_path}/tasks.db"
    ctx = "ctx-rebuild"
    reviewer_message = "Reviewed by a person: the AquaUtil bill is acceptable."

    task_store = get_adapter(Seam.task_store, "local").build_task_store(db_url=task_dsn)
    llm = _review_llm(AQUA[0])
    sessions = DatabaseSessionService(db_url=session_dsn)
    app, _leg = _app(llm, session_service=sessions, task_store=task_store)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, parked = await _park(client, ctx=ctx)
    call_id = _meta(parked.status.message)[META_REVIEW_CALL_ID]
    await task_store.engine.dispose()
    await sessions.db_engine.dispose()
    del app, _leg, llm, task_store, sessions  # the restart boundary: nothing survives

    task_store2 = get_adapter(Seam.task_store, "local").build_task_store(db_url=task_dsn)
    sessions2 = DatabaseSessionService(db_url=session_dsn)
    llm2 = _review_llm(AQUA[0])
    app2, _leg2 = _app(llm2, session_service=sessions2, task_store=task_store2)
    try:
        async with _client(app2, REVIEWER) as (_hc, client):
            restored = await client.get_task(GetTaskRequest(id=parked.id))
            assert restored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            # DatabaseTaskStore round-trips the server-authored task metadata
            assert MessageToDict(restored.metadata)[META_REVIEW_SESSION]["call_ids"] == [
                call_id
            ]
            events, task = await _send(
                client,
                _resolution(call_id, message=reviewer_message),
                context_id=parked.context_id,
                task_id=parked.id,
            )
    finally:
        await task_store2.engine.dispose()

    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task.id == parked.id and task.context_id == parked.context_id  # D8
    assert ph.get_message_text(task.status.message) == reviewer_message
    original_reason = await _collector_reason(AQUA)
    assert _meta(task.status.message)[META_REASON_CODE] == original_reason
    turn = _turn(task)
    assert turn.terminal is True
    entry = _entry(task, AQUA[0])
    assert entry.disposition is Disposition.accepted
    assert entry.gate is Gate.hitl_review
    assert entry.reason_code == original_reason
    assert entry.message == reviewer_message
    # the resumed turn REPLACED the parked one (one exchange_turn artifact)
    assert [a.name for a in task.artifacts].count("exchange_turn") == 1
    assert _states(events)[-2:] == [
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    ]
    assert llm2.call_count == 1  # exactly the post-resume model call

    # D4: the resume continued the PARKED invocation (same invocation_id)
    session = await sessions2.get_session(
        app_name=APP_NAME, user_id=PARTY, session_id=ctx
    )
    call_event = next(
        e for e in session.events
        if e.long_running_tool_ids and call_id in e.long_running_tool_ids
    )
    idx = session.events.index(call_event)
    resumed = session.events[idx + 1:]
    assert resumed, "no events after the parked call"
    assert all(e.invocation_id == call_event.invocation_id for e in resumed)
    assert any(
        p.function_response is not None and p.function_response.id == call_id
        for e in resumed if e.content for p in e.content.parts or []
    )
    await sessions2.db_engine.dispose()


# --------------------------------------------------------------------------- #
# Refusals — the task never changes                                            #
# --------------------------------------------------------------------------- #
async def test_resume_mismatched_call_id_refused():
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (hc, client):
        _events, task = await _park(client, ctx="ctx-mismatch")
        calls = llm.call_count
        before = await client.get_task(GetTaskRequest(id=task.id))
        err = await _send_error(
            client, _resolution("not-a-real-call-id"),
            context_id=task.context_id, task_id=task.id,
        )
        assert isinstance(err, InvalidParamsError)
        assert _code(err) == -32602
        assert "review_call_id_mismatch" in str(err)

        # the same refusal over a raw JSON-RPC post: the literal error code
        msg = _message(
            _resolution("not-a-real-call-id"), context_id=task.context_id,
            task_id=task.id,
        )
        resp = await hc.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "raw-1",
                "method": "SendMessage",
                "params": MessageToDict(SendMessageRequest(message=msg)),
            },
            headers={"A2A-Version": "1.0"},
        )
        body = resp.json()
        assert body["error"]["code"] == -32602, body
        assert "review_call_id_mismatch" in body["error"]["message"]

        after = await client.get_task(GetTaskRequest(id=task.id))
    assert after == before
    assert llm.call_count == calls


async def test_resume_malformed_resolution_refused():
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-malformed")
        before = await client.get_task(GetTaskRequest(id=task.id))
        bad = ph.new_data_part({"call_id": "x", "resolution": "maybe", "message": "m"})
        bad.metadata.CopyFrom(
            ParseDict(
                {META_KIND: KIND_HUMAN_REVIEW_RESOLUTION, META_CONTRACT_VERSION: "1.4"},
                struct_pb2.Struct(),
            )
        )
        err = await _send_error(
            client, [bad], context_id=task.context_id, task_id=task.id
        )
        assert isinstance(err, InvalidParamsError)
        assert "malformed_human_review_resolution" in str(err)
        assert await client.get_task(GetTaskRequest(id=task.id)) == before


async def test_resume_already_resolved_id_refused():
    """Two reviews keep the task INPUT_REQUIRED, so re-sending the FIRST (now
    answered) id hits R6's pending check specifically."""
    llm = _review_llm(GOV[0], AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, (GOV, AQUA), ctx="ctx-resolved")
        first = _meta(task.status.message)[META_REVIEW_CALL_ID]
        _events, task = await _send(
            client, _resolution(first, message="ID checked by a person."),
            context_id=task.context_id, task_id=task.id,
        )
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        calls = llm.call_count
        before = await client.get_task(GetTaskRequest(id=task.id))
        err = await _send_error(
            client, _resolution(first), context_id=task.context_id, task_id=task.id
        )
        assert isinstance(err, InvalidParamsError)
        assert _code(err) == -32602
        assert await client.get_task(GetTaskRequest(id=task.id)) == before
    assert llm.call_count == calls


async def test_resume_on_completed_task_refused():
    """After the only review resolves the task is COMPLETED: re-sending the id is
    refused (R5 / terminal-task rejection) and the task is unchanged."""
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-done")
        call_id = _meta(task.status.message)[META_REVIEW_CALL_ID]
        _events, task = await _send(
            client, _resolution(call_id), context_id=task.context_id, task_id=task.id
        )
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        calls = llm.call_count
        err = await _send_error(
            client, _resolution(call_id), context_id=task.context_id, task_id=task.id
        )
        assert isinstance(err, InvalidRequestError)
        assert "no_pending_review" in str(err)
        assert await client.get_task(GetTaskRequest(id=task.id)) == task
    assert llm.call_count == calls


async def test_resume_cross_task_call_id_refused():
    ctx = "ctx-cross"
    llm = _review_llm(AQUA[0], GOV[0])  # each submission parks its own doc
    llm.on_text = {
        f"{AQUA[0]} (": _review_llm(AQUA[0]).on_text[HITL_HANDOFF_TEXT],
        f"{GOV[0]} (": _review_llm(GOV[0]).on_text[HITL_HANDOFF_TEXT],
    }
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _e, task_a = await _park(client, (AQUA,), ctx=ctx)
        _e, task_b = await _park(client, (GOV,), ctx=ctx)
        assert task_a.id != task_b.id
        call_a = _meta(task_a.status.message)[META_REVIEW_CALL_ID]
        call_b = _meta(task_b.status.message)[META_REVIEW_CALL_ID]
        assert call_a != call_b
        calls = llm.call_count
        before = await client.get_task(GetTaskRequest(id=task_a.id))
        err = await _send_error(
            client, _resolution(call_b), context_id=ctx, task_id=task_a.id
        )
        assert isinstance(err, InvalidParamsError)
        assert _code(err) == -32602
        assert await client.get_task(GetTaskRequest(id=task_a.id)) == before
        assert llm.call_count == calls

        # each task still resolves with its OWN id
        _e, done_a = await _send(
            client, _resolution(call_a), context_id=ctx, task_id=task_a.id
        )
        assert done_a.status.state == TaskState.TASK_STATE_COMPLETED
        assert _entry(done_a, AQUA[0]).disposition is Disposition.accepted
        still_b = await client.get_task(GetTaskRequest(id=task_b.id))
        assert still_b.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        _e, done_b = await _send(
            client, _resolution(call_b, resolution="rejected", message="Expired ID."),
            context_id=ctx, task_id=task_b.id,
        )
        assert done_b.status.state == TaskState.TASK_STATE_COMPLETED
        assert _entry(done_b, GOV[0]).disposition is Disposition.rejected


@pytest.mark.parametrize(
    ("principal", "reviewers"),
    [
        (None, (REVIEWER,)),  # unauthenticated
        ("mallory", (REVIEWER,)),  # authenticated, not listed
        (REVIEWER, None),  # reviewers unset (env empty) → fail closed
    ],
    ids=["unauthenticated", "not-listed", "reviewers-unset"],
)
async def test_resume_non_reviewer_rejected(monkeypatch, principal, reviewers):
    monkeypatch.delenv("BRIDGE_EDGE_REVIEWERS", raising=False)
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm, reviewers=reviewers)
    async with _client(app, principal) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-authz")
    call_id = _meta(task.status.message)[META_REVIEW_CALL_ID]
    calls = llm.call_count
    async with _client(app, principal) as (_hc, client):
        before = await client.get_task(GetTaskRequest(id=task.id))
        # the payload's reviewer="alice" is an audit label — it never authorizes
        err = await _send_error(
            client, _resolution(call_id, reviewer=REVIEWER),
            context_id=task.context_id, task_id=task.id,
        )
        assert isinstance(err, InvalidRequestError)
        assert "reviewer_not_authorized" in str(err)
        # authorization precedes the id check: a wrong id leaks nothing either
        err = await _send_error(
            client, _resolution("wrong-id"), context_id=task.context_id,
            task_id=task.id,
        )
        assert isinstance(err, InvalidRequestError)
        assert "reviewer_not_authorized" in str(err)
        assert await client.get_task(GetTaskRequest(id=task.id)) == before
    assert llm.call_count == calls


async def test_resume_from_foreign_principal_cannot_see_task():
    """The task store scopes tasks by owner principal (a2a-sdk ``resolve_user_scope``):
    a listed reviewer who is NOT the task's owner cannot even see it — refused, the
    task unchanged. The resolving principal is the task owner's (the consumer agent
    relays its human reviewer's decision)."""
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm, reviewers=(REVIEWER, "bob"))
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-foreign")
    call_id = _meta(task.status.message)[META_REVIEW_CALL_ID]
    calls = llm.call_count
    async with _client(app, "bob") as (_hc, client):
        err = await _send_error(
            client, _resolution(call_id, reviewer="bob"),
            context_id=task.context_id, task_id=task.id,
        )
        assert isinstance(err, TaskNotFoundError)
    async with _client(app, REVIEWER) as (_hc, client):
        assert await client.get_task(GetTaskRequest(id=task.id)) == task
    assert llm.call_count == calls


# --------------------------------------------------------------------------- #
# Multi-review + rejected resolution                                           #
# --------------------------------------------------------------------------- #
async def test_two_reviews_input_required_then_completed():
    llm = _review_llm(GOV[0], AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, (GOV, AQUA), ctx="ctx-two")
        session = MessageToDict(task.metadata)[META_REVIEW_SESSION]
        first, second = session["call_ids"]
        assert _meta(task.status.message)[META_REVIEW_CALL_ID] == first
        assert {e.doc_id: e.disposition for e in _turn(task).status.entries} == {
            GOV[0]: Disposition.pending,
            AQUA[0]: Disposition.pending,
        }

        _events, task = await _send(
            client, _resolution(first, resolution="rejected", message="Expired ID."),
            context_id=task.context_id, task_id=task.id,
        )
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert _meta(task.status.message)[META_REVIEW_CALL_ID] == second
        assert _meta(task.status.message)[META_REASON_CODE] == REASON_HUMAN_REVIEW
        assert MessageToDict(task.metadata)[META_REVIEW_SESSION]["call_ids"] == [
            first,
            second,
        ]
        turn = _turn(task)
        assert turn.terminal is False
        assert _entry(task, GOV[0]).disposition is Disposition.rejected
        assert _entry(task, AQUA[0]).disposition is Disposition.pending

        _events, task = await _send(
            client, _resolution(second), context_id=task.context_id, task_id=task.id
        )
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert _turn(task).terminal is True
        assert _entry(task, GOV[0]).disposition is Disposition.rejected
        assert _entry(task, AQUA[0]).disposition is Disposition.accepted
        assert [a.name for a in task.artifacts].count("exchange_turn") == 1


async def test_rejected_resolution():
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-reject")
        call_id = _meta(task.status.message)[META_REVIEW_CALL_ID]
        _events, task = await _send(
            client,
            _resolution(call_id, resolution="rejected", message="Not a match."),
            context_id=task.context_id, task_id=task.id,
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    entry = _entry(task, AQUA[0])
    assert entry.disposition is Disposition.rejected
    assert entry.gate is Gate.hitl_review
    assert entry.message == "Not a match."
    assert ph.get_message_text(task.status.message) == "Not a match."


# --------------------------------------------------------------------------- #
# Never parks: report-mode skill, deterministic mode, model parks nothing      #
# --------------------------------------------------------------------------- #
async def test_report_mode_skill_no_handoff(monkeypatch):
    monkeypatch.setattr(executor_mod, "hitl_mode_for", lambda process: "report")
    llm = ScriptedLlm(max_calls=0)  # any model call fails loud
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        events, task = await _send(
            client, _submission((AQUA,), ctx="ctx-report"), context_id="ctx-report"
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert _entry(task, AQUA[0]).disposition is Disposition.pending
    assert TaskState.TASK_STATE_WORKING not in _states(events)
    assert META_REVIEW_SESSION not in MessageToDict(task.metadata)
    assert llm.call_count == 0


async def test_deterministic_mode_never_parks():
    llm = ScriptedLlm(max_calls=0)
    app, _leg = _app(llm, edge_mode="deterministic")
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _send(
            client, _submission((AQUA,), ctx="ctx-det"), context_id="ctx-det"
        )
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert _entry(task, AQUA[0]).disposition is Disposition.pending
        # a resolution part on a new task is not a resume in deterministic mode:
        # it rides the Collector path and is refused there, never reaching a model
        with pytest.raises(A2AError):
            await _send(client, _resolution("any-id"), context_id="ctx-det")
    assert llm.call_count == 0


async def test_model_parks_nothing_degrades_to_report():
    llm = ScriptedLlm(on_text={HITL_HANDOFF_TEXT: Reply(text="Looks fine to me!")})
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        _events, task = await _send(
            client, _submission((AQUA,), ctx="ctx-degrade"), context_id="ctx-degrade"
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    turn = _turn(task)
    assert turn.terminal is True
    entry = _entry(task, AQUA[0])
    assert entry.disposition is Disposition.pending
    assert entry.gate is Gate.hitl_review
    assert entry.reason_code == await _collector_reason(AQUA)
    assert "Looks fine" not in turn.model_dump_json()
    assert "Looks fine" not in ph.get_message_text(task.status.message)
    assert llm.call_count == 1


async def test_auto_approved_submission_never_hands_off():
    llm = ScriptedLlm(max_calls=0)
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        _events, task = await _send(
            client, _submission((POWER,), ctx="ctx-clean"), context_id="ctx-clean"
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert _entry(task, POWER[0]).disposition is Disposition.accepted
    assert llm.call_count == 0


async def test_resolution_without_pending_review_refused_pre_dispatch():
    """R5 on a new task: refused before dispatch — no task is created."""
    llm = ScriptedLlm(max_calls=0)
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        err = await _send_error(
            client, _resolution("any-id"), context_id="ctx-r5", task_id=""
        )
    assert isinstance(err, InvalidRequestError)
    assert "no_pending_review" in str(err)
    assert llm.call_count == 0


# --------------------------------------------------------------------------- #
# Tolerant reader over the wire                                                #
# --------------------------------------------------------------------------- #
async def test_resolution_tolerant_decode_over_wire():
    llm = _review_llm(AQUA[0])
    app, _leg = _app(llm)
    async with _client(app, REVIEWER) as (_hc, client):
        _events, task = await _park(client, ctx="ctx-tolerant")
        call_id = _meta(task.status.message)[META_REVIEW_CALL_ID]
        part = ph.new_data_part(
            {
                "contract_version": "1.5",
                "call_id": call_id,
                "resolution": "accepted",
                "message": "OK from a newer reviewer console.",
                "reviewer": REVIEWER,
                "review_tool_build": "2027.1",  # unknown to a 1.4 reader
            }
        )
        part.metadata.CopyFrom(
            ParseDict(
                {META_KIND: KIND_HUMAN_REVIEW_RESOLUTION, META_CONTRACT_VERSION: "1.5"},
                struct_pb2.Struct(),
            )
        )
        _events, task = await _send(
            client, [part], context_id=task.context_id, task_id=task.id
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert _entry(task, AQUA[0]).disposition is Disposition.accepted
