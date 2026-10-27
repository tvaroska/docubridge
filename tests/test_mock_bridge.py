"""Acceptance suite for the mock Bridge contract double (P2-5, WIRE-6).

Drives the mock **in-process** over an ASGI transport with the conformant
``a2a-sdk`` client — the *same* client harness as ``tests/test_edge_a2a.py`` —
proving the mock serves the same card and the same spec ops over the same wire.

The two things the mock adds over the real P2 edge are exercised here: **multi-turn
scripted scenarios** (a non-terminal ``input-required`` park turn followed by a
terminal ``COMPLETED`` on the same ``context_id``) and being a **contract-checked
double** (every scripted turn round-trips through ``bridge.edge.wire``).

A2A SDK continuation note (verified against the installed 1.1.x): a parked task is
resumed by a second ``message/send`` whose ``Message`` carries BOTH the parked
``task_id`` and the ``context_id`` (``DefaultRequestHandler._setup_message_execution``
looks the task up by ``message.task_id``; ``context_id`` alone would mint a new task).
The harness threads both on the resume leg.
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
    ListTasksRequest,
    Message,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskState,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from google.protobuf.json_format import MessageToDict

from bridge.collect import Collector
from bridge.edge import (
    SCENARIOS,
    CollectExecutor,
    build_a2a_app,
    build_mock_bridge_app,
    encode_collect_request,
    select_scenario,
)
from bridge.edge.wire import (
    KIND_EXCHANGE_TURN,
    META_KIND,
    decode_exchange_turn,
    encode_exchange_turn,
)
from bridge.extraction.fixture import FixtureExtractor
from contract.enums import Disposition, Gate
from contract.wire import CONTRACT_VERSION, CollectRequest, ExchangeTurn

BASE = "http://test"
URL = "http://test/"


# --------------------------------------------------------------------------- #
# Helpers (mirror tests/test_edge_a2a.py — same conformant client)            #
# --------------------------------------------------------------------------- #
def _mock_app():
    return build_mock_bridge_app(url=URL)


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


def _collect_message(
    scenario: str | None = None,
    *,
    context_id: str | None = None,
    task_id: str | None = None,
) -> Message:
    message = None if scenario is None else f"scenario:{scenario}"
    parts = encode_collect_request(
        CollectRequest(process="address-proof", message=message)
    )
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)
    if context_id is not None:
        msg.context_id = context_id
    if task_id is not None:
        msg.task_id = task_id
    return msg


async def _send(client, message: Message):
    """message/send; return (task_id, events)."""
    events = []
    task_id = None
    async for ev in client.send_message(SendMessageRequest(message=message)):
        events.append(ev)
        if ev.HasField("task"):
            task_id = ev.task.id
        elif ev.HasField("status_update"):
            task_id = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            task_id = ev.artifact_update.task_id
    return task_id, events


def _exchange_turn_from_artifacts(task) -> ExchangeTurn:
    """Select the LATEST exchange_turn data part by metadata (never by index).

    A multi-turn task accumulates one exchange_turn artifact per turn, so the most
    recent turn is the last matching artifact (select-by-metadata, then recency).
    """
    latest: ExchangeTurn | None = None
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                latest = ExchangeTurn.model_validate(MessageToDict(part.data))
    if latest is None:
        raise AssertionError("no exchange_turn artifact data part found")
    return latest


def _accepted_set(turn: ExchangeTurn) -> set[tuple[str, str | None]]:
    """The terminal-outcome accepted-issuer set: {(doctype, issuer)} of accepteds."""
    return {
        (e.doctype, e.issuer)
        for e in turn.status.entries
        if e.disposition is Disposition.accepted
    }


# --------------------------------------------------------------------------- #
# Same card, same wire                                                         #
# --------------------------------------------------------------------------- #
async def test_mock_serves_identical_agent_card():
    """The mock card == the real edge card (mock→real swap is only the URL)."""
    async with _client(_mock_app()) as (hc, _client_obj):
        resp = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Document Bridge"
        assert any(s["id"] == "address-proof" for s in body["skills"])
        assert "application/json" in body["defaultInputModes"]
        assert "text/plain" in body["defaultInputModes"]
        assert body["capabilities"]["streaming"] is True
        assert any(
            i["protocolBinding"] == TransportProtocol.JSONRPC.value
            for i in body["supportedInterfaces"]
        )
    # The swap-is-only-the-URL invariant: the mock and the real edge serve a
    # byte-identical Agent Card (both from build_agent_card(URL)).
    real_app = build_a2a_app(
        url=URL, executor=CollectExecutor(collector=Collector(FixtureExtractor()))
    )
    async with _client(real_app) as (real_hc, _c):
        real_body = (await real_hc.get(AGENT_CARD_WELL_KNOWN_PATH)).json()
    assert body == real_body


# --------------------------------------------------------------------------- #
# path-a-instant (CUJ-1)                                                       #
# --------------------------------------------------------------------------- #
async def test_path_a_instant_completes():
    """Fast path: one turn → COMPLETED, one accepted gov-id entry, terminal."""
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("path-a-instant"))
        assert task_id is not None
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

        turn = _exchange_turn_from_artifacts(task)
        assert turn.terminal is True
        assert len(turn.status.entries) == 1
        entry = turn.status.entries[0]
        assert entry.doc_id == "gov-id-clean"
        assert entry.doctype == "gov-id"
        assert entry.disposition is Disposition.accepted
        assert entry.gate is Gate.auto_approve
        assert turn.contract_version == CONTRACT_VERSION


async def test_default_scenario_is_path_a():
    """No marker → defaults to path-a-instant (COMPLETED)."""
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message(None))
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        turn = _exchange_turn_from_artifacts(task)
        assert turn.status.entries[0].doc_id == "gov-id-clean"


# --------------------------------------------------------------------------- #
# path-b-two-bills multi-turn park→resume (CUJ-10) — the core WIRE-6 proof     #
# --------------------------------------------------------------------------- #
async def test_path_b_two_bills_park_then_resume():
    """Turn 1 parks (INPUT_REQUIRED); resume on same context_id → COMPLETED."""
    async with _client(_mock_app()) as (_hc, client):
        # Turn 1: park.
        task_id, _events = await _send(client, _collect_message("path-b-two-bills"))
        assert task_id is not None
        parked = await client.get_task(GetTaskRequest(id=task_id))
        assert parked.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        turn1 = _exchange_turn_from_artifacts(parked)
        assert turn1.terminal is False
        assert len(turn1.status.entries) == 1
        assert turn1.status.entries[0].issuer == "power-co"
        assert turn1.status.outstanding is not None
        assert len(turn1.status.outstanding.requirements) == 1
        assert turn1.status.outstanding.done is False

        # Turn 2: resume on the SAME context_id + task_id (SDK continuation model).
        resume = _collect_message(None, context_id=parked.context_id, task_id=parked.id)
        resume_task_id, _events2 = await _send(client, resume)
        completed = await client.get_task(GetTaskRequest(id=resume_task_id))
        assert completed.status.state == TaskState.TASK_STATE_COMPLETED

        turn2 = _exchange_turn_from_artifacts(completed)
        assert turn2.terminal is True
        assert _accepted_set(turn2) == {
            ("utility-bill", "power-co"),
            ("utility-bill", "aqua-util"),
        }


# --------------------------------------------------------------------------- #
# resubmit (CUJ-4)                                                             #
# --------------------------------------------------------------------------- #
async def test_resubmit_reject_then_accept():
    """Turn 1 rejects (resubmit gate, park); resume → accepted, COMPLETED."""
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("resubmit"))
        parked = await client.get_task(GetTaskRequest(id=task_id))
        assert parked.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        turn1 = _exchange_turn_from_artifacts(parked)
        assert turn1.terminal is False
        assert len(turn1.status.entries) == 1
        entry = turn1.status.entries[0]
        assert entry.disposition is Disposition.rejected
        assert entry.gate is Gate.resubmit
        assert turn1.status.outstanding is not None
        assert len(turn1.status.outstanding.requirements) == 1

        resume = _collect_message(None, context_id=parked.context_id, task_id=parked.id)
        resume_task_id, _events2 = await _send(client, resume)
        completed = await client.get_task(GetTaskRequest(id=resume_task_id))
        assert completed.status.state == TaskState.TASK_STATE_COMPLETED

        turn2 = _exchange_turn_from_artifacts(completed)
        assert turn2.terminal is True
        assert len(turn2.status.entries) == 1
        assert turn2.status.entries[0].disposition is Disposition.accepted


# --------------------------------------------------------------------------- #
# Non-empty status.message (D3) over the stream                                #
# --------------------------------------------------------------------------- #
async def test_park_turn_streams_non_empty_status_message():
    """A park turn's stream carries a non-empty status.message (D3)."""
    async with _client(_mock_app()) as (_hc, client):
        _task_id, events = await _send(client, _collect_message("path-b-two-bills"))
        status_updates = [e for e in events if e.HasField("status_update")]
        assert status_updates, "expected at least one TaskStatusUpdateEvent"
        assert any(
            su.status_update.status.HasField("message")
            and ph.get_message_text(su.status_update.status.message).strip()
            for su in status_updates
        )
        # The park state (INPUT_REQUIRED) rode on a status update.
        assert any(
            su.status_update.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            for su in status_updates
        )


