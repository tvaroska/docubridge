"""Pure session-key, event-to-ExchangeTurn projection, and HITL park/resume readers for the LLM leg."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    import google.genai.types as genai_types

from google.adk.artifacts import BaseArtifactService
from google.adk.events import Event
from pydantic import ValidationError

from bridge import gatepin
from bridge.collect import project_collection_status
from bridge.disposition import DispositionVerdict
from bridge.edge import artifacts
from bridge.offscript import (
    REASON_DELETED,
    REASON_ESCALATED,
    REASON_HUMAN_REVIEW,
    REASON_NOTHING_TO_DELETE,
    REASON_OFF_SCRIPT_REJECT,
)
from bridge.signals import dispose_signals
from contract.enums import Disposition, Gate, LifecycleStatus
from contract.wire import ExchangeTurn, LedgerEntry

# The ADK app_name of the LLM-leg session — the artifact store's app_name. Must equal
# ``bridge.agent.build_app().name`` (enforced by test; the core never imports the edge).
APP_NAME = artifacts.APP_NAME

GATE_TOOL = gatepin.GATE_TOOL_NAME
REVIEW_TOOL = "request_human_review"
DELETE_TOOL = "handle_deletion_request"
REJECT_TOOL = "reject_submission"
ESCALATE_TOOL = "escalate_submission"
PROJECTED_TOOLS = frozenset(
    {GATE_TOOL, REVIEW_TOOL, DELETE_TOOL, REJECT_TOOL, ESCALATE_TOOL}
)
# The AgentTool's response carries extraction signals only — NEVER projected (D4).
EXTRACTION_TOOL = "extraction_agent"

# Bridge-authored status text for a paused request_human_review call (no tool
# response exists in that shape; model text is never a substitute).
PAUSED_REVIEW_MESSAGE = "This submission was sent for human review."

# D6 status precedence: pending review > escalated > the last of {deleted, rejected}.
_RANK_REVIEW = 4
_RANK_ESCALATE = 3
_RANK_OFFSCRIPT = 2

_USER_AUTHOR = "user"

# Session-state key holding, per parked A2A task, the Collector's ORIGINAL entries of
# the handed-off submission: ``{a2a_task_id: [LedgerEntry json dump, ...]}``. Written
# as a new top-level value per park (never mutated in place); persisted (no
# ``temp:``/``app:``/``user:``/``_adk`` prefix) because the resume comes later,
# possibly on another process.
REVIEW_TASKS_KEY = "bridge:review_tasks"
# The reviewer resolutions a resume may carry (mirrors ``HumanReviewResolution``).
_RESOLUTIONS = {"accepted": Disposition.accepted, "rejected": Disposition.rejected}


@dataclass(frozen=True)
class SessionKey:
    """The LLM-leg ADK session key — identical to the artifact store's key (D9)."""

    app_name: str
    user_id: str
    session_id: str


def resolve_party(
    store: BaseArtifactService, *, party: str | None, context_id: str
) -> str:
    """Resolve the session ``user_id`` for a context (D9).

    1. An explicit ``party`` (the inbound ``CollectRequest.party``) wins.
    2. Else exactly one party owning artifacts under ``context_id`` in the shared
       store (:func:`bridge.edge.artifacts.owning_parties`) → that party.
    3. Zero or more than one → :data:`bridge.edge.artifacts.UNSCOPED_USER`. Fail
       closed: never guess among parties (a context is per-leg per-party, so more
       than one is anomalous; under the unscoped user nothing foreign can be erased).
    """
    if party:
        return party
    owners = artifacts.owning_parties(store, context_id)
    if len(owners) == 1:
        return next(iter(owners))
    return artifacts.UNSCOPED_USER


def session_key(
    store: BaseArtifactService, *, party: str | None, context_id: str
) -> SessionKey:
    """Build the D9 session key ``(document_bridge, party, context_id)``.

    Raises ``ValueError`` on an empty ``context_id`` (the A2A context id is always
    present; an empty one would collapse every leg onto one session).
    """
    if not context_id:
        raise ValueError("context_id is required for the LLM-leg session key")
    return SessionKey(
        APP_NAME, resolve_party(store, party=party, context_id=context_id), context_id
    )


