"""RFP collection process (S0-arch-11) — "which quotes are ready, which are waiting on a carrier".

Drives ``bridge.rfp`` over a synthetic 3-carrier x 2-product-line RFP: one ``CollectRequest``
opens isolated per-carrier legs; quotes arriving through the real extract → signals →
disposition → ledger path (benefits corpus) are filed per item; the due date drives the SLA
ladder; the answer reads identically off ``rfp_status``, the A2A ``ExchangeTurn`` and the
Ops Dashboard ``/exchanges``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridge.collect import project_ledger_entry
from bridge.dashboard import RfpBoard, build_dashboard_app, readmodel_snapshot
from bridge.disposition import DispositionVerdict
from bridge.edge import build_agent_card
from bridge.edge.skills import loaded_skills
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import REASON_ESCALATED
from bridge.portal.store import InMemoryPortalStore
from bridge.rfp import (
    KIND_ATTACHMENT_UNREADABLE,
    KIND_ESCALATED,
    KIND_UNREQUESTED,
    acknowledge,
    decline,
    lifecycle_at,
    load_rfp_policy,
    note_unreadable,
    open_rfp,
    receive,
    rfp_status,
    to_exchange_turn,
    update_requirements,
)
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import (
    Disposition,
    Gate,
    LifecycleStatus,
    RequirementStatus,
    RfpItemStatus,
)
from contract.wire import (
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
    decode_wire,
)

PROCESS = "benefits-rfp"  # the process skill under test (named here, never in bridge/)
BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)
CARRIERS = ("carrier-a", "carrier-b", "carrier-c")
LINES = ("LIFE", "AD_AND_D")
OPENED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
DAY = timedelta(days=1)
CORPUS_DOC = {
    "carrier-a": "benefits-carrier-a-usd-en",
    "carrier-b": "benefits-carrier-b-usd-en",
    "carrier-c": "benefits-carrier-c-eur-fr",
}


@pytest.fixture(scope="module")
def policy():
    return load_rfp_policy(PROCESS)


def _requirements(carriers=CARRIERS, lines=LINES, status=RequirementStatus.required):
    return RequirementsList(
        requirements=[
            Requirement(
                item=line, party=c, status=status, doctype_hint="benefits-quote"
            )
            for c in carriers
            for line in lines
        ]
    )


def _request(**over) -> CollectRequest:
    return CollectRequest(
        process=PROCESS,
        party="acme-broker",
        context_id="rfp-ctx",
        requirements=over.pop("requirements", _requirements()),
        due_at=over.pop("due_at", DUE),
        **over,
    )


def _open():
    return open_rfp(_request(), now=OPENED, rfp_id="acme-vb-2027")


def _entry(
    doc_id,
    *,
    disposition=Disposition.accepted,
    gate=Gate.auto_approve,
    reason_code=None,
    message=None,
    product_line=None,
):
    key_fields = {"product_line": product_line} if product_line else {}
    return LedgerEntry(
        doc_id=doc_id,
        doctype="benefits-quote",
        disposition=disposition,
        gate=gate,
        key_fields=key_fields,
        reason_code=reason_code,
        message=message,
    )


def _bad(doc_id, n=0):
    return _entry(
        f"{doc_id}-{n}",
        disposition=Disposition.rejected,
        gate=Gate.resubmit,
        reason_code="low_confidence",
        message="Please resend a clearer copy.",
    )


def _status_of(view, party, line):
    return next(c for c in view.cells if c.party == party and c.product_line == line)


# --------------------------------------------------------------------------- #
# Policy from the process skill (S0-arch-15: the core names no skill)          #
# --------------------------------------------------------------------------- #
_POLICY_YAML = (
    Path(__file__).resolve().parents[1] / "skills/benefits-rfp/assets/policy.yaml"
).read_text()


def _skill(root, name, *, candidates='"benefits-quote"', policy=_POLICY_YAML):
    skill = root / name
    (skill / "assets").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: synthetic\nmetadata:\n"
        f"  bridge-kind: process\n  bridge-pattern: collect\n"
        f"  bridge-candidate-doctypes: {candidates}\n"
        f"  bridge-policy: assets/policy.yaml\n---\nbody\n"
    )
    (skill / "assets" / "policy.yaml").write_text(policy)
    return root


class TestPolicyFromSkill:
    def test_policy_carries_the_skills_process_label_space_and_item_key(self, policy):
        assert policy.process == PROCESS
        assert policy.candidate_doctypes == ("benefits-quote",)
        assert policy.item_field == "product_line"
        assert policy.doctype_hint == "benefits-quote"

    def test_core_exports_no_skill_constant(self):
        import bridge.rfp as rfp_mod

        assert not hasattr(rfp_mod, "PROCESS")
        assert not hasattr(rfp_mod, "QUOTE_DOCTYPE")

    def test_negotiate_process_refused_on_pattern(self):
        with pytest.raises(
            ValueError, match=r"benefits-procurement.*pattern 'negotiate'"
        ):
            load_rfp_policy("benefits-procurement")

    def test_doctype_skill_refused_on_kind(self):
        with pytest.raises(ValueError, match=r"benefits-quote.*bridge-kind 'doctype'"):
            load_rfp_policy("benefits-quote")

    def test_bounded_collect_without_rfp_block_refused(self):
        with pytest.raises(ValueError, match=r"address-proof.*missing an 'rfp:' block"):
            load_rfp_policy("address-proof")

    def test_empty_label_space_refused(self, tmp_path):
        root = _skill(tmp_path, "no-labels", candidates='""')
        with pytest.raises(ValueError, match=r"no-labels.*empty label space"):
            load_rfp_policy("no-labels", skills_dir=root)

    def test_empty_item_field_refused(self, tmp_path):
        policy_yaml = re.sub(r"item_field: \S+", 'item_field: ""', _POLICY_YAML)
        root = _skill(tmp_path, "blank-item", policy=policy_yaml)
        with pytest.raises(ValueError, match=r"blank-item.*rfp.item_field"):
            load_rfp_policy("blank-item", skills_dir=root)

    def test_item_field_defaults_and_two_candidates_give_no_hint(self, tmp_path):
        policy_yaml = re.sub(r"\n\s*item_field:[^\n]*", "", _POLICY_YAML)
        root = _skill(
            tmp_path, "two-labels", candidates='"form-a form-b"', policy=policy_yaml
        )
        policy = load_rfp_policy("two-labels", skills_dir=root)
        assert policy.item_field == "product_line"
        assert policy.candidate_doctypes == ("form-a", "form-b")
        assert policy.doctype_hint is None

    def test_board_refuses_an_rfp_of_another_process(self, policy):
        board = RfpBoard(policy=policy, clock=lambda: OPENED)
        foreign = _open().model_copy(update={"process": "supplier-onboarding"})
        with pytest.raises(ValueError, match="runs process 'supplier-onboarding'"):
            board.put(foreign)
        assert len(board.rfps) == 0


# --------------------------------------------------------------------------- #
# Skill + contract                                                            #
# --------------------------------------------------------------------------- #
class TestSkillAndContract:
    def test_process_skill_loads_and_is_advertised(self):
        skill = next(s for s in loaded_skills() if s.name == PROCESS)
        assert skill.kind == "process"
        assert skill.candidate_doctypes == ("benefits-quote",)
        card = build_agent_card("http://localhost:8000/")
        advertised = next(s for s in card.skills if s.id == PROCESS)
        assert "doctype:benefits-quote" in advertised.tags

    def test_policy_parses(self, policy):
        assert policy.tick == DAY
        assert policy.max_resubmissions == 3
        assert [t for t, _ in policy.sla.schedule()] == [1, 3, 5]

    def test_collect_request_round_trips_the_rfp(self):
        req = _request()
        back = decode_wire(CollectRequest, req.model_dump(mode="json"))
        assert back == req
        assert back.requirements.requirements[0].party == "carrier-a"
        assert back.due_at == DUE

    def test_older_minor_payload_still_decodes(self):
        payload = {"contract_version": "1.1", "process": "address-proof"}
        req = decode_wire(CollectRequest, payload)
        assert req.requirements is None and req.due_at is None


# --------------------------------------------------------------------------- #
# Open + isolation                                                            #
# --------------------------------------------------------------------------- #
class TestOpen:
    def test_one_request_opens_isolated_legs_and_invited_items(self):
        rfp = _open()
        assert [leg.party for leg in rfp.legs] == list(CARRIERS)
        assert len({leg.context_id for leg in rfp.legs}) == 3
        assert rfp.context_id not in {leg.context_id for leg in rfp.legs}
        assert len(rfp.cells) == 6
        assert {c.status for c in rfp.cells} == {RfpItemStatus.invited}

    @pytest.mark.parametrize(
        "bad",
        [
            {"requirements": None},
            {"due_at": None},
            {"due_at": datetime(2026, 10, 15)},  # naive
            {
                "requirements": RequirementsList(
                    requirements=[
                        Requirement(item="LIFE", status=RequirementStatus.required)
                    ]
                )
            },
            {
                "requirements": RequirementsList(
                    requirements=[
                        Requirement(
                            item="LIFE",
                            party="carrier-a",
                            status=RequirementStatus.required,
                        ),
                        Requirement(
                            item="LIFE",
                            party="carrier-a",
                            status=RequirementStatus.required,
                        ),
                    ]
                )
            },
        ],
    )
    def test_rejects_malformed_request(self, bad):
        with pytest.raises(ValueError):
            open_rfp(_request(**bad), now=OPENED)


# --------------------------------------------------------------------------- #
# The due date drives the ladder                                              #
# --------------------------------------------------------------------------- #
class TestLadder:
    @pytest.mark.parametrize(
        ("at", "step"),
        [
            (DUE - DAY, LifecycleStatus.on_track),
            (DUE - timedelta(seconds=1), LifecycleStatus.on_track),
            (DUE, LifecycleStatus.overdue),
            (DUE + DAY, LifecycleStatus.overdue),
            (DUE + 2 * DAY, LifecycleStatus.reminder),
            (DUE + 4 * DAY, LifecycleStatus.escalated),
            (DUE + 30 * DAY, LifecycleStatus.escalated),
        ],
    )
    def test_ladder_anchored_on_due_date(self, policy, at, step):
        assert lifecycle_at(policy, DUE, at) is step

    def test_waiting_items_turn_overdue_past_due(self, policy):
        rfp = acknowledge(_open(), "carrier-a", now=OPENED + DAY)
        before = rfp_status(rfp, now=DUE - DAY, policy=policy)
        assert (
            _status_of(before, "carrier-a", "LIFE").status is RfpItemStatus.acknowledged
        )
        assert _status_of(before, "carrier-b", "LIFE").status is RfpItemStatus.invited
        assert before.lifecycle is LifecycleStatus.on_track
        after = rfp_status(rfp, now=DUE, policy=policy)
        assert {c.status for c in after.cells} == {RfpItemStatus.overdue}
        assert after.lifecycle is LifecycleStatus.overdue

    def test_carrier_that_responded_stops_laddering(self, policy):
        rfp = _open()
        for line in LINES:
            rfp = receive(
                rfp,
                "carrier-a",
                _entry(f"a-{line}"),
                now=OPENED + DAY,
                policy=policy,
                product_line=line,
            )
        rfp = decline(
            rfp, "carrier-b", now=OPENED + DAY, message="Not writing VB this year."
        )
        view = rfp_status(rfp, now=DUE + 4 * DAY, policy=policy)
        lifecycle = {c.party: c.lifecycle for c in view.carriers}
        assert lifecycle == {
            "carrier-a": LifecycleStatus.on_track,
            "carrier-b": LifecycleStatus.on_track,  # declined: nothing to chase
            "carrier-c": LifecycleStatus.escalated,  # silent past due + 4d
        }
        assert view.lifecycle is LifecycleStatus.escalated


# --------------------------------------------------------------------------- #
# Filing submissions                                                          #
# --------------------------------------------------------------------------- #
class TestReceive:
    def test_accepted_quote_is_received_and_revision_is_a_new_version(self, policy):
        rfp = receive(
            _open(),
            "carrier-a",
            _entry("a-life-v0", product_line="LIFE"),
            now=OPENED + DAY,
            policy=policy,
        )
        rfp = receive(
            rfp,
            "carrier-a",
            _entry("a-life-v1", product_line="LIFE"),
            now=OPENED + 2 * DAY,
            policy=policy,
        )
        cell = rfp.cell("carrier-a", "LIFE")
        assert cell.status is RfpItemStatus.received
        assert cell.versions == ["a-life-v0", "a-life-v1"]
        assert [e.doc_id for e in rfp.entries] == ["a-life-v0", "a-life-v1"]

    def test_failing_quote_is_incomplete_with_verbatim_reason(self, policy):
        rfp = receive(
            _open(),
            "carrier-b",
            _bad("b-life"),
            now=OPENED + DAY,
            policy=policy,
            product_line="LIFE",
        )
        cell = rfp.cell("carrier-b", "LIFE")
        assert cell.status is RfpItemStatus.incomplete
        assert (cell.reason_code, cell.message) == (
            "low_confidence",
            "Please resend a clearer copy.",
        )
        assert cell.attempts == 1
        # incomplete is not "overdue" — it says more (the carrier must fix it)
        view = rfp_status(rfp, now=DUE + DAY, policy=policy)
        assert _status_of(view, "carrier-b", "LIFE").status is RfpItemStatus.incomplete

    def test_hitl_park_is_incomplete_but_not_an_attempt(self, policy):
        hitl = _entry(
            "b-life",
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            reason_code="mid_confidence",
            message="Queued for review.",
        )
        rfp = receive(
            _open(), "carrier-b", hitl, now=OPENED, policy=policy, product_line="LIFE"
        )
        assert rfp.cell("carrier-b", "LIFE").status is RfpItemStatus.incomplete
        assert rfp.cell("carrier-b", "LIFE").attempts == 0

    def test_resubmit_cap_escalates_never_rejects(self, policy):
        rfp = _open()
        for n in range(policy.max_resubmissions):
            rfp = receive(
                rfp,
                "carrier-b",
                _bad("b-life", n),
                now=OPENED,
                policy=policy,
                product_line="LIFE",
            )
        assert not rfp.cell("carrier-b", "LIFE").escalated
        rfp = receive(
            rfp,
            "carrier-b",
            _bad("b-life", 9),
            now=OPENED,
            policy=policy,
            product_line="LIFE",
        )
        cell = rfp.cell("carrier-b", "LIFE")
        assert cell.escalated and cell.status is RfpItemStatus.incomplete
        assert cell.reason_code == REASON_ESCALATED
        assert [e.kind for e in rfp.audit].count(KIND_ESCALATED) == 1
        view = rfp_status(
            rfp, now=DUE - DAY, policy=policy
        )  # before due, cap still escalates
        assert {c.party: c.lifecycle for c in view.carriers}["carrier-b"] is (
            LifecycleStatus.escalated
        )

    def test_product_line_from_the_quote_itself(self, policy):
        rfp = receive(
            _open(),
            "carrier-c",
            _entry("c-add", product_line="AD_AND_D"),
            now=OPENED,
            policy=policy,
        )
        assert rfp.cell("carrier-c", "AD_AND_D").status is RfpItemStatus.received

    def test_unrequested_quote_is_flagged_not_dropped(self, policy):
        rfp = receive(
            _open(),
            "carrier-c",
            _entry("c-ci", product_line="CRITICAL_ILLNESS"),
            now=OPENED,
            policy=policy,
        )
        assert rfp.unmatched == ["c-ci"]
        assert [e.doc_id for e in rfp.entries] == ["c-ci"]
        assert rfp.audit[-1].kind == KIND_UNREQUESTED
        assert {c.status for c in rfp.cells} == {RfpItemStatus.invited}

    def test_uninvited_carrier_is_refused(self, policy):
        with pytest.raises(KeyError):
            receive(
                _open(),
                "carrier-z",
                _entry("z", product_line="LIFE"),
                now=OPENED,
                policy=policy,
            )

    def test_decline_one_line_and_cannot_decline_a_received_one(self, policy):
        rfp = receive(
            _open(),
            "carrier-a",
            _entry("a-life", product_line="LIFE"),
            now=OPENED,
            policy=policy,
        )
        with pytest.raises(ValueError):
            decline(rfp, "carrier-a", product_line="LIFE", now=OPENED)
        rfp = decline(
            rfp,
            "carrier-a",
            product_line="AD_AND_D",
            now=OPENED,
            message="AD&D not offered in this state.",
        )
        assert rfp.cell("carrier-a", "AD_AND_D").status is RfpItemStatus.declined
        assert rfp.cell("carrier-a", "LIFE").status is RfpItemStatus.received

    def test_operations_are_pure(self, policy):
        rfp = _open()
        receive(
            rfp,
            "carrier-a",
            _entry("a", product_line="LIFE"),
            now=OPENED,
            policy=policy,
        )
        acknowledge(rfp, "carrier-b", now=OPENED)
        assert {c.status for c in rfp.cells} == {RfpItemStatus.invited}
        assert rfp.entries == []


# --------------------------------------------------------------------------- #
# Emergent requirements (sense B)                                             #
# --------------------------------------------------------------------------- #
class TestUpdateRequirements:
    def test_agent_adds_a_carrier_and_a_line_and_waives_an_item(self, policy):
        rfp = _open()
        next_reqs = RequirementsList(
            requirements=[
                Requirement(
                    item="LIFE", party="carrier-c", status=RequirementStatus.waived
                ),
                Requirement(
                    item="CRITICAL_ILLNESS",
                    party="carrier-a",
                    status=RequirementStatus.required,
                ),
                Requirement(
                    item="LIFE", party="carrier-d", status=RequirementStatus.required
                ),
            ]
        )
        rfp = update_requirements(rfp, next_reqs, now=OPENED + DAY)
        assert [leg.party for leg in rfp.legs] == [*CARRIERS, "carrier-d"]
        assert len({leg.context_id for leg in rfp.legs}) == 4
        assert rfp.cell("carrier-a", "CRITICAL_ILLNESS").status is RfpItemStatus.invited
        assert rfp.cell("carrier-c", "LIFE").requirement is RequirementStatus.waived
        assert len(rfp.cells) == 8  # nothing dropped
        view = rfp_status(rfp, now=OPENED + DAY, policy=policy)
        assert (view.ready, view.waiting) == (0, 7)  # the waived item is neither


# --------------------------------------------------------------------------- #
# The answer on the wire and on the dashboard                                 #
# --------------------------------------------------------------------------- #
async def _corpus_entry(party, *, product_line):
    """A carrier quote through the REAL path: corpus extraction → signals → gate → ledger."""
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    result = await extractor.extract(CORPUS_DOC[party])
    verdict: DispositionVerdict = dispose_signals(
        signals_from_result(result, candidate_doctypes=["benefits-quote"])
    )
    entry = project_ledger_entry(
        doc_id=f"{party}-{product_line}", result=result, verdict=verdict
    )
    return entry, product_line


async def _half_filled(policy):
    rfp = _open()
    rfp = acknowledge(rfp, "carrier-b", now=OPENED + DAY)
    for line in LINES:
        entry, line = await _corpus_entry("carrier-a", product_line=line)
        rfp = receive(
            rfp,
            "carrier-a",
            entry,
            now=OPENED + 2 * DAY,
            policy=policy,
            product_line=line,
        )
    entry, line = await _corpus_entry("carrier-c", product_line="LIFE")
    rfp = receive(
        rfp, "carrier-c", entry, now=OPENED + 3 * DAY, policy=policy, product_line=line
    )
    return decline(
        rfp,
        "carrier-c",
        product_line="AD_AND_D",
        now=OPENED + 3 * DAY,
        message="AD&D not offered.",
    )


class TestAnswer:
    async def test_corpus_quotes_file_through_the_real_gate(self, policy):
        rfp = await _half_filled(policy)
        assert all(e.disposition is Disposition.accepted for e in rfp.entries)
        view = rfp_status(rfp, now=DUE - DAY, policy=policy)
        assert (view.ready, view.waiting) == (3, 3)
        assert view.counts == {
            RfpItemStatus.received: 3,
            RfpItemStatus.acknowledged: 2,
            RfpItemStatus.declined: 1,
        }

    async def test_exchange_turn_proposes_the_outstanding_delta(self, policy):
        rfp = await _half_filled(policy)
        turn = to_exchange_turn(rfp, now=DUE, policy=policy)
        assert turn.context_id == "rfp-ctx"
        assert turn.terminal is False and turn.requirements is None
        assert turn.status.outstanding.done is False
        assert turn.lifecycle is LifecycleStatus.overdue
        outstanding = {
            (r.party, r.item): r.reason_code
            for r in turn.status.outstanding.requirements
        }
        assert outstanding == {
            ("carrier-b", "LIFE"): "overdue",
            ("carrier-b", "AD_AND_D"): "overdue",
            ("carrier-c", "AD_AND_D"): "declined",
        }
        assert len(turn.status.entries) == 3
        # survives the wire
        assert decode_wire(ExchangeTurn, turn.model_dump(mode="json")) == turn

    async def test_dashboard_exchanges_carry_the_matrix(self, policy):
        rfp = await _half_filled(policy)
        clock = {"now": DUE - DAY}
        board = RfpBoard(policy=policy, clock=lambda: clock["now"])
        board.put(rfp)
        client = TestClient(
            build_dashboard_app(store=InMemoryPortalStore(), rfps=board)
        )

        rows = client.get("/exchanges").json()
        assert [r["session_id"] for r in rows] == ["acme-vb-2027"]
        row = rows[0]
        assert (row["process"], row["status"], row["lifecycle"]) == (
            PROCESS,
            "in_flight",
            "on_track",
        )
        cells = {
            (c["party"], c["product_line"]): c["status"] for c in row["rfp"]["cells"]
        }
        assert cells[("carrier-a", "LIFE")] == "received"
        assert cells[("carrier-b", "LIFE")] == "acknowledged"
        assert client.get("/exchanges/acme-vb-2027").json() == row
        assert client.get("/queues/escalation").json() == []

        clock["now"] = DUE + 4 * DAY  # the ladder steps on read, no re-put
        row = client.get("/exchanges/acme-vb-2027").json()
        assert row["lifecycle"] == "escalated" and row["escalated"] is True
        assert [r["session_id"] for r in client.get("/queues/escalation").json()] == [
            "acme-vb-2027"
        ]

    async def test_snapshot_only_changes_on_a_state_change(self, policy):
        """The wall clock moving must not churn the SSE diff-poll; a ladder step must."""
        rfp = await _half_filled(policy)
        clock = {"now": DUE - 2 * DAY}
        board = RfpBoard(policy=policy, clock=lambda: clock["now"])
        board.put(rfp)
        store = InMemoryPortalStore()
        first = readmodel_snapshot(store, rfps=board)
        clock["now"] += timedelta(hours=3)
        assert readmodel_snapshot(store, rfps=board) == first
        clock["now"] = DUE
        assert readmodel_snapshot(store, rfps=board) != first

    def test_snapshot_unchanged_without_an_rfp_board(self):
        store = InMemoryPortalStore()
        assert readmodel_snapshot(store) == readmodel_snapshot(store, rfps=None)
        assert "rfp" not in str(readmodel_snapshot(store))


# --------------------------------------------------------------------------- #
# Unreadable attachments (S0-arch-13)                                         #
# --------------------------------------------------------------------------- #
class TestNoteUnreadable:
    def test_audit_only_and_pure(self):
        rfp = _open()
        before = rfp.model_dump()
        noted = note_unreadable(
            rfp,
            "carrier-a",
            filename="rates.xlsx",
            reason="unsupported_media_type",
            now=OPENED + DAY,
        )
        assert rfp.model_dump() == before  # the input is unchanged
        assert noted.cells == rfp.cells and noted.entries == rfp.entries
        assert noted.unmatched == rfp.unmatched
        (event,) = noted.audit[len(rfp.audit) :]
        assert event.kind == KIND_ATTACHMENT_UNREADABLE
        assert (event.party, event.seq) == ("carrier-a", len(rfp.audit))
        assert event.detail == "rates.xlsx: unsupported_media_type"

    def test_uninvited_carrier_raises_and_naive_now_refused(self):
        rfp = _open()
        with pytest.raises(KeyError):
            note_unreadable(rfp, "carrier-z", filename="x.pdf", reason="r", now=OPENED)
        with pytest.raises(ValueError, match="timezone-aware"):
            note_unreadable(
                rfp,
                "carrier-a",
                filename="x.pdf",
                reason="r",
                now=datetime(2026, 10, 1),
            )
