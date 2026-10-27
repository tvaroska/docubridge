"""Off-script outcomes over A2A — the R4 owning test (S3-edge-1 route table).

Every D6 off-script row driven through the conformant in-process ``a2a-sdk`` client
on a **hybrid** edge whose LLM leg runs on the model-free ``ScriptedLlm``:
deletion (``deletion_completed`` / ``nothing_to_delete``), off-script reject
(``off_script_rejected``), escalate (``escalated_for_review`` on a non-terminal
INPUT_REQUIRED task that survives ``tasks/get`` and a durable task-store restart)
and model-text-only (``off_script_unrouted``). The reason code rides
``status.message.metadata["reason_code"]`` (D6 — envelope extension space).

This is the CUJ-14 Driver. Judge: a real erase → the scoped fetch endpoint 404s on
every version. Traps: a soft-delete (a flag, bytes still served), or the LLM
disposing of a genuine document; the double's false "accepted" text never reaches
the wire. Erasure is confined to the leg (addressing = isolation,
``design/zones.md``): another context's or party's artifact, an ambiguous context,
a ``user:``-namespaced name and traversal-looking names never erase.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    TaskState,
)
from a2a.utils.constants import TransportProtocol
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import Collector, DocumentSubmission, project_collection_status
from bridge.edge import (
    CollectExecutor,
    artifacts,
    build_a2a_app,
    encode_collect_request,
)
from bridge.edge.artifacts import APP_NAME, UNSCOPED_USER
from bridge.edge.executor import LLM_UNROUTED_TEXT, _text_message, llm_status
from bridge.edge.llm_leg import LlmLeg, LlmProjection
from bridge.edge.wire import (
    KIND_EXCHANGE_TURN,
    META_KIND,
    META_REASON_CODE,
    summarize_turn,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import (
    REASON_DELETED,
    REASON_ESCALATED,
    REASON_NOTHING_TO_DELETE,
    REASON_OFF_SCRIPT_REJECT,
    REASON_UNROUTED,
    escalate_submission,
    reject_submission,
)
from bridge.ports import Port, get_adapter
from contract.enums import Disposition, Gate, LifecycleStatus
from contract.wire import ArtifactRef, CollectRequest, ExchangeTurn, LedgerEntry
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall

BASE = "http://test"
URL = "http://test/"
BILL = ("bill-powerco-clean", "images/bill-powerco-clean.svg")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _app(llm: ScriptedLlm, *, task_store=None, edge_mode: str = "hybrid"):
    """Edge app on the fixture extractor + an LLM leg on ``llm``; returns (app, leg).

    After ``build_a2a_app`` the leg's ``artifact_store`` is the edge's SHARED store
    (writer == reader == eraser).
    """
    leg = LlmLeg(model=llm, session_service=InMemorySessionService())
    app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        edge_mode=edge_mode,
        llm_leg=leg,
        task_store=task_store,
    )
    return app, leg


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


async def _send(client, parts, *, context_id: str = "", task_id: str = ""):
    """message/send (streamed); return (events, task via tasks/get)."""
    msg = Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=parts,
        context_id=context_id,
        task_id=task_id,
    )
    events, tid = [], None
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


async def _say(client, text: str, *, context_id: str = "", task_id: str = ""):
    return await _send(
        client, [ph.new_text_part(text)], context_id=context_id, task_id=task_id
    )


async def _submit_bill(
    client, *, ctx: str, party: str, filename: str = "bill.pdf"
) -> tuple[str, object]:
    """A Collector submission of the corpus bill under ``filename``; returns
    (the entry's scoped fetch URI, the task)."""
    doc_id, uri = BILL
    parts = encode_collect_request(
        CollectRequest(process="address-proof", party=party, context_id=ctx)
    )
    part = ph.new_url_part(uri, media_type="image/svg+xml", filename=filename)
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    parts.append(part)
    _events, task = await _send(client, parts, context_id=ctx)
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    turn = _turn(task)
    assert turn.context_id == ctx
    (entry,) = turn.status.entries
    assert entry.artifact is not None and entry.artifact.uri
    return entry.artifact.uri, task


def _turn(task) -> ExchangeTurn:
    """Select the exchange_turn data part by metadata (never by index) + validate."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return ExchangeTurn.model_validate(MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact data part found")


def _reason(msg) -> str | None:
    """The status message's D6 ``reason_code`` metadata (``None`` when unset)."""
    if not msg.HasField("metadata"):
        return None
    return MessageToDict(msg.metadata).get(META_REASON_CODE)


def _status_updates(events):
    return [e.status_update.status for e in events if e.HasField("status_update")]


def _bare(uri: str) -> str:
    return uri.split("?", 1)[0]


async def _gets(hc, uri: str) -> dict[str, int]:
    """GET the bare scoped URI and each stored version; {suffix: status}."""
    out = {}
    for suffix in ("", "?v=0", "?v=1"):
        out[suffix] = (await hc.get(_bare(uri) + suffix)).status_code
    return out


async def _versions(store, *, party: str, ctx: str, filename: str = "bill.pdf"):
    return await store.list_versions(
        app_name=APP_NAME, user_id=party, session_id=ctx, filename=filename
    )


def _delete_llm(name: str) -> ScriptedLlm:
    return ScriptedLlm(
        on_text={
            "delete": Reply(
                tool_calls=(
                    ToolCall("handle_deletion_request", {"artifact_name": name}),
                )
            )
        }
    )


def _assert_llm_outcome(events, task, *, state, reason, text, llm: ScriptedLlm):
    """The D6 wire shape of an R4 outcome, on ``tasks/get`` AND on the stream."""
    assert task.status.state == state
    assert ph.get_message_text(task.status.message) == text
    assert _reason(task.status.message) == reason

    statuses = _status_updates(events)
    states = [s.state for s in statuses]
    assert TaskState.TASK_STATE_WORKING in states  # D7: the LLM leg streams WORKING
    for s in statuses:
        if s.state == TaskState.TASK_STATE_WORKING:
            assert not s.message.HasField("metadata"), "WORKING carries no reason_code"
    final = statuses[-1]
    assert final.state == state
    assert ph.get_message_text(final.message) == text
    assert _reason(final.message) == reason

    # D6: model text is never relayed — status text (streamed + stored) or the turn.
    turn = _turn(task)
    for model_text in (llm.after_tool_text, llm.default_text):
        assert model_text not in ph.get_message_text(task.status.message)
        assert model_text not in turn.model_dump_json()
        for s in statuses:
            assert model_text not in ph.get_message_text(s.message)
    assert turn.context_id == task.context_id  # D9: the A2A context_id keys the turn
    return turn


# --------------------------------------------------------------------------- #
# Deletion — a real erase of every version                                     #
# --------------------------------------------------------------------------- #
async def test_delete_erases_all_versions_then_fetch_404():
    llm = _delete_llm("bill.pdf")
    app, leg = _app(llm)
    store = leg.artifact_store
    ctx = "ctx-del"
    async with _client(app) as (hc, client):
        uri0, first_task = await _submit_bill(client, ctx=ctx, party="jordan-lee")
        uri1, _ = await _submit_bill(client, ctx=ctx, party="jordan-lee")
        assert uri0.endswith("?v=0") and uri1.endswith("?v=1")
        first_turn = _turn(first_task)
        assert await _gets(hc, uri1) == {"": 200, "?v=0": 200, "?v=1": 200}
        assert await _versions(store, party="jordan-lee", ctx=ctx) == [0, 1]

        events, task = await _say(client, "please delete bill.pdf", context_id=ctx)
        turn = _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            reason=REASON_DELETED,
            text="The document 'bill.pdf' was permanently erased.",
            llm=llm,
        )
        assert turn.status.entries == []  # erasure is not a disposition
        assert turn.terminal is True
        assert turn.lifecycle is LifecycleStatus.on_track

        # The judge: a real erase → every version 404s (never a soft flag).
        assert await _gets(hc, uri1) == {"": 404, "?v=0": 404, "?v=1": 404}
        assert await _versions(store, party="jordan-lee", ctx=ctx) == []
        assert (
            await store.list_artifact_keys(
                app_name=APP_NAME, user_id="jordan-lee", session_id=ctx
            )
            == []
        )
        # D9: the session user is the context's owning party, resolved from the store.
        assert (
            await leg.session_service.get_session(
                app_name=APP_NAME, user_id="jordan-lee", session_id=ctx
            )
            is not None
        )

        # Deletion does not rewrite history: only the bytes are gone.
        again = await client.get_task(GetTaskRequest(id=first_task.id))
        assert again.status.state == TaskState.TASK_STATE_COMPLETED
        assert _turn(again) == first_turn
    assert llm.call_count >= 1


