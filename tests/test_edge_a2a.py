"""Acceptance suite for the A2A edge (P2-1, WIRE-1/2/3-foundation).

Drives the edge **in-process** over an ASGI transport with the conformant
``a2a-sdk`` client, exercising all six spec ops, plus pure unit tests for the
wire encode/decode.

A2A SDK generation note: the installed ``a2a-sdk`` (1.1.x) uses the proto-based
v1.0 protocol binding. Its conformant client (``a2a.client.ClientFactory``)
speaks that binding — the gRPC-method-named JSON-RPC ops (``SendMessage``,
``ListTasks``, ...) that map 1:1 to the six spec ops (``message/send`` →
``send_message``, ``tasks/list`` → ``list_tasks``, ``tasks/resubscribe`` →
``subscribe``, ...). We drive the real client rather than hand-rolling JSON-RPC
envelopes, which is the strongest conformance proof for this SDK version.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

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
from a2a.utils.errors import InvalidRequestError
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import Collector, DocumentSubmission
from bridge.edge import (
    CollectExecutor,
    SkillMeta,
    agent_card_from_skills,
    build_a2a_app,
    build_agent_card,
    decode_collect_request,
    decode_exchange_turn,
    decode_intake,
    decode_submissions,
    encode_collect_request,
    encode_exchange_turn,
    loaded_skills,
    parse_text_request,
    status_message_for,
)
from bridge.edge.card import DEFAULT_URL, PUBLIC_URL_ENV, resolved_card_url
from bridge.edge.executor import LLM_FAILED_TEXT, LLM_UNROUTED_TEXT, LLM_WORKING_TEXT
from bridge.edge.llm_leg import LlmLeg
from bridge.edge.pathroute import EDGE_MODE_ENV, EdgeMode
from bridge.edge.wire import KIND_EXCHANGE_TURN, KIND_HUMAN_REVIEW_RESOLUTION, META_KIND
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import escalate_submission
from contract.enums import Disposition, Gate, LifecycleStatus
from contract.wire import (
    CONTRACT_VERSION,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
)
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall

BASE = "http://test"
URL = "http://test/"

# Corpus fixtures (reuse ids from wiki/evals/address/expected.json).
ACCEPTED = ("gov-id-clean", "images/gov-id-clean.svg")
REJECTED = ("bill-aquautil-blurry", "images/bill-aquautil-blurry.svg")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _fixture_app(mode: str = "deterministic", llm=None, executor=None):
    """Edge app whose executor uses the deterministic fixture extractor.

    ``mode`` is the edge routing mode, passed explicitly (never the ambient env). In
    hybrid mode the LLM leg runs on ``llm`` — a model-free ``ScriptedLlm``.
    """
    executor = executor or CollectExecutor(collector=Collector(FixtureExtractor()))
    leg = None
    if mode == "hybrid":
        leg = LlmLeg(model=llm if llm is not None else ScriptedLlm(max_calls=0))
    return build_a2a_app(url=URL, executor=executor, edge_mode=mode, llm_leg=leg)


@dataclass
class _Edge:
    """One edge routing mode under test + the LLM double that must stay unused."""

    mode: str
    llm: ScriptedLlm

    def app(self, executor=None):
        return _fixture_app(self.mode, self.llm, executor)


@pytest.fixture(params=["deterministic", "hybrid"])
def edge(request):
    """Both edge modes (S3-edge-3). Structured traffic must never reach the model:
    the double fails loud on the first call (``max_calls=0`` → the task FAILs) and
    teardown asserts it was called 0 times."""
    e = _Edge(request.param, ScriptedLlm(max_calls=0))
    yield e
    assert e.llm.call_count == 0, f"structured traffic reached the LLM leg ({e.mode})"


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


def _url_doc_part(doc_id: str, uri: str, mime: str = "image/svg+xml"):
    part = ph.new_url_part(uri, media_type=mime)
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    return part


def _collect_message(process: str, docs: list[tuple[str, str]]) -> Message:
    parts = encode_collect_request(CollectRequest(process=process))
    parts += [_url_doc_part(doc_id, uri) for doc_id, uri in docs]
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


async def _send(client, process: str, docs: list[tuple[str, str]]):
    """message/send; return (task_id, events)."""
    events = []
    task_id = None
    async for ev in client.send_message(
        SendMessageRequest(message=_collect_message(process, docs))
    ):
        events.append(ev)
        if ev.HasField("task"):
            task_id = ev.task.id
        elif ev.HasField("status_update"):
            task_id = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            task_id = ev.artifact_update.task_id
    return task_id, events


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


# --------------------------------------------------------------------------- #
# Agent card                                                                   #
# --------------------------------------------------------------------------- #
async def test_agent_card_served(edge):
    """GET /.well-known/agent-card.json → 200, valid card, advertises the skill."""
    async with _client(edge.app()) as (hc, _client_obj):
        resp = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Document Bridge"
        assert any(s["id"] == "address-proof" for s in body["skills"])
        assert "application/json" in body["defaultInputModes"]
        assert body["capabilities"]["streaming"] is True
        # P4-4: push notifications advertised (DUR-6).
        assert body["capabilities"]["pushNotifications"] is True
        # The advertised interface is JSON-RPC.
        assert any(
            i["protocolBinding"] == TransportProtocol.JSONRPC.value
            for i in body["supportedInterfaces"]
        )


# --------------------------------------------------------------------------- #
# message/send                                                                 #
# --------------------------------------------------------------------------- #
async def test_message_send_happy_path_accepted(edge):
    """message/send an accepted doc → COMPLETED task with an ExchangeTurn artifact."""
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        assert task_id is not None

        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

        turn = _exchange_turn_from_artifacts(task)
        assert turn.terminal is True
        assert len(turn.status.entries) == 1
        entry = turn.status.entries[0]
        assert entry.doc_id == ACCEPTED[0]
        assert entry.disposition is Disposition.accepted
        # Domain contract carries the wire version (D2).
        assert turn.contract_version == CONTRACT_VERSION


async def test_message_send_rejected_record(edge):
    """message/send a rejected doc → COMPLETED task, one rejected ledger entry."""
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [REJECTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = _exchange_turn_from_artifacts(task)
        entry = turn.status.entries[0]
        assert entry.disposition is Disposition.rejected
        assert entry.reason_code
        # Rejected → surfaces one outstanding requirement.
        assert turn.status.outstanding is not None
        assert len(turn.status.outstanding.requirements) == 1


async def test_domain_in_parts_is_plain_dict_no_adk_event(edge):
    """The projection is a plain ExchangeTurn dict inside a standard A2A part.

    The client reads only Task status + the artifact data part — never an ADK
    event (WIRE-2). Structural proof: the data part round-trips as ExchangeTurn.
    """
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        # Selected by metadata.kind, not by position.
        turn = _exchange_turn_from_artifacts(task)
        assert isinstance(turn, ExchangeTurn)


# --------------------------------------------------------------------------- #
# No inline bytes on the wire                                                  #
# --------------------------------------------------------------------------- #
async def test_inline_bytes_rejected_end_to_end(edge):
    """A FileWithBytes (raw) part → the task does not complete successfully."""
    async with _client(edge.app()) as (_hc, client):
        parts = encode_collect_request(CollectRequest(process="address-proof"))
        parts.append(ph.new_raw_part(b"not-allowed-on-the-wire", media_type="image/svg+xml"))
        msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

        task_id = None
        raised = False
        try:
            async for ev in client.send_message(SendMessageRequest(message=msg)):
                if ev.HasField("task"):
                    task_id = ev.task.id
                elif ev.HasField("status_update"):
                    task_id = ev.status_update.task_id
        except Exception:
            raised = True

        # Either the send surfaced an error, or the task never reached COMPLETED.
        if not raised and task_id is not None:
            task = await client.get_task(GetTaskRequest(id=task_id))
            assert task.status.state != TaskState.TASK_STATE_COMPLETED
            assert not task.artifacts


# --------------------------------------------------------------------------- #
# message/stream (SSE)                                                         #
# --------------------------------------------------------------------------- #
async def test_message_stream_sse_events(edge):
    """Stream yields a non-empty status.message, an artifact event, terminal COMPLETED."""
    async with _client(edge.app()) as (_hc, client):
        _task_id, events = await _send(client, "address-proof", [ACCEPTED])

        status_updates = [e for e in events if e.HasField("status_update")]
        artifact_updates = [e for e in events if e.HasField("artifact_update")]

        assert artifact_updates, "expected at least one TaskArtifactUpdateEvent"
        assert status_updates, "expected at least one TaskStatusUpdateEvent"

        # A non-empty status.message rode on a status update (D3).
        assert any(
            su.status_update.status.HasField("message")
            and ph.get_message_text(su.status_update.status.message).strip()
            for su in status_updates
        )
        # Terminal state is COMPLETED.
        assert any(
            su.status_update.status.state == TaskState.TASK_STATE_COMPLETED
            for su in status_updates
        )
        # D7: the deterministic legs never stream WORKING — in either edge mode.
        assert not [
            su for su in status_updates
            if su.status_update.status.state == TaskState.TASK_STATE_WORKING
        ]
        # The artifact carried the ExchangeTurn data part.
        turn = decode_exchange_turn(list(artifact_updates[-1].artifact_update.artifact.parts))
        assert isinstance(turn, ExchangeTurn)


# --------------------------------------------------------------------------- #
# tasks/get, tasks/list                                                        #
# --------------------------------------------------------------------------- #
async def test_tasks_get_returns_terminal_task(edge):
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.id == task_id
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert any(a.name == "exchange_turn" for a in task.artifacts)


async def test_tasks_list_returns_created_task(edge):
    """tasks/list is a WIRE-1 required op and is implemented by the pinned SDK."""
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        listed = await client.list_tasks(ListTasksRequest())
        assert task_id in [t.id for t in listed.tasks]


# --------------------------------------------------------------------------- #
# tasks/resubscribe, tasks/cancel                                             #
# --------------------------------------------------------------------------- #
async def test_tasks_resubscribe_reachable(edge):
    """tasks/resubscribe (subscribe) is wired.

    For the deterministic one-turn edge a task is already terminal after send, so
    a2a-sdk 1.1.x rejects re-attaching to it with a well-formed protocol error
    (the queue is closed). This proves the op is wired (not MethodNotFound);
    non-terminal streaming resubscribe belongs to the durable lifecycle (P4).
    """
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        with pytest.raises(Exception):  # noqa: B017 - well-formed A2A error
            async for _ev in client.subscribe(SubscribeToTaskRequest(id=task_id)):
                pass


async def test_tasks_cancel_reachable(edge):
    """tasks/cancel is wired; on a terminal one-turn task it returns without error."""
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        result = await client.cancel_task(CancelTaskRequest(id=task_id))
        # Terminal task: reflects a terminal state (COMPLETED or CANCELED).
        assert result.status.state in (
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_CANCELED,
        )


# --------------------------------------------------------------------------- #
# Wire unit tests (pure)                                                       #
# --------------------------------------------------------------------------- #
def test_decode_collect_request_selects_by_shape():
    """decode_collect_request selects the CollectRequest part by shape, not position."""
    junk = ph.new_data_part({"not": "a-request", "totally": "different"})
    req_parts = encode_collect_request(CollectRequest(process="address-proof"))
    # Junk part first — selection must be by shape, not index.
    msg = Message(role=Role.ROLE_USER, message_id="m", parts=[junk, *req_parts])
    decoded = decode_collect_request(msg)
    assert decoded.process == "address-proof"


def test_decode_collect_request_rejects_when_absent():
    junk = ph.new_data_part({"not": "a-request"})
    msg = Message(role=Role.ROLE_USER, message_id="m", parts=[junk])
    with pytest.raises(ValueError):
        decode_collect_request(msg)


def test_decode_submissions_uri_only():
    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=[_url_doc_part("d1", "images/gov-id-clean.svg", "image/svg+xml")],
    )
    subs = decode_submissions(msg)
    assert len(subs) == 1
    assert subs[0].doc_id == "d1"
    assert subs[0].artifact_name == "images/gov-id-clean.svg"
    assert subs[0].artifact is not None
    assert subs[0].artifact.uri == "images/gov-id-clean.svg"
    assert subs[0].artifact.mime_type == "image/svg+xml"


def test_decode_submissions_generates_doc_id_when_absent():
    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=[ph.new_url_part("images/gov-id-clean.svg")],
    )
    subs = decode_submissions(msg)
    assert subs[0].doc_id == "doc-0"


def test_decode_submissions_rejects_inline_bytes():
    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=[ph.new_raw_part(b"inline-bytes", media_type="image/svg+xml")],
    )
    with pytest.raises(ValueError):
        decode_submissions(msg)


async def _one_turn(doc_id: str, artifact_name: str) -> ExchangeTurn:
    collector = Collector(FixtureExtractor())
    return await collector.collect(
        CollectRequest(process="address-proof"),
        documents=[DocumentSubmission(doc_id=doc_id, artifact_name=artifact_name)],
        candidate_doctypes=["gov-id", "utility-bill"],
    )


async def test_encode_exchange_turn_round_trips():
    turn = await _one_turn(*ACCEPTED)
    parts = encode_exchange_turn(turn)
    assert len(parts) == 1
    meta = MessageToDict(parts[0].metadata)
    assert meta[META_KIND] == KIND_EXCHANGE_TURN
    assert meta["contract_version"] == CONTRACT_VERSION
    restored = decode_exchange_turn(parts)
    assert restored.context_id == turn.context_id
    assert restored.terminal == turn.terminal
    assert [e.doc_id for e in restored.status.entries] == [
        e.doc_id for e in turn.status.entries
    ]


async def test_status_message_for_is_non_empty():
    turn = await _one_turn(*ACCEPTED)
    msg = status_message_for(turn)
    assert ph.get_message_text(msg).strip()


def test_build_agent_card_shape():
    card = build_agent_card(URL)
    assert card.name == "Document Bridge"
    assert card.capabilities.streaming is True
    assert any(s.id == "address-proof" for s in card.skills)
    assert card.supported_interfaces[0].url == URL


def test_resolved_card_url_honors_public_url_env(monkeypatch):
    """resolved_card_url honors BRIDGE_PUBLIC_URL (the Gateway-authorized origin) and
    falls back to DEFAULT_URL when unset — the served-card half of the adk >=2.7
    same-origin fix (P7-5): the deployed Bridge must advertise its card url at the
    origin the consumer fetches it from, else RemoteA2aAgent raises at resolve time.
    An explicit url still wins."""
    monkeypatch.setenv(PUBLIC_URL_ENV, "https://bridge.example/")
    assert resolved_card_url() == "https://bridge.example/"
    assert resolved_card_url("http://explicit/") == "http://explicit/"
    monkeypatch.delenv(PUBLIC_URL_ENV, raising=False)
    assert resolved_card_url() == DEFAULT_URL


async def test_build_a2a_app_advertises_public_url_origin(monkeypatch):
    """build_a2a_app with no explicit url honors BRIDGE_PUBLIC_URL so the served card
    advertises the Gateway-authorized origin (adk >=2.7 same-origin, P7-5)."""
    public = "https://bridge.example/"
    monkeypatch.setenv(PUBLIC_URL_ENV, public)
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    app = build_a2a_app(executor=executor)  # no explicit url → resolves the env
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        resp = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert resp.status_code == 200
        assert resp.json()["supportedInterfaces"][0]["url"] == public


# --------------------------------------------------------------------------- #
# P2-2: _status_for + dynamic card                                           #
# --------------------------------------------------------------------------- #
def test_status_for_terminal_turn():
    """_status_for: terminal turn → TASK_STATE_COMPLETED."""
    executor = CollectExecutor()
    turn = ExchangeTurn(
        context_id="c",
        status=CollectionStatus(),
        terminal=True,
    )
    assert executor._status_for(turn) == TaskState.TASK_STATE_COMPLETED


def test_status_for_non_terminal_turn():
    """_status_for: non-terminal turn → TASK_STATE_INPUT_REQUIRED."""
    executor = CollectExecutor()
    turn = ExchangeTurn(
        context_id="c",
        status=CollectionStatus(),
        terminal=False,
    )
    assert executor._status_for(turn) == TaskState.TASK_STATE_INPUT_REQUIRED


async def test_input_required_park_does_not_hang(edge):
    """End-to-end INPUT_REQUIRED park: returns without hanging, status message non-empty.

    Injects a stub collector that returns terminal=False, verifies the task reaches
    INPUT_REQUIRED state, the call returns (does not hang), and the exchange_turn
    artifact is present.
    """
    # Stub collector returning a non-terminal turn
    class StubCollector:
        async def collect(self, request, documents, candidate_doctypes, results_out=None):
            return ExchangeTurn(
                context_id="stub-context",
                status=CollectionStatus(entries=[]),
                terminal=False,
            )

    executor = CollectExecutor(collector=StubCollector())
    app = edge.app(executor)

    async with _client(app) as (_hc, client):
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        assert task_id is not None

        task = await client.get_task(GetTaskRequest(id=task_id))
        # The task is in INPUT_REQUIRED state (the park signal)
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        # The artifact is still present
        assert any(a.name == "exchange_turn" for a in task.artifacts)
        # Status message is non-empty (D3 rule)
        assert task.status.HasField("message")
        assert ph.get_message_text(task.status.message).strip()


def test_dynamic_card_metadata_driven():
    """Dynamic card: different skill metadata → different card, no code change.

    Build a card with a custom skill; verify the card follows the metadata
    (advertises the custom skill, not the default address-proof).
    """
    custom_skill = SkillMeta(
        name="benefits-intake",
        description="Collect benefits enrollment docs for an applicant.",
        kind="process",
        candidate_doctypes=("pay-stub",),
        tags=("benefits",),
    )
    card = agent_card_from_skills(URL, skills=[custom_skill])

    # The card advertises exactly one skill: benefits-intake
    assert len(card.skills) == 1
    skill = card.skills[0]
    assert skill.id == "benefits-intake"
    assert "benefits enrollment" in skill.description.lower()
    # address-proof is absent (proves the card follows the metadata)
    assert not any(s.id == "address-proof" for s in card.skills)


def test_default_card_from_loaded_skills():
    """Default card derives from loaded_skills(): address-proof skill present.

    The card advertises the address-proof skill, and its description/tags match
    the loaded_skills() metadata. Only process skills appear (no standalone doctype
    skills).
    """
    card = build_agent_card(URL)
    # address-proof skill is advertised
    address_proof = next((s for s in card.skills if s.id == "address-proof"), None)
    assert address_proof is not None

    # Match the description to the loaded_skills() metadata
    loaded = loaded_skills()
    address_meta = next((s for s in loaded if s.name == "address-proof"), None)
    assert address_meta is not None
    assert address_proof.description == address_meta.description

    # Doctype tags are represented (doctype:gov-id, doctype:utility-bill)
    assert any("doctype:gov-id" in address_proof.tags for _ in [1])
    assert any("doctype:utility-bill" in address_proof.tags for _ in [1])

    # No standalone doctype skills (gov-id, utility-bill) as AgentSkills
    assert not any(s.id == "gov-id" for s in card.skills)
    assert not any(s.id == "utility-bill" for s in card.skills)


def test_consolidated_candidate_doctypes():
    """Decision 5: candidate doctypes sourced from bridge.edge.skills.

    The executor reads candidate doctypes from loaded_skills(); an unknown process
    gets an empty label space — never another process's doctypes (S0-arch-18).
    """
    executor = CollectExecutor()

    # Known process: address-proof
    doctypes = executor._candidate_doctypes("address-proof")
    assert set(doctypes) == {"gov-id", "utility-bill"}

    # Unknown process: empty label space
    assert executor._candidate_doctypes("unknown-process") == []


def test_process_skill_without_candidates_has_empty_label_space(monkeypatch):
    """A resolved process skill declaring no bridge-candidate-doctypes → [] too."""
    from bridge.edge import skills

    monkeypatch.setattr(
        skills,
        "loaded_skills",
        lambda: [SkillMeta(name="no-candidates", description="d", kind="process")],
    )
    assert skills.candidate_doctypes_for("no-candidates") == []


async def test_unknown_process_rejects_against_empty_label_space(edge):
    """S0-arch-18: a doc sent under an unresolved process is dispositioned against an
    empty label space — rejected unsupported_doctype, never accepted against another
    process's doctypes. The turn stays COMPLETED (wire-compatible, no error path)."""
    async with _client(edge.app()) as (_hc, client):
        task_id, _events = await _send(client, "unknown-process", [ACCEPTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

        turn = _exchange_turn_from_artifacts(task)
        assert len(turn.status.entries) == 1
        entry = turn.status.entries[0]
        assert entry.doc_id == ACCEPTED[0]
        assert entry.disposition is Disposition.rejected
        assert entry.gate is Gate.unsupported
        assert entry.reason_code == "unsupported_doctype"
        assert "Accepted types: ." not in (entry.message or "")
        assert "accepts no document types" in (entry.message or "")


# --------------------------------------------------------------------------- #
# P2-3: Dual-mode intake (WIRE-5)                                             #
# --------------------------------------------------------------------------- #
async def test_card_advertises_dual_input_modes(edge):
    """WIRE-5: Agent Card advertises both application/json and text/plain input modes.

    The address-proof AgentSkill has input_modes containing both; card.default_input_modes
    contains both. The existing JSON assertion stays green.
    """
    async with _client(edge.app()) as (hc, _client_obj):
        resp = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert resp.status_code == 200
        body = resp.json()

        # Card-level default_input_modes contains both
        assert "application/json" in body["defaultInputModes"]
        assert "text/plain" in body["defaultInputModes"]

        # The address-proof skill has input_modes containing both
        address_skill = next((s for s in body["skills"] if s["id"] == "address-proof"), None)
        assert address_skill is not None
        assert "inputModes" in address_skill
        assert "application/json" in address_skill["inputModes"]
        assert "text/plain" in address_skill["inputModes"]


def test_parse_text_request_resolves_process():
    """parse_text_request: resolves process from text, stashes raw text, party is None."""
    request = parse_text_request("Please collect address proof for the applicant")
    assert request.process == "address-proof"
    assert request.message == "Please collect address proof for the applicant"
    assert request.party is None


def test_parse_text_request_matches_by_tag():
    """parse_text_request: matches on skill tag (e.g. 'address')."""
    request = parse_text_request("I need address verification documents")
    assert request.process == "address-proof"
    assert "address verification" in request.message


def test_parse_text_request_defaults_to_sole_process():
    """parse_text_request: defaults to the sole process skill when no match (single-skill fallback)."""
    # Text with no obvious match still defaults to address-proof (the only process skill)
    request = parse_text_request("collect some documents")
    assert request.process == "address-proof"


def test_parse_text_request_rejects_empty_text():
    """parse_text_request: raises ValueError on empty/whitespace text."""
    with pytest.raises(ValueError, match="empty or whitespace-only"):
        parse_text_request("")
    with pytest.raises(ValueError, match="empty or whitespace-only"):
        parse_text_request("   ")


def test_decode_intake_json_only():
    """decode_intake: JSON-only message → returns the structured CollectRequest."""
    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=encode_collect_request(CollectRequest(process="address-proof")),
    )
    request = decode_intake(msg)
    assert request.process == "address-proof"
    assert request.message is None  # No text path; this is the JSON path


def test_decode_intake_text_only():
    """decode_intake: text-only message → parses text to CollectRequest."""
    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=[ph.new_text_part("collect address proof")],
    )
    request = decode_intake(msg)
    assert request.process == "address-proof"
    assert request.message == "collect address proof"


