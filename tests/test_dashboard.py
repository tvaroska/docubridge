"""Ops Dashboard host acceptance suite (P6-4) — the hard ``uv run pytest`` gate.

Drives ``build_dashboard_app`` in-process over ``httpx.ASGITransport`` against a **shared
PortalStore** seeded via ``bridge.portal.session`` (the write path) — the minimal, real
integration proving the dashboard is a read-model over the running portal's store. Fresh
store + app per test. ``asyncio_mode="auto"`` (no marker on ``async def test_*``).

The 8 gates (from the plan):
1. ``uv run pytest`` fully green; existing tests unchanged; isolation-guard green.
2. In-flight + done: ``GET /exchanges`` lists Path-B exchanges with status, context_id,
   process, entries; ``GET /exchanges/{sid}`` shows the classified ledger verbatim.
3. HITL queue: a ``bill-aquautil-clear`` exchange appears in ``GET /queues/hitl`` (parked
   entry ``pending``); non-HITL exchanges do not.
4. Escalation queue: a ``bill-aquautil-blurry``×4 exchange appears in
   ``GET /queues/escalation`` with ``reason_code==REASON_ESCALATED``/``pending``, never
   ``rejected``; ``passport-unsupported`` (rejected) is absent.
5. Resolve: ``POST /exchanges/{sid}/resolve {accepted}`` on the HITL exchange (reusing
   ``bridge.portal.session.resolve``) clears the HITL queue, accepts the entry with the
   reviewer's message, and flips the exchange to ``done``; resolve with no park → 409.
6. SSE: ``GET /events`` is ``text/event-stream``, emits ≥1 read-model snapshot frame
   (``exchanges``/``hitl``/``escalation``), and closes (bounded ``max_ticks``) without
   hanging.
7. Frontend: ``pnpm`` suite (separate from pytest).
8. PLAN.md P6-4 flipped (commit step).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from bridge.dashboard import build_dashboard_app
from bridge.offscript import REASON_ESCALATED
from bridge.portal import session as portal_engine
from bridge.portal.store import InMemoryPortalStore

BASE = "http://dash"
PROCESS = "address-proof"


@asynccontextmanager
async def _client(store=None, max_ticks: int = 50):
    """An httpx client bound to a fresh dashboard app over a shared portal store."""
    shared_store = store or InMemoryPortalStore()
    app = build_dashboard_app(store=shared_store, max_ticks=max_ticks)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        yield hc, shared_store


# --------------------------------------------------------------------------- #
# Seed helpers: drive Path-B exchanges into the store via the portal engine.  #
# --------------------------------------------------------------------------- #
async def _seed_accept(store) -> str:
    """Seed a one-step accept (bill-powerco-clean → done)."""
    rec = portal_engine.start_session(store, process=PROCESS)
    await portal_engine.upload(store, rec.session_id, doc_id="d1", artifact_name="bill-powerco-clean")
    return rec.session_id


async def _seed_hitl(store) -> str:
    """Seed a HITL park (bill-aquautil-clear → hitl_review, pending)."""
    rec = portal_engine.start_session(store, process=PROCESS)
    await portal_engine.upload(store, rec.session_id, doc_id="d1", artifact_name="bill-aquautil-clear")
    return rec.session_id


async def _seed_escalate(store) -> str:
    """Seed an escalation (bill-aquautil-blurry ×4 → cap → escalate, pending)."""
    rec = portal_engine.start_session(store, process=PROCESS)
    for i in range(4):
        await portal_engine.upload(store, rec.session_id, doc_id=f"d{i}", artifact_name="bill-aquautil-blurry")
    return rec.session_id


async def _seed_reject(store) -> str:
    """Seed a one-step reject (passport-unsupported → done, rejected)."""
    rec = portal_engine.start_session(store, process=PROCESS)
    await portal_engine.upload(store, rec.session_id, doc_id="d1", artifact_name="passport-unsupported")
    return rec.session_id


# --------------------------------------------------------------------------- #
# Gate #2: In-flight + done exchanges, classified ledger verbatim.            #
# --------------------------------------------------------------------------- #
async def test_exchanges_lists_in_flight_and_done():
    """Gate #2: GET /exchanges lists Path-B exchanges with status, context_id, process, entries."""
    async with _client() as (hc, store):
        sid_accept = await _seed_accept(store)
        sid_hitl = await _seed_hitl(store)

        resp = await hc.get("/exchanges")
        assert resp.status_code == 200, resp.text
        exchanges = resp.json()
        assert len(exchanges) == 2

        # The list is newest-first (reversed), so hitl is first, accept second.
        hitl_ex = next(x for x in exchanges if x["session_id"] == sid_hitl)
        accept_ex = next(x for x in exchanges if x["session_id"] == sid_accept)

        # HITL exchange: in_flight, has a parked entry (pending)
        assert hitl_ex["status"] == "in_flight"
        assert hitl_ex["process"] == PROCESS
        assert hitl_ex["context_id"]
        assert len(hitl_ex["entries"]) > 0
        # The parked HITL entry is in the projected entries (last_step.entry appended).
        assert any(e["disposition"] == "pending" for e in hitl_ex["entries"])

        # Accept exchange: done, has an accepted entry
        assert accept_ex["status"] == "done"
        assert accept_ex["process"] == PROCESS
        assert len(accept_ex["entries"]) > 0
        assert any(e["disposition"] == "accepted" for e in accept_ex["entries"])


