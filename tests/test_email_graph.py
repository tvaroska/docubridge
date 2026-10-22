"""Graph mail transport (S0-arch-13) over an ``httpx.MockTransport`` — credential-free.

The live leg (``BRIDGE_TEST_GRAPH=1`` + the ``BRIDGE_EMAIL_GRAPH_*`` /
``BRIDGE_EMAIL_MAILBOX`` env) fetches the newest Inbox message's raw MIME.
"""

from __future__ import annotations

import json
import os
from urllib.parse import parse_qs

import httpx
import pytest

from bridge.email_ingress.graph import GraphError, GraphMailClient, MessageTooLarge
from bridge.email_ingress.mime import parse_mime

MAILBOX = "rfp-quotes@broker.example"
TOKEN_URL = "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
VALUE_URL = (
    f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/AAMk%2B1%3D/$value"
)
RAW = b"Message-ID: <x@y>\r\nSubject: hi\r\n\r\nbody\r\n"


class FakeGraph:
    def __init__(self, *, value_status=200, value_body=RAW, headers=None):
        self.requests: list[httpx.Request] = []
        self.value_status = value_status
        self.value_body = value_body
        self.headers = headers or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(
                200, json={"access_token": "tok-1", "expires_in": 3599}
            )
        if request.url.path.endswith("/$value"):
            return httpx.Response(
                self.value_status, content=self.value_body, headers=self.headers
            )
        if (
            request.url.path.endswith("/subscriptions")
            or "/subscriptions/" in request.url.path
        ):
            body = json.loads(request.content or b"{}")
            return httpx.Response(201, json={"id": "sub-1", **body})
        return httpx.Response(404)


def _client(fake: FakeGraph) -> GraphMailClient:
    return GraphMailClient(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret="secret-1",
        mailbox=MAILBOX,
        transport=httpx.MockTransport(fake),
    )


def test_token_request_form_and_the_token_is_cached():
    fake = FakeGraph()
    client = _client(fake)
    assert client.fetch_mime("AAMk+1=") == RAW
    assert client.fetch_mime("AAMk+1=") == RAW
    token_calls = [
        r for r in fake.requests if r.url.host == "login.microsoftonline.com"
    ]
    assert len(token_calls) == 1
    (token,) = token_calls
    assert str(token.url) == TOKEN_URL and token.method == "POST"
    form = {k: v[0] for k, v in parse_qs(token.content.decode()).items()}
    assert form == {
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default",
        "client_id": "client-1",
        "client_secret": "secret-1",
    }


def test_value_url_is_under_the_configured_mailbox_with_a_quoted_id():
    fake = FakeGraph()
    _client(fake).fetch_mime("AAMk+1=")
    fetch = fake.requests[-1]
    assert str(fetch.url) == VALUE_URL
    assert fetch.headers["authorization"] == "Bearer tok-1"


def test_redirect_is_not_followed():
    fake = FakeGraph(value_status=302, headers={"location": "https://evil.example/x"})
    with pytest.raises(GraphError) as err:
        _client(fake).fetch_mime("AAMk1")
    assert err.value.status == 302
    assert all(r.url.host != "evil.example" for r in fake.requests)


def test_non_2xx_is_a_graph_error_with_status():
    with pytest.raises(GraphError) as err:
        _client(FakeGraph(value_status=500)).fetch_mime("AAMk1")
    assert err.value.status == 500


def test_transport_error_is_a_graph_error():
    def boom(request):
        raise httpx.ConnectError("down", request=request)

    client = GraphMailClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox=MAILBOX,
        transport=httpx.MockTransport(boom),
    )
    with pytest.raises(GraphError):
        client.fetch_mime("AAMk1")


def test_oversize_stream_is_refused(monkeypatch):
    big = b"x" * 2048
    with pytest.raises(ValueError, match="oversize"):
        _client(FakeGraph(value_body=big)).fetch_mime("AAMk1", max_bytes=1024)
    monkeypatch.setenv("BRIDGE_EMAIL_MAX_BYTES", "1024")
    with pytest.raises(ValueError, match="oversize"):
        _client(FakeGraph(value_body=big)).fetch_mime("AAMk1")


