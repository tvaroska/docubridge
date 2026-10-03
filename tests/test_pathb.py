"""Path-B extraction graph tests (P6-1, PATH-B).

Drives the deterministic graph over the eval corpus: clean → auto-approve one step;
wrong doctype → terminal reject; the corpus multi-step (timeline exchange-2 t5-7)
blurry → resubmit park → clear → HITL park → resolved accept; and the resubmit-cap
escalation (escalation-is-NOT-rejection, §2a/§2b). The extraction seam is proven to
be called once per step via a spy.
"""

import pytest

from bridge import pathb
from bridge.extraction.fixture import FixtureExtractor
from bridge.offscript import REASON_ESCALATED, REASON_HUMAN_REVIEW
from bridge.pathb import PathBAction, PathBState
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionResult

DOCTYPES = ("gov-id", "utility-bill")


def _state(**kw) -> PathBState:
    return PathBState(candidate_doctypes=DOCTYPES, **kw)


def _artifact(doc_id: str) -> str:
    return f"images/{doc_id}.svg"


class SpyExtractor:
    """Wraps FixtureExtractor, counting extract() calls (seam-call assertion)."""

    def __init__(self) -> None:
        self._inner = FixtureExtractor()
        self.calls: list[str] = []

    async def extract(self, artifact_name: str) -> ExtractionResult:
        self.calls.append(artifact_name)
        return await self._inner.extract(artifact_name)


@pytest.mark.parametrize("doc_id", ["gov-id-clean", "bill-powerco-clean"])
async def test_clean_doc_auto_approves_one_step(doc_id):
    """A clean doc → ACCEPT, terminal, accepted, attempts unchanged (0)."""
    result = await pathb.step(
        _state(), doc_id=doc_id, artifact_name=_artifact(doc_id),
        extractor=FixtureExtractor(),
    )
    assert result.action is PathBAction.ACCEPT
    assert result.terminal is True
    assert result.verdict.disposition is Disposition.accepted
    assert result.entry.disposition is Disposition.accepted
    assert result.state.attempts == 0
    assert len(result.state.ledger) == 1


async def test_unsupported_doctype_terminal_reject():
    """passport-unsupported → REJECT, terminal, gate==unsupported (a judgment)."""
    result = await pathb.step(
        _state(), doc_id="passport-unsupported",
        artifact_name=_artifact("passport-unsupported"),
        extractor=FixtureExtractor(),
    )
    assert result.action is PathBAction.REJECT
    assert result.terminal is True
    assert result.verdict.gate is Gate.unsupported
    assert result.entry.disposition is Disposition.rejected
    assert result.state.attempts == 0  # NOT the resubmit loop


async def test_blurry_parks_for_resubmit_then_clear_hitl_then_resolved_accept():
    """The corpus multi-step (timeline exchange-2 t5-7). Gate #2.

    blurry → RESUBMIT (non-terminal, attempts==1) → thread state, clear → HITL
    (non-terminal, pending) → resolve_hitl(accepted) → terminal accepted.
    """
    ext = FixtureExtractor()
    state = _state()

    # t5: bill-aquautil-blurry → resubmit park
    s1 = await pathb.step(
        state, doc_id="bill-1", artifact_name=_artifact("bill-aquautil-blurry"),
        extractor=ext,
    )
    assert s1.action is PathBAction.RESUBMIT
    assert s1.terminal is False
    assert s1.state.attempts == 1
    assert s1.message  # actionable resubmit message
    assert s1.entry.disposition is Disposition.rejected  # this version failed quality

    # t6: resubmit bill-aquautil-clear → HITL park (mid-confidence 0.72)
    s2 = await pathb.step(
        s1.state, doc_id="bill-1", artifact_name=_artifact("bill-aquautil-clear"),
        extractor=ext,
    )
    assert s2.action is PathBAction.HITL
    assert s2.terminal is False
    assert s2.verdict.disposition is Disposition.pending
    assert s2.reason_code == REASON_HUMAN_REVIEW
    assert s2.state.attempts == 1  # HITL is not a quality-gate failure

    # t7: hitl_resolved: accepted → terminal accept
    s3 = pathb.resolve_hitl(s2, Disposition.accepted, message="Reviewer approved.")
    assert s3.action is PathBAction.ACCEPT
    assert s3.terminal is True
    assert s3.entry.disposition is Disposition.accepted
    assert s3.entry.message == "Reviewer approved."
    # The resolved entry replaced the parked one in the threaded ledger.
    assert s3.state.ledger[-1].disposition is Disposition.accepted


async def test_resubmit_cap_escalates_not_rejects():
    """Resubmit cap → ESCALATE, never rejected (§2a/§2b). Gate #3.

    Feed an always-illegible doc max_resubmissions+1 times: fails 1-3 park, fail#4
    escalates (pending, REASON_ESCALATED). The boundary (fail#3 still parks at cap=3)
    is pinned exactly.
    """
    ext = FixtureExtractor()
    state = _state(max_resubmissions=3)

    # fail#1..#3 → RESUBMIT (park), attempts 1,2,3
    for i in range(1, 4):
        state_step = await pathb.step(
            state, doc_id=f"bill-{i}",
            artifact_name=_artifact("bill-aquautil-blurry"), extractor=ext,
        )
        assert state_step.action is PathBAction.RESUBMIT, f"fail#{i} should park"
        assert state_step.terminal is False
        assert state_step.state.attempts == i
        state = state_step.state

    # fail#4 → ESCALATE (over cap): pending, route-to-human, NOT rejected
    esc = await pathb.step(
        state, doc_id="bill-4", artifact_name=_artifact("bill-aquautil-blurry"),
        extractor=ext,
    )
    assert esc.action is PathBAction.ESCALATE
    assert esc.verdict.disposition is Disposition.pending
    assert esc.entry.disposition is Disposition.pending
    assert esc.entry.disposition is not Disposition.rejected
    assert esc.reason_code == REASON_ESCALATED
    assert esc.state.attempts == 4


async def test_seam_called_once_per_document():
    """One extract() call per graph step (seam spy)."""
    spy = SpyExtractor()
    await pathb.step(
        _state(), doc_id="d1", artifact_name=_artifact("gov-id-clean"), extractor=spy,
    )
    assert len(spy.calls) == 1
    await pathb.step(
        _state(), doc_id="d2", artifact_name=_artifact("bill-powerco-clean"), extractor=spy,
    )
    assert len(spy.calls) == 2


async def test_resolve_hitl_rejects_non_park():
    """resolve_hitl only finalizes a HITL/ESCALATE park."""
    accept_step = await pathb.step(
        _state(), doc_id="d", artifact_name=_artifact("gov-id-clean"),
        extractor=FixtureExtractor(),
    )
    with pytest.raises(ValueError, match="HITL/ESCALATE park"):
        pathb.resolve_hitl(accept_step, Disposition.accepted, message="x")


async def test_resolve_hitl_reviewer_rejects():
    """A reviewer may resolve a HITL park to rejected (terminal REJECT)."""
    hitl = await pathb.step(
        _state(), doc_id="d", artifact_name=_artifact("bill-aquautil-clear"),
        extractor=FixtureExtractor(),
    )
    assert hitl.action is PathBAction.HITL
    resolved = pathb.resolve_hitl(hitl, Disposition.rejected, message="Reviewer declined.")
    assert resolved.action is PathBAction.REJECT
    assert resolved.terminal is True
    assert resolved.entry.disposition is Disposition.rejected
