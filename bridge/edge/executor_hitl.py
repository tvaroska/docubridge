"""HITL park/resume, review scope validation, and LLM leg execution mixin for CollectExecutor."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Role, TaskState
from a2a.utils.errors import InvalidParamsError, InvalidRequestError
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from bridge.edge.reviewers import is_authorized_reviewer
from bridge.edge.wire import (
    META_REASON_CODE,
    META_REVIEW_CALL_ID,
    META_REVIEW_SESSION,
    decode_human_review_resolution,
    encode_exchange_turn,
    status_message_for,
    summarize_turn,
)
from bridge.offscript import REASON_HUMAN_REVIEW, REASON_UNROUTED
from contract.enums import Gate
from contract.extraction import ExtractionResult
from contract.wire import ExchangeTurn, HumanReviewResolution

if TYPE_CHECKING:
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


class ExecutorHitlMixin:
    """Mixin implementing HITL review park/resume (R3/R6/G) and LLM leg dispatch (R4)."""

    _llm_leg: LlmLeg | None
    _reviewers: frozenset[str]

    def _llm_label_space(self, text: str) -> list[str] | None:
        """Resolve the deterministic doctype label space for an R4 turn (implemented by CollectExecutor)."""
        raise NotImplementedError

    def _status_for(self, turn: ExchangeTurn) -> TaskState:
        """Map an ExchangeTurn to an A2A TaskState (implemented by CollectExecutor)."""
        raise NotImplementedError

    async def _has_pending_review(self, context: RequestContext) -> bool:
        """Whether the task holds an unanswered ``request_human_review`` call parked
        on it (the G guard / R6 resume session read; hybrid mode only)."""
        scope = await self._review_scope(context.current_task)
        return scope is not None and bool(scope.pending)

    async def _review_scope(self, task: Any) -> ReviewScope | None:
        """The parked task's :class:`ReviewScope`, else ``None``."""
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
        """R6 checks, in order (D-h), all before any task event."""
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
        which parks the task on ``request_human_review`` (D5, D7)."""
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
        """INPUT_REQUIRED on a pending review (D6/D7)."""
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
        """R6: resume the parked invocation with a validated resolution (D-h step 4)."""
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
        """R4: hand a text-only intent to the ``document_bridge`` LlmAgent leg."""
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


def llm_status(projection: LlmProjection, turn: ExchangeTurn) -> tuple[str, str | None]:
    """D6: the R4 status text + ``status.message.metadata`` reason_code — both
    Bridge-decided, never model text."""
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
    """A non-empty, Bridge-authored agent status message."""
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