# --------------------------------------------------------------------------- #
# last_chunk=True / artifact persisted on a park turn                          #
# --------------------------------------------------------------------------- #
async def test_park_turn_artifact_persisted():
    """After a park turn, tasks/get returns the persisted exchange_turn artifact.

    A *partial* artifact (last_chunk=False) would stream but never persist.
    """
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("path-b-two-bills"))
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert any(a.name == "exchange_turn" for a in task.artifacts)


# --------------------------------------------------------------------------- #
# Six ops reachable (mirror test_edge_a2a.py op tests)                         #
# --------------------------------------------------------------------------- #
async def test_message_stream_yields_artifact_and_terminal():
    async with _client(_mock_app()) as (_hc, client):
        _task_id, events = await _send(client, _collect_message("path-a-instant"))
        artifact_updates = [e for e in events if e.HasField("artifact_update")]
        status_updates = [e for e in events if e.HasField("status_update")]
        assert artifact_updates
        assert any(
            su.status_update.status.state == TaskState.TASK_STATE_COMPLETED
            for su in status_updates
        )
        turn = decode_exchange_turn(
            list(artifact_updates[-1].artifact_update.artifact.parts)
        )
        assert isinstance(turn, ExchangeTurn)


async def test_tasks_list_returns_created_task():
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("path-a-instant"))
        listed = await client.list_tasks(ListTasksRequest())
        assert task_id in [t.id for t in listed.tasks]


