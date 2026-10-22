"""Read the latest collected ``ExchangeTurn`` off the shared session (AGENT-7).

The gate needs the collected ``ExchangeTurn``. ``RemoteA2aAgent`` surfaces a task
**artifact** as an ADK event whose content parts are the A2A→genai converted
parts, with ``event.partial = not last_chunk``. Both edges emit the artifact
``last_chunk=True``, so the event is **non-partial and persisted** to the session;
a partial event streams to the live caller but is never persisted (§ D4).

This reader scans the session events newest-first, skips ``partial`` events, and
returns the first content part that decodes as an ``ExchangeTurn`` via the hardened
consumer decoder ``wire.extract_exchange_turn`` (select-by-metadata then shape;
``extra="forbid"`` rejects a wrong dict; never by index).

The primary path reads off ``ctx.session.events`` (the AGENT-7 invariant: the gate
sees the persisted terminal ledger). As a documented fallback the reader also
honours a turn a node recorded to ``ctx.state["latest_turn"]`` (the presenter /
observability seam, and the stub tests).
"""

from __future__ import annotations

from agents.address.wire import extract_exchange_turn
from contract.wire import ExchangeTurn, decode_wire


def read_latest_exchange_turn(ctx) -> ExchangeTurn | None:
    """Return the most-recent ``ExchangeTurn`` on the shared session, or ``None``.

    Primary path: scan ``ctx.session.events`` newest-first, skip ``partial``
    events, decode each event's content parts with ``extract_exchange_turn``.
    Fallback: a turn recorded to ``ctx.state["latest_turn"]``.
    """
    session = getattr(ctx, "session", None)
    events = getattr(session, "events", None) or []
    for event in reversed(list(events)):
        if getattr(event, "partial", False):
            continue
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        turn = extract_exchange_turn(parts)
        if turn is not None:
            return turn

    # Fallback: a turn recorded to state (presenter/observability seam).
    state = getattr(ctx, "state", None)
    recorded = None
    if state is not None:
        try:
            recorded = state.get("latest_turn")
        except Exception:
            recorded = None
    if isinstance(recorded, dict):
        try:
            return decode_wire(ExchangeTurn, recorded)
        except Exception:
            return None
    return None
