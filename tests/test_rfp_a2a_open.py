"""The deployed A2A runtime opens RFPs onto the durable board (S0-arch-19).

With ``BRIDGE_RFP_PROCESS`` set, ``build_a2a_app`` binds an opener-only ``RfpBoard`` over
the durable shared ``exchange_store``; a ``CollectRequest`` of that process opens its RFP
(``rfp_id`` = the exchange context), applies the agent's next requirements, or reads its
status — model-free in both edge modes. Proves: mail drained by the RFP board host files
onto an RFP opened through A2A (the acceptance path, both edge modes); follow-up
requirements (sense B); an idempotent retried open; refusals are JSON-RPC errors that
store nothing; other processes stay on the Collector; no env → no opener; the
``rfp_board_from_env`` refusals; the CAS retry; an injected board wins.

Synthetic data only (``tests/email_fixtures.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from fastapi.testclient import TestClient
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import Collector
from bridge.dashboard.board import RfpBoard
from bridge.dashboard.board_host import RFP_PROCESS_ENV, build_rfp_board_app
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.llm_leg import LlmLeg
from bridge.edge.rfp_open import (
    RFP_SUBMISSIONS_TEXT,
    open_or_update_rfp,
    rfp_board_from_env,
)
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from bridge.email_ingress.__main__ import main as cli
from bridge.email_ingress.mime import landing_key
from bridge.exchange_store import (
    ExchangeStore,
    InMemoryExchangeStore,
    SqlExchangeStore,
    StaleExchangeWrite,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.rfp import load_rfp_policy, open_rfp
from contract.enums import RequirementStatus
from contract.wire import CollectRequest, ExchangeTurn, Requirement, RequirementsList
from tests._scripted_llm import ScriptedLlm
from tests.email_fixtures import MAILBOX, PROCESS, compose, extractor, quote_attachments

BASE = "http://test"
URL = "http://test/"
ENV_SECRET = "rfp-a2a-open-secret"
CARRIERS = ("carrier-a", "carrier-b", "carrier-c")
PRODUCT_LINES = ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY")
# Address corpus fixture (wiki/evals/address/expected.json): an accepted gov-id.
ACCEPTED = ("gov-id-clean", "images/gov-id-clean.svg")
HOST_ENV = (
    "BRIDGE_EMAIL_DIR",
    "BRIDGE_EMAIL_BUCKET",
    "BRIDGE_EMAIL_MAILBOX",
    "BRIDGE_EMAIL_TOKEN_SECRET",
    "BRIDGE_EMAIL_DRAIN_INTERVAL",
    "BRIDGE_EMAIL_DRAIN_URL",
    "BRIDGE_EMAIL_MAX_BYTES",
    "BRIDGE_SEAM_EMAIL_INGRESS",
    "BRIDGE_SEAM_EXTRACTION",
    "BRIDGE_SEAM_EXCHANGE_STORE",
    "BRIDGE_SEAM_TASK_STORE",
    "BRIDGE_SEAM_MODE",
    "BRIDGE_ARTIFACT_BUCKET",
    "BRIDGE_PUBLIC_URL",
    "BRIDGE_EXCHANGE_DB_URL",
    "BRIDGE_TASK_DB_URL",
    "BRIDGE_EDGE_MODE",
    "BRIDGE_SATISFACTION",
    "DATABASE_URL",
    RFP_PROCESS_ENV,
)


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def clean_env(monkeypatch):
    for name in HOST_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def dsn(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/rfp.db"


@pytest.fixture
def rfp_env(clean_env, dsn):
    """The A2A runtime's deployed shape locally: the RFP process + the durable DSN."""
    clean_env.setenv(RFP_PROCESS_ENV, PROCESS)
    clean_env.setenv("BRIDGE_EXCHANGE_DB_URL", dsn)
    return clean_env


@pytest.fixture
def host_env(rfp_env, tmp_path):
    """+ the board host's email intake (on-demand drain)."""
    rfp_env.setenv("BRIDGE_EMAIL_DIR", str(tmp_path / "mail"))
    rfp_env.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
    rfp_env.setenv("BRIDGE_EMAIL_TOKEN_SECRET", ENV_SECRET)
    rfp_env.setenv("BRIDGE_EMAIL_DRAIN_INTERVAL", "0")
    return rfp_env


@pytest.fixture(params=["deterministic", "hybrid"])
def mode(request):
    """Both edge modes; the RFP path must never reach the model."""
    return request.param


def _due() -> datetime:
    # The env-built board runs on the wall clock: a real future due date.
    return datetime.now(UTC) + timedelta(days=14)


