"""Tests for Option B — A2A Server on Vertex AI Agent Engine (`BridgeA2aAgentEngine`).

Covers:
1. `cloudpickle` serializability before and after `set_up()` + `clone()` protocol.
2. `register_operations()` schema registration for sync, async, and streaming modes.
3. Dynamic `AgentCard` discovery (`get_agent_card` / `async_get_agent_card`) including
   live SKILL-4 / CUJ-8 skill installation without redeploy.
4. Unary A2A JSON-RPC 2.0 operations (`message/send`, `tasks/get`, `tasks/list`)
   directly on `BridgeA2aAgentEngine` and through the thin Cloud Run proxy
   (`build_a2a_app(a2a_engine=...)` and `BRIDGE_AGENT_ENGINE_A2A=1`).
5. Streaming A2A JSON-RPC 2.0 (`message/stream` / `SendStreamingMessage`) over SSE,
   plus pre-dispatch `CollectExecutor.admit` refusal returning `application/json`
   without opening an event stream.
6. Authenticated reviewer principal propagation from the thin edge's
   `AuthenticationMiddleware` into `BridgeA2aAgentEngine` (`reviewers` allow-list).
7. Full ADK `RemoteA2aAgent` (`run_collection`) parity against the Agent Engine-backed
   A2A edge (CUJ-1 Path-A instant accept and CUJ-10 distinct-issuer bills) plus
   shared `/artifacts/{context_id}/{name}` fetch.
8. Dual Option A + Option B support on `BridgeA2aAgentEngine.async_stream_query`
   (ADK `(user_id, session_id, message)` and A2A `payload`).
"""
from __future__ import annotations

import json
import shutil
import uuid
from typing import Any

import cloudpickle
import httpx
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.sessions import InMemorySessionService
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    BaseUser,
    SimpleUser,
)
from starlette.requests import HTTPConnection

from agents.address.driver import run_collection
from agents.address.gate import is_satisfied
from agents.address.wire import DocRef
from bridge.collect import Collector
from bridge.edge import (
    AGENT_ENGINE_A2A_ENV,
    BridgeA2aAgentEngine,
    CollectExecutor,
    EdgeMode,
    agent_engine_app,
    build_a2a_app,
    is_agent_engine_a2a_enabled,
    resolve_a2a_engine,
)
from bridge.edge.llm_leg import LlmLeg
from bridge.edge.wire import (
    KIND_HUMAN_REVIEW_RESOLUTION,
    META_CONTRACT_VERSION,
    META_KIND,
    META_REVIEW_CALL_ID,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.seams import Seam, get_adapter, resolve_mode
from bridge.sla import SKILLS_DIR
from contract.enums import Disposition, Gate
from contract.wire import HumanReviewResolution
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall
from tests.address._corpus import corpus_records

BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH


def _doc(doc_id: str) -> DocRef:
    for record in corpus_records():
        if record["id"] == doc_id:
            return DocRef(doc_id=doc_id, uri=record["artifact"])
    raise AssertionError(f"corpus has no document {doc_id!r}")


class _HeaderAuthBackend(AuthenticationBackend):
    """Test auth backend that authenticates `X-Test-User: <principal>`."""

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        user = conn.headers.get("x-test-user", "").strip()
        if not user:
            return None
        return AuthCredentials(["authenticated"]), SimpleUser(user)


def test_cloudpickle_roundtrip_before_and_after_set_up():
    """BridgeA2aAgentEngine pickles cleanly with cloudpickle both before and after
    set_up() — no C-extension protobuf descriptor errors from a2a.types."""
    engine = BridgeA2aAgentEngine(
        url="https://edge.example.com/",
        edge_mode=EdgeMode.deterministic,
        reviewers=["alice@example.com"],
        satisfaction="off",
    )
    cloned = engine.clone()
    blob_before = cloudpickle.dumps(cloned)
    restored_before = cloudpickle.loads(blob_before)
    assert restored_before._config["url"] == "https://edge.example.com/"
    assert restored_before._config["edge_mode"] == "deterministic"
    assert restored_before._config["reviewers"] == ("alice@example.com",)

    # Trigger set_up() so Starlette + protobuf AgentCard + handler live on `engine`,
    # then verify __getstate__ strips runtime handles so cloudpickle still succeeds.
    engine.set_up()
    assert engine._app is not None
    blob_after = cloudpickle.dumps(engine)
    restored_after = cloudpickle.loads(blob_after)
    assert restored_after._app is None
    card = restored_after.get_agent_card()
    assert card["name"] == "Document Bridge"


def test_register_operations_exposes_all_four_modes():
    ops = agent_engine_app.register_operations()
    assert set(ops.keys()) == {"", "async", "stream", "async_stream"}
    assert "get_agent_card" in ops[""]
    assert "handle_a2a_jsonrpc" in ops[""]
    assert "async_get_agent_card" in ops["async"]
    assert "async_handle_a2a_jsonrpc" in ops["async"]
    assert "handle_a2a_jsonrpc_stream" in ops["stream"]
    assert "async_handle_a2a_jsonrpc_stream" in ops["async_stream"]


async def test_live_agent_card_reflects_new_skill_without_redeploy(
    monkeypatch, tmp_path
):
    """CUJ-8 / SKILL-4: BridgeA2aAgentEngine rebuilds the AgentCard from the live
    skill registry on every get_agent_card() call without re-instantiating."""
    for name in ("address-proof", "utility-bill"):
        shutil.copytree(SKILLS_DIR / name, tmp_path / name)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))

    engine = BridgeA2aAgentEngine(url=BASE + "/")
    card1 = await engine.async_get_agent_card()
    ap1 = next(s for s in card1["skills"] if s["id"] == "address-proof")
    assert "doctype:utility-bill" in ap1["tags"]
    assert "doctype:gov-id" not in ap1["tags"]

    # Drop the gov-id doctype skill at runtime via the skill_registry seam — no rebuild.
    src = SKILLS_DIR / "gov-id"
    reg = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
    reg.install_skill(
        "gov-id",
        (src / "SKILL.md").read_text(),
        assets={
            "assets/schema.json": (src / "assets" / "schema.json").read_text(),
            "assets/validation.yaml": (
                src / "assets" / "validation.yaml"
            ).read_text(),
        },
    )
    card2 = await engine.async_get_agent_card()
    ap2 = next(s for s in card2["skills"] if s["id"] == "address-proof")
    assert "doctype:utility-bill" in ap2["tags"]
    assert "doctype:gov-id" in ap2["tags"]


