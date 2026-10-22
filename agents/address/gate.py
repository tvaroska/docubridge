"""The Sense-B satisfaction rule + the deterministic gate node (AGENT-3).

``is_satisfied`` is the authoritative, pure, deterministic Address "done" rule — a
model never mints it. The gate ``FunctionNode`` reads the latest collected
``ExchangeTurn`` off the shared session, tracks a round counter, and routes the
graph (``ctx.route``) ``"done"`` or ``"again"`` — with a round ceiling that forces
``"done"`` so a non-terminating scenario fails fast (replaces
``LoopAgent.max_iterations``).

The gate owns Sense-B: it does **not** trust ``turn.terminal`` or
``turn.status.outstanding.done`` for the verdict (those are the Bridge's
transport/advisory signals). The accepted-ledger rule is the authority the tests
pin.
"""

from __future__ import annotations

from collections.abc import Callable

from google.adk.events import Event

from agents.address.turn_reader import read_latest_exchange_turn
from contract.enums import Disposition
from contract.wire import CollectionStatus

# Corpus doctypes for the address-proof process. P3-3 may move these into the
# skill definition; they are module constants here.
DOCTYPE_GOV_ID = "gov-id"
DOCTYPE_UTILITY_BILL = "utility-bill"

# Default round ceiling — how many collect/gate cycles before the gate forces
# "done" (a non-terminating scenario fails fast). Threaded via
# ``build_address_workflow(max_rounds=...)``.
DEFAULT_MAX_ROUNDS = 6

STATE_GATE_ROUNDS = "gate_rounds"
STATE_LATEST_TURN = "latest_turn"
# The durable, 1:1-with-casefile exchange id (design/collect-scenarios.md:
# "keeping context = keeping the context_id alive"). The gate threads it here from
# the decoded turn so the send-path interceptor can continue the SAME exchange
# across rounds (AGENT-5).
STATE_EXCHANGE_CONTEXT_ID = "exchange_context_id"


def is_satisfied(status: CollectionStatus) -> bool:
    """The authoritative Address Sense-B rule (pure, deterministic).

    Address is satisfied when, over the **accepted** ledger entries:

    - there is **≥1 accepted ``gov-id``**, **OR**
    - there are **≥2 accepted ``utility-bill`` entries from *distinct* issuers**
      (two bills from the same issuer do NOT satisfy — the CUJ-10 "same-issuer
      reject" half).

    Only ``accepted`` entries count; ``rejected``/``pending`` do not.
    """
    accepted = [e for e in status.entries if e.disposition == Disposition.accepted]
    if any(e.doctype == DOCTYPE_GOV_ID for e in accepted):
        return True
    bill_issuers = {
        e.issuer for e in accepted if e.doctype == DOCTYPE_UTILITY_BILL and e.issuer
    }
    return len(bill_issuers) >= 2


def make_gate_node_fn(max_rounds: int = DEFAULT_MAX_ROUNDS) -> Callable:
    """Return the gate node func with ``max_rounds`` captured in the closure.

    Kept as a factory so the ceiling is captured without state-schema plumbing;
    the returned func's signature is ``(ctx)`` only (so FunctionNode's
    ``parameter_binding="state"`` machinery stays out of the way).
    """

    def gate_node_fn(ctx) -> None:
        turn = read_latest_exchange_turn(ctx)

        rounds = ctx.state.get(STATE_GATE_ROUNDS, 0) + 1
        ctx.state[STATE_GATE_ROUNDS] = rounds
        if turn is not None:
            # Record the latest turn for the presenter / observability.
            ctx.state[STATE_LATEST_TURN] = turn.model_dump(mode="json")
            # AGENT-5: thread the exchange context_id so the interceptor and later
            # rounds continue the SAME A2A exchange (one context per casefile).
            if turn.context_id:
                ctx.state[STATE_EXCHANGE_CONTEXT_ID] = turn.context_id

        done = turn is not None and is_satisfied(turn.status)
        route = "done" if (rounds >= max_rounds or done) else "again"
        yield Event(route=route)

    return gate_node_fn
