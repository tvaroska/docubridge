"""RFP collection policy loading and due-date SLA ladder helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from bridge.negotiate import AuditEvent
from bridge.policy import disposition_policy_from_doc
from bridge.sla import (
    SlaPolicy,
    _load_policy_doc,
    _load_skill_frontmatter,
    skills_root,
    sla_policy_from_doc,
)
from contract.enums import LifecycleStatus, RequirementStatus, RfpItemStatus
from contract.wire import LedgerEntry

DEFAULT_ITEM_FIELD = "product_line"
RFP_PROCESS_ENV = "BRIDGE_RFP_PROCESS"

# Audit event kinds (the append-only trail).
KIND_RFP_OPENED = "rfp_opened"
KIND_INVITED = "invited"
KIND_ACKNOWLEDGED = "acknowledged"
KIND_DECLINED = "declined"
KIND_QUOTE_RECEIVED = "quote_received"
KIND_QUOTE_INCOMPLETE = "quote_incomplete"
KIND_ESCALATED = "escalated"
KIND_UNREQUESTED = "unrequested"
KIND_REQUIREMENT_CHANGED = "requirement_changed"
KIND_ATTACHMENT_UNREADABLE = "attachment_unreadable"
KIND_CHASED = "chased"

# The app statuses the Bridge chases; ``waived`` / ``satisfied`` are the agent saying stop.
_TRACKED = (RequirementStatus.required, RequirementStatus.optional)

_SEVERITY = {
    LifecycleStatus.on_track: 0,
    LifecycleStatus.overdue: 1,
    LifecycleStatus.reminder: 2,
    LifecycleStatus.escalated: 3,
}


class RfpLeg(BaseModel):
    """One carrier's isolated leg: its own context (``bridge.program.Leg``, serializable).

    ``chases`` counts the manual chases sent on this leg and ``last_chased_at`` stamps the
    latest (``record_chase``) — broker activity, kept apart from a cell's carrier-side
    ``last_activity``.
    """

    model_config = ConfigDict(extra="forbid")

    party: str
    context_id: str
    chases: int = 0
    last_chased_at: datetime | None = None


class RfpCell(BaseModel):
    """One (carrier, product line) item.

    ``status`` is the STORED base status — never ``overdue``, which is derived on read.
    ``requirement`` is the agent's chase knob. ``versions`` holds the doc ids of accepted
    quotes in arrival order (v0, v1, ...); ``attempts`` counts failed submissions (the
    resubmit cap). ``entry`` is the latest submission's ledger entry.
    """

    model_config = ConfigDict(extra="forbid")

    party: str
    product_line: str
    requirement: RequirementStatus = RequirementStatus.required
    status: RfpItemStatus = RfpItemStatus.invited
    versions: list[str] = []
    attempts: int = 0
    escalated: bool = False
    last_activity: datetime | None = None
    reason_code: str | None = None
    message: str | None = None
    entry: LedgerEntry | None = None

    @property
    def tracked(self) -> bool:
        """Return True if this RFP carrier/line cell is actively tracked."""
        return self.requirement in _TRACKED

    @property
    def outstanding(self) -> bool:
        """Still chased and not yet in: tracked and not received."""
        return self.tracked and self.status is not RfpItemStatus.received


class RfpCollection(BaseModel):
    """An RFP under collection: N isolated carrier legs, the item matrix, the ledger, the trail.

    ``entries`` is the append-only classified ledger (every submission, matched or not);
    ``unmatched`` names the doc ids no requested item claimed (flagged, never dropped).
    """

    model_config = ConfigDict(extra="forbid")

    rfp_id: str
    process: str
    context_id: str
    due_at: datetime
    opened_at: datetime
    legs: list[RfpLeg]
    cells: list[RfpCell]
    entries: list[LedgerEntry] = []
    unmatched: list[str] = []
    audit: list[AuditEvent] = []

    def cell(self, party: str, product_line: str) -> RfpCell:
        """Return the (party, product_line) item; raise ``KeyError`` if not requested."""
        return self.cells[self._cell_index(party, product_line)]

    def leg_for(self, party: str) -> RfpLeg:
        """Return ``party``'s leg; raise ``KeyError`` if the carrier was never invited."""
        for leg in self.legs:
            if leg.party == party:
                return leg
        raise KeyError(
            f"party {party!r} is not on the distribution list of RFP {self.rfp_id!r}"
        )

    def _cell_index(self, party: str, product_line: str) -> int:
        for idx, cell in enumerate(self.cells):
            if cell.party == party and cell.product_line == product_line:
                return idx
        raise KeyError(
            f"RFP {self.rfp_id!r} requests no {product_line!r} quote from {party!r}"
        )


