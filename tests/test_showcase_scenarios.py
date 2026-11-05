"""Showcase scenario contract, loader and engine (P9-1).

Beat outcomes asserted here are the same ones the legacy per-domain modules were
held to in ``test_enterprise_data_architecture`` / ``test_schema_refinery_engine``,
now driven purely from ``scenarios/<name>/scenario.yaml``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bridge.a2ui_inbox import InMemoryActionInboxStore
from bridge.agent_gym import InMemoryAgentGymEngine
from bridge.extraction.chunking import pdf_page_count
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.refinery import (
    BASELINE_REVISION_ID,
    ENTITY_RATE_GUARANTEE_ADDENDUM,
    ENTITY_WIND_ENDORSEMENT,
    InMemorySchemaRefineryEngine,
)
from bridge.showcase import (
    SCENARIOS_DIR,
    SCENARIOS_DIR_ENV,
    ScenarioEngine,
    ScenarioLoadError,
    UnknownScenarioError,
    discover_manifests,
    load_manifest,
    registered_operations,
    scenarios_root,
)
from bridge.showcase.context import OperationContext, resolve_by_beat
from bridge.showcase.loader import MANIFEST_FILENAME
from bridge.user_interaction import build_user_interaction_app
from contract.scenario import ScenarioDomain, ScenarioManifest, ScenarioStageKind

REPO_ROOT = Path(__file__).resolve().parents[1]


def _engine() -> ScenarioEngine:
    store = InMemoryOperationalStore()
    return ScenarioEngine(
        store=store,
        catalog=InMemoryKnowledgeCatalog(operational_store=store),
        inbox=InMemoryActionInboxStore(),
        gym=InMemoryAgentGymEngine(),
        refinery=InMemorySchemaRefineryEngine(),
    )


def _by_kind(
    engine: ScenarioEngine, kind: ScenarioStageKind, domain: str | None = None
) -> str:
    manifest = engine.find(kind=kind, domain=domain)
    assert manifest is not None, (kind, domain)
    return manifest.id


# --------------------------------------------------------------------------- #
# Contract + loader                                                            #
# --------------------------------------------------------------------------- #
def test_every_manifest_loads_validates_and_matches_its_folder() -> None:
    manifests = discover_manifests()
    assert len(manifests) >= 5
    ids = [m.id for m in manifests]
    assert ids == sorted(
        ids, key=lambda i: next(m.order for m in manifests if m.id == i)
    )
    for m in manifests:
        assert isinstance(m, ScenarioManifest)
        assert (SCENARIOS_DIR / m.id / MANIFEST_FILENAME).is_file()
        assert m.domain in ScenarioDomain
        assert m.display.client and m.display.program
        if m.stage is not None:
            indices = [b.index for b in m.stage.beats]
            assert indices == list(range(1, len(indices) + 1)), m.id
            for beat in m.stage.beats:
                for op in beat.operations:
                    assert op.name in registered_operations(), (m.id, op.name)
            for op in m.stage.setup:
                assert op.name in registered_operations(), (m.id, op.name)
    summaries = {s["id"]: s for s in (m.summary() for m in manifests)}
    assert "seed" not in summaries[ids[0]]
    assert summaries[ids[0]]["display"]["client"]


def test_manifests_cover_the_seeded_entities_and_both_stage_kinds() -> None:
    manifests = {m.id: m for m in discover_manifests()}
    seeded = {m.entity_id for m in manifests.values() if m.seed is not None}
    assert {
        "LOAN-MERIDIAN-001",
        "MORTGAGE-WESTBROOK-2026",
        "terrepower-coastal-2027",
    } <= seeded
    kinds = {m.stage.kind for m in manifests.values() if m.stage is not None}
    assert kinds == {
        ScenarioStageKind.consolidation,
        ScenarioStageKind.schema_evolution,
    }


def test_manifest_rejects_unknown_keys_and_folder_mismatch(tmp_path: Path) -> None:
    src = SCENARIOS_DIR / "loan-meridian-001" / MANIFEST_FILENAME
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    folder = tmp_path / "loan-meridian-001"
    folder.mkdir()
    doc["unexpected_key"] = 1
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ScenarioLoadError):
        load_manifest(folder / MANIFEST_FILENAME)
    doc.pop("unexpected_key")
    renamed = tmp_path / "other-folder"
    renamed.mkdir()
    (renamed / MANIFEST_FILENAME).write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ScenarioLoadError):
        load_manifest(renamed / MANIFEST_FILENAME)


def test_scenarios_root_resolves_env_then_repo_default(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(SCENARIOS_DIR_ENV, raising=False)
    assert scenarios_root() == SCENARIOS_DIR
    monkeypatch.setenv(SCENARIOS_DIR_ENV, str(tmp_path))
    assert scenarios_root() == tmp_path
    assert scenarios_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_resolve_by_beat_windows_and_tables() -> None:
    view = {
        "x": {"by_beat": {0: "a", 2: "b"}},
        "items": [
            {"id": 1},
            {"id": 2, "from_beat": 1, "until_beat": 2},
            {"id": 3, "from_beat": 3},
        ],
        "missing": {"by_beat": {4: "late"}},
    }
    assert resolve_by_beat(view, 0) == {"x": "a", "items": [{"id": 1}], "missing": None}
    assert resolve_by_beat(view, 2)["x"] == "b"
    assert [i["id"] for i in resolve_by_beat(view, 2)["items"]] == [1, 2]
    assert [i["id"] for i in resolve_by_beat(view, 3)["items"]] == [1, 3]


# --------------------------------------------------------------------------- #
# A new scenario folder is picked up with zero Python changes                  #
# --------------------------------------------------------------------------- #
def test_fourth_scenario_folder_appears_without_python_changes(
    monkeypatch, tmp_path: Path
) -> None:
    for folder in SCENARIOS_DIR.iterdir():
        if (folder / MANIFEST_FILENAME).is_file():
            shutil.copytree(folder, tmp_path / folder.name)
    src = yaml.safe_load(
        (SCENARIOS_DIR / "loan-meridian-001" / MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    src["id"] = "fixture-showcase-9000"
    src["entity_id"] = "FIXTURE-SHOWCASE-9000"
    src["domain"] = "other"
    src["order"] = 5
    src["display"]["client"] = "Fixture Client Co"
    (tmp_path / "fixture-showcase-9000").mkdir()
    (tmp_path / "fixture-showcase-9000" / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(src, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.setenv(SCENARIOS_DIR_ENV, str(tmp_path))

    client = TestClient(build_user_interaction_app())
    listed = client.get("/v1/demo/scenarios")
    assert listed.status_code == 200
    scenarios = listed.json()["scenarios"]
    assert scenarios[0]["id"] == "fixture-showcase-9000"
    assert scenarios[0]["display"]["client"] == "Fixture Client Co"
    assert scenarios[0]["seeded"] is True
    assert (
        client.get("/v1/demo/scenarios/fixture-showcase-9000/state").status_code == 200
    )
    seeded = client.post("/v1/demo/scenarios/seed").json()
    assert "FIXTURE-SHOWCASE-9000" in seeded["seeded_entities"]
    with pytest.raises(UnknownScenarioError):
        _engine().state("nope")


# --------------------------------------------------------------------------- #
# Seeds                                                                        #
# --------------------------------------------------------------------------- #
def test_seed_all_is_idempotent_and_reproduces_the_canonical_figures() -> None:
    engine = _engine()
    first = engine.seed_all()
    assert set(first["seeded_entities"]) >= {
        "LOAN-MERIDIAN-001",
        "MORTGAGE-WESTBROOK-2026",
        "terrepower-coastal-2027",
    }
    assert engine.seed_all()["seeded_entities"] == []
    winners, _ = engine.store.query_as_of("LOAN-MERIDIAN-001")
    fee = next(f for f in winners if f.field_path.endswith("origination_fee"))
    assert fee.field_value == "$100,000.00"
    winners, _ = engine.store.query_as_of("MORTGAGE-WESTBROOK-2026")
    assert (
        next(f for f in winners if f.field_path.endswith("origination_fee")).field_value
        == "$8,500.00"
    )
    winners, _ = engine.store.query_as_of("terrepower-coastal-2027")
    assert (
        next(f for f in winners if f.field_path.endswith("deductible")).field_value
        == "$100,000.00"
    )


# --------------------------------------------------------------------------- #
# Consolidation stage                                                          #
# --------------------------------------------------------------------------- #
def test_consolidation_scenario_state_advance_reset_match_legacy_outcomes() -> None:
    engine = _engine()
    sid = _by_kind(engine, ScenarioStageKind.consolidation)

    s0 = engine.state(sid).payload
    assert s0["entity_id"] == "LOAN-MERIDIAN-CONSOL-2026"
    assert s0["beat"] == 0 and s0["done"] is False
    assert s0["counts"] == {"total": 3, "satisfied": 0, "waived": 0, "required": 3}
    assert len(s0["borrower_checklist"]) == 3
    assert s0["financial_summary"]["consolidated_loan_amount"] == "$1,800,000.00"
    assert s0["financial_summary"]["appraised_collateral_value"] == "$2,600,000.00"
    assert s0["financial_summary"]["ltv_formatted"] == "69.23%"
    assert s0["financial_summary"]["dscr_formatted"] == "1.44x"

    b1 = engine.advance(sid, 1)
    assert b1.executed_beats == (1,)
    p1 = b1.payload
    assert p1["beat"] == 1 and p1["done"] is False
    assert p1["counts"] == {"total": 6, "satisfied": 2, "waived": 1, "required": 3}
    items = {i["id"]: i for i in p1["borrower_checklist"]}
    assert items["item-1-financials"]["status"] == "satisfied"
    assert items["item-1-financials"]["page_number"] == 4
    assert items["item-1-financials"]["vault_uri"]
    assert items["item-3-appraisal"]["status"] == "satisfied"
    assert items["item-4-fnb-payoff"]["status"] == "required"
    assert items["item-4-fnb-payoff"]["emergent"] is True
    assert items["item-5-equip-payoff"]["status"] == "required"
    assert items["item-6-internal-loc"]["status"] == "waived"

    p2 = engine.advance(sid, 2).payload
    assert p2["counts"] == {"total": 6, "satisfied": 4, "waived": 1, "required": 1}
    assert p2["cross_doc_discovery"]["page_number"] == 2
    assert p2["cross_doc_discovery"]["bounding_box"] == [0.16, 0.54, 0.88, 0.63]
    assert p2["cross_doc_discovery"]["wire_amount"] == "$48,210.00"
    assert "PAID IN FULL" in p2["cross_doc_discovery"]["wire_reference"]
    assert p2["underwriter_overwatch"]["status"] == "awaiting_approval"

    p3 = engine.advance(sid, 3).payload
    assert p3["beat"] == 3 and p3["done"] is True
    assert p3["counts"] == {"total": 6, "satisfied": 4, "waived": 2, "required": 0}
    assert {i["id"]: i for i in p3["borrower_checklist"]}["item-5-equip-payoff"][
        "status"
    ] == ("waived")
    assert p3["underwriter_overwatch"]["status"] == "approved"
    assert p3["underwriter_overwatch"]["oversight_rsr_score"] == 0.98
    assert p3["underwriter_overwatch"]["review_dwell_ms"] == 4800
    assert p3["underwriter_overwatch"]["inbox_item_id"]
    assert (
        "LOAN-MERIDIAN-CONSOL-2026" in p3["underwriter_overwatch"]["resolution_summary"]
    )
    assert len(engine.inbox.list_audit_records()) >= 1

    p4 = engine.advance(sid, 4).payload
    payoff = p4["agent_gym_payoff"]
    assert payoff["skill_name"] == "consolidation-payoff"
    assert payoff["skill_version"] == "1.1.0"
    assert payoff["stress_cases_passed"] == 4 and payoff["stress_cases_total"] == 4
    assert len(payoff["stress_cases"]) == 4
    assert payoff["day2_entity_id"] == "LOAN-APEX-CONSOL-DAY2"
    assert payoff["day2_status"] == "accepted" and payoff["day2_gate"] == "auto_approve"
    assert payoff["day2_latency_ms"] == 142
    assert payoff["human_touchpoints"] == 0 and payoff["escalation_tokens"] == 0
    assert "consolidation-payoff@1.1.0" in p4["agent1_bridge_status"]["headline"]
    assert payoff["rule_id"] in p4["agent2_policy_status"]["headline"]

    # Advancing backwards restarts from Beat 0 before replaying.
    back = engine.advance(sid, 1)
    assert back.executed_beats == (1,)
    assert back.payload["counts"]["total"] == 6

    rst = engine.reset(sid).payload
    assert rst["beat"] == 0 and rst["done"] is False
    assert rst["counts"] == {"total": 3, "satisfied": 0, "waived": 0, "required": 3}


def test_consolidation_pdf_assets_render_from_declarative_specs() -> None:
    engine = _engine()
    sid = _by_kind(engine, ScenarioStageKind.consolidation)
    catalog = engine.pdf_catalog(sid)
    assert len(catalog) == 5
    for asset in catalog.values():
        assert asset.pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_page_count(asset.pdf_bytes) == asset.asset.page_count
        assert asset.sha256
    assert engine.pdf_url(sid, "meridian-financials").endswith(
        "/01_Meridian_2025_Audited_Financials.pdf"
    )


# --------------------------------------------------------------------------- #
# Schema evolution stage (insurance + benefits share one refinery)             #
# --------------------------------------------------------------------------- #
def test_schema_evolution_insurance_beats_match_legacy_outcomes() -> None:
    engine = _engine()
    sid = _by_kind(engine, ScenarioStageKind.schema_evolution, "insurance")
    b0 = engine.reset(sid).payload
    assert b0["scenario_domain"] == "insurance"
    assert b0["entity_id"] == "terrepower-coastal-2027"
    assert b0["beat"] == 0
    assert b0["active_ontology_revision"] == BASELINE_REVISION_ID
    assert b0["entity_count"] == 7
    assert len(b0["placement_checklist"]) == 3

    b1 = engine.advance(sid, 1).payload
    assert b1["agent2_status"]["jaccard_similarity"] == 0.0667
    assert b1["placement_checklist"][-1]["status"] == "quarantined"

    b2 = engine.advance(sid, 2).payload
    assert b2["proposal"]["new_entity_id"] == ENTITY_WIND_ENDORSEMENT
    assert b2["assay_report"]["passed"] is True
    assert b2["assay_report"]["negative_flip_rate"] == 0.0
    assert len(b2["proposal"]["added_edges"]) == 3

    b3 = engine.advance(sid, 3).payload
    assert b3["active_ontology_revision"] == "ont-rev-v1-2-insurance"
    assert b3["entity_count"] == 8 and b3["done"] is True
    assert b3["financial_summary"]["coastal_wind_deductible"] == "$100,000.00"
    assert ENTITY_WIND_ENDORSEMENT in b3["financial_summary"]["deductible_authority"]
    assert len(b3["graph_edges_preview"]) == 4

    b4 = engine.advance(sid, 4).payload
    assert b4["day2_payoff"]["day2_entity_id"] == "PLACEMENT-HARBORVIEW-DAY2"
    assert b4["day2_payoff"]["classified_doctype"] == ENTITY_WIND_ENDORSEMENT
    assert b4["day2_payoff"]["false_positive_quarantine_parks"] == 0
    assert b4["day2_payoff"]["ontology_revision_id"] == "ont-rev-v1-2-insurance"


def test_schema_evolution_benefits_beats_and_refinery_handoff() -> None:
    engine = _engine()
    insurance = _by_kind(engine, ScenarioStageKind.schema_evolution, "insurance")
    benefits = _by_kind(engine, ScenarioStageKind.schema_evolution, "benefits")
    engine.advance(insurance, 3)
    assert engine.refinery is not None
    assert engine.refinery.get_active_revision().version == "1.2.0"

    b0 = engine.reset(benefits).payload
    assert b0["scenario_domain"] == "benefits"
    assert b0["entity_id"] == "BENEFITS-APEX-HDHP-2027"
    assert b0["active_ontology_revision"] == BASELINE_REVISION_ID
    assert engine.state(insurance).payload["beat"] == 0

    b1 = engine.advance(benefits, 1).payload
    assert "FSA: $1,000.00" in b1["financial_summary"]["coastal_wind_deductible"]
    assert "can ANY HDHP" in b1["socratic_challenge"]["step2_system_question"]
    assert b1["agent2_status"]["jaccard_similarity"] == 0.0714

    b2 = engine.advance(benefits, 2).payload
    assert b2["proposal"]["new_entity_id"] == ENTITY_RATE_GUARANTEE_ADDENDUM
    relations = {e["relation"] for e in b2["proposal"]["added_edges"]}
    assert {
        "SUPERSEDES",
        "SPLIT_FROM",
        "FEEDS_METRIC",
        "INCLUDES",
        "EXCLUDES",
    } <= relations
    assert all(
        e["status"] == "CANDIDATE (ASSAY PASSED)" for e in b2["graph_edges_preview"][2:]
    )

    b3 = engine.advance(benefits, 3).payload
    assert b3["active_ontology_revision"] == "ont-rev-v1-2-benefits"
    assert "HSA: $1,000.00 ✓" in b3["financial_summary"]["coastal_wind_deductible"]
    assert "$125,000.00" in b3["financial_summary"]["primary_plus_excess_limit"]

    b4 = engine.advance(benefits, 4).payload
    assert b4["day2_payoff"]["day2_entity_id"] == "BENEFITS-NORTHSTAR-DAY2"
    assert "HSA: $1,500.00 ✓" in b4["day2_payoff"]["resolved_deductible"]
    assert "BENEFITS-NORTHSTAR-DAY2" in b4["banner"]["message"]


# --------------------------------------------------------------------------- #
# Generic routes + legacy aliases                                              #
# --------------------------------------------------------------------------- #
def test_generic_routes_and_legacy_aliases_share_one_engine() -> None:
    client = TestClient(build_user_interaction_app())
    listed = client.get("/v1/demo/scenarios").json()["scenarios"]
    consol = next(s for s in listed if s["stage_kind"] == "consolidation")
    assert len(consol["beats"]) == 4 and len(consol["pdf_assets"]) == 5

    adv = client.post(f"/v1/demo/scenarios/{consol['id']}/advance", json={"beat": 2})
    assert adv.status_code == 200
    body = adv.json()
    assert body["scenario_id"] == consol["id"]
    assert body["executed_beats"] == [1, 2]
    assert body["payload"]["counts"]["satisfied"] == 4
    assert client.get("/v1/demo/consolidation-collab/state").json()["beat"] == 2
    with pytest.raises(ValueError, match=r"beat must be in 1\.\.4"):
        _engine().advance(consol["id"], 9)
    assert client.post(f"/v1/demo/scenarios/{consol['id']}/reset").json()["beat"] == 0

    pdfs = client.get(f"/v1/demo/scenarios/{consol['id']}/pdfs")
    assert pdfs.status_code == 200 and len(pdfs.json()["pdfs"]) == 5
    first = pdfs.json()["pdfs"][0]["filename"]
    assert (
        client.get(f"/v1/demo/scenarios/{consol['id']}/pdfs/{first}").status_code == 200
    )

    evo = client.get("/v1/demo/schema-evolution/state?domain=benefits").json()
    assert evo["scenario_domain"] == "benefits"
    generic = client.get(f"/v1/demo/scenarios/{listed[-1]['id']}/state").json()
    assert generic["payload"]["scenario_domain"] == listed[-1]["domain"]
    client.post("/v1/demo/schema-evolution/reset")


def test_operation_context_placeholders() -> None:
    engine = _engine()
    sid = _by_kind(engine, ScenarioStageKind.consolidation)
    ctx: OperationContext = engine.context(sid)
    assert ctx.render("${entity_id}") == ctx.manifest.entity_id
    assert ctx.render("${entity:day2}") == ctx.manifest.related_entity_ids["day2"]
    assert ctx.render("x ${display.client} y") == f"x {ctx.manifest.display.client} y"
    assert ctx.render("${pdf_url:fnb-payoff}").endswith(".pdf")
    with pytest.raises(KeyError):
        ctx.render("${doc:missing.vault_uri}")
