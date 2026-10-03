"""CUJ-12 cross-framework interop golden run (S1-benefits-6).

The two simulated carriers are real external-zone A2A servers built on **different
frameworks** — Carrier A in Google ADK, Carrier B in LangGraph — yet the *same* A2A
quote-request conversation succeeds against both, and the Bridge-side
``normalize``/``standard`` reproduces each carrier's corpus ``expected_normalized`` /
``expected_gaps``. That is the CUJ-12 headline: framework-agnostic A2A.

Drivers:
- The conformant a2a-sdk client over ``httpx.ASGITransport`` (mirrors
  ``tests/test_edge_a2a.py``) drives the carriers in-process (no ports).
- One case dials Carrier B (LangGraph) from an **ADK ``RemoteA2aAgent``** consumer, the
  literal CUJ-12 claim (an ADK Bridge negotiating with a LangGraph counterparty).

Tests import ``bridge.normalize`` / ``bridge.standard`` freely (tests are exempt from
the ``agents/`` isolation guard).
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from google.adk.agents import BaseAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from langgraph.graph.state import CompiledStateGraph

import agents.benefits.carrier_a_adk as carrier_a_mod
import agents.benefits.carrier_b_langgraph as carrier_b_mod
from agents.address.wire import decode_part_payload
from agents.benefits.carrier_a_adk import build_carrier_a_app
from agents.benefits.carrier_b_langgraph import build_carrier_b_app
from agents.benefits.quotes import (
    CARRIER_A_DOC,
    CARRIER_B_V0_DOC,
    CARRIER_B_V2_DOC,
    load_quote,
)
from agents.benefits.wire import decode_quote
from bridge.normalize import normalize_from_extraction
from bridge.standard import (
    ROUTE_NEGOTIATE,
    ROUTE_NONE,
    flag_quote,
    load_program_standard,
)
from contract.extraction import ExtractionResult

# The one A2A conversation shape both carriers speak (plan §3).
QUOTE_TEXT = "request benefits-quote for program:acme-2026 ({party})"
REVISE_TEXT = "revise: coverage gap on orthodontics for program:acme-2026 (carrier-b)"

_PROGRAM_STANDARD = load_program_standard("benefits-procurement")


# --------------------------------------------------------------------------- #
# In-process conformant-client helpers (mirror tests/test_edge_a2a.py).       #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def _client(app, base: str):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(base + "/")
        yield hc, client


def _message(text: str, *, context_id: str | None = None) -> Message:
    msg = Message(
        role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=[ph.new_text_part(text)]
    )
    if context_id is not None:
        msg.context_id = context_id
    return msg


async def _send(client, text: str, *, context_id: str | None = None):
    """message/send; return the terminal task id + context id."""
    task_id = None
    ctx_id = context_id
    async for ev in client.send_message(
        SendMessageRequest(message=_message(text, context_id=context_id))
    ):
        if ev.HasField("task"):
            task_id = ev.task.id
            ctx_id = ev.task.context_id or ctx_id
        elif ev.HasField("status_update"):
            task_id = ev.status_update.task_id
            ctx_id = ev.status_update.context_id or ctx_id
        elif ev.HasField("artifact_update"):
            task_id = ev.artifact_update.task_id
    return task_id, ctx_id


def _quote_from_task(task) -> ExtractionResult:
    """Decode the benefits-quote artifact off a terminal task (select-by-metadata)."""
    parts = [part for artifact in task.artifacts for part in artifact.parts]
    return decode_quote(parts)


async def _quote_over_a2a(app, base: str, text: str, *, context_id=None):
    """Drive one quote turn over A2A and return (quote, context_id)."""
    async with _client(app, base) as (_hc, client):
        task_id, ctx_id = await _send(client, text, context_id=context_id)
        assert task_id is not None
        task = await client.get_task(GetTaskRequest(id=task_id))
        return _quote_from_task(task), ctx_id


# --------------------------------------------------------------------------- #
# 1. Same conversation, two frameworks, same terminal outcome.                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "build_app", "base", "doc_id"),
    [
        ("carrier-a", build_carrier_a_app, "http://carrier-a", CARRIER_A_DOC),
        ("carrier-b", build_carrier_b_app, "http://carrier-b", CARRIER_B_V0_DOC),
    ],
)
async def test_interop_same_conversation_both_builds(label, build_app, base, doc_id):
    """One quote-request conversation reproduces each carrier's expected_normalized.

    The SAME A2A conversation is driven against the ADK build (Carrier A) and the
    LangGraph build (Carrier B); each decoded quote, normalized via
    ``normalize_from_extraction``, equals that carrier's authored ``expected_normalized``.
    """
    app = build_app(url=base + "/")
    quote, _ctx = await _quote_over_a2a(app, base, QUOTE_TEXT.format(party=label))

    party = quote.fields.issuer or label
    normalized = normalize_from_extraction(party, quote)

    # The oracle: this doc's expected_normalized (loaded read-only from the corpus).
    expected = load_quote(doc_id)  # sanity: the carrier served this doc
    assert quote.fields.key_fields == expected.fields.key_fields
    # Reproduce the authored expected_normalized block.
    from agents.benefits.quotes import _corpus  # test-only oracle read

    expected_normalized = _corpus()[doc_id]["expected_normalized"]
    assert normalized.model_dump(mode="json") == expected_normalized


# --------------------------------------------------------------------------- #
# 2. Multi-turn revise against the LangGraph carrier.                          #
# --------------------------------------------------------------------------- #
async def test_carrier_b_multi_turn_revise_langgraph():
    """turn 1 quote → v0 flagged (negotiate); turn 2 revise (same ctx) → v2 conformant."""
    app = build_carrier_b_app(url="http://carrier-b/")

    async with _client(app, "http://carrier-b") as (_hc, client):
        # Turn 1: first quote → v0.
        t1_id, ctx_id = await _send(client, QUOTE_TEXT.format(party="carrier-b"))
        t1 = await client.get_task(GetTaskRequest(id=t1_id))
        v0 = _quote_from_task(t1)
        assert ctx_id is not None

        verdict_v0 = flag_quote(
            normalize_from_extraction("carrier-b", v0), _PROGRAM_STANDARD
        )
        assert verdict_v0.route == ROUTE_NEGOTIATE
        assert [g.rule_id for g in verdict_v0.gaps] == ["no_orthodontics_exclusion"]

        # Turn 2: revise under the SAME context_id → v2.
        t2_id, _ = await _send(client, REVISE_TEXT, context_id=ctx_id)
        t2 = await client.get_task(GetTaskRequest(id=t2_id))
        v2 = _quote_from_task(t2)

        verdict_v2 = flag_quote(
            normalize_from_extraction("carrier-b", v2), _PROGRAM_STANDARD
        )
        assert verdict_v2.route == ROUTE_NONE
        assert verdict_v2.gaps == []

    # The revise actually revised (not echoed): v0 excludes orthodontics, v2 does not.
    assert "orthodontics" in v0.fields.key_fields["exclusions"]
    assert "orthodontics" not in v2.fields.key_fields["exclusions"]
    assert v0.fields.key_fields != v2.fields.key_fields
    assert v0 == load_quote(CARRIER_B_V0_DOC)
    assert v2 == load_quote(CARRIER_B_V2_DOC)


# --------------------------------------------------------------------------- #
# 3. ADK RemoteA2aAgent consumer dials the LangGraph carrier.                  #
# --------------------------------------------------------------------------- #
async def test_adk_consumer_dials_langgraph_carrier():
    """An ADK RemoteA2aAgent (use_legacy=True) gets a decodable quote from LangGraph B."""
    # RemoteA2aAgent requires a loopback host (or https) for the card RPC URL.
    base = "http://localhost"
    app = build_carrier_b_app(url=base + "/")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base) as hc:
        remote = RemoteA2aAgent(
            name="carrier_b_remote",
            agent_card=base + AGENT_CARD_WELL_KNOWN_PATH,
            httpx_client=hc,
            use_legacy=True,  # TEST-4 pin (bridge/adk/experimental.py).
        )
        session_service = InMemorySessionService()
        await session_service.create_session(
            app_name="bridge-consumer", user_id="bridge", session_id="dial"
        )
        runner = Runner(
            agent=remote,
            app_name="bridge-consumer",
            session_service=session_service,
        )

        quote = None
        async for event in runner.run_async(
            user_id="bridge",
            session_id="dial",
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=QUOTE_TEXT.format(party="carrier-b"))],
            ),
        ):
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                payload = decode_part_payload(part)
                if payload is None:
                    continue
                try:
                    quote = ExtractionResult.model_validate(payload)
                except Exception:
                    continue

        assert quote is not None, "ADK consumer got no decodable benefits-quote"
        # Decodes to Carrier B's v0 (the first quote request).
        assert quote == load_quote(CARRIER_B_V0_DOC)


# --------------------------------------------------------------------------- #
# 4. Structural interop guard: the two carriers really are two frameworks.     #
# --------------------------------------------------------------------------- #
def test_carriers_are_framework_diverse():
    """Carrier A's internal is a google.adk BaseAgent; Carrier B's is a compiled LangGraph."""
    assert isinstance(carrier_a_mod.CARRIER_A_AGENT, BaseAgent)
    assert isinstance(carrier_b_mod.CARRIER_B_GRAPH, CompiledStateGraph)
    # And they are genuinely different framework types (no shared shim collapsing both).
    assert not isinstance(carrier_b_mod.CARRIER_B_GRAPH, BaseAgent)
    assert not isinstance(carrier_a_mod.CARRIER_A_AGENT, CompiledStateGraph)


# --------------------------------------------------------------------------- #
# 5. No bearer token on the carrier wire (outbound no-token discipline).       #
# --------------------------------------------------------------------------- #
async def test_no_bearer_token_on_carrier_wire():
    """The carrier request/response messages carry no token/cred/bearer field."""
    base = "http://carrier-a"
    app = build_carrier_a_app(url=base + "/")
    async with _client(app, base) as (_hc, client):
        task_id, _ctx = await _send(client, QUOTE_TEXT.format(party="carrier-a"))
        task = await client.get_task(GetTaskRequest(id=task_id))

    # Serialize the whole terminal task (message history + artifacts) and scan it.
    blob = json.dumps(task.model_dump(mode="json") if hasattr(task, "model_dump") else {})
    # Fall back to proto MessageToDict when task is a proto (a2a 1.x).
    if blob == "{}":
        from google.protobuf.json_format import MessageToDict

        blob = json.dumps(MessageToDict(task)).lower()
    else:
        blob = blob.lower()
    for forbidden in ("bearer", "authorization", "\"token\"", "credential"):
        assert forbidden not in blob, f"unexpected {forbidden!r} on the carrier wire"
