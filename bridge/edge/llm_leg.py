"""The hybrid edge's LLM leg — the ``document_bridge`` LlmAgent behind the A2A edge.

S3-edge-1 D1 ratified the hybrid edge: structured traffic stays on the deterministic
Collector; intent-shaped traffic (off-script, real-erase deletion, the HITL park) is
handed to the ``document_bridge`` LlmAgent. This module is that leg's runtime — a
standalone, model-free-testable host for ``bridge.agent.build_app`` plus the pure
event → ``ExchangeTurn`` projection. Routing into it (``BRIDGE_EDGE_MODE``) is the
executor's concern, not this module's.

**Decision 2 holds.** This module runs a plain ADK ``Runner`` over the stock ``App``
and never imports ``google.adk.a2a`` / ``A2aAgentExecutor``: the A2A edge stays our
own ``CollectExecutor``, and every turn on the wire is the domain ``ExchangeTurn``
projected here — never ADK's event-to-A2A conversion.

**Host-controlled wiring (P7-8).** The session service comes from the **sessions
port** (durable ``DatabaseSessionService`` when ``BRIDGE_SESSION_DB_URL`` is set
locally, the GCP adapter on deploy) and the artifact store is injected — shared with
the Collector path so a deletion erases exactly what the edge persisted (ART-2
Decision 4) — or built by :func:`bridge.edge.artifacts.build_artifact_store`. Every
default is an ``or``-gated injectable; this module constructs no in-memory store
itself. ``App`` carries ``ResumabilityConfig(is_resumable=True)``; a FRESH ``Runner``
is built per invocation (no Runner state is held across turns — a resume runs on a
fresh Runner over the durable session).

**D4 — ledger from tool function responses only.** :func:`project_from_events`
reads ONLY the ``function_response`` parts of ``run_disposition_gate``,
``request_human_review``, ``handle_deletion_request``, ``reject_submission`` and
``escalate_submission``. Model text is never read (not even as ``status.message``),
function calls without a response are ignored (one framework-flagged exception: the
paused LongRunningFunctionTool call, see :func:`project_from_events`), the
``extraction_agent`` response never mints an entry, and a user-authored function
response is never treated as a tool response. ``reject_submission`` never mints a
``disposition=rejected`` entry (D6).

**Gate entries come from a pinned gate response only.** The root agent's
``before_tool_callback`` (:func:`bridge.gatepin.pin_gate_args`) overwrites every gate
argument from Bridge-recorded session signals and writes a pin (call id → doc_id +
the exact ``Signals``) that rides the gate response event's ``actions.state_delta``.
The projection mints a gate entry only when that pin exists and the response equals
``dispose_signals`` over the pinned signals; the entry's doc_id/doctype/issuer come
from the pin — never from model args or model/AgentTool text. Reading that
Bridge-authored callback output on a tool-response event is not reading model
content. An unpinned (refused, unwired) or inconsistent gate response mints nothing.
:meth:`LlmLeg.run` seeds the session (label space, Collector signals) before the
invocation so a seeded document is gateable in the same invocation.

**D9 — session key == artifact key.** The ADK session is keyed
``(app_name="document_bridge", user_id=<party>, session_id=<A2A context_id>)`` —
exactly the artifact store's leg-scoping key — so ``handle_deletion_request``
erases the artifacts saved for that context and cannot reach another context's.

**HITL park/resume (S3-edge-1 G/R3/R6).** ``request_human_review`` returns ``None``,
so a call to it pauses the resumable invocation with zero compute (the call event
carries ``long_running_tool_ids`` and no response). :func:`pending_review_calls` /
:func:`resolved_review_calls` read that durable session shape; :meth:`LlmLeg.resume`
feeds the reviewer's id-matched ``FunctionResponse`` to a FRESH ``Runner`` over the
same session and :func:`project_review_turn` rebuilds the task's turn from the
Collector's original entries (persisted at park under :data:`REVIEW_TASKS_KEY`) — a
resolved entry is transformed exactly as ``bridge.pathb.resolve_hitl`` does. The
executor validates the call id BEFORE resuming (ADK raises on an unknown id and
re-accepts an already-answered one).

**Isolation guard:** ``bridge/edge/`` must NEVER import ``agents/``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import google.genai.types as genai_types
from google.adk.apps import App
from google.adk.artifacts import BaseArtifactService
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events import Event, EventActions
from google.adk.memory import BaseMemoryService
from google.adk.models import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, Session
from pydantic import ValidationError

from bridge import gatepin
from bridge.agent import build_app
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
from bridge.ports import Port, PortMode, get_adapter, resolve_mode
from bridge.signals import dispose_signals
from contract.enums import Disposition, Gate, LifecycleStatus
from contract.wire import ExchangeTurn, LedgerEntry

# Opt-in env toggle to stream LlmLeg turns through a deployed Vertex AI Agent Engine
# ReasoningEngine (async_stream_query) instead of an in-process Runner.
BRIDGE_AGENT_ENGINE_REMOTE_ENV = "BRIDGE_AGENT_ENGINE_REMOTE"

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


# --------------------------------------------------------------------------- #
# Session key (D9)                                                             #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Projection (D4 + D6) — pure, no I/O                                          #
# --------------------------------------------------------------------------- #


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
    entries: dict[str, LedgerEntry] = {e.doc_id: e for e in seed_entries}
    # call id -> (tool name, args, event) — args pair a response with its call.
    calls: dict[str, tuple[str, dict[str, Any], Event]] = {}
    answered: set[str] = set()
    pending: list[str] = []
    escalated = False
    # (rank, seq, reason_code, status_text); winner = max(rank, seq) — ties → latest.
    candidates: list[tuple[int, int, str, str | None]] = []
    # gate call id -> pin, accumulated from Bridge-authored state deltas.
    pins: dict[str, Any] = {}

    def _candidate(rank: int, reason: str, text: Any) -> None:
        candidates.append(
            (rank, len(candidates), reason, text if isinstance(text, str) else None)
        )

    def _park(call_id: str, artifact_name: Any, message: str | None) -> None:
        pending.append(call_id)
        base = _review_target(
            entries, artifact_name if isinstance(artifact_name, str) else ""
        )
        if base is not None:
            entries[base.doc_id] = base.model_copy(
                update={
                    "disposition": Disposition.pending,
                    "gate": Gate.hitl_review,
                    "reason_code": REASON_HUMAN_REVIEW,
                    "message": message,
                }
            )
        _candidate(_RANK_REVIEW, REASON_HUMAN_REVIEW, message)

    for event in events:
        if event.partial is True:
            continue
        # Pins first — a state delta is read even off an event with no content, but
        # never off a user-authored event (the pin is the gate callback's output).
        delta = (
            event.actions.state_delta
            if event.actions is not None and event.author != _USER_AUTHOR
            else None
        )
        pin_delta = delta.get(gatepin.PINS_KEY) if isinstance(delta, dict) else None
        if isinstance(pin_delta, dict):
            pins.update(pin_delta)
        if event.content is None or not event.content.parts:
            continue
        for part in event.content.parts:
            fc = part.function_call
            if fc is not None:
                if event.author == _USER_AUTHOR or not fc.name:
                    continue
                args = dict(fc.args or {})
                if fc.id:
                    calls[fc.id] = (fc.name, args, event)
                continue
            fr = part.function_response
            if (
                fr is None
                or event.author == _USER_AUTHOR
                or fr.name not in PROJECTED_TOOLS
            ):
                continue
            args = calls.get(fr.id or "", (None, {}, None))[1]
            resp = dict(fr.response or {})
            if fr.id:
                answered.add(fr.id)
            if fr.name == GATE_TOOL:
                # accepted only from a pinned gate auto_approve here; the reviewer
                # resolution half arrives as an id-matched executor-fed response.
                pin = pins.get(fr.id or "")
                parsed = gatepin.pinned_signals(pin) if pin is not None else None
                if parsed is None:
                    continue  # unpinned (refused / unwired) → fail closed, never guess
                doc_id, signals = parsed
                try:
                    verdict = DispositionVerdict.model_validate(resp)
                except ValidationError:
                    continue  # malformed gate response (or a refusal) → skip
                if dispose_signals(signals).model_dump(mode="json") != resp:
                    continue  # not computed from the pinned inputs → skip
                if (
                    verdict.disposition is Disposition.accepted
                    and verdict.gate is not Gate.auto_approve
                ):
                    continue  # defense in depth: accepted only via auto_approve
                update = {
                    "disposition": verdict.disposition,
                    "gate": verdict.gate,
                    "reason_code": verdict.reason_code,
                    "message": verdict.message,
                }
                base = entries.get(doc_id)
                entries[doc_id] = (
                    base.model_copy(update=update)
                    if base is not None
                    else LedgerEntry(
                        doc_id=doc_id,
                        doctype=signals.doctype,
                        issuer=signals.issuer,
                        **update,
                    )
                )
            elif fr.name == REVIEW_TOOL:
                msg = resp.get("message")
                _park(
                    fr.id or "",
                    args.get("artifact_name"),
                    msg if isinstance(msg, str) else None,
                )
            elif fr.name == ESCALATE_TOOL:
                escalated = True
                _candidate(_RANK_ESCALATE, REASON_ESCALATED, resp.get("message"))
            elif fr.name == DELETE_TOOL:
                reason = resp.get("reason_code")
                if reason in (REASON_DELETED, REASON_NOTHING_TO_DELETE):
                    _candidate(_RANK_OFFSCRIPT, reason, resp.get("message"))
            elif fr.name == REJECT_TOOL:
                # Off-script reject: a status only — NEVER a disposition=rejected entry.
                _candidate(
                    _RANK_OFFSCRIPT, REASON_OFF_SCRIPT_REJECT, resp.get("message")
                )

    # Paused-LRFT shape: the framework-flagged, unanswered request_human_review call.
    for call_id, (name, args, event) in calls.items():
        if (
            name == REVIEW_TOOL
            and call_id not in answered
            and call_id in (event.long_running_tool_ids or set())
        ):
            _park(call_id, args.get("artifact_name"), PAUSED_REVIEW_MESSAGE)

    terminal = not pending and not escalated
    lifecycle = LifecycleStatus.escalated if escalated else LifecycleStatus.on_track
    turn = ExchangeTurn(
        context_id=context_id,
        status=project_collection_status(list(entries.values())),
        terminal=terminal,
        lifecycle=lifecycle,
    )
    reason_code: str | None = None
    status_text: str | None = None
    if candidates:
        _, _, reason_code, status_text = max(candidates, key=lambda c: (c[0], c[1]))
    return LlmProjection(
        turn=turn,
        reason_code=reason_code,
        status_text=status_text,
        review_call_ids=tuple(pending),
    )


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


# --------------------------------------------------------------------------- #
# HITL park/resume — pure session-event readers                                #
# --------------------------------------------------------------------------- #


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
        disposition = (
            _RESOLUTIONS.get(payload.get("resolution")) if payload is not None else None
        )
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


# --------------------------------------------------------------------------- #
# Runtime                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LlmLegResult:
    """One LLM-leg invocation: its session key, raw events, and wire projection."""

    key: SessionKey
    events: tuple[Event, ...]
    projection: LlmProjection

    @property
    def turn(self) -> ExchangeTurn:
        """The projected ``ExchangeTurn``."""
        return self.projection.turn


@dataclass(frozen=True)
class LlmResumeResult:
    """One resumed invocation: its session key, the yielded events, and the task's
    rebuilt turn (:class:`ReviewTurn`)."""

    key: SessionKey
    events: tuple[Event, ...]
    review: ReviewTurn

    @property
    def turn(self) -> ExchangeTurn:
        """The task's rebuilt ``ExchangeTurn``."""
        return self.review.turn