def _requirements(
    pairs=None, status: RequirementStatus = RequirementStatus.required
) -> RequirementsList:
    pairs = pairs or [(p, line) for p in CARRIERS for line in PRODUCT_LINES]
    return RequirementsList(
        requirements=[
            Requirement(item=line, party=party, status=status) for party, line in pairs
        ]
    )


def _open_request(**kw) -> CollectRequest:
    fields = {
        "process": PROCESS,
        "party": "acme-broker",
        "due_at": _due(),
        "requirements": _requirements(),
    }
    fields.update(kw)
    return CollectRequest(**fields)


def _a2a(mode: str = "deterministic", **kw):
    """The A2A app + the LLM double that must stay unused."""
    llm = ScriptedLlm(max_calls=0)
    leg = LlmLeg(model=llm) if mode == "hybrid" else None
    app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        edge_mode=mode,
        llm_leg=leg,
        **kw,
    )
    return app, llm


def _close(app) -> None:
    board = app.state.rfp_board
    if board is not None:
        board.store.close()


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


def _message(parts, *, context_id: str = "") -> Message:
    return Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=parts,
        context_id=context_id,
    )


async def _send(client, parts, *, context_id: str = ""):
    """message/stream; return the task via tasks/get."""
    tid = None
    async for ev in client.send_message(
        SendMessageRequest(message=_message(parts, context_id=context_id))
    ):
        if ev.HasField("task"):
            tid = ev.task.id
        elif ev.HasField("status_update"):
            tid = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            tid = ev.artifact_update.task_id
    assert tid is not None
    return await client.get_task(GetTaskRequest(id=tid))


async def _rpc_send(hc, parts, *, context_id: str = "") -> dict:
    """A raw JSON-RPC ``SendMessage`` (message/send); return the response body."""
    msg = _message(parts, context_id=context_id)
    resp = await hc.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-1",
            "method": "SendMessage",
            "params": MessageToDict(SendMessageRequest(message=msg)),
        },
        headers={"A2A-Version": "1.0"},
    )
    return resp.json()


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


def _outstanding(turn: ExchangeTurn) -> dict[tuple[str, str], str]:
    assert turn.status.outstanding is not None
    return {
        (r.party, r.item): r.reason_code for r in turn.status.outstanding.requirements
    }


def _open_parts(**kw):
    return encode_collect_request(_open_request(**kw))


# --------------------------------------------------------------------------- #
# 1. The acceptance path (both edge modes)                                    #
# --------------------------------------------------------------------------- #
def test_mail_drained_by_board_host_files_onto_rfp_opened_through_a2a(
    host_env, tmp_path, mode, capsys
):
    a2a, llm = _a2a(mode)
    try:
        assert isinstance(a2a.state.rfp_board.store, SqlExchangeStore)

        async def open_it():
            async with _client(a2a) as (_hc, client):
                return await _send(client, _open_parts())

        task = asyncio.run(open_it())
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        turn = _turn(task)
        ctx = turn.context_id
        assert ctx == task.context_id
        assert turn.status.entries == []
        assert _outstanding(turn) == {
            (p, line): "invited" for p in CARRIERS for line in PRODUCT_LINES
        }
        assert turn.status.outstanding.done is False
        assert turn.terminal is False

        board_app = build_rfp_board_app(extraction=extractor())
        with TestClient(board_app) as client:
            rfps = client.get("/rfps").json()
            assert [r["rfp_id"] for r in rfps] == [ctx]
            body = client.get(f"/rfps/{ctx}").json()
            assert all(
                c["reply_address"].startswith("rfp-quotes+") for c in body["carriers"]
            )
            address = next(
                c["reply_address"]
                for c in body["carriers"]
                if c["party"] == "carrier-c"
            )
            eml = tmp_path / "reply.eml"
            eml.write_bytes(
                compose(
                    address,
                    message_id="<quote-1@carrier-c.example>",
                    sender="quotes@carrier-c.example",
                    attachments=quote_attachments(),
                )
            )
            assert cli(["land", str(eml)]) == 0
            key = landing_key(eml.read_bytes())
            assert capsys.readouterr().out.strip() == f"landed {key}"
            assert cli(["drain", "--url", "http://testserver"], client=client) == 0
            assert capsys.readouterr().out.strip().splitlines() == [
                f"filed {key} {ctx} carrier-c filed=2 unreadable=1",
                "drained 1",
            ]
            cells = {
                (c["party"], c["product_line"]): c["status"]
                for c in client.get(f"/rfps/{ctx}").json()["cells"]
            }
            assert cells[("carrier-c", "CRITICAL_ILLNESS")] == "received"
            assert cells[("carrier-c", "HOSPITAL_INDEMNITY")] == "incomplete"

        # A status read on the SAME context through A2A sees the drained record.
        async def status():
            async with _client(a2a) as (_hc, client):
                parts = encode_collect_request(CollectRequest(process=PROCESS))
                return await _send(client, parts, context_id=ctx)

        task2 = asyncio.run(status())
        assert task2.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        turn2 = _turn(task2)
        assert turn2.context_id == ctx
        out = _outstanding(turn2)
        assert ("carrier-c", "CRITICAL_ILLNESS") not in out
        assert out[("carrier-c", "HOSPITAL_INDEMNITY")] == "incomplete"
        assert len(turn2.status.entries) >= 1
    finally:
        _close(a2a)
    assert llm.call_count == 0, f"the RFP path reached the LLM leg ({mode})"


