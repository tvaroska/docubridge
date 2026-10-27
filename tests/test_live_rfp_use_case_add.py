"""CUJ-9 board companion: a live-added RFP use case opens on the shared board next to a
running exchange (S4-usecase-4).

The synthetic ``supplier-onboarding`` use case (two doctype skills, then the process) is
added live while an Address exchange is parked, and an RFP for it is opened through the
A2A Requirements path onto the shared board. One ``build_a2a_app`` (hybrid edge,
``ScriptedLlm``) and one RFP board host (``build_rfp_board_app``, home process
``benefits-rfp``) share one sqlite exchange store; the carrier checklist is served by
``build_portal_app`` over the board host's board. The phases:

- **build / red-before**: neither board serves ``supplier-onboarding``, ``GET /rfps`` does
  not list it, the card does not advertise it;
- **park** an Address exchange (PowerCo accepted, AquaUtil parked on the R3 HITL handoff);
  a control core runs the same park + resolution BEFORE the upload (``BRIDGE_SKILLS_DIR``
  is process-global);
- **upload** the use case through the ``skill_registry`` port — same apps, no rebuild;
- **A2A open** ``onboard-live-2027`` with Requirements (supplier-x / supplier-y × W9 / COI):
  ``INPUT_REQUIRED``, 4 × ``invited``, model-free;
- the board host lists it (process ``supplier-onboarding``); the carrier checklist accepts
  the live label space (``["w9-form", "insurance-cert"]``, never ``benefits-quote``);
- an emailed W-9 is filed with the live ``item_field: credential`` (``product_line ==
  "W9"``) and the A2A status read on the same context sees it (one shared record);
- the parked Address exchange resumes to the control's terminal outcome.

Traps (hard-fail): the app rebuilt (``build_a2a_app`` patched to raise, no
``CollectExecutor`` constructed), the in-flight task disturbed by the upload (proto-equal
snapshot, no new model call), the new RFP opened but projected / filed with the home
process's policy (``benefits-quote`` accept list, ``product_line`` lookup).

The scenario runs locally, through the gcp ``skill_registry`` adapter over a fake bucket
(credential-free: the policy can only have come through the port), and as the gated port
parity param against a provisioned bucket — terminal-outcome parity, never
ledger-identical.

Synthetic data only (``jordan-lee``; suppliers ``supplier-x`` / ``supplier-y``;
``acme-buyer``).
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from a2a.types import GetTaskRequest, TaskState

from bridge.dashboard.board_host import RFP_PROCESS_ENV, build_rfp_board_app
from bridge.edge import encode_collect_request
from bridge.edge.skills import loaded_skills
from bridge.edge.wire import META_REASON_CODE, META_REVIEW_CALL_ID
from bridge.email_ingress.mime import landing_key
from bridge.exchange_store import SqlExchangeStore
from bridge.portal.server import build_portal_app
from bridge.ports import Port, PortMode, get_adapter, resolve_mode
from bridge.ports.gcp import SKILLS_BUCKET_ENV, GcpSkillRegistry
from bridge.sla import SKILLS_DIR
from contract.enums import Disposition, RequirementStatus
from contract.wire import CollectRequest, Requirement, RequirementsList
from tests import _a2a_live as a2a
from tests._a2a_live import ADDRESS_CTX, ADDRESS_SKILLS, BASE, ENV_TO_CLEAR, REVIEWER
from tests._fake_gcs import FakeBucket, seed_from_dir
from tests._skill_fixtures import (
    CANDIDATES,
    PROCESS,
    StubExtraction,
    install_supplier_onboarding,
    supplier_result,
)
from tests.email_fixtures import compose
from tests.test_rfp_a2a_open import HOST_ENV

HOME = "benefits-rfp"
RFP_ID = "onboard-live-2027"
BUYER = "acme-buyer"
SUPPLIERS = ("supplier-x", "supplier-y")
ITEMS = ("W9", "COI")
MAILBOX = "onboarding@buyer.example"
SECRET = "live-rfp-use-case-secret"
SUPPLIER_PREFIXES = (*CANDIDATES, PROCESS)
EXPECTED_OUTCOME = {
    "address": ("TASK_STATE_COMPLETED", "needs_review", ["aqua-util", "power-co"]),
    "rfp_listed": True,
    "checklist_accept": ["w9-form", "insurance-cert"],
    "email": ("filed", "W9", "received"),
    "open_outstanding": 4,
}


@pytest.fixture
def live_rfp_env(tmp_path, monkeypatch):
    """The deployed shape locally: home RFP process + the durable shared store + email."""
    for name in (*HOST_ENV, *ENV_TO_CLEAR):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "skills"
    root.mkdir()
    for name in (*ADDRESS_SKILLS, HOME, "benefits-quote"):
        shutil.copytree(SKILLS_DIR / name, root / name)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(root))
    monkeypatch.setenv(RFP_PROCESS_ENV, HOME)
    monkeypatch.setenv("BRIDGE_EXCHANGE_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    monkeypatch.setenv("BRIDGE_EMAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
    monkeypatch.setenv("BRIDGE_EMAIL_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("BRIDGE_EMAIL_DRAIN_INTERVAL", "0")
    return root


def _open_request() -> CollectRequest:
    # The env-built boards run on the wall clock: a real future due date.
    return CollectRequest(
        process=PROCESS,
        party=BUYER,
        context_id=RFP_ID,
        due_at=datetime.now(UTC) + timedelta(days=14),
        requirements=RequirementsList(
            requirements=[
                Requirement(item=item, party=s, status=RequirementStatus.required)
                for s in SUPPLIERS
                for item in ITEMS
            ]
        ),
    )


def _outstanding(exchange_turn) -> dict[tuple[str, str], str]:
    assert exchange_turn.status.outstanding is not None
    return {
        (r.party, r.item): r.reason_code
        for r in exchange_turn.status.outstanding.requirements
    }


def _cells(body: dict) -> dict[tuple[str, str], str]:
    return {(c["party"], c["product_line"]): c["status"] for c in body["cells"]}


def _host(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _live_rfp_add(monkeypatch, *, control: bool = True) -> dict[str, object]:
    """The whole scenario, asserted inline; returns its terminal-outcome summary."""
    # --- 0. Build: one A2A edge + one board host over the same sqlite store ----------
    live = a2a.core()
    stores = [live.app.state.rfp_board.store]
    extraction = StubExtraction({"w9.pdf": supplier_result("w9-form", "W9")})
    board_app = build_rfp_board_app(extraction=extraction)
    stores.append(board_app.state.rfps.store)
    try:
        edge_board = live.app.state.rfp_board
        board = board_app.state.rfps
        assert edge_board.live_policy is not None
        assert board.live_policy is not None
        assert isinstance(edge_board.store, SqlExchangeStore)
        assert isinstance(board.store, SqlExchangeStore)
        portal = build_portal_app(rfps=board)

        async with (
            a2a.client(live.app, REVIEWER) as (hc, client),
            _host(board_app) as host,
            _host(portal) as carrier,
        ):
            # --- 1. Red-before -------------------------------------------------------
            assert edge_board.serves(PROCESS) is False
            assert board.serves(PROCESS) is False
            assert edge_board.serves(HOME) is True
            listed = (await host.get("/rfps")).json()
            assert RFP_ID not in {r["rfp_id"] for r in listed}
            assert PROCESS not in await a2a.card_skills(hc)

            # --- 2. Park the Address leg --------------------------------------------
            parked = await a2a.park(client, ctx=ADDRESS_CTX)
            status_meta = a2a.meta(parked.status.message)
            assert status_meta[META_REASON_CODE] == "awaiting_human_review"
            call_id = status_meta[META_REVIEW_CALL_ID]
            assert call_id
            parked_before = await client.get_task(GetTaskRequest(id=parked.id))
            calls_parked = live.llm.call_count

            # --- 3. Control: the same exchange, no upload, to its terminal outcome ---
            # Load-bearing order: BRIDGE_SKILLS_DIR is process-global.
            control_task = None
            if control:
                control_core = a2a.core()
                stores.append(control_core.app.state.rfp_board.store)
                control_task = await a2a.control_outcome(control_core)
            assert PROCESS not in {s.name for s in loaded_skills()}

            # --- 4. Trap guards: nothing may be rebuilt or reconstructed -------------
            constructed = a2a.arm_trap_guards(monkeypatch)

            # --- 5. Upload the use case through the skill_registry port --------------
            registry = get_adapter(
                Port.skill_registry, resolve_mode(Port.skill_registry)
            )
            metas = install_supplier_onboarding(registry)
            assert [m.kind for m in metas] == ["doctype", "doctype", "process"]
            assert await client.get_task(GetTaskRequest(id=parked.id)) == parked_before
            assert live.llm.call_count == calls_parked
            assert edge_board.serves(PROCESS) is True  # no rebuild: read per call
            assert board.serves(PROCESS) is True

            # --- 6. A2A open on the SAME edge client ---------------------------------
            opened = await a2a.send(
                client, encode_collect_request(_open_request()), context_id=RFP_ID
            )
            assert opened.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            open_turn = a2a.turn(opened)
            assert open_turn.context_id == RFP_ID
            assert open_turn.status.entries == []
            open_outstanding = _outstanding(open_turn)
            assert open_outstanding == {
                (s, item): "invited" for s in SUPPLIERS for item in ITEMS
            }
            assert all(
                r.doctype_hint is None
                for r in open_turn.status.outstanding.requirements
            )  # two candidate doctypes: no single hint
            assert open_turn.terminal is False
            assert live.llm.call_count == calls_parked  # model-free
            card = await a2a.card_skills(hc)
            assert {"doctype:w9-form", "doctype:insurance-cert"} <= set(card[PROCESS])

            # --- 7. The board host lists it ------------------------------------------
            listed = {r["rfp_id"]: r for r in (await host.get("/rfps")).json()}
            assert listed[RFP_ID]["process"] == PROCESS
            rfp_listed = listed[RFP_ID]["process"] == PROCESS
            body = (await host.get(f"/rfps/{RFP_ID}")).json()
            assert {c["party"] for c in body["carriers"]} == set(SUPPLIERS)
            assert body["waiting"] == len(SUPPLIERS) * len(ITEMS)
            assert all(c["reply_address"] for c in body["carriers"])
            exchanges = (await host.get("/exchanges")).json()
            assert RFP_ID in str(exchanges)
            assert board.serves(HOME) is True  # the home path is unaffected

            # --- 8. The carrier checklist accepts the LIVE label space ---------------
            ctx_x = board.rfps[RFP_ID].leg_for("supplier-x").context_id
            checklist = (await carrier.get(f"/rfp/legs/{ctx_x}")).json()
            assert checklist["kind"] == "rfp_checklist"
            assert [i["product_line"] for i in checklist["items"]] == list(ITEMS)
            (action,) = checklist["actions"]
            assert action["kind"] == "upload"
            assert action["accept"] == list(CANDIDATES)  # never ["benefits-quote"]
            checklist_accept = action["accept"]

            # --- 9. Email intake files with the live item_field ----------------------
            address_y = next(
                c["reply_address"]
                for c in body["carriers"]
                if c["party"] == "supplier-y"
            )
            raw = compose(
                address_y,
                message_id="<w9-1@supplier-y.example>",
                sender="ap@supplier-y.example",
                subject="Re: supplier onboarding",
                attachments=[("w9.pdf", b"%PDF-1.4 synthetic w9", "application/pdf")],
            )
            assert board_app.state.email_processor.port.land(landing_key(raw), raw)
            (outcome,) = (await host.post("/email/drain")).json()
            assert outcome["status"] == "filed", outcome
            assert (outcome["rfp_id"], outcome["party"]) == (RFP_ID, "supplier-y")
            (filed,) = outcome["filed"]
            assert filed["disposition"] == "accepted"
            assert filed["product_line"] == "W9"  # the live item_field: credential
            (called,) = extraction.calls
            assert called.endswith("/w9.pdf")
            cells = _cells((await host.get(f"/rfps/{RFP_ID}")).json())
            assert cells[("supplier-y", "W9")] == "received"
            assert cells[("supplier-y", "COI")] == "acknowledged"  # replied, no COI yet
            assert {cells[("supplier-x", item)] for item in ITEMS} == {"invited"}

            # --- 10. A2A status read: edge and board host share one record -----------
            read = await a2a.send(
                client,
                encode_collect_request(CollectRequest(process=PROCESS)),
                context_id=RFP_ID,
            )
            assert read.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            read_out = _outstanding(a2a.turn(read))
            assert ("supplier-y", "W9") not in read_out
            assert len(read_out) == len(SUPPLIERS) * len(ITEMS) - 1
            assert live.llm.call_count == calls_parked

            # --- 11. Resume the parked Address leg -----------------------------------
            task = await a2a.send(
                client,
                a2a.resolution(call_id),
                context_id=parked.context_id,
                task_id=parked.id,
            )
            assert task.id == parked.id
            if control_task is not None:
                a2a.assert_same_outcome(task, control_task)
            resumed = a2a.turn(task)
            assert task.status.state == TaskState.TASK_STATE_COMPLETED
            reason = a2a.meta(task.status.message)[META_REASON_CODE]
            assert reason == "needs_review"
            assert [e.disposition for e in resumed.status.entries] == [
                Disposition.accepted
            ] * 2
            issuers = sorted(e.issuer for e in resumed.status.entries)
            assert issuers == ["aqua-util", "power-co"]
            assert resumed.terminal is True

        # --- 12. Identity traps ------------------------------------------------------
        assert constructed == []
        # park, open, status read, resolve — one executor saw them all.
        assert len(live.executor.executed) == 4
        assert live.executor.executed[0] == live.executor.executed[3] == parked.id
        assert live.executor.llm_leg is live.leg

        # --- 13. The terminal-outcome summary ----------------------------------------
        return {
            "address": (TaskState.Name(task.status.state), reason, issuers),
            "rfp_listed": rfp_listed,
            "checklist_accept": checklist_accept,
            "email": (
                outcome["status"],
                filed["product_line"],
                cells[("supplier-y", "W9")],
            ),
            "open_outstanding": len(open_outstanding),
        }
    finally:
        for store in stores:
            store.close()


# --------------------------------------------------------------------------- #
# The Driver (the S4-usecase-5 companion node)                                 #
# --------------------------------------------------------------------------- #
async def test_live_rfp_use_case_opens_on_the_board_next_to_a_running_exchange(
    live_rfp_env, monkeypatch
):
    assert await _live_rfp_add(monkeypatch) == EXPECTED_OUTCOME


# --------------------------------------------------------------------------- #
# Through the gcp skill_registry adapter, credential-free                      #
# --------------------------------------------------------------------------- #
async def test_live_rfp_policy_comes_through_the_gcp_registry_adapter(
    live_rfp_env, monkeypatch
):
    fake = FakeBucket()
    monkeypatch.setattr(GcpSkillRegistry, "_bucket", lambda self: fake)
    seed_from_dir(fake, SKILLS_DIR, ADDRESS_SKILLS)
    monkeypatch.setenv("BRIDGE_PORT_SKILL_REGISTRY", "gcp")
    monkeypatch.setenv(SKILLS_BUCKET_ENV, "fake-skills")

    assert await _live_rfp_add(monkeypatch, control=False) == EXPECTED_OUTCOME
    assert f"{PROCESS}/assets/policy.yaml" in fake.objects
    # The policy can only have come through the port: the filesystem never had it.
    assert not (Path(os.environ["BRIDGE_SKILLS_DIR"]) / PROCESS).exists()


# --------------------------------------------------------------------------- #
# Port parity (terminal outcome; gcp gated on BRIDGE_TEST_GCP + a bucket)      #
# --------------------------------------------------------------------------- #
def _gcp_bucket_or_skip() -> str:
    bucket = os.environ.get(SKILLS_BUCKET_ENV, "").strip()
    if not bucket:
        pytest.skip(
            f"set {SKILLS_BUCKET_ENV}=<provisioned skills bucket> (a P7-9 input) to run "
            "the gcp live RFP use-case-add param"
        )
    return bucket


@pytest.mark.port("skill_registry")
async def test_live_rfp_use_case_add_terminal_outcome_parity(
    adapter, live_rfp_env, monkeypatch
):
    if adapter is PortMode.local:
        assert await _live_rfp_add(monkeypatch, control=False) == EXPECTED_OUTCOME
        return

    _gcp_bucket_or_skip()
    monkeypatch.setenv("BRIDGE_PORT_SKILL_REGISTRY", "gcp")
    reg = get_adapter(Port.skill_registry, PortMode.gcp)
    names = {s.name for s in reg.list_skills()}
    if not set(ADDRESS_SKILLS) <= names:
        pytest.skip(f"the skills bucket must hold {ADDRESS_SKILLS} for this param")
    bucket = reg._bucket()
    if any(
        next(iter(bucket.list_blobs(prefix=f"{name}/")), None) is not None
        for name in SUPPLIER_PREFIXES
    ):
        pytest.skip(
            "the skills bucket already holds a supplier-onboarding skill; refusing to "
            "touch blobs this test did not write"
        )
    try:
        assert await _live_rfp_add(monkeypatch, control=False) == EXPECTED_OUTCOME
    finally:
        for name in SUPPLIER_PREFIXES:
            for blob in bucket.list_blobs(prefix=f"{name}/"):
                blob.delete()
