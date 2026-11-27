"""The Bridge edge executor: ``CollectExecutor`` (an ``a2a-sdk`` AgentExecutor).

Structured traffic (Path-A extraction assertions, ``CollectRequest``s, and file
submissions) runs on the deterministic, model-free path. Under ``BRIDGE_EDGE_MODE=hybrid``
text-only intents route to the ``document_bridge`` ADK ``LlmAgent`` leg
(``bridge.edge.llm_leg``), parking ``hitl_review`` entries on ``request_human_review``
and admitting authorized ``HumanReviewResolution`` resumes via :meth:`CollectExecutor.admit`.
When ``build_a2a_app`` binds an ``RfpBoard`` (``BRIDGE_RFP_PROCESS``), matching
``CollectRequest`` processes route to ``bridge.edge.rfp_open``. See ``design/a2a-edge.md``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Task, TaskState
from a2a.utils.errors import (
    InvalidParamsError,
    InvalidRequestError,
    UnsupportedOperationError,
)
from google.adk.artifacts import BaseArtifactService

from bridge.collect import Collector, DocumentSubmission
from bridge.edge.artifacts import (
    UNSCOPED_USER,
    Resolver,
    build_artifact_store,
    resolve_bytes,
)
from bridge.edge.card import DEFAULT_URL
from bridge.edge.executor_hitl import (
    GUARD_REFUSE_TEXT,
    HITL_HANDOFF_TEXT,
    LLM_FAILED_TEXT,
    LLM_UNROUTED_TEXT,
    LLM_WORKING_TEXT,
    MALFORMED_RESOLUTION_TEXT,
    NO_PENDING_REVIEW_TEXT,
    PAUSED_REVIEW_TEXT,
    REVIEW_CALL_ID_MISMATCH_TEXT,
    REVIEWER_NOT_AUTHORIZED_TEXT,
    ExecutorHitlMixin,
    ReviewScope,
    _require_context_ids,
    _text_message,
    _turn_artifact_id,
    llm_status,
)
from bridge.edge.executor_paths import (
    build_path_a_turn,
    emit_turn_payload_summary,
    execute_rfp_turn,
    finalize_completed_turn_outcome,
    persist_turn_submissions,
    preresolve_submissions,
)
from bridge.edge.intake import decode_intake, parse_text_request
from bridge.edge.pathroute import EdgeMode, Route, classify_route
from bridge.edge.reviewers import resolve_reviewers
from bridge.edge.skills import (
    candidate_doctypes_for,
    hitl_mode_for,
    satisfaction_for,
)
from bridge.edge.wire import (
    decode_submissions,
    encode_exchange_turn,
    status_message_for,
)
from bridge.satisfaction import (
    SatisfactionAssessor,
    propose_outstanding,
)
from bridge.telemetry.outcome import (
    close_outcome,
    current_outcome_id,
    current_outcome_owner,
    resume_outcome,
    start_outcome,
)
from contract.enums import Gate
from contract.extraction import ExtractionResult
from contract.wire import (
    CollectRequest,
    ExchangeTurn,
)

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard
    from bridge.edge.llm_leg import LlmLeg
    from bridge.knowledge_catalog import KnowledgeCatalog
    from bridge.operational_store import OperationalStore
    from bridge.vault import AuditVaultStore

logger = logging.getLogger(__name__)


class CollectExecutor(ExecutorHitlMixin, AgentExecutor):
    """Edge executor: routes each message (``BRIDGE_EDGE_MODE``) to Path-A, the
    deterministic Collector, or the LLM leg."""

    def __init__(  # noqa: PLR0913 - keyword-only injectable collaborators
        self,
        collector: Collector | None = None,
        *,
        artifact_store: BaseArtifactService | None = None,
        base_url: str = DEFAULT_URL,
        resolver: Resolver = resolve_bytes,
        vault_store: AuditVaultStore | None = None,
        operational_store: OperationalStore | None = None,
        knowledge_catalog: KnowledgeCatalog | None = None,
        freshness_clock: Callable[[], date] | None = None,
    ) -> None:
        # Default Collector lazily resolves the extraction port via get_adapter
        # (P1-4). Injectable for tests.
        self._collector = collector or Collector(
            vault_store=vault_store,
            operational_store=operational_store,
            knowledge_catalog=knowledge_catalog,
        )
        if collector is not None and (
            vault_store is not None
            or operational_store is not None
            or knowledge_catalog is not None
        ):
            self.bind_enterprise_stores(
                vault_store=vault_store,
                operational_store=operational_store,
                knowledge_catalog=knowledge_catalog,
            )
        # ART-2: the artifact store is a native-service backend swap, not a port
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
        self._freshness_clock: Callable[[], date] | None = freshness_clock
        self._task_outcomes: dict[str, str] = {}
        self._caller_owned_tasks: set[str] = set()

    def bind_freshness_clock(self, clock: Callable[[], date] | None) -> None:
        """Bind a date clock so Collect evaluates ``validation.yaml`` ``within_days`` rules."""
        self._freshness_clock = clock

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
        reviewers: Iterable[str] | str | None = None,
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

    def bind_enterprise_stores(
        self,
        *,
        vault_store: AuditVaultStore | None = None,
        operational_store: OperationalStore | None = None,
        knowledge_catalog: KnowledgeCatalog | None = None,
    ) -> None:
        """Bind Zone 3–6 Enterprise Data Architecture stores onto the underlying Collector.

        When bound, accepted document entries on ``CollectExecutor`` turns are promoted
        into the Zone 3 WORM Audit Vault, Zone 3–4 Bi-Temporal Operational Store, and
        Zone 6 Knowledge Catalog lineage graph.
        """
        if vault_store is not None:
            self._collector._vault_store = vault_store
        if operational_store is not None:
            self._collector._operational_store = operational_store
        if knowledge_catalog is not None:
            self._collector._knowledge_catalog = knowledge_catalog

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
        cid = context.context_id or context.task_id or "a2a-task"
        default_owner = (
            os.environ.get("BRIDGE_APPHUB_APPLICATION_ID", "").strip() or "a2a-edge"
        )
        inbound_outcome = current_outcome_id()
        inbound_owner = current_outcome_owner() or default_owner
        stored_outcome = self._task_outcomes.get(cid)
        if inbound_outcome:
            self._caller_owned_tasks.add(cid)
            self._task_outcomes[cid] = inbound_outcome
            resume_outcome(inbound_outcome, owner=inbound_owner)
        elif stored_outcome:
            resume_outcome(stored_outcome, owner=default_owner)
        else:
            active_outcome = start_outcome(outcome_id=f"out_{cid}", owner=default_owner)
            self._task_outcomes[cid] = active_outcome
        message = context.message
        if message is None:
            raise InvalidParamsError(message="inbound A2A request is missing a message")
        route, scope = await self._route_for(message, context.current_task)

        # Dual-path routing (PATH-A/PATH-B): a structured extraction-assertion (no
        # file bytes) is validate-only — schema + disposition, NO extraction port
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

    async def admit(
        self, message: Message | None, task: Task | None, user: object
    ) -> None:
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
        self, message: Message, task: Task | None
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

    async def _preresolve_submissions(
        self, submissions: list[DocumentSubmission]
    ) -> tuple[list[DocumentSubmission], dict[str, tuple[bytes, str]]]:
        return await preresolve_submissions(submissions, self._resolver)

    async def _run_collect(
        self,
        request: CollectRequest,
        submissions: list[DocumentSubmission],
        doctypes: list[str],
        results_out: dict[str, ExtractionResult] | None,
    ) -> ExchangeTurn:
        kwargs: dict[str, Any] = {
            "documents": submissions,
            "candidate_doctypes": doctypes,
        }
        if results_out is not None:
            kwargs["results_out"] = results_out
        if self._freshness_clock is not None:
            kwargs["as_of"] = self._freshness_clock()
        return await self._collector.collect(request, **kwargs)

    async def _execute_collect(
        self, context: RequestContext, event_queue: EventQueue, message: Message
    ) -> None:
        """R2: the deterministic, model-free Collector path."""
        request = decode_intake(message)
        if self._rfp is not None and self._rfp.serves(request.process):
            await self._execute_rfp(context, event_queue, message, request)
            return
        submissions, resolved_payloads = await self._preresolve_submissions(
            decode_submissions(message)
        )
        doctypes = self._candidate_doctypes(request.process)
        park = (
            self._mode is EdgeMode.hybrid
            and self._llm_leg is not None
            and hitl_mode_for(request.process) == "park"
        )
        results: dict[str, ExtractionResult] = {}
        turn = await self._run_collect(
            request, submissions, doctypes, results if park else None
        )
        party = request.party or UNSCOPED_USER
        ctx_user = context.call_context.user if context.call_context else None
        caller_principal = (
            ctx_user.user_name
            if ctx_user is not None and ctx_user.is_authenticated and ctx_user.user_name
            else None
        )
        await persist_turn_submissions(
            store=self._store,
            base_url=self._base_url,
            turn=turn,
            submissions=submissions,
            resolved_payloads=resolved_payloads,
            resolver=self._resolver,
            party=party,
            caller_principal=caller_principal,
        )
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
        message: Message,
        request: CollectRequest,
    ) -> None:
        """Open, update, or read the RFP of this exchange on the durable board."""
        turn = await execute_rfp_turn(self._rfp, context, message, request)
        await self._emit_turn(context, event_queue, turn, message)

    async def _propose(
        self, turn: ExchangeTurn, process: str, doctypes: list[str]
    ) -> None:
        """Replace the turn's per-entry outstanding list with the advisory proposal when bound."""
        if self._assessor is None:
            return
        rule = satisfaction_for(process)
        if not rule:
            return
        turn.status.outstanding = await propose_outstanding(
            turn.status.entries,
            rule=rule,
            candidate_doctypes=doctypes,
            assessor=self._assessor,
        )

    def _build_path_a_turn(self, message: Message) -> ExchangeTurn:
        """Schema-validate and dispose a structured Path-A extraction assertion."""
        return build_path_a_turn(
            message,
            candidate_doctypes_fn=self._candidate_doctypes,
            collector=self._collector,
        )

    async def _emit_turn(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        turn: ExchangeTurn,
        message: Message | None,
    ) -> None:
        """Enqueue the task (if new), add the ExchangeTurn artifact, and set task state."""
        task_id, context_id = _require_context_ids(context)
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    task_id,
                    context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[message] if message is not None else [],
                )
            )
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.add_artifact(
            encode_exchange_turn(turn),
            name="exchange_turn",
            last_chunk=True,
        )
        state = self._status_for(turn)
        status_message = status_message_for(turn)
        emit_turn_payload_summary(turn, state)
        if state == TaskState.TASK_STATE_COMPLETED:
            finalize_completed_turn_outcome(
                context,
                turn,
                caller_owned_tasks=self._caller_owned_tasks,
                task_outcomes=self._task_outcomes,
            )
            await updater.complete(message=status_message)
        else:
            await updater.update_status(state, message=status_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # P4-5: tasks/cancel is the app's explicit terminal close (DUR-6) —
        # transitions any non-terminal task (WORKING/INPUT_REQUIRED, including an
        # escalated stall) to CANCELED; an already-terminal task is not cancelable
        # (the SDK handler enforces both).
        """Cancel an in-flight A2A collection task and emit a cancellation event."""
        cid = context.context_id or context.task_id or "a2a-task"
        outcome_id = (
            self._task_outcomes.pop(cid, None) or current_outcome_id() or f"out_{cid}"
        )
        close_outcome(
            "abandoned",
            subject=context.context_id,
            outcome_id=outcome_id,
            owner="a2a-edge",
        )
        task_id, context_id = _require_context_ids(context)
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel()

    def _candidate_doctypes(self, process: str) -> list[str]:
        """Decision 5: consolidated source in ``bridge.edge.skills``.

        Reads the process skill's candidate doctypes from the loaded skills
        metadata; an unresolved process → ``[]`` (empty label space; every document
        is rejected ``unsupported_doctype``). The real source is ``bridge.edge.skills.loaded_skills()``,
        a P6-6/SKILL-4 port-in-waiting.
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


__all__ = [
    "GUARD_REFUSE_TEXT",
    "HITL_HANDOFF_TEXT",
    "LLM_FAILED_TEXT",
    "LLM_UNROUTED_TEXT",
    "LLM_WORKING_TEXT",
    "MALFORMED_RESOLUTION_TEXT",
    "NO_PENDING_REVIEW_TEXT",
    "PAUSED_REVIEW_TEXT",
    "REVIEWER_NOT_AUTHORIZED_TEXT",
    "REVIEW_CALL_ID_MISMATCH_TEXT",
    "CollectExecutor",
    "ExecutorHitlMixin",
    "ReviewScope",
    "_text_message",
    "_turn_artifact_id",
    "llm_status",
]
