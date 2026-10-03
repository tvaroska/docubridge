"""Carrier-facing RFP checklist (S0-frontend-1) — the provider portal's RFP surface.

A carrier opens its checklist by its **leg** ``context_id`` (``GET /rfp/legs/{context_id}``):
which of its product lines are still outstanding, the due date, and every field-level
resubmit reason (``reason_code`` / ``message`` / ``FieldReview`` — ``path``, ``reason``,
``confidence``, ``detail``) relayed **verbatim** from the gate (A2UI-1 content-not-pixels:
the models carry content + protocol, never a style field).

**Addressing is the isolation (``design/zones.md``).** Carrier-vs-carrier confidentiality
is addressing, not a wall: a leg context is unguessable (``uuid4().hex``) and names exactly
one carrier, and the checklist is filtered to that carrier only — no other carrier's name,
cell, counts, doc ids or ``unmatched`` list ever appears in it. No new auth scheme.

**Upload into a row.** ``upload_quote`` files one quote into a named product-line row
through the same extract → signals → disposition gate → ledger path as the email intake
(``bridge.edge.email_ingress`` phases 1/3): extract first with the board untouched, then a
FRESH read → ``acknowledge`` (first response) → ``receive(product_line=…)`` → ``put`` with
no ``await`` in between (the S0-arch-13 concurrency rule). Locally the upload is a corpus
``artifact_name`` the fixture extractor resolves (the Path-B portal posture); a real byte
upload is the P7 swap — noted, not built.

A separate model, NOT a new ``A2uiView.kind``: the A2UI kinds (and their frontend union +
tests) stay untouched; the checklist reuses ``A2uiAction`` for its upload affordance.

Pure-ish (no ``fastapi``): imports ``bridge.*`` + ``contract.*`` + pydantic + stdlib only;
NEVER ``agents/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from bridge.collect import project_ledger_entry
from bridge.edge.a2ui import A2uiAction
from bridge.rfp import (
    RfpCollection,
    RfpPolicy,
    RfpStatusView,
    acknowledge,
    receive,
    rfp_status,
)
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import LifecycleStatus, RequirementStatus, RfpItemStatus
from contract.extraction import FieldReview

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard
    from bridge.email_ingress.correlation import IntakeAddressing


class UploadConflict(ValueError):
    """The row is not requested any more (waived/satisfied), or the ``doc_id`` was already
    filed on this RFP — the host maps it to 409."""


class UploadUnreadable(ValueError):
    """The extraction engine refused the artifact — the host maps it to 422 (board untouched)."""


class RfpChecklistItem(BaseModel):
    """One of the carrier's product lines (content only; reasons relayed verbatim)."""

    model_config = ConfigDict(extra="forbid")

    product_line: str
    requirement: RequirementStatus
    status: RfpItemStatus
    outstanding: bool
    version_count: int
    last_activity: datetime | None
    reason_code: str | None
    message: str | None
    field_reviews: list[FieldReview] = []