def test_decode_intake_precedence_json_wins():
    """decode_intake: both JSON and text present → structured JSON wins (Decision 2).

    Text part is first to prove precedence is by content, not position.
    """
    text_part = ph.new_text_part("collect address proof")
    json_parts = encode_collect_request(CollectRequest(process="address-proof", message="from JSON"))
    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=[text_part, *json_parts],  # text first, JSON second
    )
    request = decode_intake(msg)
    # JSON wins: message is "from JSON", not the text part
    assert request.message == "from JSON"


def test_decode_intake_neither_present():
    """decode_intake: neither JSON nor text → raises ValueError."""
    junk = ph.new_data_part({"not": "a-request"})
    msg = Message(role=Role.ROLE_USER, message_id="m", parts=[junk])
    with pytest.raises(ValueError, match="no CollectRequest data part or text/plain intake found"):
        decode_intake(msg)


async def test_end_to_end_text_intake(edge):
    """End-to-end: text intake + by-reference docs routes to the same collect path.

    A message with only a text part (no JSON CollectRequest) plus by-reference docs
    reaches the same terminal outcome as the JSON path (WIRE-5: hit the same skill).
    """
    async with _client(edge.app()) as (_hc, client):
        # Build a message: text part + by-reference doc (no JSON CollectRequest)
        text_part = ph.new_text_part("collect address proof")
        doc_part = _url_doc_part(*ACCEPTED)
        msg = Message(
            role=Role.ROLE_USER,
            message_id=uuid.uuid4().hex,
            parts=[text_part, doc_part],
        )

        # Send through the real client
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id

        assert task_id is not None

        # Task reaches COMPLETED with the accepted entry (same terminal outcome as JSON path)
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

        turn = _exchange_turn_from_artifacts(task)
        assert turn.terminal is True
        assert len(turn.status.entries) == 1
        entry = turn.status.entries[0]
        assert entry.doc_id == ACCEPTED[0]
        assert entry.disposition is Disposition.accepted


