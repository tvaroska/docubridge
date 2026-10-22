"""The send-path ``RequestInterceptor`` (AGENT-6/5) — resume re-detection.

ADK's ``RemoteA2aAgent`` natively re-parks a resumed task by reading the parked
``task_id``/``context_id`` off the matching function-**call** event's
``custom_metadata`` — but only when ``ctx.session.events[-1].author == "user"``.
Under a ``Workflow`` the graph orchestrator appends an orchestration event **after**
the user ``FunctionResponse``, so native detection returns ``None`` and the agent
rebuilds a *fresh* message with **no ``task_id``**; the peer opens a brand-new task
instead of resuming the parked one.

This module fixes that with an ordering-robust detector (``pending_resume_target``)
and a ``before_request`` interceptor that stamps the empty outbound message with the
parked ids so the peer resumes the **same** task (AGENT-6) under the **same**
exchange ``context_id`` (AGENT-5). It only fills fields the native path left empty
— it never clobbers ids the native code intentionally set.

``A2aRemoteAgentConfig``/``RequestInterceptor`` from
``google.adk.a2a.agent.config`` are **not** ``@experimental`` and are not in the
experimental register catalog — no register change (see ``bridge/adk``). Never
imports ``bridge/`` (isolation convention).
"""

from __future__ import annotations

from typing import Any

from google.adk.a2a.agent.config import RequestInterceptor

# The ``a2a:`` metadata keys ``RemoteA2aAgent`` stamps on every relayed event
# (A2A_METADATA_PREFIX = "a2a:"); the parked function-call event carries these.
A2A_TASK_ID = "a2a:task_id"
A2A_CONTEXT_ID = "a2a:context_id"

# Session-state key the gate threads the durable exchange context id into
# (mirrors ``agents.address.gate.STATE_EXCHANGE_CONTEXT_ID``).
STATE_EXCHANGE_CONTEXT_ID = "exchange_context_id"


def _function_response_ids(event: Any) -> list[str]:
    """Return the ``function_response.id``s on an event (helper, then parts)."""
    ids: list[str] = []
    getter = getattr(event, "get_function_responses", None)
    if callable(getter):
        try:
            for fr in getter() or []:
                fid = getattr(fr, "id", None)
                if fid:
                    ids.append(fid)
        except Exception:
            ids = []
    if ids:
        return ids
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", None) or []:
        fr = getattr(part, "function_response", None)
        fid = getattr(fr, "id", None)
        if fid:
            ids.append(fid)
    return ids


def _function_call_ids(event: Any) -> list[str]:
    """Return the ``function_call.id``s on an event (helper, then parts)."""
    ids: list[str] = []
    getter = getattr(event, "get_function_calls", None)
    if callable(getter):
        try:
            for fc in getter() or []:
                cid = getattr(fc, "id", None)
                if cid:
                    ids.append(cid)
        except Exception:
            ids = []
    if ids:
        return ids
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", None) or []:
        fc = getattr(part, "function_call", None)
        cid = getattr(fc, "id", None)
        if cid:
            ids.append(cid)
    return ids


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def pending_resume_target(ctx: Any, agent_name: str) -> tuple[str | None, str | None]:
    """Ordering-robust version of ADK's native resume detection (the AGENT-6 fix).

    Find the most-recent ``user`` event carrying a ``function_response`` that has
    **not yet been consumed by this agent** — i.e. no event authored by
    ``agent_name`` appears *after* it in the session. Match that response's ``id``
    to the earlier function-**call** event and read the parked
    (``a2a:task_id``, ``a2a:context_id``) off the call event's ``custom_metadata``.

    Returns ``(task_id, context_id)`` — either may be ``None``. Returns
    ``(None, None)`` when there is no pending resume (a fresh first turn), or when
    the resume signal exists but the ids can't be recovered.
    """
    session = getattr(ctx, "session", None)
    events = list(getattr(session, "events", None) or [])

    # Walk newest-first: the first user-FR reached before any agent-authored event
    # is the unconsumed resume signal (ordering-robust, not "events[-1]").
    fr_id: str | None = None
    for event in reversed(events):
        if getattr(event, "author", None) == agent_name:
            # An agent-authored event: any earlier user-FR is already consumed.
            break
        if getattr(event, "author", None) == "user":
            ids = _function_response_ids(event)
            if ids:
                fr_id = ids[0]
                break
    if fr_id is None:
        return (None, None)

    # Match the FR id to the earlier function-call event carrying the parked ids.
    for event in events:
        if fr_id in _function_call_ids(event):
            metadata = getattr(event, "custom_metadata", None) or {}
            task_id = _as_str(metadata.get(A2A_TASK_ID))
            context_id = _as_str(metadata.get(A2A_CONTEXT_ID))
            return (task_id, context_id)
    return (None, None)


def resume_stamping_interceptor(agent_name: str) -> RequestInterceptor:
    """Build the ``before_request`` interceptor that re-stamps a resumed task.

    Installed on the collect node via ``A2aRemoteAgentConfig(request_interceptors=
    [...])``. Only fills empty fields — the ``completing``/``park=false`` path opens
    a new task per round under the same context, so a blank ``task_id`` on a
    non-resume turn is left blank (never force-reused).
    """

    async def before_request(ctx: Any, a2a_request: Any, params: Any):
        task_id, context_id = pending_resume_target(ctx, agent_name)

        # AGENT-6: re-stamp the parked task so the peer resumes the SAME task
        # (native detection missed it under the Workflow event ordering).
        if task_id and not getattr(a2a_request, "task_id", None):
            a2a_request.task_id = task_id

        # AGENT-5: continue the SAME exchange context across rounds. Prefer the
        # parked context_id; else the exchange id the gate threaded into state.
        session = getattr(ctx, "session", None)
        state = getattr(session, "state", None)
        state_ctx = None
        if state is not None:
            try:
                state_ctx = state.get(STATE_EXCHANGE_CONTEXT_ID)
            except Exception:
                state_ctx = None
        resolved_ctx = context_id or _as_str(state_ctx)
        if resolved_ctx and not getattr(a2a_request, "context_id", None):
            a2a_request.context_id = resolved_ctx

        return a2a_request, params

    return RequestInterceptor(before_request=before_request)
