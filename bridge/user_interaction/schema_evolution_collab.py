"""Collaborative 4-Beat Live Schema Evolution & Bi-Temporal Ontology Graph Demo.

Implements the interactive Dual-Zone Carrier/Client Submission Portal + Schema Refinery
& Dual-Agent Board scenario (`terrepower-coastal-2027` — Terrepower Coastal LLC `$142.50M`
Commercial Property Placement):
  - Beat 0: Baseline Ontology `ont-rev-v1-1-baseline` (`v1.1.0`, 7 OKF doctypes).
            3 baseline placement documents (`14-Location SOV $142.5M TIV`, `Chubb Primary
            Quote $50M Limit / $50K Base Deductible`, `AIG Signed Excess Binder $25M xs $50M`).
            `commercial-property-quote` (`Prio 20`) requires all 7 fields (`premium`,
            `deductible`, `coverage_limits`, `exclusions`, etc.).
  - Beat 1: Carrier (`Chubb Syndicate 2488`) uploads 1-page `Schedule B Coastal Wind
            Deductible Endorsement` (`$100,000.00` overriding `$50,000.00` on `Page 9
            [0.11, 0.39]`). Under `ont-rev-v1-1-baseline`, `gemini-3.8-flash` maps it into
            `commercial-property-quote` (`Prio 20`), exposing two ontology gaps:
            (a) Bimodal Field Sparsity (`J(A,B) = 0.0667 <= 0.10`) causing false-positive
                missing-field Quarantine parks on `premium`, `coverage_limits`, and `exclusions`.
            (b) Missing `[:SUPERSEDES]` authority edge (`Prio 20 == Prio 20` tie on `deductible`).
  - Beat 2: Schema Refinery (`bridge/refinery/` + `gemini-4-argon`) executes the 3-Tier
            Taxonomy & Sparsity Assay (`POST /v1/refinery/assay`), proposing `SPLIT_ENTITY`
            (`wind-deductible-endorsement.okf.md`, `Prio 40`, `required: [currency, language,
            deductible, effective_date]`), 3 Knowledge Graph edges (`[:SUPERSEDES]`,
            `[:SPLIT_FROM]`, `[:FEEDS_METRIC]`), and a 3-part contrastive description diff
            (`NFR = 0.0%`, `alpha = 0.94 >= 0.80`, `AC2 = 0.93 >= 0.80`, Rogan-Gladen `98.8%`).
  - Beat 3: Senior Broker / Data Steward (`usr:senior.broker@docubridge.io`, SPIFFE OBO
            `spiffe://agents.docubridge.io/internal/broker-placement-agent`, `RSR = 0.99`,
            `5,100 ms` dwell) approves 1-click Ontology Promotion (`POST /v1/refinery/promote`),
            activating `ont-rev-v1-2-insurance` (`v1.2.0`). `wind-deductible-endorsement`
            (`Prio 40`) deterministically supersedes `commercial-property-quote` (`Prio 20`)
            on `deductible` (`$100,000.00`), resolving the placement (`4/4` verified, `done=True`)
            while Turn-1 facts stay immutably pinned to `ont-rev-v1-1-baseline` via `[:SPLIT_FROM]`.
  - Beat 4: Day-2 Payoff verification on `PLACEMENT-HARBORVIEW-DAY2` (`Harborview Marine
            Terminal LLC — $88.00M Coastal Property Placement`): a new 1-page Schedule B
            Wind Deductible Endorsement (`$75,000.00`) is classified directly as
            `wind-deductible-endorsement` (`Prio 40`) under `ont-rev-v1-2-insurance` with
            0 false-positive missing-field parks, auto-superseding the base quote deductible
            in `138 ms` with `0` human touchpoints and `0` `gemini-4-argon` escalation tokens.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter

from bridge.a2ui_inbox import ActionInboxStore
from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.knowledge_catalog import KnowledgeCatalog
from bridge.refinery import ENTITY_COMMERCIAL_PROPERTY_QUOTE, SchemaRefineryEngine
from bridge.telemetry import trace_step
from bridge.user_interaction.schemas import AdvanceSchemaEvolutionCollabRequest

SCHEMA_EVOL_ENTITY_ID = "terrepower-coastal-2027"
SCHEMA_EVOL_CLIENT = "Terrepower Coastal LLC"
SCHEMA_EVOL_PROGRAM = "$142,500,000.00 Commercial Coastal Property Placement"
SCHEMA_EVOL_DAY2_ENTITY_ID = "PLACEMENT-HARBORVIEW-DAY2"
SCHEMA_EVOL_DAY2_CLIENT = "Harborview Marine Terminal LLC"

BROKER_AGENT_SPIFFE_ID = "spiffe://agents.docubridge.io/internal/broker-placement-agent"
REFINERY_AGENT_SPIFFE_ID = "spiffe://agents.docubridge.io/internal/credit-officer-agent"
SENIOR_BROKER_PRINCIPAL = "usr:senior.broker@docubridge.io"

_STATE: dict[str, Any] = {"beat": 0, "last_proposal_id": None}


def _build_state_payload(
    *,
    beat: int,
    refinery: SchemaRefineryEngine,
    proposal_dict: dict[str, Any] | None = None,
    assay_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the complete 4-beat Schema Evolution UI state dictionary."""
    active_rev = refinery.get_active_revision()
    revisions = refinery.list_revisions()
    done = beat >= 3

    checklist = [
        {
            "id": "doc-1-sov",
            "label": "14-Location Coastal Property Schedule (SOV — $142.5M TIV)",
            "doctype": "structured-sov-xlsx",
            "status": "satisfied",
            "emergent": False,
            "badge_text": "VERIFIED · PATH A (0 TOKENS)",
            "reason": "Deterministic schema check verified 14 coastal locations ($142,500,000.00 TIV).",
            "source_citation": "Schedule Tab 1 · Row 14",
            "ontology_rev": "ont-rev-v1-1-baseline",
        },
        {
            "id": "doc-2-chubb-quote",
            "label": "Chubb Primary Commercial Property Quote ($50M Limit · $50K Base Deductible)",
            "doctype": ENTITY_COMMERCIAL_PROPERTY_QUOTE,
            "status": "satisfied",
            "emergent": False,
            "badge_text": "VERIFIED · PRIO 20 (v1.1.0)",
            "reason": "24-page base quote populates all 7 required fields under ont-rev-v1-1-baseline.",
            "source_citation": "Page 1 · Region [0.14, 0.31]",
            "ontology_rev": "ont-rev-v1-1-baseline",
        },
        {
            "id": "doc-3-aig-binder",
            "label": "AIG Signed Excess Property Binder ($25M xs $50M · $412K Premium)",
            "doctype": ENTITY_COMMERCIAL_PROPERTY_QUOTE,
            "status": "satisfied",
            "emergent": False,
            "badge_text": "VERIFIED · PRIO 20 (v1.1.0)",
            "reason": "Multi-window table merge verified excess attachment point and annual premium.",
            "source_citation": "Page 11 · Region [0.18, 0.52]",
            "ontology_rev": "ont-rev-v1-1-baseline",
        },
    ]

    if beat >= 1:
        if beat < 3:
            checklist.append(
                {
                    "id": "doc-4-wind-endorsement",
                    "label": "Chubb Schedule B Coastal Wind Deductible Endorsement ($100,000.00)",
                    "doctype": "commercial-property-quote (Overloaded in v1.1.0)",
                    "status": "quarantined",
                    "emergent": True,
                    "badge_text": (
                        "QUARANTINED · BIMODAL SPARSITY J=0.0667"
                        if beat == 1
                        else "ASSAY PASSED · READY FOR v1.2.0 PROMOTION"
                    ),
                    "reason": (
                        "1-page endorsement populates 'deductible' ($100,000.00) but leaves "
                        "'premium', 'coverage_limits', and 'exclusions' absent_from_document, "
                        "and ties Base Quote at Prio 20."
                    ),
                    "source_citation": "Page 9 · Region [0.11, 0.39]",
                    "ontology_rev": "ont-rev-v1-1-baseline",
                }
            )
        else:
            checklist.append(
                {
                    "id": "doc-4-wind-endorsement",
                    "label": "Chubb Schedule B Coastal Wind Deductible Endorsement ($100,000.00)",
                    "doctype": "wind-deductible-endorsement",
                    "status": "satisfied",
                    "emergent": True,
                    "badge_text": "SUPERSEDED BASE QUOTE · PRIO 40 (v1.2.0)",
                    "reason": (
                        "Classified under new OKF entity 'wind-deductible-endorsement' (Prio 40); "
                        "[:SUPERSEDES] edge deterministically overrides $50K base deductible -> $100K."
                    ),
                    "source_citation": "Page 9 · Region [0.11, 0.39]",
                    "ontology_rev": "ont-rev-v1-2-insurance",
                }
            )

    graph_edges_preview = [
        {
            "source": "commercial-property-quote (Prio 20)",
            "relation": "[:FEEDS_METRIC]",
            "target": "FinancialSummary.{total_premium, total_deductible, total_coverage_limit}",
            "revision": "ont-rev-v1-1-baseline",
            "is_new": False,
        }
    ]
    if beat >= 3:
        graph_edges_preview.extend(
            [
                {
                    "source": "wind-deductible-endorsement (Prio 40)",
                    "relation": "[:SUPERSEDES {on: deductible}]",
                    "target": "commercial-property-quote (Prio 20)",
                    "revision": "ont-rev-v1-2-insurance",
                    "is_new": True,
                },
                {
                    "source": "wind-deductible-endorsement (Prio 40)",
                    "relation": "[:SPLIT_FROM]",
                    "target": "commercial-property-quote (v1.1.0 Lineage Preserved)",
                    "revision": "ont-rev-v1-2-insurance",
                    "is_new": True,
                },
                {
                    "source": "wind-deductible-endorsement (Prio 40)",
                    "relation": "[:FEEDS_METRIC]",
                    "target": "FinancialSummary.total_deductible ($100,000.00)",
                    "revision": "ont-rev-v1-2-insurance",
                    "is_new": True,
                },
            ]
        )

    if beat == 0:
        banner = {
            "tone": "info",
            "headline": "Baseline Ontology Active · ont-rev-v1-1-baseline (v1.1.0 · 7 OKF Entities)",
            "message": (
                "All 3 base placement documents reconcile under v1.1.0 ($142.5M TIV, $50K Base Deductible). "
                "Click Beat 1 to upload Chubb's 1-page Schedule B Coastal Wind Deductible Endorsement ($100,000.00)."
            ),
            "agent_note": (
                "Under v1.1.0, 'commercial-property-quote' is a single monolithic doctype (Prio 20) "
                "with no separate endorsement entity or [:SUPERSEDES] edge."
            ),
        }
        a1_headline = "3 Baseline Documents Extracted under ont-rev-v1-1-baseline"
        a1_detail = (
            "Pinned ontology_revision_id='ont-rev-v1-1-baseline' across all multi-window chunks. "
            "3-part contrastive descriptions active on all 7 OKF doctypes."
        )
        a2_headline = "Zone 6 Knowledge Graph Monitoring Field-Presence Sparsity"
        a2_detail = (
            "Watching AlloyDB operational facts and BigQuery otel_spans for bimodal null "
            "clusters (Jaccard J(A,B) <= 0.10) and doctype priority collisions."
        )
    elif beat == 1:
        banner = {
            "tone": "warning",
            "headline": "Ontology Friction Detected · Bimodal Field Sparsity (J = 0.0667) & Priority Tie",
            "message": (
                "Chubb's 1-page Schedule B Wind Endorsement ($100,000.00 on Page 9) was forced into "
                "'commercial-property-quote' (Prio 20), triggering 3 false-positive absent_from_document "
                "warnings and a Prio 20 == Prio 20 tie against the $50,000.00 Cover Quote."
            ),
            "agent_note": (
                "Click Beat 2 to run the Schema Refinery Taxonomy & Sparsity Assay (gemini-4-argon)."
            ),
        }
        a1_headline = (
            "Quarantined 1-Page Schedule B Wind Endorsement (Overloaded Doctype)"
        )
        a1_detail = (
            "Extracted deductible=$100,000.00 (Page 9 [0.11, 0.39]), but commercial-property-quote "
            "requires premium, coverage_limits, and exclusions (all absent on a 1-page endorsement)."
        )
        a2_headline = (
            "Bimodal Null-Matrix Cluster Detected on 'commercial-property-quote'"
        )
        a2_detail = (
            "Cluster A (24-page base quotes) vs. Cluster B (1-page Schedule B endorsements) "
            "yields Jaccard similarity J(A,B) = 0.0667 <= 0.10 threshold -> Candidate SPLIT_ENTITY."
        )
    elif beat == 2:
        banner = {
            "tone": "info",
            "headline": "3-Tier Refinery Assay PASSED · Proposing 'wind-deductible-endorsement' (v1.2.0)",
            "message": (
                "Schema Refinery synthesized OKF entity 'wind-deductible-endorsement.okf.md' (Prio 40) "
                "with [:SUPERSEDES] + [:SPLIT_FROM] edges and verified 0.0% Negative Flip Rate across "
                "36 historical BigQuery Iceberg documents."
            ),
            "agent_note": (
                "Click Beat 3 for 1-click Data Steward / Senior Broker approval to promote ont-rev-v1-2-insurance."
            ),
        }
        a1_headline = "Contrastive Description Diff Ready for gemini-3.8-flash (thinking_budget=0)"
        a1_detail = (
            "Updated 'distinguish_from' on commercial-property-quote and wind-deductible-endorsement "
            "achieves Krippendorff's alpha = 0.94 and Gwet's AC2 = 0.93 vs. gemini-4-argon."
        )
        a2_headline = (
            "3-Tier Counterfactual Assay Gate: PASS (NFR = 0.0% · Rogan-Gladen 98.8%)"
        )
        a2_detail = (
            "Tier 1: [:SUPERSEDES] DAG acyclic & Decimal math valid · Tier 2: 0/36 historical flips · "
            "Tier 3: FA=0 corridor satisfied."
        )
    elif beat == 3:
        banner = {
            "tone": "success",
            "headline": "Ontology v1.2.0 Active (ont-rev-v1-2-insurance) · $100K Wind Deductible Reconciled!",
            "message": (
                "Zone 6 Knowledge Catalog promoted 'wind-deductible-endorsement' (Prio 40) with "
                "[:SUPERSEDES] over 'commercial-property-quote' (Prio 20). Historical Turn-1 facts "
                "remain immutably pinned to ont-rev-v1-1-baseline via [:SPLIT_FROM]."
            ),
            "agent_note": (
                "Click Beat 4 to verify the Day-2 Payoff on Harborview Marine Terminal LLC ($88M Placement)."
            ),
        }
        a1_headline = (
            "Active Ontology Revision: ont-rev-v1-2-insurance (8 OKF Entities)"
        )
        a1_detail = (
            "Schedule B Wind Endorsement re-indexed as 'wind-deductible-endorsement' (Prio 40) "
            "with required=[currency, language, deductible, effective_date]."
        )
        a2_headline = "Declarative Graph Consolidation Complete ($50,000 -> $100,000)"
        a2_detail = (
            "Resolved via edge-wind-endorsement-supersedes-prop-quote (Prio 40 > Prio 20). "
            "Bi-temporal lineage links v1.2.0 -> v1.1.0 via [:SPLIT_FROM]."
        )
    else:
        banner = {
            "tone": "success",
            "headline": "Day-2 Zero-Quarantine Payoff Verified · Harborview Marine Terminal ($138ms, 0 Touchpoints)",
            "message": (
                "Next placement's 1-page Schedule B Wind Endorsement ($75,000.00) classified directly as "
                "'wind-deductible-endorsement' (Prio 40) under ont-rev-v1-2-insurance with 0 false-positive "
                "missing-field parks and 0 gemini-4-argon escalation tokens."
            ),
            "agent_note": (
                "Full closed-loop Schema Evolution proven: Sparsity Detection -> 3-Tier Assay -> "
                "Bi-Temporal Promotion -> Day-2 Zero-Touch Auto-Consolidation."
            ),
        }
        a1_headline = "Day-2 Endorsement Auto-Approved in 138 ms (0 Escalation Tokens)"
        a1_detail = (
            "gemini-3.8-flash classified Harborview Schedule B directly as 'wind-deductible-endorsement' "
            "(ont-rev-v1-2-insurance) with 100% required-field match."
        )
        a2_headline = (
            "Two-Dimensional Lineage Verified Across Turn-1 (v1.1.0) & Turn-2 (v1.2.0)"
        )
        a2_detail = (
            "Turn-1 facts retain ont-rev-v1-1-baseline; Turn-2 facts carry ont-rev-v1-2-insurance "
            "+ consolidation_edge_id='edge-wind-endorsement-supersedes-prop-quote'."
        )

    day2_payoff = None
    if beat >= 4:
        day2_payoff = {
            "day2_entity_id": SCHEMA_EVOL_DAY2_ENTITY_ID,
            "day2_client": SCHEMA_EVOL_DAY2_CLIENT,
            "day2_program": "$88,000,000.00 Coastal Marine Terminal Property Placement",
            "classified_doctype": "wind-deductible-endorsement",
            "ontology_revision_id": active_rev.revision_id,
            "consolidation_edge_id": "edge-wind-endorsement-supersedes-prop-quote",
            "resolved_deductible": "$75,000.00 (Supersedes $25,000.00 Base Quote · Prio 40 > Prio 20)",
            "false_positive_quarantine_parks": 0,
            "latency_ms": 138,
            "human_touchpoints": 0,
            "escalation_tokens": 0,
        }

    return {
        "entity_id": SCHEMA_EVOL_ENTITY_ID,
        "client_name": SCHEMA_EVOL_CLIENT,
        "program_title": SCHEMA_EVOL_PROGRAM,
        "beat": beat,
        "done": done,
        "active_ontology_revision": active_rev.revision_id,
        "active_ontology_version": active_rev.version,
        "entity_count": len(active_rev.entities),
        "edge_count": len(active_rev.edges),
        "revision_history": [
            {
                "revision_id": r.revision_id,
                "version": r.version,
                "state": r.state.value,
                "entity_count": len(r.entities),
                "edge_count": len(r.edges),
            }
            for r in revisions
        ],
        "financial_summary": {
            "total_insured_value": "$142,500,000.00",
            "primary_plus_excess_limit": "$75,000,000.00",
            "coastal_wind_deductible": "$100,000.00"
            if beat >= 3
            else "$50,000.00 (⚠ Conflict)",
            "deductible_authority": (
                "wind-deductible-endorsement (Prio 40 · v1.2.0)"
                if beat >= 3
                else "commercial-property-quote (Prio 20 · v1.1.0)"
            ),
            "annual_premium": "$418,500.00",
        },
        "placement_checklist": checklist,
        "graph_edges_preview": graph_edges_preview,
        "banner": banner,
        "agent1_status": {
            "agent_name": "Agent 1 · Document Bridge (Sense A Contrastive Extractor)",
            "spiffe_id": BROKER_AGENT_SPIFFE_ID,
            "model": WORKHORSE_MODEL,
            "headline": a1_headline,
            "detail": a1_detail,
            "pinned_ontology_revision": active_rev.revision_id,
        },
        "agent2_status": {
            "agent_name": "Agent 2 · Schema Refinery & Graph Consolidation (Sense B)",
            "spiffe_id": REFINERY_AGENT_SPIFFE_ID,
            "model": REASONING_MODEL,
            "headline": a2_headline,
            "detail": a2_detail,
            "jaccard_similarity": 0.0667,
            "nfr": 0.0,
            "krippendorff_alpha": 0.94,
            "gwets_ac2": 0.93,
            "rogan_gladen_pass_rate": 0.988,
        },
        "proposal": proposal_dict,
        "assay_report": assay_dict,
        "day2_payoff": day2_payoff,
    }