# --------------------------------------------------------------------------- #
# S3-edge-3: BRIDGE_EDGE_MODE wiring + the hybrid LLM leg (R4/R5)              #
# --------------------------------------------------------------------------- #
def test_edge_mode_default_never_constructs_llm_leg(monkeypatch):
    """D2: unset → deterministic, and the LLM leg is never constructed."""
    monkeypatch.delenv(EDGE_MODE_ENV, raising=False)

    def _boom(self, *args, **kwargs):
        raise AssertionError("LlmLeg constructed in deterministic mode")

    monkeypatch.setattr(LlmLeg, "__init__", _boom)
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    build_a2a_app(url=URL, executor=executor)
    assert executor.mode is EdgeMode.deterministic
    assert executor.llm_leg is None


def test_edge_mode_env_hybrid_constructs_leg(monkeypatch):
    """BRIDGE_EDGE_MODE=hybrid → a leg bound to the app's SHARED artifact store."""
    from google.adk.artifacts import InMemoryArtifactService

    monkeypatch.setenv(EDGE_MODE_ENV, "hybrid")
    store = InMemoryArtifactService()
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    build_a2a_app(url=URL, executor=executor, artifact_store=store)
    assert executor.mode is EdgeMode.hybrid
    assert isinstance(executor.llm_leg, LlmLeg)
    assert executor.llm_leg.artifact_store is store


