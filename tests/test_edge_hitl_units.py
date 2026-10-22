"""S3-edge-6 unit tests — the HITL park/resume building blocks, no server.

- ``bridge-hitl`` skill metadata (``park`` | ``report``; default ``report``);
- reviewer authorization (fail closed; the principal never comes from the payload);
- the LLM leg's pure session-event readers + the resume turn builder;
- the wire encode/decode of ``HumanReviewResolution``.
"""

from __future__ import annotations

import pytest
from a2a.auth.user import UnauthenticatedUser
from a2a.server.routes.common import StarletteUser
from a2a.types import Message, Role
from google.adk.events import Event
from google.genai import types as genai_types
from pydantic import ValidationError
from starlette.authentication import SimpleUser

from bridge.edge.llm_leg import (
    PAUSED_REVIEW_MESSAGE,
    REVIEW_TOOL,
    pending_review_calls,
    project_review_turn,
    resolved_review_calls,
)
from bridge.edge.reviewers import (
    REVIEWERS_ENV,
    is_authorized_reviewer,
    resolve_reviewers,
)
from bridge.edge.skills import hitl_mode_for, skill_meta_from_dir
from bridge.edge.wire import (
    decode_human_review_resolution,
    encode_human_review_resolution,
)
from bridge.offscript import REASON_HUMAN_REVIEW
from contract.enums import Disposition, Gate
from contract.wire import HumanReviewResolution, LedgerEntry

AGENT = "document_bridge"


# --------------------------------------------------------------------------- #
# Skill metadata                                                               #
# --------------------------------------------------------------------------- #
def test_address_proof_parks_benefits_reports():
    assert hitl_mode_for("address-proof") == "park"
    assert hitl_mode_for("benefits-procurement") == "report"
    assert hitl_mode_for("no-such-process") == "report"


def _skill(tmp_path, extra: str):
    d = tmp_path / "x-proc"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: x-proc\ndescription: test\nmetadata:\n"
        "  bridge-kind: process\n" + extra + "---\nbody\n"
    )
    return d


def test_bridge_hitl_absent_defaults_to_report(tmp_path):
    assert skill_meta_from_dir(_skill(tmp_path, "")).hitl == "report"


def test_bridge_hitl_invalid_value_fails_load(tmp_path):
    with pytest.raises(ValidationError):
        skill_meta_from_dir(_skill(tmp_path, "  bridge-hitl: always\n"))


# --------------------------------------------------------------------------- #
# Reviewer authorization                                                       #
# --------------------------------------------------------------------------- #
def test_resolve_reviewers_explicit_wins_over_env(monkeypatch):
    monkeypatch.setenv(REVIEWERS_ENV, "carol")
    assert resolve_reviewers(["alice", "bob"]) == {"alice", "bob"}
    assert resolve_reviewers("alice, bob  dave") == {"alice", "bob", "dave"}
    assert resolve_reviewers() == {"carol"}


def test_resolve_reviewers_unset_is_empty(monkeypatch):
    monkeypatch.delenv(REVIEWERS_ENV, raising=False)
    assert resolve_reviewers() == frozenset()
    monkeypatch.setenv(REVIEWERS_ENV, " , ")
    assert resolve_reviewers() == frozenset()


def test_is_authorized_reviewer_fails_closed():
    alice = StarletteUser(SimpleUser("alice"))
    assert is_authorized_reviewer(alice, frozenset({"alice"}))
    assert not is_authorized_reviewer(alice, frozenset())  # unset → refused
    assert not is_authorized_reviewer(alice, frozenset({"bob"}))
    assert not is_authorized_reviewer(UnauthenticatedUser(), frozenset({""}))
    assert not is_authorized_reviewer(None, frozenset({"alice"}))


# --------------------------------------------------------------------------- #
# Wire                                                                         #
# --------------------------------------------------------------------------- #
def test_resolution_wire_round_trip_and_absent():
    res = HumanReviewResolution(call_id="c1", resolution="rejected", message="no")
    msg = Message(
        role=Role.ROLE_USER, message_id="m", parts=[encode_human_review_resolution(res)]
    )
    assert decode_human_review_resolution(msg) == res
    assert (
        decode_human_review_resolution(
            Message(role=Role.ROLE_USER, message_id="m2", parts=[])
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Pure session-event readers + resume turn builder                             #
# --------------------------------------------------------------------------- #
def _call_event(*calls: tuple[str, str], flagged: bool = True, inv: str = "inv-1"):
    parts = [
        genai_types.Part(
            function_call=genai_types.FunctionCall(
                id=cid, name=REVIEW_TOOL, args={"reason": "r", "artifact_name": name}
            )
        )
        for cid, name in calls
    ]
    return Event(
        author=AGENT,
        invocation_id=inv,
        content=genai_types.Content(role="model", parts=parts),
        long_running_tool_ids={cid for cid, _ in calls} if flagged else None,
    )


def _resolution_event(cid: str, resolution: str, message: str, inv: str = "inv-1"):
    return Event(
        author="user",
        invocation_id=inv,
        content=genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id=cid,
                        name=REVIEW_TOOL,
                        response={"resolution": resolution, "message": message},
                    )
                )
            ],
        ),
    )


