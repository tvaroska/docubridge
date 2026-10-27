"""Shared A2A live-add kit: one hybrid-edge core, the wire helpers, the HITL park and the
trap guards (the CUJ-9 Drivers' shared idiom).

Used by ``tests/test_live_use_case_add.py`` (S4-usecase-2/3) and
``tests/test_live_rfp_use_case_add.py`` (S4-usecase-4). Callers import the MODULE
(``from tests import _a2a_live as a2a``) — the helper names (``client``, ``turn``, ``meta``)
are also natural local variable names. Test-only doubles; never shipped in bridge/.

Synthetic data only (the corpus party ``jordan-lee``; suppliers ``supplier-x``).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from google.adk.sessions import InMemorySessionService
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    SimpleUser,
)

import bridge.edge
import bridge.edge.server
from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.artifacts import resolve_bytes
from bridge.edge.executor import HITL_HANDOFF_TEXT
from bridge.edge.llm_leg import REVIEW_TOOL, LlmLeg
from bridge.edge.wire import (
    KIND_EXCHANGE_TURN,
    META_KIND,
    META_REASON_CODE,
    META_REVIEW_CALL_ID,
    encode_human_review_resolution,
)
from bridge.extraction.fixture import FixtureExtractor
from contract.enums import Disposition
from contract.extraction import ExtractionResult
from contract.wire import CollectRequest, ExchangeTurn, HumanReviewResolution
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall
from tests._skill_fixtures import supplier_result

BASE = "http://test"
URL = "http://test/"
PARTY = "jordan-lee"  # synthetic
REVIEWER = "alice"
PRINCIPAL_HEADER = "X-Test-Principal"
ADDRESS_SKILLS = ("address-proof", "gov-id", "utility-bill")
POWER = ("bill-powerco-clean", "images/bill-powerco-clean.svg")
AQUA = ("bill-aquautil-clear", "images/bill-aquautil-clear.svg")
W9 = ("w9", "synthetic/supplier-x-w9.pdf")
COI = ("coi", "synthetic/supplier-x-coi.pdf")
ADDRESS_CTX = "ctx-live-add"  # the SAME on both cores, so artifact URIs compare equal
ENV_TO_CLEAR = (
    "BRIDGE_PORT_MODE",
    "BRIDGE_PORT_SKILL_REGISTRY",
    "BRIDGE_PORT_EXTRACTION",
    "BRIDGE_PORT_TASK_STORE",
    "BRIDGE_PORT_SESSIONS",
    "BRIDGE_TASK_DB_URL",
    "BRIDGE_ARTIFACT_BUCKET",
    "BRIDGE_PUBLIC_URL",
    "BRIDGE_EDGE_MODE",
    "BRIDGE_EDGE_REVIEWERS",
    "BRIDGE_SATISFACTION",
    "BRIDGE_RFP_PROCESS",
    "DATABASE_URL",
)


# --------------------------------------------------------------------------- #
# Test-only doubles (never shipped in bridge/)                                 #
# --------------------------------------------------------------------------- #
class HeaderAuth(AuthenticationBackend):
    """``X-Test-Principal: <name>`` -> an authenticated ``SimpleUser(<name>)``."""

    async def authenticate(self, conn):
        name = conn.headers.get(PRINCIPAL_HEADER)
        if not name:
            return None
        return AuthCredentials(["authenticated"]), SimpleUser(name)


class SpyExecutor(CollectExecutor):
    """Records the task id of every message the executor handles."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.executed: list[str] = []

    async def execute(self, context, event_queue):
        self.executed.append(context.task_id)
        await super().execute(context, event_queue)


class Extractor:
    """Supplier documents -> canned results; every other document -> the Address corpus."""

    def __init__(self) -> None:
        self._fixture = FixtureExtractor()

    async def extract(self, artifact_name: str) -> ExtractionResult:
        if artifact_name == W9[1]:
            return supplier_result("w9-form", "W9")
        if artifact_name == COI[1]:
            return supplier_result("insurance-cert", "COI")
        return await self._fixture.extract(artifact_name)


async def resolver(uri: str):
    """Supplier docs resolve to synthetic bytes (so their artifacts persist + get scoped
    URIs); every other URI goes through the edge's own resolver."""
    if uri.startswith("synthetic/"):
        return f"%PDF-1.4 synthetic {uri}".encode(), "application/pdf"
    return await resolve_bytes(uri)


@dataclass
class Core:
    app: object
    executor: SpyExecutor
    leg: LlmLeg
    llm: ScriptedLlm


def core(**build_kw) -> Core:
    """One hybrid-edge core; the live and the control core are built by this same factory.

    ``build_kw`` passes extra ``build_a2a_app`` keyword arguments through.
    """
    llm = ScriptedLlm(
        on_text={
            HITL_HANDOFF_TEXT: Reply(
                tool_calls=(
                    ToolCall(
                        REVIEW_TOOL,
                        {
                            "reason": f"{AQUA[0]} needs a person",
                            "artifact_name": AQUA[0],
                        },
                    ),
                )
            )
        }
    )
    leg = LlmLeg(model=llm, session_service=InMemorySessionService())
    executor = SpyExecutor(collector=Collector(Extractor()), resolver=resolver)
    app = build_a2a_app(
        url=URL,
        executor=executor,
        edge_mode="hybrid",
        llm_leg=leg,
        reviewers=(REVIEWER,),
        auth_backend=HeaderAuth(),
        satisfaction="off",
        **build_kw,
    )
    return Core(app=app, executor=executor, leg=leg, llm=llm)