def test_oversize_is_its_own_refusal_type():
    with pytest.raises(MessageTooLarge):
        _client(FakeGraph(value_body=b"x" * 2048)).fetch_mime("AAMk1", max_bytes=1024)
    with pytest.raises(MessageTooLarge):  # refused on the declared length alone
        _client(
            FakeGraph(value_body=b"x" * 2048, headers={"content-length": "2048"})
        ).fetch_mime("AAMk1", max_bytes=1024)


@pytest.mark.parametrize(
    "token_response",
    [
        httpx.Response(200, content=b"<html>proxy login</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"access_token": "t", "expires_in": "soon"}),
        httpx.Response(200, json={"expires_in": 3599}),
    ],
)
def test_garbled_token_response_is_a_graph_error_never_a_value_error(token_response):
    # Review fix: a ValueError here used to read as "oversize" to the webhook, which
    # acked the notification without landing the mail.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return token_response
        return httpx.Response(200, content=RAW)

    client = GraphMailClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox=MAILBOX,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GraphError) as info:
        client.fetch_mime("AAMk1")
    assert not isinstance(info.value, ValueError)


@pytest.mark.parametrize(
    "bad", ["", "../users/ceo@x/messages/1", "a b", "x" * 513, "a?b"]
)
def test_invalid_id_issues_no_request(bad):
    fake = FakeGraph()
    with pytest.raises(ValueError):
        _client(fake).fetch_mime(bad)
    assert fake.requests == []


def test_subscription_body_shape_and_renew_patch():
    fake = FakeGraph()
    client = _client(fake)
    sub = client.create_subscription(
        "https://hook.example/email/graph/notifications", "s3cret", minutes=60
    )
    req = fake.requests[-1]
    assert req.method == "POST"
    assert str(req.url) == "https://graph.microsoft.com/v1.0/subscriptions"
    body = json.loads(req.content)
    assert body["changeType"] == "created"
    assert body["resource"] == f"users/{MAILBOX}/mailFolders('Inbox')/messages"
    assert body["notificationUrl"] == "https://hook.example/email/graph/notifications"
    assert body["clientState"] == "s3cret"
    assert body["expirationDateTime"].endswith("Z")
    assert sub["id"] == "sub-1"

    client.renew_subscription("sub-1", minutes=120)
    req = fake.requests[-1]
    assert req.method == "PATCH"
    assert str(req.url) == "https://graph.microsoft.com/v1.0/subscriptions/sub-1"
    assert set(json.loads(req.content)) == {"expirationDateTime"}


def test_subscription_inputs_are_validated():
    client = _client(FakeGraph())
    with pytest.raises(ValueError):
        client.create_subscription("http://plain.example/hook", "s3cret")
    with pytest.raises(ValueError):
        client.create_subscription("https://hook.example/x", "s" * 129)
    with pytest.raises(ValueError):
        client.renew_subscription("../me")


def test_from_env_names_the_missing_env(monkeypatch):
    for name in (
        "BRIDGE_EMAIL_GRAPH_TENANT",
        "BRIDGE_EMAIL_GRAPH_CLIENT_ID",
        "BRIDGE_EMAIL_GRAPH_CLIENT_SECRET",
        "BRIDGE_EMAIL_MAILBOX",
    ):
        monkeypatch.setenv(name, "set")
    monkeypatch.delenv("BRIDGE_EMAIL_GRAPH_CLIENT_SECRET")
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_GRAPH_CLIENT_SECRET"):
        GraphMailClient.from_env()
    monkeypatch.setenv("BRIDGE_EMAIL_GRAPH_CLIENT_SECRET", "set")
    assert GraphMailClient.from_env().mailbox == "set"


@pytest.mark.skipif(
    os.environ.get("BRIDGE_TEST_GRAPH") != "1",
    reason="set BRIDGE_TEST_GRAPH=1 + BRIDGE_EMAIL_GRAPH_* / BRIDGE_EMAIL_MAILBOX to run",
)
def test_live_fetch_newest_inbox_message():  # pragma: no cover - live
    client = GraphMailClient.from_env()
    ids = client.latest_message_ids(1)
    if not ids:
        pytest.skip("the intake mailbox Inbox is empty")
    raw = client.fetch_mime(ids[0])
    assert parse_mime(raw).message_id
