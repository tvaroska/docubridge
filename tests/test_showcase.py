"""The showcase runner: one core, one card, one dashboard, three demos (S0-arch-20).

Drives ``bridge.demos.showcase.build_showcase_app`` in-process over
``httpx.ASGITransport``. Proves: ONE Agent Card advertises ``address-proof``,
``benefits-procurement`` and ``benefits-rfp``; ONE dashboard serves the seeded Address
exchanges (``/exchanges``), the Benefits program (``/programs``) and the RFP (``/rfps``);
an A2A Address task, an RFP opened over A2A, a portal upload and a dashboard read run
concurrently on one app, and every write lands on the stores the dashboard reads; the RFP
seed is idempotent over a durable store; a naive ``now`` is refused; and
``scripts/run-showcase.sh`` boots this factory. ``asyncio_mode="auto"`` (no marker on
``async def test_*``).

Synthetic data only (the Address / benefits eval corpora, read-only).
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import Collector
from bridge.demos.showcase import build_showcase_app
from bridge.edge import CollectExecutor, encode_collect_request
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from bridge.exchange_store import SqlExchangeStore
from bridge.extraction.fixture import FixtureExtractor
from contract.enums import RequirementStatus
from contract.wire import CollectRequest, ExchangeTurn, Requirement, RequirementsList

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run-showcase.sh"
BASE = "http://test"
URL = "http://test/"
SEEDED_RFP = "acme-vb-2027"
LIVE_RFP = "showcase-live-rfp"
PROGRAM_ID = "acme-2026"
DEMO_SKILLS = {"address-proof", "benefits-procurement", "benefits-rfp"}
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
    "BRIDGE_SEAM_MODE",
    "BRIDGE_SEAM_EXTRACTION",
    "BRIDGE_SEAM_EXCHANGE_STORE",
    "BRIDGE_SEAM_TASK_STORE",
    "BRIDGE_EXTRACTION_ENGINE",
    "BRIDGE_EXCHANGE_DB_URL",
    "BRIDGE_TASK_DB_URL",
    "BRIDGE_PUBLIC_URL",
    "BRIDGE_EDGE_MODE",
    "BRIDGE_SATISFACTION",
    "BRIDGE_RFP_PROCESS",
    "BRIDGE_SKILLS_DIR",
    "DATABASE_URL",
    "BRIDGE_ARTIFACT_BUCKET",
)


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def clean_env(monkeypatch):
    for name in HOST_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _app(**kw):
    """The showcase host with a fixture-engine edge executor (model-free Address)."""
    return build_showcase_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        max_ticks=5,
        **kw,
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
        client = await ClientFactory(config).create_from_url(URL)
        yield hc, client


def _url_doc_part(doc_id: str, uri: str, mime: str = "image/svg+xml"):
    part = ph.new_url_part(uri, media_type=mime)
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    return part


async def _send(client, parts, *, context_id: str = ""):
    """message/stream; return the task via tasks/get."""
    message = Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=parts,
        context_id=context_id,
    )
    tid = None
    async for ev in client.send_message(SendMessageRequest(message=message)):
        if ev.HasField("task"):
            tid = ev.task.id
        elif ev.HasField("status_update"):
            tid = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            tid = ev.artifact_update.task_id
    assert tid is not None
    return await client.get_task(GetTaskRequest(id=tid))


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


def _rfp_open_parts():
    """A benefits-rfp CollectRequest: 3 carriers x 2 lines, due 14 days out."""
    return encode_collect_request(
        CollectRequest(
            process="benefits-rfp",
            party="acme-broker",
            due_at=datetime.now(UTC) + timedelta(days=14),
            requirements=RequirementsList(
                requirements=[
                    Requirement(
                        item=line, party=party, status=RequirementStatus.required
                    )
                    for party in CARRIERS
                    for line in PRODUCT_LINES
                ]
            ),
        )
    )


def _leg(program: dict, party: str) -> dict:
    return next(leg for leg in program["legs"] if leg["party"] == party)


# --------------------------------------------------------------------------- #
# 1. One card                                                                  #
# --------------------------------------------------------------------------- #
async def test_one_card_advertises_all_three_demos(clean_env):
    app = _app()
    async with _client(app) as (hc, _):
        resp = await hc.get("/.well-known/agent-card.json")
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert {s["id"] for s in card["skills"]} >= DEMO_SKILLS
    assert [i["url"] for i in card["supportedInterfaces"]] == [URL]


# --------------------------------------------------------------------------- #
# 2. One dashboard, all three seeded                                           #
# --------------------------------------------------------------------------- #
async def test_one_dashboard_serves_all_three_seeded(clean_env):
    app = _app()
    async with _client(app) as (hc, _):
        exchanges, programs, rfps = await asyncio.gather(
            hc.get("/ops/exchanges"), hc.get("/ops/programs"), hc.get("/ops/rfps")
        )
        hitl = await hc.get("/ops/queues/hitl")
    for resp in (exchanges, programs, rfps, hitl):
        assert resp.status_code == 200, resp.text

    rows = exchanges.json()
    address = {
        r["session_id"]: r["status"] for r in rows if r["process"] == "address-proof"
    }
    done_sid, hitl_sid = app.state.address_session_ids
    assert address == {done_sid: "done", hitl_sid: "in_flight"}
    assert any(
        r["session_id"] == SEEDED_RFP and r["process"] == "benefits-rfp" for r in rows
    )

    body = programs.json()
    assert len(body) == 1
    program = body[0]
    assert program["program_id"] == PROGRAM_ID
    assert len(program["legs"]) == 4
    carrier_b = _leg(program, "carrier-b")
    assert carrier_b["verdict"]["route"] == "negotiate"
    gap_ids = [g["rule_id"] for g in carrier_b["verdict"]["gaps"]]
    assert "no_orthodontics_exclusion" in gap_ids

    assert [r["rfp_id"] for r in rfps.json()] == [SEEDED_RFP]
    assert hitl_sid in {item["session_id"] for item in hitl.json()}


# --------------------------------------------------------------------------- #
# 3. All three exercised concurrently on one core (the acceptance)            #
# --------------------------------------------------------------------------- #
async def test_exercises_all_three_concurrently_on_one_core(clean_env):
    app = _app()
    async with _client(app) as (hc, client):

        async def address_over_a2a():
            parts = encode_collect_request(CollectRequest(process="address-proof"))
            parts.append(_url_doc_part(*ACCEPTED))
            return await _send(client, parts)

        async def rfp_over_a2a():
            return await _send(client, _rfp_open_parts(), context_id=LIVE_RFP)

        async def address_over_portal():
            created = await hc.post(
                "/portal/sessions", json={"process": "address-proof"}
            )
            assert created.status_code == 200, created.text
            sid = created.json()["session_id"]
            up = await hc.post(
                f"/portal/sessions/{sid}/uploads",
                json={"doc_id": "d1", "artifact_name": "bill-powerco-clean"},
            )
            assert up.status_code == 200, up.text
            return sid

        address_task, rfp_task, portal_sid, programs = await asyncio.gather(
            address_over_a2a(),
            rfp_over_a2a(),
            address_over_portal(),
            hc.get("/ops/programs"),
        )

        assert address_task.status.state == TaskState.TASK_STATE_COMPLETED
        assert rfp_task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert _turn(rfp_task).context_id == LIVE_RFP
        assert programs.status_code == 200, programs.text

        # The ONE dashboard sees every write.
        rfps = await hc.get("/ops/rfps")
        exchanges = await hc.get("/ops/exchanges")
        programs_after = await hc.get("/ops/programs")
        live = await hc.get(f"/ops/rfps/{LIVE_RFP}")

    # The RFP opened through the edge is on the board the dashboard reads.
    assert {r["rfp_id"] for r in rfps.json()} >= {SEEDED_RFP, LIVE_RFP}
    rows = exchanges.json()
    address = {
        r["session_id"]: r["status"] for r in rows if r["process"] == "address-proof"
    }
    assert len(address) == 3
    assert address[portal_sid] == "done"
    rfp_rows = {r["session_id"] for r in rows if r["process"] == "benefits-rfp"}
    assert rfp_rows >= {SEEDED_RFP, LIVE_RFP}
    assert [p["program_id"] for p in programs_after.json()] == [PROGRAM_ID]
    assert live.status_code == 200, live.text
    assert live.json()["process"] == "benefits-rfp"


# --------------------------------------------------------------------------- #
# 4. The RFP seed is idempotent over a durable store                           #
# --------------------------------------------------------------------------- #
async def test_seeds_are_idempotent_over_a_durable_store(clean_env, tmp_path):
    clean_env.setenv("BRIDGE_EXCHANGE_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/rfp.db")
    first = _app()
    try:
        assert isinstance(first.state.rfps.store, SqlExchangeStore)
        audit = len(first.state.rfps.rfps[SEEDED_RFP].audit)
    finally:
        first.state.rfps.store.close()

    second = _app()  # same env, a fresh store handle: the seed must not re-run
    try:
        async with _client(second) as (hc, _):
            rfps = (await hc.get("/ops/rfps")).json()
        assert [r["rfp_id"] for r in rfps] == [SEEDED_RFP]
        assert len(second.state.rfps.rfps[SEEDED_RFP].audit) == audit
    finally:
        second.state.rfps.store.close()


# --------------------------------------------------------------------------- #
# 5. A naive clock pin is refused                                              #
# --------------------------------------------------------------------------- #
def test_naive_now_refused(clean_env):
    with pytest.raises(ValueError, match="timezone-aware"):
        build_showcase_app(now=datetime(2026, 1, 1))


# --------------------------------------------------------------------------- #
# 6. The script boots this factory                                             #
# --------------------------------------------------------------------------- #
def test_run_showcase_script_boots_the_showcase_factory():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    text = SCRIPT.read_text()
    target = "bridge.demos.showcase:build_showcase_app"
    assert f"--factory {target}" in text
    module, attr = target.split(":")
    assert callable(getattr(importlib.import_module(module), attr))
    assert 'source "${REPO_ROOT}/scripts/_dev-env.sh"' in text
    assert "export BRIDGE_PUBLIC_URL=" in text
