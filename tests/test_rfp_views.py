"""RFP views + consumer read API (S0-frontend-1).

Drives the consumer status API (``GET /rfps``, ``GET /rfps/{id}``), the manual "chase now"
(``POST /rfps/{id}/chase`` → one ``Nudge`` per carrier over the outbound client, audit-only
on the board), the carrier-facing checklist addressed by the leg ``context_id`` (filtered
to that carrier only) with its upload-into-a-row, and the local demo host that serves both
off one shared board. Synthetic carriers; quotes from the benefits conformance corpus.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bridge.dashboard import RFP_STATUS_API_VERSION, build_dashboard_app
from bridge.demos.rfp_demo import DEMO_RFP_ID, build_rfp_demo_app
from bridge.email_ingress.correlation import (
    IntakeAddressing,
    intake_address,
    mint_token,
    subject_tag,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.outbound import FakeTransport, Nudge, OutboundClient
from bridge.portal.server import build_portal_app
from bridge.portal.store import InMemoryPortalStore
from bridge.rfp import (
    KIND_CHASED,
    RfpStatusView,
    acknowledge,
    chase_message,
    chaseable_items,
    decline,
    receive,
    record_chase,
    update_requirements,
)
from contract.enums import (
    Disposition,
    Gate,
    LifecycleStatus,
    RequirementStatus,
)
from contract.wire import LedgerEntry, Requirement, RequirementsList
from tests.email_fixtures import (
    CONFORMANCE,
    DUE,
    MAILBOX,
    NOW,
    RFP_ID,
    SECRET,
    make_board,
)

INTAKE = IntakeAddressing(MAILBOX, SECRET)
HI_REVIEW_PATH = "fields.tables.rates[1].frequency"


@pytest.fixture(autouse=True)
def _no_email_env(monkeypatch):
    """Reply addressing comes only from what each test injects."""
    monkeypatch.delenv("BRIDGE_EMAIL_MAILBOX", raising=False)
    monkeypatch.delenv("BRIDGE_EMAIL_TOKEN_SECRET", raising=False)


def _accepted(doc_id: str, line: str) -> LedgerEntry:
    return LedgerEntry(
        doc_id=doc_id,
        doctype="benefits-quote",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields={"product_line": line},
    )


def _reply(party: str, rfp_id: str = RFP_ID) -> str:
    return intake_address(MAILBOX, mint_token(SECRET, rfp_id, party))


def _ctx(board, party: str, rfp_id: str = RFP_ID) -> str:
    return board.rfps[rfp_id].leg_for(party).context_id


def _dashboard(board, *, intake=INTAKE, outbound=None):
    outbound = outbound or OutboundClient(FakeTransport())
    app = build_dashboard_app(
        store=InMemoryPortalStore(), rfps=board, intake=intake, outbound=outbound
    )
    return TestClient(app)


def _portal(board, *, intake=INTAKE):
    app = build_portal_app(
        rfps=board, intake=intake, extraction=FixtureExtractor(corpus_path=CONFORMANCE)
    )
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Pure: chase state on the aggregate                                          #
# --------------------------------------------------------------------------- #
class TestChaseAggregate:
    def test_record_chase_is_audit_only_and_pure(self):
        rfp = make_board().rfps[RFP_ID]
        before = rfp.model_dump()
        at = NOW + timedelta(hours=1)
        chased = record_chase(
            rfp, "carrier-b", now=at, step=LifecycleStatus.on_track, channel="email"
        )
        assert rfp.model_dump() == before  # the input is unchanged
        leg = chased.leg_for("carrier-b")
        assert (leg.chases, leg.last_chased_at) == (1, at)
        assert chased.leg_for("carrier-a").chases == 0
        assert chased.cells == rfp.cells and chased.entries == rfp.entries
        (event,) = chased.audit[len(rfp.audit) :]
        assert (event.kind, event.party) == (KIND_CHASED, "carrier-b")
        assert event.detail == "on_track via email: CRITICAL_ILLNESS"
        again = record_chase(
            chased, "carrier-b", now=at, step=LifecycleStatus.overdue, channel="email"
        )
        assert again.leg_for("carrier-b").chases == 2

    def test_record_chase_refuses_naive_now_and_unknown_party(self):
        rfp = make_board().rfps[RFP_ID]
        with pytest.raises(ValueError, match="timezone-aware"):
            record_chase(
                rfp,
                "carrier-a",
                now=datetime(2026, 10, 2),
                step=LifecycleStatus.on_track,
                channel="email",
            )
        with pytest.raises(KeyError):
            record_chase(
                rfp,
                "carrier-z",
                now=NOW,
                step=LifecycleStatus.on_track,
                channel="email",
            )
        with pytest.raises(KeyError):
            chaseable_items(rfp, "carrier-z")

    def test_chaseable_excludes_received_declined_and_waived(self):
        board = make_board()
        policy = board.policy
        rfp = board.rfps[RFP_ID]
        rfp = receive(
            rfp,
            "carrier-a",
            _accepted("q1", "CRITICAL_ILLNESS"),
            now=NOW,
            policy=policy,
        )
        assert [c.product_line for c in chaseable_items(rfp, "carrier-a")] == [
            "HOSPITAL_INDEMNITY"
        ]
        declined = decline(rfp, "carrier-a", now=NOW, product_line="HOSPITAL_INDEMNITY")
        assert chaseable_items(declined, "carrier-a") == []
        waived = update_requirements(
            rfp,
            RequirementsList(
                requirements=[
                    Requirement(
                        item="CRITICAL_ILLNESS",
                        party="carrier-b",
                        status=RequirementStatus.waived,
                    )
                ]
            ),
            now=NOW,
        )
        assert chaseable_items(waived, "carrier-b") == []

    def test_update_requirements_preserves_chase_state(self):
        rfp = make_board().rfps[RFP_ID]
        rfp = record_chase(
            rfp, "carrier-a", now=NOW, step=LifecycleStatus.on_track, channel="email"
        )
        updated = update_requirements(
            rfp,
            RequirementsList(
                requirements=[
                    Requirement(
                        item="CRITICAL_ILLNESS",
                        party="carrier-c",
                        status=RequirementStatus.required,
                    )
                ]
            ),
            now=NOW,
        )
        assert updated.leg_for("carrier-a").chases == 1
        assert updated.leg_for("carrier-a").last_chased_at == NOW
        assert (
            updated.leg_for("carrier-a").context_id
            == rfp.leg_for("carrier-a").context_id
        )
        assert updated.leg_for("carrier-c").chases == 0  # a new carrier, a fresh leg

    def test_chase_message_names_only_the_carrier_and_its_lines(self):
        rfp = make_board().rfps[RFP_ID]
        text = chase_message(
            rfp,
            "carrier-b",
            lines=["CRITICAL_ILLNESS"],
            reply_address=_reply("carrier-b"),
            reply_subject_tag="[ref:abc]",
        )
        assert "carrier-b" in text and "CRITICAL_ILLNESS" in text
        assert "carrier-a" not in text and "HOSPITAL_INDEMNITY" not in text
        assert _reply("carrier-b") in text and "[ref:abc]" in text
        assert DUE.isoformat() in text
        bare = chase_message(rfp, "carrier-b", lines=["CRITICAL_ILLNESS"])
        assert "Reply to" not in bare


# --------------------------------------------------------------------------- #
# Nudge idempotency + intake addressing                                       #
# --------------------------------------------------------------------------- #
class TestNudgeAndAddressing:
    def test_nudge_key_unchanged_without_seq_and_seq_keyed_with_it(self):
        base = dict(context_id="ctx", party="p", channel="email", message="m")
        assert Nudge(step=LifecycleStatus.overdue, **base).idempotency_key == (
            "nudge:ctx:overdue"
        )
        assert Nudge(step=LifecycleStatus.overdue, seq=0, **base).idempotency_key == (
            "nudge:ctx:chase-0"
        )
        # step-independent: a re-drive after the ladder stepped still dedupes
        assert Nudge(step=LifecycleStatus.reminder, seq=0, **base).idempotency_key == (
            "nudge:ctx:chase-0"
        )

    def test_intake_addressing_matches_the_correlation_primitives(self):
        token = mint_token(SECRET, RFP_ID, "carrier-a")
        assert INTAKE.token(RFP_ID, "carrier-a") == token
        assert INTAKE.reply_address(RFP_ID, "carrier-a") == intake_address(
            MAILBOX, token
        )
        assert INTAKE.reply_subject_tag(RFP_ID, "carrier-a") == subject_tag(token)
        with pytest.raises(ValueError):
            IntakeAddressing("not-a-mailbox", SECRET)
        with pytest.raises(ValueError):
            IntakeAddressing(MAILBOX, b"")

    def test_from_env(self, monkeypatch):
        assert IntakeAddressing.from_env() is None
        monkeypatch.setenv("BRIDGE_EMAIL_MAILBOX", "  ")
        assert IntakeAddressing.from_env() is None
        monkeypatch.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
        with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_TOKEN_SECRET"):
            IntakeAddressing.from_env()
        monkeypatch.setenv("BRIDGE_EMAIL_TOKEN_SECRET", SECRET.decode())
        assert IntakeAddressing.from_env() == INTAKE


# --------------------------------------------------------------------------- #
# Consumer read API                                                           #
# --------------------------------------------------------------------------- #
class TestConsumerReadApi:
    def test_list_and_get_carry_the_matrix(self):
        board = make_board()
        client = _dashboard(board)
        listed = client.get("/rfps").json()
        assert [r["rfp_id"] for r in listed] == [RFP_ID]
        body = client.get(f"/rfps/{RFP_ID}").json()
        assert body == listed[0]
        assert body["api_version"] == RFP_STATUS_API_VERSION == "1"
        assert body["as_of"] == NOW.isoformat().replace("+00:00", "Z")
        assert body["due_at"] == DUE.isoformat().replace("+00:00", "Z")
        for cell in body["cells"]:
            assert {
                "status",
                "last_activity",
                "versions",
                "reason_code",
                "message",
                "field_reviews",
            } <= set(cell)
        for carrier in body["carriers"]:
            assert carrier["reply_address"] == _reply(carrier["party"])
            assert carrier["reply_subject_tag"] == INTAKE.reply_subject_tag(
                RFP_ID, carrier["party"]
            )
            assert (carrier["chases"], carrier["last_chased_at"]) == (0, None)

    def test_shape_is_the_status_view_plus_api_version_and_reply_keys(self):
        body = _dashboard(make_board()).get(f"/rfps/{RFP_ID}").json()
        body.pop("api_version")
        for carrier in body["carriers"]:
            carrier.pop("reply_address")
            carrier.pop("reply_subject_tag")
        RfpStatusView.model_validate(body)  # extra="forbid": nothing else invented

    def test_unknown_and_no_board(self):
        client = _dashboard(make_board())
        assert client.get("/rfps/nope").status_code == 404
        bare = TestClient(build_dashboard_app(store=InMemoryPortalStore()))
        assert bare.get("/rfps").json() == []
        assert bare.get("/rfps/x").status_code == 404
        assert bare.post("/rfps/x/chase", json={}).status_code == 404

    def test_reply_fields_null_without_intake(self):
        body = _dashboard(make_board(), intake=None).get(f"/rfps/{RFP_ID}").json()
        assert all(
            c["reply_address"] is None and c["reply_subject_tag"] is None
            for c in body["carriers"]
        )

    def test_intake_from_env_when_not_injected(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
        monkeypatch.setenv("BRIDGE_EMAIL_TOKEN_SECRET", SECRET.decode())
        body = _dashboard(make_board(), intake=None).get(f"/rfps/{RFP_ID}").json()
        assert body["carriers"][0]["reply_address"] == _reply("carrier-a")


# --------------------------------------------------------------------------- #
# Manual "chase now"                                                          #
# --------------------------------------------------------------------------- #
class TestChase:
    def test_chase_one_carrier_then_again(self):
        board = make_board()
        transport = FakeTransport()
        outbound = OutboundClient(transport)
        client = _dashboard(board, outbound=outbound)
        cells_before = [c.model_dump() for c in board.rfps[RFP_ID].cells]

        resp = client.post(f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        (nudge,) = transport.outbox
        assert isinstance(nudge, Nudge)
        assert nudge.context_id == _ctx(board, "carrier-b")
        assert (nudge.party, nudge.seq, nudge.channel) == ("carrier-b", 0, "email")
        assert _reply("carrier-b") in nudge.message
        assert "carrier-a" not in nudge.message
        assert body["chased"] == [
            {
                "party": "carrier-b",
                "step": "on_track",
                "idempotency_key": f"nudge:{nudge.context_id}:chase-0",
                "resent": False,
                "reply_address": _reply("carrier-b"),
            }
        ]
        carriers = {c["party"]: c for c in body["rfp"]["carriers"]}
        assert carriers["carrier-b"]["chases"] == 1
        assert carriers["carrier-b"]["last_chased_at"] is not None
        assert carriers["carrier-a"]["chases"] == 0
        # audit-only: no cell (status, last_activity, ...) moved
        assert [c.model_dump() for c in board.rfps[RFP_ID].cells] == cells_before

        second = client.post(
            f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"}
        ).json()
        assert second["chased"][0]["idempotency_key"].endswith(":chase-1")
        assert second["chased"][0]["resent"] is False
        assert len(transport.outbox) == 2
        # re-driving the first send (a crash replay) dedupes: no new outbox entry
        assert outbound.send(nudge).resent is True
        assert len(transport.outbox) == 2

        # the dashboard row carries the chase count too
        row = client.get(f"/exchanges/{RFP_ID}").json()
        carriers = {c["party"]: c for c in row["rfp"]["carriers"]}
        assert carriers["carrier-b"]["chases"] == 2

    def test_chase_step_is_the_carriers_ladder_step(self):
        board = make_board()
        board.clock = lambda: DUE + timedelta(hours=1)
        transport = FakeTransport()
        client = _dashboard(board, outbound=OutboundClient(transport))
        body = client.post(f"/rfps/{RFP_ID}/chase", json={"party": "carrier-a"}).json()
        assert body["chased"][0]["step"] == "overdue"
        assert transport.outbox[0].step is LifecycleStatus.overdue

    def test_nothing_to_chase_is_409_unknowns_are_404(self):
        board = make_board()
        rfp = board.rfps[RFP_ID]
        board.put(
            receive(
                rfp,
                "carrier-b",
                _accepted("qb", "CRITICAL_ILLNESS"),
                now=NOW,
                policy=board.policy,
            )
        )
        transport = FakeTransport()
        client = _dashboard(board, outbound=OutboundClient(transport))
        assert (
            client.post(
                f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/rfps/{RFP_ID}/chase", json={"party": "carrier-z"}
            ).status_code
            == 404
        )
        assert client.post("/rfps/nope/chase", json={}).status_code == 404
        assert (
            client.post(
                f"/rfps/{RFP_ID}/chase", json={"party": "x", "extra": 1}
            ).status_code
            == 422
        )
        assert transport.outbox == []

    def test_chase_all_targets_only_chaseable_carriers(self):
        board = make_board()
        rfp = board.rfps[RFP_ID]
        board.put(
            receive(
                rfp,
                "carrier-b",
                _accepted("qb", "CRITICAL_ILLNESS"),
                now=NOW,
                policy=board.policy,
            )
        )
        transport = FakeTransport()
        client = _dashboard(board, outbound=OutboundClient(transport))
        body = client.post(f"/rfps/{RFP_ID}/chase", json={}).json()
        assert [c["party"] for c in body["chased"]] == ["carrier-a"]
        assert [n.party for n in transport.outbox] == ["carrier-a"]
        assert "CRITICAL_ILLNESS" in transport.outbox[0].message
        # no body at all behaves the same (a second chase-all → chase-1)
        again = client.post(f"/rfps/{RFP_ID}/chase").json()
        assert again["chased"][0]["idempotency_key"].endswith(":chase-1")

    def test_nothing_chaseable_anywhere_is_409(self):
        board = make_board()
        rfp = board.rfps[RFP_ID]
        for party, line in (
            ("carrier-a", "CRITICAL_ILLNESS"),
            ("carrier-a", "HOSPITAL_INDEMNITY"),
            ("carrier-b", "CRITICAL_ILLNESS"),
        ):
            rfp = receive(
                rfp,
                party,
                _accepted(f"q-{party}-{line}", line),
                now=NOW,
                policy=board.policy,
            )
        board.put(rfp)
        assert (
            _dashboard(board).post(f"/rfps/{RFP_ID}/chase", json={}).status_code == 409
        )

    def test_concurrently_recorded_seq_is_not_counted_twice(self):
        """A send that deduped because another chase already recorded its seq."""
        board = make_board()
        transport = FakeTransport()
        outbound = OutboundClient(transport)
        client = _dashboard(board, outbound=outbound)
        client.post(f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"})
        # roll the board back to before the record, as a crash after send would leave it
        rfp = board.rfps[RFP_ID]
        leg = rfp.leg_for("carrier-b")
        board.put(
            rfp.model_copy(
                update={
                    "legs": [
                        lg.model_copy(update={"chases": 0}) if lg is leg else lg
                        for lg in rfp.legs
                    ]
                }
            )
        )
        body = client.post(f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"}).json()
        assert body["chased"][0]["resent"] is True  # the re-drive deduped
        assert len(transport.outbox) == 1
        carriers = {c["party"]: c for c in body["rfp"]["carriers"]}
        assert carriers["carrier-b"]["chases"] == 1  # recorded once

    def test_misconfigured_outbound_adapter_is_503(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_PORT_A2A_CLIENT", "gcp")
        monkeypatch.delenv("BRIDGE_GATEWAY_HOST", raising=False)
        app = build_dashboard_app(store=InMemoryPortalStore(), rfps=make_board())
        resp = TestClient(app).post(f"/rfps/{RFP_ID}/chase", json={})
        assert resp.status_code == 503
        assert "BRIDGE_GATEWAY_HOST" in resp.json()["detail"]

    def test_lazy_local_adapter_is_built_once(self, monkeypatch):
        monkeypatch.delenv("BRIDGE_PORT_A2A_CLIENT", raising=False)
        monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)
        client = TestClient(
            build_dashboard_app(store=InMemoryPortalStore(), rfps=make_board())
        )
        first = client.post(f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"}).json()
        second = client.post(
            f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"}
        ).json()
        assert first["chased"][0]["resent"] is False
        assert second["chased"][0]["idempotency_key"].endswith(":chase-1")


# --------------------------------------------------------------------------- #
# Carrier-facing checklist (portal)                                           #
# --------------------------------------------------------------------------- #
class TestCarrierChecklist:
    def test_checklist_is_this_carrier_only(self):
        board = make_board()
        body = _portal(board).get(f"/rfp/legs/{_ctx(board, 'carrier-b')}").json()
        assert body["kind"] == "rfp_checklist"
        assert (body["rfp_id"], body["party"]) == (RFP_ID, "carrier-b")
        assert body["context_id"] == _ctx(board, "carrier-b")
        assert [i["product_line"] for i in body["items"]] == ["CRITICAL_ILLNESS"]
        assert body["items"][0]["status"] == "invited"
        assert body["items"][0]["outstanding"] is True
        assert body["reply_address"] == _reply("carrier-b")
        assert body["reply_subject_tag"] == INTAKE.reply_subject_tag(
            RFP_ID, "carrier-b"
        )
        assert body["actions"] == [
            {"kind": "upload", "accept": ["benefits-quote"], "prompt": None}
        ]
        dumped = json.dumps(body)
        assert "carrier-a" not in dumped
        assert _ctx(board, "carrier-a") not in dumped
        assert "HOSPITAL_INDEMNITY" not in dumped
        assert not any(k in body for k in ("counts", "unmatched", "carriers", "cells"))

    def test_unknown_leg_and_no_board_are_404(self):
        board = make_board()
        assert _portal(board).get("/rfp/legs/deadbeef").status_code == 404
        bare = TestClient(build_portal_app())
        assert bare.get("/rfp/legs/x").status_code == 404
        assert (
            bare.post(
                "/rfp/legs/x/uploads",
                json={"doc_id": "d", "artifact_name": "a", "product_line": "p"},
            ).status_code
            == 404
        )

    def test_upload_into_rows_files_through_the_real_gate(self):
        board = make_board()
        portal = _portal(board)
        ctx_a = _ctx(board, "carrier-a")
        hi = portal.post(
            f"/rfp/legs/{ctx_a}/uploads",
            json={
                "doc_id": "a-hi-1",
                "artifact_name": "benefits-conformance-hi-usd-flagged",
                "product_line": "HOSPITAL_INDEMNITY",
            },
        )
        assert hi.status_code == 200, hi.text
        items = {i["product_line"]: i for i in hi.json()["items"]}
        assert items["HOSPITAL_INDEMNITY"]["status"] == "incomplete"
        assert items["HOSPITAL_INDEMNITY"]["reason_code"] == "flagged_fields"
        assert items["HOSPITAL_INDEMNITY"]["field_reviews"][0]["path"] == HI_REVIEW_PATH
        # the first response acknowledged the carrier's other invited line
        assert items["CRITICAL_ILLNESS"]["status"] == "acknowledged"

        ci = portal.post(
            f"/rfp/legs/{ctx_a}/uploads",
            json={
                "doc_id": "a-ci-1",
                "artifact_name": "benefits-conformance-ci-usd",
                "product_line": "CRITICAL_ILLNESS",
            },
        ).json()
        items = {i["product_line"]: i for i in ci["items"]}
        assert items["CRITICAL_ILLNESS"]["status"] == "received"
        assert items["CRITICAL_ILLNESS"]["version_count"] == 1
        assert items["CRITICAL_ILLNESS"]["outstanding"] is False

        # a dashboard on the SAME board sees both
        cells = {
            (c["party"], c["product_line"]): c
            for c in _dashboard(board).get(f"/rfps/{RFP_ID}").json()["cells"]
        }
        assert cells[("carrier-a", "CRITICAL_ILLNESS")]["status"] == "received"
        hi_cell = cells[("carrier-a", "HOSPITAL_INDEMNITY")]
        assert hi_cell["status"] == "incomplete"
        assert hi_cell["field_reviews"][0]["path"] == HI_REVIEW_PATH

    def test_upload_refusals_leave_the_board_untouched(self):
        board = make_board()
        portal = _portal(board)
        ctx_a = _ctx(board, "carrier-a")
        before = board.rfps[RFP_ID].model_dump()

        def post(**over):
            payload = {
                "doc_id": "d1",
                "artifact_name": "benefits-conformance-ci-usd",
                "product_line": "CRITICAL_ILLNESS",
                **over,
            }
            return portal.post(f"/rfp/legs/{ctx_a}/uploads", json=payload)

        assert post(product_line="LIFE").status_code == 404
        assert post(artifact_name="no-such-artifact").status_code == 422
        assert post(extra="x").status_code == 422
        assert board.rfps[RFP_ID].model_dump() == before

        board.put(
            update_requirements(
                board.rfps[RFP_ID],
                RequirementsList(
                    requirements=[
                        Requirement(
                            item="CRITICAL_ILLNESS",
                            party="carrier-a",
                            status=RequirementStatus.waived,
                        )
                    ]
                ),
                now=NOW,
            )
        )
        waived = board.rfps[RFP_ID].model_dump()
        assert post().status_code == 409
        assert board.rfps[RFP_ID].model_dump() == waived

    def test_duplicate_doc_id_is_409(self):
        board = make_board()
        portal = _portal(board)
        ctx_b = _ctx(board, "carrier-b")
        payload = {
            "doc_id": "b-1",
            "artifact_name": "benefits-conformance-ci-usd",
            "product_line": "CRITICAL_ILLNESS",
        }
        assert (
            portal.post(f"/rfp/legs/{ctx_b}/uploads", json=payload).status_code == 200
        )
        after = board.rfps[RFP_ID].model_dump()
        assert (
            portal.post(f"/rfp/legs/{ctx_b}/uploads", json=payload).status_code == 409
        )
        assert board.rfps[RFP_ID].model_dump() == after

    def test_acknowledged_carrier_is_not_reacknowledged(self):
        board = make_board()
        board.put(acknowledge(board.rfps[RFP_ID], "carrier-b", now=NOW))
        audit_len = len(board.rfps[RFP_ID].audit)
        _portal(board).post(
            f"/rfp/legs/{_ctx(board, 'carrier-b')}/uploads",
            json={
                "doc_id": "b-1",
                "artifact_name": "benefits-conformance-ci-usd",
                "product_line": "CRITICAL_ILLNESS",
            },
        )
        kinds = [e.kind for e in board.rfps[RFP_ID].audit[audit_len:]]
        assert kinds == ["quote_received"]


# --------------------------------------------------------------------------- #
# Local demo host: one board behind both views                                #
# --------------------------------------------------------------------------- #
class TestDemoHost:
    def test_seeded_matrix_checklist_and_shared_board(self):
        app = build_rfp_demo_app(now=NOW)
        client = TestClient(app)
        (rfp,) = client.get("/ops/rfps").json()
        assert rfp["rfp_id"] == DEMO_RFP_ID == app.state.rfp_id
        cells = {(c["party"], c["product_line"]): c for c in rfp["cells"]}
        assert len(cells) == 6
        assert cells[("carrier-a", "CRITICAL_ILLNESS")]["status"] == "received"
        hi = cells[("carrier-a", "HOSPITAL_INDEMNITY")]
        assert hi["status"] == "incomplete"
        assert hi["field_reviews"][0]["path"] == HI_REVIEW_PATH
        assert {
            cells[("carrier-b", ln)]["status"]
            for ln in ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY")
        } == {"acknowledged"}
        assert {
            cells[("carrier-c", ln)]["status"]
            for ln in ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY")
        } == {"invited"}

        board = app.state.rfps
        ctx_c = board.rfps[DEMO_RFP_ID].leg_for("carrier-c").context_id
        checklist = client.get(f"/portal/rfp/legs/{ctx_c}").json()
        assert checklist["party"] == "carrier-c" and len(checklist["items"]) == 2

        up = client.post(
            f"/portal/rfp/legs/{ctx_c}/uploads",
            json={
                "doc_id": "c-ci-1",
                "artifact_name": "benefits-conformance-ci-usd",
                "product_line": "CRITICAL_ILLNESS",
            },
        )
        assert up.status_code == 200
        cells = {
            (c["party"], c["product_line"]): c["status"]
            for c in client.get(f"/ops/rfps/{DEMO_RFP_ID}").json()["cells"]
        }
        assert cells[("carrier-c", "CRITICAL_ILLNESS")] == "received"
        assert cells[("carrier-c", "HOSPITAL_INDEMNITY")] == "acknowledged"

    def test_naive_now_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            build_rfp_demo_app(now=datetime(2026, 10, 2))