async def test_tasks_cancel_reachable():
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("path-a-instant"))
        result = await client.cancel_task(CancelTaskRequest(id=task_id))
        assert result.status.state in (
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_CANCELED,
        )


async def test_tasks_resubscribe_reachable():
    """tasks/resubscribe is wired; on a terminal one-turn task the SDK errors."""
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("path-a-instant"))
        with pytest.raises(Exception):  # noqa: B017 - well-formed A2A error
            async for _ev in client.subscribe(SubscribeToTaskRequest(id=task_id)):
                pass


# --------------------------------------------------------------------------- #
# Unknown scenario marker fails loud                                           #
# --------------------------------------------------------------------------- #
def test_select_scenario_unknown_marker_raises():
    with pytest.raises(ValueError, match="unknown scenario marker"):
        select_scenario(
            CollectRequest(process="address-proof", message="scenario:nope"),
            SCENARIOS,
        )


async def test_unknown_scenario_marker_does_not_complete():
    """An unknown marker surfaces an error / the task never reaches COMPLETED."""
    async with _client(_mock_app()) as (_hc, client):
        raised = False
        task_id = None
        try:
            task_id, _events = await _send(client, _collect_message("nope"))
        except Exception:
            raised = True
        if not raised and task_id is not None:
            task = await client.get_task(GetTaskRequest(id=task_id))
            assert task.status.state != TaskState.TASK_STATE_COMPLETED


