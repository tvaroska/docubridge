"""Offline unit tests for P8-5 Doctype Onboarding and P8-6 Ontology Slicing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridge.dashboard import build_dashboard_app
from bridge.disposition import run_disposition_gate
from bridge.doctypes import doctype_schemas, slice_ontology_for_extraction
from bridge.extraction.gemini import GeminiExtractor
from bridge.extraction.gemini_schema import (
    build_extraction_prompt,
    build_response_schema,
)
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.refinery import (
    EMPTY_CATALOG_ENV,
    EMPTY_REVISION_ID,
    ENTITY_COMMERCIAL_PROPERTY_QUOTE,
    ENTITY_CRE_CREDIT_AGREEMENT,
    ENTITY_FEE_SCHEDULE_ADDENDUM,
    ENTITY_TRID_CLOSING_DISCLOSURE,
    InMemorySchemaRefineryEngine,
    OntologyRevisionState,
    RefineryProposalStatus,
    SqlSchemaRefineryEngine,
    build_baseline_ontology_revision,
    create_schema_from_documents,
    detect_out_of_ontology_escape_cluster,
    load_manifest_file,
    onboard_use_case,
    parse_okf_entity_markdown,
    schema_refinery_from_env,
)
from contract.enums import Disposition, Gate


class _FakeArgonModels:
    """Credential-free stub for `gemini-4-argon` structured onboarding induction."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: list[Any],
        config: dict[str, Any],
    ) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        payload = {
            "entity_description": {
                "what_it_is": "Binding excess cyber liability binder schedule.",
                "where_to_read": "Page 1 Excess Binder Schedule & Layer Attachment box.",
                "distinguish_from": "Do NOT confuse with primary property quotes.",
            },
            "aliases": ["cyber-excess-binder"],
            "fields": {
                "annual_layer_premium": {
                    "required": True,
                    "monetary": True,
                    "feeds_metric": "total_premium",
                    "synonyms": ["Bound Layer Premium", "Annual Cyber Premium"],
                    "what_it_is": "Annual bound premium for the excess cyber layer.",
                    "where_to_read": "Section 2 Premium Summary row.",
                    "distinguish_from": "Exclude surplus lines stamping taxes.",
                }
            },
            "target_projection_hints": {
                "annual_layer_premium": "bound_premium_usd",
            },
        }
        return SimpleNamespace(text=json.dumps(payload), candidates=[])


class _FakeArgonClient:
    def __init__(self) -> None:
        self.models = _FakeArgonModels()


class _FakeWorkhorseModels:
    """Credential-free stub capturing prompt + response_schema sent by GeminiExtractor."""

    def __init__(self, response_payload: dict[str, Any]) -> None:
        self.response_payload = response_payload
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: list[Any],
        config: dict[str, Any],
    ) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(
            text=json.dumps(self.response_payload),
            candidates=[],
            usage_metadata=SimpleNamespace(
                prompt_token_count=420,
                candidates_token_count=85,
                total_token_count=505,
                cached_content_token_count=0,
            ),
        )


class _FakeWorkhorseClient:
    def __init__(self, response_payload: dict[str, Any]) -> None:
        self.models = _FakeWorkhorseModels(response_payload)


