"""The Graph webhook + intake host (S0-arch-13): ``build_email_app`` over ASGI.

A fake Graph (``httpx.MockTransport``) serves the raw message; the port is the local
landing directory. Proves: the handshake echo, clientState authentication BEFORE any
Graph call, create-only landing (a re-delivery is a duplicate), 503-on-transient so
Graph retries, the 413 body cap, and the drain / outcome routes.
"""

from __future__ import annotations

import json

import httpx
import pytest
from google.adk.artifacts import InMemoryArtifactService

from bridge.edge.email_ingress import EmailProcessor, build_email_app, oversize_key
from bridge.email_ingress.graph import GraphMailClient
from bridge.email_ingress.mime import landing_key
from bridge.ports.local import LocalEmailIngress
from contract.enums import RfpItemStatus
from tests.email_fixtures import (
    BASE_URL,
    CI_PDF,
    CI_PDF_BYTES,
    MAILBOX,
    RFP_ID,
    SECRET,
    compose,
    extractor,
    make_board,
)

STATE = "s3cret"
URL = "/email/graph/notifications"


class FakeGraph:
    def __init__(self):
        self.messages: dict[str, bytes] = {}
        self.fail_with: int | None = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if self.fail_with:
            return httpx.Response(self.fail_with)
        prefix = f"/v1.0/users/{MAILBOX}/messages/"
        path = request.url.path
        if path.startswith(prefix) and path.endswith("/$value"):
            mid = path[len(prefix) : -len("/$value")]
            if mid in self.messages:
                return httpx.Response(200, content=self.messages[mid])
        return httpx.Response(404)

    @property
    def fetches(self) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith("/$value"))


def _notification(mid="AAMk1", state=STATE, **extra) -> dict:
    return {
        "value": [
            {
                "subscriptionId": "sub-1",
                "clientState": state,
                "changeType": "created",
                "resource": "Users/abc/Messages/" + mid,
                "resourceData": {"id": mid},
                **extra,
            }
        ]
    }


@pytest.fixture
def env(tmp_path):
    fake = FakeGraph()
    graph = GraphMailClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox=MAILBOX,
        transport=httpx.MockTransport(fake),
    )
    port = LocalEmailIngress(tmp_path)
    board = make_board()
    proc = EmailProcessor(
        board=board,
        port=port,
        mailbox=MAILBOX,
        secret=SECRET,
        extraction=extractor(),
        artifact_store=InMemoryArtifactService(),
        base_url=BASE_URL,
    )
    return fake, graph, port, board, proc


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_validation_token_handshake_echoes_plain_text(env):
    fake, graph, port, *_ = env
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        resp = await c.post(f"{URL}?validationToken=abc%20123")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == "abc 123"
        assert (await c.post(f"{URL}?validationToken={'x' * 1025}")).status_code == 400
    assert fake.requests == []


async def test_valid_notification_lands_once_and_a_resend_is_a_duplicate(env):
    fake, graph, port, *_ = env
    raw = compose("rfp-quotes@broker.example")
    fake.messages["AAMk1"] = raw
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        resp = await c.post(URL, json=_notification())
        assert resp.status_code == 202
        assert resp.json() == {"landed": 1, "duplicates": 0, "ignored": 0}
        assert fake.fetches == 1
        resp = await c.post(URL, json=_notification())
        assert resp.json() == {"landed": 0, "duplicates": 1, "ignored": 0}
    assert port.pending() == [landing_key(raw)]
    assert port.read(landing_key(raw)) == raw


@pytest.mark.parametrize(
    "note",
    [
        _notification(state="wrong"),
        _notification(state=None),
        _notification(mid="../../users/ceo@x/messages/1"),
        _notification(tenantId="other-tenant"),
        {"value": ["not-an-object"]},
    ],
    ids=["wrong-state", "no-state", "bad-id", "wrong-tenant", "non-object"],
)
async def test_unauthenticated_or_malformed_items_are_ignored_without_graph(env, note):
    fake, graph, port, *_ = env
    app = build_email_app(
        port=port, graph=graph, client_state=STATE, tenant_id="tenant-1"
    )
    async with _client(app) as c:
        resp = await c.post(URL, json=note)
    assert resp.status_code == 202
    assert resp.json() == {"landed": 0, "duplicates": 0, "ignored": 1}
    assert fake.requests == []  # zero Graph traffic, token included
    assert port.pending() == []


async def test_graph_failure_is_503_and_nothing_lands(env):
    fake, graph, port, *_ = env
    fake.fail_with = 500
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        resp = await c.post(URL, json=_notification())
    assert resp.status_code == 503
    assert port.pending() == []


async def test_storage_failure_is_503(env, monkeypatch):
    fake, graph, port, *_ = env
    fake.messages["AAMk1"] = compose("rfp-quotes@broker.example")

    def broken(key, raw):
        raise OSError("disk full")

    monkeypatch.setattr(port, "land", broken)
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        assert (await c.post(URL, json=_notification())).status_code == 503