async def test_delete_missing_file_nothing_to_delete():
    llm = _delete_llm("missing.pdf")
    app, _leg = _app(llm)
    async with _client(app) as (hc, client):
        uri, _ = await _submit_bill(client, ctx="ctx-miss", party="jordan-lee")
        events, task = await _say(client, "delete missing.pdf", context_id="ctx-miss")
        turn = _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            reason=REASON_NOTHING_TO_DELETE,
            text="No document named 'missing.pdf' was found to erase.",
            llm=llm,
        )
        assert turn.status.entries == []
        assert turn.terminal is True
        assert (await hc.get(uri)).status_code == 200  # the other file is untouched


async def test_delete_in_empty_context_nothing_to_delete_unscoped():
    llm = _delete_llm("bill.pdf")
    app, leg = _app(llm)
    async with _client(app) as (_hc, client):
        events, task = await _say(client, "delete bill.pdf", context_id="ctx-empty")
        _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            reason=REASON_NOTHING_TO_DELETE,
            text="No document named 'bill.pdf' was found to erase.",
            llm=llm,
        )
    # No owner → the unscoped session user (D9), under which nothing foreign exists.
    assert (
        await leg.session_service.get_session(
            app_name=APP_NAME, user_id=UNSCOPED_USER, session_id="ctx-empty"
        )
        is not None
    )