def _default_memory_service() -> BaseMemoryService | None:
    """Resolve the default ``BaseMemoryService`` from the memory_bank port.

    In local mode, always builds the local adapter (`InMemoryMemoryService` or
    `SqliteMemoryService` when `BRIDGE_MEMORY_DB_PATH` is set). In GCP mode, builds
    `VertexAiMemoryBankService` when `BRIDGE_AGENT_ENGINE_ID` (or an explicit
    `BRIDGE_PORT_MEMORY_BANK=gcp` override) is set; returns `None` when GCP mode
    is running on Cloud SQL without an Agent Engine ID.
    """
    mode = resolve_mode(Port.memory_bank)
    if (
        mode is PortMode.gcp
        and not os.environ.get("BRIDGE_AGENT_ENGINE_ID", "").strip()
        and not os.environ.get("BRIDGE_PORT_MEMORY_BANK", "").strip()
    ):
        return None
    return get_adapter(Port.memory_bank, mode).build_memory_service()


def _default_remote_engine() -> Any | None:
    """Resolve a remote Vertex AI Agent Engine ``ReasoningEngine`` handle when
    ``BRIDGE_AGENT_ENGINE_REMOTE`` is enabled (`1`/`true`/`yes`)."""
    flag = os.environ.get(BRIDGE_AGENT_ENGINE_REMOTE_ENV, "").strip().lower()
    if flag not in {"1", "true", "yes"}:
        return None
    from bridge.ports.gcp import parse_agent_engine_resource

    proj, loc, eid = parse_agent_engine_resource(None)
    import vertexai

    if proj and loc:
        vertexai.init(project=proj, location=loc)
    resource_name = (
        f"projects/{proj}/locations/{loc}/reasoningEngines/{eid}"
        if proj and loc
        else eid
    )
    return vertexai.agent_engines.get(resource_name)