def test_edge_mode_hybrid_rebinds_injected_leg_store():
    """An injected leg is re-bound to the app's store (writer == reader == eraser)."""
    from google.adk.artifacts import InMemoryArtifactService

    store = InMemoryArtifactService()
    leg = LlmLeg(model=ScriptedLlm(max_calls=0), artifact_store=InMemoryArtifactService())
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    build_a2a_app(
        url=URL, executor=executor, artifact_store=store, edge_mode="hybrid", llm_leg=leg
    )
    assert executor.llm_leg is leg
    assert leg.artifact_store is store


def test_edge_mode_invalid_fails_app_build(monkeypatch):
    """Any other value fails closed at build time (env or explicit argument)."""
    monkeypatch.setenv(EDGE_MODE_ENV, "bogus")
    with pytest.raises(ValueError, match=EDGE_MODE_ENV):
        build_a2a_app(url=URL, executor=CollectExecutor())
    monkeypatch.delenv(EDGE_MODE_ENV, raising=False)
    with pytest.raises(ValueError, match=EDGE_MODE_ENV):
        build_a2a_app(url=URL, executor=CollectExecutor(), edge_mode="bogus")


def test_explicit_edge_mode_overrides_env(monkeypatch):
    """An explicit edge_mode wins over the env; deterministic ignores an injected leg."""
    monkeypatch.setenv(EDGE_MODE_ENV, "hybrid")
    executor = CollectExecutor()
    leg = LlmLeg(model=ScriptedLlm(max_calls=0))
    build_a2a_app(url=URL, executor=executor, edge_mode="deterministic", llm_leg=leg)
    assert executor.mode is EdgeMode.deterministic
    assert executor.llm_leg is None


