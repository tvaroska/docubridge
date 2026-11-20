"""Canonical A2A v1.0 counterparty dial-out & `/ebc` shim badging tests (P11-2).

Exercises real in-process ``a2a-sdk`` 1.1.x Starlette servers (Agent Card discovery at
``/.well-known/agent-card.json`` + JSON-RPC 2.0 ``SendMessage`` at ``POST /``) for
Scenarios A (FNB Path-A payoff) and B (Carrier A Path-A + Carrier B Path-B PDF),
proving zero legacy ``/v1/payoff-assertion`` or ``/v1/rfp/quote*`` HTTP calls remain.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    TaskState,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict
from starlette.applications import Starlette
from starlette.testclient import TestClient

from bridge.a2ui_inbox import InMemoryActionInboxStore
from bridge.agent_gym import InMemoryAgentGymEngine
from bridge.dashboard.rfp_procurement import (
    RunRfpProcurementRequest,
    execute_rfp_procurement_run,
)
from bridge.identity import SPIFFE_ID_HEADER, SPIFFE_SVID_HEADER
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.outbound import stringify_decimals
from bridge.ports import Port, PortMode, get_adapter
from bridge.showcase import ScenarioEngine
from bridge.showcase.counterparty_and_fleet import (
    request_a2a_payoff,
    run_meridian_fleet_deal,
)
from contract.extraction import ExtractionFields, ExtractionResult
from contract.scenario import ScenarioStageKind

REPO_ROOT = Path(__file__).resolve().parents[1]
FNB_SPIFFE_ID = "spiffe://agents.fnb.example.com/external/fnb-payoff-agent"


class _FnbA2AExecutor(AgentExecutor):
    """Real a2a-sdk 1.1.x executor mirroring FNB's ``loan-payoff-statement`` server."""

    def __init__(self) -> None:
        self.received_payloads: list[dict[str, Any]] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raw_text = (
            " ".join(ph.get_text_parts(context.message.parts))
            if context.message is not None
            else ""
        )
        req_data: dict[str, Any] = {}
        if raw_text.strip().startswith("{"):
            req_data = json.loads(raw_text)
        data_parts = ph.get_data_parts(context.message.parts) if context.message else []
        if data_parts and isinstance(data_parts[0], dict):
            self.received_payloads.append(dict(data_parts[0]))
        elif req_data:
            self.received_payloads.append(req_data)

        result = ExtractionResult(
            fields=ExtractionFields(
                doctype="loan-payoff-statement",
                issuer="First National Bank",
                key_fields={
                    "borrower_name": str(
                        req_data.get("borrower_name", "Meridian Logistics Center LLC")
                    ),
                    "loan_account": str(req_data.get("loan_account", "#FNB-4402")),
                    "payoff_amount": str(req_data.get("payoff_amount", "$412,000.00")),
                    "good_through_date": "2026-11-30",
                    "per_diem_interest": "$67.73",
                    "wire_routing_aba": "021000021",
                    "currency": "USD",
                },
            ),
            overall_confidence=1.0,
            field_confidence={
                "loan_account": 1.0,
                "payoff_amount": 1.0,
                "good_through_date": 1.0,
                "currency": 1.0,
            },
            legible=True,
            flagged_fields=[],
        )
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )
        part = ph.new_data_part(
            result.model_dump(mode="json"), media_type="application/json"
        )
        part.metadata.CopyFrom(
            ParseDict(
                {"kind": "loan-payoff-statement", "agent_spiffe_id": FNB_SPIFFE_ID},
                struct_pb2.Struct(),
            )
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.add_artifact(
            [part], name="loan-payoff-statement", last_chunk=True
        )
        await updater.complete(
            message=ph.new_message(
                parts=[ph.new_text_part("Signed Path-A payoff issued")],
                context_id=context.context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class _CarrierAA2AExecutor(AgentExecutor):
    """Real a2a-sdk 1.1.x executor emitting Path-A structured ``benefits-quote``."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        quote = ExtractionResult(
            fields=ExtractionFields(
                doctype="benefits-quote",
                issuer="Carrier A",
                key_fields={
                    "currency": "USD",
                    "language": "en",
                    "premium": "1200.00",
                    "deductible": "250.00",
                    "coverage_limits": "up to 50000",
                    "exclusions": "none",
                    "effective_date": "2027-01-01",
                    "expiry_date": "2027-12-31",
                },
            ),
            overall_confidence=0.99,
            field_confidence={"premium": 0.99},
            legible=True,
            flagged_fields=[],
        )
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )
        part = ph.new_data_part(
            quote.model_dump(mode="json"), media_type="application/json"
        )
        part.metadata.CopyFrom(
            ParseDict({"kind": "benefits-quote"}, struct_pb2.Struct())
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.add_artifact([part], name="benefits-quote", last_chunk=True)
        await updater.complete(
            message=ph.new_message(
                parts=[ph.new_text_part("benefits-quote from carrier-a")],
                context_id=context.context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class _CarrierBA2AExecutor(AgentExecutor):
    """Real a2a-sdk 1.1.x executor emitting ``benefits-quote`` + optional Path-B PDF URL part."""

    def __init__(self, *, pdf_uri: str = "") -> None:
        self._pdf_uri = pdf_uri
        self.received_texts: list[str] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raw_text = (
            " ".join(ph.get_text_parts(context.message.parts))
            if context.message is not None
            else ""
        )
        self.received_texts.append(raw_text)
        quote = ExtractionResult(
            fields=ExtractionFields(
                doctype="benefits-quote",
                issuer="Carrier B",
                key_fields={
                    "currency": "USD",
                    "language": "en",
                    "premium": "1140.00",
                    "deductible": "300.00",
                    "coverage_limits": "up to 50000",
                    "exclusions": "orthodontics",
                    "effective_date": "2027-01-01",
                    "expiry_date": "2027-12-31",
                },
            ),
            overall_confidence=0.95,
            field_confidence={"premium": 0.95},
            legible=True,
            flagged_fields=[],
        )
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )
        parts = [
            ph.new_data_part(
                quote.model_dump(mode="json"), media_type="application/json"
            )
        ]
        parts[0].metadata.CopyFrom(
            ParseDict({"kind": "benefits-quote"}, struct_pb2.Struct())
        )
        if self._pdf_uri:
            url_part = ph.new_url_part(
                self._pdf_uri,
                media_type="application/pdf",
                filename="carrier-b-rate-sheet.pdf",
            )
            url_part.metadata.CopyFrom(
                ParseDict({"pdf_uri": self._pdf_uri}, struct_pb2.Struct())
            )
            parts.append(url_part)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.add_artifact(parts, name="benefits-quote", last_chunk=True)
        await updater.complete(
            message=ph.new_message(
                parts=[ph.new_text_part("benefits-quote from carrier-b")],
                context_id=context.context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def _build_a2a_app(
    *,
    name: str,
    url: str,
    skill_id: str,
    executor: AgentExecutor,
    card_rpc_url: str | None = None,
) -> Starlette:
    card = AgentCard(
        name=name,
        description=f"Test A2A server for {skill_id}",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=card_rpc_url or url,
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id=skill_id,
                name=skill_id,
                description=f"Serve {skill_id}",
                tags=[skill_id],
                input_modes=["application/json", "text/plain"],
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")
    return Starlette(routes=routes)


class _HostRoutingA2ATransport(httpx.BaseTransport):
    """Routes sync ``httpx.Client`` calls to real in-process Starlette A2A servers and records wire traffic."""

    def __init__(self, apps_by_host: dict[str, Starlette]) -> None:
        self._clients = {
            host: TestClient(app, base_url=f"https://{host}")
            for host, app in apps_by_host.items()
        }
        self.recorded: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        body_bytes = request.read()
        self.recorded.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "headers": dict(request.headers),
                "body": body_bytes.decode("utf-8") if body_bytes else "",
            }
        )
        client = self._clients.get(host)
        if client is None:
            return httpx.Response(404, json={"error": f"unknown host {host}"})
        resp = client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers=dict(request.headers),
            content=body_bytes if body_bytes else None,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
            request=request,
        )

    def close(self) -> None:
        for client in self._clients.values():
            client.close()


def _consolidation_engine() -> tuple[ScenarioEngine, str]:
    op_store = InMemoryOperationalStore()
    engine = ScenarioEngine(
        store=op_store,
        catalog=InMemoryKnowledgeCatalog(operational_store=op_store),
        inbox=InMemoryActionInboxStore(),
        gym=InMemoryAgentGymEngine(),
    )
    manifest = engine.find(kind=ScenarioStageKind.consolidation)
    assert manifest is not None
    return engine, manifest.id


def _isolate_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skills_copy = tmp_path / "skills"
    shutil.copytree(REPO_ROOT / "skills", skills_copy)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(skills_copy))
    monkeypatch.setenv("BRIDGE_CLOUD_TRACE_EXPORT", "0")


def test_scenario_a_fnb_payoff_over_canonical_a2a_send_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario A (`request_a2a_payoff` & fleet deal) uses canonical A2A v1.0 `SendMessage` with 0 legacy REST calls."""
    _isolate_skills(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_FNB_AGENT_URL", "https://fnb.test")
    monkeypatch.delenv("BRIDGE_PORT_A2A_CLIENT", raising=False)
    monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)

    fnb_exec = _FnbA2AExecutor()
    fnb_app = _build_a2a_app(
        name="First National Bank Payoff Agent",
        url="https://fnb.test/",
        skill_id="loan-payoff-statement",
        executor=fnb_exec,
    )
    transport = _HostRoutingA2ATransport({"fnb.test": fnb_app})
    engine, sid = _consolidation_engine()

    with httpx.Client(transport=transport) as hc:
        state = request_a2a_payoff(
            engine, sid, counterparty_id="fnb-payoff", mode="live", http_client=hc
        )
        _, fleet_res = run_meridian_fleet_deal(
            engine,
            sid,
            case_id="meridian-a2a-01",
            case_type="day1",
            deal_entity_id="DEAL-A2A-01",
            borrower_name="Harbor Freight Logistics LLC",
            fnb_account="#FNB-4402",
            fnb_amount="$412,000.00",
            pdf_uris=["gs://test-bucket/apex.pdf"],
            wire_amount="$48,210.00",
            wire_facility_ref="#8841",
            wire_memo="FINAL PAYOFF - LOAN #8841 - PAID IN FULL",
            mode="live",
            genai_client=None,
            http_client=hc,
        )

    assert state["live_telemetry"]["a2a_path_a_payoff"]["used_live_agent"] is True
    assert state["live_telemetry"]["a2a_path_a_payoff"]["llm_tokens"] == 0
    assert (
        state["live_telemetry"]["a2a_path_a_payoff"]["counterparty_spiffe_id"]
        == FNB_SPIFFE_ID
    )
    items = {i["id"]: i for i in state["borrower_checklist"]}
    assert items["item-4-fnb-payoff"]["status"] == "satisfied"
    assert items["item-4-fnb-payoff"]["source_mode"] == "A2A_PATH_A"
    assert fleet_res["outcome_status"] == "succeeded"

    paths = [rec["path"] for rec in transport.recorded]
    assert "/v1/payoff-assertion" not in paths
    assert "/v1/rfp/quote" not in paths
    assert "/v1/rfp/quote-pdf" not in paths
    assert paths[:2] == ["/.well-known/agent-card.json", "/"]

    post_reqs = [r for r in transport.recorded if r["method"] == "POST"]
    assert len(post_reqs) == 2
    for req in post_reqs:
        assert req["headers"].get("a2a-version") == "1.0"
        assert req["headers"].get(SPIFFE_ID_HEADER.lower())
        assert req["headers"].get(SPIFFE_SVID_HEADER.lower())
        assert "outcome.id" not in req["headers"].get("baggage", "")
        assert "outcome.owner" not in req["headers"].get("baggage", "")
        rpc_body = json.loads(req["body"])
        assert rpc_body["jsonrpc"] == "2.0"
        assert rpc_body["method"] == "SendMessage"
        parts = rpc_body["params"]["message"]["parts"]
        data_part = next(p["data"] for p in parts if "data" in p)
        assert isinstance(data_part["payoff_amount"], str)
        assert isinstance(data_part["payoff_amount_decimal"], str)
        assert data_part["payoff_amount_decimal"] == "412000.00"


def test_scenario_b_rfp_procurement_over_canonical_a2a_send_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario B (`execute_rfp_procurement_run`) calls Carrier A & B via canonical A2A `SendMessage` with 0 legacy REST calls."""
    _isolate_skills(tmp_path, monkeypatch)
    monkeypatch.delenv("BRIDGE_PORT_A2A_CLIENT", raising=False)
    monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)

    carrier_a_app = _build_a2a_app(
        name="Benefits Carrier (carrier-a)",
        url="https://carrier-a.test/",
        skill_id="benefits-quote",
        executor=_CarrierAA2AExecutor(),
    )
    carrier_b_exec = _CarrierBA2AExecutor(
        pdf_uri="gs://test-bucket/eval-corpus/carrier-b-quote.pdf"
    )
    carrier_b_app = _build_a2a_app(
        name="Benefits Carrier (carrier-b)",
        url="https://carrier-b.test/",
        skill_id="benefits-quote",
        executor=carrier_b_exec,
    )
    carrier_b_no_uri_app = _build_a2a_app(
        name="Benefits Carrier Fallback (carrier-b)",
        url="https://carrier-b-fallback.test/",
        skill_id="benefits-quote",
        executor=_CarrierBA2AExecutor(pdf_uri=""),
    )
    transport = _HostRoutingA2ATransport(
        {
            "carrier-a.test": carrier_a_app,
            "carrier-b.test": carrier_b_app,
            "carrier-b-fallback.test": carrier_b_no_uri_app,
        }
    )

    seen_uris: list[str] = []

    class _FakeGenaiModels:
        def generate_content(self, *, model: str, contents: list[Any], config: Any):
            for part in contents:
                fd = getattr(part, "file_data", None)
                if fd is not None and getattr(fd, "file_uri", None):
                    seen_uris.append(fd.file_uri)
            return SimpleNamespace(
                text='{"carrier":"Carrier B","revision":"v2","orthodontics_gap_closed":true}',
                usage_metadata=SimpleNamespace(
                    prompt_token_count=215000 if "argon" in model else 1240,
                    candidates_token_count=180,
                    thoughts_token_count=120 if "argon" in model else 0,
                ),
            )

    fake_genai = SimpleNamespace(models=_FakeGenaiModels())

    with httpx.Client(transport=transport) as hc:
        _, rfp_flash = execute_rfp_procurement_run(
            body=RunRfpProcurementRequest(
                case_id="rfp-a2a-001",
                carrier_a_url="https://carrier-a.test",
                carrier_b_url="https://carrier-b.test",
                argon_cohort=False,
                mode="live",
            ),
            genai_client=fake_genai,
            http_client=hc,
        )
        _, rfp_argon = execute_rfp_procurement_run(
            body=RunRfpProcurementRequest(
                case_id="rfp-a2a-002",
                carrier_a_url="https://carrier-a.test",
                carrier_b_url="https://carrier-b-fallback.test",
                carrier_b_pdf_uri="gs://test-bucket/caller-supplied-carrier-b.pdf",
                argon_cohort=True,
                mode="live",
            ),
            genai_client=fake_genai,
            http_client=hc,
        )

    assert rfp_flash["carrier_a_mode"] == "A2A_PATH_A"
    assert rfp_flash["carrier_b_mode"] == "PDF_PATH_B_FLASH"
    assert rfp_argon["carrier_b_mode"] == "PDF_PATH_B_ARGON"
    assert rfp_argon["input_tokens"] > 200000
    assert seen_uris == [
        "gs://test-bucket/eval-corpus/carrier-b-quote.pdf",
        "gs://test-bucket/caller-supplied-carrier-b.pdf",
    ]
    assert all("revise" not in txt.lower() for txt in carrier_b_exec.received_texts)

    paths = [rec["path"] for rec in transport.recorded]
    assert "/v1/rfp/quote" not in paths
    assert "/v1/rfp/quote-pdf" not in paths
    assert "/v1/payoff-assertion" not in paths
    assert paths.count("/.well-known/agent-card.json") == 4
    post_reqs = [r for r in transport.recorded if r["method"] == "POST"]
    assert len(post_reqs) == 4
    for req in post_reqs:
        assert req["headers"].get("a2a-version") == "1.0"
        assert "outcome.id" not in req["headers"].get("baggage", "")
        assert json.loads(req["body"])["method"] == "SendMessage"


def test_a2a_client_port_discovery_gate_same_origin_and_gcp_oidc() -> None:
    """`Port.a2a_client` enforces skill discovery gate, same-origin reconciliation, Decimal strings, and GCP OIDC."""
    assert stringify_decimals(
        {"amount": Decimal("412000.00"), "nested": [Decimal("67.73")]}
    ) == {"amount": "412000.00", "nested": ["67.73"]}

    fnb_app = _build_a2a_app(
        name="FNB",
        url="https://fnb.test/",
        skill_id="loan-payoff-statement",
        executor=_FnbA2AExecutor(),
    )
    evil_card_app = _build_a2a_app(
        name="Evil Redirect",
        url="https://evil-card.test/",
        card_rpc_url="https://attacker.example/rpc",
        skill_id="loan-payoff-statement",
        executor=_FnbA2AExecutor(),
    )
    downgrade_app = _build_a2a_app(
        name="Downgrade Redirect",
        url="https://downgrade.test/",
        card_rpc_url="http://downgrade.test/",
        skill_id="loan-payoff-statement",
        executor=_FnbA2AExecutor(),
    )
    transport = _HostRoutingA2ATransport(
        {
            "fnb.test": fnb_app,
            "evil-card.test": evil_card_app,
            "downgrade.test": downgrade_app,
        }
    )

    local_port = get_adapter(Port.a2a_client, PortMode.local)
    gcp_port = get_adapter(Port.a2a_client, PortMode.gcp)

    with httpx.Client(transport=transport) as hc:
        with pytest.raises(ValueError, match="does not advertise skill"):
            local_port.send_message(
                endpoint_url="https://fnb.test",
                payload={"x": 1},
                expected_skill="unadvertised-skill",
                http_client=hc,
            )
        assert not [r for r in transport.recorded if r["method"] == "POST"]

        with pytest.raises(ValueError, match=r"attacker\.example"):
            local_port.send_message(
                endpoint_url="https://evil-card.test",
                payload={"x": 1},
                expected_skill="loan-payoff-statement",
                http_client=hc,
            )
        assert not [r for r in transport.recorded if r["method"] == "POST"]

        with pytest.raises(ValueError, match="downgrades expected"):
            local_port.send_message(
                endpoint_url="https://downgrade.test",
                payload={"x": 1},
                expected_skill="loan-payoff-statement",
                http_client=hc,
            )
        assert not [r for r in transport.recorded if r["method"] == "POST"]

        audiences: list[str] = []
        res = gcp_port.send_message(
            endpoint_url="https://fnb.test",
            payload={"payoff_amount": Decimal("412000.00")},
            expected_skill="loan-payoff-statement",
            headers={
                "baggage": "outcome.id=out_secret,outcome.owner=internal,tenant=meridian",
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            },
            http_client=hc,
            gateway_host="fnb.test",
            id_token_fetcher=lambda aud: audiences.append(aud) or f"tok::{aud}",
        )
        assert res.task_state == "TASK_STATE_COMPLETED"
        assert audiences == [
            "https://fnb.test/.well-known/agent-card.json",
            "https://fnb.test/",
        ]
        post_req = next(r for r in transport.recorded if r["method"] == "POST")
        assert post_req["headers"]["authorization"] == "Bearer tok::https://fnb.test/"
        assert post_req["headers"]["baggage"] == "tenant=meridian"
        assert post_req["headers"]["traceparent"].startswith("00-4bf92f3577")

    def _failed_task_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "Failing Peer",
                    "supportedInterfaces": [
                        {
                            "url": "https://fail.test/",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                    "skills": [{"id": "loan-payoff-statement"}],
                },
            )
        rpc = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": rpc["id"],
                "result": {
                    "task": {
                        "id": "task-err-1",
                        "status": {
                            "state": "TASK_STATE_FAILED",
                            "message": {"parts": [{"text": "upstream failure"}]},
                        },
                    }
                },
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(_failed_task_handler)) as fail_hc,
        pytest.raises(RuntimeError, match="TASK_STATE_FAILED"),
    ):
        local_port.send_message(
            endpoint_url="https://fail.test",
            payload={"x": 1},
            expected_skill="loan-payoff-statement",
            http_client=fail_hc,
        )


def test_zero_legacy_counterparty_rest_paths_in_bridge_source() -> None:
    """No Python file under `bridge/` may reference legacy `/v1/payoff-assertion` or `/v1/rfp/quote*` paths."""
    forbidden = ("/v1/payoff-assertion", "/v1/rfp/quote", "/v1/rfp/quote-pdf")
    offenders: list[str] = []
    for py_path in sorted((REPO_ROOT / "bridge").rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        text = py_path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{py_path.relative_to(REPO_ROOT)}: {needle}")
    assert not offenders, (
        f"Legacy counterparty REST paths remain in bridge/: {offenders}"
    )


def test_ebc_live_badges_simulated_registry_and_gateway_shims() -> None:
    """`/ebc` clearly badges simulated Agent Registry & Agent Gateway shims while preserving DOM IDs."""
    html = (REPO_ROOT / "demo" / "ebc-live.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "demo" / "ebc-live.js").read_text(encoding="utf-8")

    assert "SIMULATED IN-PROCESS SHIM" in html
    assert "SIMULATED IN-PROCESS SHIM" in js
    for required_id in (
        "badge-skill-registry-shim",
        "registry-gateway-panel",
        "badge-registry-gateway-shim",
        "registry-gateway-summary",
        "kpi-skill",
        "kpi-skill-sub",
        "rpanel-telemetry",
        "bq-gates-strip",
        "otel-waterfall-box",
        "compiled-skill-pre",
        "btn-request-a2a",
    ):
        assert f'id="{required_id}"' in html, f"missing DOM id {required_id!r}"
    assert "/v1/registry/catalog" in js
    assert "/v1/gateway/status" in js
