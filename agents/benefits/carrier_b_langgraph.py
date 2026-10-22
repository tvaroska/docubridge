"""Carrier B — a simulated benefits carrier built in **LangGraph** (S1-benefits-6).

Carrier B holds the coverage gap and drives the multi-turn revise loop. Its internal is
a genuine compiled ``langgraph`` ``StateGraph``: a classifier conditional edge routes a
first quote request to the ``quote_v0`` node (returns ``CARRIER_B_V0_DOC`` — holds the
``orthodontics`` exclusion the program standard flags) and a revise pushback to the
``revise_v2`` node (returns ``CARRIER_B_V2_DOC`` — gap closed). The nodes are pure
Python (no LLM, deterministic); the graph is compiled once at module import.

The ``AgentExecutor`` classifies the inbound turn (``wire.classify_turn``), invokes the
graph, loads the resulting quote, and emits it as a ``benefits-quote`` A2A artifact —
the same framework-neutral wire shape Carrier A emits. Statelessness (v0-vs-revised
decided purely from the turn text) keeps the graph a clean conditional and both carriers
deterministic.

External zone: imports ``a2a.*``, ``langgraph.*``, ``contract.*`` and the sibling
``agents.benefits.*`` — never ``bridge/``.
"""

from __future__ import annotations

from typing import TypedDict

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from langgraph.graph import END, START, StateGraph

from agents.benefits.quotes import CARRIER_B_V0_DOC, CARRIER_B_V2_DOC, load_quote
from agents.benefits.server import build_carrier_app, build_carrier_card
from agents.benefits.wire import classify_turn, encode_quote

_CARRIER_PARTY = "carrier-b"

# Node names.
_NODE_QUOTE_V0 = "quote_v0"
_NODE_REVISE_V2 = "revise_v2"


class CarrierBState(TypedDict):
    """Graph state: the classified turn in, the resolved corpus doc id out."""

    turn: str  # "quote" | "revise" (from wire.classify_turn)
    doc_id: str


def _route(state: CarrierBState) -> str:
    """Conditional edge: a revise turn → revise_v2, else quote_v0."""
    return _NODE_REVISE_V2 if state["turn"] == "revise" else _NODE_QUOTE_V0


def _quote_v0(state: CarrierBState) -> dict:
    """First quote: v0 (holds the orthodontics coverage gap)."""
    return {"doc_id": CARRIER_B_V0_DOC}


def _revise_v2(state: CarrierBState) -> dict:
    """Revised quote: v2 (orthodontics exclusion dropped → conformant)."""
    return {"doc_id": CARRIER_B_V2_DOC}


def _build_graph():
    """Compile Carrier B's StateGraph (classify → quote_v0 | revise_v2)."""
    graph = StateGraph(CarrierBState)
    graph.add_node(_NODE_QUOTE_V0, _quote_v0)
    graph.add_node(_NODE_REVISE_V2, _revise_v2)
    graph.add_conditional_edges(START, _route, [_NODE_QUOTE_V0, _NODE_REVISE_V2])
    graph.add_edge(_NODE_QUOTE_V0, END)
    graph.add_edge(_NODE_REVISE_V2, END)
    return graph.compile()


# Compiled once at import — the module-level internal graph. Exposed so the
# framework-diversity test can assert it is a compiled LangGraph without a server.
CARRIER_B_GRAPH = _build_graph()


class CarrierBExecutor(AgentExecutor):
    """A2A executor that invokes the LangGraph Carrier B graph and emits a quote."""

    def __init__(self, graph=None) -> None:
        self._graph = graph if graph is not None else CARRIER_B_GRAPH

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Execute an inbound benefits RFP quote turn on the LangGraph Carrier B agent."""
        turn = (
            classify_turn(context.message) if context.message is not None else "quote"
        )
        # graph.invoke is synchronous + CPU-only — safe inside the async executor.
        result = self._graph.invoke({"turn": turn})
        quote = load_quote(result["doc_id"])

        # 1.x new-task-first gotcha (copy bridge/edge/executor._emit_turn).
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
                parts=[
                    ph.new_text_part(f"benefits-quote from {_CARRIER_PARTY} ({turn})")
                ],
                context_id=context.context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel an in-flight RFP quote task on the LangGraph Carrier B agent."""
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def build_carrier_b_app(url: str = "http://carrier-b/"):
    """Build Carrier B's A2A Starlette app (LangGraph internal)."""
    card = build_carrier_card(url, party=_CARRIER_PARTY)
    return build_carrier_app(url, CarrierBExecutor(), card)
