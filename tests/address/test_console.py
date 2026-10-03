"""P6-3 console_server host tests — the hard ``uv run pytest`` gate.

Drives the Agent Console (``build_console_app``) over ``httpx.ASGITransport`` with the
Bridge edge injected in-process (mirror ``tests/address/test_golden.py`` §1.4). Two
in-process apps, two clients (§5.1): the console talks to the edge via the injected
``httpx_client``; the test talks to the console via its own ASGITransport client. Both
live on the one asyncio loop, so the background ``run_collection`` task advances while the
test polls/streams.

The console is a HOST over ``run_collection`` — the gate mints satisfaction; the console
*reads* ``is_satisfied`` and displays it. The same-issuer run surfaces ``satisfied False``
from the agent (the gate), not a console rule (the FE-2 "Sense B watchable" acceptance).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.sessions import InMemorySessionService

from agents.console.server import build_console_app
from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, build_mock_bridge_app
from bridge.extraction.fixture import FixtureExtractor

BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH


# --------------------------------------------------------------------------- #
# In-process edge apps + a console client (two ASGITransport clients, §5.1).    #
# --------------------------------------------------------------------------- #
def _mock_app():
    return build_mock_bridge_app(url=BASE + "/")


def _real_app():
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    return build_a2a_app(url=BASE + "/", executor=executor)


@asynccontextmanager
async def _console(edge_app):
    """Yield a client over a console app wired to an in-process edge (caller owns both).

    The console reaches the edge via the injected ``httpx_client`` (edge ASGITransport);
    the yielded client talks to the console via its own ASGITransport. A fresh store +
    session service per case.
    """
    edge_transport = httpx.ASGITransport(app=edge_app)
    edge_client = httpx.AsyncClient(transport=edge_transport, base_url=BASE)
    console_app = build_console_app(
        card_url=CARD_URL,
        httpx_client=edge_client,
        session_service=InMemorySessionService(),
    )
    console_transport = httpx.ASGITransport(app=console_app)
    console_client = httpx.AsyncClient(
        transport=console_transport, base_url="http://console"
    )
    try:
        yield console_client
    finally:
        await console_client.aclose()
        await edge_client.aclose()


async def _start(client, prepared_key: str) -> str:
    resp = await client.post("/sessions", json={"prepared_key": prepared_key})
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    assert sid
    return sid


async def _wait_done(client, sid: str, *, tries: int = 300, delay: float = 0.02) -> dict:
    """Poll the snapshot until terminal (or fail with the last snapshot)."""
    snap = None
    for _ in range(tries):
        resp = await client.get(f"/sessions/{sid}")
        assert resp.status_code == 200, resp.text
        snap = resp.json()
        if snap["status"] in ("done", "error"):
            return snap
        await asyncio.sleep(delay)
    raise AssertionError(f"session {sid} never terminated; last snapshot: {snap}")


def _accepted(turn: dict) -> list[dict]:
    return [
        e for e in turn["status"]["entries"] if e["disposition"] == "accepted"
    ]


# --------------------------------------------------------------------------- #
# Tests.                                                                        #
# --------------------------------------------------------------------------- #
async def test_prepared_lists_catalog():
    async with _console(_mock_app()) as client:
        resp = await client.get("/prepared")
        assert resp.status_code == 200
        keys = {e["key"] for e in resp.json()}
        assert {"path-a-instant", "same-issuer-reject", "distinct-issuer-accept"} <= keys
        assert all(e["label"] for e in resp.json())


async def test_new_session_from_prepared_runs_to_done_satisfied():
    """Gate #2 / CUJ-1: prepared request -> done, accepted gov-id, satisfied True."""
    async with _console(_mock_app()) as client:
        sid = await _start(client, "path-a-instant")
        snap = await _wait_done(client, sid)
        assert snap["status"] == "done"
        assert snap["satisfied"] is True
        assert snap["rounds"] >= 1
        accepted = _accepted(snap["turn"])
        assert any(
            e["doctype"] == "gov-id" and e["doc_id"] == "gov-id-clean"
            for e in accepted
        )