def _coerce_event(item: Event | Mapping[str, Any]) -> Event:
    """Coerce an event yielded by ``Runner.run_async`` or remote
    ``ReasoningEngine.async_stream_query`` into an ADK ``Event``."""
    if isinstance(item, Event):
        return item
    return Event.model_validate(item)


class LlmLeg:
    """Runtime for the LLM leg: the resumable ``document_bridge`` App on a fresh
    ``Runner`` per invocation (or a remote Vertex AI Agent Engine ``ReasoningEngine``),
    over port-wired sessions, memory bank, and the shared artifact store.

    Every collaborator is injectable; the defaults are host-controlled (P7-8): the
    App from :func:`bridge.agent.build_app` (``model`` may be a name or a
    ``BaseLlm``), the session service from the sessions port, the memory service
    from the memory_bank port, and the artifact store from
    :func:`bridge.edge.artifacts.build_artifact_store`.
    """

    def __init__(
        self,
        *,
        model: str | BaseLlm | None = None,
        app: App | None = None,
        session_service: BaseSessionService | None = None,
        artifact_store: BaseArtifactService | None = None,
        memory_service: BaseMemoryService | None = None,
        remote_engine: Any | None = None,
    ) -> None:
        self._app = app or build_app(model)
        self._sessions = (
            session_service
            or get_adapter(
                Port.sessions, resolve_mode(Port.sessions)
            ).build_session_service()
        )
        self._store = artifact_store or artifacts.build_artifact_store()
        self._memory = (
            memory_service if memory_service is not None else _default_memory_service()
        )
        self._remote_engine = (
            remote_engine if remote_engine is not None else _default_remote_engine()
        )

    def bind_store(self, store: BaseArtifactService) -> None:
        """Share an artifact store (the edge's) with this leg — mirrors
        ``CollectExecutor.bind_store`` so a deletion erases what the edge persisted."""
        self._store = store

    @property
    def app(self) -> App:
        """The resumable ``document_bridge`` App."""
        return self._app

    @property
    def session_service(self) -> BaseSessionService:
        """The port-wired session service."""
        return self._sessions

    @property
    def artifact_store(self) -> BaseArtifactService:
        """The (shared) artifact store."""
        return self._store

    @property
    def memory_service(self) -> BaseMemoryService | None:
        """The port-wired memory service (`BaseMemoryService`), if configured."""
        return self._memory

    @property
    def remote_engine(self) -> Any | None:
        """Optional remote Vertex AI Agent Engine (`ReasoningEngine`) handle."""
        return self._remote_engine

    def runner(self) -> Runner:
        """A FRESH ``Runner`` over the App — never cached (no Runner state is held
        across turns; a resume runs on a fresh Runner over the durable session)."""
        return Runner(
            app=self._app,
            session_service=self._sessions,
            artifact_service=self._store,
            memory_service=self._memory,
        )

    async def _stream_events(
        self,
        *,
        session: Session | None,
        user_id: str,
        session_id: str,
        new_message: genai_types.Content,
        state_delta: dict[str, Any] | None = None,
    ) -> list[Event]:
        """Run one invocation either locally (`Runner.run_async`) or against a remote
        Vertex AI Agent Engine (`ReasoningEngine.async_stream_query`)."""
        if self._remote_engine is None:
            return [
                event
                async for event in self.runner().run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=new_message,
                    state_delta=state_delta,
                )
            ]
        if state_delta and session is not None:
            await self._sessions.append_event(
                session=session,
                event=Event(
                    author="bridge",
                    actions=EventActions(state_delta=state_delta),
                ),
            )
        payload = new_message.model_dump(mode="json", exclude_none=True)
        return [
            _coerce_event(raw)
            async for raw in self._remote_engine.async_stream_query(
                user_id=user_id,
                session_id=session_id,
                message=payload,
            )
        ]

    async def ensure_session(
        self, key: SessionKey, *, state: dict[str, Any] | None = None
    ) -> Session:
        """Get the session at ``key``, creating it (with ``state``) when absent.

        A concurrent create of the same key (``AlreadyExistsError``) falls back to a
        re-read.
        """
        session = await self._sessions.get_session(
            app_name=key.app_name, user_id=key.user_id, session_id=key.session_id
        )
        if session is not None:
            return session
        try:
            return await self._sessions.create_session(
                app_name=key.app_name,
                user_id=key.user_id,
                session_id=key.session_id,
                state=state or {},
            )
        except AlreadyExistsError:
            session = await self._sessions.get_session(
                app_name=key.app_name, user_id=key.user_id, session_id=key.session_id
            )
            if session is None:
                raise
            return session

    async def run(
        self,
        *,
        party: str | None,
        context_id: str,
        message: str | genai_types.Content,
        label_space: Sequence[str] | None = None,
        seed_signals: Sequence[gatepin.RecordedSignals] = (),
        seed_entries: Sequence[LedgerEntry] = (),
        review_task_id: str | None = None,
    ) -> LlmLegResult:
        """Run one LLM-leg invocation and project it (D4/D6) onto an ``ExchangeTurn``.

        The session key is ``(document_bridge, party, context_id)`` (D9); ``party``
        ``None`` resolves from the shared artifact store (:func:`resolve_party`).

        Seeding (Bridge-decided, never model-supplied): ``label_space`` is the
        process's candidate doctypes the gate pin uses for extraction-sourced
        records; ``seed_signals`` are recorded signals (e.g. the Collector's) made
        gateable; both ride the new message's ``state_delta``, so the pin sees them
        in this same invocation. ``seed_entries`` are the projection's seed entries
        (e.g. the Collector's entries of the same submission).

        ``review_task_id`` (the R3 HITL handoff): also persist ``seed_entries`` under
        :data:`REVIEW_TASKS_KEY` for that A2A task, in the same ``state_delta``, so a
        later :meth:`resume` rebuilds the turn from the Collector's ORIGINAL entries.
        """
        key = session_key(self._store, party=party, context_id=context_id)
        session = await self.ensure_session(key)
        delta = gatepin.seed_state_delta(
            session.state, label_space=label_space, signals=seed_signals
        )
        if review_task_id:
            delta[REVIEW_TASKS_KEY] = {
                **(session.state.get(REVIEW_TASKS_KEY) or {}),
                review_task_id: [e.model_dump(mode="json") for e in seed_entries],
            }
        content = (
            message
            if isinstance(message, genai_types.Content)
            else genai_types.Content(
                role="user", parts=[genai_types.Part(text=message)]
            )
        )
        events = await self._stream_events(
            session=session,
            user_id=key.user_id,
            session_id=key.session_id,
            new_message=content,
            state_delta=delta or None,
        )
        return LlmLegResult(
            key=key,
            events=tuple(events),
            projection=project_from_events(
                events, context_id=context_id, seed_entries=seed_entries
            ),
        )

    # ------------------------------------------------------------------ #
    # HITL park/resume (G / R6) — session reads never build a Runner      #
    # ------------------------------------------------------------------ #
    def key_for(self, *, user_id: str, session_id: str) -> SessionKey:
        """The session key of a parked task (from its ``META_REVIEW_SESSION``)."""
        if not session_id:
            raise ValueError("session_id is required for the LLM-leg session key")
        return SessionKey(APP_NAME, user_id, session_id)

    async def get_session(self, key: SessionKey) -> Session | None:
        """Read the session at ``key`` (a session-service read only — no Runner)."""
        return await self._sessions.get_session(
            app_name=key.app_name, user_id=key.user_id, session_id=key.session_id
        )

    async def pending_for(self, key: SessionKey, call_ids: Iterable[str]) -> list[str]:
        """The task's still-pending review call ids, in call order: ``call_ids`` ∩
        :func:`pending_review_calls` over the durable session. A session read only —
        never builds a Runner, never calls the model (the G guard's zero compute)."""
        wanted = set(call_ids)
        if not wanted:
            return []
        session = await self.get_session(key)
        if session is None:
            return []
        return [cid for cid in pending_review_calls(session.events) if cid in wanted]

    async def resume(
        self,
        key: SessionKey,
        *,
        call_id: str,
        resolution: str,
        message: str,
        reviewer: str | None,
        task_id: str,
        call_ids: Sequence[str],
    ) -> LlmResumeResult:
        """Resume a parked invocation with the reviewer's resolution (R6).

        Feeds an id-matched ``FunctionResponse(id=call_id, name=request_human_review)``
        as ``role="user"`` content to a FRESH :meth:`runner` over the durable session
        — ADK resumes the paused invocation (same ``invocation_id``); no HTTP
        re-drive. The CALLER must have validated that ``call_id`` is pending on this
        task (ADK raises on an unknown id and re-accepts an answered one).

        The task's turn is rebuilt by :func:`project_review_turn` from the reloaded
        session, the Collector's original entries persisted at park for ``task_id``
        (:data:`REVIEW_TASKS_KEY`) and the resumed invocation's events.
        """
        content = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id=call_id,
                        name=REVIEW_TOOL,
                        response={
                            "resolution": resolution,
                            "message": message,
                            "reviewer": reviewer,
                        },
                    )
                )
            ],
        )
        events = await self._stream_events(
            session=None,
            user_id=key.user_id,
            session_id=key.session_id,
            new_message=content,
        )
        session = await self.get_session(key)
        if session is None:  # the session existed a moment ago (pending check)
            raise RuntimeError(f"LLM-leg session vanished during resume: {key}")
        stored = _stored_review_entries(session.state, task_id)
        review = project_review_turn(
            session.events,
            context_id=key.session_id,
            seed_entries=stored,
            call_ids=call_ids,
            resume_events=events,
        )
        return LlmResumeResult(key=key, events=tuple(events), review=review)


def _stored_review_entries(state: Mapping[str, Any], task_id: str) -> list[LedgerEntry]:
    """The Collector entries persisted at park for ``task_id`` (malformed → skipped)."""
    raw = (state.get(REVIEW_TASKS_KEY) or {}).get(task_id) or []
    entries: list[LedgerEntry] = []
    for item in raw:
        try:
            entries.append(LedgerEntry.model_validate(item))
        except ValidationError:
            continue
    return entries