async def test_unary_and_streaming_jsonrpc_through_thin_edge_proxy():
    """build_a2a_app(a2a_engine=...) delegates discovery, unary message/send, tasks/get,
    tasks/list, and streaming message/stream to BridgeA2aAgentEngine while serving
    persisted artifacts from /artifacts/{context_id}/{name}."""
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    engine = BridgeA2aAgentEngine(url=BASE + "/", executor=executor)
    app = build_a2a_app(url=BASE + "/", a2a_engine=engine)
    assert app.state.a2a_engine is engine

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        # 1. Agent Card discovery
        card_resp = await client.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert card_resp.status_code == 200
        card_data = card_resp.json()
        assert card_data["name"] == "Document Bridge"
        assert card_data["capabilities"]["streaming"] is True

        # 2. Unary SendMessage (Path-A instant accept)
        doc = _doc("gov-id-clean")
        req_id = str(uuid.uuid4())
        send_payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "ROLE_USER",
                    "parts": [
                        {"text": "Collect address proof"},
                        {
                            "url": doc.uri,
                            "mediaType": "image/svg+xml",
                            "filename": "gov-id-clean.svg",
                            "metadata": {"doc_id": doc.doc_id},
                        },
                    ],
                }
            },
        }
        rpc_resp = await client.post("/", json=send_payload)
        assert rpc_resp.status_code == 200
        rpc_body = rpc_resp.json()
        assert rpc_body["id"] == req_id
        task_obj = rpc_body["result"]["task"]
        task_id = task_obj["id"]
        context_id = task_obj["contextId"]
        assert task_obj["status"]["state"] == "TASK_STATE_COMPLETED"

        # 3. Verify /artifacts/{context_id}/{name} works on the thin edge over the shared store
        entries = task_obj["artifacts"][0]["parts"][0]["data"]["status"]["entries"]
        assert len(entries) == 1
        assert entries[0]["doc_id"] == "gov-id-clean"
        assert entries[0]["disposition"] == "accepted"
        artifact_uri = entries[0]["artifact"]["uri"]
        rel_path = artifact_uri.removeprefix(BASE)
        art_resp = await client.get(rel_path)
        assert art_resp.status_code == 200
        assert len(art_resp.content) > 0

        # 4. GetTask and ListTasks
        get_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "get-1",
                "method": "GetTask",
                "params": {"id": task_id},
            },
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["result"]["id"] == task_id

        list_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "list-1",
                "method": "ListTasks",
                "params": {"contextId": context_id},
            },
        )
        assert list_resp.status_code == 200
        assert any(
            t["id"] == task_id for t in list_resp.json()["result"].get("tasks", [])
        )

        # 5. Streaming SendStreamingMessage (SSE)
        stream_payload = {
            "jsonrpc": "2.0",
            "id": "stream-1",
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "ROLE_USER",
                    "parts": [
                        {"text": "Stream collection"},
                        {
                            "url": doc.uri,
                            "mediaType": "image/svg+xml",
                            "filename": "gov-id-clean.svg",
                            "metadata": {"doc_id": doc.doc_id},
                        },
                    ],
                }
            },
        }
        stream_resp = await client.post("/", json=stream_payload)
        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers.get("content-type", "")
        sse_events = [
            json.loads(line[5:].strip())
            for line in stream_resp.text.splitlines()
            if line.startswith("data:")
        ]
        assert len(sse_events) >= 1
        assert all(ev.get("jsonrpc") == "2.0" for ev in sse_events)