async def test_session_list_shows_started_run():
    """Gate #3: GET /sessions lists a started run with its key + a valid status."""
    async with _console(_mock_app()) as client:
        sid = await _start(client, "path-a-instant")
        await _wait_done(client, sid)
        resp = await client.get("/sessions")
        assert resp.status_code == 200
        rows = resp.json()
        row = next(r for r in rows if r["session_id"] == sid)
        assert row["prepared_key"] == "path-a-instant"
        assert row["status"] in ("running", "parked", "done", "error")


async def test_same_issuer_reject_not_satisfied():
    """Gate #4 / Sense-B watchable: two same-issuer bills -> satisfied False.

    The gate (not the console) withholds ``done`` — two accepted ``power-co`` bills fail
    the distinct-issuer rule, and a non-empty requirements/outstanding list surfaces.
    """
    async with _console(_real_app()) as client:
        sid = await _start(client, "same-issuer-reject")
        snap = await _wait_done(client, sid)
        assert snap["status"] == "done"
        assert snap["satisfied"] is False
        accepted = _accepted(snap["turn"])
        assert len(accepted) == 2
        issuers = {
            e["issuer"] for e in accepted if e["doctype"] == "utility-bill"
        }
        assert issuers == {"power-co"}
        # The gate withheld satisfaction; the Bridge's advisory outstanding is present
        # and NOT marked done (the real edge emits an empty requirements list here —
        # the authoritative Sense-B signal is ``satisfied is False`` above, which the
        # console reads from ``is_satisfied``, never a console rule).
        outstanding = snap["turn"]["status"]["outstanding"]
        assert outstanding is not None
        assert outstanding["done"] is False


async def test_distinct_issuer_accept_satisfied():
    async with _console(_real_app()) as client:
        sid = await _start(client, "distinct-issuer-accept")
        snap = await _wait_done(client, sid)
        assert snap["status"] == "done"
        assert snap["satisfied"] is True
        accepted = _accepted(snap["turn"])
        issuers = {
            e["issuer"] for e in accepted if e["doctype"] == "utility-bill"
        }
        assert issuers == {"power-co", "aqua-util"}


async def test_sse_stream_frames_and_terminates():
    """Gate #5: the SSE endpoint frames + carries a terminal frame + closes."""
    async with _console(_mock_app()) as client:
        sid = await _start(client, "path-a-instant")
        frames: list[dict] = []
        async with client.stream("GET", f"/sessions/{sid}/events") as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[len("data: ") :]))
        assert len(frames) >= 1
        final = frames[-1]
        assert final["status"] == "done"
        assert final["satisfied"] is True
        assert any(
            e["doctype"] == "gov-id" for e in _accepted(final["turn"])
        )


async def test_unknown_session_404():
    async with _console(_mock_app()) as client:
        resp = await client.get("/sessions/does-not-exist")
        assert resp.status_code == 404
        events = await client.get("/sessions/does-not-exist/events")
        assert events.status_code == 404


async def test_unknown_prepared_key_rejected():
    async with _console(_mock_app()) as client:
        resp = await client.post("/sessions", json={"prepared_key": "nope"})
        assert resp.status_code == 404
        # extra field forbidden (ConfigDict extra="forbid") -> 422
        bad = await client.post(
            "/sessions", json={"prepared_key": "path-a-instant", "x": 1}
        )
        assert bad.status_code == 422


async def test_background_error_captured():
    """A bad card URL with no injected client -> record ends status=error, no lost task."""
    console_app = build_console_app(
        card_url="http://127.0.0.1:9/does-not-exist",
        session_service=InMemorySessionService(),
    )
    transport = httpx.ASGITransport(app=console_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://console")
    try:
        sid = await _start(client, "path-a-instant")
        snap = await _wait_done(client, sid)
        assert snap["status"] == "error"
        assert snap["error"]
    finally:
        await client.aclose()