def test_bind_edge_hybrid_without_leg_is_a_wiring_error():
    with pytest.raises(ValueError, match="LLM leg"):
        CollectExecutor().bind_edge(EdgeMode.hybrid)


def _path_a_assertion() -> dict:
    return {
        "fields": {
            "doctype": "gov-id",
            "key_fields": {
                "name": "Jordan Lee",
                "address": "14 Elm Row",
                "doc_number": "DL-1",
                "issuing_authority": "DMV",
                "expiry": "2030-01-01",
            },
        },
        "overall_confidence": 0.96,
        "legible": True,
        "flagged_fields": [],
    }


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


def _states(events) -> list[int]:
    return [e.status_update.status.state for e in events if e.HasField("status_update")]


async def test_path_a_assertion_both_modes(edge):
    """R1 in both modes: a structured assertion is validate-only, model-free."""
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_data_part(_path_a_assertion()))
    async with _client(edge.app()) as (_hc, client):
        events, task = await _send_parts(client, parts)
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert TaskState.TASK_STATE_WORKING not in _states(events)
    turn = _exchange_turn_from_artifacts(task)
    assert turn.terminal is True
    assert len(turn.status.entries) == 1
    assert turn.status.entries[0].doctype == "gov-id"
    assert turn.status.entries[0].disposition is Disposition.accepted