def test_p8_5_onboard_doctype_from_paired_examples_and_promote(tmp_path: Path) -> None:
    """Synthesizes a DRAFT `.okf.md` bundle with `[:FEEDS_METRIC]` + `[:SUPERSEDES]` and passes 3-Tier Assay."""
    samples = [
        {
            "sample_id": "s1",
            "filename": "Northwind_Equipment_Breakdown_Binder_1.pdf",
            "raw_text": (
                "# EQUIPMENT BREAKDOWN BINDER\n"
                "Annual Equipment Premium: $48,500.00\n"
                "Per-Accident Deductible: $25,000.00\n"
                "Boiler & Machinery Limit: $15,000,000.00\n"
                "Effective Date: 2027-01-01\n"
            ),
        },
        {
            "sample_id": "s2",
            "filename": "Harborview_Equipment_Breakdown_Binder_2.pdf",
            "extracted_fields": {
                "Annual Equipment Premium": "$62,000.00",
                "Per-Accident Deductible": "$50,000.00",
                "Boiler & Machinery Limit": "$20,000,000.00",
                "Effective Date": "2027-02-01",
                "Inspection Contact": "safety@harborview-example.com",
            },
        },
    ]
    paired_targets = [
        {
            "sample_id": "s1",
            "target_schema_name": "placement_ledger_row",
            "target_record": {
                "bound_annual_premium": "$48,500.00",
                "accident_retention": "$25,000.00",
                "equipment_limit": "$15,000,000.00",
            },
        }
    ]

    sql_dsn = f"sqlite+aiosqlite:///{tmp_path / 'refinery_onboard.db'}"
    catalog_mem = InMemoryKnowledgeCatalog()
    catalog_sql = InMemoryKnowledgeCatalog()
    engines = [
        InMemorySchemaRefineryEngine(knowledge_catalog=catalog_mem),
        SqlSchemaRefineryEngine(sql_dsn, knowledge_catalog=catalog_sql),
    ]
    try:
        for engine in engines:
            engine.reset()
            res = engine.onboard_doctype_from_examples(
                entity_id="equipment-breakdown-binder",
                samples=samples,
                paired_target_examples=paired_targets,
                spine_entity_id=ENTITY_COMMERCIAL_PROPERTY_QUOTE,
                context_set="commercial-property-placement",
                domain="insurance",
                source_priority=30,
            )

            assert res.draft_revision.state == OntologyRevisionState.DRAFT
            assert res.proposal.status == RefineryProposalStatus.ASSAY_PASSED
            assert res.assay_report.passed is True
            assert res.assay_report.negative_flip_rate == 0.0
            assert res.assay_report.dag_acyclic is True
            assert res.assay_report.decimal_formulas_valid is True
            assert res.assay_report.paired_removal_intact is True

            # Active revision remains baseline until explicitly promoted
            assert engine.get_active_revision().revision_id == "ont-rev-v1-1-baseline"

            # Verify induced ContrastiveDescription, synonyms, projection hints & edges
            spec = res.entity_spec
            assert spec.entity_description.what_it_is
            assert spec.entity_description.where_to_read
            assert (
                ENTITY_COMMERCIAL_PROPERTY_QUOTE
                in spec.entity_description.distinguish_from
            )
            assert (
                res.target_projection_hints["annual_equipment_premium"]
                == "bound_annual_premium"
            )
            assert (
                res.target_projection_hints["per_accident_deductible"]
                == "accident_retention"
            )
            assert spec.fields["annual_equipment_premium"].required is True
            assert spec.fields["inspection_contact"].required is False
            assert "currency" in spec.fields

            relations = {(e.relation, e.target_id) for e in spec.edges}
            assert ("SUPERSEDES", ENTITY_COMMERCIAL_PROPERTY_QUOTE) in relations
            assert ("FEEDS_METRIC", "FinancialSummary.total_premium") in relations
            assert ("FEEDS_METRIC", "FinancialSummary.total_deductible") in relations

            # Round-trip synthesized .okf.md bundle through parse_okf_entity_markdown
            parsed_back = parse_okf_entity_markdown(res.okf_markdown)
            assert parsed_back.entity_id == "equipment-breakdown-binder"
            assert set(parsed_back.fields.keys()) == set(spec.fields.keys())

            # Promote the ASSAY_PASSED proposal and verify active revision advances
            promoted = engine.promote_proposal(res.proposal.proposal_id)
            assert promoted.state == OntologyRevisionState.ACTIVE
            assert "equipment-breakdown-binder" in promoted.entities
            engine.reset()
    finally:
        for engine in engines:
            if hasattr(engine, "close"):
                engine.close()


def test_p8_5_argon_client_induction_and_bounds_validation() -> None:
    """Uses injected `gemini-4-argon` client when provided and enforces 1..5 sample bounds."""
    engine = InMemorySchemaRefineryEngine(knowledge_catalog=InMemoryKnowledgeCatalog())
    fake_argon = _FakeArgonClient()

    res = engine.onboard_doctype_from_examples(
        entity_id="excess-cyber-binder",
        samples=[
            {
                "sample_id": "c1",
                "extracted_fields": {"Annual Layer Premium": "$92,000.00"},
            }
        ],
        client=fake_argon,
    )
    assert res.induced_by_model == "gemini-4-argon"
    assert len(fake_argon.models.calls) == 1
    assert fake_argon.models.calls[0]["model"] == "gemini-4-argon"
    assert res.target_projection_hints["annual_layer_premium"] == "bound_premium_usd"
    assert "cyber-excess-binder" in res.entity_spec.aliases
    assert res.assay_report.passed is True

    with pytest.raises(ValueError, match="1 to 5 samples"):
        engine.onboard_doctype_from_examples(entity_id="empty-doc", samples=[])
    with pytest.raises(ValueError, match="1 to 5 samples"):
        engine.onboard_doctype_from_examples(
            entity_id="too-many",
            samples=[{"extracted_fields": {"k": "v"}} for _ in range(6)],
        )