async def test_get_exchange_shows_classified_ledger():
    """Gate #2: GET /exchanges/{sid} shows the classified ledger verbatim."""
    async with _client() as (hc, store):
        sid = await _seed_accept(store)

        resp = await hc.get(f"/exchanges/{sid}")
        assert resp.status_code == 200, resp.text
        exchange = resp.json()
        assert exchange["session_id"] == sid
        assert exchange["status"] == "done"
        assert len(exchange["entries"]) > 0

        # The accepted entry's reason_code/message are relayed verbatim (content-not-pixels).
        accepted_entry = next(e for e in exchange["entries"] if e["disposition"] == "accepted")
        assert accepted_entry["doctype"] in ("utility-bill", "gov-id")
        assert accepted_entry["issuer"] == "power-co"
        assert accepted_entry["reason_code"]
        assert accepted_entry["message"]


async def test_unknown_exchange_404():
    """GET /exchanges/nope → 404."""
    async with _client() as (hc, _store):
        resp = await hc.get("/exchanges/nope")
        assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- #
# Gate #3: HITL queue — exchanges with an active HITL park.                   #
# --------------------------------------------------------------------------- #
async def test_hitl_queue():
    """Gate #3: GET /queues/hitl → exactly the HITL-parked exchanges, parked entry pending."""
    async with _client() as (hc, store):
        sid_hitl = await _seed_hitl(store)
        sid_accept = await _seed_accept(store)

        resp = await hc.get("/queues/hitl")
        assert resp.status_code == 200, resp.text
        queue = resp.json()
        assert len(queue) == 1

        hitl_ex = queue[0]
        assert hitl_ex["session_id"] == sid_hitl
        assert hitl_ex["hitl"] is True
        # The parked entry is pending.
        assert any(e["disposition"] == "pending" for e in hitl_ex["entries"])

        # The accept exchange is NOT in the HITL queue.
        assert sid_accept not in [x["session_id"] for x in queue]


# --------------------------------------------------------------------------- #
# Gate #4: Escalation queue — entries with REASON_ESCALATED, pending not      #
#          rejected. Wrong-doctype reject does NOT appear.                     #
# --------------------------------------------------------------------------- #
async def test_escalation_queue():
    """Gate #4: GET /queues/escalation → the escalated exchange with REASON_ESCALATED,
    pending, never rejected; passport-unsupported (rejected) is absent."""
    async with _client() as (hc, store):
        sid_escalate = await _seed_escalate(store)
        sid_reject = await _seed_reject(store)

        resp = await hc.get("/queues/escalation")
        assert resp.status_code == 200, resp.text
        queue = resp.json()
        assert len(queue) == 1

        esc_ex = queue[0]
        assert esc_ex["session_id"] == sid_escalate
        assert esc_ex["escalated"] is True
        # An entry has REASON_ESCALATED, disposition pending (route-to-human).
        escalated_entry = next(e for e in esc_ex["entries"] if e["reason_code"] == REASON_ESCALATED)
        assert escalated_entry["disposition"] == "pending"

        # NO entry in the escalated exchange is rejected (dual-path invariant).
        assert not any(e["disposition"] == "rejected" for e in esc_ex["entries"])

        # The reject exchange (passport-unsupported) is NOT in the escalation queue.
        assert sid_reject not in [x["session_id"] for x in queue]


