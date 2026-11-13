"""Tests for P8-2, P8-3, and P8-4: Graph Consolidation, Schema Refinery Engine & Showcase UI."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridge.a2ui_inbox import InMemoryActionInboxStore
from bridge.extraction.fixture import FixtureExtractor
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import (
    InMemoryOperationalStore,
    compute_financial_summary,
    source_priority_for_doctype,
)
from bridge.refinery import (
    BASELINE_REVISION_ID,
    ENTITY_BENEFITS_QUOTE,
    ENTITY_COMMERCIAL_PROPERTY_QUOTE,
    ENTITY_FEE_SCHEDULE_ADDENDUM,
    ENTITY_RATE_GUARANTEE_ADDENDUM,
    ENTITY_TRID_CLOSING_DISCLOSURE,
    ENTITY_WIND_ENDORSEMENT,
    InMemorySchemaRefineryEngine,
    OntologyEdge,
    OntologyRevisionState,
    RefineryProposalStatus,
    SqlSchemaRefineryEngine,
    detect_chronic_absent_field_cardinality,
    detect_colliding_entities,
    detect_out_of_ontology_escape_cluster,
    verify_supersedes_dag_acyclic,
)
from bridge.user_interaction import build_user_interaction_app, seed_demo_scenarios
from contract.extraction import ExtractionResult


def test_p8_2_graph_driven_consolidation_and_canonical_deal_rollups() -> None:
    """Zone 3 & Zone 6: [:SUPERSEDES] and [:FEEDS_METRIC] graph lookups match canonical rollups."""
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    catalog.reset_ontology_revisions()

    prio_cd, edge_cd = catalog.resolve_doctype_priority("trid-closing-disclosure")
    prio_ca, edge_ca = catalog.resolve_doctype_priority("cre-credit-agreement")
    prio_ap, _ = catalog.resolve_doctype_priority("mai-appraisal")
    prio_ub, _ = catalog.resolve_doctype_priority("utility-bill")
    assert (prio_cd, prio_ca, prio_ap, prio_ub) == (40, 30, 20, 10)
    assert edge_cd == "edge-trid-supersedes-credit-agreement"
    assert edge_ca == "edge-credit-agreement-supersedes-appraisal"

    bucket_ded, _ = catalog.resolve_metric_bucket(
        "deductible", "commercial-property-quote"
    )
    bucket_fee, _ = catalog.resolve_metric_bucket(
        "origination_fee", "trid-closing-disclosure"
    )
    bucket_tiv, _ = catalog.resolve_metric_bucket("appraised_value", "mai-appraisal")
    assert bucket_ded == "total_deductible"
    assert bucket_fee == "total_deductible"
    assert bucket_tiv == "total_insured_value"
    assert catalog.is_monetary_field("deductible") is True
    assert catalog.is_monetary_field("property_address") is False

    seed_demo_scenarios(operational_store=store, knowledge_catalog=catalog)

    # 1. Insurance: terrepower-coastal-2027
    tp_facts, tp_conflicts = store.query_as_of("terrepower-coastal-2027")
    tp_summary = compute_financial_summary(tp_facts)
    assert tp_summary.total_deductible == "100000.00"
    assert tp_summary.total_coverage_limit == "50000000.00"
    assert tp_summary.total_insured_value == "142500000.00"
    assert tp_summary.total_premium == "418500.00"
    assert len(tp_conflicts) == 1
    assert all(f.ontology_revision_id == BASELINE_REVISION_ID for f in tp_facts)

    # 2. Banking: LOAN-MERIDIAN-001
    mer_facts, mer_conflicts = store.query_as_of("LOAN-MERIDIAN-001")
    mer_summary = compute_financial_summary(mer_facts)
    assert mer_summary.total_deductible == "100000.00"
    assert mer_summary.total_coverage_limit == "45000000.00"
    assert mer_summary.total_insured_value == "62500000.00"
    assert len(mer_conflicts) == 1

    # 3. Mortgage: MORTGAGE-WESTBROOK-2026
    wb_facts, wb_conflicts = store.query_as_of("MORTGAGE-WESTBROOK-2026")
    wb_summary = compute_financial_summary(wb_facts)
    assert wb_summary.total_deductible == "8500.00"
    assert wb_summary.total_coverage_limit == "1850000.00"
    assert wb_summary.total_insured_value == "2400000.00"
    assert wb_summary.total_premium == "138400.00"
    assert len(wb_conflicts) == 1


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_p8_3_schema_refinery_engine_and_bitemporal_ontology_versioning(
    backend: str,
    tmp_path: Path,
) -> None:
    """Insurance & Lending bimodal entity splits pass 3-Tier Assay and promote to v1.2."""
    catalog = InMemoryKnowledgeCatalog()
    catalog.reset_ontology_revisions()
    inbox = InMemoryActionInboxStore()

    if backend == "sqlite":
        db_file = tmp_path / "refinery.sqlite"
        engine = SqlSchemaRefineryEngine(
            f"sqlite+aiosqlite:///{db_file}",
            knowledge_catalog=catalog,
        )
    else:
        engine = InMemorySchemaRefineryEngine(knowledge_catalog=catalog)

    try:
        baseline = engine.get_active_revision()
        assert baseline.revision_id == BASELINE_REVISION_ID

        # 1. Insurance assay: commercial-property-quote -> wind-deductible-endorsement
        ins_prop, ins_report, _ = engine.run_taxonomy_assay(domain="insurance")
        assert ins_prop.target_entity_id == ENTITY_COMMERCIAL_PROPERTY_QUOTE
        assert ins_prop.new_entity_id == ENTITY_WIND_ENDORSEMENT
        assert ins_prop.status == RefineryProposalStatus.ASSAY_PASSED
        assert ins_report.passed is True
        assert ins_report.dag_acyclic is True
        assert ins_report.decimal_formulas_valid is True
        assert ins_report.paired_removal_intact is True
        assert ins_report.negative_flip_rate == 0.0
        # Offline: no live extractor, so perturbation stability is honestly absent.
        assert ins_report.perturbation_evaluated is False
        assert ins_report.perturbation_krippendorff_alpha is None
        assert ins_report.workhorse_teacher_ac2 is None
        assert ins_report.perturbation_items == 0
        assert "perturbation stability: not evaluated (no live extractor)" in (
            ins_report.summary
        )
        assert ins_report.bimodal_jaccard_similarity <= 0.10
        assert ins_report.bimodal_jaccard_distance > 0.50

        promoted_ins = engine.promote_proposal(
            ins_prop.proposal_id,
            revision_id="ont-rev-v1-2-insurance",
            version="1.2.0",
            action_inbox=inbox,
        )
        assert promoted_ins.revision_id == "ont-rev-v1-2-insurance"
        assert promoted_ins.state == OntologyRevisionState.ACTIVE
        assert ENTITY_WIND_ENDORSEMENT in promoted_ins.entities
        assert source_priority_for_doctype(ENTITY_WIND_ENDORSEMENT) == 40

        # 2. Banking/Mortgage assay: trid-closing-disclosure -> fee-schedule-addendum
        bank_prop, bank_report, _ = engine.run_taxonomy_assay(domain="banking")
        assert bank_prop.target_entity_id == ENTITY_TRID_CLOSING_DISCLOSURE
        assert bank_prop.new_entity_id == ENTITY_FEE_SCHEDULE_ADDENDUM
        assert bank_report.passed is True

        promoted_bank = engine.promote_proposal(
            bank_prop.proposal_id,
            revision_id="ont-rev-v1-2-banking",
            version="1.2.1",
            action_inbox=inbox,
        )
        assert promoted_bank.revision_id == "ont-rev-v1-2-banking"
        assert ENTITY_FEE_SCHEDULE_ADDENDUM in promoted_bank.entities
        assert source_priority_for_doctype(ENTITY_FEE_SCHEDULE_ADDENDUM) == 40

        revs = engine.list_revisions()
        assert len(revs) == 3
        assert revs[0].revision_id == BASELINE_REVISION_ID
        assert revs[0].state == OntologyRevisionState.DEPRECATED
        assert revs[0].effective_to is not None
        assert revs[-1].state == OntologyRevisionState.ACTIVE

        # Verify SPLIT_FROM lineage edges exist in active ontology graph
        split_edges = [e for e in promoted_bank.edges if e.relation == "SPLIT_FROM"]
        assert len(split_edges) >= 2
    finally:
        engine.reset()
        if hasattr(engine, "close"):
            engine.close()


class _StableCorpusExtractor:
    """Deterministic extractor: the corpus answer regardless of catalog order."""

    def __init__(self) -> None:
        self._inner = FixtureExtractor()
        self.calls = 0

    async def extract(
        self, artifact_name: str, *, catalog_order: Sequence[str] | None = None
    ) -> ExtractionResult:
        assert catalog_order, "the assay must present a catalog permutation"
        self.calls += 1
        return await self._inner.extract(artifact_name)


def test_p8_3_assay_tier3_evaluates_perturbation_stability_with_injected_extractor() -> (
    None
):
    """With an injected extractor Tier 3 is measured from real runs and gates passed."""
    catalog = InMemoryKnowledgeCatalog()
    catalog.reset_ontology_revisions()
    extractor = _StableCorpusExtractor()
    docs = ("gov-id-clean", "bill-powerco-clean")
    engine = InMemorySchemaRefineryEngine(
        knowledge_catalog=catalog, extractor=extractor, sample_documents=docs
    )
    _, report, _ = engine.run_taxonomy_assay(domain="insurance")
    assert report.perturbation_evaluated is True
    assert report.perturbation_permutations == 3
    assert report.perturbation_items >= len(docs) * 2
    assert extractor.calls == len(docs) * 3
    assert report.perturbation_krippendorff_alpha is not None
    assert report.perturbation_krippendorff_alpha >= 0.80
    assert report.workhorse_teacher_ac2 is not None
    assert report.workhorse_teacher_ac2 >= 0.80
    assert report.passed is True
    assert "perturbation stability: alpha=1.00" in report.summary


def test_p8_3_auxiliary_detectors_and_dag_cycle_guard() -> None:
    """Verify DAG cycle rejection and auxiliary taxonomy detectors."""
    cyclic_edges = (
        OntologyEdge(
            edge_id="e1",
            source_id="entity-a",
            relation="SUPERSEDES",
            target_id="entity-b",
        ),
        OntologyEdge(
            edge_id="e2",
            source_id="entity-b",
            relation="SUPERSEDES",
            target_id="entity-a",
        ),
    )
    assert verify_supersedes_dag_acyclic(cyclic_edges) is False

    escape_prop = detect_out_of_ontology_escape_cluster(
        observed_slug="rent-roll", sample_count=8
    )
    assert escape_prop.new_entity_id == "rent-roll"

    merge_prop = detect_colliding_entities(
        source_entity_id="binder-slip",
        target_entity_id="commercial-property-quote",
    )
    assert any(e.relation == "MERGED_INTO" for e in merge_prop.added_edges)

    card_prop = detect_chronic_absent_field_cardinality(
        target_entity_id="commercial-property-quote",
        field_name="total_insured_value",
    )
    assert card_prop.target_field_name == "total_insured_value"


def test_p8_4_refinery_fastapi_routes_and_insurance_first_demo_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify /v1/refinery/* endpoints and /demo?domain=insurance + BRIDGE_DEFAULT_DEMO_DOMAIN."""
    app = build_user_interaction_app()
    client = TestClient(app)

    # Reset refinery to baseline
    r_reset = client.post("/v1/refinery/reset")
    assert r_reset.status_code == 200
    assert r_reset.json()["active_revision"]["revision_id"] == BASELINE_REVISION_ID

    # Run assay via API
    r_assay = client.post("/v1/refinery/assay", json={"domain": "insurance"})
    assert r_assay.status_code == 200
    assay_payload = r_assay.json()
    proposal_id = assay_payload["proposal"]["proposal_id"]
    assert assay_payload["assay_report"]["passed"] is True
    assert assay_payload["assay_report"]["negative_flip_rate"] == 0.0
    assert assay_payload["assay_report"]["perturbation_evaluated"] is False
    assert assay_payload["assay_report"]["perturbation_krippendorff_alpha"] is None
    assert assay_payload["assay_report"]["workhorse_teacher_ac2"] is None

    # Promote proposal via API
    r_promote = client.post(
        "/v1/refinery/promote",
        json={"proposal_id": proposal_id, "version": "1.2.0"},
    )
    assert r_promote.status_code == 200
    promoted_rev = r_promote.json()["promoted_revision"]
    assert promoted_rev["state"] == "ACTIVE"

    # List revisions via API
    r_revs = client.get("/v1/refinery/revisions?relation=SPLIT_FROM")
    assert r_revs.status_code == 200
    assert len(r_revs.json()["edges"]) >= 1

    # Verify /demo?domain=insurance and BRIDGE_DEFAULT_DEMO_DOMAIN=insurance
    r_demo_query = client.get("/demo?domain=insurance")
    assert r_demo_query.status_code == 200
    assert 'window.DEFAULT_DEMO_DOMAIN = "insurance";' in r_demo_query.text

    monkeypatch.setenv("BRIDGE_DEFAULT_DEMO_DOMAIN", "insurance")
    r_demo_env = client.get("/demo")
    assert r_demo_env.status_code == 200
    assert 'window.DEFAULT_DEMO_DOMAIN = "insurance";' in r_demo_env.text

    # Clean up back to baseline
    client.post("/v1/refinery/reset")


