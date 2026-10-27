"""The served email drain (S0-bug-3): landed mail reaches its RFP leg on the HOST.

Every case drives a real host builder (``build_rfp_demo_app`` / ``build_dashboard_app``)
wired from env — the acceptance path constructs no processor in the test. Proves: the
CLI ``land`` + ``drain`` (which POSTs to the served host, never drains in-process) files
a carrier's reply on the demo board; the lifespan poll loop drains with no explicit
call and survives a failing drain; the env gate (no board / no landing location → no
drain routes; no secret → fail loud; malformed interval → fail loud); the processor
drains only onto the host's own board; the shown reply address is the processor's;
the CLI's error paths; and the fresh-process import order (no circular import).

Synthetic data only (``tests/email_fixtures.py``).
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from google.adk.artifacts import InMemoryArtifactService

from bridge.dashboard.server import build_dashboard_app
from bridge.demos.rfp_demo import DEMO_RFP_ID, build_rfp_demo_app
from bridge.edge.email_ingress import EmailProcessor
from bridge.email_ingress.__main__ import main as cli
from bridge.email_ingress.mime import landing_key
from bridge.ports.local import LocalEmailIngress
from contract.enums import RfpItemStatus
from tests.email_fixtures import (
    BASE_URL,
    CI_PDF,
    MAILBOX,
    RFP_ID,
    SECRET,
    compose,
    extractor,
    make_board,
    quote_attachments,
)

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
ENV_SECRET = "served-test-secret"
OPS = "http://testserver/ops"
EMAIL_ENV = (
    "BRIDGE_EMAIL_DIR",
    "BRIDGE_EMAIL_BUCKET",
    "BRIDGE_EMAIL_MAILBOX",
    "BRIDGE_EMAIL_TOKEN_SECRET",
    "BRIDGE_EMAIL_DRAIN_INTERVAL",
    "BRIDGE_EMAIL_DRAIN_URL",
    "BRIDGE_EMAIL_MAX_BYTES",
    "BRIDGE_PORT_EMAIL_INGRESS",
    "BRIDGE_PORT_EXTRACTION",
    "BRIDGE_PORT_MODE",
    "BRIDGE_ARTIFACT_BUCKET",
    "BRIDGE_PUBLIC_URL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in EMAIL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def intake_env(clean_env, tmp_path):
    """Email intake fully configured from env (local landing dir), on-demand drain."""
    clean_env.setenv("BRIDGE_EMAIL_DIR", str(tmp_path / "mail"))
    clean_env.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
    clean_env.setenv("BRIDGE_EMAIL_TOKEN_SECRET", ENV_SECRET)
    clean_env.setenv("BRIDGE_EMAIL_DRAIN_INTERVAL", "0")
    return clean_env


def _cells(client: TestClient, prefix: str = "/ops") -> dict[tuple[str, str], str]:
    body = client.get(f"{prefix}/rfps/{DEMO_RFP_ID}").json()
    return {(c["party"], c["product_line"]): c["status"] for c in body["cells"]}


def _reply_address(client: TestClient, party: str, prefix: str = "/ops") -> str:
    body = client.get(f"{prefix}/rfps/{DEMO_RFP_ID}").json()
    return next(c["reply_address"] for c in body["carriers"] if c["party"] == party)


def _wait_for(predicate, *, timeout: float = 5.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# --------------------------------------------------------------------------- #
# 1. The acceptance path: land + drain through the SERVED host                 #
# --------------------------------------------------------------------------- #
def test_landed_reply_reaches_its_rfp_leg_through_the_served_host(
    intake_env, tmp_path, capsys
):
    app = build_rfp_demo_app(now=NOW)
    proc = app.state.email_processor
    assert isinstance(proc, EmailProcessor)  # built by the host from env, not injected
    assert proc.board is app.state.rfps
    client = TestClient(app)

    addr = _reply_address(client, "carrier-c")  # the served API, not a minted token
    assert addr and addr.startswith("rfp-quotes+")
    eml = tmp_path / "reply.eml"
    eml.write_bytes(
        compose(
            addr,
            message_id="<quote-1@carrier-c.example>",
            sender="quotes@carrier-c.example",
            attachments=quote_attachments(),
        )
    )
    assert cli(["land", str(eml)]) == 0
    key = landing_key(eml.read_bytes())
    assert capsys.readouterr().out.strip() == f"landed {key}"

    assert cli(["drain", "--url", OPS], client=client) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [
        f"filed {key} {DEMO_RFP_ID} carrier-c filed=2 unreadable=1",
        "drained 1",
    ]
    cells = _cells(client)
    assert cells[("carrier-c", "CRITICAL_ILLNESS")] == "received"
    assert cells[("carrier-c", "HOSPITAL_INDEMNITY")] == "incomplete"

    marker = client.get(f"/ops/email/outcomes/{key}").json()
    assert (marker["status"], marker["party"], marker["rfp_id"]) == (
        "filed",
        "carrier-c",
        DEMO_RFP_ID,
    )
    assert [u["reason"] for u in marker["unreadable"]] == ["unsupported_media_type"]

    before = app.state.rfps.rfps[DEMO_RFP_ID].model_dump(mode="json")
    assert cli(["drain", "--url", OPS], client=client) == 0
    assert capsys.readouterr().out.strip().splitlines() == ["drained 0"]
    assert app.state.rfps.rfps[DEMO_RFP_ID].model_dump(mode="json") == before


def test_cli_drain_reports_a_quarantine(intake_env, tmp_path, capsys):
    app = build_rfp_demo_app(now=NOW)
    raw = compose(MAILBOX, message_id="<untagged@carrier-x.example>")
    key = landing_key(raw)
    assert LocalEmailIngress(tmp_path / "mail").land(key, raw)
    assert cli(["drain", "--url", OPS], client=TestClient(app)) == 0
    assert capsys.readouterr().out.strip().splitlines() == [
        f"quarantined {key} no_exchange_token",
        "drained 1",
    ]


# --------------------------------------------------------------------------- #
# 2-3. The lifespan poll loop                                                  #
# --------------------------------------------------------------------------- #
def test_poll_loop_drains_without_any_explicit_call(intake_env, tmp_path):
    intake_env.setenv("BRIDGE_EMAIL_DRAIN_INTERVAL", "0.05")
    app = build_rfp_demo_app(now=NOW)
    port = LocalEmailIngress(tmp_path / "mail")
    with TestClient(app) as client:
        assert _cells(client)[("carrier-b", "CRITICAL_ILLNESS")] == "acknowledged"
        raw = compose(
            _reply_address(client, "carrier-b"),
            message_id="<quote-1@carrier-b.example>",
            sender="quotes@carrier-b.example",
            attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")],
        )
        key = landing_key(raw)
        assert port.land(key, raw)
        assert _wait_for(
            lambda: _cells(client)[("carrier-b", "CRITICAL_ILLNESS")] == "received"
        )
    assert port.pending() == []
    assert port.processed(key)["status"] == "filed"


def test_poll_loop_survives_a_failing_drain(clean_env, tmp_path, caplog):
    port = LocalEmailIngress(tmp_path)
    board = make_board()

    class Flaky:
        async def extract(self, name):
            raise RuntimeError("503 upstream")

    proc = EmailProcessor(
        board=board,
        port=port,
        mailbox=MAILBOX,
        secret=SECRET,
        extraction=Flaky(),
        artifact_store=InMemoryArtifactService(),
        base_url=BASE_URL,
    )
    app = build_dashboard_app(rfps=board, email=proc, email_drain_interval=0.02)
    raw = compose(
        proc.reply_address(RFP_ID, "carrier-a"),
        attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")],
    )
    key = landing_key(raw)
    assert port.land(key, raw)

    def failures() -> int:
        return sum(
            "periodic email drain failed" in r.getMessage() for r in caplog.records
        )

    with TestClient(app):
        assert _wait_for(lambda: failures() >= 2)  # the loop kept going
        assert port.pending() == [key]  # still queued, board untouched
        assert board.rfps[RFP_ID].cell("carrier-a", "CRITICAL_ILLNESS").status is (
            RfpItemStatus.invited
        )
        proc.extraction = extractor()
        assert _wait_for(lambda: port.processed(key) is not None)
    cell = board.rfps[RFP_ID].cell("carrier-a", "CRITICAL_ILLNESS")
    assert cell.status is RfpItemStatus.received


# --------------------------------------------------------------------------- #
# 4-9. The env gate + host binding rules                                       #
# --------------------------------------------------------------------------- #
def test_no_board_no_drain_even_with_env_fully_set(intake_env):
    app = build_dashboard_app()
    client = TestClient(app)
    assert app.state.email_processor is None
    assert client.post("/email/drain").status_code == 404
    assert client.get(f"/email/outcomes/{'a' * 32}").status_code == 404


def test_mailbox_without_landing_location_shows_addresses_but_no_drain(clean_env):
    clean_env.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
    clean_env.setenv("BRIDGE_EMAIL_TOKEN_SECRET", ENV_SECRET)
    app = build_rfp_demo_app(now=NOW)
    client = TestClient(app)
    assert app.state.email_processor is None
    body = client.get(f"/ops/rfps/{DEMO_RFP_ID}").json()
    assert all(c["reply_address"] for c in body["carriers"])
    assert client.post("/ops/email/drain").status_code == 404


def test_mailbox_and_landing_without_secret_fails_at_build(intake_env):
    intake_env.delenv("BRIDGE_EMAIL_TOKEN_SECRET")
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_TOKEN_SECRET"):
        build_rfp_demo_app(now=NOW)


@pytest.mark.parametrize("raw", ["-1", "abc", "nan", "inf"])
def test_malformed_drain_interval_fails_at_build(intake_env, raw):
    intake_env.setenv("BRIDGE_EMAIL_DRAIN_INTERVAL", raw)
    with pytest.raises(ValueError, match="BRIDGE_EMAIL_DRAIN_INTERVAL"):
        build_rfp_demo_app(now=NOW)


def test_zero_and_unset_drain_interval(intake_env):
    dash = build_dashboard_app(rfps=make_board())
    assert dash.state.email_processor is not None
    assert dash.state.email_drain_interval == 0
    intake_env.delenv("BRIDGE_EMAIL_DRAIN_INTERVAL")
    dash = build_dashboard_app(rfps=make_board())
    assert dash.state.email_drain_interval == 30.0
    with pytest.raises(ValueError, match="email_drain_interval"):
        build_dashboard_app(rfps=make_board(), email_drain_interval=-1)


def _proc(board, tmp_path) -> EmailProcessor:
    return EmailProcessor(
        board=board,
        port=LocalEmailIngress(tmp_path),
        mailbox=MAILBOX,
        secret=SECRET,
        extraction=extractor(),
        artifact_store=InMemoryArtifactService(),
        base_url=BASE_URL,
    )


def test_processor_must_drain_onto_this_hosts_board(clean_env, tmp_path):
    proc = _proc(make_board(), tmp_path)
    with pytest.raises(ValueError, match="rfps"):
        build_dashboard_app(rfps=make_board(), email=proc)
    with pytest.raises(ValueError, match="rfps"):
        build_dashboard_app(email=proc)


def test_shown_reply_address_is_the_processors(clean_env, tmp_path):
    clean_env.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
    clean_env.setenv("BRIDGE_EMAIL_TOKEN_SECRET", "a-different-env-secret")
    board = make_board()
    proc = _proc(board, tmp_path)
    app = build_dashboard_app(rfps=board, email=proc, email_drain_interval=0)
    body = TestClient(app).get(f"/rfps/{RFP_ID}").json()
    shown = {c["party"]: c["reply_address"] for c in body["carriers"]}
    assert shown == {p: proc.reply_address(RFP_ID, p) for p in shown}
    assert shown  # carriers were listed


# --------------------------------------------------------------------------- #
# 10. CLI drain error paths                                                    #
# --------------------------------------------------------------------------- #
def test_cli_drain_without_url_is_a_usage_error(clean_env):
    with pytest.raises(SystemExit) as exc:
        cli(["drain"])
    assert exc.value.code == 2


def test_cli_drain_url_from_env(intake_env, capsys):
    intake_env.setenv("BRIDGE_EMAIL_DRAIN_URL", OPS)
    app = build_rfp_demo_app(now=NOW)
    assert cli(["drain"], client=TestClient(app)) == 0
    assert capsys.readouterr().out.strip() == "drained 0"


def test_cli_drain_against_a_host_without_the_route_exits_1(clean_env, capsys):
    client = TestClient(build_dashboard_app())
    assert cli(["drain", "--url", "http://testserver"], client=client) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("drain failed: HTTP 404")


def test_cli_drain_connection_error_exits_1_without_traceback(clean_env, capsys):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    assert cli(["drain", "--url", "http://127.0.0.1:9"], client=client) == 1
    err = capsys.readouterr().err
    assert err.startswith("drain failed: ConnectError")
    assert "Traceback" not in err


# --------------------------------------------------------------------------- #
# 11. Fresh-process import order (no circular import)                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "module",
    [
        "bridge.edge.email_ingress",
        "bridge.dashboard",
        "bridge.demos",
        "bridge.demos.rfp_demo",
        "bridge.demos.showcase",
        "bridge.dashboard.board_host",
        "bridge.edge.rfp_open",
        "bridge.edge.server",
        "bridge.edge.executor",
    ],
)
def test_module_imports_first_in_a_fresh_process(module):
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
