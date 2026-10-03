"""P4-4 push notification config tests (DUR-6).

Tests the four push-config ops (create/get/list/delete) and webhook emission.
Reuses the ``_client`` / ``_fixture_app`` / ``_send`` harness from
``test_edge_a2a.py``. The webhook receiver is a mock httpx transport that records
POSTs into a shared list.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import (
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigsRequest,
    Message,
    Role,
    SendMessageRequest,
    TaskPushNotificationConfig,
)
from a2a.utils.constants import TransportProtocol
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.extraction.fixture import FixtureExtractor
from contract.wire import CollectRequest

BASE = "http://test"
URL = "http://test/"

# Corpus fixture (reuse id from wiki/evals/address/expected.json).
ACCEPTED = ("gov-id-clean", "images/gov-id-clean.svg")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _fixture_app_with_push(push_httpx_client):
    """Edge app with a push sender bound to the supplied httpx client."""
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    return build_a2a_app(url=URL, executor=executor, push_httpx_client=push_httpx_client)


@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(URL)
        yield hc, client


def _url_doc_part(doc_id: str, uri: str, mime: str = "image/svg+xml"):
    part = ph.new_url_part(uri, media_type=mime)
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    return part


def _collect_message(process: str, docs: list[tuple[str, str]]) -> Message:
    parts = encode_collect_request(CollectRequest(process=process))
    parts += [_url_doc_part(doc_id, uri) for doc_id, uri in docs]
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


async def _send(client, process: str, docs: list[tuple[str, str]]):
    """message/send; return (task_id, events)."""
    events = []
    task_id = None
    async for ev in client.send_message(
        SendMessageRequest(message=_collect_message(process, docs))
    ):
        events.append(ev)
        if ev.HasField("task"):
            task_id = ev.task.id
        elif ev.HasField("status_update"):
            task_id = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            task_id = ev.artifact_update.task_id
    return task_id, events


class MockWebhookRecorder:
    """Mock httpx transport that records webhook POSTs."""

    def __init__(self):
        self.posts = []
        self.requests = []

    async def handle_request(self, request):
        """Record all requests and POST bodies, return 200."""
        method = request.method
        method_bytes = method if isinstance(method, bytes) else method.encode()
        self.requests.append((method, str(request.url)))
        if method_bytes == b"POST":
            body = await request.aread()
            self.posts.append(body)
        return httpx.Response(200, json={"status": "ok"})


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #
async def test_push_config_crud_round_trip():
    """Create/get/list/delete a TaskPushNotificationConfig with no error.

    The card advertises push_notifications=True and a push_config_store is wired,
    so the ops are reachable (no PushNotificationNotSupportedError).
    """
    recorder = MockWebhookRecorder()
    mock_transport = httpx.MockTransport(recorder.handle_request)
    push_client = httpx.AsyncClient(transport=mock_transport, base_url="http://receiver")

    async with _client(_fixture_app_with_push(push_client)) as (_hc, client):
        # Send to create a task
        task_id, _events = await _send(client, "address-proof", [ACCEPTED])
        assert task_id is not None

        # Create a push notification config
        config = TaskPushNotificationConfig(
            tenant="test-tenant",
            id=uuid.uuid4().hex,
            task_id=task_id,
            url="http://receiver/hook",
        )
        created = await client.create_task_push_notification_config(config)
        assert created.id == config.id
        assert created.task_id == task_id

        # Get the config
        fetched = await client.get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(
                tenant=config.tenant, task_id=task_id, id=config.id
            )
        )
        assert fetched.id == config.id
        assert fetched.task_id == task_id

        # List configs
        response = await client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(tenant=config.tenant, task_id=task_id)
        )
        configs = response.configs
        assert any(c.id == config.id for c in configs)

        # Delete the config
        await client.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(
                tenant=config.tenant, task_id=task_id, id=config.id
            )
        )

        # List again - should be empty
        response_after = await client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(tenant=config.tenant, task_id=task_id)
        )
        configs_after = response_after.configs
        assert not any(c.id == config.id for c in configs_after)


async def test_webhook_fires_on_task_events():
    """A registered webhook receives a POST doorbell when the task status/artifact changes.

    Use the mock park scenario (path-b-two-bills): register the config after turn
    1 parks, then drive the resume turn and assert the resume's status update
    fired a webhook.
    """
    from bridge.edge import build_mock_bridge_app

    recorder = MockWebhookRecorder()
    mock_transport = httpx.MockTransport(recorder.handle_request)
    push_client = httpx.AsyncClient(transport=mock_transport, base_url="http://receiver")

    # Use mock bridge for multi-turn scenario
    app = build_mock_bridge_app(url=URL, push_httpx_client=push_client)

    async with _client(app) as (_hc, client):
        # Turn 1: park on path-b-two-bills
        message1 = _collect_message("address-proof", [ACCEPTED])
        # Add scenario marker via CollectRequest message field
        from bridge.edge import encode_collect_request
        from contract.wire import CollectRequest
        parts = encode_collect_request(
            CollectRequest(process="address-proof", message="scenario:path-b-two-bills")
        )
        message1 = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

        task_id = None
        context_id = None
        async for ev in client.send_message(SendMessageRequest(message=message1)):
            if ev.HasField("task"):
                task_id = ev.task.id
                context_id = ev.task.context_id

        assert task_id is not None
        assert context_id is not None

        # Register a push config for this task
        config = TaskPushNotificationConfig(
            tenant="test-tenant",
            id=uuid.uuid4().hex,
            task_id=task_id,
            url="http://receiver/hook",
        )
        await client.create_task_push_notification_config(config)

        # Turn 2: resume on the same context_id + task_id
        # The resume will trigger status/artifact events which should fire webhooks
        message2 = Message(
            role=Role.ROLE_USER,
            message_id=uuid.uuid4().hex,
            parts=encode_collect_request(CollectRequest(process="address-proof")),
            context_id=context_id,
            task_id=task_id,
        )

        async for _ev in client.send_message(SendMessageRequest(message=message2)):
            pass

        # Assert the webhook receiver captured ≥1 POST
        assert len(recorder.posts) > 0, "Expected webhook to fire on resume events"

        # Assert at least one POST body mentions the task_id
        # The body is JSON-encoded PushNotificationEvent (Task/status/artifact)
        task_id_bytes = task_id.encode("utf-8")
        assert any(
            task_id_bytes in post for post in recorder.posts
        ), "Expected task_id in webhook POST body"
