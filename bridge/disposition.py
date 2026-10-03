"""Disposition gate — the one inversion (pure, deterministic, imports only contract + stdlib).

The gate order, thresholds and boundary semantics are the published consumer contract:
design/disposition.md, "The review contract".
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from bridge.classification import is_in_label_space
from contract.enums import Disposition, Gate


@dataclass(frozen=True)
class DispositionThresholds:
    """Thresholds for the disposition gate.

    Invariant: ``0.0 <= resubmit_below <= auto_approve_at <= 1.0`` (NaN refused).
    Equal values are legal: there is then no confidence HITL band, and flags still
    route to HITL.
    """
    auto_approve_at: float = 0.85
    resubmit_below: float = 0.55

    def __post_init__(self) -> None:
        # Written as one chained comparison so NaN (never ordered) is refused too.
        if not (0.0 <= self.resubmit_below <= self.auto_approve_at <= 1.0):
            raise ValueError(
                "disposition thresholds must satisfy "
                "0 <= resubmit_below <= auto_approve_at <= 1; got "
                f"auto_approve_at={self.auto_approve_at!r}, "
                f"resubmit_below={self.resubmit_below!r}"
            )


DEFAULT_THRESHOLDS = DispositionThresholds()


# --- Calibration corridor (pure) ---------------------------------------------------
# Each gate-bearing corpus record bounds the thresholds through the gate's own
# precedence; the legal region is the intersection (the corridor). The corpus reading
# lives in ``bridge.calibration``; this is the pure arithmetic beside the gate it mirrors.


@dataclass(frozen=True)
class CorridorPoint:
    """One gate-bearing corpus record, projected onto what the gate reads."""
    record_id: str
    overall_confidence: float
    legible: bool
    in_label_space: bool
    flagged: bool
    expected_gate: Gate


@dataclass(frozen=True)
class Bound:
    """A half-open interval ``gt < value <= le``; ``None`` = unconstrained on that side.

    ``gt_by`` / ``le_by`` name the corpus record that set each edge.
    """
    gt: float | None = None
    gt_by: str | None = None
    le: float | None = None
    le_by: str | None = None

    def contains(self, v: float) -> bool:
        """``v`` lies inside the interval (NaN never does)."""
        if math.isnan(v):
            return False
        if self.gt is not None and not v > self.gt:
            return False
        return self.le is None or v <= self.le

    @property
    def feasible(self) -> bool:
        """The interval is non-empty."""
        return self.gt is None or self.le is None or self.gt < self.le


@dataclass(frozen=True)
class ThresholdCorridor:
    """The feasible threshold region implied by a set of corpus points."""
    resubmit_below: Bound
    auto_approve_at: Bound
    points: int

    def contains(self, t: DispositionThresholds) -> bool:
        """Both thresholds lie inside their bounds (``r <= a`` is the thresholds' own
        invariant, enforced at construction)."""
        return self.resubmit_below.contains(t.resubmit_below) and self.auto_approve_at.contains(
            t.auto_approve_at
        )

    @property
    def feasible(self) -> bool:
        """Some legal ``DispositionThresholds`` reproduces every point."""
        if not (self.resubmit_below.feasible and self.auto_approve_at.feasible):
            return False
        r_gt, a_le = self.resubmit_below.gt, self.auto_approve_at.le
        return r_gt is None or a_le is None or r_gt < a_le


def threshold_corridor(points: Iterable[CorridorPoint]) -> ThresholdCorridor:
    """Intersect the threshold constraints every point imposes, mirroring ``dispose``.

    Rules (first-match precedence, exactly as the gate):
    - out of the label space → must expect ``unsupported``; constrains nothing.
    - illegible → must expect ``resubmit``; constrains nothing.
    - legible + ``resubmit`` → ``resubmit_below > c``.
    - legible + ``hitl_review`` / ``auto_approve`` → ``resubmit_below <= c``.
    - unflagged ``hitl_review`` → ``auto_approve_at > c`` (a flagged one is HITL at any
      ``auto_approve_at``).
    - ``auto_approve`` → must be unflagged; ``auto_approve_at <= c``.

    A record no threshold pair could reproduce raises ``ValueError``. On ties the
    first record id is kept as the binding one.
    """
    r_gt: tuple[float, str] | None = None
    r_le: tuple[float, str] | None = None
    a_gt: tuple[float, str] | None = None
    a_le: tuple[float, str] | None = None

    def _raise(p: CorridorPoint, why: str) -> None:
        raise ValueError(
            f"corpus record {p.record_id!r} expects gate {p.expected_gate.value!r} "
            f"but {why} — no thresholds can reproduce it"
        )

    def _max(cur: tuple[float, str] | None, p: CorridorPoint) -> tuple[float, str]:
        c = p.overall_confidence
        return (c, p.record_id) if cur is None or c > cur[0] else cur

    def _min(cur: tuple[float, str] | None, p: CorridorPoint) -> tuple[float, str]:
        c = p.overall_confidence
        return (c, p.record_id) if cur is None or c < cur[0] else cur

    n = 0
    for p in points:
        n += 1
        if not p.in_label_space:
            if p.expected_gate is not Gate.unsupported:
                _raise(p, "its doctype is outside the label space")
            continue
        if p.expected_gate is Gate.unsupported:
            _raise(p, "its doctype is inside the label space")
        if not p.legible:
            if p.expected_gate is not Gate.resubmit:
                _raise(p, "it is illegible")
            continue
        if p.expected_gate is Gate.resubmit:
            r_gt = _max(r_gt, p)
            continue
        # legible, in label space, expected hitl_review or auto_approve
        r_le = _min(r_le, p)
        if p.expected_gate is Gate.hitl_review:
            if not p.flagged:
                a_gt = _max(a_gt, p)
        elif p.expected_gate is Gate.auto_approve:
            if p.flagged:
                _raise(p, "it is flagged (a flagged document never auto-approves)")
            a_le = _min(a_le, p)
        else:  # pragma: no cover - Gate has exactly four members
            _raise(p, "the gate is unknown")

    def _bound(gt: tuple[float, str] | None, le: tuple[float, str] | None) -> Bound:
        return Bound(
            gt=gt[0] if gt else None,
            gt_by=gt[1] if gt else None,
            le=le[0] if le else None,
            le_by=le[1] if le else None,
        )

    return ThresholdCorridor(
        resubmit_below=_bound(r_gt, r_le),
        auto_approve_at=_bound(a_gt, a_le),
        points=n,
    )


class DispositionVerdict(BaseModel, extra="forbid"):
    """A disposition verdict with reason code and human-readable message."""
    disposition: Disposition
    gate: Gate
    reason_code: str
    message: str


def dispose(
    *,
    doctype: str,
    candidate_doctypes: list[str],
    legible: bool,
    overall_confidence: float,
    flagged_fields: list[str] | None = None,
    thresholds: DispositionThresholds = DEFAULT_THRESHOLDS,
) -> DispositionVerdict:
    """Pure disposition gate implementing the ordered decision logic.

    Precedence (first match wins):
    1. Label-space / type gate (doctype not in candidate_doctypes)
    2. Quality gate (not legible or overall_confidence < resubmit_below)
    3. Confidence gate (flagged_fields non-empty or overall_confidence < auto_approve_at)
    4. Auto-approve (fell through all gates)

    Args:
        doctype: The extracted document type.
        candidate_doctypes: The accepted document types for this process.
        legible: Whether the document was readable.
        overall_confidence: The extraction confidence score (0.0-1.0).
        flagged_fields: List of field names flagged for review (None treated as []).
        thresholds: Override for policy thresholds (defaults to module constants).

    Returns:
        DispositionVerdict with disposition, gate, reason_code, and message.
    """
    # Treat None as empty list
    flagged = flagged_fields or []

    # 1. Label-space / type gate
    if not is_in_label_space(doctype, candidate_doctypes):
        # An empty label space (an unresolved process, S0-arch-18) accepts nothing;
        # the non-empty message is relayed by the corpus goldens — keep it verbatim.
        accepted = (
            f"Accepted types: {', '.join(candidate_doctypes)}."
            if candidate_doctypes
            else "This process accepts no document types."
        )
        return DispositionVerdict(
            disposition=Disposition.rejected,
            gate=Gate.unsupported,
            reason_code="unsupported_doctype",
            message=f"The document type '{doctype}' is not supported for this process. {accepted}",
        )

    # 2. Quality gate
    if not legible:
        return DispositionVerdict(
            disposition=Disposition.rejected,
            gate=Gate.resubmit,
            reason_code="illegible",
            message="The document was unreadable — please resubmit a clearer copy.",
        )

    if overall_confidence < thresholds.resubmit_below:
        return DispositionVerdict(
            disposition=Disposition.rejected,
            gate=Gate.resubmit,
            reason_code="low_quality",
            message=f"The document quality was too low (confidence: {overall_confidence:.2f}) — please resubmit a clearer copy.",
        )

    # 3. Confidence gate
    if flagged:
        return DispositionVerdict(
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            reason_code="flagged_fields",
            message=f"The following fields require human review: {', '.join(flagged)}.",
        )

    if overall_confidence < thresholds.auto_approve_at:
        return DispositionVerdict(
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            reason_code="needs_review",
            message=f"The document requires human review (confidence: {overall_confidence:.2f}).",
        )

    # 4. Auto-approve (fell through all gates)
    return DispositionVerdict(
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        reason_code="auto_approved",
        message="The document was automatically approved.",
    )


def run_disposition_gate(
    doctype: str,
    candidate_doctypes: list[str],
    legible: bool,
    overall_confidence: float,
    flagged_fields: list[str],
) -> dict:
    """Tool-facing wrapper for the disposition gate.

    This function is exposed as a FunctionTool to the LLM agent. It determines
    whether a document should be accepted, rejected, or sent for human review
    based on extraction signals.

    Args:
        doctype: The extracted document type.
        candidate_doctypes: The accepted document types for this process.
        legible: Whether the document was readable.
        overall_confidence: The extraction confidence score (0.0-1.0).
        flagged_fields: List of field names flagged for review.

    Returns:
        A dictionary with disposition, gate, reason_code, and message.
    """
    verdict = dispose(
        doctype=doctype,
        candidate_doctypes=candidate_doctypes,
        legible=legible,
        overall_confidence=overall_confidence,
        flagged_fields=flagged_fields,
        thresholds=DEFAULT_THRESHOLDS,
    )
    return verdict.model_dump(mode="json")
