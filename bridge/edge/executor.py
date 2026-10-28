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
from typing import TYPE_CHECKING, Any

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState
from a2a.utils.errors import (
    InvalidParamsError,
    InvalidRequestError,
    UnsupportedOperationError,
)
from google.adk.artifacts import BaseArtifactService
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
    _text_message,
    _turn_artifact_id,
    llm_status,
)
from bridge.edge.intake import decode_intake, parse_text_request
from bridge.edge.pathroute import EdgeMode, Route, classify_route, decode_assertion
from bridge.edge.reviewers import resolve_reviewers
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
    decode_submissions,
    encode_exchange_turn,
    status_message_for,
)
from bridge.exchange_store import StaleExchangeWrite
from bridge.satisfaction import SatisfactionAssessor, propose_outstanding
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionResult
from contract.wire import (
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
)

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard
    from bridge.edge.llm_leg import LlmLeg

logger = logging.getLogger(__name__)


class CollectExecutor(ExecutorHitlMixin, AgentExecutor):
    """Edge executor: routes each message (``BRIDGE_EDGE_MODE``) to Path-A, the
    deterministic Collector, or the LLM leg."""

    def __init__(
        self,
        collector: Collector | None = None,
        *,
        artifact_store: BaseArtifactService | None = None,
        base_url: str = DEFAULT_URL,
        resolver: Resolver = resolve_bytes,
        vault_store: Any = None,
        operational_store: Any = None,
        knowledge_catalog: Any = None,
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

    def bind_enterprise_stores(
        self,
        *,
        vault_store: Any = None,
        operational_store: Any = None,
        knowledge_catalog: Any = None,
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
        message = context.message
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

    def _build_path_a_turn(self, message: Any) -> ExchangeTurn:
        """PATH-A validate-only: schema-validate the structured assertion + dispose it
        into a one-entry terminal turn — NO extraction port call, NO artifact persistence
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
            if entry.disposition is Disposition.accepted and (
                self._collector._vault_store is not None
                or self._collector._operational_store is not None
                or self._collector._knowledge_catalog is not None
            ):
                from bridge.user_interaction import ingest_accepted_submission

                entity_id = request.party or context_id
                ingested = ingest_accepted_submission(
                    entry,
                    entity_id=entity_id,
                    context_id=context_id,
                    filename=f"{doc_id}.json",
                    vault_store=self._collector._vault_store,
                    operational_store=self._collector._operational_store,
                    knowledge_catalog=self._collector._knowledge_catalog,
                )
                entry = ingested.entry
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
