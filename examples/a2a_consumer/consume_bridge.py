"""Minimal external consumer: one Collect round-trip against a deployed Bridge.

Depends only on ``a2a-sdk`` (+ its ``httpx``), ``contract`` and the stdlib — never on
``bridge/`` or ``agents/``. See ``docs/consume-over-a2a.md`` for the contract it relies on.

    python examples/a2a_consumer/consume_bridge.py http://localhost:8000/ \\
        --process address-proof --doc gov-id-clean=images/gov-id-clean.svg
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Part, Role, SendMessageRequest, Task
from a2a.utils.constants import TransportProtocol
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from contract.wire import CollectRequest, ExchangeTurn, decode_wire

KIND_COLLECT_REQUEST = "collect_request"
KIND_EXCHANGE_TURN = "exchange_turn"


def _tagged_data_part(payload: dict, metadata: dict) -> Part:
    part = ph.new_data_part(payload)
    part.metadata.CopyFrom(ParseDict(metadata, struct_pb2.Struct()))
    return part


def collect_message(
    request: CollectRequest, documents: list[tuple[str, str]]
) -> Message:
    """A2A message: one tagged ``CollectRequest`` data part + by-reference document parts."""
    parts = [
        _tagged_data_part(
            request.model_dump(mode="json"),
            {
                "kind": KIND_COLLECT_REQUEST,
                "contract_version": request.contract_version,
            },
        )
    ]
    for doc_id, uri in documents:
        part = ph.new_url_part(uri, media_type="application/octet-stream")
        part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
        parts.append(part)
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


def exchange_turn_from(task: Task) -> ExchangeTurn:
    """Select the ``exchange_turn`` artifact part by its metadata tag, never by index."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get("kind") == KIND_EXCHANGE_TURN:
                return decode_wire(ExchangeTurn, MessageToDict(part.data))
    raise RuntimeError("task carries no exchange_turn artifact")


async def run(
    base_url: str,
    process: str,
    documents: list[tuple[str, str]],
    *,
    httpx_client: httpx.AsyncClient | None = None,
) -> ExchangeTurn:
    """Resolve the card, send one Collect, return the terminal ``ExchangeTurn``."""
    hc = httpx_client or httpx.AsyncClient(base_url=base_url, timeout=60.0)
    try:
        card = await A2ACardResolver(hc, base_url).get_agent_card()
        print(f"agent: {card.name} skills={[s.id for s in card.skills]}")
        if process not in {s.id for s in card.skills}:
            raise SystemExit(f"process {process!r} is not advertised by this Bridge")
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(base_url)
        message = collect_message(CollectRequest(process=process), documents)
        task_id = None
        async for event in client.send_message(SendMessageRequest(message=message)):
            if event.HasField("task"):
                task_id = event.task.id
            elif event.HasField("status_update"):
                task_id = event.status_update.task_id
        if task_id is None:
            raise RuntimeError("Bridge returned no task")
        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = exchange_turn_from(task)
        for entry in turn.status.entries:
            print(f"{entry.doc_id}: {entry.disposition.value} ({entry.gate.value})")
        print(f"terminal={turn.terminal} lifecycle={turn.lifecycle.value}")
        return turn
    finally:
        if httpx_client is None:
            await hc.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", help="Bridge origin, e.g. http://localhost:8000/")
    parser.add_argument("--process", required=True, help="an advertised skill id")
    parser.add_argument(
        "--doc", action="append", default=[], metavar="DOC_ID=URI", help="repeatable"
    )
    args = parser.parse_args(argv)
    documents = [(doc_id, uri) for doc_id, uri in (d.split("=", 1) for d in args.doc)]
    asyncio.run(run(args.base_url, args.process, documents))
    return 0


if __name__ == "__main__":
    sys.exit(main())