# --------------------------------------------------------------------------- #
# Gate #5: Resolve — passthrough to bridge.portal.session.resolve, clears     #
#          the HITL queue, flips the exchange to done.                         #
# --------------------------------------------------------------------------- #
async def test_resolve_hitl_clears_queue_and_accepts():
    """Gate #5: POST /exchanges/{sid}/resolve {accepted} on the HITL exchange clears the
    HITL queue, accepts the entry with the reviewer's message, and flips to done."""
    async with _client() as (hc, store):
        sid = await _seed_hitl(store)

        # Before resolve: in HITL queue, in_flight.
        queue_before = (await hc.get("/queues/hitl")).json()
        assert len(queue_before) == 1
        assert queue_before[0]["session_id"] == sid

        # Resolve accepted.
        resp = await hc.post(
            f"/exchanges/{sid}/resolve",
            json={"resolution": "accepted", "message": "Reviewer approved."},
        )
        assert resp.status_code == 200, resp.text
        resolved = resp.json()
        assert resolved["status"] == "done"
        # The ledger has an accepted entry with the reviewer's message verbatim.
        accepted_entry = next(e for e in resolved["entries"] if e["disposition"] == "accepted")
        assert accepted_entry["message"] == "Reviewer approved."

        # After resolve: HITL queue is empty.
        queue_after = (await hc.get("/queues/hitl")).json()
        assert len(queue_after) == 0

        # GET /exchanges/{sid} shows done.
        exchange = (await hc.get(f"/exchanges/{sid}")).json()
        assert exchange["status"] == "done"


async def test_resolve_rejected():
    """Gate #5 optional: resolve a HITL park to rejected."""
    async with _client() as (hc, store):
        sid = await _seed_hitl(store)

        resp = await hc.post(
            f"/exchanges/{sid}/resolve",
            json={"resolution": "rejected", "message": "Reviewer rejected."},
        )
        assert resp.status_code == 200, resp.text
        resolved = resp.json()
        assert resolved["status"] == "done"
        # The ledger has a rejected entry with the reviewer's message.
        rejected_entry = next(e for e in resolved["entries"] if e["disposition"] == "rejected")
        assert rejected_entry["message"] == "Reviewer rejected."

        # HITL queue is empty.
        queue = (await hc.get("/queues/hitl")).json()
        assert len(queue) == 0


async def test_resolve_without_park_409():
    """Gate #5: POST …/resolve on an exchange with no park → 409."""
    async with _client() as (hc, store):
        sid = await _seed_accept(store)

        resp = await hc.post(
            f"/exchanges/{sid}/resolve",
            json={"resolution": "accepted", "message": "No park to resolve."},
        )
        assert resp.status_code == 409, resp.text


async def test_resolve_unknown_exchange_404():
    """POST /exchanges/nope/resolve → 404."""
    async with _client() as (hc, _store):
        resp = await hc.post(
            "/exchanges/nope/resolve",
            json={"resolution": "accepted", "message": "msg"},
        )
        assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- #
# Gate #6: SSE — text/event-stream, frames the read-model snapshot, closes.   #
# --------------------------------------------------------------------------- #
async def test_sse_stream_frames_and_closes():
    """Gate #6: GET /events returns text/event-stream; emits ≥1 framed snapshot
    (exchanges/hitl/escalation) and closes (bounded max_ticks) without hanging."""
    async with _client(max_ticks=50) as (hc, store):
        # Seed ≥1 exchange so the snapshot is non-empty.
        await _seed_accept(store)
        await _seed_hitl(store)

        async with hc.stream("GET", "/events") as resp:
            assert resp.status_code == 200, resp.text
            assert resp.headers["content-type"].startswith("text/event-stream")

            frames = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    frames.append(line[6:])  # strip "data: "

            # At least 1 frame emitted.
            assert len(frames) >= 1

            # The first frame parses to a dict with exchanges/hitl/escalation keys.
            import json
            first = json.loads(frames[0])
            assert "exchanges" in first
            assert "hitl" in first
            assert "escalation" in first

            # The seeded exchanges are present.
            assert len(first["exchanges"]) >= 2
            # One is in the HITL queue.
            assert len(first["hitl"]) == 1

            # The stream closed without hanging (bounded max_ticks).


# --------------------------------------------------------------------------- #
# S0-perf-1: the snapshot projects the store ONCE per tick.                   #
# --------------------------------------------------------------------------- #
async def test_snapshot_projects_once_and_matches_the_three_call_composition(monkeypatch):
    """``readmodel_snapshot`` calls ``list_exchanges`` once (not three times) and the
    snapshot equals the old ``list_exchanges`` / ``hitl_queue`` / ``escalation_queue``
    composition exactly (queues are filters over the one projection)."""
    import bridge.dashboard.readmodel as readmodel

    store = InMemoryPortalStore()
    await _seed_accept(store)
    await _seed_hitl(store)
    await _seed_escalate(store)
    await _seed_reject(store)

    expected = {
        "exchanges": readmodel.list_exchanges(store),
        "hitl": readmodel.hitl_queue(store),
        "escalation": readmodel.escalation_queue(store),
    }
    assert len(expected["hitl"]) == 1 and len(expected["escalation"]) == 1

    calls = []
    real = readmodel.list_exchanges

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(readmodel, "list_exchanges", counting)
    snapshot = readmodel.readmodel_snapshot(store)

    assert len(calls) == 1
    assert snapshot == expected