# --------------------------------------------------------------------------- #
# Scoping — addressing = isolation (design/zones.md)                           #
# --------------------------------------------------------------------------- #
async def test_cannot_erase_another_contexts_or_partys_artifact():
    llm = _delete_llm("bill.pdf")
    app, leg = _app(llm)
    store = leg.artifact_store
    async with _client(app) as (hc, client):
        alice_uri, _ = await _submit_bill(client, ctx="ctx-a", party="alice")
        bob_uri, _ = await _submit_bill(client, ctx="ctx-b", party="bob")

        events, task = await _say(client, "delete bill.pdf", context_id="ctx-b")
        _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            reason=REASON_DELETED,
            text="The document 'bill.pdf' was permanently erased.",
            llm=llm,
        )
        assert (await hc.get(bob_uri)).status_code == 404
        assert (await hc.get(alice_uri)).status_code == 200
        assert await _versions(store, party="alice", ctx="ctx-a") == [0]

        # A context nobody owns erases nothing — alice's same-named file stays.
        events, task = await _say(client, "delete bill.pdf", context_id="ctx-c")
        _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            reason=REASON_NOTHING_TO_DELETE,
            text="No document named 'bill.pdf' was found to erase.",
            llm=llm,
        )
        assert (await hc.get(alice_uri)).status_code == 200
        assert await _versions(store, party="alice", ctx="ctx-a") == [0]


async def test_ambiguous_context_fails_closed():
    """D9: a context owned by >1 party resolves to the unscoped user — never guess."""
    llm = _delete_llm("bill.pdf")
    app, leg = _app(llm)
    store = leg.artifact_store
    async with _client(app) as (_hc, client):
        await _submit_bill(client, ctx="ctx-x", party="alice")
        await _submit_bill(client, ctx="ctx-x", party="bob")
        events, task = await _say(client, "delete bill.pdf", context_id="ctx-x")
        _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            reason=REASON_NOTHING_TO_DELETE,
            text="No document named 'bill.pdf' was found to erase.",
            llm=llm,
        )
    # Checked through the store: the fetch endpoint serves whichever party it finds.
    assert await _versions(store, party="alice", ctx="ctx-x") == [0]
    assert await _versions(store, party="bob", ctx="ctx-x") == [0]


@pytest.mark.parametrize(
    "name", ["user:bill.pdf", "../ctx-A/user:bill.pdf", "../ctx-A/other.pdf"]
)
async def test_user_namespace_and_traversal_names_never_erase_across_contexts(name):
    """A ``user:`` name lands in ADK's party-wide namespace (listed under EVERY
    session of the party); it must never be erasable from another context."""
    store = InMemoryArtifactService()
    for ctx, filename in (
        ("ctx-A", "user:bill.pdf"),
        ("ctx-A", "other.pdf"),
        ("ctx-B", "mine.pdf"),
    ):
        await artifacts.persist_submission(
            store,
            party="jordan-lee",
            context_id=ctx,
            submission=DocumentSubmission(
                doc_id="d",
                artifact_name=filename,
                artifact=ArtifactRef(uri=filename, name=filename),
            ),
            resolver=lambda _u: (b"x", "application/pdf"),
        )
    before = set(store.artifacts)
    # The precondition that made the hole: ctx-B's own listing includes the user: key.
    assert "user:bill.pdf" in await store.list_artifact_keys(
        app_name=APP_NAME, user_id="jordan-lee", session_id="ctx-B"
    )

    llm = _delete_llm(name)
    result = await LlmLeg(
        model=llm, artifact_store=store, session_service=InMemorySessionService()
    ).run(party=None, context_id="ctx-B", message="delete it")

    assert result.key.user_id == "jordan-lee"  # the party resolved; only scope saves us
    assert result.projection.reason_code == REASON_NOTHING_TO_DELETE
    assert set(store.artifacts) == before  # every document remains


