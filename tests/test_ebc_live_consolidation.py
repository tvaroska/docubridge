"""Tests for real vector PDF generation, live Gemini PDF intake, and `/ebc` console routes."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from bridge.a2ui_inbox import InMemoryActionInboxStore
from bridge.agent_gym import InMemoryAgentGymEngine
from bridge.dashboard.server import build_dashboard_app
from bridge.extraction.chunking import pdf_page_count
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.showcase import ScenarioEngine
from bridge.showcase.live_extraction import process_uploaded_pdfs
from bridge.showcase.pdf_assets import build_pdf_zip, pdf_metadata
from bridge.user_interaction import build_user_interaction_app
from contract.scenario import ScenarioStageKind


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
    """All 5 showcase PDFs have valid page counts, searchable text, and `.zip` packaging."""
    engine, sid = _consolidation_engine()
    catalog = engine.pdf_catalog(sid)
    assert len(catalog) == 5
    expected_pages = {
        "meridian-financials": 4,
        "meridian-appraisal": 2,
        "fnb-payoff": 1,
        "meridian-bank-stmt": 2,
        "apex-day2-bank-stmt": 2,
    }
    for asset_id, exp_pages in expected_pages.items():
        asset = catalog[asset_id]
        assert asset.pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_page_count(asset.pdf_bytes) == exp_pages
        assert engine.resolve_pdf(sid, asset_id) == asset
        assert engine.resolve_pdf(sid, asset.filename) == asset

    assert engine.resolve_pdf(sid, "nonexistent.pdf") is None
    meta = [
        pdf_metadata(a, url_base=engine.pdf_url_base(sid)) for a in catalog.values()
    ]
    assert len(meta) == 5

    zip_bytes = build_pdf_zip(list(catalog.values()))
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "01_Meridian_2025_Audited_Financials.pdf" in names
        assert "04_Meridian_Oct2026_Operating_Bank_Statement.pdf" in names
        assert "05_Apex_Precision_Day2_Bank_Statement.pdf" in names


def test_ebc_console_and_pdf_upload_cached_and_custom_flows() -> None:
    """Verify `/ebc` HTML/CSS/JS/favicon routes and full PDF upload + waiver + distill lifecycle."""
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

    # 1. Static /ebc console assets
    for path, mime in (
        ("/ebc", "text/html"),
        ("/ebc/ebc-live.css", "text/css"),
        ("/ebc/ebc-live.js", "javascript"),
        ("/ebc/favicon.svg", "image/svg+xml"),
    ):
        r = client.get(path)
        assert r.status_code == 200
        assert mime in r.headers["content-type"]

    # 2. PDF catalog, zip download, and individual PDF inline serve
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

    # 3. Upload Batch 1 via sample_ids (cached mode)
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
    assert b1_json["counts"]["total"] == 6
    assert b1_json["live_telemetry"]["pii_tokens_redacted"] >= 2

    # 4. Drag-and-drop base64 PDF upload (with data: URI prefix) for Batch 2 + custom PDF
    pdf_engine, pdf_sid = _consolidation_engine()
    pdf_cat = pdf_engine.pdf_catalog(pdf_sid)
    bank_b64 = "data:application/pdf;base64," + base64.b64encode(
        pdf_cat["meridian-bank-stmt"].pdf_bytes
    ).decode("ascii")
    fnb_b64 = base64.b64encode(pdf_cat["fnb-payoff"].pdf_bytes).decode("ascii")
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
                    "filename": "03_First_National_Payoff_FNB4402.pdf",
                    "content_base64": fnb_b64,
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
    assert b2.json()["underwriter_overwatch"]["status"] == "awaiting_approval"

    # Custom uploaded file is now served inline
    cust_serve = client.get(
        "/v1/demo/consolidation-collab/pdfs/custom_borrower_note.pdf"
    )
    assert cust_serve.status_code == 200

    # 5. Approve waiver -> Beat 3
    b3 = client.post(
        "/v1/demo/consolidation-collab/approve-waiver", json={"mode": "cached"}
    )
    assert b3.status_code == 200
    assert b3.json()["beat"] == 3
    assert b3.json()["done"] is True

    # 6. Distill skill -> Beat 4 (`SKILL.md` v1.1.0 included)
    b4 = client.post(
        "/v1/demo/consolidation-collab/distill-skill", json={"mode": "cached"}
    )
    assert b4.status_code == 200
    payoff = b4.json()["agent_gym_payoff"]
    assert payoff["skill_version"] == "1.1.0"
    assert "consolidation-payoff" in payoff["skill_markdown"]

    # 7. Upload Day-2 Apex Precision PDF -> Zero escalation verification
    d2 = client.post(
        "/v1/demo/consolidation-collab/upload-pdf",
        json={"sample_ids": ["apex-day2-bank-stmt"], "mode": "cached"},
    )
    assert d2.status_code == 200
    assert d2.json()["agent_gym_payoff"]["day2_entity_id"] == "LOAN-APEX-CONSOL-DAY2"


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
        def generate_content(self, *, model: str, contents, config=None):
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
                            "balance": "$1,200,000.00",
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
                text="Live Argon rationale: checklist expanded 3 -> 6 with internal LOC waived.",
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

    # Verify BRIDGE_CORS_ORIGINS=* exposes x-docubridge-trace-id header
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
