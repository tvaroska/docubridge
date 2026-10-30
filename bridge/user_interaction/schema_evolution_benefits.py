"""Employee Benefits (HDHP · HSA vs. FSA & $125K Rate Addendum) 4-Beat State Builder.

Implements the interactive 4-beat Live Schema Evolution & Knowledge Graph scenario
for Employee Health & Benefits (`BENEFITS-APEX-HDHP-2027` -> `BENEFITS-NORTHSTAR-DAY2`):
- 7-Node Knowledge Graph where rules live on the arrows:
  - `Signed Rate Addendum ($125K)` --[:SUPERSEDES]--> `Base Quote PDF ($140K)` --[:SUPERSEDES]--> `Manual Workbook Entry ($145K)`
  - `HSA ($1,000 Seed)` --[:INCLUDES (Part Of)]--> `HDHP Plan`
  - `FSA` --[:EXCLUDES (✕ NOT Part Of)]--> `HDHP Plan`
- Graph in Action on Page 4 (`"Plan: HDHP · Employer Account Seed: $1,000.00"`):
  - Prompt alone (`~60%`) guesses `"FSA: $1,000.00" ✕`
  - Knowledge Graph (`95%+`) rules out `FSA` and extracts `"HSA: $1,000.00" ✓`
- Continuous Learning Socratic Challenge:
  - Broker fixes `"FSA"` -> `"HSA"` on Page 4
  - System challenges: `"Is this only for this quote — or can ANY HDHP plan never have a standard FSA?"`
  - Distills general graph edges `[:INCLUDES]` / `[:EXCLUDES]` + splits `rate-guarantee-addendum` (`Prio 40`) with `0.0% Backsliding` (`NFR = 0.0%`).
"""

from __future__ import annotations

from typing import Any

from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.refinery import (
    ENTITY_BENEFITS_QUOTE,
    ENTITY_RATE_GUARANTEE_ADDENDUM,
    SchemaRefineryEngine,
)

BENEFITS_EVOL_ENTITY_ID = "BENEFITS-APEX-HDHP-2027"
BENEFITS_EVOL_CLIENT = "Apex Global Manufacturing"
BENEFITS_EVOL_PROGRAM = "2027 Employee Health & Benefits Renewal (HDHP + Group Life)"
BENEFITS_EVOL_DAY2_ENTITY_ID = "BENEFITS-NORTHSTAR-DAY2"
BENEFITS_EVOL_DAY2_CLIENT = "NorthStar Precision Systems"

BROKER_AGENT_SPIFFE_ID = "spiffe://agents.docubridge.io/internal/broker-placement-agent"
REFINERY_AGENT_SPIFFE_ID = "spiffe://agents.docubridge.io/internal/credit-officer-agent"