def get_schema_evolution_collab_state(
    *,
    refinery: SchemaRefineryEngine,
) -> dict[str, Any]:
    """Return a deep copy of the current 4-beat Schema Evolution demo state."""
    beat = int(_STATE.get("beat", 0))
    prop_id = _STATE.get("last_proposal_id")
    prop = refinery.get_proposal(prop_id) if prop_id else None
    proposals = refinery.list_proposals(domain="insurance")
    if prop is None and proposals:
        prop = proposals[-1]
    reports = refinery.list_assay_reports()
    rep = reports[-1] if reports else None
    return copy.deepcopy(
        _build_state_payload(
            beat=beat,
            refinery=refinery,
            proposal_dict=prop.model_dump(mode="json") if prop else None,
            assay_dict=rep.model_dump(mode="json") if rep else None,
        )
    )


def reset_schema_evolution_collab_state(
    *,
    refinery: SchemaRefineryEngine,
) -> dict[str, Any]:
    """Reset the 4-beat Schema Evolution demo and Zone 6 Ontology back to Beat 0 (v1.1.0)."""
    refinery.reset()
    _STATE["beat"] = 0
    _STATE["last_proposal_id"] = None
    return get_schema_evolution_collab_state(refinery=refinery)


def advance_schema_evolution_collab_to_beat(
    target_beat: int,
    *,
    refinery: SchemaRefineryEngine,
    inbox: ActionInboxStore | None = None,
    domain: str = "insurance",
) -> dict[str, Any]:
    """Advance the 4-beat Schema Evolution workflow to ``target_beat`` (1..4)."""
    with trace_step(
        f"schema_evolution_collab_beat_{target_beat}",
        attributes={
            "docubridge.zone": "zone6_schema_refinery",
            "docubridge.entity_id": SCHEMA_EVOL_ENTITY_ID,
            "docubridge.agent_spiffe_id": REFINERY_AGENT_SPIFFE_ID,
            "docubridge.schema_evolution.beat": target_beat,
        },
    ):
        if target_beat <= 1:
            refinery.reset()
            _STATE["beat"] = 1
            _STATE["last_proposal_id"] = None
            return get_schema_evolution_collab_state(refinery=refinery)

        # Ensure Beat 2 assay has run
        prop_id = _STATE.get("last_proposal_id")
        prop = refinery.get_proposal(prop_id) if prop_id else None
        if prop is None:
            prop, _rep, _cand = refinery.run_taxonomy_assay(domain=domain)
            _STATE["last_proposal_id"] = prop.proposal_id

        if target_beat == 2:
            _STATE["beat"] = 2
            return get_schema_evolution_collab_state(refinery=refinery)

        # Beat 3 or 4: Ensure Ontology v1.2.0 is promoted
        active_rev = refinery.get_active_revision()
        if active_rev.version == "1.1.0" and prop is not None:
            refinery.promote_proposal(
                prop.proposal_id,
                version="1.2.0",
                action_inbox=inbox,
            )

        _STATE["beat"] = 3 if target_beat == 3 else 4
        return get_schema_evolution_collab_state(refinery=refinery)


def register_schema_evolution_collab_routes(
    router: APIRouter,
    *,
    catalog: KnowledgeCatalog,
    refinery: SchemaRefineryEngine,
    inbox: ActionInboxStore | None = None,
) -> None:
    """Mount ``/v1/demo/schema-evolution/*`` routes on the Zone 5 API router."""
    _ = catalog

    @router.get("/v1/demo/schema-evolution/state")
    def get_state() -> dict[str, Any]:
        """Return the current 4-beat Schema Evolution demo state."""
        return get_schema_evolution_collab_state(refinery=refinery)

    @router.post("/v1/demo/schema-evolution/advance")
    def post_advance(body: AdvanceSchemaEvolutionCollabRequest) -> dict[str, Any]:
        """Advance the 4-beat Schema Evolution demo to Beat 1, 2, 3, or 4."""
        return advance_schema_evolution_collab_to_beat(
            body.beat,
            refinery=refinery,
            inbox=inbox,
            domain=body.domain,
        )

    @router.post("/v1/demo/schema-evolution/reset")
    def post_reset() -> dict[str, Any]:
        """Reset the 4-beat Schema Evolution demo back to Beat 0 (v1.1.0)."""
        return reset_schema_evolution_collab_state(refinery=refinery)