class RfpChecklist(BaseModel):
    """One carrier's RFP checklist (content + protocol, no pixels)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["rfp_checklist"] = "rfp_checklist"
    rfp_id: str
    party: str
    context_id: str
    due_at: datetime
    lifecycle: LifecycleStatus
    items: list[RfpChecklistItem]
    reply_address: str | None = None
    reply_subject_tag: str | None = None
    actions: list[A2uiAction] = []


def render_rfp_checklist(
    rfp: RfpCollection,
    view: RfpStatusView,
    party: str,
    *,
    intake: IntakeAddressing | None,
    candidate_doctypes: Sequence[str],
) -> RfpChecklist:
    """``party``'s checklist off the status projection — filtered to ``party`` ONLY.

    ``candidate_doctypes`` is the process skill's label space (``RfpPolicy``) — what the
    upload affordance accepts. Raises ``KeyError`` for a carrier not on the distribution
    list.
    """
    leg = rfp.leg_for(party)
    carrier = next(c for c in view.carriers if c.party == party)
    items = [
        RfpChecklistItem(
            product_line=cv.product_line,
            requirement=cv.requirement,
            status=cv.status,
            outstanding=cell.outstanding,
            version_count=len(cv.versions),
            last_activity=cv.last_activity,
            reason_code=cv.reason_code,
            message=cv.message,
            field_reviews=cv.field_reviews,
        )
        for cv, cell in zip(view.cells, rfp.cells, strict=True)
        if cv.party == party
    ]
    actions = (
        [A2uiAction(kind="upload", accept=list(candidate_doctypes))]
        if any(i.outstanding for i in items)
        else []
    )
    return RfpChecklist(
        rfp_id=rfp.rfp_id,
        party=party,
        context_id=leg.context_id,
        due_at=rfp.due_at,
        lifecycle=carrier.lifecycle,
        items=items,
        reply_address=intake.reply_address(rfp.rfp_id, party) if intake else None,
        reply_subject_tag=intake.reply_subject_tag(rfp.rfp_id, party)
        if intake
        else None,
        actions=actions,
    )


def _policy_of(board: RfpBoard, rfp: RfpCollection) -> RfpPolicy:
    """The RFP's OWN process policy (``board.policy_for``: the home process, or one
    installed live in the registry — S4-usecase-4). An unresolvable process (its skill
    removed or broken after the RFP opened) → ``KeyError`` (chained) → 404."""
    try:
        return board.policy_for(rfp.process)
    except ValueError as exc:
        raise KeyError(
            f"RFP {rfp.rfp_id!r} process {rfp.process!r} is unavailable"
        ) from exc


def checklist_for(
    board: RfpBoard, context_id: str, *, intake: IntakeAddressing | None
) -> RfpChecklist:
    """The checklist of the leg addressed by ``context_id``; ``KeyError`` if no open leg
    (or its RFP's process no longer resolves)."""
    rfp, leg = board.find_leg(context_id)
    policy = _policy_of(board, rfp)
    view = rfp_status(rfp, now=board.clock(), policy=policy)
    return render_rfp_checklist(
        rfp,
        view,
        leg.party,
        intake=intake,
        candidate_doctypes=policy.candidate_doctypes,
    )


def _require_requested(rfp: RfpCollection, party: str, product_line: str) -> None:
    cell = rfp.cell(party, product_line)  # KeyError → not requested of this carrier
    if not cell.tracked:
        raise UploadConflict(
            f"{product_line!r} is no longer requested on RFP {rfp.rfp_id!r} "
            f"({cell.requirement.value})"
        )


async def upload_quote(
    board: RfpBoard,
    context_id: str,
    *,
    doc_id: str,
    artifact_name: str,
    product_line: str,
    extraction: Any,
    intake: IntakeAddressing | None,
) -> RfpChecklist:
    """File one uploaded quote into the leg's ``product_line`` row; return the new checklist.

    Raises ``KeyError`` (unknown leg / row → 404), :class:`UploadConflict` (row waived or
    satisfied, or ``doc_id`` already filed → 409) and :class:`UploadUnreadable` (the engine
    refused the artifact → 422). The board is untouched on every refusal.
    """
    rfp, leg = board.find_leg(context_id)
    policy = _policy_of(board, rfp)
    _require_requested(rfp, leg.party, product_line)

    # Phase 1 — extract and disposition; the board is NOT touched.
    try:
        result = await extraction.extract(artifact_name)
    except (ValueError, KeyError) as exc:
        raise UploadUnreadable(
            f"could not read {artifact_name!r}: {type(exc).__name__}"
        ) from exc
    verdict = dispose_signals(
        signals_from_result(
            result, candidate_doctypes=list(policy.candidate_doctypes)
        )
    )
    entry = project_ledger_entry(doc_id=doc_id, result=result, verdict=verdict)

    # Phase 2 — a FRESH read, then apply and put with no await in between.
    now = board.clock()
    current = board.rfps.get(rfp.rfp_id)
    if current is None:
        raise KeyError(f"RFP {rfp.rfp_id!r} is no longer open")
    _require_requested(current, leg.party, product_line)
    if any(e.doc_id == doc_id for e in current.entries):
        raise UploadConflict(f"doc_id {doc_id!r} was already filed on this RFP")
    if any(
        c.party == leg.party and c.status is RfpItemStatus.invited
        for c in current.cells
    ):
        current = acknowledge(current, leg.party, now=now)
    current = receive(
        current,
        leg.party,
        entry,
        now=now,
        policy=policy,
        product_line=product_line,
    )
    board.put(current)
    view = rfp_status(current, now=now, policy=policy)
    return render_rfp_checklist(
        current,
        view,
        leg.party,
        intake=intake,
        candidate_doctypes=policy.candidate_doctypes,
    )