def build_benefits_schema_evolution_payload(
    *,
    beat: int,
    refinery: SchemaRefineryEngine,
    proposal_dict: dict[str, Any] | None = None,
    assay_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the 4-beat Employee Benefits (HDHP · HSA vs. FSA) UI state dictionary."""
    active_rev = refinery.get_active_revision()
    revisions = refinery.list_revisions()
    done = beat >= 3

    checklist: list[dict[str, Any]] = [
        {
            "id": "doc-1-manual-workbook",
            "label": "Manual Benefits Workbook Entry ($145,000.00 Estimated Annual Premium)",
            "doctype": "structured-benefits-workbook",
            "status": "satisfied",
            "emergent": False,
            "badge_text": "OVERRIDDEN BY PDF · PRIO 10 (0 TOKENS)",
            "reason": (
                "Initial manual entry ($145,000.00) superseded by uploaded Carrier Base "
                "Quote PDF ($140,000.00) via source priority rules."
            ),
            "source_citation": "Workbook Tab 2 · Row 8 ($145,000.00)",
            "ontology_rev": "ont-rev-v1-1-baseline",
        },
        {
            "id": "doc-2-base-quote-pdf",
            "label": "150-Page Carrier Base Quote PDF ($140,000.00 · 90-Day Benefit Waiting Period)",
            "doctype": ENTITY_BENEFITS_QUOTE,
            "status": "satisfied",
            "emergent": False,
            "badge_text": "VERIFIED · PRIO 20 (4-PART FIELD GUIDE)",
            "reason": (
                "4-Part Field Guide maps carrier 'Elimination Period' / 'Qualifying Period' -> "
                "90-Day Benefit Waiting Period (Page 14) and ignores 30-Day New-Hire Enrollment Wait (Page 2)."
            ),
            "source_citation": "Page 14 · Region [0.15, 0.42] ($140,000.00 · 90 Days)",
            "ontology_rev": "ont-rev-v1-1-baseline",
        },
    ]

    if beat >= 1:
        if beat < 3:
            checklist.append(
                {
                    "id": "doc-3-hdhp-page4",
                    "label": "Page 4 HDHP Schedule ('Plan: HDHP · Employer Account Seed: $1,000.00')",
                    "doctype": f"{ENTITY_BENEFITS_QUOTE} (Missing HDHP -> HSA/FSA Graph Rule)",
                    "status": "quarantined",
                    "emergent": True,
                    "badge_text": (
                        "PROMPT ALONE (~60%) · GUESSES 'FSA: $1,000.00' ✕"
                        if beat == 1
                        else "SOCRATIC CHALLENGE · 'CAN ANY HDHP EVER HAVE FSA?'"
                    ),
                    "reason": (
                        "Page 4 prints 'Plan: HDHP · Employer Account Seed: $1,000.00' without "
                        "explicitly writing HSA or FSA next to $1,000.00. Prompt alone guesses "
                        "'FSA: $1,000.00' ✕; broker changes 'FSA' -> 'HSA' on Page 4."
                        if beat == 1
                        else "Agent Gym + Schema Refinery challenges the 1-file fix: 'Is this only "
                        "for this quote — or can ANY HDHP plan never have a standard FSA?' "
                        "Compiles broad graph rule: HDHP [:INCLUDES] HSA · HDHP [:EXCLUDES] FSA."
                    ),
                    "source_citation": "Page 4 · Region [0.18, 0.36] ('Plan: HDHP · Employer Account Seed: $1,000')",
                    "ontology_rev": "ont-rev-v1-1-baseline",
                }
            )
            checklist.append(
                {
                    "id": "doc-4-rate-addendum",
                    "label": "2-Page Signed Rate Guarantee Addendum ($125,000.00 Friday Override)",
                    "doctype": f"{ENTITY_BENEFITS_QUOTE} (Overloaded in v1.1.0)",
                    "status": "quarantined",
                    "emergent": True,
                    "badge_text": (
                        "QUARANTINED · BIMODAL SPARSITY J=0.0714"
                        if beat == 1
                        else "ASSAY PASSED · NFR=0.0% (0% BACKSLIDING)"
                    ),
                    "reason": (
                        "2-page signed addendum carries only amended annual premium ($125,000.00); "
                        "forcing it into 150-page benefits-quote triggers 35 false 'missing field' "
                        "alerts and a Prio 20 == Prio 20 tie ($140,000.00 vs. $125,000.00)."
                    ),
                    "source_citation": "Addendum Page 1 · Region [0.12, 0.29] ($125,000.00 Signed)",
                    "ontology_rev": "ont-rev-v1-1-baseline",
                }
            )
        else:
            checklist.append(
                {
                    "id": "doc-3-hdhp-page4",
                    "label": "Page 4 HDHP Schedule ('Plan: HDHP · Employer Account Seed: $1,000.00')",
                    "doctype": f"{ENTITY_BENEFITS_QUOTE} (Governed by [:INCLUDES] / [:EXCLUDES])",
                    "status": "satisfied",
                    "emergent": True,
                    "badge_text": "EXTRACTS 'HSA: $1,000.00' ✓ (KNOWLEDGE GRAPH 95%+)",
                    "reason": (
                        "Knowledge Graph sees Plan == HDHP, follows [:EXCLUDES] ('FSA ✕ NOT Part Of HDHP') "
                        "to rule out FSA, and follows [:INCLUDES] ('HSA ✓ Part Of HDHP') to extract "
                        "'HSA: $1,000.00' with 100% confidence."
                    ),
                    "source_citation": "Page 4 · Region [0.18, 0.36] -> HSA: $1,000.00 ✓",
                    "ontology_rev": active_rev.revision_id,
                }
            )
            checklist.append(
                {
                    "id": "doc-4-rate-addendum",
                    "label": "2-Page Signed Rate Guarantee Addendum ($125,000.00 Friday Override)",
                    "doctype": ENTITY_RATE_GUARANTEE_ADDENDUM,
                    "status": "satisfied",
                    "emergent": True,
                    "badge_text": "SUPERSEDES BASE QUOTE · PRIO 40 ($125,000.00 WINS)",
                    "reason": (
                        "Right-sized 2-page template ('rate-guarantee-addendum', Prio 40) overrides "
                        "Base Quote PDF ($140,000.00, Prio 20) and Manual Entry ($145,000.00, Prio 10) "
                        "and feeds the live Client Renewal Summary across all 8 specialty teams."
                    ),
                    "source_citation": "Addendum Page 1 · Region [0.12, 0.29] ($125,000.00 Active)",
                    "ontology_rev": active_rev.revision_id,
                }
            )

    graph_edges_preview: list[dict[str, Any]] = [
        {
            "edge_id": "edge-base-quote-supersedes-manual",
            "source_id": ENTITY_BENEFITS_QUOTE,
            "relation": "SUPERSEDES",
            "target_id": "structured-benefits-workbook ($145K)",
            "priority_rank": 20,
            "on_fields": ["premium ($140K overrides $145K)"],
            "status": "ACTIVE (v1.1.0)",
        },
        {
            "edge_id": "edge-benefits-quote-feeds-summary",
            "source_id": ENTITY_BENEFITS_QUOTE,
            "relation": "FEEDS_METRIC",
            "target_id": "ClientRenewalSummary (All 8 Teams)",
            "priority_rank": 20,
            "on_fields": ["premium", "deductible", "waiting_period_days"],
            "status": "ACTIVE (v1.1.0)",
        },
    ]
    if beat >= 2:
        edge_state = "ACTIVE (v1.2.0)" if beat >= 3 else "CANDIDATE (ASSAY PASSED)"
        graph_edges_preview.extend(
            [
                {
                    "edge_id": "edge-hdhp-includes-hsa",
                    "source_id": "HSA (Health Savings · $1,000 Seed)",
                    "relation": "INCLUDES (Part Of)",
                    "target_id": "HDHP Plan (High Deductible)",
                    "priority_rank": 40,
                    "on_fields": ["plan_type == HDHP => HSA ✓"],
                    "status": edge_state,
                },
                {
                    "edge_id": "edge-hdhp-excludes-fsa",
                    "source_id": "FSA (Flexible Spending)",
                    "relation": "EXCLUDES (✕ NOT Part Of)",
                    "target_id": "HDHP Plan (High Deductible)",
                    "priority_rank": 40,
                    "on_fields": ["plan_type == HDHP => NOT FSA ✕"],
                    "status": edge_state,
                },
                {
                    "edge_id": "edge-rate-addendum-supersedes-benefits-quote",
                    "source_id": f"{ENTITY_RATE_GUARANTEE_ADDENDUM} ($125K)",
                    "relation": "SUPERSEDES",
                    "target_id": f"{ENTITY_BENEFITS_QUOTE} ($140K)",
                    "priority_rank": 40,
                    "on_fields": ["premium ($125K overrides $140K)"],
                    "status": edge_state,
                },
                {
                    "edge_id": "edge-rate-addendum-split-from-benefits-quote",
                    "source_id": ENTITY_RATE_GUARANTEE_ADDENDUM,
                    "relation": "SPLIT_FROM",
                    "target_id": ENTITY_BENEFITS_QUOTE,
                    "priority_rank": 40,
                    "on_fields": ["premium"],
                    "status": edge_state,
                },
            ]
        )

    if beat == 0:
        banner = {
            "tone": "info",
            "headline": "Baseline Ontology Active · ont-rev-v1-1-baseline (v1.1.0 · 7 OKF Entities)",
            "message": (
                "Base Quote PDF ($140,000.00) overrides Manual Workbook Entry ($145,000.00), "
                "and the 4-Part Field Guide maps 'Elimination Period' -> 90-Day Waiting Period (Page 14). "
                "Click Beat 1 to upload the Page 4 HDHP Rider ('Employer Account Seed: $1,000') & "
                "2-Page Signed Rate Addendum ($125,000.00)."
            ),
        }
        a1_headline = (
            "4-Part Field Guide Active (`Elimination Period` -> 90-Day Waiting Period)"
        )
        a1_detail = (
            "gemini-3.8-flash reads Page 14 Benefit Waiting Period (90 days) and ignores Page 2 "
            "New-Hire Enrollment Wait (30 days), but lacks HDHP -> HSA/FSA graph edges."
        )
        a2_headline = (
            "Zone 6 Knowledge Graph Standby (Watching for One-Off SME Fixes & Sparsity)"
        )
        a2_detail = (
            "Consolidation Rules Engine resolves Base Quote PDF ($140,000.00, Prio 20) over "
            "Manual Workbook Entry ($145,000.00, Prio 10)."
        )
    elif beat == 1:
        banner = {
            "tone": "amber",
            "headline": "Prompt Alone (~60%) Guesses 'FSA: $1,000.00' ✕ + 2-Page Addendum Quarantined (J = 0.0714)",
            "message": (
                "1) Page 4 prints 'Plan: HDHP · Employer Account Seed: $1,000.00' without writing HSA or FSA — "
                "prompt alone guesses 'FSA: $1,000.00' ✕. 2) The 2-page Signed Rate Addendum ($125,000.00) is "
                "forced into 150-page benefits-quote, flagging 35 unprinted fields as missing and tying at Prio 20."
            ),
        }
        a1_headline = "Ambiguous Page 4 ('Plan: HDHP · Employer Account Seed: $1,000') Misclassified as FSA ✕"
        a1_detail = (
            "Reading 'Employer Account Seed: $1,000.00' in isolation without the HDHP Knowledge Graph "
            "causes prompt-only extraction to guess 'FSA: $1,000.00' ✕ and parks the 2-page $125K addendum."
        )
        a2_headline = "Broker Fixes 'FSA' -> 'HSA' on Page 4 · Socratic Challenge Ready"
        a2_detail = (
            "Click Beat 2 so Agent Gym & Schema Refinery challenge the one-off fix ('Only this quote — "
            "or can ANY HDHP plan never have a standard FSA?') and run the 3-Tier Assay Gate."
        )
    elif beat == 2:
        banner = {
            "tone": "amber",
            "headline": "Socratic Challenge Confirmed -> 'HDHP includes HSA · HDHP excludes FSA' + SplitEntity Passed (0% Backsliding)",
            "message": (
                "gemini-4-argon challenged the 1-file fix into general Knowledge Graph edges "
                "([:INCLUDES] HSA, [:EXCLUDES] FSA) + split 'rate-guarantee-addendum' (Prio 40, $125K). "
                "3-Tier Historical Replay: NFR = 0.0% (0% Backsliding), alpha = 0.94, AC2 = 0.93."
            ),
        }
        a1_headline = (
            "Contrastive Field Guide & Right-Sized 2-Page Addendum Template Compiled"
        )
        a1_detail = (
            "When Plan == HDHP, [:EXCLUDES] rules out FSA ✕ and [:INCLUDES] extracts 'HSA: $1,000.00' ✓; "
            "rate-guarantee-addendum requires only [currency, premium, effective_date]."
        )
        a2_headline = "3-Tier Counterfactual Assay PASSED (NFR = 0.0% · Ready for Business Sign-Off)"
        a2_detail = (
            "Replayed across historical placements with 0.0% backsliding. Click Beat 3 for 1-click "
            "business-owner approval to promote Ontology v1.2.0."
        )
    elif beat == 3:
        banner = {
            "tone": "green",
            "headline": "Ontology v1.2.0 Active · Extracts 'HSA: $1,000.00' ✓ & Signed Addendum ($125,000.00) Wins!",
            "message": (
                "1) Graph in Action on Page 4: rules out FSA (✕ NOT Part Of HDHP) and extracts "
                "'HSA: $1,000.00' ✓ (Part Of HDHP). 2) Signed Rate Addendum ($125,000.00, Prio 40) "
                "overrides Base Quote PDF ($140,000.00) and Manual Entry ($145,000.00), feeding all 8 teams."
            ),
        }
        a1_headline = (
            "Graph in Action: Page 4 Extracted as 'HSA: $1,000.00' ✓ (95%+ Accuracy)"
        )
        a1_detail = (
            "Pinned to ont-rev-v1-2-benefits (v1.2.0); 2-page Signed Rate Addendum ($125,000.00) "
            "passes disposition with 0 false missing-field alerts."
        )
        a2_headline = (
            "Signed Rate Addendum ($125,000.00 · Prio 40) Feeds Unified Renewal Summary"
        )
        a2_detail = (
            "Deterministic Decimal rollup updates Client Renewal Summary ($125,000.00 Annual Premium, "
            "HSA: $1,000.00 Seed, 90-Day Benefit Waiting Period) while locking in-flight v1.1.0 deals."
        )
    else:
        banner = {
            "tone": "purple",
            "headline": "Day-2 Payoff Verified · Next HDHP Renewal Auto-Reconciled in 134 ms (0% Backsliding)",
            "message": (
                f"Next employer renewal ({BENEFITS_EVOL_DAY2_ENTITY_ID} — {BENEFITS_EVOL_DAY2_CLIENT}) "
                "arrives with 'Plan: HDHP · Employer Account Seed: $1,500.00' and a 2-page $118,000.00 "
                "Signed Rate Addendum. Extracted as 'HSA: $1,500.00' ✓ and auto-approved in 134 ms!"
            ),
        }
        a1_headline = "Day-2 HDHP Renewal: 'HSA: $1,500.00' ✓ + $118K Addendum Auto-Approved in 134 ms"
        a1_detail = (
            "Zero FSA misclassifications and zero missing-field quarantine parks under "
            "ont-rev-v1-2-benefits (0 gemini-4-argon escalation tokens)."
        )
        a2_headline = "Broad Graph Rule Protects All Future Carrier HDHP Quotes"
        a2_detail = (
            "One broker fix on Page 4 challenged into a reusable Knowledge Graph rule "
            "('HDHP includes HSA · HDHP excludes FSA') scaling across all carriers."
        )

    socratic_challenge = {
        "step1_expert_fix": 'Broker changes "FSA: $1,000.00" ✕ → "HSA: $1,000.00" ✓ on Page 4 ("Plan: HDHP · Employer Account Seed: $1,000.00")',
        "step2_system_question": '"Is this only for this quote — or can ANY HDHP plan never have a standard FSA?"',
        "step3_broad_rule": "HDHP Plan [:INCLUDES] HSA (Part Of) · HDHP Plan [:EXCLUDES] FSA (✕ NOT Part Of) + Split 2-Page Signed Rate Addendum (Prio 40)",
        "step4_replay_gate": "Replayed across historical placements · 0.0% Backsliding (NFR = 0.0%, alpha = 0.94, AC2 = 0.93)",
    }

    day2_payoff = None
    if beat >= 4:
        day2_payoff = {
            "day2_entity_id": BENEFITS_EVOL_DAY2_ENTITY_ID,
            "day2_client": BENEFITS_EVOL_DAY2_CLIENT,
            "day2_program": "2027 HDHP Medical & Group Life Renewal (1,450 Lives)",
            "classified_doctype": ENTITY_RATE_GUARANTEE_ADDENDUM,
            "ontology_revision_id": active_rev.revision_id,
            "consolidation_edge_id": "edge-rate-addendum-supersedes-benefits-quote",
            "resolved_deductible": (
                "HSA: $1,500.00 ✓ (FSA Ruled Out by [:EXCLUDES]) · "
                "Premium: $118,000.00 (Signed Addendum Overrides $132,000.00 Base Quote)"
            ),
            "false_positive_quarantine_parks": 0,
            "latency_ms": 134,
            "human_touchpoints": 0,
            "escalation_tokens": 0,
        }

    return {
        "scenario_domain": "benefits",
        "entity_id": BENEFITS_EVOL_ENTITY_ID,
        "client_name": BENEFITS_EVOL_CLIENT,
        "program_title": BENEFITS_EVOL_PROGRAM,
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
            "total_insured_value": "$145K Manual → $140K Base PDF",
            "primary_plus_excess_limit": (
                "$125,000.00 (Signed Addendum Wins)"
                if beat >= 3
                else "$140,000.00 (⚠ $125K Addendum Tie)"
            ),
            "coastal_wind_deductible": (
                "HSA: $1,000.00 ✓ (Graph Rule)"
                if beat >= 3
                else (
                    "FSA: $1,000.00 ✕ (Prompt Guess)"
                    if beat >= 1
                    else "$1,000.00 Seed (Page 4)"
                )
            ),
            "deductible_authority": (
                "HDHP [:INCLUDES] HSA · [:EXCLUDES] FSA (95%+)"
                if beat >= 3
                else "Prompt Alone (~60% · Guesses FSA ✕)"
            ),
            "annual_premium": "$125,000.00" if beat >= 3 else "$140,000.00",
            "metric_labels": {
                "col1_label": "Manual Entry vs. Base Quote PDF",
                "col1_sub": "Base PDF ($140K) Overrides Manual ($145K)",
                "col2_label": "Winning Renewal Premium (All 8 Teams)",
                "col2_sub": "2-Page Signed Rate Addendum ($125K · Prio 40)",
                "col3_label": "Page 4: 'Plan: HDHP · Account Seed: $1,000'",
            },
        },
        "placement_checklist": checklist,
        "graph_edges_preview": graph_edges_preview,
        "banner": banner,
        "socratic_challenge": socratic_challenge,
        "agent1_status": {
            "agent_name": "Agent 1 · Document Bridge (Sense A Contrastive Extractor)",
            "spiffe_id": BROKER_AGENT_SPIFFE_ID,
            "model": WORKHORSE_MODEL,
            "headline": a1_headline,
            "detail": a1_detail,
            "pinned_ontology_revision": active_rev.revision_id,
        },
        "agent2_status": {
            "agent_name": "Agent 2 · Schema Refinery & Knowledge Graph (Sense B)",
            "spiffe_id": REFINERY_AGENT_SPIFFE_ID,
            "model": REASONING_MODEL,
            "headline": a2_headline,
            "detail": a2_detail,
            "jaccard_similarity": 0.0714,
            "nfr": 0.0,
            "krippendorff_alpha": 0.94,
            "gwets_ac2": 0.93,
            "rogan_gladen_pass_rate": 0.988,
        },
        "proposal": proposal_dict,
        "assay_report": assay_dict,
        "day2_payoff": day2_payoff,
    }