@dataclass(frozen=True)
class LlmProjection:
    """One LLM-leg invocation projected onto the wire (D6).

    ``reason_code`` / ``status_text`` are the precedence winner's (a tool's own
    reason code + Bridge-authored message); both ``None`` when no off-script/review
    tool ran (the caller then uses ``summarize_turn``, or the unrouted text).
    ``review_call_ids`` are the pending ``request_human_review`` call ids.
    """

    turn: ExchangeTurn
    reason_code: str | None
    status_text: str | None
    review_call_ids: tuple[str, ...]


def _basename(uri: str) -> str:
    """Basename of an artifact URI's path (``http://h/x/bill.pdf`` → ``bill.pdf``)."""
    return os.path.basename(urlparse(uri).path or uri)


def _review_target(
    entries: dict[str, LedgerEntry], artifact_name: str
) -> LedgerEntry | None:
    """The entry a ``request_human_review(artifact_name=...)`` call parks.

    Match order: ``doc_id`` → ``artifact.name`` → basename of ``artifact.uri`` →
    else the single ``hitl_review`` entry when exactly one exists. ``None`` when
    nothing matches (the park is still real; it just mints no entry).
    """
    if artifact_name:
        if artifact_name in entries:
            return entries[artifact_name]
        for e in entries.values():
            if e.artifact is not None and e.artifact.name == artifact_name:
                return e
        for e in entries.values():
            if (
                e.artifact is not None
                and e.artifact.uri
                and _basename(e.artifact.uri) == artifact_name
            ):
                return e
    hitl = [e for e in entries.values() if e.gate is Gate.hitl_review]
    return hitl[0] if len(hitl) == 1 else None


def _apply_gate_response(
    entries: dict[str, LedgerEntry],
    pins: dict[str, Any],
    call_id: str,
    resp: dict[str, Any],
) -> None:
    """Mint or update a ledger entry from a pinned ``run_disposition_gate`` response."""
    pin = pins.get(call_id)
    parsed = gatepin.pinned_signals(pin) if pin is not None else None
    if parsed is None:
        return
    doc_id, signals = parsed
    try:
        verdict = DispositionVerdict.model_validate(resp)
    except ValidationError:
        return
    if dispose_signals(signals).model_dump(mode="json") != resp:
        return
    if (
        verdict.disposition is Disposition.accepted
        and verdict.gate is not Gate.auto_approve
    ):
        return
    base = entries.get(doc_id)
    if base is not None:
        entries[doc_id] = base.model_copy(
            update={
                "disposition": verdict.disposition,
                "gate": verdict.gate,
                "reason_code": verdict.reason_code,
                "message": verdict.message,
            }
        )
    else:
        entries[doc_id] = LedgerEntry(
            doc_id=doc_id,
            doctype=signals.doctype,
            issuer=signals.issuer,
            disposition=verdict.disposition,
            gate=verdict.gate,
            reason_code=verdict.reason_code,
            message=verdict.message,
        )


