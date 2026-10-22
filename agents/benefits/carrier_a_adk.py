"""Carrier A — a simulated benefits carrier built in **Google ADK** (S1-benefits-6).

Carrier A is the conformant Path-A one-shot: it returns ``CARRIER_A_DOC`` (USD/en, no
coverage gap) on every quote request, and re-emits the same unchanged quote on a revise
turn (A has no gap, so it is never pushed back on — see the plan §3). Its internal is a
genuine ``google.adk.agents.BaseAgent`` — a **deterministic** agent whose
``_run_async_impl`` yields the quote as JSON, hosted through a stock ADK ``Runner`` +
``InMemorySessionService``. There is **no ``LlmAgent`` and no Gemini/API key** (mirrors
``bridge/edge/executor.py`` Decision 1: a model-free, testable agent).

The ``AgentExecutor`` runs the ADK agent through the Runner, pulls the quote JSON it
produced, and emits it as a ``benefits-quote`` A2A artifact — the framework-neutral wire
shape ``agents.benefits.wire`` defines (identical to Carrier B's).

External zone: imports ``a2a.*``, ``google.adk.*``, ``google.genai.*``, ``contract.*``
and the sibling ``agents.benefits.*`` — never ``bridge/``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.benefits.quotes import CARRIER_A_DOC, load_quote
from agents.benefits.server import build_carrier_app, build_carrier_card
from agents.benefits.wire import encode_quote
from contract.extraction import ExtractionResult

_APP_NAME = "carrier-a"
_CARRIER_PARTY = "carrier-a"


class CarrierAAgent(BaseAgent):
    """A deterministic ADK ``BaseAgent`` that yields Carrier A's quote as JSON.

    No model, no tools — ``_run_async_impl`` yields one ``Event`` whose content carries
    ``load_quote(CARRIER_A_DOC)`` serialized to JSON. Carrier A is conformant and
    stateless: it emits the same quote for a first request and for a revise pushback.
    """

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        quote = load_quote(CARRIER_A_DOC)
        payload = json.dumps(quote.model_dump(mode="json"))
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=payload)]),
        )


# Module-level internal agent so the framework-diversity test can inspect it without
# standing up a server (it asserts this is a google.adk BaseAgent).
CARRIER_A_AGENT = CarrierAAgent(name="carrier_a")


async def _produce_quote(agent: BaseAgent, prompt: str) -> ExtractionResult:
    """Run the ADK agent through a stock Runner and parse the quote JSON it yields."""
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=_APP_NAME, user_id="bridge", session_id="quote"
    )
    runner = Runner(agent=agent, app_name=_APP_NAME, session_service=session_service)
    text: str | None = None
    async for event in runner.run_async(
        user_id="bridge",
        session_id="quote",
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    text = part.text
    if text is None:
        raise RuntimeError("Carrier A ADK agent produced no quote")
    return ExtractionResult.model_validate(json.loads(text))


class CarrierAExecutor(AgentExecutor):
    """A2A executor that runs the ADK Carrier A agent and emits a benefits-quote."""

    def __init__(self, agent: BaseAgent | None = None) -> None:
        self._agent = agent if agent is not None else CARRIER_A_AGENT

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Execute an inbound benefits RFP quote turn on the ADK Carrier A agent."""
        prompt = (
            " ".join(ph.get_text_parts(context.message.parts))
            if context.message is not None
            else ""
        )
        quote = await _produce_quote(self._agent, prompt)

        # 1.x requires a brand-new task's first enqueued event to be a Task, else the
        # server raises InvalidAgentResponseError (copy bridge/edge/executor._emit_turn).
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.add_artifact(
            encode_quote(quote), name="benefits-quote", last_chunk=True
        )
        await updater.complete(
            message=ph.new_message(
                parts=[ph.new_text_part(f"benefits-quote from {_CARRIER_PARTY}")],
                context_id=context.context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel an in-flight RFP quote task on the ADK Carrier A agent."""
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def build_carrier_a_app(url: str = "http://carrier-a/") -> Any:
    """Build Carrier A's A2A Starlette app (Google ADK internal)."""
    card = build_carrier_card(url, party=_CARRIER_PARTY)
    return build_carrier_app(url, CarrierAExecutor(), card)