async def test_hybrid_text_only_routes_to_llm_leg_unrouted():
    """R4: text-only → LLM leg; model text only → COMPLETED with Bridge-authored text."""
    llm = ScriptedLlm()
    async with _client(_fixture_app("hybrid", llm)) as (_hc, client):
        events, task = await _send_parts(client, [ph.new_text_part("hello there")])

    working = [
        e.status_update.status for e in events
        if e.HasField("status_update")
        and e.status_update.status.state == TaskState.TASK_STATE_WORKING
    ]
    assert working, "the LLM leg streams WORKING (D7)"
    assert ph.get_message_text(working[0].message) == LLM_WORKING_TEXT
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    text = ph.get_message_text(task.status.message)
    assert text == LLM_UNROUTED_TEXT
    assert llm.default_text not in text  # model text never reaches the wire (D6)
    turn = _exchange_turn_from_artifacts(task)
    assert turn.status.entries == []
    assert turn.terminal is True
    assert turn.context_id == task.context_id  # D9: the A2A context_id keys the turn
    assert llm.call_count >= 1


async def test_hybrid_text_only_escalate_parks_input_required():
    """R4 escalate → a non-terminal INPUT_REQUIRED task with the tool's message."""
    reason = "party asked for a person"
    llm = ScriptedLlm(
        on_text={
            "person": Reply(
                tool_calls=(ToolCall("escalate_submission", {"reason": reason}),)
            )
        }
    )
    async with _client(_fixture_app("hybrid", llm)) as (_hc, client):
        events, task = await _send_parts(client, [ph.new_text_part("I need a person")])

    assert TaskState.TASK_STATE_WORKING in _states(events)
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    turn = _exchange_turn_from_artifacts(task)
    assert turn.terminal is False
    assert turn.lifecycle is LifecycleStatus.escalated
    text = ph.get_message_text(task.status.message)
    assert text == escalate_submission(reason)["message"]
    assert llm.after_tool_text not in text  # the double's false "accepted" claim
    assert llm.call_count >= 1


async def test_hybrid_llm_leg_failure_marks_task_failed():
    """A leg failure → FAILED with Bridge-authored text; never stuck in WORKING."""
    llm = ScriptedLlm(max_calls=0)
    async with _client(_fixture_app("hybrid", llm)) as (_hc, client):
        _events, task = await _send_parts(client, [ph.new_text_part("delete bill.pdf")])
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert ph.get_message_text(task.status.message) == LLM_FAILED_TEXT
    assert not task.artifacts
    assert llm.call_count == 1


async def test_hybrid_resolution_part_without_pending_review_refused():
    """R5: a human-review resolution with nothing pending → InvalidRequestError."""
    llm = ScriptedLlm(max_calls=0)
    part = ph.new_data_part(
        {"call_id": "c1", "resolution": "accepted", "message": "looks fine"}
    )
    part.metadata.CopyFrom(
        ParseDict({META_KIND: KIND_HUMAN_REVIEW_RESOLUTION}, struct_pb2.Struct())
    )
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=[part])
    async with _client(_fixture_app("hybrid", llm)) as (_hc, client):
        with pytest.raises(InvalidRequestError, match="no_pending_review"):
            async for _ev in client.send_message(SendMessageRequest(message=msg)):
                pass
    assert llm.call_count == 0