class _ProjectionAccumulator:
    """Mutable single-pass accumulator backing :func:`project_from_events`."""

    def __init__(self, seed_entries: Sequence[LedgerEntry]) -> None:
        self.entries: dict[str, LedgerEntry] = {e.doc_id: e for e in seed_entries}
        self.calls: dict[str, tuple[str, dict[str, Any], Event]] = {}
        self.answered: set[str] = set()
        self.pending: list[str] = []
        self.escalated = False
        self.candidates: list[tuple[int, int, str, str | None]] = []
        self.pins: dict[str, Any] = {}

    def add_candidate(self, rank: int, reason: str, text: object) -> None:
        self.candidates.append(
            (
                rank,
                len(self.candidates),
                reason,
                text if isinstance(text, str) else None,
            )
        )

    def park(self, call_id: str, artifact_name: object, message: str | None) -> None:
        self.pending.append(call_id)
        base = _review_target(
            self.entries, artifact_name if isinstance(artifact_name, str) else ""
        )
        if base is not None:
            self.entries[base.doc_id] = base.model_copy(
                update={
                    "disposition": Disposition.pending,
                    "gate": Gate.hitl_review,
                    "reason_code": REASON_HUMAN_REVIEW,
                    "message": message,
                }
            )
        self.add_candidate(_RANK_REVIEW, REASON_HUMAN_REVIEW, message)

    def apply_response(
        self, name: str, call_id: str, args: dict[str, Any], resp: dict[str, Any]
    ) -> None:
        if name == GATE_TOOL:
            _apply_gate_response(self.entries, self.pins, call_id, resp)
        elif name == REVIEW_TOOL:
            msg = resp.get("message")
            self.park(
                call_id,
                args.get("artifact_name"),
                msg if isinstance(msg, str) else None,
            )
        elif name == ESCALATE_TOOL:
            self.escalated = True
            self.add_candidate(_RANK_ESCALATE, REASON_ESCALATED, resp.get("message"))
        elif name == DELETE_TOOL:
            reason = resp.get("reason_code")
            if reason in (REASON_DELETED, REASON_NOTHING_TO_DELETE):
                self.add_candidate(_RANK_OFFSCRIPT, str(reason), resp.get("message"))
        elif name == REJECT_TOOL:
            self.add_candidate(
                _RANK_OFFSCRIPT, REASON_OFF_SCRIPT_REJECT, resp.get("message")
            )

    def ingest_event(self, event: Event) -> None:
        if event.partial is True:
            return
        delta = (
            event.actions.state_delta
            if event.actions is not None and event.author != _USER_AUTHOR
            else None
        )
        pin_delta = delta.get(gatepin.PINS_KEY) if isinstance(delta, dict) else None
        if isinstance(pin_delta, dict):
            self.pins.update(pin_delta)
        if event.content is None or not event.content.parts:
            return
        for part in event.content.parts:
            self._ingest_part(event, part)

    def _ingest_part(self, event: Event, part: genai_types.Part) -> None:
        fc = part.function_call
        if fc is not None:
            if event.author != _USER_AUTHOR and fc.name and fc.id:
                self.calls[fc.id] = (fc.name, dict(fc.args or {}), event)
            return
        fr = part.function_response
        if fr is None or event.author == _USER_AUTHOR or fr.name not in PROJECTED_TOOLS:
            return
        call_id = fr.id or ""
        args = self.calls.get(call_id, (None, {}, None))[1]
        if fr.id:
            self.answered.add(fr.id)
        self.apply_response(fr.name, call_id, args, dict(fr.response or {}))

    def finish(self, context_id: str) -> LlmProjection:
        for call_id, (name, args, event) in self.calls.items():
            if (
                name == REVIEW_TOOL
                and call_id not in self.answered
                and call_id in (event.long_running_tool_ids or set())
            ):
                self.park(call_id, args.get("artifact_name"), PAUSED_REVIEW_MESSAGE)
        terminal = not self.pending and not self.escalated
        lifecycle = (
            LifecycleStatus.escalated if self.escalated else LifecycleStatus.on_track
        )
        turn = ExchangeTurn(
            context_id=context_id,
            status=project_collection_status(list(self.entries.values())),
            terminal=terminal,
            lifecycle=lifecycle,
        )
        reason_code: str | None = None
        status_text: str | None = None
        if self.candidates:
            _, _, reason_code, status_text = max(
                self.candidates, key=lambda c: (c[0], c[1])
            )
        return LlmProjection(
            turn=turn,
            reason_code=reason_code,
            status_text=status_text,
            review_call_ids=tuple(self.pending),
        )