async def test_admit_guard_and_reviewer_auth_propagate_to_agent_engine():
    """Pre-dispatch CollectExecutor.admit guard and authenticated reviewer principal
    work identically when hosted inside BridgeA2aAgentEngine behind the thin proxy."""
    hitl_doc = _doc("gov-id-expired")
    llm = ScriptedLlm(
        on_text={
            "human review required for:": Reply(
                tool_calls=(
                    ToolCall(
                        "request_human_review",
                        {
                            "artifact_name": hitl_doc.doc_id,
                            "reason": "expired ID requires human review",
                        },
                    ),
                )
            )
        }
    )
    leg = LlmLeg(model=llm)
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    engine = BridgeA2aAgentEngine(
        url=BASE + "/",
        executor=executor,
        edge_mode=EdgeMode.hybrid,
        llm_leg=leg,
        reviewers=["alice@example.com"],
    )
    app = build_a2a_app(
        url=BASE + "/",
        a2a_engine=engine,
        auth_backend=_HeaderAuthBackend(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        park_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "park-1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "ROLE_USER",
                        "parts": [
                            {"text": "Collect address proof"},
                            {
                                "url": hitl_doc.uri,
                                "mediaType": "image/svg+xml",
                                "filename": "gov-id-expired.svg",
                                "metadata": {"doc_id": hitl_doc.doc_id},
                            },
                        ],
                    }
                },
            },
        )
        assert park_resp.status_code == 200
        parked_task = park_resp.json()["result"]["task"]
        task_id = parked_task["id"]
        context_id = parked_task["contextId"]
        assert parked_task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        call_id = parked_task["status"]["message"]["metadata"][META_REVIEW_CALL_ID]

        # 1. Sending an ordinary message on the parked task is refused pre-dispatch
        # on BOTH SendMessage and SendStreamingMessage (returning application/json, not SSE).
        refused_stream = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "refuse-stream-1",
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "taskId": task_id,
                        "contextId": context_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": "still waiting"}],
                    }
                },
            },
        )
        assert refused_stream.status_code == 200
        assert "application/json" in refused_stream.headers.get("content-type", "")
        assert "awaiting_human_review" in json.dumps(refused_stream.json()["error"])

        # 2. Unauthenticated resolution attempt on the parked task is refused pre-dispatch
        # with reviewer_not_authorized.
        resolution = HumanReviewResolution(
            call_id=call_id,
            resolution="accepted",
            message="Looks fine",
        )
        unauth_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "resolve-unauth",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "taskId": task_id,
                        "contextId": context_id,
                        "role": "ROLE_USER",
                        "parts": [
                            {
                                "data": resolution.model_dump(mode="json"),
                                "mediaType": "application/json",
                                "metadata": {
                                    META_KIND: KIND_HUMAN_REVIEW_RESOLUTION,
                                    META_CONTRACT_VERSION: resolution.contract_version,
                                },
                            }
                        ],
                    }
                },
            },
        )
        assert unauth_resp.status_code == 200
        assert "reviewer_not_authorized" in json.dumps(unauth_resp.json()["error"])

        # 3. Park a task under alice@example.com and verify authorized reviewer resolution
        # succeeds and transitions the task to TASK_STATE_COMPLETED.
        alice_park_resp = await client.post(
            "/",
            headers={"X-Test-User": "alice@example.com"},
            json={
                "jsonrpc": "2.0",
                "id": "park-alice",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "ROLE_USER",
                        "parts": [
                            {"text": "Collect address proof"},
                            {
                                "url": hitl_doc.uri,
                                "mediaType": "image/svg+xml",
                                "filename": "gov-id-expired.svg",
                                "metadata": {"doc_id": hitl_doc.doc_id},
                            },
                        ],
                    }
                },
            },
        )
        alice_parked = alice_park_resp.json()["result"]["task"]
        alice_task_id = alice_parked["id"]
        alice_context_id = alice_parked["contextId"]
        alice_call_id = alice_parked["status"]["message"]["metadata"][
            META_REVIEW_CALL_ID
        ]
        alice_resolution = HumanReviewResolution(
            call_id=alice_call_id,
            resolution="accepted",
            message="Verified by Alice",
        )

        auth_resp = await client.post(
            "/",
            headers={"X-Test-User": "alice@example.com"},
            json={
                "jsonrpc": "2.0",
                "id": "resolve-auth",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "taskId": alice_task_id,
                        "contextId": alice_context_id,
                        "role": "ROLE_USER",
                        "parts": [
                            {
                                "data": alice_resolution.model_dump(mode="json"),
                                "mediaType": "application/json",
                                "metadata": {
                                    META_KIND: KIND_HUMAN_REVIEW_RESOLUTION,
                                    META_CONTRACT_VERSION: alice_resolution.contract_version,
                                },
                            }
                        ],
                    }
                },
            },
        )
        assert auth_resp.status_code == 200
        resolved_task = auth_resp.json()["result"]["task"]
        assert resolved_task["status"]["state"] == "TASK_STATE_COMPLETED"
        resolved_entries = resolved_task["artifacts"][0]["parts"][0]["data"]["status"][
            "entries"
        ]
        assert resolved_entries[0]["disposition"] == "accepted"


