"""Document bridge agent — the root mediator built on stock ADK."""

from google.adk.agents import LlmAgent
from google.adk.apps.app import App, ResumabilityConfig
from google.adk.models import BaseLlm
from google.adk.planners import BuiltInPlanner
from google.adk.tools import FunctionTool, LongRunningFunctionTool
from google.genai import types

from bridge.disposition import run_disposition_gate
from bridge.extraction.agent import (
    REASONING_MODEL,
    WORKHORSE_MODEL,
    build_extraction_agent,
    resolve_model,
    resolve_reasoning_model,
)
from bridge.gatepin import pin_gate_args
from bridge.offscript import (
    escalate_submission,
    handle_deletion_request,
    reject_submission,
    request_human_review,
)

DEFAULT_MODEL = WORKHORSE_MODEL

__all__ = [
    "DEFAULT_MODEL",
    "INSTRUCTION",
    "REASONING_MODEL",
    "WORKHORSE_MODEL",
    "app",
    "build_app",
    "build_document_bridge",
    "resolve_model",
    "resolve_reasoning_model",
    "root_agent",
]

INSTRUCTION = """You are the document bridge agent, responsible for routing and mediating document submissions.

For any genuine candidate document you receive, first call the `extraction_agent` to read its fields and signals, then call `run_disposition_gate` with those signals and relay its verdict verbatim. You may NEVER accept, reject, or approve a document yourself — the gate is the sole authority on document disposition. The Bridge fills `run_disposition_gate`'s arguments from the signals it recorded when `extraction_agent` read the document, so a gate call made before any extraction is refused.

When `run_disposition_gate` returns a `hitl_review` verdict (a flagged field or a mid-confidence read), call `request_human_review` with the reason to park the submission for a person — do not accept or reject it yourself; relay the pending status and wait for the reviewer's response.

Not every message is a candidate document. Route off-script intents to the right tool: a deletion or erasure request → `handle_deletion_request`; a wrong, unrelated, or out-of-scope submission that is not a genuine candidate document → `reject_submission`; anything ambiguous or that needs a person → `escalate_submission`. These edge tools never touch the compliance decision: a genuine candidate document is still only accepted or rejected by `run_disposition_gate`, which you may never overrule."""


def build_document_bridge(model: str | BaseLlm | None = None) -> LlmAgent:
    """Build the document_bridge LlmAgent with extraction agent and disposition gate tools.

    Args:
        model: Override model — a model name or a ``BaseLlm`` instance (defaults to
            BRIDGE_MODEL env or gemini-2.5-flash). A ``BaseLlm`` instance is shared
            with the ``extraction_agent`` sub-agent (one model object for both
            agents; requests are told apart by the tools they offer).

    The gate inversion (CORE-2) is held by the agent itself: the root's
    ``before_tool_callback`` is :func:`bridge.gatepin.pin_gate_args`, which
    overwrites every ``run_disposition_gate`` argument (label space included) with
    the signals the Bridge recorded in session state, and refuses the call when
    there are none. It is always on — not an edge-only guard.

    Returns:
        A stock LlmAgent configured with extraction and gate tools.
    """
    effective_model = resolve_model(model)
    return LlmAgent(
        name="document_bridge",
        model=effective_model,
        instruction=INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        ),
        sub_agents=[build_extraction_agent(effective_model)],
        tools=[
            FunctionTool(func=run_disposition_gate),
            FunctionTool(func=handle_deletion_request),
            FunctionTool(func=reject_submission),
            FunctionTool(func=escalate_submission),
            LongRunningFunctionTool(func=request_human_review),
        ],
        before_tool_callback=pin_gate_args,
    )


def build_app(model: str | BaseLlm | None = None, *, is_resumable: bool = True) -> App:
    """Build a stock App with the document_bridge as the root agent.

    Mirrors ``agents/address/app.py::build_address_app``: the ``App`` carries
    ``ResumabilityConfig(is_resumable=True)`` so a ``Runner(app=...)`` can durably
    park on the ``request_human_review`` LongRunningFunctionTool and resume on an
    id-matched FunctionResponse. Resumability lives on the App, not the Runner
    (``design/long-running.md``, ``design/adk.md``). ``ResumabilityConfig`` is a
    registered experimental surface (TEST-4, ``bridge/adk/experimental.py``) and is
    imported from its registered path ``google.adk.apps.app``.

    The App name ``document_bridge`` is the ADK session/artifact ``app_name``; it is
    kept in lockstep with ``bridge.edge.artifacts.APP_NAME`` by test (the core never
    imports the edge).

    Args:
        model: Override model — a model name or a ``BaseLlm`` instance (defaults to
            BRIDGE_MODEL env or gemini-2.5-flash).
        is_resumable: The App's resumability switch (default True).

    Returns:
        A stock App with document_bridge as root_agent.
    """
    return App(
        name="document_bridge",
        root_agent=build_document_bridge(model),
        resumability_config=ResumabilityConfig(is_resumable=is_resumable),
    )


root_agent = build_document_bridge()
app = build_app()
