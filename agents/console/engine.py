"""Agent-Console engine (P6-3) — a HOST over ``agents.address.driver.run_collection``.

The console does **only** (§2a): (1) turn a prepared-request key into ``run_collection``
kwargs; (2) launch ``run_collection`` as a background task over a **console-owned**
session service + a fixed ``(user_id, session_id)``; (3) read the run's terminal turn +
gate round counter back off that session service; (4) compute
``satisfied = is_satisfied(turn.status)`` and a ``running|parked|done|error`` status;
(5) project a snapshot + stream SSE frames. It mints no disposition and never
re-implements the gate/workflow — **Sense B lives in the gate, not here**
(``design/frontend.md`` §1).

Isolation (§2b): imports ``agents.address.*`` (driver, gate) + ``contract.*`` + stdlib
only; NEVER ``bridge/`` — the Bridge is reached by card URL alone. No ``fastapi`` import
here (that is ``server.py``).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from agents.address.driver import run_collection
from agents.address.gate import STATE_GATE_ROUNDS, STATE_LATEST_TURN, is_satisfied
from agents.address.wire import DocRef
from agents.console.store import ConsoleSessionRecord, ConsoleStore
from contract.wire import ExchangeTurn


# --------------------------------------------------------------------------- #
# The prepared-request catalog (§2d) — the "prepared" in the button.           #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PreparedRequest:
    """One curated demo scenario the console can start by key.

    The client POSTs a *key* (not raw agent args), so the console is the curated
    initiation surface. Each entry carries the ``run_collection`` kwargs for the run.
    ``edge`` records whether the run targets the mock (script-driven, keys off the
    ``scenario:`` marker) or the real edge (extracts ``documents``) — the dev server
    points at the matching card; tests inject the matching in-process app.
    """

    key: str
    label: str
    process: str
    scenario: str | None
    doc_ids: tuple[str, ...]
    max_rounds: int
    durable: bool
    edge: Literal["mock", "real"]


# The ≤5 corpus doc ids used by the catalog, with their by-reference artifact uris.
# Copied from ``wiki/evals/address/expected.json`` ``artifact`` fields so the SERVER
# code stays off ``tests/`` (§2d / gotcha 10). P7 replaces this with real uploads.
_CORPUS_DOCS: dict[str, str] = {
    "gov-id-clean": "images/gov-id-clean.svg",
    "bill-powerco-clean": "images/bill-powerco-clean.svg",
    "bill-powerco-clean-2": "images/bill-powerco-clean-2.svg",
    "bill-aquautil-clean": "images/bill-aquautil-clean.svg",
    "bill-aquautil-blurry": "images/bill-aquautil-blurry.svg",
}


PREPARED_REQUESTS: dict[str, PreparedRequest] = {
    "path-a-instant": PreparedRequest(
        key="path-a-instant",
        label="Path-A instant accept (gov-id) — satisfied",
        process="address-proof",
        scenario="path-a-instant",
        doc_ids=("gov-id-clean",),
        max_rounds=6,
        durable=False,
        edge="mock",
    ),
    "same-issuer-reject": PreparedRequest(
        key="same-issuer-reject",
        label="Two same-issuer bills — NOT satisfied (Sense-B watchable)",
        process="address-proof",
        scenario=None,
        doc_ids=("bill-powerco-clean", "bill-powerco-clean-2"),
        max_rounds=2,
        durable=False,
        edge="real",
    ),
    "distinct-issuer-accept": PreparedRequest(
        key="distinct-issuer-accept",
        label="Two distinct-issuer bills — satisfied",
        process="address-proof",
        scenario=None,
        doc_ids=("bill-powerco-clean", "bill-aquautil-clean"),
        max_rounds=6,
        durable=False,
        edge="real",
    ),
}


def doc_refs(pr: PreparedRequest) -> list[DocRef]:
    """Resolve a prepared request's ``doc_ids`` into by-reference ``DocRef``s.

    Uses the console-local ``_CORPUS_DOCS`` table (server code must not depend on
    ``tests/``, §2d). Unknown ids fail loud so a mis-edited catalog is caught early.
    """
    refs: list[DocRef] = []
    for doc_id in pr.doc_ids:
        try:
            uri = _CORPUS_DOCS[doc_id]
        except KeyError as e:
            raise KeyError(f"unknown corpus doc {doc_id!r}") from e
        refs.append(DocRef(doc_id=doc_id, uri=uri))
    return refs


# --------------------------------------------------------------------------- #
# Snapshot projection — the per-turn view + session-list row.                  #
# --------------------------------------------------------------------------- #
def snapshot(rec: ConsoleSessionRecord) -> dict:
    """Project a record onto the wire snapshot the host serves / streams.

    ``turn`` is the stored ``ExchangeTurn`` dump (already snake_case contract shape);
    the frontend maps it snake->camel. ``satisfied`` was computed by ``is_satisfied``
    when the turn was read (see ``_run``), not re-derived here (§2a).
    """
    return {
        "session_id": rec.session_id,
        "prepared_key": rec.prepared_key,
        "status": rec.status,
        "rounds": rec.rounds,
        "satisfied": rec.satisfied,
        "error": rec.error,
        "turn": rec.turn,
    }


def _apply_turn(rec: ConsoleSessionRecord, turn: ExchangeTurn) -> None:
    """Record an observed ``ExchangeTurn`` onto the record (turn dump + Sense-B check)."""
    rec.turn = turn.model_dump(mode="json")
    rec.satisfied = is_satisfied(turn.status)


# --------------------------------------------------------------------------- #
# The background run + its launch.                                             #
# --------------------------------------------------------------------------- #
async def _read_rounds(session_service: Any, rec: ConsoleSessionRecord) -> int:
    """Read the gate round counter back off the shared session state."""
    session = await session_service.get_session(
        app_name=rec.app_name, user_id=rec.user_id, session_id=rec.session_id
    )
    if session is None:
        return rec.rounds
    return int(session.state.get(STATE_GATE_ROUNDS, rec.rounds))


async def _read_live_turn(session_service: Any, rec: ConsoleSessionRecord) -> None:
    """Best-effort mid-run read of the gate-written state (live rounds/turn).

    Optional live-status nuance (§4.2): while the run is in flight the gate writes
    ``STATE_LATEST_TURN``/``STATE_GATE_ROUNDS`` each round. Reading them lets the
    session list/viewer show progress before the terminal turn. Single-turn runs finish
    so fast this is mostly cosmetic; it never overrides the terminal projection.
    """
    session = await session_service.get_session(
        app_name=rec.app_name, user_id=rec.user_id, session_id=rec.session_id
    )
    if session is None:
        return
    rec.rounds = int(session.state.get(STATE_GATE_ROUNDS, rec.rounds))
    latest = session.state.get(STATE_LATEST_TURN)
    if latest is not None:
        # Defensive: a partial/invalid dump is skipped (keep the try/except so the
        # `# pragma: no cover` marks the illegible-dump branch, not a suppress line).
        try:  # noqa: SIM105
            _apply_turn(rec, ExchangeTurn.model_validate(latest))
        except Exception:  # pragma: no cover - defensive; a partial dump is skipped
            pass


async def _run(
    store: ConsoleStore,
    *,
    session_id: str,
    pr: PreparedRequest,
    card_url: str,
    httpx_client: Any = None,
    session_service: Any,
) -> None:
    """The background coroutine: drive ``run_collection`` and store the projection.

    Never lets a background task die silently (gotcha 3): every exception is captured
    into ``status="error"``/``error`` and the record is ``put`` in a ``finally``.
    """
    rec = store.get(session_id)
    try:
        turn = await run_collection(
            card_url,
            process=pr.process,
            scenario=pr.scenario,
            documents=doc_refs(pr),
            max_rounds=pr.max_rounds,
            durable=pr.durable,
            httpx_client=httpx_client,
            session_service=session_service,
            session_id=session_id,
            user_id=rec.user_id,
        )
        if turn is not None:
            _apply_turn(rec, turn)
            rec.status = "done"
        else:
            rec.status = "error"
            rec.error = "no terminal output"
        rec.rounds = await _read_rounds(session_service, rec)
    except Exception as e:  # capture -> store (never a lost task exception)
        rec.status = "error"
        rec.error = repr(e)
    finally:
        store.put(rec)


def start_session(
    store: ConsoleStore,
    task_handles: dict[str, asyncio.Task],
    *,
    prepared_key: str,
    card_url: str,
    httpx_client: Any = None,
    session_service: Any,
) -> ConsoleSessionRecord:
    """Start a run from a prepared-request key. Must be called inside the event loop.

    Validates the key (``KeyError`` -> host 404), mints a fresh ``session_id``, creates
    a ``running`` record, and schedules ``_run`` on the serving loop with
    ``asyncio.create_task`` (§4.2). Returns the freshly created record.
    """
    if prepared_key not in PREPARED_REQUESTS:
        raise KeyError(prepared_key)
    pr = PREPARED_REQUESTS[prepared_key]
    session_id = uuid4().hex
    rec = ConsoleSessionRecord(session_id=session_id, prepared_key=prepared_key)
    store.create(rec)
    task_handles[session_id] = asyncio.create_task(
        _run(
            store,
            session_id=session_id,
            pr=pr,
            card_url=card_url,
            httpx_client=httpx_client,
            session_service=session_service,
        )
    )
    return rec


# --------------------------------------------------------------------------- #
# SSE — the first event-stream surface in the repo (§2e/§4.4).                 #
# --------------------------------------------------------------------------- #
def _frame(d: dict) -> str:
    """Format one snapshot as an SSE ``data:`` frame."""
    return f"data: {json.dumps(d)}\n\n"


async def sse_frames(
    store: ConsoleStore,
    *,
    session_id: str,
    interval: float = 0.02,
    max_ticks: int = 2000,
) -> Any:
    """Yield SSE frames for a session until it terminates (§2e).

    Emits the current snapshot immediately, then re-reads on each tick and yields a
    frame whenever the snapshot changed. Returns (closes the stream) once
    ``status in {"done","error"}`` — a terminal frame is always emitted before the
    return. ``await asyncio.sleep(interval)`` yields the loop so the background
    ``run_collection`` task advances between polls; ``max_ticks`` bounds the stream so a
    stuck run cannot hang it forever (fail-fast, like the gate ceiling).
    """
    rec = store.get(session_id)  # KeyError handled by the route (404 before streaming)
    last = snapshot(rec)
    yield _frame(last)
    if rec.status in ("done", "error"):
        return
    for _ in range(max_ticks):
        await asyncio.sleep(interval)
        rec = store.get(session_id)
        snap = snapshot(rec)
        if snap != last:
            yield _frame(snap)
            last = snap
        if rec.status in ("done", "error"):
            return
    # Deadline hit -> emit a final fail-fast frame and close (don't hang).
    stalled = dict(last)
    stalled["status"] = "error"
    stalled["error"] = "sse deadline exceeded"
    yield _frame(stalled)
