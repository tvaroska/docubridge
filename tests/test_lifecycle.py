"""P4-5 durable lifecycle tests: escalated-status restart proof + durable cancel."""

import uuid

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    TaskState,
)
from a2a.utils.constants import TransportProtocol

from bridge.edge import build_mock_bridge_app, encode_collect_request
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from bridge.ports import Port, get_adapter
from contract.enums import LifecycleStatus
from contract.wire import CollectRequest, ExchangeTurn


async def test_escalated_status_survives_restart(tmp_path):
    """P4-5 §5.3: escalated lifecycle status persists across task_store dispose/reopen.

    Drive an escalated-stall scenario through the mock with a durable task store,
    dispose the engine, rebuild on the same DSN, and verify the persisted task reads
    back with lifecycle == escalated (the restart proof).
    """
    from google.protobuf.json_format import MessageToDict

    dsn = f"sqlite+aiosqlite:///{tmp_path}/lifecycle.db"
    url = "http://test/"
    base = "http://test"

    # Build task store via port adapter
    task_store = get_adapter(Port.task_store, "local").build_task_store(db_url=dsn)

    # Build mock app with durable task store
    app = build_mock_bridge_app(url=url, task_store=task_store)

    # Send escalated-stall scenario
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(url)

        # Collect message for escalated-stall scenario
        parts = encode_collect_request(
            CollectRequest(process="address-proof", message="scenario:escalated-stall")
        )
        msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

        # Send message and get task_id
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id

        assert task_id is not None

        # Verify task is escalated and non-terminal
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        # Extract exchange_turn from artifacts
        turn = None
        for artifact in task.artifacts:
            for part in artifact.parts:
                if not part.HasField("data"):
                    continue
                meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
                if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                    turn = ExchangeTurn.model_validate(MessageToDict(part.data))

        assert turn is not None
        assert turn.lifecycle == LifecycleStatus.escalated
        assert turn.terminal is False

    # Dispose the engine (the restart boundary)
    await task_store.engine.dispose()

    # Rebuild task store on the same DSN
    task_store2 = get_adapter(Port.task_store, "local").build_task_store(db_url=dsn)
    app2 = build_mock_bridge_app(url=url, task_store=task_store2)

    # Read the task back through a fresh client
    transport2 = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport2, base_url=base) as hc2:
        config2 = ClientConfig(
            httpx_client=hc2,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client2 = await ClientFactory(config2).create_from_url(url)

        # Get the same task_id from the new store
        task_restored = await client2.get_task(GetTaskRequest(id=task_id))

        # Verify escalated status survived restart
        assert task_restored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        # Extract exchange_turn from artifacts
        turn_restored = None
        for artifact in task_restored.artifacts:
            for part in artifact.parts:
                if not part.HasField("data"):
                    continue
                meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
                if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                    turn_restored = ExchangeTurn.model_validate(
                        MessageToDict(part.data)
                    )

        assert turn_restored is not None
        assert turn_restored.lifecycle == LifecycleStatus.escalated
        assert turn_restored.terminal is False


async def test_durable_cancel_survives_restart(tmp_path):
    """P4-5 §5.4: tasks/cancel on a durable non-terminal task persists across restart."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/cancel.db"
    url = "http://test/"
    base = "http://test"

    # Build task store via port adapter
    task_store = get_adapter(Port.task_store, "local").build_task_store(db_url=dsn)
    app = build_mock_bridge_app(url=url, task_store=task_store)

    # Send non-terminal scenario (path-b-two-bills parks at INPUT_REQUIRED)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(url)

        parts = encode_collect_request(
            CollectRequest(process="address-proof", message="scenario:path-b-two-bills")
        )
        msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

        # Send message and get task_id
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id

        assert task_id is not None

        # Verify task is non-terminal
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        # Cancel the task
        result = await client.cancel_task(CancelTaskRequest(id=task_id))
        assert result.status.state == TaskState.TASK_STATE_CANCELED

    # Dispose the engine (the restart boundary)
    await task_store.engine.dispose()

    # Rebuild task store on the same DSN
    task_store2 = get_adapter(Port.task_store, "local").build_task_store(db_url=dsn)
    app2 = build_mock_bridge_app(url=url, task_store=task_store2)

    # Read the task back through a fresh client
    transport2 = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport2, base_url=base) as hc2:
        config2 = ClientConfig(
            httpx_client=hc2,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client2 = await ClientFactory(config2).create_from_url(url)

        # Verify canceled state survived restart
        task_restored = await client2.get_task(GetTaskRequest(id=task_id))
        assert task_restored.status.state == TaskState.TASK_STATE_CANCELED