def project_from_events(
    events: Iterable[Event],
    *,
    context_id: str,
    seed_entries: Sequence[LedgerEntry] = (),
) -> LlmProjection:
    """Project an LLM-leg invocation's events onto an ``ExchangeTurn`` (D4 + D6).

    A single ordered pass over the events. Only ``function_call`` (to pair a
    response with its args) and ``function_response`` parts are read — never
    ``text`` or ``thought``. Partial (streaming) events, empty events, user-authored
    function responses (an executor-fed resolution is not a tool response) and
    responses of tools outside :data:`PROJECTED_TOOLS` are skipped.

    Per tool response:
    - ``run_disposition_gate``: minted ONLY from a pinned response — the pin
      (:data:`bridge.gatepin.PINS_KEY`, Bridge-authored by
      :func:`bridge.gatepin.pin_gate_args` on the response event's
      ``actions.state_delta``) must exist for the call id, the response must
      validate as a ``DispositionVerdict``, and it must equal ``dispose_signals``
      over the pinned signals. The entry's doc_id/doctype/issuer come from the pin
      (never from model args or text), overlaid on a seed entry with the same
      ``doc_id`` when present. Unpinned, malformed or inconsistent → skipped.
    - ``request_human_review``: the target entry becomes
      ``pending/hitl_review/awaiting_human_review`` with the tool's message; the call
      id is recorded; the turn is non-terminal.
    - ``escalate_submission``: no entry; non-terminal, ``lifecycle=escalated``.
    - ``handle_deletion_request`` / ``reject_submission``: no entry (never a
      ``rejected`` disposition); terminal.

    Paused LongRunningFunctionTool (ADK pause shape): a ``request_human_review`` call
    whose id is in its event's ``long_running_tool_ids`` and that has no response is
    treated as a pending review (with Bridge-authored text). The framework flag, not
    model text, is the signal. Other unanswered calls stay ignored.

    ``seed_entries`` (e.g. the Collector's entries of the same submission) are kept
    in order; projected entries overwrite by ``doc_id`` (last write wins) or append.
    """
    acc = _ProjectionAccumulator(seed_entries)
    for event in events:
        acc.ingest_event(event)
    return acc.finish(context_id)


def project_turn_from_events(
    events: Iterable[Event],
    *,
    context_id: str,
    seed_entries: Sequence[LedgerEntry] = (),
) -> ExchangeTurn:
    """The ``ExchangeTurn`` of :func:`project_from_events` (``context_id`` is required:
    an ADK ``Event`` carries no session id)."""
    return project_from_events(
        events, context_id=context_id, seed_entries=seed_entries
    ).turn


def _review_calls(events: Iterable[Event]) -> dict[str, dict[str, Any]]:
    """Framework-flagged ``request_human_review`` calls, in order: call id → args.

    Only model-authored calls whose id is in their event's ``long_running_tool_ids``
    (the ADK pause shape) count; partial events are skipped.
    """
    calls: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.partial is True or event.author == _USER_AUTHOR:
            continue
        if event.content is None or not event.content.parts:
            continue
        flagged = event.long_running_tool_ids or set()
        for part in event.content.parts:
            fc = part.function_call
            if fc is not None and fc.name == REVIEW_TOOL and fc.id and fc.id in flagged:
                calls.setdefault(fc.id, dict(fc.args or {}))
    return calls


def pending_review_calls(events: Iterable[Event]) -> list[str]:
    """The ids of still-pending ``request_human_review`` calls, in call order.

    A call is pending when it is framework-flagged long-running (its id is in its
    event's ``long_running_tool_ids``) and NO function response with that id exists
    from ANY author — unlike :func:`project_from_events`, a user-authored (executor-
    fed resolution) response counts as an answer here.
    """
    events = list(events)
    answered: set[str] = set()
    for event in events:
        if event.partial is True or event.content is None or not event.content.parts:
            continue
        for part in event.content.parts:
            fr = part.function_response
            if fr is not None and fr.id:
                answered.add(fr.id)
    return [cid for cid in _review_calls(events) if cid not in answered]


