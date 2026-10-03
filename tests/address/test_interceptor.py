"""Unit tests for the send-path resume interceptor (AGENT-6/5).

Constructs synthetic session events that reproduce the ``Workflow`` event ordering
(an orchestration event appended *after* the user ``FunctionResponse``) that defeats
ADK's native ``events[-1].author == "user"`` resume detection, and proves the
ordering-robust ``pending_resume_target`` + ``before_request`` re-stamps the parked
``task_id``/``context_id``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.events.event import Event
from google.genai import types

from agents.address.interceptor import (
    A2A_CONTEXT_ID,
    A2A_TASK_ID,
    pending_resume_target,
    resume_stamping_interceptor,
)

AGENT = "collect"


def _fake_ctx(events, state=None):
    return SimpleNamespace(
        session=SimpleNamespace(events=list(events), state=state or {})
    )


def _fc_event(call_id, *, task_id=None, context_id=None, author=AGENT):
    """A relayed function-call event carrying the parked a2a ids in metadata."""
    metadata = {}
    if task_id is not None:
        metadata[A2A_TASK_ID] = task_id
    if context_id is not None:
        metadata[A2A_CONTEXT_ID] = context_id
    return Event(
        author=author,
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=AGENT, args={}))],
        ),
        custom_metadata=metadata or None,
    )


def _fr_event(call_id):
    """A user function-response event (the resume signal)."""
    return Event(
        author="user",
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=call_id, name=AGENT, response={"result": "resume"}
                    )
                )
            ],
        ),
    )


def _user_seed(text="scenario:path-b-two-bills"):
    return Event(
        author="user",
        content=types.Content(role="user", parts=[types.Part(text=text)]),
    )


def _orchestration_event():
    """A non-user, non-agent event appended after the FR (the Workflow bug §1.2)."""
    return Event(
        author="workflow",
        content=types.Content(role="model", parts=[types.Part(text="route")]),
    )


def _a2a_message(task_id="", context_id=""):
    """A minimal outbound A2A message stand-in (settable id fields)."""
    return SimpleNamespace(task_id=task_id, context_id=context_id)


# --------------------------------------------------------------------------- #
# pending_resume_target                                                        #
# --------------------------------------------------------------------------- #
def test_resume_detected_under_workflow_ordering():
    """FR is not events[-1] (orchestration event after it) — still detected."""
    ctx = _fake_ctx(
        [
            _user_seed(),
            _fc_event("fc1", task_id="T1", context_id="C1"),
            _fr_event("fc1"),
            _orchestration_event(),  # the event that breaks native detection
        ]
    )
    task_id, context_id = pending_resume_target(ctx, AGENT)
    assert task_id == "T1"
    assert context_id == "C1"


def test_fresh_first_turn_has_no_pending_resume():
    ctx = _fake_ctx([_user_seed()])
    assert pending_resume_target(ctx, AGENT) == (None, None)


def test_most_recent_unconsumed_fr_wins():
    """Two parked legs: the later one's ids are used."""
    ctx = _fake_ctx(
        [
            _user_seed(),
            _fc_event("fc1", task_id="T1", context_id="C1"),
            _fr_event("fc1"),
            _fc_event("fc2", task_id="T2", context_id="C2"),
            _fr_event("fc2"),
            _orchestration_event(),
        ]
    )
    task_id, context_id = pending_resume_target(ctx, AGENT)
    assert task_id == "T2"
    assert context_id == "C2"


def test_fr_already_consumed_by_agent_is_not_pending():
    """An agent-authored event after the FR means it's already been answered."""
    ctx = _fake_ctx(
        [
            _user_seed(),
            _fc_event("fc1", task_id="T1", context_id="C1"),
            _fr_event("fc1"),
            _fc_event("fc2", task_id="T2", context_id="C2"),  # agent event AFTER the FR
        ]
    )
    assert pending_resume_target(ctx, AGENT) == (None, None)


# --------------------------------------------------------------------------- #
# before_request stamping                                                      #
# --------------------------------------------------------------------------- #
async def test_before_request_stamps_task_and_context():
    ctx = _fake_ctx(
        [
            _user_seed(),
            _fc_event("fc1", task_id="T1", context_id="C1"),
            _fr_event("fc1"),
            _orchestration_event(),
        ]
    )
    interceptor = resume_stamping_interceptor(AGENT)
    msg = _a2a_message()
    out, params = await interceptor.before_request(ctx, msg, "params")
    assert out is msg
    assert params == "params"
    assert msg.task_id == "T1"
    assert msg.context_id == "C1"


async def test_before_request_noop_on_fresh_turn():
    ctx = _fake_ctx([_user_seed()])
    interceptor = resume_stamping_interceptor(AGENT)
    msg = _a2a_message()
    await interceptor.before_request(ctx, msg, None)
    assert msg.task_id == ""
    assert msg.context_id == ""


async def test_context_id_from_state_fallback():
    """Parked call event has task_id but no context_id — fall back to state (AGENT-5)."""
    ctx = _fake_ctx(
        [
            _user_seed(),
            _fc_event("fc1", task_id="T1"),  # no a2a:context_id
            _fr_event("fc1"),
            _orchestration_event(),
        ],
        state={"exchange_context_id": "CtxX"},
    )
    interceptor = resume_stamping_interceptor(AGENT)
    msg = _a2a_message()
    await interceptor.before_request(ctx, msg, None)
    assert msg.task_id == "T1"
    assert msg.context_id == "CtxX"


async def test_already_stamped_not_clobbered():
    """A task_id the native path set is preserved (never force-reused)."""
    ctx = _fake_ctx(
        [
            _user_seed(),
            _fc_event("fc1", task_id="T1", context_id="C1"),
            _fr_event("fc1"),
            _orchestration_event(),
        ]
    )
    interceptor = resume_stamping_interceptor(AGENT)
    msg = _a2a_message(task_id="Tnative", context_id="Cnative")
    await interceptor.before_request(ctx, msg, None)
    assert msg.task_id == "Tnative"
    assert msg.context_id == "Cnative"


async def test_non_resume_does_not_fabricate_task_id():
    """The completing/park=false path opens a new task — leave task_id blank."""
    ctx = _fake_ctx([_user_seed()], state={"exchange_context_id": "CtxX"})
    interceptor = resume_stamping_interceptor(AGENT)
    msg = _a2a_message()
    await interceptor.before_request(ctx, msg, None)
    assert msg.task_id == ""  # no fabricated task_id
    assert msg.context_id == "CtxX"  # but the exchange context is threaded


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
