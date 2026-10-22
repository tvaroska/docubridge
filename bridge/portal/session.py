"""Provider-Portal engine (P6-2) — a HOST over the P6-1 Path-B engines.

The portal is the external-zone A2UI Path-B host for humans. This module is the
pure-ish engine (no ``fastapi`` import): it (1) loads the disposition policy → builds a
``PathBState``; (2) drives ``bridge.pathb.step`` / ``resolve_hitl`` across parks,
persisting state in a ``PortalStore``; (3) projects the accumulated ledger + the active
park into an ``ExchangeTurn`` and calls the matching P6-1 ``render_*``.

**It hosts, it never re-decides (§2a).** The disposition gate, the Path-B graph, the
A2UI renderers, and the policy loader are done (P6-1). The engine mints no disposition
and authors no explanation — every ``reason_code``/``message`` is relayed verbatim from
the ``LedgerEntry``/``Requirement`` the graph produced (A2UI-1). "Computing outstanding"
or "writing a nicer message" would violate the relay invariant.

**Escalation is NOT rejection (§2a, gate #4).** The resubmit-cap path returns a
terminal ESCALATE (``disposition=pending`` / ``REASON_ESCALATED`` = route-to-human),
which rides into ``party_status`` as a non-accepted entry — never ``rejected``. Only a
wrong doctype (``Gate.unsupported``) is a reject. The engine must not "simplify"
ESCALATE into a reject.

Isolation: imports ``contract/`` + ``bridge.*`` + stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from bridge import pathb
from bridge.collect import project_collection_status
from bridge.disposition import DEFAULT_THRESHOLDS
from bridge.edge.a2ui import (
    A2uiView,
    render_hitl_review,
    render_party_status,
    render_upload_prompt,
)
from bridge.edge.skills import candidate_doctypes_for
from bridge.pathb import PathBAction, PathBState
from bridge.policy import load_disposition_policy
from bridge.portal.store import PortalSessionRecord, PortalStore
from contract.enums import Disposition
from contract.wire import ArtifactRef, ExchangeTurn


class NoActivePark(ValueError):
    """Raised when ``resolve`` is called without an active HITL/ESCALATE park
    (the host maps it to HTTP 409)."""


def start_session(
    store: PortalStore, *, process: str, context_id: str | None = None
) -> PortalSessionRecord:
    """Create a fresh portal session for ``process``.

    Loads the process skill's disposition policy (``max_resubmissions`` + gate
    thresholds); falls back to the gate defaults when the skill declares no
    ``disposition:`` block. Persists a record with no active slot / empty ledger.
    """
    try:
        policy = load_disposition_policy(process)
        max_resubmissions = policy.max_resubmissions
        thresholds = policy.thresholds()
    except (ValueError, KeyError, FileNotFoundError, OSError):
        # No disposition policy for this skill — use the gate's built-in defaults.
        max_resubmissions = PathBState.max_resubmissions
        thresholds = DEFAULT_THRESHOLDS

    rec = PortalSessionRecord(
        session_id=uuid4().hex,
        context_id=context_id or uuid4().hex,
        process=process,
        candidate_doctypes=tuple(candidate_doctypes_for(process)),
        max_resubmissions=max_resubmissions,
        thresholds=thresholds,
    )
    store.create(rec)
    return rec


async def upload(
    store: PortalStore,
    session_id: str,
    *,
    doc_id: str,
    artifact_name: str,
    artifact: ArtifactRef | None = None,
    extractor: Any = None,
) -> PortalSessionRecord:
    """Feed one uploaded document into the active slot's Path-B graph.

    If the active park was a RESUBMIT, thread the **persisted** ``PathBState`` so
    ``attempts`` increments across HTTP calls (this is what makes the cap → escalate
    path work durably, gate #4/#6). Otherwise start a fresh slot. On a terminal step
    (ACCEPT/REJECT/ESCALATE) append the entry to the ledger and clear the slot; on a
    non-terminal park (RESUBMIT/HITL) keep the threaded state + park.

    ``extractor`` is injectable for the seam-spy test; production leaves it ``None`` so
    ``pathb.step`` resolves the extraction seam lazily (fixture adapter locally).
    """
    rec = store.get(session_id)

    if rec.last_step is not None and rec.last_step.action is PathBAction.RESUBMIT:
        state = rec.active_state
    else:
        state = PathBState(
            candidate_doctypes=rec.candidate_doctypes,
            max_resubmissions=rec.max_resubmissions,
            thresholds=rec.thresholds,
        )

    step_result = await pathb.step(
        state,
        doc_id=doc_id,
        artifact_name=artifact_name,
        artifact=artifact,
        extractor=extractor,
    )

    if step_result.terminal:
        entry = step_result.entry
        if step_result.action is PathBAction.ESCALATE:
            # The graph's escalate entry keeps the raw quality-gate verdict
            # (``illegible``/"resubmit a clearer copy"), but the human-facing relay for
            # an escalation is the step's ``REASON_ESCALATED`` + escalate message
            # (route-to-human, not "resubmit"). Both come from the graph (``pathb``) —
            # this is still a verbatim relay (§2a/§2e), not host-authored prose. Without
            # it party-status would wrongly tell the party to resubmit (gate #4).
            entry = entry.model_copy(
                update={
                    "reason_code": step_result.reason_code,
                    "message": step_result.message,
                }
            )
        rec.ledger = (*rec.ledger, entry)
        rec.active_state = None
        rec.last_step = None
    else:
        rec.active_state = step_result.state
        rec.last_step = step_result

    store.put(rec)
    return rec


def resolve(
    store: PortalStore,
    session_id: str,
    *,
    resolution: Disposition,
    message: str,
) -> PortalSessionRecord:
    """Finalize the active HITL/ESCALATE park with the reviewer's resolution.

    Appends the resolved entry (carrying the reviewer's ``message``) to the ledger and
    clears the active slot. Raises :class:`NoActivePark` (→ 409) when there is no
    HITL/ESCALATE park to resolve.
    """
    rec = store.get(session_id)
    last = rec.last_step
    if last is None or last.action not in (PathBAction.HITL, PathBAction.ESCALATE):
        raise NoActivePark("no HITL/ESCALATE park to resolve for this session")

    resolved = pathb.resolve_hitl(last, resolution, message=message)
    rec.ledger = (*rec.ledger, resolved.entry)
    rec.active_state = None
    rec.last_step = None
    store.put(rec)
    return rec


def _turn(rec: PortalSessionRecord) -> ExchangeTurn:
    """Build the ``ExchangeTurn`` the P6-1 renderers consume.

    ``entries`` = the accumulated ledger + (if a non-terminal park is active) the
    parked entry, so the parked entry's ``reason_code``/``message`` reach the view (a
    resubmit park's actionable "blurry, resend a clear copy" rides into ``outstanding``
    via ``project_collection_status``). ``terminal`` is ``True`` only when no
    non-terminal park is active (§ gotcha 10).
    """
    entries = list(rec.ledger)
    if rec.last_step is not None:
        entries.append(rec.last_step.entry)
    status = project_collection_status(entries)
    return ExchangeTurn(
        context_id=rec.context_id,
        status=status,
        terminal=rec.last_step is None,
    )


def current_view(rec: PortalSessionRecord) -> A2uiView:
    """Choose + render the current A2UI view from the session's active park (§2c).

    | state | view |
    |---|---|
    | start (no park, empty ledger) / RESUBMIT park | ``upload_prompt`` |
    | HITL park | ``hitl_review`` |
    | terminal (ACCEPT/REJECT/ESCALATE, park cleared) | ``party_status`` |
    """
    turn = _turn(rec)
    last = rec.last_step

    if last is None:
        if not rec.ledger:
            return render_upload_prompt(turn, accept=list(rec.candidate_doctypes))
        return render_party_status(turn)

    if last.action is PathBAction.RESUBMIT:
        return render_upload_prompt(turn, accept=list(rec.candidate_doctypes))

    if last.action is PathBAction.HITL:
        return render_hitl_review(turn, entry=last.entry)

    # Defensive: terminal parks are never stored as last_step.
    return render_party_status(turn)