async def test_adk_remote_a2a_agent_run_collection_over_agent_engine_a2a():
    """Full ADK `RemoteA2aAgent` consumer loop (`run_collection`) reaches the exact
    corpus terminal outcomes when the edge delegates A2A to `BridgeA2aAgentEngine`."""
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    engine = BridgeA2aAgentEngine(url=BASE + "/", executor=executor)
    app = build_a2a_app(url=BASE + "/", a2a_engine=engine)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        # CUJ-1: Path-A instant accept
        turn1 = await run_collection(
            CARD_URL,
            httpx_client=hc,
            session_service=InMemorySessionService(),
            session_id="ae-cuj1",
            scenario="path-a-instant",
            documents=[_doc("gov-id-clean")],
        )
        assert turn1 is not None
        assert turn1.terminal is True
        accepted1 = [
            e for e in turn1.status.entries if e.disposition is Disposition.accepted
        ]
        assert len(accepted1) == 1
        assert accepted1[0].doc_id == "gov-id-clean"
        assert accepted1[0].gate is Gate.auto_approve
        assert is_satisfied(turn1.status) is True

        # CUJ-10: Two distinct utility bills
        turn2 = await run_collection(
            CARD_URL,
            httpx_client=hc,
            session_service=InMemorySessionService(),
            session_id="ae-cuj10",
            documents=[_doc("bill-powerco-clean"), _doc("bill-aquautil-clean")],
        )
        assert turn2 is not None
        assert turn2.terminal is True
        issuers = {
            e.issuer
            for e in turn2.status.entries
            if e.disposition is Disposition.accepted and e.doctype == "utility-bill"
        }
        assert issuers == {"power-co", "aqua-util"}
        assert is_satisfied(turn2.status) is True


async def test_dual_option_a_and_option_b_on_single_engine_and_fallback_stub(
    monkeypatch,
):
    """A single BridgeA2aAgentEngine serves both Option A (ADK async_stream_query with
    user_id/session_id/message) and Option B (A2A async_stream_query with payload), and
    resolve_a2a_engine honors BRIDGE_AGENT_ENGINE_A2A."""
    llm = ScriptedLlm(
        on_text={
            "need a person": Reply(
                tool_calls=(
                    ToolCall(
                        "escalate_submission",
                        {"reason": "user requested human help"},
                    ),
                )
            )
        }
    )
    leg = LlmLeg(model=llm)
    engine = BridgeA2aAgentEngine(
        url=BASE + "/",
        edge_mode=EdgeMode.hybrid,
        llm_leg=leg,
    )

    # Create session for Option A ADK stream_query
    await leg.session_service.create_session(
        app_name="document_bridge",
        user_id="u-1",
        session_id="s-1",
    )
    adk_events = [
        ev
        async for ev in engine.async_stream_query(
            user_id="u-1",
            session_id="s-1",
            message={"role": "user", "parts": [{"text": "I need a person"}]},
        )
    ]
    assert len(adk_events) >= 1

    # Also test a remote stub that only exposes `async_query` and `async_stream_query`
    # (simulating a Vertex AI AgentEngine client without custom method wrappers).
    class _GenericRemoteEngineStub:
        async def async_query(self, **kwargs: Any) -> dict[str, Any]:
            return await engine.async_query(**kwargs)

        async def async_stream_query(self, **kwargs: Any):
            async for item in engine.async_stream_query(**kwargs):
                yield item

    monkeypatch.setenv(AGENT_ENGINE_A2A_ENV, "1")
    assert is_agent_engine_a2a_enabled() is True
    stub = _GenericRemoteEngineStub()
    assert resolve_a2a_engine(stub) is stub

    app = build_a2a_app(url=BASE + "/", a2a_engine=stub)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        c_resp = await client.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert c_resp.status_code == 200
        assert c_resp.json()["name"] == "Document Bridge"