def _seed():
    return [
        LedgerEntry(
            doc_id="gov-id-expired",
            doctype="gov-id",
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            reason_code="flagged_fields",
            message="m1",
        ),
        LedgerEntry(
            doc_id="bill-aquautil-clear",
            doctype="utility-bill",
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            reason_code="low_confidence",
            message="m2",
        ),
        LedgerEntry(
            doc_id="bill-powerco-clean",
            doctype="utility-bill",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="ok",
            message="m3",
        ),
    ]


def test_pending_review_calls_requires_flag_and_no_response():
    parked = _call_event(("c1", "gov-id-expired"), ("c2", "bill-aquautil-clear"))
    unflagged = _call_event(("c3", "bill-aquautil-clear"), flagged=False)
    assert pending_review_calls([parked, unflagged]) == ["c1", "c2"]
    answered = _resolution_event("c1", "accepted", "ok")
    assert pending_review_calls([parked, answered]) == ["c2"]
    assert resolved_review_calls([parked, answered]) == {
        "c1": {"resolution": "accepted", "message": "ok"}
    }


def test_project_review_turn_partial_then_full_resolution():
    seed = _seed()
    parked = _call_event(("c1", "gov-id-expired"), ("c2", "bill-aquautil-clear"))
    r1 = _resolution_event("c1", "rejected", "Expired ID.")
    review = project_review_turn(
        [parked, r1], context_id="ctx", seed_entries=seed, call_ids=["c1", "c2"]
    )
    by_id = {e.doc_id: e for e in review.turn.status.entries}
    assert review.pending == ("c2",)
    assert review.turn.terminal is False
    # resolved: the ORIGINAL entry transformed (gate + gate reason kept)
    assert by_id["gov-id-expired"].disposition is Disposition.rejected
    assert by_id["gov-id-expired"].gate is Gate.hitl_review
    assert by_id["gov-id-expired"].reason_code == "flagged_fields"
    assert by_id["gov-id-expired"].message == "Expired ID."
    # still pending: the paused overlay
    assert by_id["bill-aquautil-clear"].reason_code == REASON_HUMAN_REVIEW
    assert by_id["bill-aquautil-clear"].message == PAUSED_REVIEW_MESSAGE
    # untouched entries ride along unchanged, in seed order
    assert by_id["bill-powerco-clean"] == seed[2]
    assert [e.doc_id for e in review.turn.status.entries] == [e.doc_id for e in seed]

    r2 = _resolution_event("c2", "accepted", "Fine.")
    review = project_review_turn(
        [parked, r1, r2], context_id="ctx", seed_entries=seed, call_ids=["c1", "c2"]
    )
    by_id = {e.doc_id: e for e in review.turn.status.entries}
    assert review.pending == ()
    assert review.turn.terminal is True
    assert by_id["gov-id-expired"].disposition is Disposition.rejected
    assert by_id["bill-aquautil-clear"].disposition is Disposition.accepted
    assert by_id["bill-aquautil-clear"].reason_code == "low_confidence"
    assert set(review.resolved) == {"c1", "c2"}


def test_project_review_turn_scoped_to_task_call_ids():
    """A call parked on another task is never pending for this one."""
    seed = _seed()[1:]
    mine = _call_event(("a1", "bill-aquautil-clear"), inv="inv-a")
    theirs = _call_event(("b1", "gov-id-expired"), inv="inv-b")
    review = project_review_turn(
        [mine, theirs], context_id="ctx", seed_entries=seed, call_ids=["a1"]
    )
    assert review.pending == ("a1",)
    assert review.call_ids == ("a1",)


def test_model_authored_response_is_not_a_resolution():
    """Only a user-authored (executor-fed) response resolves; a model/tool-authored
    review response does not count as a reviewer decision."""
    parked = _call_event(("c1", "bill-aquautil-clear"))
    fake = Event(
        author=AGENT,
        invocation_id="inv-1",
        content=genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id="c1",
                        name=REVIEW_TOOL,
                        response={"resolution": "accepted", "message": "x"},
                    )
                )
            ],
        ),
    )
    assert resolved_review_calls([parked, fake]) == {}
    review = project_review_turn(
        [parked, fake], context_id="ctx", seed_entries=_seed()[1:2], call_ids=["c1"]
    )
    assert review.resolved == {}
    (entry,) = review.turn.status.entries
    assert entry.disposition is Disposition.pending