async def test_oversize_message_is_recorded_not_retried(env, monkeypatch):
    fake, graph, port, *_ = env
    fake.messages["AAMk1"] = b"x" * 4096
    monkeypatch.setenv("BRIDGE_EMAIL_MAX_BYTES", "1024")
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        resp = await c.post(URL, json=_notification())
        assert resp.status_code == 202 and resp.json()["ignored"] == 1
        # The public webhook-only host serves no marker reads (review fix).
        assert (
            await c.get(f"/email/outcomes/{oversize_key('AAMk1')}")
        ).status_code == 404
    assert port.processed(oversize_key("AAMk1"))["reason"] == "oversize"
    assert port.pending() == []


async def test_a_garbled_token_response_is_503_not_an_oversize_ack(env):
    # Review fix: only MessageTooLarge is acked; any other fetch failure must make
    # Graph retry, or the mail is acknowledged without ever landing.
    fake, _graph, port, *_ = env
    fake.messages["AAMk1"] = compose("rfp-quotes@broker.example")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, content=b"<html>not json</html>")
        return fake(request)

    graph = GraphMailClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox=MAILBOX,
        transport=httpx.MockTransport(handler),
    )
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        assert (await c.post(URL, json=_notification())).status_code == 503
    assert port.processed(oversize_key("AAMk1")) is None
    assert port.pending() == []


def test_malformed_max_bytes_env_fails_at_startup(env, monkeypatch):
    _, graph, port, board, _ = env
    monkeypatch.setenv("BRIDGE_EMAIL_MAX_BYTES", "25MB")
    with pytest.raises(ValueError, match="BRIDGE_EMAIL_MAX_BYTES"):
        build_email_app(port=port, graph=graph, client_state=STATE)
    with pytest.raises(ValueError, match="BRIDGE_EMAIL_MAX_BYTES"):
        EmailProcessor(
            board=board,
            port=port,
            mailbox=MAILBOX,
            secret=SECRET,
            extraction=extractor(),
            artifact_store=InMemoryArtifactService(),
            base_url=BASE_URL,
        )


async def test_body_cap_and_bad_json(env):
    _, graph, port, *_ = env
    async with _client(
        build_email_app(port=port, graph=graph, client_state=STATE)
    ) as c:
        big = json.dumps({"value": [], "pad": "x" * (256 * 1024)})
        assert (await c.post(URL, content=big)).status_code == 413
        assert (await c.post(URL, content=b"{not json")).status_code == 400
        assert (await c.post(URL, json={"value": {}})).status_code == 400


async def test_no_graph_bound_is_404_and_graph_requires_client_state(env):
    _, graph, port, *_ = env
    async with _client(build_email_app(port=port)) as c:
        assert (await c.post(URL, json=_notification())).status_code == 404
        assert (await c.post("/email/drain")).status_code == 404
    with pytest.raises(ValueError, match="client_state"):
        build_email_app(port=port, graph=graph)


async def test_processor_bound_drain_files_and_outcome_route(env):
    fake, graph, _port, board, proc = env
    raw = compose(
        proc.reply_address(RFP_ID, "carrier-a"),
        attachments=[(CI_PDF.name, CI_PDF_BYTES, "application/pdf")],
    )
    fake.messages["AAMk1"] = raw
    key = landing_key(raw)
    async with _client(
        build_email_app(graph=graph, client_state=STATE, processor=proc)
    ) as c:
        resp = await c.post(URL, json=_notification())
        assert resp.json()["landed"] == 1
        # The background drain already filed it; an explicit drain finds nothing new.
        assert (await c.post("/email/drain")).json() == []
        marker = (await c.get(f"/email/outcomes/{key}")).json()
        assert marker["status"] == "filed" and marker["party"] == "carrier-a"
        assert (await c.get("/email/outcomes/not-a-key")).status_code == 404
        assert (await c.get(f"/email/outcomes/{'0' * 32}")).status_code == 404
    cell = board.rfps[RFP_ID].cell("carrier-a", "CRITICAL_ILLNESS")
    assert cell.status is RfpItemStatus.received


async def test_explicit_drain_route_returns_outcomes(env):
    _, _, port, _, proc = env
    raw = compose(proc.reply_address(RFP_ID, "carrier-b"))
    port.land(landing_key(raw), raw)
    async with _client(build_email_app(processor=proc)) as c:
        (outcome,) = (await c.post("/email/drain")).json()
    assert (outcome["status"], outcome["reason"], outcome["party"]) == (
        "filed",
        "no_attachments",
        "carrier-b",
    )


async def test_background_drain_failure_is_logged_not_raised(env, caplog):
    fake, graph, port, _, proc = env

    class Flaky:
        async def extract(self, name):
            raise RuntimeError("503 upstream")

    proc.extraction = Flaky()
    raw = compose(
        proc.reply_address(RFP_ID, "carrier-a"),
        attachments=[(CI_PDF.name, CI_PDF_BYTES, "application/pdf")],
    )
    fake.messages["AAMk1"] = raw
    async with _client(
        build_email_app(graph=graph, client_state=STATE, processor=proc)
    ) as c:
        assert (await c.post(URL, json=_notification())).status_code == 202
    assert port.pending() == [landing_key(raw)]  # still queued for the next drain
    assert any("background email drain failed" in r.message for r in caplog.records)
