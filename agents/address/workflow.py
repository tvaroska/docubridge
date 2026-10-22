"""``build_address_workflow`` — the collect→gate→collect conditional cycle (AGENT-1..4).

The Address service agent is an ADK ``Workflow`` graph:

    START ──▶ collect ──▶ gate ──again──▶ collect
                            └────done────▶ present

- **collect** — a ``RemoteA2aAgent`` reaching the Bridge by card URL (``collect.py``).
- **gate** — a deterministic ``FunctionNode`` that owns the Sense-B verdict and
  routes ``ctx.route`` ``"done"``/``"again"`` with a round ceiling (``gate.py``).
- **present** — a code pass-through presenter (AGENT-4) that sets the terminal
  ``ExchangeTurn`` as the graph output. The presenter never routes (routing already
  happened in the gate — which is *why* an ``LlmAgent`` presenter would be safe
  here; that drop-in is a future option, this is the code node).

Graph-validation facts that shape the edges (ADK 2.7.x): edges from ``START`` must
have ``route=None``; a node with a loop-back / multiple out-edges must use routed
edges. So the gate's two out-edges are routed ``"again"``/``"done"`` while
``START→collect`` and ``collect→gate`` are unrouted single out-edges.

Runs via ``Runner(node=workflow, ...)`` — the App-path + ``ResumabilityConfig``
durability is P4, not wired here.
"""

from __future__ import annotations

from google.adk.workflow import START, Edge, FunctionNode, Workflow

from agents.address.collect import build_bridge_remote_agent
from agents.address.gate import DEFAULT_MAX_ROUNDS, make_gate_node_fn
from agents.address.turn_reader import read_latest_exchange_turn


def present_node_fn(ctx) -> dict | None:
    """Presenter node (AGENT-4): return the terminal ``ExchangeTurn`` as graph output.

    Reads the latest terminal turn off the shared session (falling back to the
    turn the gate recorded to state) and returns it as a JSON-mode dict so
    ``FunctionNode`` emits ``Event(output=...)``. Never routes — routing already
    happened in the gate; an ``LlmAgent`` presenter could drop in here unchanged.
    """
    turn = read_latest_exchange_turn(ctx)
    return None if turn is None else turn.model_dump(mode="json")


def build_address_workflow(
    card_url: str,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    httpx_client=None,
    collect_node=None,
) -> Workflow:
    """Build the Address ``Workflow`` (the four-edge conditional cycle).

    Args:
        card_url: Bridge Agent Card URL for the collect node.
        max_rounds: Round ceiling — force ``"done"`` after this many collect/gate
            cycles (fail fast on a non-terminating scenario).
        httpx_client: Test seam forwarded to ``build_bridge_remote_agent`` (points
            the collect node at an in-process mock over ``ASGITransport``).
        collect_node: Test seam — inject a stub collect ``FunctionNode`` (or any
            ``BaseNode``) instead of the live ``RemoteA2aAgent``, so routing/ceiling
            can be proven without a network or a live park.

    Returns:
        A validated ``Workflow`` (construction runs ADK's graph validator).
    """
    collect = collect_node or build_bridge_remote_agent(
        card_url, httpx_client=httpx_client
    )
    gate = FunctionNode(func=make_gate_node_fn(max_rounds), name="gate")
    present = FunctionNode(func=present_node_fn, name="present")

    return Workflow(
        name="address",
        edges=[
            Edge(from_node=START, to_node=collect),
            Edge(from_node=collect, to_node=gate),
            Edge(from_node=gate, to_node=collect, route="again"),
            Edge(from_node=gate, to_node=present, route="done"),
        ],
    )