# --------------------------------------------------------------------------- #
# Parity smoke (WIRE-6 acceptance seed for P3-5)                               #
# --------------------------------------------------------------------------- #
async def test_parity_smoke_terminal_outcome_matches_real_edge():
    """Mock and real edge reach the same terminal outcome for gov-id-clean.

    Parity = terminal state + accepted-issuer set (NOT ledger-identical). The real
    side exercises the extraction port; the mock is script-driven.
    """
    # Mock side: path-a-instant.
    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("path-a-instant"))
        mock_task = await client.get_task(GetTaskRequest(id=task_id))
        mock_turn = _exchange_turn_from_artifacts(mock_task)

    # Real edge side: drive gov-id-clean through the deterministic fixture edge.
    real_executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    real_app = build_a2a_app(url=URL, executor=real_executor)
    async with _client(real_app) as (_hc, client):
        parts = encode_collect_request(CollectRequest(process="address-proof"))
        doc = ph.new_url_part("images/gov-id-clean.svg", media_type="image/svg+xml")
        from google.protobuf import struct_pb2
        from google.protobuf.json_format import ParseDict

        doc.metadata.CopyFrom(
            ParseDict({"doc_id": "gov-id-clean"}, struct_pb2.Struct())
        )
        msg = Message(
            role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=[*parts, doc]
        )
        real_task_id, _events = await _send(client, msg)
        real_task = await client.get_task(GetTaskRequest(id=real_task_id))
        real_turn = _exchange_turn_from_artifacts(real_task)

    # Terminal-outcome tuple equality (state + accepted-issuer set).
    assert (
        mock_task.status.state
        == real_task.status.state
        == TaskState.TASK_STATE_COMPLETED
    )
    assert _accepted_set(mock_turn) == _accepted_set(real_turn) == {("gov-id", None)}


# --------------------------------------------------------------------------- #
# Contract-faithful (maintained-double proof)                                  #
# --------------------------------------------------------------------------- #
def test_every_scenario_step_round_trips_through_wire():
    """Every scripted turn is a valid ExchangeTurn that round-trips via wire.py.

    Building the registry already validates each step via Pydantic; this asserts the
    same encode/decode path the real edge uses round-trips each step (so a contract
    change breaks the mock — 'maintained, not throwaway').
    """
    for scenario in SCENARIOS.values():
        assert scenario.steps, f"{scenario.name} has no steps"
        for step in scenario.steps:
            restored = decode_exchange_turn(encode_exchange_turn(step))
            assert restored.terminal == step.terminal
            assert [e.doc_id for e in restored.status.entries] == [
                e.doc_id for e in step.status.entries
            ]


# --------------------------------------------------------------------------- #
# P4-5: escalated lifecycle, non-terminal invariant, cancel                   #
# --------------------------------------------------------------------------- #
async def test_escalated_scenario_emits_non_terminal():
    """P4-5 §5.2: escalated-stall scenario emits escalated lifecycle + non-terminal."""
    from contract.enums import LifecycleStatus

    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("escalated-stall"))
        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = _exchange_turn_from_artifacts(task)

        # The escalated turn is non-terminal (task stays INPUT_REQUIRED)
        assert turn.lifecycle == LifecycleStatus.escalated
        assert turn.terminal is False
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        # Outstanding requirement present (the stall holds)
        assert turn.status.outstanding is not None
        assert not turn.status.outstanding.done


async def test_cancel_non_terminal_task_to_canceled():
    """P4-5 §5.4: tasks/cancel on a non-terminal task → CANCELED (persists)."""
    async with _client(_mock_app()) as (_hc, client):
        # Start a non-terminal task (path-b-two-bills parks at INPUT_REQUIRED)
        task_id, _events = await _send(client, _collect_message("path-b-two-bills"))
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        # Cancel the parked task
        result = await client.cancel_task(CancelTaskRequest(id=task_id))
        assert result.status.state == TaskState.TASK_STATE_CANCELED

        # Verify cancellation persisted (get returns CANCELED)
        task_after = await client.get_task(GetTaskRequest(id=task_id))
        assert task_after.status.state == TaskState.TASK_STATE_CANCELED


async def test_cancel_escalated_task_to_canceled():
    """P4-5 §5.4: tasks/cancel on an escalated task → CANCELED (only cancel terminates)."""
    from contract.enums import LifecycleStatus

    async with _client(_mock_app()) as (_hc, client):
        task_id, _events = await _send(client, _collect_message("escalated-stall"))
        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = _exchange_turn_from_artifacts(task)

        # Verify task is escalated and non-terminal before cancel
        assert turn.lifecycle == LifecycleStatus.escalated
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        # Cancel the escalated task
        result = await client.cancel_task(CancelTaskRequest(id=task_id))
        assert result.status.state == TaskState.TASK_STATE_CANCELED

        # Verify cancellation persisted
        task_after = await client.get_task(GetTaskRequest(id=task_id))
        assert task_after.status.state == TaskState.TASK_STATE_CANCELED