def test_p8_5_post_refinery_onboard_http_endpoint() -> None:
    """Verifies `POST /v1/refinery/onboard` on the Zone 5 FastAPI app."""
    app = build_dashboard_app()
    client = TestClient(app)
    try:
        resp = client.post(
            "/v1/refinery/onboard",
            json={
                "entity_id": "marine-cargo-certificate",
                "samples": [
                    {
                        "sample_id": "m1",
                        "extracted_fields": {
                            "Cargo Insured Value": "$4,500,000.00",
                            "Voyage Premium": "$18,200.00",
                        },
                    }
                ],
                "paired_target_examples": [
                    {
                        "sample_id": "m1",
                        "target_record": {"shipment_tiv": "$4,500,000.00"},
                    }
                ],
                "spine_entity_id": ENTITY_COMMERCIAL_PROPERTY_QUOTE,
                "context_set": "commercial-property-placement",
                "domain": "insurance",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["draft_revision"]["state"] == "DRAFT"
        assert body["assay_report"]["passed"] is True
        assert body["assay_report"]["negative_flip_rate"] == 0.0
        assert body["target_projection_hints"]["cargo_insured_value"] == "shipment_tiv"
        assert "marine-cargo-certificate" in body["okf_markdown"]

        bad = client.post(
            "/v1/refinery/onboard",
            json={"entity_id": "bad", "samples": []},
        )
        assert bad.status_code == 400
    finally:
        client.post("/v1/refinery/reset")


@pytest.mark.asyncio
async def test_p8_6_target_scoped_slicing_reduces_footprint_and_preserves_escape_hatch() -> (
    None
):
    """Slices prompt/schema to target + 1-hop neighborhood while preserving free-string doctype."""
    full_schemas = doctype_schemas()
    assert len(full_schemas) >= 7

    catalog = InMemoryKnowledgeCatalog()
    refinery = InMemorySchemaRefineryEngine(knowledge_catalog=catalog)
    refinery.reset()
    try:
        baseline_rev = refinery.get_active_revision()

        # 1. Slice for `trid-closing-disclosure` -> brings in 1-hop `cre-credit-agreement` via [:SUPERSEDES]
        lending_slice = slice_ontology_for_extraction(
            ENTITY_TRID_CLOSING_DISCLOSURE,
            None,
            baseline_rev,
            schemas=full_schemas,
        )
        assert set(lending_slice.keys()) == {
            ENTITY_TRID_CLOSING_DISCLOSURE,
            ENTITY_CRE_CREDIT_AGREEMENT,
        }

        # 2. Promote banking split (`fee-schedule-addendum` [:SPLIT_FROM] `trid-closing-disclosure`)
        prop, rep, _ = refinery.run_taxonomy_assay(
            domain="banking",
            target_entity_id=ENTITY_TRID_CLOSING_DISCLOSURE,
        )
        assert rep.passed is True
        v1_2_rev = refinery.promote_proposal(prop.proposal_id)

        split_slice = slice_ontology_for_extraction(
            ENTITY_TRID_CLOSING_DISCLOSURE,
            None,
            v1_2_rev,
            schemas=full_schemas,
        )
        assert ENTITY_FEE_SCHEDULE_ADDENDUM in split_slice
        assert ENTITY_CRE_CREDIT_AGREEMENT in split_slice
        assert "gov-id" not in split_slice
        assert "benefits-quote" not in split_slice

        # 3. Verify prompt & response_schema footprint reduction
        full_prompt = build_extraction_prompt(full_schemas)
        scoped_prompt = build_extraction_prompt(
            full_schemas,
            target_doctype=ENTITY_TRID_CLOSING_DISCLOSURE,
            revision=baseline_rev,
        )
        assert len(scoped_prompt) < int(len(full_prompt) * 0.60)
        assert ENTITY_TRID_CLOSING_DISCLOSURE in scoped_prompt
        assert ENTITY_CRE_CREDIT_AGREEMENT in scoped_prompt
        assert "utility-bill" not in scoped_prompt

        full_resp_schema = build_response_schema(full_schemas)
        scoped_resp_schema = build_response_schema(
            full_schemas,
            target_doctype=ENTITY_TRID_CLOSING_DISCLOSURE,
            revision=baseline_rev,
        )
        assert full_resp_schema is not None and scoped_resp_schema is not None
        assert len(json.dumps(scoped_resp_schema)) < int(
            len(json.dumps(full_resp_schema)) * 0.55
        )

        # Critical invariant: fields.doctype MUST remain a free string with NO enum
        doctype_prop = scoped_resp_schema["properties"]["fields"]["properties"][
            "doctype"
        ]
        assert doctype_prop["type"] == "string"
        assert "enum" not in doctype_prop

        # 4. Out-of-ontology upload under target-scoped GeminiExtractor.extract()
        escape_payload = {
            "fields": {
                "doctype": "environmental-indemnity",
                "issuer": "Meridian Commercial Bank",
                "key_fields": {},
            },
            "overall_confidence": 0.94,
            "field_confidence": {},
            "legible": True,
            "flagged_fields": [],
            "field_reviews": [],
        }
        fake_client = _FakeWorkhorseClient(escape_payload)
        extractor = GeminiExtractor(
            client=fake_client,
            resolver=lambda _name: (
                b"%PDF-1.4\n% synthetic\n%%EOF\n",
                "application/pdf",
            ),
            schemas=full_schemas,
        )
        extracted = await extractor.extract(
            "meridian-env-indemnity.pdf",
            candidate_doctypes=[ENTITY_TRID_CLOSING_DISCLOSURE],
            revision=baseline_rev,
        )
        assert extracted.fields.doctype == "environmental-indemnity"
        sent_prompt = fake_client.models.calls[0]["contents"][0]
        assert "utility-bill" not in sent_prompt
        assert ENTITY_TRID_CLOSING_DISCLOSURE in sent_prompt

        # Disposition gate cleanly classifies out-of-ontology escape as unsupported/rejected
        decision = run_disposition_gate(
            doctype=extracted.fields.doctype,
            candidate_doctypes=sorted(lending_slice.keys()),
            legible=extracted.legible,
            overall_confidence=extracted.overall_confidence,
            flagged_fields=list(extracted.flagged_fields),
        )
        assert decision["disposition"] == Disposition.rejected.value
        assert decision["gate"] == Gate.unsupported.value
        assert decision["reason_code"] == "unsupported_doctype"

        # And feeds Schema Refinery escape-cluster detection cleanly
        escape_prop = detect_out_of_ontology_escape_cluster(
            observed_slug=extracted.fields.doctype,
            sample_count=5,
            domain="banking",
        )
        assert escape_prop.target_entity_id == "environmental-indemnity"
    finally:
        refinery.reset()


def test_empty_docubridge_boot_and_create_schema_from_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies DocuBridge starts empty under BRIDGE_EMPTY_CATALOG_ON_START=1 and induces schemas from sample files."""
    from bridge.edge.card import build_agent_card

    empty_skills = tmp_path / "skills"
    empty_skills.mkdir(parents=True, exist_ok=True)
    out_dir = tmp_path / "generated"

    monkeypatch.setenv(EMPTY_CATALOG_ENV, "1")
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(empty_skills))
    schema_refinery_from_env().reset()

    try:
        # 1. Empty boot: zero doctype bundles, ont-rev-v0-empty, and zero skills on the Agent Card
        rev = build_baseline_ontology_revision()
        assert rev.revision_id == EMPTY_REVISION_ID
        assert rev.entities == {}
        assert doctype_schemas() == {}
        card = build_agent_card()
        assert card.skills == []

        # 2. Induce a doctype schema from sample SVG documents on disk
        repo_root = Path(__file__).resolve().parent.parent
        pkg = create_schema_from_documents(
            entity_id="utility-bill",
            sample_files=[
                repo_root
                / "wiki"
                / "evals"
                / "address"
                / "images"
                / "bill-powerco-clean.svg",
                repo_root
                / "wiki"
                / "evals"
                / "address"
                / "images"
                / "bill-aquautil-clean.svg",
            ],
            paired_target_examples=[
                {
                    "holder_name": "JANE M. DOE",
                    "service_address": "742 EVERGREEN TERRACE, SPRINGFIELD",
                    "issuer": "Northstar Electric Co.",
                    "statement_date": "2026-02-18",
                }
            ],
            spine_entity_id="spine.party",
            domain="kyc",
            auto_promote=True,
            install_skill=True,
            write_dir=out_dir,
        )
        assert pkg.onboarding_result.assay_report.passed is True
        assert pkg.installed_in_registry is True
        assert (out_dir / "catalog" / "doctypes" / "utility-bill.okf.md").is_file()
        assert (out_dir / "skills" / "utility-bill" / "SKILL.md").is_file()
        assert (
            out_dir / "skills" / "utility-bill" / "assets" / "schema.json"
        ).is_file()
        assert (
            out_dir / "skills" / "utility-bill" / "assets" / "validation.yaml"
        ).is_file()

        # Doctype is now live in doctype_schemas() and KnowledgeCatalog
        live_schemas = doctype_schemas()
        assert "utility-bill" in live_schemas
        assert (
            "service_address"
            in live_schemas["utility-bill"]["properties"]["key_fields"]["properties"]
        )
    finally:
        monkeypatch.delenv(EMPTY_CATALOG_ENV, raising=False)
        monkeypatch.delenv("BRIDGE_SKILLS_DIR", raising=False)
        schema_refinery_from_env().reset()


def test_onboard_use_case_manifest_and_http_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Onboards the address and benefits use-case manifests onto an empty DocuBridge instance."""
    import yaml

    from bridge.edge.card import build_agent_card

    empty_skills = tmp_path / "skills"
    empty_skills.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv(EMPTY_CATALOG_ENV, "1")
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(empty_skills))
    schema_refinery_from_env().reset()

    try:
        assert build_agent_card().skills == []
        repo_root = Path(__file__).resolve().parent.parent

        # 1. Onboard Address Proof use-case via load_manifest_file + onboard_use_case
        addr_manifest_path = tmp_path / "address-manifest.yaml"
        addr_manifest_path.write_text(
            yaml.safe_dump(
                {
                    "process_name": "address-proof",
                    "process_description": "Collect proof of residential address.",
                    "pattern": "collect",
                    "satisfaction": "Accept one unexpired gov-id OR two utility bills from distinct issuers.",
                    "doctypes": [
                        {
                            "entity_id": "gov-id",
                            "spine_entity_id": "spine.party",
                            "domain": "kyc",
                            "sample_files": [
                                str(
                                    repo_root
                                    / "wiki"
                                    / "evals"
                                    / "address"
                                    / "images"
                                    / "gov-id-clean.svg"
                                )
                            ],
                        },
                        {
                            "entity_id": "utility-bill",
                            "spine_entity_id": "spine.party",
                            "domain": "kyc",
                            "sample_files": [
                                str(
                                    repo_root
                                    / "wiki"
                                    / "evals"
                                    / "address"
                                    / "images"
                                    / "bill-powerco-clean.svg"
                                )
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        addr_manifest, addr_base = load_manifest_file(addr_manifest_path)
        addr_res = onboard_use_case(addr_manifest, base_dir=addr_base)
        assert addr_res.process_name == "address-proof"
        assert set(addr_res.installed_doctypes) == {"gov-id", "utility-bill"}
        assert "address-proof" in addr_res.agent_card_skill_ids

        # 2. Onboard Benefits Procurement use-case via POST /v1/refinery/onboard-use-case
        ben_payload = {
            "process_name": "benefits-procurement",
            "process_description": "Collect carrier quotes and negotiate rates.",
            "pattern": "negotiate",
            "doctypes": [
                {
                    "entity_id": "benefits-quote",
                    "spine_entity_id": "spine.quote",
                    "domain": "benefits",
                    "samples": [
                        {
                            "sample_id": "ben-q1",
                            "extracted_fields": {
                                "Carrier": "SunLife Benefits",
                                "Coverage Line": "AD&D",
                                "Volume Rate": "0.025",
                            },
                        }
                    ],
                    "tables": {
                        "rates": {"type": "array", "items": {"type": "object"}},
                        "design": {"type": "array", "items": {"type": "object"}},
                    },
                }
            ],
        }
        app = build_dashboard_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/refinery/onboard-use-case",
            json=ben_payload,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["process_name"] == "benefits-procurement"
        assert body["installed_doctypes"] == ["benefits-quote"]
        assert set(body["agent_card_skill_ids"]) == {
            "address-proof",
            "benefits-procurement",
        }

        # Verify tables.rates and tables.design were preserved in benefits-quote schema.json
        live_schemas = doctype_schemas()
        assert (
            "rates"
            in live_schemas["benefits-quote"]["properties"]["tables"]["properties"]
        )
        assert (
            "design"
            in live_schemas["benefits-quote"]["properties"]["tables"]["properties"]
        )

        # Verify Agent Card now advertises both onboarded use-case processes
        card = build_agent_card()
        assert {s.id for s in card.skills} == {"address-proof", "benefits-procurement"}
    finally:
        monkeypatch.delenv(EMPTY_CATALOG_ENV, raising=False)
        monkeypatch.delenv("BRIDGE_SKILLS_DIR", raising=False)
        schema_refinery_from_env().reset()
