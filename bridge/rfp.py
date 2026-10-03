"""RFP collection process (S0-arch-11) — "which quotes are ready, which are waiting on a carrier".

An emergent-Collect process skill (e.g. ``benefits-rfp``) as ONE cohesive Bridge-side
aggregate, an **emergent Collect** over a carrier distribution list x product lines. The
process name, label space (``bridge-candidate-doctypes``) and item key
(``rfp.item_field``) come from the process skill via ``RfpPolicy`` (``load_rfp_policy``);
the core names no skill (S0-arch-15) — a new emergent-Collect industry is a new skill:

* **One request, N isolated legs.** The broker's agent sends ONE ``CollectRequest`` whose
  ``requirements`` name each (carrier, product line) item (``Requirement.party`` = carrier,
  ``Requirement.item`` = product line) and whose ``due_at`` is the response deadline.
  ``open_rfp`` fans out one leg per carrier through ``bridge.program.fan_out`` — every
  carrier gets its own context, competitors never share one (the program isolation
  invariant).
* **Per-item collection status (sense A, Bridge-owned).** Each item moves
  invited → acknowledged → received / incomplete / declined (``contract.enums.RfpItemStatus``).
  An arriving quote is filed by ``receive`` against its item: ``accepted`` → received (a later
  accepted quote is a new version on the same item — the ``QuoteVersion`` idiom of
  ``bridge.negotiate``); anything else → incomplete with the gate's ``reason_code`` /
  ``message`` relayed verbatim. Failed submissions past the disposition policy's
  ``max_resubmissions`` escalate — the same ``attempts > cap`` boundary as ``bridge.pathb``,
  with the escalate vocabulary sourced from it so the wording can't drift.
* **The due date drives the SLA ladder.** ``lifecycle_at`` anchors the process skill's
  ``sla:`` ladder on ``due_at``: overdue fires AT the due date, each later step at
  ``due_at + (step_ticks - overdue_ticks) * tick`` (``tick`` = the ``rfp.tick_seconds`` policy
  knob). The lifecycle is **derived on read** — a pure function of ``now`` — so the status
  view needs no alarm; an invited/acknowledged item past due reads as ``overdue``. A carrier
  whose every tracked item is received/declined stops laddering (``on_track``).
* **Requirements stay the agent's (sense B).** ``update_requirements`` applies the agent's
  next requirements list — new carriers / product lines join (a new carrier gets a new
  isolated leg), a ``waived`` item stops being chased. The Bridge never drops an item on its
  own, and never mints ``done``: ``to_exchange_turn`` proposes the outstanding delta
  (advisory), ``RequirementsList.done`` is always False here.
* **Unreadable attachments stay visible, never verdicted.** ``note_unreadable`` appends an
  audit-only ``attachment_unreadable`` event (an emailed rate sheet in a format no engine
  reads, a deterministic engine refusal — S0-arch-13's email intake). No cell moves: the
  disposition gate is the sole verdict authority, so nothing is fabricated for it.
* **A manual chase is audit-only (S0-frontend-1).** ``chaseable_items`` names what a
  carrier still owes (tracked, outstanding, not declined — a declined line is the agent's
  to waive or replace, never chased); ``record_chase`` bumps the leg's ``chases`` /
  ``last_chased_at`` and appends one ``chased`` event — no cell, status, ``last_activity``
  (that is *carrier* activity), entry or attempt moves. ``chase_message`` is the
  Bridge-authored chase copy naming only that carrier and its own lines. The send itself
  is the host's (``bridge.outbound.Nudge`` over the ``a2a_client`` seam); the chase is
  operator-triggered, not ladder-armed.

Two read projections: ``rfp_status`` (the per-carrier x product matrix the dashboard and
the consumer's tracker render) and ``to_exchange_turn`` (the same answer on the A2A wire:
``status.outstanding`` lists each not-yet-received item with its ``RfpItemStatus`` as the
``reason_code``, and ``lifecycle`` is the worst outstanding carrier's ladder step).

Pure: imports stdlib + pydantic + ``contract`` + ``bridge.program`` / ``bridge.sla`` /
``bridge.policy`` + the escalate vocabulary (``bridge.offscript.REASON_ESCALATED`` /
``bridge.pathb.ESCALATE_MESSAGE``) + ``bridge.negotiate.AuditEvent`` only — NO ``a2a``, NO
``google.adk``, NEVER ``agents/``. Every function returns NEW immutable state (the
``bridge.negotiate`` replace-and-return idiom).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bridge.negotiate import AuditEvent
from bridge.offscript import REASON_ESCALATED
from bridge.pathb import ESCALATE_MESSAGE
from bridge.policy import disposition_policy_from_doc
from bridge.program import Leg, Program, add_leg, fan_out
from bridge.sla import (
    SlaPolicy,
    _load_policy_doc,
    _load_skill_frontmatter,
    skills_root,
    sla_policy_from_doc,
)
from contract.enums import (
    Disposition,
    Gate,
    LifecycleStatus,
    RequirementStatus,
    RfpItemStatus,
)
from contract.extraction import FieldReview
from contract.wire import (
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
)

# The ``rfp.item_field`` default: the extracted ``key_fields`` key naming an item.
DEFAULT_ITEM_FIELD = "product_line"

# The env naming the emergent-Collect process skill a deployed RFP board host / A2A RFP
# opener runs (S0-arch-17/19) — one process per board; the core names no skill.
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

# Ladder severity, for "the worst outstanding carrier".
_SEVERITY = {
    LifecycleStatus.on_track: 0,
    LifecycleStatus.overdue: 1,
    LifecycleStatus.reminder: 2,
    LifecycleStatus.escalated: 3,
}


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
    """Load the RFP policy from an emergent-Collect process skill on the filesystem.

    From SKILL.md frontmatter (``bridge.sla._load_skill_frontmatter`` — pure, no
    ``bridge.edge`` import) and the declared policy.yaml (``bridge.sla._load_policy_doc``),
    validated by the shared pure parser ``rfp_policy_from_docs``. This is the build-time
    loader for a host's home process; the live registry reader
    (``bridge.edge.skills.rfp_policy_for``) feeds the same parser through the
    ``skill_registry`` seam.
    """
    frontmatter = _load_skill_frontmatter(skill_name, skills_dir=skills_dir)
    return rfp_policy_from_docs(
        skill_name,
        frontmatter,
        lambda: _load_policy_doc(skill_name, skills_dir=skills_dir),
        where=str(skills_root(skills_dir) / skill_name / "assets/policy.yaml"),
    )


def rfp_policy_from_docs(
    skill_name: str,
    frontmatter: dict,
    load_policy_doc: Callable[[], dict],
    *,
    where: str | None = None,
) -> RfpPolicy:
    """Validate an emergent-Collect process skill's RFP policy from its parsed docs.

    Pure: ``frontmatter`` is the SKILL.md mapping; ``load_policy_doc`` returns the declared
    policy.yaml doc and is called at most ONCE, and only after the frontmatter checks pass
    (so a non-collect skill with no policy file still fails on kind / pattern). From
    frontmatter: ``bridge-kind`` must be ``process``, ``bridge-pattern`` ``collect`` and
    ``bridge-candidate-doctypes`` (space-separated) non-empty. From policy.yaml: the
    ``rfp:`` block's ``tick_seconds`` and optional ``item_field`` (default
    ``product_line``), the ``sla:`` ladder (``bridge.sla.sla_policy_from_doc``) and the
    ``disposition:`` cap (``bridge.policy.disposition_policy_from_doc``), all from the
    same doc. Raises ``ValueError`` naming the skill, checked in order: kind → pattern →
    candidates → ``rfp:`` block → tick → item_field → sla → disposition. ``where`` names
    the policy source in the ``sla:`` error messages.
    """
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
        raise ValueError(f"skill {skill_name!r} policy.yaml 'rfp:' block is not a mapping")
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
    """The SLA ladder step at ``now``, anchored so ``overdue`` fires exactly at ``due_at``.

    Each step fires at ``due_at + (step_ticks - overdue_ticks) * tick``. Before the due date
    the exchange is ``on_track``. Pure — derived on read, no alarm.
    """
    _require_aware(due_at, "due_at")
    _require_aware(now, "now")
    anchor = policy.sla.overdue_ticks
    current = LifecycleStatus.on_track
    for fire_tick, step in policy.sla.schedule():
        if now >= due_at + (fire_tick - anchor) * policy.tick:
            current = step
    return current


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


def _append(
    audit: list[AuditEvent],
    *,
    kind: str,
    party: str,
    detail: str,
    version: int | None = None,
) -> list[AuditEvent]:
    """Return a NEW audit list with one event appended (seq = current length, monotonic)."""
    return [
        *audit,
        AuditEvent(
            seq=len(audit), kind=kind, party=party, version=version, detail=detail
        ),
    ]


def _replace_cell(cells: list[RfpCell], idx: int, cell: RfpCell) -> list[RfpCell]:
    return [*cells[:idx], cell, *cells[idx + 1 :]]


def _validated_items(requirements: RequirementsList) -> list[Requirement]:
    seen: set[tuple[str, str]] = set()
    for req in requirements.requirements:
        if not req.party:
            raise ValueError(
                f"RFP requirement {req.item!r} names no party (every item awaits a carrier)"
            )
        key = (req.party, req.item)
        if key in seen:
            raise ValueError(
                f"duplicate RFP requirement {req.item!r} for {req.party!r}"
            )
        seen.add(key)
    return list(requirements.requirements)


def open_rfp(
    request: CollectRequest, *, now: datetime, rfp_id: str | None = None
) -> RfpCollection:
    """Open an RFP from ONE ``CollectRequest``: one isolated leg per carrier, one item per
    (carrier, product line), every item ``invited``.

    Requires ``request.requirements`` (each naming a ``party``) and a timezone-aware
    ``request.due_at``. Legs come from ``bridge.program.fan_out`` (distinct context per
    carrier). ``context_id`` is the broker-side exchange context (``request.context_id`` or
    minted). Pure — returns a new ``RfpCollection``.
    """
    if request.requirements is None or not request.requirements.requirements:
        raise ValueError(
            "an RFP CollectRequest must carry requirements (carriers x product lines)"
        )
    if request.due_at is None:
        raise ValueError("an RFP CollectRequest must carry a due_at")
    _require_aware(request.due_at, "due_at")
    _require_aware(now, "now")
    items = _validated_items(request.requirements)

    rfp_id = rfp_id or uuid.uuid4().hex
    parties = list(dict.fromkeys(req.party for req in items if req.party))
    program = fan_out(rfp_id, parties)
    audit = _append(
        [], kind=KIND_RFP_OPENED, party="", detail=f"due {request.due_at.isoformat()}"
    )
    for party in parties:
        lines = [r.item for r in items if r.party == party]
        audit = _append(audit, kind=KIND_INVITED, party=party, detail=", ".join(lines))
    return RfpCollection(
        rfp_id=rfp_id,
        process=request.process,
        context_id=request.context_id or uuid.uuid4().hex,
        due_at=request.due_at,
        opened_at=now,
        legs=[
            RfpLeg(party=leg.party, context_id=leg.context_id) for leg in program.legs
        ],
        cells=[
            RfpCell(party=r.party or "", product_line=r.item, requirement=r.status)
            for r in items
        ],
        audit=audit,
    )


def update_requirements(
    rfp: RfpCollection, requirements: RequirementsList, *, now: datetime
) -> RfpCollection:
    """Apply the agent's next requirements list (the emergent mutation, sense B).

    A new (carrier, product line) joins as ``invited`` — a new carrier gets a new isolated leg
    (``bridge.program.add_leg``); an existing item takes the agent's new status (``waived``
    stops the chase). Items absent from the list are left as they are: the Bridge never drops
    an item on its own. Pure.
    """
    items = _validated_items(requirements)
    program = Program(
        program_id=rfp.rfp_id,
        legs=tuple(Leg(party=leg.party, context_id=leg.context_id) for leg in rfp.legs),
    )
    cells = list(rfp.cells)
    audit = rfp.audit
    for req in items:
        party = req.party or ""
        try:
            idx = rfp._cell_index(party, req.item)
        except KeyError:
            if party not in program.parties():
                program = add_leg(program, party)
            cells.append(
                RfpCell(
                    party=party,
                    product_line=req.item,
                    requirement=req.status,
                    last_activity=now,
                )
            )
            audit = _append(audit, kind=KIND_INVITED, party=party, detail=req.item)
            continue
        if cells[idx].requirement is not req.status:
            cells = _replace_cell(
                cells, idx, cells[idx].model_copy(update={"requirement": req.status})
            )
            audit = _append(
                audit,
                kind=KIND_REQUIREMENT_CHANGED,
                party=party,
                detail=f"{req.item}: {req.status.value}",
            )
    # Keep each existing leg object (its chase state rides on it); only a carrier new to
    # the distribution list gets a fresh leg.
    existing = {leg.party: leg for leg in rfp.legs}
    return rfp.model_copy(
        update={
            "legs": [
                existing.get(leg.party)
                or RfpLeg(party=leg.party, context_id=leg.context_id)
                for leg in program.legs
            ],
            "cells": cells,
            "audit": audit,
        }
    )


def acknowledge(rfp: RfpCollection, party: str, *, now: datetime) -> RfpCollection:
    """The carrier acknowledged the invitation: its ``invited`` items become ``acknowledged``.

    Acknowledging does not stop the ladder — an acknowledged item still goes overdue. Raises
    ``KeyError`` for a carrier not on the distribution list. Pure.
    """
    rfp.leg_for(party)
    cells = [
        c.model_copy(
            update={"status": RfpItemStatus.acknowledged, "last_activity": now}
        )
        if c.party == party and c.status is RfpItemStatus.invited
        else c
        for c in rfp.cells
    ]
    audit = _append(
        rfp.audit, kind=KIND_ACKNOWLEDGED, party=party, detail="acknowledged"
    )
    return rfp.model_copy(update={"cells": cells, "audit": audit})


def decline(
    rfp: RfpCollection,
    party: str,
    *,
    now: datetime,
    product_line: str | None = None,
    message: str | None = None,
) -> RfpCollection:
    """The carrier declined — one product line, or (``product_line=None``) every item not yet
    received.

    A declined item stops the ladder but stays outstanding: whether to waive it or invite a
    replacement carrier is the agent's call. ``message`` (the carrier's own words) is kept
    verbatim. Raises ``KeyError`` for an unknown carrier/item, ``ValueError`` when declining
    an item already received. Pure.
    """
    rfp.leg_for(party)
    if (
        product_line is not None
        and rfp.cell(party, product_line).status is RfpItemStatus.received
    ):
        raise ValueError(
            f"{party!r} already supplied an accepted {product_line!r} quote"
        )
    update = {
        "status": RfpItemStatus.declined,
        "last_activity": now,
        "reason_code": RfpItemStatus.declined.value,
        "message": message,
    }
    cells = [
        c.model_copy(update=update)
        if c.party == party
        and (
            c.product_line == product_line
            if product_line is not None
            else c.status is not RfpItemStatus.received
        )
        else c
        for c in rfp.cells
    ]
    audit = _append(
        rfp.audit, kind=KIND_DECLINED, party=party, detail=product_line or "all"
    )
    return rfp.model_copy(update={"cells": cells, "audit": audit})


def _counts_as_attempt(entry: LedgerEntry) -> bool:
    """A failed submission the carrier must redo (the resubmit cap counts these). A HITL
    park is the Bridge's review, not the carrier's failure — it does not count (the
    ``bridge.pathb`` posture)."""
    return entry.disposition is Disposition.rejected or entry.gate is Gate.resubmit


def receive(
    rfp: RfpCollection,
    party: str,
    entry: LedgerEntry,
    *,
    now: datetime,
    policy: RfpPolicy,
    product_line: str | None = None,
) -> RfpCollection:
    """File one dispositioned submission from ``party`` against its item.

    The item is ``product_line`` when the caller knows it (a portal upload into a specific
    row), else the submission's own ``key_fields[policy.item_field]`` (the process skill's
    item key, ``product_line`` by default). ``accepted`` → ``received``
    (a further accepted quote appends a version); anything else → ``incomplete`` with the
    gate's ``reason_code`` / ``message`` verbatim, counting an attempt when the carrier must
    resubmit. Past ``policy.max_resubmissions`` attempts the item escalates (route to human,
    never reject). A submission no requested item claims joins ``unmatched`` (flagged, never
    dropped). Raises ``KeyError`` for a carrier not on the distribution list (isolation). Pure.
    """
    rfp.leg_for(party)
    entries = [*rfp.entries, entry]
    line = product_line or entry.key_fields.get(policy.item_field)
    try:
        idx = rfp._cell_index(party, line or "")
    except KeyError:
        audit = _append(
            rfp.audit,
            kind=KIND_UNREQUESTED,
            party=party,
            detail=f"{entry.doc_id}: no requested item for product line {line!r}",
        )
        return rfp.model_copy(
            update={
                "entries": entries,
                "unmatched": [*rfp.unmatched, entry.doc_id],
                "audit": audit,
            }
        )

    cell = rfp.cells[idx]
    if entry.disposition is Disposition.accepted:
        version = len(cell.versions)
        new_cell = cell.model_copy(
            update={
                "status": RfpItemStatus.received,
                "versions": [*cell.versions, entry.doc_id],
                "escalated": False,
                "last_activity": now,
                "reason_code": entry.reason_code,
                "message": entry.message,
                "entry": entry,
            }
        )
        audit = _append(
            rfp.audit,
            kind=KIND_QUOTE_RECEIVED,
            party=party,
            version=version,
            detail=f"{cell.product_line}: {entry.doc_id}",
        )
    else:
        attempts = cell.attempts + (1 if _counts_as_attempt(entry) else 0)
        escalated = attempts > policy.max_resubmissions
        new_cell = cell.model_copy(
            update={
                "status": RfpItemStatus.incomplete,
                "attempts": attempts,
                "escalated": escalated,
                "last_activity": now,
                "reason_code": REASON_ESCALATED if escalated else entry.reason_code,
                "message": ESCALATE_MESSAGE if escalated else entry.message,
                "entry": entry,
            }
        )
        audit = _append(
            rfp.audit,
            kind=KIND_QUOTE_INCOMPLETE,
            party=party,
            detail=f"{cell.product_line}: {entry.doc_id} ({entry.reason_code})",
        )
        if escalated and not cell.escalated:
            audit = _append(
                audit,
                kind=KIND_ESCALATED,
                party=party,
                detail=f"{cell.product_line}: attempts {attempts} > cap {policy.max_resubmissions}",
            )
    return rfp.model_copy(
        update={
            "cells": _replace_cell(rfp.cells, idx, new_cell),
            "entries": entries,
            "audit": audit,
        }
    )


def note_unreadable(
    rfp: RfpCollection, party: str, *, filename: str, reason: str, now: datetime
) -> RfpCollection:
    """Record that ``party`` sent an attachment nothing could read (S0-arch-13).

    Audit-only: one ``attachment_unreadable`` event naming the file and why (an
    unsupported media type, a deterministic engine refusal). No cell, status, ledger
    entry or attempt changes — the disposition gate is the sole verdict authority, so
    an unreadable file never gets a fabricated one; it stays visible on the trail for
    the broker. Raises ``KeyError`` for a carrier not on the distribution list. Pure.
    """
    _require_aware(now, "now")
    rfp.leg_for(party)
    audit = _append(
        rfp.audit,
        kind=KIND_ATTACHMENT_UNREADABLE,
        party=party,
        detail=f"{filename}: {reason}",
    )
    return rfp.model_copy(update={"audit": audit})


def chaseable_items(rfp: RfpCollection, party: str) -> list[RfpCell]:
    """``party``'s items a chase may name: tracked, outstanding and not ``declined``.

    A declined line stops the ladder and is the agent's to waive or replace — never chased
    (the same rule ``_carrier_lifecycle`` uses). Raises ``KeyError`` for a carrier not on
    the distribution list. Pure.
    """
    rfp.leg_for(party)
    return [
        c
        for c in rfp.cells
        if c.party == party and c.outstanding and c.status is not RfpItemStatus.declined
    ]


def record_chase(
    rfp: RfpCollection,
    party: str,
    *,
    now: datetime,
    step: LifecycleStatus,
    channel: str,
) -> RfpCollection:
    """Record that a chase was sent to ``party`` (S0-frontend-1, the manual "chase now").

    Audit-only on the matrix: the leg's ``chases`` increments and ``last_chased_at`` = ``now``,
    and one ``chased`` event names the ladder step, the channel and the lines chased. No
    cell, status, ``last_activity`` (carrier activity, not broker), entry or attempt
    changes. Raises ``KeyError`` for an unknown carrier, ``ValueError`` for a naive ``now``.
    Pure.
    """
    _require_aware(now, "now")
    leg = rfp.leg_for(party)
    lines = [c.product_line for c in chaseable_items(rfp, party)]
    chased = leg.model_copy(update={"chases": leg.chases + 1, "last_chased_at": now})
    audit = _append(
        rfp.audit,
        kind=KIND_CHASED,
        party=party,
        detail=f"{step.value} via {channel}: {', '.join(lines)}",
    )
    return rfp.model_copy(
        update={
            "legs": [chased if lg.party == party else lg for lg in rfp.legs],
            "audit": audit,
        }
    )


def chase_message(
    rfp: RfpCollection,
    party: str,
    *,
    lines: list[str],
    reply_address: str | None = None,
    reply_subject_tag: str | None = None,
) -> str:
    """The Bridge-authored chase copy for ``party`` (outbound copy, not a relay).

    Names only ``party`` and its own ``lines`` (carrier-vs-carrier confidentiality is
    addressing), plus the leg's reply address / subject tag when email intake is enabled.
    """
    text = (
        f"Reminder for {party}: RFP {rfp.rfp_id} is still waiting on your "
        f"{', '.join(lines)} quote(s), due {rfp.due_at.isoformat()}."
    )
    if reply_address:
        text += f" Reply to {reply_address}"
        if reply_subject_tag:
            text += f" with subject tag {reply_subject_tag}"
        text += "."
    return text


# --------------------------------------------------------------------------- #
# Read projections                                                            #
# --------------------------------------------------------------------------- #


class RfpCellView(BaseModel):
    """One item as a reader sees it: the EFFECTIVE status (``overdue`` derived)."""

    model_config = ConfigDict(extra="forbid")

    party: str
    product_line: str
    requirement: RequirementStatus
    status: RfpItemStatus
    versions: list[str]
    attempts: int
    escalated: bool
    last_activity: datetime | None
    reason_code: str | None
    message: str | None
    field_reviews: list[FieldReview] = []


class RfpCarrierView(BaseModel):
    """One carrier: its leg context and ladder step (``on_track`` once nothing is outstanding),
    plus the manual-chase count and the latest chase time."""

    model_config = ConfigDict(extra="forbid")

    party: str
    context_id: str
    lifecycle: LifecycleStatus
    ready: int
    waiting: int
    chases: int = 0
    last_chased_at: datetime | None = None


class RfpStatusView(BaseModel):
    """The whole answer: per-carrier x product matrix, counts, and the overall ladder step.

    ``ready`` / ``waiting`` count TRACKED items only (a waived item is neither).
    ``counts`` is keyed by effective ``RfpItemStatus`` over tracked items.
    """

    model_config = ConfigDict(extra="forbid")

    rfp_id: str
    process: str
    context_id: str
    due_at: datetime
    as_of: datetime
    lifecycle: LifecycleStatus
    ready: int
    waiting: int
    counts: dict[RfpItemStatus, int]
    carriers: list[RfpCarrierView]
    cells: list[RfpCellView]
    unmatched: list[str]


def _effective_status(cell: RfpCell, ladder: LifecycleStatus) -> RfpItemStatus:
    """``overdue`` is derived: an invited/acknowledged item once the ladder has left on_track."""
    waiting = cell.status in (RfpItemStatus.invited, RfpItemStatus.acknowledged)
    if waiting and ladder is not LifecycleStatus.on_track:
        return RfpItemStatus.overdue
    return cell.status


def _carrier_lifecycle(
    cells: list[RfpCell], ladder: LifecycleStatus
) -> LifecycleStatus:
    """A carrier ladders only while it owes a response: any escalated item → escalated; an
    item still invited/acknowledged/incomplete → the due-date ladder; otherwise on_track
    (everything tracked is received or declined — nothing left to chase)."""
    if any(c.escalated for c in cells if c.outstanding):
        return LifecycleStatus.escalated
    if any(c.outstanding and c.status is not RfpItemStatus.declined for c in cells):
        return ladder
    return LifecycleStatus.on_track


def rfp_status(
    rfp: RfpCollection, *, now: datetime, policy: RfpPolicy
) -> RfpStatusView:
    """Project the RFP at ``now``: "which quotes are ready, which are waiting on a carrier"."""
    ladder = lifecycle_at(policy, rfp.due_at, now)
    cell_views = [
        RfpCellView(
            party=c.party,
            product_line=c.product_line,
            requirement=c.requirement,
            status=_effective_status(c, ladder),
            versions=c.versions,
            attempts=c.attempts,
            escalated=c.escalated,
            last_activity=c.last_activity,
            reason_code=c.reason_code,
            message=c.message,
            field_reviews=c.entry.field_reviews if c.entry else [],
        )
        for c in rfp.cells
    ]
    carriers = []
    for leg in rfp.legs:
        own = [c for c in rfp.cells if c.party == leg.party]
        carriers.append(
            RfpCarrierView(
                party=leg.party,
                context_id=leg.context_id,
                lifecycle=_carrier_lifecycle(own, ladder),
                ready=sum(1 for c in own if c.tracked and not c.outstanding),
                waiting=sum(1 for c in own if c.outstanding),
                chases=leg.chases,
                last_chased_at=leg.last_chased_at,
            )
        )
    counts: dict[RfpItemStatus, int] = {}
    for view, cell in zip(cell_views, rfp.cells, strict=True):
        if cell.tracked:
            counts[view.status] = counts.get(view.status, 0) + 1
    overall = max(
        (c.lifecycle for c in carriers),
        key=_SEVERITY.__getitem__,
        default=LifecycleStatus.on_track,
    )
    return RfpStatusView(
        rfp_id=rfp.rfp_id,
        process=rfp.process,
        context_id=rfp.context_id,
        due_at=rfp.due_at,
        as_of=now,
        lifecycle=overall,
        ready=sum(1 for c in rfp.cells if c.tracked and not c.outstanding),
        waiting=sum(1 for c in rfp.cells if c.outstanding),
        counts=counts,
        carriers=carriers,
        cells=cell_views,
        unmatched=rfp.unmatched,
    )


def to_exchange_turn(
    rfp: RfpCollection, *, now: datetime, policy: RfpPolicy
) -> ExchangeTurn:
    """The same answer on the A2A wire (the broker-side exchange's per-turn deliverable).

    ``status.entries`` is the classified ledger; ``status.outstanding`` proposes each
    not-yet-received tracked item as a ``Requirement`` (``party`` = carrier, ``item`` =
    product line) whose ``reason_code`` is its effective ``RfpItemStatus`` — declined items
    included, so the agent can waive or replace them. ``done`` is always False (the Bridge
    never mints completeness); ``requirements`` stays None (app-owned); ``terminal`` is False
    (the exchange waits on carriers); ``lifecycle`` is the worst outstanding carrier's step.
    """
    view = rfp_status(rfp, now=now, policy=policy)
    outstanding = [
        Requirement(
            item=cv.product_line,
            party=cv.party,
            status=cv.requirement,
            doctype_hint=policy.doctype_hint,
            reason_code=cv.status.value,
            message=cv.message,
        )
        for cv, cell in zip(view.cells, rfp.cells, strict=True)
        if cell.outstanding
    ]
    return ExchangeTurn(
        context_id=rfp.context_id,
        status=CollectionStatus(
            entries=rfp.entries,
            outstanding=RequirementsList(requirements=outstanding, done=False),
        ),
        requirements=None,
        terminal=False,
        lifecycle=view.lifecycle,
    )