def test_p8_4_live_schema_evolution_4_beat_collab_stage() -> None:
    """Verify the 4-beat Live Schema Evolution & Bi-Temporal Ontology Graph demo stage."""
    app = build_user_interaction_app()
    client = TestClient(app)

    # Reset both refinery and schema evolution stage
    r_reset = client.post("/v1/demo/schema-evolution/reset")
    assert r_reset.status_code == 200
    beat0 = r_reset.json()
    assert beat0["beat"] == 0
    assert beat0["active_ontology_revision"] == BASELINE_REVISION_ID
    assert beat0["entity_count"] == 8

    # Beat 1: Carrier uploads 1-page Schedule B Coastal Wind Deductible Endorsement
    r_b1 = client.post("/v1/demo/schema-evolution/advance", json={"beat": 1})
    assert r_b1.status_code == 200
    beat1 = r_b1.json()
    assert beat1["beat"] == 1
    assert beat1["agent2_status"]["jaccard_similarity"] == 0.0667
    assert beat1["placement_checklist"][-1]["status"] == "quarantined"

    # Beat 2: Run Taxonomy Assay & 3-Tier Evaluation Gate
    r_b2 = client.post("/v1/demo/schema-evolution/advance", json={"beat": 2})
    assert r_b2.status_code == 200
    beat2 = r_b2.json()
    assert beat2["beat"] == 2
    assert beat2["proposal"]["new_entity_id"] == ENTITY_WIND_ENDORSEMENT
    assert beat2["assay_report"]["passed"] is True
    assert beat2["assay_report"]["negative_flip_rate"] == 0.0
    assert len(beat2["proposal"]["added_edges"]) == 3

    # Beat 3: Promote Ontology v1.2.0 & Reconcile $100k Endorsement
    r_b3 = client.post("/v1/demo/schema-evolution/advance", json={"beat": 3})
    assert r_b3.status_code == 200
    beat3 = r_b3.json()
    assert beat3["beat"] == 3
    assert beat3["active_ontology_revision"] == "ont-rev-v1-2-insurance"
    assert beat3["entity_count"] == 9
    assert beat3["done"] is True
    assert beat3["financial_summary"]["coastal_wind_deductible"] == "$100,000.00"
    assert (
        "wind-deductible-endorsement"
        in beat3["financial_summary"]["deductible_authority"]
    )

    # Beat 4: Verify Day-2 Payoff on Next Placement (Harborview Marine Terminal LLC)
    r_b4 = client.post("/v1/demo/schema-evolution/advance", json={"beat": 4})
    assert r_b4.status_code == 200
    beat4 = r_b4.json()
    assert beat4["beat"] == 4
    assert beat4["day2_payoff"]["day2_entity_id"] == "PLACEMENT-HARBORVIEW-DAY2"
    assert beat4["day2_payoff"]["classified_doctype"] == ENTITY_WIND_ENDORSEMENT
    assert beat4["day2_payoff"]["false_positive_quarantine_parks"] == 0

    # Verify /demo HTML exposes the Schema Evolution stage DOM hooks
    r_html = client.get("/demo?domain=insurance")
    assert r_html.status_code == 200
    for dom_id in (
        "schema-evolution-stage",
        "btn-schema-evol-beat-1",
        "btn-schema-evol-beat-2",
        "btn-schema-evol-beat-3",
        "btn-schema-evol-beat-4",
        "schema-evol-checklist",
        "schema-evol-edges-list",
        "schema-evol-refinery-box",
    ):
        assert dom_id in r_html.text

    # Clean up back to baseline
    client.post("/v1/demo/schema-evolution/reset")


