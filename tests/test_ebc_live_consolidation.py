"""Tests for real vector PDF generation, live Gemini PDF intake, M2 Path-A/GCS/fleet routes, and `/ebc`."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bridge import patha
from bridge.a2ui_inbox import InMemoryActionInboxStore
from bridge.agent_gym import InMemoryAgentGymEngine
from bridge.dashboard.rfp_procurement import (
    RunRfpProcurementRequest,
    execute_rfp_procurement_run,
)
from bridge.dashboard.server import build_dashboard_app
from bridge.extraction.chunking import pdf_page_count
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.showcase import ScenarioEngine
from bridge.showcase.counterparty_and_fleet import (
    request_a2a_payoff,
    run_meridian_fleet_deal,
)
from bridge.showcase.gcs_scenarios import discover_gcs_manifests, fetch_gcs_bytes
from bridge.showcase.live_extraction import process_uploaded_pdfs
from bridge.showcase.loader import discover_manifests
from bridge.showcase.pdf_assets import build_pdf_zip, pdf_metadata
from bridge.telemetry import trace_step
from bridge.telemetry.payload_log import emit_trace_payload_log
from bridge.user_interaction import build_user_interaction_app
from contract.scenario import ScenarioStageKind

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_five_demo_vector_pdfs_and_zip_bundle() -> None:
    """All 5 showcase PDFs have valid page counts (incl. 28-page bank statement), searchable text, and `.zip`."""
    engine, sid = _consolidation_engine()
    catalog = engine.pdf_catalog(sid)
    assert len(catalog) == 5
    expected_pages = {
        "meridian-financials": 4,
        "meridian-appraisal": 2,
        "apex-payoff": 1,
        "meridian-bank-stmt": 28,
        "apex-day2-bank-stmt": 2,
    }
    for asset_id, exp_pages in expected_pages.items():
        asset = catalog[asset_id]
        assert asset.pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_page_count(asset.pdf_bytes) == exp_pages
        assert engine.resolve_pdf(sid, asset_id) == asset
        assert engine.resolve_pdf(sid, asset.filename) == asset

    assert b"Page 14 of 28" in catalog["meridian-bank-stmt"].pdf_bytes
    assert b"EQUIPLEASE FINANCIAL CORP" in catalog["meridian-bank-stmt"].pdf_bytes
    assert engine.resolve_pdf(sid, "nonexistent.pdf") is None
    meta = [
        pdf_metadata(a, url_base=engine.pdf_url_base(sid)) for a in catalog.values()
    ]
    assert len(meta) == 5

    zip_bytes = build_pdf_zip(list(catalog.values()))
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "01_Meridian_2025_Audited_Financials.pdf" in names
        assert "03_Apex_Capital_Payoff_7712.pdf" in names
        assert "04_Meridian_Oct2026_Operating_Bank_Statement.pdf" in names
        assert "05_Apex_Precision_Day2_Bank_Statement.pdf" in names


def _isolate_skills_and_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_copy = tmp_path / "skills"
    shutil.copytree(REPO_ROOT / "skills", skills_copy)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(skills_copy))
    monkeypatch.setenv("BRIDGE_CLOUD_TRACE_EXPORT", "0")


def test_ebc_console_and_pdf_upload_a2a_promote_and_custom_flows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `/ebc` routes, Path-A A2A payoff, PDF upload, DRAFT compile -> 1-click promote, and Day-2."""
    _isolate_skills_and_telemetry(tmp_path, monkeypatch)
    op_store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=op_store)
    inbox = InMemoryActionInboxStore()
    gym = InMemoryAgentGymEngine()

    app = build_user_interaction_app(
        operational_store=op_store,
        knowledge_catalog=catalog,
        inbox_store=inbox,
        agent_gym=gym,
    )
    client = TestClient(app)

    for path, mime in (
        ("/ebc", "text/html"),
        ("/ebc/ebc-live.css", "text/css"),
        ("/ebc/ebc-live.js", "javascript"),
        ("/ebc/favicon.svg", "image/svg+xml"),
    ):
        r = client.get(path)
        assert r.status_code == 200
        assert mime in r.headers["content-type"]

    cat_r = client.get("/v1/demo/consolidation-collab/pdfs")
    assert cat_r.status_code == 200
    assert len(cat_r.json()["pdfs"]) == 5

    zip_r = client.get("/v1/demo/consolidation-collab/pdfs.zip")
    assert zip_r.status_code == 200
    assert zip_r.headers["content-type"] == "application/zip"

    pdf_r = client.get(
        "/v1/demo/consolidation-collab/pdfs/01_Meridian_2025_Audited_Financials.pdf"
    )
    assert pdf_r.status_code == 200
    assert pdf_r.headers["content-type"] == "application/pdf"

    assert (
        client.get("/v1/demo/consolidation-collab/pdfs/missing-doc.pdf").status_code
        == 404
    )
    assert (
        client.post(
            "/v1/demo/consolidation-collab/upload-pdf",
            json={"sample_ids": [], "files": [], "mode": "cached"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/demo/consolidation-collab/upload-pdf",
            json={"sample_ids": ["unknown-id"], "mode": "cached"},
        ).status_code
        == 404
    )

    b1 = client.post(
        "/v1/demo/consolidation-collab/upload-pdf",
        json={
            "sample_ids": ["meridian-financials", "meridian-appraisal"],
            "mode": "cached",
        },
    )
    assert b1.status_code == 200
    b1_json = b1.json()
    assert b1_json["beat"] == 1
    assert b1_json["counts"]["total"] == 7
    assert b1_json["live_telemetry"]["pii_tokens_redacted"] >= 2

    a2a_r = client.post(
        "/v1/demo/consolidation-collab/request-a2a-payoff",
        json={"counterparty_id": "fnb-payoff", "mode": "cached"},
    )
    assert a2a_r.status_code == 200
    a2a_state = a2a_r.json()
    a2a_items = {i["id"]: i for i in a2a_state["borrower_checklist"]}
    assert a2a_items["item-4-fnb-payoff"]["status"] == "satisfied"
    assert a2a_items["item-4-fnb-payoff"]["source_mode"] == "A2A_PATH_A"
    assert a2a_state["live_telemetry"]["a2a_path_a_payoff"]["llm_tokens"] == 0

    pdf_engine, pdf_sid = _consolidation_engine()
    pdf_cat = pdf_engine.pdf_catalog(pdf_sid)
    bank_b64 = "data:application/pdf;base64," + base64.b64encode(
        pdf_cat["meridian-bank-stmt"].pdf_bytes
    ).decode("ascii")
    apex_b64 = base64.b64encode(pdf_cat["apex-payoff"].pdf_bytes).decode("ascii")
    custom_b64 = base64.b64encode(pdf_cat["meridian-appraisal"].pdf_bytes).decode(
        "ascii"
    )

    b2 = client.post(
        "/v1/demo/consolidation-collab/upload-pdf",
        json={
            "files": [
                {
                    "filename": "04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
                    "content_base64": bank_b64,
                },
                {
                    "filename": "03_Apex_Capital_Payoff_7712.pdf",
                    "content_base64": apex_b64,
                },
                {
                    "filename": "custom_borrower_note",
                    "content_base64": custom_b64,
                },
            ],
            "mode": "cached",
        },
    )
    assert b2.status_code == 200
    assert b2.json()["beat"] == 2
    assert b2.json()["cross_doc_discovery"]["page_number"] == 14
    assert b2.json()["underwriter_overwatch"]["status"] == "awaiting_approval"

    cust_serve = client.get(
        "/v1/demo/consolidation-collab/pdfs/custom_borrower_note.pdf"
    )
    assert cust_serve.status_code == 200

    b3 = client.post(
        "/v1/demo/consolidation-collab/approve-waiver", json={"mode": "cached"}
    )
    assert b3.status_code == 200
    assert b3.json()["beat"] == 3
    assert b3.json()["done"] is True

    b4_draft = client.post(
        "/v1/demo/consolidation-collab/distill-skill",
        json={"mode": "cached", "auto_promote": False},
    )
    assert b4_draft.status_code == 200
    draft_payoff = b4_draft.json()["agent_gym_payoff"]
    assert draft_payoff["skill_version"] == "1.1.0"
    assert draft_payoff["target_state"] == "DRAFT"
    assert "loan-payoff-statement" in draft_payoff["skill_markdown"]
    assert "$100,000.00" in draft_payoff["skill_markdown"]

    b4_prom = client.post(
        "/v1/demo/consolidation-collab/promote-skill",
        json={"revision_id": draft_payoff["skill_revision_id"]},
    )
    assert b4_prom.status_code == 200
    prom_payoff = b4_prom.json()["agent_gym_payoff"]
    assert prom_payoff["target_state"] == "ACTIVE"
    assert prom_payoff["is_default_revision"] is True

    d2 = client.post(
        "/v1/demo/consolidation-collab/upload-pdf",
        json={"sample_ids": ["apex-day2-bank-stmt"], "mode": "cached"},
    )
    assert d2.status_code == 200
    assert d2.json()["agent_gym_payoff"]["day2_entity_id"] == "LOAN-APEX-CONSOL-DAY2"

    reload_r = client.post("/v1/demo/scenarios/reload")
    assert reload_r.status_code == 200
    assert reload_r.json()["status"] == "reloaded"


def test_live_a2a_payoff_meridian_fleet_deals_and_rfp_procurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify live HTTP Path-A payoff, Day-1/Day-2/out-of-policy fleet deals, and RFP Flash vs Argon."""
    _isolate_skills_and_telemetry(tmp_path, monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")

    engine, sid = _consolidation_engine()
    shadow_deny = engine.gym.evaluate_gateway_route(
        governed_access_path="AGENT_TO_ANYWHERE",
        source_principal="spiffe://agents.shadow.io/rogue-scraper",
        target_resource="projects/0/locations/us-central1/agents/agentregistry-fnb-payoff-agent",
        protocol="A2A",
    )
    assert shadow_deny["allowed"] is False
    assert shadow_deny["decision"] == "DENIED_BY_SIMULATED_POLICY"
    assert shadow_deny["gate_2_identity_verified_demo"] is False

    with pytest.raises(ValueError, match="no installed schema"):
        patha.validate_assertion(
            {
                "fields": {
                    "doctype": "nonexistent-doctype-xyz",
                    "key_fields": {"foo": "bar"},
                },
                "overall_confidence": 1.0,
                "field_confidence": {"foo": 1.0},
                "legible": True,
                "flagged_fields": [],
            },
            require_installed_schema=True,
        )

    def _mock_handler(request: httpx.Request) -> httpx.Response:
        assert "outcome.id" not in request.headers.get("baggage", "")
        if request.url.path == "/v1/payoff-assertion":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "agent_spiffe_id": "spiffe://agents.fnb.example.com/external/fnb-payoff-agent",
                    "assertion": {
                        "fields": {
                            "doctype": "loan-payoff-statement",
                            "issuer": "First National Bank",
                            "key_fields": {
                                "borrower_name": body["borrower_name"],
                                "loan_account": body["loan_account"],
                                "payoff_amount": body["payoff_amount"],
                                "good_through_date": "2026-11-30",
                                "per_diem_interest": "$67.73",
                                "wire_routing_aba": "021000021",
                                "currency": "USD",
                            },
                        },
                        "overall_confidence": 1.0,
                        "field_confidence": {
                            "loan_account": 1.0,
                            "payoff_amount": 1.0,
                            "good_through_date": 1.0,
                            "currency": 1.0,
                        },
                        "legible": True,
                        "flagged_fields": [],
                    },
                },
            )
        if request.url.path == "/v1/rfp/quote":
            return httpx.Response(
                200,
                json={
                    "assertion": {
                        "fields": {
                            "doctype": "benefits-quote",
                            "issuer": "Carrier A",
                            "key_fields": {
                                "currency": "USD",
                                "language": "en",
                                "premium": "1200.00",
                                "deductible": "250.00",
                                "coverage_limits": "up to 50000",
                                "exclusions": "none",
                                "effective_date": "2027-01-01",
                                "expiry_date": "2027-12-31",
                            },
                        },
                        "overall_confidence": 0.99,
                        "field_confidence": {"premium": 0.99},
                        "legible": True,
                        "flagged_fields": [],
                    }
                },
            )
        return httpx.Response(
            200,
            json={"pdf_uri": "gs://test-bucket/eval-corpus/rfp-quote-b.pdf"},
        )

    transport = httpx.MockTransport(_mock_handler)
    with httpx.Client(transport=transport, base_url="https://counterparty.test") as hc:
        live_a2a = request_a2a_payoff(
            engine, sid, counterparty_id="fnb-payoff", mode="live", http_client=hc
        )
        assert (
            live_a2a["live_telemetry"]["a2a_path_a_payoff"]["used_live_agent"] is True
        )

        out_d1, res_d1 = run_meridian_fleet_deal(
            engine,
            sid,
            case_id="meridian-d1-01",
            case_type="day1",
            deal_entity_id="DEAL-D1-01",
            borrower_name="Harbor Freight Logistics LLC",
            fnb_account="#FNB-4402",
            fnb_amount="$412,000.00",
            pdf_uris=["gs://test-bucket/apex.pdf", "gs://test-bucket/bank.pdf"],
            wire_amount="$48,210.00",
            wire_facility_ref="#8841",
            wire_memo="FINAL PAYOFF - LOAN #8841 - PAID IN FULL",
            mode="cached",
            http_client=hc,
        )
        assert out_d1.startswith("out_")
        assert res_d1["outcome_status"] == "succeeded"
        assert res_d1["skill_version"] == "1.0.0"
        assert res_d1["human_reviews"] == 1

        engine.advance(sid, 4)

        out_d2, res_d2 = run_meridian_fleet_deal(
            engine,
            sid,
            case_id="meridian-d2-01",
            case_type="day2_in_policy",
            deal_entity_id="DEAL-D2-01",
            borrower_name="Summit Cold Storage LLC",
            fnb_account="#FNB-5501",
            fnb_amount="$310,000.00",
            pdf_uris=["gs://test-bucket/day2-bank.pdf"],
            wire_amount="$64,200.00",
            wire_facility_ref="#9914",
            wire_memo="FINAL PAYOFF - LOAN #9914 - PAID IN FULL",
            parent_outcome_id=out_d1,
            mode="cached",
            http_client=hc,
        )
        assert out_d2 != out_d1
        assert res_d2["outcome_status"] == "succeeded"
        assert res_d2["skill_version"] == "1.1.0"
        assert res_d2["human_reviews"] == 0

        _, res_oop = run_meridian_fleet_deal(
            engine,
            sid,
            case_id="meridian-d2-oop",
            case_type="day2_out_of_policy",
            deal_entity_id="DEAL-D2-OOP",
            borrower_name="Overlimit Trucking LLC",
            fnb_account="#FNB-6601",
            fnb_amount="$390,000.00",
            pdf_uris=["gs://test-bucket/day2-oop.pdf"],
            wire_amount="$148,500.00",
            wire_facility_ref="#9988",
            wire_memo="FINAL PAYOFF - LOAN #9988 - PAID IN FULL",
            parent_outcome_id=out_d1,
            mode="cached",
            http_client=hc,
        )
        assert res_oop["outcome_status"] == "escalated"
        assert res_oop["ledger_locked"] is False
        assert res_oop["human_reviews"] == 1

        _, rfp_flash = execute_rfp_procurement_run(
            body=RunRfpProcurementRequest(
                case_id="rfp-001",
                carrier_a_url="https://counterparty.test",
                carrier_b_url="https://counterparty.test",
                argon_cohort=False,
                mode="live",
            ),
            http_client=hc,
        )
        assert rfp_flash["carrier_b_mode"] == "PDF_PATH_B_FLASH"

        _, rfp_argon = execute_rfp_procurement_run(
            body=RunRfpProcurementRequest(
                case_id="rfp-002-argon",
                carrier_a_url="https://counterparty.test",
                carrier_b_url="https://counterparty.test",
                argon_cohort=True,
                mode="live",
            ),
            http_client=hc,
        )
        assert rfp_argon["carrier_b_mode"] == "PDF_PATH_B_ARGON"
        assert rfp_argon["input_tokens"] > 200000

    with trace_step("test.payload.log", {"entity_id": "E1"}):
        emit_trace_payload_log(
            user_prompt="p", retrieved_context="c", final_response="r"
        )


def test_gcs_scenario_push_and_loader_roundtrip(monkeypatch) -> None:
    """Verify `push-scenario.py` uploads in-memory PDFs + `scenario.yaml` and `discover_manifests` reads `gs://`."""

    class _FakeBlob:
        def __init__(self, store: dict[str, bytes], name: str) -> None:
            self._store = store
            self.name = name

        def upload_from_string(self, data: bytes, content_type: str = "") -> None:
            del content_type
            self._store[self.name] = data if isinstance(data, bytes) else data.encode()

        def download_as_bytes(self) -> bytes:
            return self._store[self.name]

    class _FakeBucket:
        def __init__(self, store: dict[str, bytes]) -> None:
            self._store = store

        def blob(self, name: str) -> _FakeBlob:
            return _FakeBlob(self._store, name)

    class _FakeStorageClient:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}

        def bucket(self, _name: str) -> _FakeBucket:
            return _FakeBucket(self.objects)

        def list_blobs(self, _bucket: _FakeBucket, prefix: str = "") -> list[_FakeBlob]:
            return [
                _FakeBlob(self.objects, k)
                for k in sorted(self.objects)
                if k.startswith(prefix)
            ]

    spec = importlib.util.spec_from_file_location(
        "push_scenario_mod", REPO_ROOT / "scripts" / "push-scenario.py"
    )
    assert spec is not None and spec.loader is not None
    push_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(push_mod)

    fake_gcs = _FakeStorageClient()
    pushed = push_mod.push_scenario(
        "loan-meridian-consol-2026",
        bucket_uri="gs://test-scenarios-bucket",
        storage_client=fake_gcs,
    )
    assert len(pushed["uploaded_pdfs"]) == 5
    assert "loan-meridian-consol-2026/scenario.yaml" in fake_gcs.objects

    loaded = discover_gcs_manifests(
        "gs://test-scenarios-bucket", storage_client=fake_gcs
    )
    assert len(loaded) == 1
    assert loaded[0].id == "loan-meridian-consol-2026"

    monkeypatch.setenv("BRIDGE_SCENARIOS_DIR", "gs://test-scenarios-bucket")
    gcs_engine = ScenarioEngine(
        store=InMemoryOperationalStore(),
        inbox=InMemoryActionInboxStore(),
        gym=InMemoryAgentGymEngine(),
        storage_client=fake_gcs,
    )
    gcs_pdfs = gcs_engine.pdf_catalog("loan-meridian-consol-2026")
    assert pdf_page_count(gcs_pdfs["meridian-bank-stmt"].pdf_bytes) == 28
    first_asset = loaded[0].stage.pdf_assets[0]
    fetched = fetch_gcs_bytes(
        first_asset.gcs_uri or "",
        expected_sha256=first_asset.sha256,
        storage_client=fake_gcs,
    )
    assert fetched.startswith(b"%PDF-1.4")
    assert (
        len(discover_manifests("gs://test-scenarios-bucket", storage_client=fake_gcs))
        == 1
    )


def test_live_gemini_multimodal_pdf_extraction_with_injected_client(
    monkeypatch,
) -> None:
    """Verify `process_uploaded_pdfs` invokes `gemini-3.8-flash` & `gemini-4-argon` when live."""
    engine, sid = _consolidation_engine()
    engine.reset(sid)
    op_store = engine.store
    catalog = engine.catalog
    pdf_cat = engine.pdf_catalog(sid)

    calls: list[str] = []

    class _FakeModels:
        def generate_content(
            self, *, model: str, contents: Any, config: Any = None
        ) -> Any:
            del contents, config
            calls.append(model)
            if "flash" in model:
                payload = {
                    "doc_role": "audited_financials",
                    "borrower_name": "Meridian Logistics Center LLC",
                    "issuer": "Grant & Sterling LLP",
                    "loan_amount": "$1,800,000.00",
                    "appraised_value": "$2,600,000.00",
                    "annual_noi": "$312,000.00",
                    "citation_page": 4,
                    "citation_snippet": "Note 6 Schedule of Existing Liabilities ($1.80M)",
                    "auto_waive_payoff_letter": False,
                    "liabilities": [
                        {
                            "creditor": "First National Bank",
                            "account_ref": "#FNB-4402",
                            "balance": "$412,000.00",
                            "is_internal_on_us": False,
                        }
                    ],
                    "payoff_wires": [],
                }
                return SimpleNamespace(
                    text=json.dumps(payload),
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=2700, candidates_token_count=350
                    ),
                )
            return SimpleNamespace(
                text="Live Argon rationale: checklist expanded 3 -> 7 with internal LOC waived.",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=620, candidates_token_count=110
                ),
            )

    fake_client = SimpleNamespace(models=_FakeModels())
    updated = process_uploaded_pdfs(
        engine.context(sid),
        resolved_files=[
            (
                "01_Meridian_2025_Audited_Financials.pdf",
                pdf_cat["meridian-financials"].pdf_bytes,
            )
        ],
        mode="live",
        run_beat=lambda beat: engine.run_beat(sid, beat),
        genai_client=fake_client,
    )
    assert updated["beat"] == 1
    assert updated["live_telemetry"]["execution_mode"] == "live_gcp"
    assert updated["live_telemetry"]["flash_input_tokens"] == 2700
    assert updated["live_telemetry"]["argon_calls"] == 1
    assert "Live Argon rationale" in updated["agent2_policy_status"]["detail"]
    assert len(calls) == 2

    monkeypatch.setenv("BRIDGE_CORS_ORIGINS", "*")
    dash_app = build_dashboard_app(
        operational_store=op_store, knowledge_catalog=catalog
    )
    dash_client = TestClient(dash_app)
    cors_resp = dash_client.get(
        "/v1/demo/consolidation-collab/state",
        headers={"Origin": "https://example.org"},
    )
    assert cors_resp.status_code == 200
    assert cors_resp.headers.get("access-control-allow-origin") == "*"
    assert "x-docubridge-trace-id" in cors_resp.headers.get(
        "access-control-expose-headers", ""
    )