# --------------------------------------------------------------------------- #
# 2. Follow-up requirements (sense B)                                          #
# --------------------------------------------------------------------------- #
def test_follow_up_requirements_waive_and_add_a_carrier(rfp_env, dsn):
    a2a, _ = _a2a()
    try:

        async def go():
            async with _client(a2a) as (_hc, client):
                first = _turn(await _send(client, _open_parts()))
                follow = RequirementsList(
                    requirements=[
                        Requirement(
                            item="HOSPITAL_INDEMNITY",
                            party="carrier-a",
                            status=RequirementStatus.waived,
                        ),
                        Requirement(
                            item="CRITICAL_ILLNESS",
                            party="carrier-d",
                            status=RequirementStatus.required,
                        ),
                    ]
                )
                parts = encode_collect_request(
                    CollectRequest(process=PROCESS, requirements=follow)
                )
                second = _turn(await _send(client, parts, context_id=first.context_id))
                return first, second

        first, second = asyncio.run(go())
        out = _outstanding(second)
        assert ("carrier-a", "HOSPITAL_INDEMNITY") not in out
        assert out[("carrier-d", "CRITICAL_ILLNESS")] == "invited"
        assert len(out) == 6  # 6 - 1 waived + 1 new
    finally:
        _close(a2a)

    reader = SqlExchangeStore(dsn)
    try:
        rfp = RfpBoard(policy=load_rfp_policy(PROCESS), store=reader).rfps[
            first.context_id
        ]
        legs = {leg.party: leg.context_id for leg in rfp.legs}
        assert set(legs) == {*CARRIERS, "carrier-d"}
        assert len(set(legs.values())) == 4  # every carrier its own leg context
        assert rfp.cell("carrier-a", "HOSPITAL_INDEMNITY").requirement is (
            RequirementStatus.waived
        )
    finally:
        reader.close()


# --------------------------------------------------------------------------- #
# 3. An identical retried open is idempotent                                   #
# --------------------------------------------------------------------------- #
def test_retried_open_is_idempotent(rfp_env):
    a2a, _ = _a2a()
    store = a2a.state.rfp_board.store
    try:
        request = _open_request(context_id="acme-rfp-2027")

        async def go():
            async with _client(a2a) as (_hc, client):
                parts = encode_collect_request(request)
                one = _turn(await _send(client, parts))
                (rec1,) = store.list("rfp")
                two = _turn(await _send(client, parts))
                (rec2,) = store.list("rfp")
                return one, two, rec1, rec2

        one, two, rec1, rec2 = asyncio.run(go())
        assert one.context_id == two.context_id == "acme-rfp-2027"
        assert rec2.version == rec1.version
        assert len(rec2.doc["audit"]) == len(rec1.doc["audit"])
        assert one.status == two.status
        assert (one.terminal, one.requirements) == (two.terminal, two.requirements)
    finally:
        _close(a2a)


# --------------------------------------------------------------------------- #
# 4. Refusals → a JSON-RPC error, nothing stored                               #
# --------------------------------------------------------------------------- #
def _naive_due_parts():
    data = _open_request().model_dump(mode="json")
    data["due_at"] = (
        (datetime.now() + timedelta(days=14)).replace(microsecond=0).isoformat()
    )
    assert "+" not in data["due_at"] and not data["due_at"].endswith("Z")
    return [ph.new_data_part(data, media_type="application/json")]


def _file_parts():
    part = ph.new_url_part("images/quote.pdf", media_type="application/pdf")
    part.metadata.CopyFrom(ParseDict({"doc_id": "quote"}, struct_pb2.Struct()))
    return [*_open_parts(), part]


