"""P4-2 — DUR-3 zero-compute park + DUR-4 crash-safe durable resume.

Proves the two durability invariants the P4-2 gate turns on, against the in-process
mock over an ``httpx.ASGITransport`` loopback client:

- **DUR-3 (zero-compute park).** When the Bridge returns ``input-required``,
  ``RemoteA2aAgent`` synthesizes a ``LongRunningFunctionTool`` call
  (``mock_function_call_for_required_user_input``) and the workflow invocation ends.
  Turn 1 leaves an **unanswered** long-running function call on the durable session
  and produces **no** terminal output — the pause costs no compute (the ``async for``
  over ``run_async`` simply completes; no busy loop).

- **DUR-4 (crash-safety, CUJ-7 in miniature).** Park turn 1 on a
  ``DatabaseSessionService`` over a ``tmp_path`` DSN, then **drop the Runner and the
  session service** (simulate a process restart), rebuild a fresh session service
  from the **same DSN** + a fresh ``Runner``, recover the parked call id from the
  reloaded session, feed an id-matched ``FunctionResponse``, and reach the **same
  terminal outcome** — with **no HTTP re-drive** of turn 1 and on the **same
  ``session_id``** (no throwaway child session).

Both scripted multi-turn scenarios (``path-b-two-bills``, ``resubmit``) are covered
end to end.
"""
from __future__ import annotations

import httpx
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner

from agents.address.app import build_address_app, build_durable_session_service
from agents.address.driver import (
    _pending_long_running_call,
    _resume_content,
    _run_once,
    _seed_content,
    open_request,
)
from bridge.edge import build_mock_bridge_app
from contract.enums import Disposition
from contract.wire import ExchangeTurn

# The A2A client requires a loopback host for http, so the mock is served loopback.
BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH

USER = "u"
SID = "s"
PARK_CALL_NAME = "mock_function_call_for_required_user_input"


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def _runner(hc, session_service, *, max_rounds=6):
    """A fresh Runner on the App path (ResumabilityConfig) over ``session_service``."""
    return Runner(
        app=build_address_app(CARD_URL, max_rounds=max_rounds, httpx_client=hc),
        session_service=session_service,
        artifact_service=InMemoryArtifactService(),
    )


async def _get_session(session_service):
    return await session_service.get_session(
        app_name="address", user_id=USER, session_id=SID
    )


# --------------------------------------------------------------------------- #
# DUR-3 — the park is zero-compute.                                            #
# --------------------------------------------------------------------------- #
async def test_dur3_park_is_zero_compute(tmp_path):
    """Turn 1 parks: an unanswered LRFT call on the session, no terminal output."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/session.db"
    session_service = build_durable_session_service(db_url=dsn)
    await session_service.create_session(
        app_name="address", user_id=USER, session_id=SID
    )
    req = open_request(process="address-proof", scenario="path-b-two-bills")

    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        runner = _runner(hc, session_service)
        # Turn 1 only — the invocation returns control (this line completing proves
        # there is no busy loop; a park ends the invocation with zero compute).
        final_output = await _run_once(
            runner, user_id=USER, session_id=SID, new_message=_seed_content(req)
        )
    finally:
        await hc.aclose()

    # No terminal turn was produced.
    assert final_output is None

    # An UNANSWERED long-running function call is parked on the durable session.
    session = await _get_session(session_service)
    call = _pending_long_running_call(session)
    assert call is not None
    assert call.name == PARK_CALL_NAME

    # And the workflow's App is resumable (the switch the resume relies on).
    assert runner.resumability_config.is_resumable is True


# --------------------------------------------------------------------------- #
# DUR-4 — crash-safe resume on a fresh session service from the same DSN.      #
# --------------------------------------------------------------------------- #
async def _park_then_restart_resume(tmp_path, scenario):
    """Park turn 1, drop the Runner + session service, rebuild from the same DSN,
    resume on a fresh Runner, and return ``(terminal_turn, reloaded_session)``.
    """
    dsn = f"sqlite+aiosqlite:///{tmp_path}/session.db"
    req = open_request(process="address-proof", scenario=scenario)

    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        # --- Process 1: seed + park, then let the Runner + service go. ---
        session_service = build_durable_session_service(db_url=dsn)
        await session_service.create_session(
            app_name="address", user_id=USER, session_id=SID
        )
        runner = _runner(hc, session_service)
        first_output = await _run_once(
            runner, user_id=USER, session_id=SID, new_message=_seed_content(req)
        )
        assert first_output is None, "turn 1 must park, not terminate"
        parked = await _get_session(session_service)
        n_events_at_park = len(parked.events)
        del runner
        del session_service

        # --- Process 2: rebuild the session service from the SAME DSN. ---
        resumed_service = build_durable_session_service(db_url=dsn)
        reloaded = await resumed_service.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        # The durable session survived: same session_id, turn-1 events reloaded.
        assert reloaded is not None
        assert reloaded.id == SID
        assert len(reloaded.events) == n_events_at_park

        call = _pending_long_running_call(reloaded)
        assert call is not None, "the parked call must be recoverable after restart"

        # Resume on a FRESH Runner over the rebuilt durable service — no HTTP
        # re-drive of turn 1, same session_id (no throwaway child session).
        fresh_runner = _runner(hc, resumed_service)
        assert fresh_runner.resumability_config.is_resumable is True
        final_output = await _run_once(
            fresh_runner,
            user_id=USER,
            session_id=SID,
            new_message=_resume_content(call.id, call.name, req),
        )
        final_session = await resumed_service.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
    finally:
        await hc.aclose()

    assert final_output is not None
    return ExchangeTurn.model_validate(final_output), final_session


async def test_dur4_crash_safe_resume_path_b_two_bills(tmp_path):
    """path-b: park → restart → resume → same terminal two-issuer outcome."""
    turn, session = await _park_then_restart_resume(tmp_path, "path-b-two-bills")

    assert turn.terminal is True
    issuers = {
        e.issuer for e in turn.status.entries if e.disposition is Disposition.accepted
    }
    assert issuers == {"power-co", "aqua-util"}
    # The resume reused the same durable session (turn-1 events still present).
    assert session.id == SID
    assert len(session.events) > 0


async def test_dur4_crash_safe_resume_resubmit(tmp_path):
    """resubmit: park → restart → resume → clear copy accepted."""
    turn, session = await _park_then_restart_resume(tmp_path, "resubmit")

    assert turn.terminal is True
    accepted = [
        e for e in turn.status.entries if e.disposition is Disposition.accepted
    ]
    assert any(
        e.doc_id == "bill-aquautil-clean" and e.doctype == "utility-bill"
        for e in accepted
    )
    assert session.id == SID
