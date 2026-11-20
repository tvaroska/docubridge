"""The hybrid edge's LLM leg — the ``document_bridge`` LlmAgent behind the A2A edge.

S3-edge-1 D1 ratified the hybrid edge: structured traffic stays on the deterministic
Collector; intent-shaped traffic (off-script, real-erase deletion, the HITL park) is
handed to the ``document_bridge`` LlmAgent. This module is that leg's runtime — a
standalone, model-free-testable host for ``bridge.agent.build_app`` plus the pure
event → ``ExchangeTurn`` projection (re-exported from :mod:`bridge.edge.llm_projection`).
Routing into it (``BRIDGE_EDGE_MODE``) is the executor's concern, not this module's.

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

**Isolation guard:** ``bridge/edge/`` must NEVER import ``agents/``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

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
from bridge.edge import artifacts
from bridge.edge.llm_projection import (
    _RANK_ESCALATE as _RANK_ESCALATE,
)
from bridge.edge.llm_projection import (
    _RANK_OFFSCRIPT as _RANK_OFFSCRIPT,
)
from bridge.edge.llm_projection import (
    _RANK_REVIEW as _RANK_REVIEW,
)
from bridge.edge.llm_projection import (
    _RESOLUTIONS as _RESOLUTIONS,
)
from bridge.edge.llm_projection import (
    _USER_AUTHOR as _USER_AUTHOR,
)
from bridge.edge.llm_projection import (
    APP_NAME as APP_NAME,
)
from bridge.edge.llm_projection import (
    DELETE_TOOL as DELETE_TOOL,
)
from bridge.edge.llm_projection import (
    ESCALATE_TOOL as ESCALATE_TOOL,
)
from bridge.edge.llm_projection import (
    EXTRACTION_TOOL as EXTRACTION_TOOL,
)
from bridge.edge.llm_projection import (
    GATE_TOOL as GATE_TOOL,
)
from bridge.edge.llm_projection import (
    PAUSED_REVIEW_MESSAGE as PAUSED_REVIEW_MESSAGE,
)
from bridge.edge.llm_projection import (
    PROJECTED_TOOLS as PROJECTED_TOOLS,
)
from bridge.edge.llm_projection import (
    REJECT_TOOL as REJECT_TOOL,
)
from bridge.edge.llm_projection import (
    REVIEW_TASKS_KEY as REVIEW_TASKS_KEY,
)
from bridge.edge.llm_projection import (
    REVIEW_TOOL as REVIEW_TOOL,
)
from bridge.edge.llm_projection import (
    LlmProjection as LlmProjection,
)
from bridge.edge.llm_projection import (
    ReviewTurn as ReviewTurn,
)
from bridge.edge.llm_projection import (
    SessionKey as SessionKey,
)
from bridge.edge.llm_projection import (
    _basename as _basename,
)
from bridge.edge.llm_projection import (
    _review_calls as _review_calls,
)
from bridge.edge.llm_projection import (
    _review_target as _review_target,
)
from bridge.edge.llm_projection import (
    pending_review_calls as pending_review_calls,
)
from bridge.edge.llm_projection import (
    project_from_events as project_from_events,
)
from bridge.edge.llm_projection import (
    project_review_turn as project_review_turn,
)
from bridge.edge.llm_projection import (
    project_turn_from_events as project_turn_from_events,
)
from bridge.edge.llm_projection import (
    resolve_party as resolve_party,
)
from bridge.edge.llm_projection import (
    resolved_review_calls as resolved_review_calls,
)
from bridge.edge.llm_projection import (
    session_key as session_key,
)
from bridge.ports import Port, PortMode, get_adapter, resolve_mode
from contract.wire import ExchangeTurn, LedgerEntry

# Opt-in env toggle to stream LlmLeg turns through a deployed Gemini platform Agent Engine
# ReasoningEngine (async_stream_query) instead of an in-process Runner.
BRIDGE_AGENT_ENGINE_REMOTE_ENV = "BRIDGE_AGENT_ENGINE_REMOTE"


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


class _RemoteReasoningEngine(Protocol):
    """Minimal protocol for a remote Gemini platform Agent Engine handle."""

    def async_stream_query(
        self,
        *,
        user_id: str,
        session_id: str,
        message: dict[str, Any],
    ) -> AsyncIterator[Event | Mapping[str, Any]]: ...


def _default_remote_engine() -> _RemoteReasoningEngine | None:
    """Resolve a remote Gemini platform Agent Engine ``ReasoningEngine`` handle when
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
    return cast(_RemoteReasoningEngine, vertexai.agent_engines.get(resource_name))


def _coerce_event(item: Event | Mapping[str, Any]) -> Event:
    """Coerce an event yielded by ``Runner.run_async`` or remote
    ``ReasoningEngine.async_stream_query`` into an ADK ``Event``."""
    if isinstance(item, Event):
        return item
    return Event.model_validate(item)


class LlmLeg:
    """Runtime for the LLM leg: the resumable ``document_bridge`` App on a fresh
    ``Runner`` per invocation (or a remote Gemini platform Agent Engine ``ReasoningEngine``),
    over port-wired sessions, memory bank, and the shared artifact store.

    Every collaborator is injectable; the defaults are host-controlled (P7-8): the
    App from :func:`bridge.agent.build_app` (``model`` may be a name or a
    ``BaseLlm``), the session service from the sessions port, the memory service
    from the memory_bank port, and the artifact store from
    :func:`bridge.edge.artifacts.build_artifact_store`.
    """

    def __init__(  # noqa: PLR0913 - keyword-only injectable runtime collaborators
        self,
        *,
        model: str | BaseLlm | None = None,
        app: App | None = None,
        session_service: BaseSessionService | None = None,
        artifact_store: BaseArtifactService | None = None,
        memory_service: BaseMemoryService | None = None,
        remote_engine: _RemoteReasoningEngine | None = None,
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
    def remote_engine(self) -> _RemoteReasoningEngine | None:
        """Optional remote Gemini platform Agent Engine (`ReasoningEngine`) handle."""
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
        Gemini platform Agent Engine (`ReasoningEngine.async_stream_query`)."""
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

    async def run(  # noqa: PLR0913 - keyword-only invocation inputs
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

    # HITL park/resume (G / R6) — session reads never build a Runner
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

    async def resume(  # noqa: PLR0913 - keyword-only HITL resume inputs
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


__all__ = [
    "APP_NAME",
    "BRIDGE_AGENT_ENGINE_REMOTE_ENV",
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
    "LlmLeg",
    "LlmLegResult",
    "LlmProjection",
    "LlmResumeResult",
    "ReviewTurn",
    "SessionKey",
    "_basename",
    "_coerce_event",
    "_default_memory_service",
    "_default_remote_engine",
    "_review_calls",
    "_review_target",
    "_stored_review_entries",
    "pending_review_calls",
    "project_from_events",
    "project_review_turn",
    "project_turn_from_events",
    "resolve_party",
    "resolved_review_calls",
    "session_key",
]
