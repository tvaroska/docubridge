"""Shared enum vocabularies for wire types."""

from enum import Enum


class Disposition(str, Enum):
    """Per-document verdict (accepted/rejected/pending)."""

    accepted = "accepted"
    rejected = "rejected"
    pending = "pending"


class Gate(str, Enum):
    """Disposition gate (auto_approve/hitl_review/resubmit/unsupported)."""

    auto_approve = "auto_approve"
    hitl_review = "hitl_review"
    resubmit = "resubmit"
    unsupported = "unsupported"


class ReviewReason(str, Enum):
    """Why a field requires review (extraction provenance, sense A).

    ``model_flagged`` is the honest default when all the engine gave us is a bare
    name in ``flagged_fields``: ``low_confidence`` and ``derived`` are NEVER
    inferred — they are only ever stated by an engine that knows them.
    """

    low_confidence = "low_confidence"  # engine reported a confidence it distrusts
    derived = "derived"  # inferred/defaulted, not read off the document
    absent_from_document = "absent_from_document"  # asked for, carries no value
    model_flagged = "model_flagged"  # flagged without a stated reason


class RequirementStatus(str, Enum):
    """Requirement status (required/optional/satisfied/waived)."""

    required = "required"
    optional = "optional"
    satisfied = "satisfied"
    waived = "waived"


class RfpItemStatus(str, Enum):
    """Where one (carrier x product line) RFP item stands (S0-arch-11). Bridge-owned
    collection status (sense A), orthogonal to the app's ``RequirementStatus``.
    ``overdue`` is derived on read (an invited/acknowledged item past its due date)."""

    invited = "invited"
    acknowledged = "acknowledged"
    received = "received"
    incomplete = "incomplete"
    declined = "declined"
    overdue = "overdue"


class LifecycleStatus(str, Enum):
    """Exchange follow-up lifecycle (the SLA ladder). Orthogonal to per-doc
    Disposition and to the transport `terminal` flag: `escalated` is a durable,
    NON-terminal stall signal — only an app `tasks/cancel` terminates (DUR-6)."""

    on_track = "on_track"
    overdue = "overdue"
    reminder = "reminder"
    escalated = "escalated"
