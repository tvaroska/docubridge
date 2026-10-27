"""RFP board durability through the exchange_store port (S0-arch-14).

An RFP has weeks-scale due dates, so the ``RfpCollection`` (the materialized Exchange
record of an emergent Collect) must survive the process: the board reads and writes it
through the ``exchange_store`` port. These tests prove a board over a file-backed store
survives a close/reopen with an identical aggregate and projection, the fully seeded demo
RFP round-trips through JSON losslessly, a superseded write is refused (compare-and-set),
default boards stay in memory and isolated, and the demo host keeps a portal upload across
a restart wired only through ``BRIDGE_EXCHANGE_DB_URL``.

Synthetic data only (the fictional carriers / broker of ``tests/email_fixtures``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from bridge.dashboard.board import KIND_RFP, RfpBoard
from bridge.demos.rfp_demo import DEMO_RFP_ID, build_rfp_demo_app
from bridge.exchange_store import (
    ExchangeStore,
    InMemoryExchangeStore,
    SqlExchangeStore,
    StaleExchangeWrite,
)
from bridge.ports import Port, PortMode, get_adapter
from bridge.ports.local import EXCHANGE_DB_URL_ENV
from bridge.rfp import (
    RfpCollection,
    acknowledge,
    load_rfp_policy,
    open_rfp,
    receive,
    record_chase,
    update_requirements,
)
from contract.enums import Disposition, Gate, LifecycleStatus, RequirementStatus
from contract.wire import CollectRequest, LedgerEntry, Requirement, RequirementsList
from tests.email_fixtures import DUE, NOW, RFP_ID

PROCESS = "benefits-rfp"  # the process skill under test (named here, never in bridge/)
POLICY = load_rfp_policy(PROCESS)
SECOND_RFP = "acme-dental-2027"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Only what each test sets reaches the ports and the demo host."""
    for env in (
        EXCHANGE_DB_URL_ENV,
        "BRIDGE_PORT_MODE",
        "BRIDGE_PORT_EXCHANGE_STORE",
        "BRIDGE_EMAIL_MAILBOX",
        "BRIDGE_EMAIL_TOKEN_SECRET",
        "BRIDGE_EMAIL_DIR",
    ):
        monkeypatch.delenv(env, raising=False)