REFUSALS = {
    "no-requirements": (
        lambda: _open_parts(requirements=None),
        -32602,
        "invalid_rfp_request: ",
    ),
    "no-due-at": (lambda: _open_parts(due_at=None), -32602, "invalid_rfp_request: "),
    "naive-due-at": (_naive_due_parts, -32602, "invalid_rfp_request: "),
    "party-less-item": (
        lambda: _open_parts(
            requirements=RequirementsList(
                requirements=[
                    Requirement(
                        item="CRITICAL_ILLNESS", status=RequirementStatus.required
                    )
                ]
            )
        ),
        -32602,
        "invalid_rfp_request: ",
    ),
    "duplicate-item": (
        lambda: _open_parts(
            requirements=_requirements([("carrier-a", "CRITICAL_ILLNESS")] * 2)
        ),
        -32602,
        "invalid_rfp_request: ",
    ),
    "file-part": (_file_parts, -32602, "rfp_submissions_unsupported: "),
    "bad-context": (
        lambda: _open_parts(context_id="a/b"),
        -32602,
        "invalid_rfp_context: ",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_refusal_is_a_jsonrpc_error_and_stores_nothing(rfp_env, mode, case):
    build, code, prefix = REFUSALS[case]
    a2a, llm = _a2a(mode)
    store = a2a.state.rfp_board.store
    try:

        async def go():
            async with _client(a2a) as (hc, _c):
                return await _rpc_send(hc, build())

        body = asyncio.run(go())
        assert "error" in body, body
        assert body["error"]["code"] == code, body
        assert body["error"]["message"].startswith(prefix), body
        assert store.list("rfp") == []
    finally:
        _close(a2a)
    assert llm.call_count == 0
    if case == "file-part":
        assert body["error"]["message"] == RFP_SUBMISSIONS_TEXT


# --------------------------------------------------------------------------- #
# 5. Other processes stay on the Collector                                     #
# --------------------------------------------------------------------------- #
def test_other_processes_stay_on_the_collector(clean_env, mode):
    board = RfpBoard(policy=load_rfp_policy(PROCESS))
    a2a, llm = _a2a(mode, rfp_board=board)
    assert a2a.state.rfp_board is board

    async def go():
        async with _client(a2a) as (_hc, client):
            parts = encode_collect_request(
                CollectRequest(process="address-proof", party="jordan-lee")
            )
            part = ph.new_url_part(ACCEPTED[1], media_type="image/svg+xml")
            part.metadata.CopyFrom(
                ParseDict({"doc_id": ACCEPTED[0]}, struct_pb2.Struct())
            )
            return await _send(client, [*parts, part])

    task = asyncio.run(go())
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    (entry,) = _turn(task).status.entries
    assert (entry.doc_id, entry.disposition.value) == (ACCEPTED[0], "accepted")
    assert board.store.list("rfp") == []
    assert llm.call_count == 0


# --------------------------------------------------------------------------- #
# 6. No env → no opener                                                        #
# --------------------------------------------------------------------------- #
def test_no_env_no_opener(clean_env, dsn):
    clean_env.setenv("BRIDGE_EXCHANGE_DB_URL", dsn)
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    a2a = build_a2a_app(url=URL, executor=executor, edge_mode="deterministic")
    assert a2a.state.rfp_board is None
    assert executor.rfp_board is None

    async def go():
        async with _client(a2a) as (_hc, client):
            return await _send(client, _open_parts())

    task = asyncio.run(go())
    turn = _turn(task)
    assert task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED
    reasons = [r.reason_code for r in (turn.status.outstanding.requirements or [])]
    assert "invited" not in reasons
    reader = SqlExchangeStore(dsn)
    try:
        assert reader.list("rfp") == []
    finally:
        reader.close()


# --------------------------------------------------------------------------- #
# 7. rfp_board_from_env                                                        #
# --------------------------------------------------------------------------- #
def test_rfp_board_from_env_unset_or_blank_is_none(clean_env):
    assert rfp_board_from_env() is None
    clean_env.setenv(RFP_PROCESS_ENV, "   ")
    assert rfp_board_from_env() is None


def test_rfp_board_from_env_refuses_an_in_memory_store(clean_env):
    clean_env.setenv(RFP_PROCESS_ENV, PROCESS)
    with pytest.raises(RuntimeError, match="BRIDGE_EXCHANGE_DB_URL"):
        rfp_board_from_env()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        rfp_board_from_env(exchange_store=InMemoryExchangeStore())
    with pytest.raises(RuntimeError, match="BRIDGE_EXCHANGE_DB_URL"):
        build_a2a_app(url=URL, edge_mode="deterministic")


def test_rfp_board_from_env_over_a_dsn(rfp_env):
    board = rfp_board_from_env()
    try:
        assert isinstance(board, RfpBoard)
        assert board.policy.process == PROCESS
        assert isinstance(board.store, SqlExchangeStore)
    finally:
        board.store.close()


def test_a_non_emergent_collect_skill_fails_at_build(clean_env, dsn):
    clean_env.setenv("BRIDGE_EXCHANGE_DB_URL", dsn)
    with pytest.raises(ValueError, match="address-proof"):
        rfp_board_from_env("address-proof")
    clean_env.setenv(RFP_PROCESS_ENV, "address-proof")
    with pytest.raises(ValueError, match="address-proof"):
        build_a2a_app(url=URL, edge_mode="deterministic")


def test_gcp_without_database_url_is_refused(clean_env):
    clean_env.setenv(RFP_PROCESS_ENV, PROCESS)
    clean_env.setenv("BRIDGE_SEAM_MODE", "gcp")
    with pytest.raises(RuntimeError):
        rfp_board_from_env()


# --------------------------------------------------------------------------- #
# 8. CAS                                                                       #
# --------------------------------------------------------------------------- #
class _Wrapping(ExchangeStore):
    """Delegates to ``inner``; ``before_put`` runs first and may raise / race."""

    def __init__(self, inner: ExchangeStore, before_put=None) -> None:
        self.inner = inner
        self.before_put = before_put
        self.puts = 0

    def get(self, kind, ref):
        return self.inner.get(kind, ref)

    def list(self, kind):
        return self.inner.list(kind)

    def put(self, kind, ref, *, context_id, doc, expected_version):
        self.puts += 1
        if self.before_put is not None:
            self.before_put(self.puts)
        return self.inner.put(
            kind, ref, context_id=context_id, doc=doc, expected_version=expected_version
        )


def test_a_racing_create_retries_as_an_update():
    inner = InMemoryExchangeStore()
    cid = "acme-race-2027"

    def race(n: int) -> None:
        if n == 1:  # another replica opens the same RFP (carrier-a only) first
            other = RfpBoard(policy=load_rfp_policy(PROCESS), store=inner)
            request = _open_request(
                context_id=cid,
                requirements=_requirements([("carrier-a", "CRITICAL_ILLNESS")]),
            )
            other.put(open_rfp(request, now=datetime.now(UTC), rfp_id=cid))

    store = _Wrapping(inner, race)
    board = RfpBoard(policy=load_rfp_policy(PROCESS), store=store)
    turn = open_or_update_rfp(board, _open_request(), context_id=cid)
    assert store.puts == 2  # the create lost, the retry updated
    assert len(_outstanding(turn)) == 6
    (rec,) = inner.list("rfp")
    assert rec.version == 2
    assert len(rec.doc["cells"]) == 6


def _always_stale(n: int) -> None:
    raise StaleExchangeWrite("rfp", "x", n)


def test_exhausted_cas_retry_raises():
    store = _Wrapping(InMemoryExchangeStore(), _always_stale)
    board = RfpBoard(policy=load_rfp_policy(PROCESS), store=store)
    with pytest.raises(StaleExchangeWrite):
        open_or_update_rfp(board, _open_request(), context_id="acme-stale")
    assert store.puts == 3


def test_exhausted_cas_retry_is_a_jsonrpc_conflict(clean_env):
    inner = InMemoryExchangeStore()
    board = RfpBoard(
        policy=load_rfp_policy(PROCESS), store=_Wrapping(inner, _always_stale)
    )
    a2a, _ = _a2a(rfp_board=board)

    async def go():
        async with _client(a2a) as (hc, _c):
            return await _rpc_send(hc, _open_parts(context_id="acme-stale"))

    body = asyncio.run(go())
    assert body["error"]["code"] == -32600, body
    assert body["error"]["message"].startswith("rfp_changed_concurrently: "), body
    assert inner.list("rfp") == []


# --------------------------------------------------------------------------- #
# 9. An injected board wins                                                    #
# --------------------------------------------------------------------------- #
def test_injected_board_wins(clean_env):
    board = RfpBoard(policy=load_rfp_policy(PROCESS))  # in-memory: accepted as-is
    a2a, _ = _a2a(rfp_board=board)
    assert a2a.state.rfp_board is board

    async def go():
        async with _client(a2a) as (_hc, client):
            return await _send(client, _open_parts())

    task = asyncio.run(go())
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert [rec.ref for rec in board.store.list("rfp")] == [task.context_id]
