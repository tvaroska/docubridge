"""Address driver tests — CLI, ``run_collection``, and resume helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

from agents.address.driver import (
    feed_function_response,
    latest_parked_call,
    main,
    open_request,
    run_collection,
)
from bridge.edge import build_mock_bridge_app
from contract.enums import Disposition, Gate
from contract.wire import ExchangeTurn

BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH


# --------------------------------------------------------------------------- #
# open_request unit (no network)                                              #
# --------------------------------------------------------------------------- #
def test_open_request_with_scenario_sets_scenario_marker():
    req = open_request(process="address-proof", scenario="path-a-instant")
    assert req.process == "address-proof"
    assert req.message == "scenario:path-a-instant"
    assert req.party is None
    assert req.context_id is None


def test_open_request_with_explicit_message_preserves_it():
    req = open_request(
        process="address-proof",
        scenario="path-a-instant",
        message="custom message",
    )
    assert req.message == "custom message"


def test_open_request_passes_through_party_and_context_id():
    req = open_request(
        process="address-proof",
        scenario="path-a-instant",
        party="party-123",
        context_id="ctx-abc",
    )
    assert req.party == "party-123"
    assert req.context_id == "ctx-abc"


def test_open_request_without_scenario_has_none_message():
    req = open_request(process="address-proof")
    assert req.message is None


# --------------------------------------------------------------------------- #
# run_collection end-to-end vs in-process mock (the core proof)               #
# --------------------------------------------------------------------------- #
async def test_run_collection_path_a_instant_returns_terminal_gov_id():
    """The driver fires the opening request and reaches the terminal turn.

    This is the P3-4 acceptance proof — ``run_collection`` drives
    ``path-a-instant`` against the in-process mock and returns the terminal
    accepted gov-id ``ExchangeTurn`` (same outcome
    ``test_workflow.py::test_live_path_a_instant_...`` pins).
    """
    app = build_mock_bridge_app(url=BASE + "/")
    hc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
    try:
        turn = await run_collection(
            CARD_URL, scenario="path-a-instant", httpx_client=hc
        )
    finally:
        await hc.aclose()

    assert turn is not None
    assert turn.terminal is True
    assert len(turn.status.entries) == 1
    entry = turn.status.entries[0]
    assert entry.doctype == "gov-id"
    assert entry.disposition is Disposition.accepted
    assert entry.gate is Gate.auto_approve


# --------------------------------------------------------------------------- #
# CLI wiring (main) — no network, monkeypatched host                          #
# --------------------------------------------------------------------------- #
def test_main_prints_terminal_turn_json_and_returns_zero(capsys, monkeypatch):
    """Monkeypatch ``run_collection`` returning a terminal turn → stdout JSON."""
    terminal_turn = ExchangeTurn(
        context_id="ctx-test",
        terminal=True,
        status={"entries": []},
    )

    async def stub_run_collection(*args, **kwargs):
        return terminal_turn

    monkeypatch.setattr("agents.address.driver.run_collection", stub_run_collection)

    exit_code = main(["--scenario", "path-a-instant"])
    captured = capsys.readouterr()

    assert exit_code == 0
    output = json.loads(captured.out)
    assert output["context_id"] == "ctx-test"
    assert output["terminal"] is True


def test_main_returns_one_on_none_turn(capsys, monkeypatch):
    """Stub ``run_collection`` returning None → exit 1 + stderr."""

    async def stub_run_collection(*args, **kwargs):
        return None

    monkeypatch.setattr("agents.address.driver.run_collection", stub_run_collection)

    exit_code = main(["--scenario", "path-a-instant"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Error" in captured.err or "terminal" in captured.err


def test_main_forwards_parsed_args_to_run_collection(monkeypatch):
    """Assert ``main`` forwarded the parsed args into ``run_collection``."""
    captured_kwargs = {}

    async def stub_run_collection(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ExchangeTurn(
            context_id="ctx-test",
            terminal=True,
            status={"entries": []},
        )

    monkeypatch.setattr("agents.address.driver.run_collection", stub_run_collection)

    main(
        [
            "--card-url",
            "http://localhost:9999/card.json",
            "--process",
            "custom-process",
            "--scenario",
            "custom-scenario",
            "--max-rounds",
            "10",
        ]
    )

    assert captured_kwargs["card_url"] == "http://localhost:9999/card.json"
    assert captured_kwargs["process"] == "custom-process"
    assert captured_kwargs["scenario"] == "custom-scenario"
    assert captured_kwargs["max_rounds"] == 10


# --------------------------------------------------------------------------- #
# Module entry smoke (optional but cheap)                                     #
# --------------------------------------------------------------------------- #
def test_module_entry_help_returns_zero():
    """Smoke test — ``python -m agents.address --help`` resolves and prints usage."""
    result = subprocess.run(
        [sys.executable, "-m", "agents.address", "--help"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower() or "Address" in result.stdout


# --------------------------------------------------------------------------- #
# feed_function_response shape (no live terminate)                            #
# --------------------------------------------------------------------------- #
async def test_feed_function_response_builds_correct_response_content():
    """Shape test — ``feed_function_response`` builds the right content.

    This is NOT a live multi-turn resume (that needs P4 durable stores) — just
    a shape test to keep the resume capability from bit-rotting. A fake runner
    whose ``run_async`` yields a synthetic ``event.output``.
    """

    # Fake event with an output attribute.
    class FakeEvent:
        def __init__(self, output):
            self.output = output

    # Fake runner whose run_async yields one event with output.
    class FakeRunner:
        async def run_async(self, user_id, session_id, new_message):
            # Verify the function_response was built correctly.
            assert new_message.role == "user"
            assert len(new_message.parts) == 1
            fr = new_message.parts[0].function_response
            assert fr.id == "call-123"
            assert fr.name == "collect"
            assert fr.response == {"result": "test-result"}

            # Yield a synthetic terminal turn.
            yield FakeEvent(
                output={
                    "context_id": "ctx-fake",
                    "terminal": True,
                    "status": {"entries": []},
                }
            )

    runner = FakeRunner()
    turn = await feed_function_response(
        runner,
        user_id="u",
        session_id="s",
        call_id="call-123",
        call_name="collect",
        result="test-result",
    )

    assert turn is not None
    assert turn.context_id == "ctx-fake"
    assert turn.terminal is True


async def test_latest_parked_call_returns_most_recent_call():
    """Unit test for ``latest_parked_call`` helper."""

    # Fake session with events.
    class FakeEvent:
        def __init__(self, calls):
            self._calls = calls

        def get_function_calls(self):
            return self._calls

    class FakeCall:
        def __init__(self, call_id, name):
            self.id = call_id
            self.name = name

    class FakeSession:
        def __init__(self):
            self.events = [
                FakeEvent([]),  # no calls
                FakeEvent([FakeCall("call-1", "collect")]),  # first call
                FakeEvent([]),  # no calls
                FakeEvent([FakeCall("call-2", "collect")]),  # most recent
            ]

    session = FakeSession()
    call = latest_parked_call(session)

    assert call is not None
    assert call.id == "call-2"
    assert call.name == "collect"


async def test_latest_parked_call_returns_none_when_no_calls():
    """Edge case — no function calls on the session."""

    class FakeEvent:
        def get_function_calls(self):
            return []

    class FakeSession:
        def __init__(self):
            self.events = [FakeEvent(), FakeEvent()]

    session = FakeSession()
    call = latest_parked_call(session)

    assert call is None