# --------------------------------------------------------------------------- #
# Off-script reject / escalate / unrouted                                      #
# --------------------------------------------------------------------------- #
async def test_reject_submission_off_script_rejected():
    reason = "not an address document"
    llm = ScriptedLlm(
        on_text={
            "wrong file": Reply(
                tool_calls=(ToolCall("reject_submission", {"reason": reason}),)
            )
        }
    )
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        events, task = await _say(client, "here is the wrong file, a cat photo")
    turn = _assert_llm_outcome(
        events,
        task,
        state=TaskState.TASK_STATE_COMPLETED,
        reason=REASON_OFF_SCRIPT_REJECT,
        text=reject_submission(reason)["message"],
        llm=llm,
    )
    assert turn.status.entries == []  # never a model-minted `rejected` entry
    assert turn.terminal is True


def _escalate_llm(reason: str, **kw) -> ScriptedLlm:
    return ScriptedLlm(
        on_text={
            "person": Reply(
                tool_calls=(ToolCall("escalate_submission", {"reason": reason}),)
            ),
            **kw,
        }
    )


async def test_escalate_input_required_survives_tasks_get_and_restart(tmp_path):
    reason = "party asked for a person"
    expected_text = escalate_submission(reason)["message"]
    dsn = f"sqlite+aiosqlite:///{tmp_path}/offscript.db"
    task_store = get_adapter(Port.task_store, "local").build_task_store(db_url=dsn)
    llm = _escalate_llm(reason)
    app, _leg = _app(llm, task_store=task_store)
    async with _client(app) as (_hc, client):
        events, task = await _say(client, "I need a person")
        turn = _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            reason=REASON_ESCALATED,
            text=expected_text,
            llm=llm,
        )
        assert turn.terminal is False
        assert turn.lifecycle is LifecycleStatus.escalated
        task_id = task.id
        # No TTL: nothing auto-closes an escalated task.
        again = await client.get_task(GetTaskRequest(id=task_id))
        assert again.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert _reason(again.status.message) == REASON_ESCALATED

    await task_store.engine.dispose()  # the restart boundary

    store2 = get_adapter(Port.task_store, "local").build_task_store(db_url=dsn)
    quiet = ScriptedLlm(max_calls=0)
    app2, _leg2 = _app(quiet, task_store=store2)
    try:
        async with _client(app2) as (_hc2, client2):
            restored = await client2.get_task(GetTaskRequest(id=task_id))
            assert restored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            # The metadata survives the DB round-trip.
            assert _reason(restored.status.message) == REASON_ESCALATED
            assert ph.get_message_text(restored.status.message) == expected_text
            restored_turn = _turn(restored)
            assert restored_turn.lifecycle is LifecycleStatus.escalated
            assert restored_turn.terminal is False

            # DUR-6: only tasks/cancel terminates an escalated task.
            canceled = await client2.cancel_task(CancelTaskRequest(id=task_id))
            assert canceled.status.state == TaskState.TASK_STATE_CANCELED
    finally:
        await store2.engine.dispose()
    assert quiet.call_count == 0


async def test_followup_on_escalated_task_routes_to_llm_leg():
    """R4 also covers an INPUT_REQUIRED task with no pending review (escalated)."""
    llm = _escalate_llm(
        "party asked for a person",
        delete=Reply(
            tool_calls=(
                ToolCall("handle_deletion_request", {"artifact_name": "bill.pdf"}),
            )
        ),
    )
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        _events, parked = await _say(client, "I need a person")
        assert parked.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        calls_before = llm.call_count

        events, task = await _say(
            client,
            "please delete bill.pdf",
            context_id=parked.context_id,
            task_id=parked.id,
        )
    assert task.id == parked.id
    assert llm.call_count > calls_before  # the follow-up reached the LLM leg
    _assert_llm_outcome(
        events,
        task,
        state=TaskState.TASK_STATE_COMPLETED,
        reason=REASON_NOTHING_TO_DELETE,
        text="No document named 'bill.pdf' was found to erase.",
        llm=llm,
    )