def test_p8_4_benefits_hdhp_hsa_vs_fsa_4_beat_schema_evolution() -> None:
    """Verify Employee Benefits (HDHP · HSA vs. FSA & $125K Rate Addendum) 4-beat evolution."""
    app = build_user_interaction_app()
    client = TestClient(app)

    # Beat 0: Reset to Benefits scenario
    r_reset = client.post("/v1/demo/schema-evolution/reset?domain=benefits")
    assert r_reset.status_code == 200
    beat0 = r_reset.json()
    assert beat0["scenario_domain"] == "benefits"
    assert beat0["entity_id"] == "BENEFITS-APEX-HDHP-2027"
    assert beat0["beat"] == 0
    assert beat0["active_ontology_revision"] == BASELINE_REVISION_ID

    # Beat 1: Upload Page 4 HDHP Schedule ('Employer Account Seed: $1,000') & 2-Page $125K Rate Addendum
    r_b1 = client.post(
        "/v1/demo/schema-evolution/advance",
        json={"beat": 1, "domain": "benefits"},
    )
    assert r_b1.status_code == 200
    beat1 = r_b1.json()
    assert beat1["beat"] == 1
    assert "FSA: $1,000.00" in beat1["financial_summary"]["coastal_wind_deductible"]
    assert "can ANY HDHP" in beat1["socratic_challenge"]["step2_system_question"]
    assert beat1["agent2_status"]["jaccard_similarity"] == 0.0714

    # Beat 2: Challenge One-Off Fix ('FSA' -> 'HSA') & Run 3-Tier Assay
    r_b2 = client.post(
        "/v1/demo/schema-evolution/advance",
        json={"beat": 2, "domain": "benefits"},
    )
    assert r_b2.status_code == 200
    beat2 = r_b2.json()
    assert beat2["beat"] == 2
    assert beat2["proposal"]["target_entity_id"] == ENTITY_BENEFITS_QUOTE
    assert beat2["proposal"]["new_entity_id"] == ENTITY_RATE_GUARANTEE_ADDENDUM
    assert beat2["assay_report"]["passed"] is True
    assert beat2["assay_report"]["negative_flip_rate"] == 0.0
    relations = {e["relation"] for e in beat2["proposal"]["added_edges"]}
    assert {"SUPERSEDES", "SPLIT_FROM", "FEEDS_METRIC", "INCLUDES", "EXCLUDES"} <= (
        relations
    )

    # Beat 3: Promote Ontology v1.2.0 ('HSA: $1,000.00' ✓ + $125,000.00 Rate Addendum Wins)
    r_b3 = client.post(
        "/v1/demo/schema-evolution/advance",
        json={"beat": 3, "domain": "benefits"},
    )
    assert r_b3.status_code == 200
    beat3 = r_b3.json()
    assert beat3["beat"] == 3
    assert beat3["done"] is True
    assert beat3["active_ontology_revision"] == "ont-rev-v1-2-benefits"
    assert "HSA: $1,000.00 ✓" in beat3["financial_summary"]["coastal_wind_deductible"]
    assert "$125,000.00" in beat3["financial_summary"]["primary_plus_excess_limit"]

    # Beat 4: Day-2 Payoff on Next HDHP Renewal (NorthStar Precision Systems)
    r_b4 = client.post(
        "/v1/demo/schema-evolution/advance",
        json={"beat": 4, "domain": "benefits"},
    )
    assert r_b4.status_code == 200
    beat4 = r_b4.json()
    assert beat4["beat"] == 4
    assert beat4["day2_payoff"]["day2_entity_id"] == "BENEFITS-NORTHSTAR-DAY2"
    assert beat4["day2_payoff"]["classified_doctype"] == ENTITY_RATE_GUARANTEE_ADDENDUM
    assert "HSA: $1,500.00 ✓" in beat4["day2_payoff"]["resolved_deductible"]
    assert beat4["day2_payoff"]["false_positive_quarantine_parks"] == 0

    # Clean up back to baseline
    client.post("/v1/demo/schema-evolution/reset")