# --------------------------------------------------------------------------- #
# Wire helpers (the test_edge_hitl_a2a.py idiom)                               #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def client(app, principal: str):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE, headers={PRINCIPAL_HEADER: principal}
    ) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        a2a_client = await ClientFactory(config).create_from_url(URL)
        yield hc, a2a_client


async def send(a2a_client, parts, *, context_id: str = "", task_id: str = ""):
    """message/stream; return the task via tasks/get."""
    msg = Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=parts,
        context_id=context_id,
        task_id=task_id,
    )
    tid = None
    async for ev in a2a_client.send_message(SendMessageRequest(message=msg)):
        if ev.HasField("task"):
            tid = ev.task.id
        elif ev.HasField("status_update"):
            tid = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            tid = ev.artifact_update.task_id
    assert tid is not None
    return await a2a_client.get_task(GetTaskRequest(id=tid))


def url_part(doc):
    doc_id, uri = doc
    part = ph.new_url_part(uri, media_type="application/octet-stream", filename=doc_id)
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    return part


def submission(docs, *, ctx: str, process: str, party: str):
    parts = encode_collect_request(
        CollectRequest(process=process, party=party, context_id=ctx)
    )
    parts.extend(url_part(doc) for doc in docs)
    return parts


def resolution(call_id: str):
    return [
        encode_human_review_resolution(
            HumanReviewResolution(
                call_id=call_id,
                resolution="accepted",
                message="Reviewed by a person: the AquaUtil bill is acceptable.",
                reviewer=REVIEWER,
            )
        )
    ]


def meta(msg) -> dict:
    return MessageToDict(msg.metadata) if msg.HasField("metadata") else {}


def turn(task) -> ExchangeTurn:
    """The LAST exchange_turn data part (select by metadata, never by index)."""
    found = None
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            part_meta = (
                MessageToDict(part.metadata) if part.HasField("metadata") else {}
            )
            if part_meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                found = ExchangeTurn.model_validate(MessageToDict(part.data))
    assert found is not None, "no exchange_turn artifact data part found"
    return found


async def card_skills(hc) -> dict[str, list[str]]:
    resp = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert resp.status_code == 200
    return {s["id"]: s["tags"] for s in resp.json()["skills"]}


async def park(a2a_client, *, ctx: str):
    """POWER + AQUA in one round: PowerCo auto-approves, AquaUtil parks for review."""
    parked = await send(
        a2a_client,
        submission((POWER, AQUA), ctx=ctx, process="address-proof", party=PARTY),
        context_id=ctx,
    )
    assert parked.status.state == TaskState.TASK_STATE_INPUT_REQUIRED, parked.status
    return parked


def arm_trap_guards(monkeypatch) -> list[tuple]:
    """From here on nothing may be rebuilt or reconstructed: a rebuild raises, and every
    ``CollectExecutor`` construction is recorded (the caller asserts none happened)."""

    def _no_rebuild(*_args, **_kwargs):
        raise AssertionError("app rebuilt")

    monkeypatch.setattr(bridge.edge.server, "build_a2a_app", _no_rebuild)
    monkeypatch.setattr(bridge.edge, "build_a2a_app", _no_rebuild)
    constructed: list[tuple] = []
    original_init = CollectExecutor.__init__

    def _spy_init(self, *args, **kwargs):
        constructed.append((args, kwargs))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(CollectExecutor, "__init__", _spy_init)
    return constructed


def assert_same_outcome(task, control_task) -> None:
    """The resumed exchange ended exactly as the control did (state, reason, full turn)."""
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task.status.state == control_task.status.state
    assert (
        meta(task.status.message)[META_REASON_CODE]
        == meta(control_task.status.message)[META_REASON_CODE]
        == "needs_review"
    )
    assert ph.get_message_text(task.status.message) == ph.get_message_text(
        control_task.status.message
    )
    # The full turn: entries, issuers, artifact URIs, outstanding, terminal.
    resumed = turn(task)
    assert resumed.model_dump(mode="json") == turn(control_task).model_dump(mode="json")
    assert resumed.terminal is True
    assert [e.disposition for e in resumed.status.entries] == [Disposition.accepted] * 2
    assert {e.issuer for e in resumed.status.entries} == {"power-co", "aqua-util"}
    assert resumed.status.outstanding.requirements == []
    assert [a.name for a in task.artifacts].count("exchange_turn") == 1


async def control_outcome(control: Core):
    """The control core parks the same exchange and resolves it to its terminal outcome."""
    async with client(control.app, REVIEWER) as (_chc, control_client):
        control_parked = await park(control_client, ctx=ADDRESS_CTX)
        control_call_id = meta(control_parked.status.message)[META_REVIEW_CALL_ID]
        control_task = await send(
            control_client,
            resolution(control_call_id),
            context_id=control_parked.context_id,
            task_id=control_parked.id,
        )
    assert control_task.status.state == TaskState.TASK_STATE_COMPLETED
    return control_task
