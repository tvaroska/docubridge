"""A second emergent-Collect use case runs with ZERO ``bridge/`` edits (S0-arch-15).

The reusability proof behind ``design/bridge.md`` ("skills plus a thin driver … with no
change to the core") and ``design/patterns.md`` ("a new industry is new skills, not new
flow"): a synthetic ``supplier-onboarding`` process skill — written into ``tmp_path``, never
into ``skills/`` — declares its own label space (``w9-form`` + ``insurance-cert``) and item
key (``rfp.item_field: credential``), and runs through the RFP aggregate, the board, the A2A
turn, the email intake drain, the carrier checklist and the dashboard read API exactly as
``benefits-rfp`` does. Nothing in the core names either skill.

Synthetic data only (suppliers ``supplier-x`` / ``supplier-y``; no ``wiki/evals/`` writes).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from google.adk.artifacts import InMemoryArtifactService

from bridge.collect import project_ledger_entry
from bridge.dashboard import RfpBoard, build_dashboard_app
from bridge.edge.email_ingress import EmailProcessor
from bridge.email_ingress.mime import landing_key
from bridge.portal.rfp_checklist import checklist_for
from bridge.portal.store import InMemoryPortalStore
from bridge.rfp import load_rfp_policy, open_rfp, receive, to_exchange_turn
from bridge.seams.local import LocalEmailIngress
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import Disposition, RequirementStatus, RfpItemStatus
from contract.wire import CollectRequest, Requirement, RequirementsList
from tests._skill_fixtures import (
    CANDIDATES,
    PROCESS,
    StubExtraction,
    supplier_result,
    write_supplier_onboarding,
)
from tests.email_fixtures import compose

SUPPLIERS = ("supplier-x", "supplier-y")
ITEMS = ("W9", "COI")
RFP_ID = "onboard-2027"
MAILBOX = "onboarding@buyer.example"
SECRET = b"synthetic-onboarding-secret"
BASE_URL = "http://bridge.test/"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=7)

@pytest.fixture(scope="module")
def skills_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("skills")
    write_supplier_onboarding(root, doctypes=False)  # the process skill only
    return root


@pytest.fixture(autouse=True)
def _no_email_env(monkeypatch):
    for name in (
        "BRIDGE_EMAIL_MAILBOX",
        "BRIDGE_EMAIL_TOKEN_SECRET",
        "BRIDGE_EMAIL_DIR",
        "BRIDGE_EMAIL_DRAIN_INTERVAL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def board(skills_dir):
    policy = load_rfp_policy(PROCESS, skills_dir=skills_dir)
    board = RfpBoard(policy=policy, clock=lambda: NOW)
    request = CollectRequest(
        process=policy.process,
        party="acme-buyer",
        context_id="onboard-ctx",
        due_at=DUE,
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item=item,
                    party=supplier,
                    status=RequirementStatus.required,
                    doctype_hint=policy.doctype_hint,
                )
                for supplier in SUPPLIERS
                for item in ITEMS
            ]
        ),
    )
    board.put(open_rfp(request, now=NOW - timedelta(days=1), rfp_id=RFP_ID))
    return board


def test_policy_comes_from_the_synthetic_skill(skills_dir):
    policy = load_rfp_policy(PROCESS, skills_dir=skills_dir)
    assert policy.process == PROCESS
    assert policy.candidate_doctypes == CANDIDATES
    assert policy.item_field == "credential"
    assert policy.doctype_hint is None  # two candidates → no single hint
    assert policy.tick == timedelta(days=1)
    assert policy.max_resubmissions == 3


def test_board_runs_the_skills_process_only(board, skills_dir):
    rfp = board.rfps[RFP_ID]
    assert rfp.process == PROCESS
    assert {(c.party, c.product_line) for c in rfp.cells} == {
        (s, i) for s in SUPPLIERS for i in ITEMS
    }
    foreign = rfp.model_copy(update={"rfp_id": "other", "process": "benefits-rfp"})
    with pytest.raises(ValueError, match="this board runs 'supplier-onboarding'"):
        board.put(foreign)
    assert list(board.rfps) == [RFP_ID]


def test_exchange_turn_proposes_no_single_doctype(board):
    turn = to_exchange_turn(board.rfps[RFP_ID], now=NOW, policy=board.policy)
    outstanding = turn.status.outstanding.requirements
    assert len(outstanding) == len(SUPPLIERS) * len(ITEMS)
    assert all(r.doctype_hint is None for r in outstanding)
    assert all(r.reason_code == "invited" for r in outstanding)
    assert turn.status.outstanding.done is False


def test_receive_files_on_the_skills_item_key(board):
    result = supplier_result("w9-form", "W9")
    verdict = dispose_signals(
        signals_from_result(
            result, candidate_doctypes=list(board.policy.candidate_doctypes)
        )
    )
    entry = project_ledger_entry(doc_id="x-w9-1", result=result, verdict=verdict)
    assert entry.disposition is Disposition.accepted
    rfp = receive(
        board.rfps[RFP_ID], "supplier-x", entry, now=NOW, policy=board.policy
    )
    board.put(rfp)
    cell = board.rfps[RFP_ID].cell("supplier-x", "W9")
    assert cell.status is RfpItemStatus.received and cell.versions == ["x-w9-1"]
    assert board.rfps[RFP_ID].unmatched == []


async def test_email_drain_uses_the_skills_label_space(board, tmp_path):
    seam = LocalEmailIngress(tmp_path)
    extraction = StubExtraction(
        {
            "w9.pdf": supplier_result("w9-form", "W9"),
            # A benefits quote is OUT of this skill's label space → gated, not received.
            "quote.pdf": supplier_result("benefits-quote", "COI"),
        }
    )
    proc = EmailProcessor(
        board=board,
        seam=seam,
        mailbox=MAILBOX,
        secret=SECRET,
        extraction=extraction,
        artifact_store=InMemoryArtifactService(),
        base_url=BASE_URL,
    )
    raw = compose(
        proc.reply_address(RFP_ID, "supplier-y"),
        message_id="<creds-1@supplier-y.example>",
        sender="ap@supplier-y.example",
        subject="Re: supplier onboarding",
        attachments=[
            ("w9.pdf", b"%PDF-1.4 synthetic w9", "application/pdf"),
            ("quote.pdf", b"%PDF-1.4 synthetic quote", "application/pdf"),
        ],
    )
    assert seam.land(landing_key(raw), raw)

    (outcome,) = await proc.drain()

    assert outcome.status == "filed" and (outcome.rfp_id, outcome.party) == (
        RFP_ID,
        "supplier-y",
    )
    assert len(extraction.calls) == 2
    w9, quote = outcome.filed
    assert (w9.disposition, w9.product_line) == ("accepted", "W9")
    assert (quote.reason_code, quote.product_line) == ("unsupported_doctype", "COI")

    rfp = board.rfps[RFP_ID]
    assert rfp.cell("supplier-y", "W9").status is RfpItemStatus.received
    coi = rfp.cell("supplier-y", "COI")
    assert coi.status is RfpItemStatus.incomplete
    assert coi.reason_code == "unsupported_doctype"
    assert "w9-form, insurance-cert" in (coi.message or "")
    # The other supplier's leg is untouched (isolation).
    assert rfp.cell("supplier-x", "W9").status is RfpItemStatus.invited


def test_carrier_checklist_accepts_the_skills_label_space(board):
    ctx_x = board.rfps[RFP_ID].leg_for("supplier-x").context_id
    checklist = checklist_for(board, ctx_x, intake=None)
    assert checklist.party == "supplier-x"
    assert [i.product_line for i in checklist.items] == list(ITEMS)
    (action,) = checklist.actions
    assert action.kind == "upload"
    assert action.accept == list(CANDIDATES)


def test_dashboard_reads_the_second_process(board):
    client = TestClient(build_dashboard_app(store=InMemoryPortalStore(), rfps=board))
    resp = client.get(f"/rfps/{RFP_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["process"] == PROCESS
    assert {c["party"] for c in body["carriers"]} == set(SUPPLIERS)
    assert body["waiting"] == len(SUPPLIERS) * len(ITEMS)