def resolved_review_calls(events: Iterable[Event]) -> dict[str, dict[str, Any]]:
    """User-authored ``request_human_review`` function responses: call id → payload.

    Only the executor authors these (R6 builds them from a validated
    ``HumanReviewResolution``): R4 forwards text only, and a client cannot inject a
    function response through the edge. The last response per id wins.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.partial is True or event.author != _USER_AUTHOR:
            continue
        if event.content is None or not event.content.parts:
            continue
        for part in event.content.parts:
            fr = part.function_response
            if fr is not None and fr.name == REVIEW_TOOL and fr.id:
                resolved[fr.id] = dict(fr.response or {})
    return resolved


@dataclass(frozen=True)
class ReviewTurn:
    """A parked task's turn rebuilt after a resume (D6 resolution row).

    ``call_ids`` are the task's review call ids (the parked ones plus any new park
    the resumed invocation made); ``pending`` the subset still unanswered;
    ``resolved`` maps each answered call id to its resolved ledger entry.
    """

    turn: ExchangeTurn
    call_ids: tuple[str, ...]
    pending: tuple[str, ...]
    resolved: dict[str, LedgerEntry]


def project_review_turn(
    session_events: Iterable[Event],
    *,
    context_id: str,
    seed_entries: Sequence[LedgerEntry],
    call_ids: Sequence[str],
    resume_events: Iterable[Event] = (),
) -> ReviewTurn:
    """Rebuild a parked task's ``ExchangeTurn`` from the durable session (pure).

    - ``seed_entries`` are the Collector's ORIGINAL entries of the task's submission
      (:data:`REVIEW_TASKS_KEY`), kept in order.
    - ``resume_events`` (the resumed invocation's yielded events) are projected over
      the seed (:func:`project_from_events`): a pinned gate response, an escalate or a
      new paused review call contribute as on any LLM-leg turn.
    - Each of the task's review calls targets its entry (:func:`_review_target` over
      the seed). A call resolved by an executor-fed response (over ALL session events,
      so earlier resolutions survive) → the ORIGINAL entry transformed exactly as
      ``bridge.pathb.resolve_hitl`` does (disposition = the resolution, message = the
      reviewer's message; gate ``hitl_review`` and the gate reason_code kept). A call
      still pending → the pending overlay (``awaiting_human_review``, the
      Bridge-authored paused text).
    - ``terminal`` = nothing pending on the task and no escalate.
    """
    session_events = list(session_events)
    seed = {e.doc_id: e for e in seed_entries}
    projection = project_from_events(
        resume_events, context_id=context_id, seed_entries=seed_entries
    )
    entries = {e.doc_id: e for e in projection.turn.status.entries}
    pending_all = pending_review_calls(session_events)
    resolved_all = resolved_review_calls(session_events)
    calls = _review_calls(session_events)

    task_calls = list(dict.fromkeys(call_ids))
    for cid in projection.review_call_ids:  # a new park made by the resumed run
        if cid in pending_all and cid not in task_calls:
            task_calls.append(cid)

    resolved: dict[str, LedgerEntry] = {}
    for cid in task_calls:
        name = calls.get(cid, {}).get("artifact_name")
        target = _review_target(seed, name if isinstance(name, str) else "")
        if target is None:
            continue
        payload = resolved_all.get(cid)
        raw_res = payload.get("resolution") if payload is not None else None
        disposition = _RESOLUTIONS.get(raw_res) if isinstance(raw_res, str) else None
        if disposition is not None:
            msg = payload.get("message") if payload is not None else None
            entry = target.model_copy(
                update={
                    "disposition": disposition,
                    "message": msg if isinstance(msg, str) else None,
                }
            )
            entries[target.doc_id] = entry
            resolved[cid] = entry
        elif cid in pending_all:
            entries[target.doc_id] = target.model_copy(
                update={
                    "disposition": Disposition.pending,
                    "gate": Gate.hitl_review,
                    "reason_code": REASON_HUMAN_REVIEW,
                    "message": PAUSED_REVIEW_MESSAGE,
                }
            )

    pending = tuple(cid for cid in pending_all if cid in task_calls)
    escalated = projection.turn.lifecycle is LifecycleStatus.escalated
    turn = ExchangeTurn(
        context_id=context_id,
        status=project_collection_status(list(entries.values())),
        terminal=not pending and not escalated,
        lifecycle=projection.turn.lifecycle,
    )
    return ReviewTurn(
        turn=turn, call_ids=tuple(task_calls), pending=pending, resolved=resolved
    )


__all__ = [
    "APP_NAME",
    "DELETE_TOOL",
    "ESCALATE_TOOL",
    "EXTRACTION_TOOL",
    "GATE_TOOL",
    "PAUSED_REVIEW_MESSAGE",
    "PROJECTED_TOOLS",
    "REJECT_TOOL",
    "REVIEW_TASKS_KEY",
    "REVIEW_TOOL",
    "_RANK_ESCALATE",
    "_RANK_OFFSCRIPT",
    "_RANK_REVIEW",
    "_RESOLUTIONS",
    "_USER_AUTHOR",
    "LlmProjection",
    "ReviewTurn",
    "SessionKey",
    "_basename",
    "_review_calls",
    "_review_target",
    "pending_review_calls",
    "project_from_events",
    "project_review_turn",
    "project_turn_from_events",
    "resolve_party",
    "resolved_review_calls",
    "session_key",
]
