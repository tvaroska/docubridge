"""The Bridge edge executor: ``CollectExecutor`` (an ``a2a-sdk`` AgentExecutor).

**Decision 1 — the hybrid edge (S3-edge-1 D1).** Structured traffic — Path-A
extraction assertions, ``CollectRequest``s and by-reference file submissions — runs on
the deterministic, model-free path: ``bridge.collect.Collector.collect(...)`` (the
P1-4 one-turn Sense-A path) or Path-A validate-only, its ``ExchangeTurn`` encoded into
A2A parts. That front door stays deterministic and testable (a conformant A2A client
drives every spec op; the app reads only standard task status + the ledger
projection, never ADK events). Behind ``BRIDGE_EDGE_MODE=hybrid`` a text-only intent
(off-script, real-erase deletion, "I need a person") is handed to the
``document_bridge`` LlmAgent leg (``bridge.edge.llm_leg``); its turn is still a
projected domain ``ExchangeTurn`` data part built from tool function responses — never
model text, on the artifact or in ``status.message``. The route table is
``bridge.edge.pathroute.classify_route``; the default (``deterministic``) mode routes
every message model-free, exactly as before. See ``design/a2a-edge.md``.

**Decision 2 — built on ``a2a-sdk``'s ``AgentExecutor`` ABC, NOT ADK's
``A2aAgentExecutor``** (which is ``@a2a_experimental`` and unregistered in
``bridge/adk/experimental.py``). This keeps us on a first-class, non-experimental
Ring-1 surface. The LLM leg runs a plain ADK ``Runner`` behind this executor; it does
not change this.

**HITL park/resume (S3-edge-1 G/R3/R6).** On a hybrid edge, a Collector turn with a
``hitl_review`` entry under a process whose skill opts in (``bridge-hitl: park``) is
handed to the LLM leg (R3), which parks the task on ``request_human_review``
(INPUT_REQUIRED, zero compute). The parked task is locked to its resolution: any
other message is refused (G), and an authorized reviewer's id-matched
``HumanReviewResolution`` resumes the parked invocation (R6). Every refusal is raised
by :meth:`CollectExecutor.admit` BEFORE the SDK dispatches the message (the
``bridge.edge.server`` handler calls it) — an exception raised inside ``execute``
would mark the existing task FAILED. ``execute`` repeats the checks as defense in
depth.

**COLL-2 satisfaction proposal (S0-arch-16).** When ``build_a2a_app`` binds a
satisfaction assessor (opt-in, ``BRIDGE_SATISFACTION=llm``), the deterministic legs
(Collector turns and Path-A turns) replace the turn's per-entry
``status.outstanding`` with the assessor's advisory proposal read from the process
skill's ``bridge-satisfaction`` prose (``bridge.satisfaction``); any failure keeps the
per-entry list. The proposal never sets ``done``, never touches ``terminal`` or the
task state, and is never a gate input. With no assessor bound (the default) these
legs stay model-free and byte-identical.

**RFP opener (S0-arch-19).** When ``build_a2a_app`` binds an RFP board (env-gated by
``BRIDGE_RFP_PROCESS``, over the durable shared ``exchange_store``; ``bind_rfp``), a
``CollectRequest`` of a process the board SERVES (``RfpBoard.serves``: its home process, or
an emergent-Collect process skill installed live in the registry — S4-usecase-4) skips the
Collector: ``bridge.edge.rfp_open`` opens the RFP (``rfp_id`` = the exchange context:
``request.context_id``, else the A2A task context), applies the agent's next requirements,
or reads its status, and the RFP ``ExchangeTurn`` is emitted (``terminal=False`` →
INPUT_REQUIRED: the exchange waits on carriers). Any other process — a non-RFP process
such as a plain Collect skill without an ``rfp:`` block — stays on the Collector; with no
board bound every process does. Model-free in both edge modes (a ``CollectRequest`` data part classifies as
R2). Refusals (a file part, an invalid context id, an invalid request, an exhausted CAS
conflict) are raised before any task event, so a new task is a JSON-RPC error and nothing
is stored.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Role, TaskState
from a2a.utils.errors import (
    InvalidParamsError,
    InvalidRequestError,
    UnsupportedOperationError,
)
from google.adk.artifacts import BaseArtifactService
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from bridge import patha
from bridge.collect import Collector, project_collection_status
from bridge.edge.artifacts import (
    UNSCOPED_USER,
    Resolver,
    build_artifact_store,
    filename_for,
    persist_submission,
    resolve_bytes,
    scoped_uri,
)
from bridge.edge.card import DEFAULT_URL
from bridge.edge.intake import decode_intake, parse_text_request
from bridge.edge.pathroute import EdgeMode, Route, classify_route, decode_assertion
from bridge.edge.reviewers import is_authorized_reviewer, resolve_reviewers
from bridge.edge.rfp_open import (
    INVALID_RFP_CONTEXT_TEXT,
    INVALID_RFP_REQUEST_PREFIX,
    RFP_CONFLICT_TEXT,
    RFP_SUBMISSIONS_TEXT,
    InvalidRfpContext,
    open_or_update_rfp,
    rfp_context_id,
)
from bridge.edge.skills import (
    candidate_doctypes_for,
    hitl_mode_for,
    satisfaction_for,
)
from bridge.edge.wire import (
    META_REASON_CODE,
    META_REVIEW_CALL_ID,
    META_REVIEW_SESSION,
    decode_human_review_resolution,
    decode_submissions,
    encode_exchange_turn,
    status_message_for,
    summarize_turn,
)
from bridge.exchange_store import StaleExchangeWrite
from bridge.offscript import REASON_HUMAN_REVIEW, REASON_UNROUTED
from bridge.satisfaction import SatisfactionAssessor, propose_outstanding
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionResult
from contract.wire import (
    CollectRequest,
    ExchangeTurn,
    HumanReviewResolution,
    LedgerEntry,
)

if TYPE_CHECKING:
    # Never imported at runtime here: ``import bridge.edge`` stays free of the
    # Runner / ``bridge.agent`` import cost; ``build_a2a_app`` imports it lazily,
    # and only in hybrid mode.
    from bridge.dashboard.board import RfpBoard
    from bridge.edge.llm_leg import LlmLeg, LlmProjection, SessionKey

logger = logging.getLogger(__name__)

# Bridge-authored status texts for the LLM leg (D6: never model text).
LLM_WORKING_TEXT = "The Bridge is working on your message."
LLM_UNROUTED_TEXT = "No action was taken on this message."
LLM_FAILED_TEXT = "The Bridge could not process this message."
# Bridge-authored user text of the R3 HITL handoff; one " <doc_id> (<reason_code>)"
# per hitl entry follows (tests key ScriptedLlm ``on_text`` on this prefix).
HITL_HANDOFF_TEXT = "Human review required for:"
# The status text of a parked review (== ``llm_leg.PAUSED_REVIEW_MESSAGE``; duplicated
# so this module never imports the leg at runtime — a test pins the equality).
PAUSED_REVIEW_TEXT = "This submission was sent for human review."

# Refusal texts (raised pre-dispatch by ``admit``; the reason code leads the text).
GUARD_REFUSE_TEXT = (
    "awaiting_human_review: this task is parked for a reviewer and accepts only "
    "the review resolution; resubmit on a new task"
)
NO_PENDING_REVIEW_TEXT = (
    "no_pending_review: this task has no pending human review to resolve"
)
MALFORMED_RESOLUTION_TEXT = (
    "malformed_human_review_resolution: the human_review_resolution part is not a "
    "valid HumanReviewResolution"
)
REVIEWER_NOT_AUTHORIZED_TEXT = (
    "reviewer_not_authorized: the caller is not an authorized reviewer"
)
REVIEW_CALL_ID_MISMATCH_TEXT = (
    "review_call_id_mismatch: call_id does not match a pending human review on "
    "this task"
)


@dataclass(frozen=True)
class ReviewScope:
    """A parked task's review scope, read from its server-authored
    ``task.metadata[META_REVIEW_SESSION]`` + the durable LLM-leg session.

    ``call_ids`` are the review calls parked on THIS task; ``pending`` the subset
    still unanswered in the session (call order).
    """

    key: SessionKey
    call_ids: tuple[str, ...]
    pending: tuple[str, ...]


class CollectExecutor(AgentExecutor):
    """Edge executor: routes each message (``BRIDGE_EDGE_MODE``) to Path-A, the
    deterministic Collector, or the LLM leg."""

    def __init__(
        self,
        collector: Collector | None = None,
        *,
        artifact_store: BaseArtifactService | None = None,
        base_url: str = DEFAULT_URL,
        resolver: Resolver = resolve_bytes,
    ) -> None:
        # Default Collector lazily resolves the extraction seam via get_adapter
        # (P1-4). Injectable for tests.
        self._collector = collector or Collector()
        # ART-2: the artifact store is a native-service backend swap, not a seam
        # (GCS on deploy via BRIDGE_ARTIFACT_BUCKET, else in-memory; P7-3). A
        # standalone fallback instance is created here, but the SHARED instance
        # (writer == reader, Decision 4) is bound by build_a2a_app via bind_store.
        self._store: BaseArtifactService = artifact_store or build_artifact_store()
        self._base_url = base_url
        self._resolver = resolver
        # D2: the routing mode + LLM leg are bound once by build_a2a_app (bind_edge);
        # a bare executor is deterministic (never touches a model).
        self._mode: EdgeMode = EdgeMode.deterministic
        self._llm_leg: LlmLeg | None = None
        # D10: the reviewer allow-list (fail closed: empty → every resolution refused).
        self._reviewers: frozenset[str] = frozenset()
        # COLL-2: the satisfaction assessor, bound once by build_a2a_app
        # (bind_assessor); a bare executor never assesses (never touches a model).
        self._assessor: SatisfactionAssessor | None = None
        # S0-arch-19: the RFP opener's board, bound once by build_a2a_app (bind_rfp);
        # a bare executor routes every CollectRequest to the Collector.
        self._rfp: RfpBoard | None = None

    def bind_store(
        self, store: BaseArtifactService, *, base_url: str | None = None
    ) -> None:
        """Bind the shared artifact store (Decision 4: writer == reader).

        ``build_a2a_app`` calls this so the executor (inbound writer) and the
        fetch app (outbound reader) share ONE ``InMemoryArtifactService`` — two
        instances would leave every fetch 404ing. ``base_url`` is re-bound too so
        a pre-built executor's rewritten URIs point at the app's actual card url.
        """
        self._store = store
        if base_url is not None:
            self._base_url = base_url

    def bind_edge(
        self,
        mode: EdgeMode,
        *,
        llm_leg: LlmLeg | None = None,
        reviewers: Any = None,
    ) -> None:
        """D2: bind the routing mode (resolved once at app build) and, in hybrid
        mode, the LLM leg.

        ``build_a2a_app`` is the single caller (mirrors ``bind_store``). A hybrid edge
        without a leg is a wiring bug → ``ValueError``. In deterministic mode any
        passed leg is ignored — the deterministic edge never holds one.

        ``reviewers`` (D10): the reviewer principal allow-list, resolved by
        :func:`bridge.edge.reviewers.resolve_reviewers` (explicit wins, else
        ``BRIDGE_EDGE_REVIEWERS``; unset → empty → every resolution refused).
        """
        self._reviewers = resolve_reviewers(reviewers)
        if mode is EdgeMode.hybrid:
            if llm_leg is None:
                raise ValueError("a hybrid edge needs an LLM leg (llm_leg=None)")
            self._llm_leg = llm_leg
        else:
            self._llm_leg = None
        self._mode = mode

    def bind_assessor(self, assessor: SatisfactionAssessor | None) -> None:
        """COLL-2: bind the satisfaction assessor (resolved once at app build by
        :func:`bridge.satisfaction.resolve_assessor`; mirrors ``bind_edge``).
        ``None`` → the per-entry outstanding list, model-free."""
        self._assessor = assessor

    def bind_rfp(self, board: RfpBoard | None) -> None:
        """S0-arch-19: bind the RFP opener's board (resolved once at app build by
        :func:`bridge.edge.rfp_open.rfp_board_from_env`, or injected; mirrors
        ``bind_assessor``). ``None`` → no opener: every ``CollectRequest`` goes to the
        Collector."""
        self._rfp = board

    @property
    def rfp_board(self) -> RfpBoard | None:
        """The bound RFP opener board (``None`` = no opener, the default)."""
        return self._rfp

    @property
    def assessor(self) -> SatisfactionAssessor | None:
        """The bound satisfaction assessor (``None`` = off, the default)."""
        return self._assessor

    @property
    def mode(self) -> EdgeMode:
        """The bound edge routing mode."""
        return self._mode

    @property
    def llm_leg(self) -> LlmLeg | None:
        """The bound LLM leg (``None`` in deterministic mode)."""
        return self._llm_leg

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Execute an inbound A2A document collection request against the bridge state machine."""
        message = context.message
        route, scope = await self._route_for(message, context.current_task)

        # Dual-path routing (PATH-A/PATH-B): a structured extraction-assertion (no
        # file bytes) is validate-only — schema + disposition, NO extraction seam
        # call, NO artifact persistence. Everything else keeps today's file/text
        # collect path unchanged (§2c).
        if route is Route.PATH_A:
            turn = self._build_path_a_turn(message)
            process = decode_intake(message).process
            await self._propose(turn, process, self._candidate_doctypes(process))
            await self._emit_turn(context, event_queue, turn, message)
            return
        if route is Route.COLLECT:
            await self._execute_collect(context, event_queue, message)
            return
        if route is Route.LLM:
            await self._execute_llm_leg(context, event_queue, message)
            return
        # Defense in depth: ``admit`` refuses G/R5/bad R6 before dispatch (an
        # exception here would mark the existing task FAILED).
        if route is Route.NO_PENDING_REVIEW:
            raise InvalidRequestError(message=NO_PENDING_REVIEW_TEXT)
        if route is Route.GUARD_REFUSE:
            raise InvalidRequestError(message=GUARD_REFUSE_TEXT)
        if route is Route.RESUME_REVIEW and scope is not None:
            user = context.call_context.user if context.call_context else None
            resolution = self._validate_resolution(message, user, scope)
            await self._execute_resume_review(context, event_queue, scope, resolution)
            return
        raise UnsupportedOperationError(  # pragma: no cover - route table exhausted
            message="this message is not supported by this edge"
        )

    async def admit(self, message: Message | None, task: Any, user: Any) -> None:
        """Pre-dispatch guard (G / R5 / R6 validation) — called by the edge's request
        handler BEFORE the SDK touches the task, so a refusal leaves the task
        byte-for-byte unchanged (an exception raised inside ``execute`` would mark
        an existing task FAILED).

        Deterministic mode: a no-op (never reads the LLM session). Hybrid:
        - G: a non-resolution message on a task parked for a reviewer →
          ``InvalidRequestError`` ``awaiting_human_review`` (zero compute: a session
          read only, never a Runner);
        - R5: a resolution with nothing pending → ``InvalidRequestError``
          ``no_pending_review``;
        - R6: :meth:`_validate_resolution` (malformed → ``InvalidParamsError``,
          unauthorized principal → ``InvalidRequestError``, call id not pending on
          this task → ``InvalidParamsError``).
        ``user`` is the A2A call-context user — the ONLY authorization principal.
        """
        if self._mode is not EdgeMode.hybrid or message is None:
            return
        route, scope = await self._route_for(message, task)
        if route is Route.GUARD_REFUSE:
            raise InvalidRequestError(message=GUARD_REFUSE_TEXT)
        if route is Route.NO_PENDING_REVIEW:
            raise InvalidRequestError(message=NO_PENDING_REVIEW_TEXT)
        if route is Route.RESUME_REVIEW and scope is not None:
            self._validate_resolution(message, user, scope)

    async def _route_for(
        self, message: Message | None, task: Any
    ) -> tuple[Route, ReviewScope | None]:
        """Classify ``message`` against ``task`` (D3 route table) + the task's review
        scope. G1: deterministic mode never reads the LLM session."""
        scope = (
            await self._review_scope(task) if self._mode is EdgeMode.hybrid else None
        )
        route = classify_route(
            message,
            mode=self._mode,
            task_state=task.status.state if task is not None else None,
            has_pending_review=scope is not None and bool(scope.pending),
        )
        return route, scope

    async def _has_pending_review(self, context: RequestContext) -> bool:
        """Whether the task holds an unanswered ``request_human_review`` call parked
        on it (the G guard / R6 resume session read; hybrid mode only)."""
        scope = await self._review_scope(context.current_task)
        return scope is not None and bool(scope.pending)

    async def _review_scope(self, task: Any) -> ReviewScope | None:
        """The parked task's :class:`ReviewScope`, else ``None``.

        Requires a bound leg, an INPUT_REQUIRED task and the server-authored
        ``task.metadata[META_REVIEW_SESSION]`` (client request metadata never reaches
        ``task.metadata``). Pending-for-task = the task's call ids ∩ the session's
        unanswered review calls — a call parked on ANOTHER task of the same context
        is never pending here. A session-service read only: never builds a Runner,
        never calls the model.
        """
        if self._llm_leg is None or task is None:
            return None
        if task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
            return None
        if not task.HasField("metadata"):
            return None
        raw = MessageToDict(task.metadata).get(META_REVIEW_SESSION)
        if not isinstance(raw, dict):
            return None
        user_id, session_id = raw.get("user_id"), raw.get("session_id")
        call_ids = raw.get("call_ids") or []
        if not (
            isinstance(user_id, str) and isinstance(session_id, str) and session_id
        ):
            return None
        call_ids = tuple(c for c in call_ids if isinstance(c, str) and c)
        key = self._llm_leg.key_for(user_id=user_id, session_id=session_id)
        pending = tuple(await self._llm_leg.pending_for(key, call_ids))
        return ReviewScope(key=key, call_ids=call_ids, pending=pending)

    def _validate_resolution(
        self, message: Message | None, user: Any, scope: ReviewScope
    ) -> HumanReviewResolution:
        """R6 checks, in order (D-h), all before any task event:

        1. decode the tagged part (tolerant reader) → else ``InvalidParamsError``
           ``malformed_human_review_resolution``;
        2. the call-context principal is an authorized reviewer → else
           ``InvalidRequestError`` ``reviewer_not_authorized`` (BEFORE the id check,
           so id validity never leaks to a non-reviewer; the payload ``reviewer`` is
           an audit label and never read here);
        3. ``call_id`` is pending on THIS task → else ``InvalidParamsError``
           ``review_call_id_mismatch`` (a wrong, already-resolved or other-task id).
        """
        try:
            resolution = (
                decode_human_review_resolution(message) if message is not None else None
            )
        except ValidationError as e:
            raise InvalidParamsError(message=MALFORMED_RESOLUTION_TEXT) from e
        if resolution is None:
            raise InvalidParamsError(message=MALFORMED_RESOLUTION_TEXT)
        if not is_authorized_reviewer(user, self._reviewers):
            raise InvalidRequestError(message=REVIEWER_NOT_AUTHORIZED_TEXT)
        if resolution.call_id not in scope.pending:
            raise InvalidParamsError(message=REVIEW_CALL_ID_MISMATCH_TEXT)
        return resolution

    async def _execute_collect(
        self, context: RequestContext, event_queue: EventQueue, message: Any
    ) -> None:
        """R2: the deterministic, model-free Collector path."""
        # WIRE-5 dual-mode intake: content-route JSON or text to CollectRequest
        request = decode_intake(message)
        # S0-arch-19: a process the bound RFP board serves opens/updates/reads its RFP
        # instead — BEFORE submissions decode and the HITL park logic; no COLL-2
        # proposal (the RFP turn already proposes the outstanding delta). ``serves`` is
        # the board's home process (free) or, S4-usecase-4, an emergent-Collect process
        # live in the registry: a sync registry read on the loop, the same posture as
        # ``_candidate_doctypes`` / ``hitl_mode_for`` below.
        if self._rfp is not None and self._rfp.serves(request.process):
            await self._execute_rfp(context, event_queue, message, request)
            return
        submissions = decode_submissions(message)
        doctypes = self._candidate_doctypes(request.process)

        # R3 eligibility (D5): a hybrid edge with a leg, under a process whose skill
        # opts in to Bridge-owned review. Only then are the genuine extraction
        # results captured (the handoff seeds the gate pin from them).
        park = (
            self._mode is EdgeMode.hybrid
            and self._llm_leg is not None
            and hitl_mode_for(request.process) == "park"
        )
        results: dict[str, ExtractionResult] = {}
        if park:
            turn = await self._collector.collect(
                request,
                documents=submissions,
                candidate_doctypes=doctypes,
                results_out=results,
            )
        else:
            turn = await self._collector.collect(
                request, documents=submissions, candidate_doctypes=doctypes
            )

        # ART-2: persist inbound bytes into the shared ArtifactService (versioned
        # per resubmission = ART-1), then rewrite each entry's ArtifactRef.uri to
        # the scoped fetch endpoint. Read context_id off the RETURNED turn — the
        # Collector mints it when the request omits one (Decision 5 gotcha).
        party = request.party or UNSCOPED_USER
        saved: dict[str, tuple[str, int]] = {}  # doc_id -> (filename, version)
        for sub in submissions:
            version = await persist_submission(
                self._store,
                party=party,
                context_id=turn.context_id,
                submission=sub,
                resolver=self._resolver,
            )
            if version is not None:
                saved[sub.doc_id] = (filename_for(sub), version)
        # Rewrite every ledger entry that has a persisted artifact (Decision 8).
        for entry in turn.status.entries:
            if entry.artifact is None:
                continue
            mapped = saved.get(entry.doc_id)
            if mapped is None:
                continue  # unresolvable byte source: leave the ref unchanged
            filename, version = mapped
            entry.artifact.uri = scoped_uri(
                self._base_url,
                context_id=turn.context_id,
                filename=filename,
                version=version,
            )

        # COLL-2: the advisory proposal rides the Collector turn (also on the HITL
        # degrade path, which emits this turn; a parked task emits the leg's turn).
        await self._propose(turn, request.process, doctypes)

        if park and any(e.gate is Gate.hitl_review for e in turn.status.entries):
            await self._execute_hitl_handoff(
                context,
                event_queue,
                message,
                turn=turn,
                party=party,
                doctypes=doctypes,
                results=results,
            )
            return
        await self._emit_turn(context, event_queue, turn, message)

    async def _execute_rfp(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        message: Any,
        request: CollectRequest,
    ) -> None:
        """S0-arch-19: open / update / read the RFP of this exchange on the durable board.

        Every refusal is raised BEFORE any event is enqueued (on a new task the SDK
        answers a JSON-RPC error and creates nothing): a file part →
        ``rfp_submissions_unsupported`` (checked before any store access); an invalid
        context id → ``invalid_rfp_context``; a ``ValueError`` from the RFP core →
        ``invalid_rfp_request: <text>`` (all ``InvalidParamsError``); an exhausted CAS
        conflict → ``InvalidRequestError`` ``rfp_changed_concurrently``. The whole read →
        apply → put runs on one worker thread (thread-local CAS versions; the loop never
        blocks on the DB).
        """
        assert self._rfp is not None  # guarded by the caller
        if any(p.HasField("raw") or p.HasField("url") for p in message.parts):
            raise InvalidParamsError(message=RFP_SUBMISSIONS_TEXT)
        try:
            cid = rfp_context_id(request, context.context_id)
        except InvalidRfpContext as e:
            raise InvalidParamsError(message=INVALID_RFP_CONTEXT_TEXT) from e
        try:
            turn = await asyncio.to_thread(
                open_or_update_rfp, self._rfp, request, context_id=cid
            )
        except StaleExchangeWrite as e:
            raise InvalidRequestError(
                message=RFP_CONFLICT_TEXT.format(rfp_id=cid)
            ) from e
        except ValueError as e:
            raise InvalidParamsError(message=INVALID_RFP_REQUEST_PREFIX + str(e)) from e
        await self._emit_turn(context, event_queue, turn, message)

    async def _propose(
        self, turn: ExchangeTurn, process: str, doctypes: list[str]
    ) -> None:
        """COLL-2: replace the turn's per-entry outstanding with the advisory
        proposal (:func:`bridge.satisfaction.propose_outstanding`; never raises,
        never sets ``done``). No-op without a bound assessor or a satisfaction rule.
        """
        if self._assessor is None:
            return  # checked FIRST: the default edge never reads skills for this
        rule = satisfaction_for(process)
        if not rule:
            return
        turn.status.outstanding = await propose_outstanding(
            turn.status.entries,
            rule=rule,
            candidate_doctypes=doctypes,
            assessor=self._assessor,
        )

    async def _execute_hitl_handoff(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        message: Any,
        *,
        turn: ExchangeTurn,
        party: str,
        doctypes: list[str],
        results: dict[str, ExtractionResult],
    ) -> None:
        """R3: hand a Collector turn with a ``hitl_review`` entry to the LLM leg,
        which parks the task on ``request_human_review`` (D5, D7).

        - Session key = the artifact key (D9): ``(party, turn.context_id)`` — the
          Collector may have minted the context id.
        - The leg is seeded with the Collector entries, the Bridge-decided label
          space and one ``source="collector"`` recorded signal per hitl entry (the
          gate pin accepts a re-gating); the Collector entries are also persisted
          for this task (``REVIEW_TASKS_KEY``) so the resume rebuilds the turn.
        - Parked → the projected turn (the hitl entry ``pending``/
          ``awaiting_human_review``, ``terminal=False``) + INPUT_REQUIRED whose
          status message carries ``reason_code`` + ``review_call_id`` and whose
          server-authored ``task.metadata[META_REVIEW_SESSION]`` scopes the review
          to this task.
        - Degrade (the model parks nothing, or the leg fails): finish exactly as
          report mode does — the Collector turn, never worse.

        Gotcha: the model may call any leg tool here (e.g. ``handle_deletion_request``);
        the S3-edge-5 erase scoping still applies.
        """
        assert self._llm_leg is not None  # guarded by the caller (park)
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[message] if message is not None else [],
                )
            )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=_text_message(LLM_WORKING_TEXT, context.context_id),
        )
        # Lazy (as ``build_a2a_app`` imports the leg): the deterministic edge never
        # pays the ADK import cost.
        from bridge.edge.llm_leg import pending_review_calls
        from bridge.gatepin import recorded_from_result

        hitl = [e for e in turn.status.entries if e.gate is Gate.hitl_review]
        text = HITL_HANDOFF_TEXT + "".join(
            f" {e.doc_id} ({e.reason_code})." for e in hitl
        )
        signals = [
            recorded_from_result(
                results[e.doc_id],
                doc_id=e.doc_id,
                source="collector",
                candidate_doctypes=doctypes,
            )
            for e in hitl
            if e.doc_id in results
        ]
        pending: list[str] = []
        projected: ExchangeTurn | None = None
        try:
            result = await self._llm_leg.run(
                party=party,
                context_id=turn.context_id,
                message=text,
                label_space=doctypes,
                seed_signals=signals,
                seed_entries=turn.status.entries,
                review_task_id=context.task_id,
            )
            pending = pending_review_calls(result.events)
            projected = result.turn
        except Exception:
            logger.exception("HITL handoff failed for task %s", context.task_id)
            pending = []

        if not pending or projected is None:
            # Degrade to report mode: the Collector turn, completed (never worse).
            await updater.add_artifact(
                encode_exchange_turn(turn), name="exchange_turn", last_chunk=True
            )
            await updater.complete(message=status_message_for(turn))
            return

        await updater.add_artifact(
            encode_exchange_turn(projected), name="exchange_turn", last_chunk=True
        )
        session = {
            "user_id": result.key.user_id,
            "session_id": result.key.session_id,
            "call_ids": list(pending),
        }
        await self._park(updater, context.context_id, pending[0], session)

    async def _park(
        self,
        updater: TaskUpdater,
        context_id: str,
        next_call_id: str,
        session: dict[str, Any],
    ) -> None:
        """INPUT_REQUIRED on a pending review (D6/D7): Bridge-authored text,
        ``reason_code`` + ``review_call_id`` on the status message (readable without
        tasks/get) and the FULL ``META_REVIEW_SESSION`` on ``task.metadata``
        (``MergeFrom`` replaces top-level keys)."""
        await updater.update_status(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            message=_text_message(
                PAUSED_REVIEW_TEXT,
                context_id,
                reason_code=REASON_HUMAN_REVIEW,
                extra={META_REVIEW_CALL_ID: next_call_id},
            ),
            metadata={
                META_REVIEW_CALL_ID: next_call_id,
                META_REVIEW_SESSION: session,
            },
        )

    async def _execute_resume_review(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        scope: ReviewScope,
        resolution: HumanReviewResolution,
    ) -> None:
        """R6: resume the parked invocation with a validated resolution (D-h step 4).

        WORKING → ``LlmLeg.resume`` (a fresh Runner over the durable session; same
        invocation id) → the rebuilt turn replaces the task's ``exchange_turn``
        artifact (same artifact id) → INPUT_REQUIRED with the next pending call id
        when reviews remain on this task, else COMPLETED with the reviewer's message
        and the resolved entry's reason code (D6). A failure after WORKING → FAILED.
        """
        assert self._llm_leg is not None  # a scope implies a bound leg
        context_id = context.context_id
        updater = TaskUpdater(event_queue, context.task_id, context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=_text_message(LLM_WORKING_TEXT, context_id),
        )
        try:
            result = await self._llm_leg.resume(
                scope.key,
                call_id=resolution.call_id,
                resolution=resolution.resolution,
                message=resolution.message,
                reviewer=resolution.reviewer,
                task_id=context.task_id,
                call_ids=scope.call_ids,
            )
        except Exception:
            logger.exception("HITL resume failed for task %s", context.task_id)
            await updater.failed(message=_text_message(LLM_FAILED_TEXT, context_id))
            return

        review = result.review
        await updater.add_artifact(
            encode_exchange_turn(review.turn),
            artifact_id=_turn_artifact_id(context.current_task),
            name="exchange_turn",
            last_chunk=True,
        )
        if review.pending:
            session = {
                "user_id": scope.key.user_id,
                "session_id": scope.key.session_id,
                "call_ids": list(review.call_ids),
            }
            await self._park(updater, context_id, review.pending[0], session)
            return
        resolved = review.resolved.get(resolution.call_id)
        status_message = _text_message(
            resolution.message,
            context_id,
            reason_code=resolved.reason_code if resolved is not None else None,
        )
        state = self._status_for(review.turn)
        await updater.update_status(state, message=status_message)

    async def _execute_llm_leg(
        self, context: RequestContext, event_queue: EventQueue, message: Any
    ) -> None:
        """R4: hand a text-only intent to the ``document_bridge`` LlmAgent leg.

        Lifecycle (D7): SUBMITTED (if new) → WORKING → COMPLETED | INPUT_REQUIRED —
        the model call is genuine in-connection work, so WORKING is honest here. A
        leg failure marks the task FAILED (never left stuck in WORKING). Every status
        text is Bridge-authored (D6): the projected tool message, else
        ``summarize_turn`` when entries exist, else ``LLM_UNROUTED_TEXT`` — model
        text never reaches the wire; the final status message carries
        ``metadata.reason_code`` (D6, :func:`llm_status`). The session/turn key is
        the A2A ``context_id`` and the party resolves from the shared artifact store
        (D9). The gate's label space is Bridge-decided (:meth:`_llm_label_space`),
        never model-supplied.
        """
        if self._llm_leg is None:  # bind_edge forbids hybrid without a leg
            raise RuntimeError("LLM route reached without a bound LLM leg")
        context_id = context.context_id
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[message] if message is not None else [],
                )
            )
        updater = TaskUpdater(event_queue, context.task_id, context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=_text_message(LLM_WORKING_TEXT, context_id),
        )

        text = " ".join(
            t.strip() for t in ph.get_text_parts(message.parts) if t and t.strip()
        )
        try:
            result = await self._llm_leg.run(
                party=None,
                context_id=context_id,
                message=text,
                label_space=self._llm_label_space(text),
            )
        except Exception:
            logger.exception("LLM leg failed for task %s", context.task_id)
            await updater.failed(message=_text_message(LLM_FAILED_TEXT, context_id))
            return

        turn = result.turn
        await updater.add_artifact(
            encode_exchange_turn(turn),
            name="exchange_turn",
            last_chunk=True,
        )
        status_text, reason_code = llm_status(result.projection, turn)
        status_message = _text_message(status_text, context_id, reason_code=reason_code)
        state = self._status_for(turn)
        if state == TaskState.TASK_STATE_COMPLETED:
            await updater.complete(message=status_message)
        else:
            await updater.update_status(state, message=status_message)

    def _build_path_a_turn(self, message: Any) -> ExchangeTurn:
        """PATH-A validate-only: schema-validate the structured assertion + dispose it
        into a one-entry terminal turn — NO extraction seam call, NO artifact persistence
        (a structured response carries no bytes).

        A malformed assertion (missing/extra required key) raises ``ValidationError`` at
        schema validation → a rejected ``LedgerEntry`` carrying the distinct
        ``schema_invalid`` reason code, so the "schema-validated" step is visibly distinct
        from a disposition-driven reject.
        """
        request = decode_intake(message)  # process/context_id (no extraction)
        doctypes = self._candidate_doctypes(request.process)
        context_id = request.context_id or uuid.uuid4().hex
        assertion = decode_assertion(message)
        doc_id = assertion.fields.doctype or "assertion"
        try:
            entry = patha.validate_only_entry(
                assertion, doc_id=doc_id, candidate_doctypes=doctypes
            )
        except ValidationError as e:
            entry = LedgerEntry(
                doc_id=doc_id,
                doctype=assertion.fields.doctype,
                disposition=Disposition.rejected,
                gate=Gate.unsupported,
                reason_code="schema_invalid",
                message=(
                    "The structured extraction assertion failed schema validation "
                    f"and was rejected before disposition: {e.error_count()} error(s)."
                ),
            )
        status = project_collection_status([entry])
        return ExchangeTurn(context_id=context_id, status=status, terminal=True)

    async def _emit_turn(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        turn: ExchangeTurn,
        message: Any,
    ) -> None:
        """Enqueue the task (if new), add the ExchangeTurn artifact, set task state.

        Lifecycle of the deterministic legs (Path-A, Collector; S3-edge-1 D7): the
        one-turn collect completes synchronously, so these legs emit SUBMITTED →
        COMPLETED|INPUT_REQUIRED and never stream an intermediate WORKING state (a
        synthetic one would manufacture progress that didn't happen). The LLM leg
        (``_execute_llm_leg``) does emit WORKING — its model call is genuine work. On
        the completing path a new task opens per round under the same exchange
        context_id (see agents/address/interceptor.py; D8: not collapsed).
        """
        # 1.x requires a brand-new task's first enqueued event to be a Task,
        # else the server raises InvalidAgentResponseError.
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[message] if message is not None else [],
                )
            )

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        # last_chunk=True matters (D4 / consumer gotcha): a *partial* artifact
        # event is streamed but never persisted, so a downstream gate reading the
        # session couldn't see it.
        await updater.add_artifact(
            encode_exchange_turn(turn),
            name="exchange_turn",
            last_chunk=True,
        )

        state = self._status_for(turn)
        status_message = status_message_for(turn)
        if state == TaskState.TASK_STATE_COMPLETED:
            await updater.complete(message=status_message)
        else:
            # Non-terminal turn (terminal=False) → INPUT_REQUIRED, the park signal.
            await updater.update_status(state, message=status_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # P4-5: tasks/cancel is the app's explicit terminal close (DUR-6) —
        # transitions any non-terminal task (WORKING/INPUT_REQUIRED, including an
        # escalated stall) to CANCELED; an already-terminal task is not cancelable
        # (the SDK handler enforces both).
        """Cancel an in-flight A2A collection task and emit a cancellation event."""
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()

    # ------------------------------------------------------------------ #
    def _candidate_doctypes(self, process: str) -> list[str]:
        """Decision 5: consolidated source in ``bridge.edge.skills``.

        Reads the process skill's candidate doctypes from the loaded skills
        metadata; an unresolved process → ``[]`` (empty label space; every document
        is rejected ``unsupported_doctype``). The real source is ``bridge.edge.skills.loaded_skills()``,
        a P6-6/SKILL-4 seam-in-waiting.
        """
        return candidate_doctypes_for(process)

    def _llm_label_space(self, text: str) -> list[str] | None:
        """The deterministic label space for an R4 turn: the process resolved from
        the text by the deterministic intake resolver (``parse_text_request``), else
        ``None`` — the gate pin then refuses every gate call (fail closed). Decided by
        Bridge code, never by the model."""
        try:
            process = parse_text_request(text).process
        except ValueError:
            return None
        return self._candidate_doctypes(process)

    def _status_for(self, turn: ExchangeTurn) -> TaskState:
        """Map a turn to a task state (WIRE-3).

        Terminal turn → ``TASK_STATE_COMPLETED``; non-terminal turn →
        ``TASK_STATE_INPUT_REQUIRED`` — the resumable park signal. Keyed off
        ``ExchangeTurn.terminal`` (the transport turn state), which is the authoritative
        signal (Decision 1); a pending item alone does not park. The P1-4 one-turn
        Collector path always returns ``terminal=True``; the LLM leg returns
        ``terminal=False`` for an escalate or a paused human review, and the
        RFP opener's turn is non-terminal while it waits on carriers.
        """
        return (
            TaskState.TASK_STATE_COMPLETED
            if turn.terminal
            else TaskState.TASK_STATE_INPUT_REQUIRED
        )


def llm_status(projection: LlmProjection, turn: ExchangeTurn) -> tuple[str, str | None]:
    """D6: the R4 status text + ``status.message.metadata`` reason_code — both
    Bridge-decided, never model text.

    text: the winning tool's message, else ``summarize_turn`` when entries exist,
    else :data:`LLM_UNROUTED_TEXT`. reason_code: the projection's precedence winner
    (pending review > escalated > last of {deleted, rejected}); else ``None`` when
    the turn has entries (a gate-only turn — D6 row "—"); else
    :data:`~bridge.offscript.REASON_UNROUTED` (nothing ran, nothing disposed).
    """
    text = projection.status_text or (
        summarize_turn(turn) if turn.status.entries else LLM_UNROUTED_TEXT
    )
    if projection.reason_code is not None:
        return text, projection.reason_code
    return text, (None if turn.status.entries else REASON_UNROUTED)


def _turn_artifact_id(task: Any) -> str | None:
    """The id of the task's existing ``exchange_turn`` artifact (the last one), so a
    resumed turn REPLACES it; ``None`` (a fresh id) when the task has none."""
    if task is None:
        return None
    ids = [a.artifact_id for a in task.artifacts if a.name == "exchange_turn"]
    return ids[-1] if ids else None


def _text_message(
    text: str,
    context_id: str,
    *,
    reason_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Message:
    """A non-empty, Bridge-authored agent status message (mirrors
    ``status_message_for``; D3: an empty status message is dropped downstream).

    ``reason_code`` (D6) rides ``metadata[META_REASON_CODE]``; ``extra`` adds further
    Bridge-authored envelope keys (e.g. ``review_call_id``). Without either the
    metadata stays unset — never an empty Struct or a null value.
    """
    msg = ph.new_message(
        parts=[ph.new_text_part(text)],
        context_id=context_id,
        role=Role.ROLE_AGENT,
    )
    if reason_code:
        msg.metadata.update({META_REASON_CODE: reason_code})
    if extra:
        msg.metadata.update(extra)
    return msg