async def test_unrouted_model_text_only():
    llm = ScriptedLlm()
    app, _leg = _app(llm)
    async with _client(app) as (_hc, client):
        events, task = await _say(client, "hello there")
    turn = _assert_llm_outcome(
        events,
        task,
        state=TaskState.TASK_STATE_COMPLETED,
        reason=REASON_UNROUTED,
        text=LLM_UNROUTED_TEXT,
        llm=llm,
    )
    assert turn.status.entries == []
    assert turn.terminal is True
    assert llm.call_count >= 1


async def test_precedence_escalate_beats_delete_over_a2a():
    """D6 combination rule: escalated outranks deleted — yet the erase is real."""
    reason = "also wants a person"
    llm = ScriptedLlm(
        on_text={
            "delete": Reply(
                tool_calls=(
                    ToolCall("handle_deletion_request", {"artifact_name": "bill.pdf"}),
                    ToolCall("escalate_submission", {"reason": reason}),
                )
            )
        }
    )
    app, _leg = _app(llm)
    async with _client(app) as (hc, client):
        uri, _ = await _submit_bill(client, ctx="ctx-p", party="jordan-lee")
        events, task = await _say(client, "delete bill.pdf", context_id="ctx-p")
        turn = _assert_llm_outcome(
            events,
            task,
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            reason=REASON_ESCALATED,
            text=escalate_submission(reason)["message"],
            llm=llm,
        )
        assert turn.lifecycle is LifecycleStatus.escalated
        assert turn.terminal is False
        assert (await hc.get(uri)).status_code == 404  # erased even though it parks


async def test_deterministic_edge_never_erases():
    """The erase capability never leaks into the default (deterministic) edge."""
    llm = ScriptedLlm(max_calls=0)
    app, _leg = _app(llm, edge_mode="deterministic")
    async with _client(app) as (hc, client):
        uri, _ = await _submit_bill(client, ctx="ctx-det", party="jordan-lee")
        # R2 text intake needs a resolvable process (a bare "please delete bill.pdf"
        # is refused outright by the deterministic resolver — also no erase).
        events, task = await _say(
            client, "address-proof: please delete bill.pdf", context_id="ctx-det"
        )
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert TaskState.TASK_STATE_WORKING not in [
            s.state for s in _status_updates(events)
        ]
        assert _reason(task.status.message) is None
        assert not task.status.message.HasField("metadata")
        assert (await hc.get(uri)).status_code == 200  # nothing erased
    assert llm.call_count == 0


# --------------------------------------------------------------------------- #
# llm_status / _text_message (pure)                                            #
# --------------------------------------------------------------------------- #
def _turn_with(entries: list[LedgerEntry]) -> ExchangeTurn:
    return ExchangeTurn(
        context_id="c", status=project_collection_status(entries), terminal=True
    )


def _gate_entry() -> LedgerEntry:
    return LedgerEntry(
        doc_id="gov-id-clean",
        doctype="gov-id",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
    )


def _projection(turn, *, reason=None, text=None) -> LlmProjection:
    return LlmProjection(
        turn=turn, reason_code=reason, status_text=text, review_call_ids=()
    )


def test_llm_status_reason_winner():
    turn = _turn_with([])
    assert llm_status(_projection(turn, reason="r", text="tool says"), turn) == (
        "tool says",
        "r",
    )


def test_llm_status_gate_only_has_no_reason():
    turn = _turn_with([_gate_entry()])
    assert llm_status(_projection(turn), turn) == (summarize_turn(turn), None)


def test_llm_status_nothing_ran_is_unrouted():
    turn = _turn_with([])
    assert llm_status(_projection(turn), turn) == (LLM_UNROUTED_TEXT, REASON_UNROUTED)


def test_llm_status_reason_without_text_falls_back_and_keeps_reason():
    empty = _turn_with([])
    assert llm_status(_projection(empty, reason="r"), empty) == (LLM_UNROUTED_TEXT, "r")
    gated = _turn_with([_gate_entry()])
    assert llm_status(_projection(gated, reason="r"), gated) == (
        summarize_turn(gated),
        "r",
    )


def test_text_message_reason_code_metadata():
    plain = _text_message("x", "c")
    assert not plain.HasField("metadata")  # unset — no empty Struct, no null
    assert _reason(plain) is None
    tagged = _text_message("x", "c", reason_code="r")
    assert MessageToDict(tagged.metadata) == {META_REASON_CODE: "r"}
    assert ph.get_message_text(tagged) == "x"