@dataclass(frozen=True)
class RfpPolicy:
    """The RFP policy read from an emergent-Collect process skill (SKILL.md + policy.yaml).

    ``process`` is the skill it was loaded from; ``candidate_doctypes`` its label space
    (``metadata.bridge-candidate-doctypes``, never empty); ``item_field`` the extracted
    ``key_fields`` key that names an item (``rfp.item_field``, default ``product_line``).
    ``sla`` is the validated ``sla:`` ladder, ``tick`` the wall-clock length of one ladder
    tick (``rfp.tick_seconds``), ``max_resubmissions`` the ``disposition:`` resubmit cap.
    Keyword construction only — every field is required.
    """

    process: str
    candidate_doctypes: tuple[str, ...]
    item_field: str
    sla: SlaPolicy
    tick: timedelta
    max_resubmissions: int

    @property
    def doctype_hint(self) -> str | None:
        """The sole candidate doctype when the label space has exactly one, else ``None``."""
        if len(self.candidate_doctypes) == 1:
            return self.candidate_doctypes[0]
        return None


def load_rfp_policy(skill_name: str, *, skills_dir: Path | None = None) -> RfpPolicy:
    """Load the RFP policy from an emergent-Collect process skill on the filesystem."""
    frontmatter = _load_skill_frontmatter(skill_name, skills_dir=skills_dir)
    return rfp_policy_from_docs(
        skill_name,
        frontmatter,
        lambda: _load_policy_doc(skill_name, skills_dir=skills_dir),
        where=str(skills_root(skills_dir) / skill_name / "assets/policy.yaml"),
    )


def rfp_policy_from_docs(
    skill_name: str,
    frontmatter: dict[str, Any],
    load_policy_doc: Callable[[], dict[str, Any]],
    *,
    where: str | None = None,
) -> RfpPolicy:
    """Validate an emergent-Collect process skill's RFP policy from its parsed docs."""
    meta = (frontmatter or {}).get("metadata") or {}
    kind = meta.get("bridge-kind")
    if kind != "process":
        raise ValueError(
            f"skill {skill_name!r} is not a process skill (bridge-kind {kind!r})"
        )
    pattern = meta.get("bridge-pattern")
    if pattern != "collect":
        raise ValueError(
            f"skill {skill_name!r} is not an emergent-Collect process "
            f"(bridge-pattern {pattern!r}, expected 'collect')"
        )
    candidates = tuple(str(meta.get("bridge-candidate-doctypes") or "").split())
    if not candidates:
        raise ValueError(
            f"skill {skill_name!r} declares no bridge-candidate-doctypes (empty label space)"
        )
    doc = load_policy_doc()
    if not isinstance(doc, dict):
        raise ValueError(f"skill {skill_name!r} policy.yaml is not a mapping")
    rfp = doc.get("rfp")
    if not rfp:
        raise ValueError(f"skill {skill_name!r} policy.yaml missing an 'rfp:' block")
    if not isinstance(rfp, dict):
        raise ValueError(
            f"skill {skill_name!r} policy.yaml 'rfp:' block is not a mapping"
        )
    tick_seconds = rfp.get("tick_seconds")
    if (
        not isinstance(tick_seconds, int)
        or isinstance(tick_seconds, bool)
        or tick_seconds < 1
    ):
        raise ValueError(
            f"skill {skill_name!r} rfp.tick_seconds must be an int >= 1; got {tick_seconds!r}"
        )
    item_field = rfp.get("item_field", DEFAULT_ITEM_FIELD)
    if not isinstance(item_field, str) or not item_field:
        raise ValueError(
            f"skill {skill_name!r} rfp.item_field must be a non-empty str; got {item_field!r}"
        )
    return RfpPolicy(
        process=skill_name,
        candidate_doctypes=candidates,
        item_field=item_field,
        sla=sla_policy_from_doc(doc, where=where or f"{skill_name}/policy.yaml"),
        tick=timedelta(seconds=tick_seconds),
        max_resubmissions=disposition_policy_from_doc(
            skill_name, doc
        ).max_resubmissions,
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{name} must be timezone-aware; got naive {value.isoformat()}"
        )


def lifecycle_at(policy: RfpPolicy, due_at: datetime, now: datetime) -> LifecycleStatus:
    """The SLA ladder step at ``now``, anchored so ``overdue`` fires exactly at ``due_at``."""
    _require_aware(due_at, "due_at")
    _require_aware(now, "now")
    anchor = policy.sla.overdue_ticks
    current = LifecycleStatus.on_track
    for fire_tick, step in policy.sla.schedule():
        if now >= due_at + (fire_tick - anchor) * policy.tick:
            current = step
    return current


__all__ = [
    "DEFAULT_ITEM_FIELD",
    "KIND_ACKNOWLEDGED",
    "KIND_ATTACHMENT_UNREADABLE",
    "KIND_CHASED",
    "KIND_DECLINED",
    "KIND_ESCALATED",
    "KIND_INVITED",
    "KIND_QUOTE_INCOMPLETE",
    "KIND_QUOTE_RECEIVED",
    "KIND_REQUIREMENT_CHANGED",
    "KIND_RFP_OPENED",
    "KIND_UNREQUESTED",
    "RFP_PROCESS_ENV",
    "_SEVERITY",
    "_TRACKED",
    "RfpCell",
    "RfpCollection",
    "RfpLeg",
    "RfpPolicy",
    "_require_aware",
    "lifecycle_at",
    "load_rfp_policy",
    "rfp_policy_from_docs",
]
