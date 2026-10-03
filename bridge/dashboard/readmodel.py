"""Ops Dashboard read-model projection (P6-4) — pure (no fastapi).

The dashboard is a **read-model over the shared PortalStore** (the P6-2 Path-B session
store): it projects each ``PortalSessionRecord`` into an operator ``OpsExchange`` view,
derives the **HITL** and **escalation** queues, and streams the cross-exchange snapshot
over SSE. It mints no disposition and authors no prose — every ``reason_code``/``message``
is relayed verbatim from the graph (A2UI-1 content-not-pixels, even though this is SSE,
not A2UI).

**HITL vs escalation detection is NOT symmetric (§1.2, gates #3/#4 gotcha #1).**
- **HITL** is an **active park** — detect via ``rec.last_step.action is PathBAction.HITL``.
  The parked entry is on ``last_step.entry``, not yet in ``rec.ledger``; append it to the
  projected ``entries`` (mirror ``bridge/portal/session.py::_turn``) or the HITL entry is
  invisible.
- **Escalation** is **terminal in the ledger** — detect via a ``rec.ledger`` entry with
  ``reason_code == REASON_ESCALATED`` (the portal engine copies ``REASON_ESCALATED`` onto
  the finalized escalate entry, ``bridge/portal/session.py:121-133``). Do not try to
  detect escalation via ``last_step`` (it is never stored as an active park).

**Escalation is pending, never rejected (gate #4).** The escalation queue's entry is
``disposition==pending``/``REASON_ESCALATED`` (route-to-human). Only wrong doctype
(``passport-unsupported``) is a ``rejected`` **done** exchange; it does NOT appear in the
escalation queue.

Isolation: imports ``contract/`` + ``bridge.*`` + stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from bridge.offscript import REASON_ESCALATED
from bridge.pathb import PathBAction
from bridge.portal.store import PortalSessionRecord, PortalStore
from contract.enums import LifecycleStatus

if TYPE_CHECKING:
    from bridge.comparison import ProgramComparison
    from bridge.dashboard.board import ProgramBoard, RfpBoard
    from bridge.email_ingress.correlation import IntakeAddressing
    from bridge.rfp import RfpCollection, RfpStatusView

# The consumer RFP status API's shape version (``GET /rfps`` / ``GET /rfps/{id}``).
RFP_STATUS_API_VERSION = "1"


def project_exchange(rec: PortalSessionRecord) -> dict:
    """Project a ``PortalSessionRecord`` into an operator ``OpsExchange`` view.

    ``entries`` = the accumulated ledger + (if a non-terminal park is active) the parked
    entry, so the parked entry's ``reason_code``/``message`` reach the view (mirror
    ``bridge/portal/session.py::_turn``). Returns JSON-ready snake_case.
    """
    entries = list(rec.ledger)
    if rec.last_step is not None:
        entries.append(rec.last_step.entry)

    # status: in_flight if an active park exists, else done
    status = "in_flight" if rec.last_step is not None else "done"

    # hitl: True if the active park is a HITL park (gotcha #1)
    hitl = rec.last_step is not None and rec.last_step.action is PathBAction.HITL

    # escalated: True if any ledger entry has REASON_ESCALATED (gotcha #1)
    cap_escalated = any(e.reason_code == REASON_ESCALATED for e in rec.ledger)

    # lifecycle (P6-5): the SLA ladder's stamped lifecycle (``rec.lifecycle``) wins when set;
    # otherwise fall back to the existing cosmetic ledger derivation. ADDITIVE — a record with
    # the default ``on_track`` lifecycle and no REASON_ESCALATED entry projects byte-identically
    # to before, so the P6-4 dashboard suite does not flip.
    sla_escalated = rec.lifecycle is LifecycleStatus.escalated
    lifecycle = rec.lifecycle if rec.lifecycle is not LifecycleStatus.on_track else (
        LifecycleStatus.escalated if cap_escalated else LifecycleStatus.on_track
    )
    # The escalation-queue bool unions both sources: cap-driven (ledger) + SLA-driven (ladder).
    escalated = cap_escalated or sla_escalated

    return {
        "session_id": rec.session_id,
        "context_id": rec.context_id,
        "process": rec.process,
        "status": status,
        "lifecycle": lifecycle,
        "entries": [e.model_dump(mode="json") for e in entries],
        "hitl": hitl,
        "escalated": escalated,
    }


def project_rfp_exchange(rfp: RfpCollection, view: RfpStatusView) -> dict:
    """Project an RFP collection (S0-arch-11) into the same ``OpsExchange`` shape, plus an
    ``rfp`` key carrying the per-carrier x product matrix (``RfpStatusView``, verbatim).

    ``status`` is ``in_flight`` while any tracked item is still waiting on a carrier, else
    ``done`` — a collection state (nothing left to chase), NOT the agent's sense-B done.
    ``hitl`` is False: a mid-confidence quote is reviewed on its carrier's own portal
    session, not on the broker-side RFP row. ``escalated`` = the ladder (or a resubmit cap)
    reached ``escalated``, so the row joins the escalation queue. The view's ``as_of`` is
    dropped: it moves on every read, and ``sse_frames`` emits whenever the snapshot changes —
    keeping it would push a frame every tick instead of on a real state change.
    """
    return {
        "session_id": rfp.rfp_id,
        "context_id": rfp.context_id,
        "process": rfp.process,
        "status": "in_flight" if view.waiting else "done",
        "lifecycle": view.lifecycle,
        "entries": [e.model_dump(mode="json") for e in rfp.entries],
        "hitl": False,
        "escalated": view.lifecycle is LifecycleStatus.escalated,
        "rfp": view.model_dump(mode="json", exclude={"as_of"}),
    }


def project_rfp_status(view: RfpStatusView, *, intake: IntakeAddressing | None) -> dict:
    """The consumer read API's per-RFP status (S0-frontend-1, ``GET /rfps/{id}``).

    ``view.model_dump(mode="json")`` verbatim — ``rfp_id``, ``process``, ``context_id``,
    ``due_at``, ``as_of``, ``lifecycle``, ``ready``, ``waiting``, ``counts``, ``carriers``,
    ``cells`` (carrier x product line → effective status, ``versions``, ``attempts``,
    ``escalated``, ``last_activity``, ``reason_code`` / ``message`` / ``field_reviews``
    relayed verbatim), ``unmatched`` — plus exactly:

    * ``api_version`` (``RFP_STATUS_API_VERSION``), and
    * on each ``carriers[i]``: ``reply_address`` / ``reply_subject_tag`` — the leg's intake
      address and subject tag, ``None`` when email intake is not enabled (``intake=None``).

    Unlike the dashboard row (``project_rfp_exchange``), ``as_of`` is KEPT: a REST consumer
    renders the due-date countdown against it, and REST has no diff-poll to churn.
    """
    body = view.model_dump(mode="json")
    body["api_version"] = RFP_STATUS_API_VERSION
    for carrier in body["carriers"]:
        party = carrier["party"]
        carrier["reply_address"] = (
            intake.reply_address(view.rfp_id, party) if intake else None
        )
        carrier["reply_subject_tag"] = (
            intake.reply_subject_tag(view.rfp_id, party) if intake else None
        )
    return body


def list_exchanges(store: PortalStore, *, rfps: RfpBoard | None = None) -> list[dict]:
    """List all exchanges (project each record [+ each RFP on an injected board])."""
    exchanges = [project_exchange(rec) for rec in store.list()]
    if rfps is not None:
        exchanges += [project_rfp_exchange(r, v) for r, v in rfps.items()]
    return exchanges


def hitl_queue(store: PortalStore, *, rfps: RfpBoard | None = None) -> list[dict]:
    """The HITL queue: exchanges with an active HITL park."""
    return [x for x in list_exchanges(store, rfps=rfps) if x["hitl"]]


def escalation_queue(store: PortalStore, *, rfps: RfpBoard | None = None) -> list[dict]:
    """The escalation queue: exchanges with a REASON_ESCALATED entry (or an escalated RFP)."""
    return [x for x in list_exchanges(store, rfps=rfps) if x["escalated"]]


def project_comparison(comparison: ProgramComparison) -> dict:
    """Project a ``ProgramComparison`` into protocol-only JSON (content-not-pixels).

    Returns ``comparison.model_dump(mode="json")`` verbatim: already snake_case and
    JSON-ready, with each leg's nested ``quote`` + ``verdict.gaps`` (rule_id / message /
    expected / actual) coming through unchanged. The Bridge surfaces the comparison; THIS
    is the data the S1-benefits-7 view arranges + labels — it authors no prose (A2UI-1
    verbatim relay, even though this rides SSE/REST, not A2UI).
    """
    return comparison.model_dump(mode="json")


def readmodel_snapshot(
    store: PortalStore,
    *,
    board: ProgramBoard | None = None,
    rfps: RfpBoard | None = None,
) -> dict:
    """The whole cross-exchange read-model snapshot (exchanges + queues [+ programs]).

    When ``board`` is injected, adds a ``"programs"`` key carrying the recomputed program
    comparisons (recompute-on-read → landed revisions surface). When ``rfps`` is injected,
    RFP collections join ``exchanges`` (projected at the board's clock, so an item turning
    overdue surfaces on the next tick). **When both are None the dict is byte-identical to
    before** — the P6-4 dashboard suite must not flip.

    **Projected ONCE per snapshot (S0-perf-1).** The queues are filters over the same
    projection (exactly what ``hitl_queue`` / ``escalation_queue`` compute), so the
    store and the RFP board are projected once — not three times — per SSE tick, and
    all three keys share one ``clock()`` reading. At RFP scale (10 carriers x 5 products
    carrying 1000-row quotes) each projection dumps megabytes of rows, so the triple
    projection tripled the per-tick cost for no new information.
    """
    exchanges = list_exchanges(store, rfps=rfps)
    snapshot = {
        "exchanges": exchanges,
        "hitl": [x for x in exchanges if x["hitl"]],
        "escalation": [x for x in exchanges if x["escalated"]],
    }
    if board is not None:
        snapshot["programs"] = [project_comparison(c) for c in board.comparisons()]
    return snapshot


def _frame(d: dict) -> str:
    """Format one snapshot as an SSE ``data:`` frame."""
    return f"data: {json.dumps(d)}\n\n"


async def sse_frames(
    store: PortalStore,
    *,
    board: ProgramBoard | None = None,
    rfps: RfpBoard | None = None,
    interval: float = 0.02,
    max_ticks: int = 2000,
):
    """Yield SSE frames for the cross-exchange read-model until ``max_ticks`` deadline.

    Emits the current snapshot immediately, then re-reads on each tick and yields a frame
    whenever the snapshot changed. When ``board`` is injected the snapshot carries a
    ``programs`` key; because the snapshot is re-read each tick, a board mutation (a revise
    landing via ``board.put``) surfaces on the next tick automatically. **Unlike the console
    (which closes on terminal status), the dashboard stream has no terminal state** — it
    MUST close on the ``max_ticks`` deadline (a plain ``return``, no error frame).
    ``await asyncio.sleep(interval)`` yields the loop so concurrent ``store.put`` /
    ``board.put``s become visible on the next tick.
    """
    last = readmodel_snapshot(store, board=board, rfps=rfps)
    yield _frame(last)

    for _ in range(max_ticks):
        await asyncio.sleep(interval)
        snap = readmodel_snapshot(store, board=board, rfps=rfps)
        if snap != last:
            yield _frame(snap)
            last = snap

    # Deadline hit -> close the stream (long-lived stream, no terminal state)
    return