@pytest.fixture
def stores(tmp_path):
    """A factory of independent file-sqlite store handles over ONE db; closed after."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/rfp.db"
    handles: list[ExchangeStore] = []

    def make() -> ExchangeStore:
        store = get_adapter(Port.exchange_store, PortMode.local).build_exchange_store(
            db_url=dsn
        )
        assert isinstance(store, SqlExchangeStore)
        handles.append(store)
        return store

    yield make
    for store in handles:
        store.close()


def _request(carriers: dict[str, tuple[str, ...]], ctx: str) -> CollectRequest:
    return CollectRequest(
        process=PROCESS,
        party="acme-broker",
        context_id=ctx,
        due_at=DUE,
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item=line,
                    party=party,
                    status=RequirementStatus.required,
                    doctype_hint="benefits-quote",
                )
                for party, lines in carriers.items()
                for line in lines
            ]
        ),
    )


def _accepted(doc_id: str, line: str) -> LedgerEntry:
    return LedgerEntry(
        doc_id=doc_id,
        doctype="benefits-quote",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields={"product_line": line},
    )


def _board(store: ExchangeStore | None = None) -> RfpBoard:
    return RfpBoard(policy=POLICY, clock=lambda: NOW, store=store)


def _populate(board: RfpBoard) -> None:
    """Two RFPs (same broker context — the context token is not the key) + activity."""
    lines = {
        "carrier-a": ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY"),
        "carrier-b": ("CRITICAL_ILLNESS",),
    }
    board.put(
        open_rfp(_request(lines, "rfp-ctx"), now=NOW - timedelta(days=1), rfp_id=RFP_ID)
    )
    board.put(
        open_rfp(
            _request({"carrier-c": ("DENTAL",)}, "rfp-ctx"),
            now=NOW - timedelta(days=1),
            rfp_id=SECOND_RFP,
        )
    )
    # Each put follows a fresh read (the write sites' rule).
    board.put(acknowledge(board.rfps[RFP_ID], "carrier-a", now=NOW))
    board.put(
        receive(
            board.rfps[RFP_ID],
            "carrier-a",
            _accepted("a-ci-1", "CRITICAL_ILLNESS"),
            now=NOW,
            policy=POLICY,
        )
    )
    board.put(
        record_chase(
            board.rfps[RFP_ID],
            "carrier-b",
            now=NOW,
            step=LifecycleStatus.reminder,
            channel="email",
        )
    )
    board.put(
        update_requirements(
            board.rfps[RFP_ID],
            RequirementsList(
                requirements=[
                    Requirement(
                        item="HOSPITAL_INDEMNITY",
                        party="carrier-a",
                        status=RequirementStatus.waived,
                    )
                ]
            ),
            now=NOW,
        )
    )


# --------------------------------------------------------------------------- #
# 1. Restart survival                                                         #
# --------------------------------------------------------------------------- #
def test_rfp_board_survives_store_close_and_reopen(stores):
    store_a = stores()
    board_a = _board(store_a)
    _populate(board_a)
    snapshot = board_a.rfps[RFP_ID]
    view_a = board_a.get(RFP_ID)
    ctx_b = snapshot.leg_for("carrier-b").context_id
    assert snapshot.cell("carrier-a", "CRITICAL_ILLNESS").versions == ["a-ci-1"]
    assert snapshot.leg_for("carrier-b").chases == 1
    assert (
        snapshot.cell("carrier-a", "HOSPITAL_INDEMNITY").requirement
        is RequirementStatus.waived
    )
    store_a.close()  # the process dies

    board_b = _board(stores())
    restored = board_b.rfps[RFP_ID]
    assert restored == snapshot
    assert board_b.get(RFP_ID) == view_a
    rfp, leg = board_b.find_leg(ctx_b)
    assert (rfp.rfp_id, leg.party) == (RFP_ID, "carrier-b")
    assert [r.rfp_id for r, _ in board_b.items()] == [RFP_ID, SECOND_RFP]
    assert list(board_b.rfps) == [RFP_ID, SECOND_RFP]
    assert [e.kind for e in restored.audit] == [e.kind for e in snapshot.audit]
    assert len(restored.audit) == len(snapshot.audit)
    # The record carries the broker-side A2A context token (never a minted id).
    rec = board_b.store.get(KIND_RFP, RFP_ID)
    assert rec is not None and rec.context_id == "rfp-ctx" and rec.version == 5

    # The reopened board keeps writing (fresh read → put) over the restored record.
    board_b.put(acknowledge(board_b.rfps[RFP_ID], "carrier-b", now=NOW))
    assert board_b.store.get(KIND_RFP, RFP_ID).version == 6


# --------------------------------------------------------------------------- #
# 2. Lossless JSON roundtrip of a fully populated RFP                         #
# --------------------------------------------------------------------------- #
def test_seeded_demo_rfp_roundtrips_json_losslessly():
    app = build_rfp_demo_app(now=NOW, exchange_store=InMemoryExchangeStore())
    rfp = app.state.rfps.rfps[DEMO_RFP_ID]
    # The seed is the richest aggregate we have: field reviews, an incomplete cell,
    # a received cell, an acknowledged carrier, a full audit trail.
    assert any(e.field_reviews for e in rfp.entries)
    statuses = {(c.party, c.product_line): c.status.value for c in rfp.cells}
    assert statuses[("carrier-a", "CRITICAL_ILLNESS")] == "received"
    assert statuses[("carrier-a", "HOSPITAL_INDEMNITY")] == "incomplete"
    assert len(rfp.audit) > 3
    assert RfpCollection.model_validate(rfp.model_dump(mode="json")) == rfp


# --------------------------------------------------------------------------- #
# 3. A superseded write is refused                                            #
# --------------------------------------------------------------------------- #
def test_stale_write_from_a_second_board_is_refused(stores):
    board_a, board_b = _board(stores()), _board(stores())
    board_a.put(
        open_rfp(
            _request({"carrier-a": ("CRITICAL_ILLNESS",)}, "rfp-ctx"),
            now=NOW - timedelta(days=1),
            rfp_id=RFP_ID,
        )
    )
    stale = board_a.rfps[RFP_ID]

    board_b.put(acknowledge(board_b.rfps[RFP_ID], "carrier-a", now=NOW))

    with pytest.raises(StaleExchangeWrite):
        board_a.put(
            receive(
                stale,
                "carrier-a",
                _accepted("a-ci-1", "CRITICAL_ILLNESS"),
                now=NOW,
                policy=POLICY,
            )
        )
    # B's write survived the refused put.
    fresh = board_a.rfps[RFP_ID]
    assert fresh.cell("carrier-a", "CRITICAL_ILLNESS").status.value == "acknowledged"
    assert fresh.entries == []
    # After a fresh read A's write lands on top of B's.
    board_a.put(
        receive(
            fresh,
            "carrier-a",
            _accepted("a-ci-1", "CRITICAL_ILLNESS"),
            now=NOW,
            policy=POLICY,
        )
    )
    final = board_b.rfps[RFP_ID]
    assert final.cell("carrier-a", "CRITICAL_ILLNESS").status.value == "received"
    # A's trail extends B's (B's acknowledgement is not lost under A's receive).
    assert final.audit[: len(fresh.audit)] == fresh.audit
    assert len(final.audit) > len(fresh.audit)


def test_creating_an_existing_rfp_from_another_board_is_refused(stores):
    board_a, board_b = _board(stores()), _board(stores())
    request = _request({"carrier-a": ("CRITICAL_ILLNESS",)}, "rfp-ctx")
    board_a.put(open_rfp(request, now=NOW, rfp_id=RFP_ID))
    with pytest.raises(StaleExchangeWrite, match="already exists"):
        board_b.put(open_rfp(request, now=NOW, rfp_id=RFP_ID))


# --------------------------------------------------------------------------- #
# 4. Default boards are in-memory and isolated                                #
# --------------------------------------------------------------------------- #
def test_default_boards_are_in_memory_and_isolated():
    one, two = RfpBoard(policy=POLICY), RfpBoard(policy=POLICY)
    assert isinstance(one.store, InMemoryExchangeStore)
    assert one.store is not two.store
    one.put(
        open_rfp(
            _request({"carrier-a": ("CRITICAL_ILLNESS",)}, "rfp-ctx"),
            now=NOW,
            rfp_id=RFP_ID,
        )
    )
    assert RFP_ID in one.rfps and len(one.rfps) == 1
    assert RFP_ID not in two.rfps and len(two.rfps) == 0
    with pytest.raises(KeyError):
        two.get(RFP_ID)


def test_rfps_view_is_read_only():
    board = _board()
    with pytest.raises(TypeError):
        board.rfps["x"] = None  # type: ignore[index]


# --------------------------------------------------------------------------- #
# 5. The demo host keeps a portal upload across a restart (env-wired)          #
# --------------------------------------------------------------------------- #
def test_demo_host_restart_keeps_portal_upload(monkeypatch, tmp_path):
    monkeypatch.setenv(EXCHANGE_DB_URL_ENV, f"sqlite+aiosqlite:///{tmp_path}/rfp.db")

    app1 = build_rfp_demo_app(now=NOW)
    assert isinstance(app1.state.rfps.store, SqlExchangeStore)
    try:
        with TestClient(app1) as client:
            before = {
                (c["party"], c["product_line"]): c["status"]
                for c in client.get(f"/ops/rfps/{DEMO_RFP_ID}").json()["cells"]
            }
            ctx_c = app1.state.rfps.rfps[DEMO_RFP_ID].leg_for("carrier-c").context_id
            up = client.post(
                f"/portal/rfp/legs/{ctx_c}/uploads",
                json={
                    "doc_id": "c-ci-1",
                    "artifact_name": "benefits-conformance-ci-usd",
                    "product_line": "CRITICAL_ILLNESS",
                },
            )
            assert up.status_code == 200
        final_audit = len(app1.state.rfps.rfps[DEMO_RFP_ID].audit)
    finally:
        app1.state.rfps.store.close()  # the host process dies

    app2 = build_rfp_demo_app(now=NOW)  # same env, a fresh store handle
    try:
        with TestClient(app2) as client:
            cells = {
                (c["party"], c["product_line"]): c["status"]
                for c in client.get(f"/ops/rfps/{DEMO_RFP_ID}").json()["cells"]
            }
        assert cells[("carrier-c", "CRITICAL_ILLNESS")] == "received"
        assert cells[("carrier-c", "HOSPITAL_INDEMNITY")] == "acknowledged"
        for line in ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY"):
            assert cells[("carrier-a", line)] == before[("carrier-a", line)]
        # The seed did NOT re-run: the restored trail is exactly app1's final one.
        assert len(app2.state.rfps.rfps[DEMO_RFP_ID].audit) == final_audit
        assert list(app2.state.rfps.rfps) == [DEMO_RFP_ID]
    finally:
        app2.state.rfps.store.close()
