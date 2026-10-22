"""Time-warp host acceptance suite (P6-5) — the hard ``uv run pytest`` gate.

Drives ``build_timewarp_app`` in-process over ``httpx.ASGITransport`` against a **shared**
``InMemoryPortalStore`` (mirror ``tests/test_dashboard.py``). Proves the host drives the
SLA proof end-to-end over HTTP and that the fired lifecycle is visible to the dashboard
read-model reading the SAME store:

- ``arm`` → one pending alarm at ``overdue_ticks`` (tick 1), record lifecycle ``on_track``;
- ``advance`` past each boundary → ``overdue`` → ``reminder`` → ``escalated``, one fired
  event per step, exactly one pending alarm between steps, no pending after ``escalated``;
- re-advancing never refires (zero-compute-between);
- the dashboard ``escalation_queue`` lists the exchange once SLA-escalated;
- ``reset`` returns the clock to 0, clears pending, and restores ``on_track``;
- backward advance → 400; unknown session → 404; pause rejects advance (409).

Fresh store + app per test. ``asyncio_mode="auto"`` (no marker on ``async def test_*``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from bridge.dashboard import readmodel
from bridge.portal import session as portal_engine
from bridge.portal.store import InMemoryPortalStore
from bridge.timewarp import build_timewarp_app
from contract.enums import LifecycleStatus

BASE = "http://timewarp"
PROCESS = "address-proof"
# address-proof SLA policy: overdue_ticks=1, reminder_ticks=2, escalate_ticks=3.
OVERDUE, REMINDER, ESCALATE = 1, 2, 3


@asynccontextmanager
async def _client(store=None):
    """An httpx client bound to a fresh time-warp app over a shared portal store."""
    shared_store = store or InMemoryPortalStore()
    app = build_timewarp_app(store=shared_store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        yield hc, shared_store


def _seed(store) -> str:
    """Create a fresh portal session record (address-proof) in the shared store."""
    rec = portal_engine.start_session(store, process=PROCESS)
    return rec.session_id


# --------------------------------------------------------------------------- #
# Gate: arm → one pending alarm at overdue_ticks, lifecycle on_track.         #
# --------------------------------------------------------------------------- #
async def test_arm_pends_overdue_step():
    async with _client() as (hc, store):
        sid = _seed(store)

        resp = await hc.post("/timewarp/arm", json={"session_id": sid})
        assert resp.status_code == 200, resp.text
        state = resp.json()

        assert state["now"] == 0
        assert state["paused"] is False
        # Exactly one pending alarm, at overdue_ticks, carrying the overdue step.
        assert len(state["pending"]) == 1
        assert state["pending"][0]["fire_at"] == OVERDUE
        assert state["pending"][0]["step"] == "overdue"
        # The armed exchange is on_track with no fired events yet.
        assert len(state["exchanges"]) == 1
        ex = state["exchanges"][0]
        assert ex["session_id"] == sid
        assert ex["lifecycle"] == "on_track"
        assert ex["events"] == []
        # The shared store record is on_track.
        assert store.get(sid).lifecycle is LifecycleStatus.on_track


# --------------------------------------------------------------------------- #
# Gate: advance drives on_track → overdue → reminder → escalated on the       #
#       shared store, one fired event per step, one pending between steps.     #
# --------------------------------------------------------------------------- #
async def test_advance_drives_ladder_on_shared_store():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})

        # Advance past overdue_ticks -> overdue, one pending at reminder_ticks, one event.
        state = (await hc.post("/timewarp/advance", json={"ticks": OVERDUE})).json()
        assert state["now"] == OVERDUE
        ex = state["exchanges"][0]
        assert ex["lifecycle"] == "overdue"
        assert [e["step"] for e in ex["events"]] == ["overdue"]
        assert len(state["pending"]) == 1
        assert state["pending"][0]["fire_at"] == REMINDER
        # The shared store record + the dashboard projection both show overdue.
        assert store.get(sid).lifecycle is LifecycleStatus.overdue
        assert (
            readmodel.project_exchange(store.get(sid))["lifecycle"]
            == LifecycleStatus.overdue
        )

        # Advance to reminder_ticks -> reminder, one pending at escalate_ticks.
        state = (
            await hc.post("/timewarp/advance", json={"ticks": REMINDER - OVERDUE})
        ).json()
        ex = state["exchanges"][0]
        assert ex["lifecycle"] == "reminder"
        assert [e["step"] for e in ex["events"]] == ["overdue", "reminder"]
        assert len(state["pending"]) == 1
        assert state["pending"][0]["fire_at"] == ESCALATE

        # Advance to escalate_ticks -> escalated, NO pending alarm (terminal stall).
        state = (
            await hc.post("/timewarp/advance", json={"ticks": ESCALATE - REMINDER})
        ).json()
        ex = state["exchanges"][0]
        assert ex["lifecycle"] == "escalated"
        assert ex["done"] is True
        assert [e["step"] for e in ex["events"]] == ["overdue", "reminder", "escalated"]
        assert state["pending"] == []
        assert store.get(sid).lifecycle is LifecycleStatus.escalated

        # The dashboard escalation queue now lists the exchange (SLA-driven).
        queue = readmodel.escalation_queue(store)
        assert [x["session_id"] for x in queue] == [sid]
        assert queue[0]["lifecycle"] == LifecycleStatus.escalated


# --------------------------------------------------------------------------- #
# Gate: advance_to (scrubber) fast-forwards straight to escalated.            #
# --------------------------------------------------------------------------- #
async def test_advance_to_fast_forwards_to_escalated():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})

        # Scrub straight to the deadline: all three steps fire, in order.
        state = (await hc.post("/timewarp/advance", json={"to": ESCALATE})).json()
        assert state["now"] == ESCALATE
        ex = state["exchanges"][0]
        assert ex["lifecycle"] == "escalated"
        assert [e["step"] for e in ex["events"]] == ["overdue", "reminder", "escalated"]
        assert state["pending"] == []


# --------------------------------------------------------------------------- #
# Gate: zero-compute-between — re-advancing without new ticks never refires.   #
# --------------------------------------------------------------------------- #
async def test_no_refire_on_readvance():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})

        await hc.post("/timewarp/advance", json={"ticks": OVERDUE})
        # Re-advance by 0 ticks: no new fires (still exactly one overdue event).
        state = (await hc.post("/timewarp/advance", json={"ticks": 0})).json()
        ex = state["exchanges"][0]
        assert [e["step"] for e in ex["events"]] == ["overdue"]
        assert len(state["pending"]) == 1


# --------------------------------------------------------------------------- #
# Gate: step advances one tick.                                               #
# --------------------------------------------------------------------------- #
async def test_step_advances_one_tick():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})

        state = (await hc.post("/timewarp/step")).json()
        assert state["now"] == 1
        assert state["exchanges"][0]["lifecycle"] == "overdue"


# --------------------------------------------------------------------------- #
# Gate: reset (replay) → clock 0, no pending, lifecycle back to on_track.     #
# --------------------------------------------------------------------------- #
async def test_reset_restores_on_track():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})
        await hc.post("/timewarp/advance", json={"to": ESCALATE})
        assert store.get(sid).lifecycle is LifecycleStatus.escalated

        state = (await hc.post("/timewarp/reset")).json()
        assert state["now"] == 0
        assert state["pending"] == []
        assert state["exchanges"] == []
        # The shared store record is restored to on_track; the escalation queue empties.
        assert store.get(sid).lifecycle is LifecycleStatus.on_track
        assert readmodel.escalation_queue(store) == []


# --------------------------------------------------------------------------- #
# Gate: pause rejects advance (409); resume re-enables it.                    #
# --------------------------------------------------------------------------- #
async def test_pause_rejects_advance():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})

        state = (await hc.post("/timewarp/pause")).json()
        assert state["paused"] is True

        resp = await hc.post("/timewarp/advance", json={"ticks": 1})
        assert resp.status_code == 409, resp.text

        resume = (await hc.post("/timewarp/resume")).json()
        assert resume["paused"] is False
        ok = await hc.post("/timewarp/advance", json={"ticks": 1})
        assert ok.status_code == 200, ok.text


# --------------------------------------------------------------------------- #
# Gate: error mapping — backward advance → 400, unknown session → 404.        #
# --------------------------------------------------------------------------- #
async def test_backward_advance_400():
    async with _client() as (hc, store):
        sid = _seed(store)
        await hc.post("/timewarp/arm", json={"session_id": sid})

        resp = await hc.post("/timewarp/advance", json={"ticks": -1})
        assert resp.status_code == 400, resp.text


async def test_arm_unknown_session_404():
    async with _client() as (hc, _store):
        resp = await hc.post("/timewarp/arm", json={"session_id": "nope"})
        assert resp.status_code == 404, resp.text


async def test_get_state():
    async with _client() as (hc, _store):
        resp = await hc.get("/timewarp/state")
        assert resp.status_code == 200, resp.text
        state = resp.json()
        assert state == {"now": 0, "paused": False, "pending": [], "exchanges": []}
