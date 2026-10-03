"""``python -m agents.address`` — the FE-1 CLI driver (App/Runner host).

The Address driver is an **App/Runner host** — it builds the Address ``Workflow`` on a
``Runner``, fires the opening ``CollectRequest``, drives one collection to its terminal
``ExchangeTurn``, and prints it. This is NOT an ADK agent — it is a driver/host.

The driver reaches the Bridge by **card URL only** — it never imports ``bridge/``
(keeps the mock→real swap a URL change). Test seams inject an in-process mock via
``httpx_client`` (ASGITransport over ``build_mock_bridge_app``).

Defaults to the plain ``Runner(node=...)`` path; ``run_collection(durable=True)``
opts into the App path (``ResumabilityConfig`` + durable session service, P4-1).
The end-to-end durable resume run itself is **P4-2**.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    import httpx

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.address.app import build_address_app, build_durable_session_service
from agents.address.gate import DEFAULT_MAX_ROUNDS
from agents.address.wire import DocRef, encode_doc_parts
from agents.address.workflow import build_address_workflow
from contract.wire import CollectRequest, ExchangeTurn, decode_wire


def open_request(
    *,
    process: str,
    scenario: str | None = None,
    party: str | None = None,
    message: str | None = None,
    context_id: str | None = None,
) -> CollectRequest:
    """Build the typed opening request from CLI args.

    If ``scenario`` is given and ``message`` is None, sets ``message`` to the
    ``scenario:<name>`` marker the mock Bridge consumes. If both are given, the
    explicit ``message`` is used (this function does not compose; the caller can
    ensure the marker is present if needed).
    """
    if message is None and scenario is not None:
        message = f"scenario:{scenario}"
    return CollectRequest(
        process=process,
        party=party,
        context_id=context_id,
        message=message,
    )


def _intake_text(req: CollectRequest) -> str:
    """Compose the intake text carrying what each edge reads.

    The real edge resolves ``process`` from a name-token / tag match in the text
    (``bridge.edge.intake.parse_text_request``); the mock keys off the ``scenario:``
    marker in the text (``bridge.edge.mock.select_scenario``). When both a scenario
    ``message`` and a ``process`` are present, the text carries BOTH (process token
    first, then the marker) so each edge reads what it understands and ignores the
    rest — the mock→real parity design. When only one is present, it stands alone.

    This became load-bearing once a second process skill was authored
    (``benefits-procurement``, S1-benefits-1): ``parse_text_request`` no longer
    defaults to the sole process skill, so scenario-only text can no longer resolve a
    process on the real edge. Naming the process in the text restores real/mock parity.
    """
    if req.message and req.process and req.process not in req.message:
        return f"{req.process} {req.message}"
    return req.message or req.process


def _seed_content(
    req: CollectRequest, documents: list[DocRef] | None = None
) -> types.Content:
    """Encode the opening request as the seed user message.

    Carries BOTH inputs in one message (P3-5): the intake text (``_intake_text`` —
    the ``process`` token the real edge resolves plus the ``scenario:`` marker the
    mock keys off) as the first part, followed by any by-reference document parts (the
    real edge's ``decode_submissions`` keys off these; the mock ignores them —
    script-driven). Each edge reads what it understands and ignores the rest, so one
    seed drives both toward the same terminal outcome (the mock→real parity design).

    Document bytes never ride inline — ``encode_doc_parts`` emits by-reference
    ``file_data`` url parts only (ART-2).
    """
    text = _intake_text(req)
    parts = [types.Part(text=text), *encode_doc_parts(documents or [])]
    return types.Content(role="user", parts=parts)


def _resume_content(
    call_id: str,
    call_name: str,
    req: CollectRequest,
    documents: list[DocRef] | None = None,
) -> types.Content:
    """Build the resume message for a parked ``LongRunningFunctionTool`` call.

    Feeds an **id-matched** ``FunctionResponse`` (so ADK un-parks the pending call),
    re-carrying the same intake the seed used inside the response payload — the
    ``scenario:`` marker (and doc refs) the seed sent. ADK forbids mixing a function
    response with text/data parts in one message (a function response *resumes* an
    invocation while text *starts* one), so the intake rides in ``response`` rather
    than as sibling parts. On the durable fresh-``Runner`` resume path the outbound
    A2A message is rebuilt from session history (which already carries the seed
    marker + doc parts), so behaviour stays robust regardless of the payload
    (P4-2 §2.1/§4.2).
    """
    text = _intake_text(req)
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=call_name,
                    response={
                        "result": text,
                        "documents": [d.uri for d in (documents or [])],
                    },
                )
            ),
        ],
    )


def _pending_long_running_call(session):
    """Return the newest **unanswered** function call on the session, or None.

    A parked ``LongRunningFunctionTool`` leaves a function *call* on the session with
    no matching function *response* later in the event stream. Scanning newest-first,
    the first call whose id has no later matching response is the live park to resume.
    A call that already carries a response is skipped (answering it again would
    double-resume and loop forever — P4-2 §4.1).
    """
    answered: set[str] = set()
    for event in reversed(session.events):
        for response in event.get_function_responses() or []:
            if getattr(response, "id", None) is not None:
                answered.add(response.id)
        for call in event.get_function_calls() or []:
            if getattr(call, "id", None) is not None and call.id not in answered:
                return call
    return None


async def _run_once(runner, *, user_id, session_id, new_message):
    """Run one turn on ``runner`` and return the last ``event.output`` (raw dict|None).

    Shared by the seed turn and every resume turn on the durable path: iterate
    ``run_async`` to exhaustion (a parked turn produces no ``event.output`` and the
    invocation ends — zero compute), capturing the most recent terminal output.
    """
    final_output = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        if getattr(event, "output", None) is not None:
            final_output = event.output
    return final_output


async def _resume_parked(
    session_service,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    card_url: str,
    httpx_client: httpx.AsyncClient | None = None,
    process: str = "address-proof",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_resumes: int | None = None,
    artifact_service=None,
    req: CollectRequest | None = None,
    documents: list[DocRef] | None = None,
) -> ExchangeTurn | None:
    """Drive an already-parked durable session to terminal (shared restore path).

    The **one** restore path shared by ``run_collection``'s durable resume loop and
    the P5-4 ``wake_task`` handler: while the collect leg is parked (an unanswered
    ``LongRunningFunctionTool`` call) and no terminal turn has surfaced, feed an
    id-matched ``FunctionResponse`` to a **fresh** resumable ``Runner`` over the
    **same** durable session service (the process-restart shape). Returns the
    terminal ``ExchangeTurn`` or ``None`` (parked-only / no pending call / cap hit).

    On the fresh-``Runner`` resume path the outbound A2A message is rebuilt from
    session history (which already carries the seed marker + doc parts), so a
    minimal ``open_request(process=process)`` is sufficient when the original
    ``req``/``documents`` are not on hand — e.g. the out-of-band wake path
    (P4-2 §2.1/§4.2).
    """
    if artifact_service is None:
        artifact_service = InMemoryArtifactService()
    if req is None:
        req = open_request(process=process)

    def _new_runner():
        # A FRESH Runner per turn over the SAME durable session service — the
        # process-restart shape (durable state lives in the DB, not the Runner).
        return Runner(
            app=build_address_app(
                card_url, max_rounds=max_rounds, httpx_client=httpx_client
            ),
            session_service=session_service,
            artifact_service=artifact_service,
        )

    final_output = None
    cap = max_rounds if max_resumes is None else max_resumes
    for _ in range(cap):
        session = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        call = _pending_long_running_call(session) if session is not None else None
        if call is None:
            break  # neither parked nor (any longer) resumable → done
        # DUR-4: id-matched FunctionResponse on a fresh Runner (same DB).
        final_output = await _run_once(
            _new_runner(),
            user_id=user_id,
            session_id=session_id,
            new_message=_resume_content(call.id, call.name, req, documents),
        )
        if final_output is not None:
            break  # terminal turn surfaced → done

    if final_output is None:
        return None
    return decode_wire(ExchangeTurn, final_output)


async def run_collection(
    card_url: str,
    *,
    process: str = "address-proof",
    scenario: str | None = None,
    party: str | None = None,
    message: str | None = None,
    documents: list[DocRef] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_resumes: int | None = None,  # durable resume-loop cap (defaults to max_rounds)
    httpx_client: httpx.AsyncClient | None = None,  # test seam (in-process mock)
    user_id: str = "address-driver",
    session_id: str | None = None,
    session_service=None,  # default InMemorySessionService()
    artifact_service=None,  # default InMemoryArtifactService()
    durable: bool = False,  # opt-in: App-path + ResumabilityConfig (P4-1)
    session_index=None,  # P5-4: optional SessionIndex; on park, record peer task_id
) -> ExchangeTurn | None:
    """Run a collection to a terminal ``ExchangeTurn``.

    Builds the Address workflow, seeds the opening ``CollectRequest``, runs the
    collect→gate→present cycle, and returns the terminal turn (or None if the run
    did not reach a terminal output).

    By default runs on the plain ``Runner(node=...)`` path (in-memory, no
    resumability). With ``durable=True`` it runs on the App path
    (``build_address_app`` + ``Runner(app=app, …)``), carrying
    ``ResumabilityConfig`` and defaulting the session service to
    ``build_durable_session_service()`` (durable when ``BRIDGE_SESSION_DB_URL`` is
    set). The end-to-end durable resume run itself is P4-2.

    Args:
        card_url: Bridge Agent Card URL (local mock at :8001, real edge at :8000).
        process: Process-skill key (default "address-proof").
        scenario: Scenario marker for mock Bridge (default None).
        party: Counterparty reference (default None).
        message: Explicit free-text instruction (overrides scenario-derived seed).
        documents: By-reference ``DocRef`` documents to submit on the opening
            message (the real edge extracts them; the mock ignores them). Default
            None (text-only seed).
        max_rounds: Round ceiling — force done after this many collect/gate cycles.
        httpx_client: Test seam — injected client for in-process mock (via
            ASGITransport). Do not close an injected client (the caller owns it).
        user_id: ADK user_id (default "address-driver").
        session_id: ADK session_id (defaults to a fresh hex UUID).
        session_service: Session service (defaults to InMemorySessionService).
        artifact_service: Artifact service (defaults to InMemoryArtifactService).

    Returns:
        The terminal ``ExchangeTurn`` (read off ``event.output``) or None if the
        run did not produce a terminal output.
    """
    req = open_request(
        process=process, scenario=scenario, party=party, message=message
    )

    # Default the services and session_id.
    if artifact_service is None:
        artifact_service = InMemoryArtifactService()
    if session_id is None:
        session_id = uuid4().hex

    if durable:
        # App path — carries ResumabilityConfig so the Runner is resumable (P4-1),
        # and drives the park→resume loop across parks to a terminal turn (P4-2).
        if session_service is None:
            session_service = build_durable_session_service()
        await session_service.create_session(
            app_name="address", user_id=user_id, session_id=session_id
        )

        def _new_runner():
            # A FRESH Runner per turn over the SAME durable session service — the
            # process-restart shape (durable state lives in the DB, not the Runner).
            return Runner(
                app=build_address_app(
                    card_url, max_rounds=max_rounds, httpx_client=httpx_client
                ),
                session_service=session_service,
                artifact_service=artifact_service,
            )

        # Turn 1 — seed.
        final_output = await _run_once(
            _new_runner(),
            user_id=user_id,
            session_id=session_id,
            new_message=_seed_content(req, documents),
        )

        # Terminal on the seed turn → done (no resume needed).
        if final_output is not None:
            return decode_wire(ExchangeTurn, final_output)

        # P5-4: index population on park. When the seed leg parked (no terminal
        # output but an unanswered long-running call), record the peer (Bridge)
        # task id -> SessionRef in the injected index so a later "task X changed"
        # doorbell can find this session. Default ``session_index=None`` → no
        # indexing → existing behavior byte-for-byte unchanged.
        session = await session_service.get_session(
            app_name="address", user_id=user_id, session_id=session_id
        )
        if session_index is not None and _pending_long_running_call(session):
            # Lazy import avoids a wake<->driver import cycle (wake imports driver).
            from agents.address.wake import SessionRef, peer_task_id_for

            peer_task_id = peer_task_id_for(session)
            if peer_task_id:
                session_index.put(
                    peer_task_id,
                    SessionRef(
                        app_name="address", user_id=user_id, session_id=session_id
                    ),
                )

        # Resume loop — while the collect leg parked (an unanswered long-running
        # call) and no terminal turn surfaced yet. Bounded by ``max_resumes``
        # (mirrors the gate ceiling) so a never-terminating scenario fails fast.
        # Shared with the P5-4 wake path (one restore path, not two).
        return await _resume_parked(
            session_service,
            app_name="address",
            user_id=user_id,
            session_id=session_id,
            card_url=card_url,
            httpx_client=httpx_client,
            process=process,
            max_rounds=max_rounds,
            max_resumes=max_resumes,
            artifact_service=artifact_service,
            req=req,
            documents=documents,
        )

    # Non-durable path — plain Runner(node=...), single turn (unchanged).
    wf = build_address_workflow(
        card_url, max_rounds=max_rounds, httpx_client=httpx_client
    )
    if session_service is None:
        session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="address", user_id=user_id, session_id=session_id
    )
    runner = Runner(
        node=wf,
        session_service=session_service,
        app_name="address",
        artifact_service=artifact_service,
    )

    final_output = await _run_once(
        runner,
        user_id=user_id,
        session_id=session_id,
        new_message=_seed_content(req, documents),
    )

    if final_output is None:
        return None
    return decode_wire(ExchangeTurn, final_output)


def latest_parked_call(session):
    """Return the most-recent function-call on the session (the parked LRFT call).

    This is a helper for callers of ``feed_function_response`` to find the parked
    call id/name to answer. Returns None if no function calls are found.
    """
    for event in reversed(session.events):
        calls = event.get_function_calls() or []
        if calls:
            return calls[0]
    return None


async def feed_function_response(
    runner,
    *,
    user_id: str,
    session_id: str,
    call_id: str,
    call_name: str,
    result: str,
) -> ExchangeTurn | None:
    """Feed a wake ``FunctionResponse`` and resume the collection.

    Builds a ``FunctionResponse`` content for the given call id/name and runs it
    on the session; returns the last ``event.output`` as an ``ExchangeTurn | None``.

    NOTE: A *terminating* live multi-turn resume needs the App-path
    ``ResumabilityConfig`` durable stores from **P4**. On the plain
    ``Runner(node=...)`` path this feeds the response but the terminating golden
    runs land in **P3-5/P4**. See
    ``tests/address/test_workflow.py::test_live_path_b_park_then_resume_same_task``
    (currently xfail).

    This helper is importable and unit-touched (a shape test) but not a
    live-terminate assertion.

    Args:
        runner: The ``Runner`` instance (shared session/app).
        user_id: ADK user_id.
        session_id: ADK session_id (must match the parked session).
        call_id: The parked function call id.
        call_name: The parked function call name.
        result: The result string to feed.

    Returns:
        The terminal ``ExchangeTurn`` (if reached) or None.
    """
    response_content = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=call_name,
                    response={"result": result},
                )
            )
        ],
    )

    final_output = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=response_content
    ):
        if getattr(event, "output", None) is not None:
            final_output = event.output

    if final_output is None:
        return None
    return decode_wire(ExchangeTurn, final_output)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — parse args, run a collection, print the terminal turn.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        0 on success (terminal turn reached), 1 on failure (no terminal output).
    """
    parser = argparse.ArgumentParser(
        prog="python -m agents.address",
        description=(
            "Address service agent CLI driver. Runs a collection against the "
            "Bridge by card URL and prints the terminal ExchangeTurn as JSON. "
            "Point the card URL at the mock (:8001) or the real edge (:8000) — "
            "run the server separately (python -m bridge.edge.mock or "
            "python -m bridge.edge.server)."
        ),
    )
    parser.add_argument(
        "--card-url",
        default="http://localhost:8000" + AGENT_CARD_WELL_KNOWN_PATH,
        help="Bridge Agent Card URL (default: real edge at :8000)",
    )
    parser.add_argument(
        "--process",
        default="address-proof",
        help="Process-skill key (default: address-proof)",
    )
    parser.add_argument(
        "--scenario",
        default="path-a-instant",
        help="Scenario marker for mock Bridge (default: path-a-instant)",
    )
    parser.add_argument(
        "--party",
        default=None,
        help="Counterparty reference (default: None)",
    )
    parser.add_argument(
        "--message",
        default=None,
        help="Explicit free-text instruction (overrides scenario-derived seed)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=f"Round ceiling (default: {DEFAULT_MAX_ROUNDS})",
    )

    args = parser.parse_args(argv)

    turn = asyncio.run(
        run_collection(
            card_url=args.card_url,
            process=args.process,
            scenario=args.scenario,
            party=args.party,
            message=args.message,
            max_rounds=args.max_rounds,
        )
    )

    if turn is None:
        print("Error: Collection did not reach a terminal output.", file=sys.stderr)
        return 1

    print(json.dumps(turn.model_dump(mode="json"), indent=2))
    return 0
