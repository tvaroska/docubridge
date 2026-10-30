"""Collaborative Commercial Consolidation Loan (4-Beat Split-Screen & Day-2 Agent Gym Waiver).

Implements the interactive Dual-Zone Borrower Portal + Bank Underwriter & Dual-Agent
Board scenario (`LOAN-MERIDIAN-CONSOL-2026` — Meridian Logistics Center LLC `$1.80M`
Commercial Debt Consolidation Facility):
  - Beat 0: Initial 3-document borrower checklist (`0/3` complete).
  - Beat 1: Borrower uploads 2025 Audited Financials & Note 6 Debt Schedule (`Page 4`).
            Agent 1 (`gemini-3.8-flash`) extracts the 3-row liability schedule;
            Agent 2 (`gemini-4-argon` + deterministic `Decimal` math) mutates the
            checklist from 3 to 6 items (`2` emergent external payoff letters required,
            `1` internal `$250K` LOC auto-waived via core banking feed).
  - Beat 2: Borrower uploads Oct 2026 Operating Bank Statement & First National `$1.20M`
            Payoff Letter. Agent 1 spots a `$48,210.00` outgoing wire on `Page 2
            [0.16, 0.54]` (`FINAL PAYOFF - LOAN #8841 - PAID IN FULL`) to EquipLease
            Financial Corp; Agent 2 links the cross-document proof to Item #5 and raises
            an Underwriter Overwatch waiver recommendation.
  - Beat 3: Senior Credit Officer (`usr:senior.credit.officer@docubridge.io`, SPIFFE OBO
            `spiffe://agents.docubridge.io/internal/credit-officer-agent`) approves the
            1-click waiver (`RSR = 0.98`, `4,800 ms` dwell, `citation_opened=True`),
            transitioning the loan package to `done = True` (`4 satisfied`, `2 waived`,
            `0 required`).
  - Beat 4: Agent Gym distills the cross-document `PAID IN FULL` wire waiver into a
            promoted `EXTRACTION_HINT` rule, passes `4/4` synthetic stress cases
            (including the negative monthly installment guardrail), compiles `v1.1.0` of
            the `consolidation-payoff` skill, and verifies Day-2 zero-touch auto-waiver
            for `Apex Precision Manufacturing LLC` (`LOAN-APEX-CONSOL-DAY2`) in `142 ms`
            with `0` human touchpoints and `0` `gemini-4-argon` escalation tokens.
"""

from __future__ import annotations

import copy
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Request

from bridge.a2ui_inbox import (
    A2uiClientActionRequest,
    ActionCitation,
    ActionInboxStore,
    ActionProgressStep,
)
from bridge.agent_gym import AgentGymEngine, TribalRuleCategory
from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.knowledge_catalog import CatalogRole, KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.telemetry import trace_step
from bridge.user_interaction.schemas import AdvanceConsolidationCollabRequest
from bridge.user_interaction.surfaces import ingest_accepted_submission
from contract.extraction import SourceAnchor
from contract.wire import Disposition, Gate, LedgerEntry

CONSOL_COLLAB_ENTITY_ID = "LOAN-MERIDIAN-CONSOL-2026"
CONSOL_COLLAB_BORROWER = "Meridian Logistics Center LLC"
CONSOL_COLLAB_FACILITY = "$1,800,000.00 Commercial Debt Consolidation Facility"
CONSOL_DAY2_ENTITY_ID = "LOAN-APEX-CONSOL-DAY2"
CONSOL_DAY2_BORROWER = "Apex Precision Manufacturing LLC"

BRIDGE_AGENT_SPIFFE_ID = "spiffe://agents.docubridge.io/internal/broker-placement-agent"
UNDERWRITER_AGENT_SPIFFE_ID = (
    "spiffe://agents.docubridge.io/internal/credit-officer-agent"
)
SENIOR_UNDERWRITER_PRINCIPAL = "usr:senior.credit.officer@docubridge.io"

_DOCTYPES = {
    "financials": "-".join(("cre", "credit", "agreement")),
    "appraisal": "-".join(("mai", "appraisal")),
    "bank_statement": "closing-disclosure",
    "payoff_letter": "consolidation-payoff",
}


def _compute_deterministic_financial_summary() -> dict[str, Any]:
    """Compute exact Decimal LTV, DSCR, and liability schedule totals (Rule 0 firewall)."""
    fnb_balance = Decimal("1200000.00")
    equip_balance = Decimal("350000.00")
    internal_loc_balance = Decimal("250000.00")
    total_loan = fnb_balance + equip_balance + internal_loc_balance
    appraised_value = Decimal("2600000.00")
    annual_noi = Decimal("312000.00")
    annual_debt_service = Decimal("216666.67")

    ltv_pct = ((total_loan / appraised_value) * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    dscr_ratio = (annual_noi / annual_debt_service).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "consolidated_loan_amount": f"${total_loan:,.2f}",
        "appraised_collateral_value": f"${appraised_value:,.2f}",
        "ltv_pct": float(ltv_pct),
        "ltv_formatted": f"{ltv_pct}%",
        "ltv_policy_cap": "75.00%",
        "ltv_compliant": ltv_pct <= Decimal("75.00"),
        "annual_cfads_noi": f"${annual_noi:,.2f}",
        "annual_debt_service": f"${annual_debt_service:,.2f}",
        "dscr_ratio": float(dscr_ratio),
        "dscr_formatted": f"{dscr_ratio}x",
        "dscr_policy_min": "1.25x",
        "dscr_compliant": dscr_ratio >= Decimal("1.25"),
        "existing_liabilities_schedule": [
            {
                "creditor": "First National Bank",
                "account_ref": "#FNB-4402",
                "facility_type": "Senior Commercial Mortgage",
                "payoff_amount": f"${fnb_balance:,.2f}",
                "handling": "External Payoff Letter Required",
            },
            {
                "creditor": "EquipLease Financial Corp",
                "account_ref": "#8841",
                "facility_type": "Fleet & Material Handling Term Loan",
                "payoff_amount": f"${equip_balance:,.2f}",
                "handling": "External Payoff or Verified Wire Proof",
            },
            {
                "creditor": "DocuBridge Commercial Bank (Internal)",
                "account_ref": "#LOC-9012",
                "facility_type": "Revolving Working Capital Line",
                "payoff_amount": f"${internal_loc_balance:,.2f}",
                "handling": "Auto-Waived via Internal Core Feed",
            },
        ],
    }


def _build_initial_collab_state() -> dict[str, Any]:
    """Return the Beat 0 initial state for the Collaborative Consolidation Loan stage."""
    checklist: list[dict[str, Any]] = [
        {
            "id": "item-1-financials",
            "label": "2025 Audited Financials & Note 6 Debt Schedule",
            "doctype": _DOCTYPES["financials"],
            "status": "required",
            "emergent": False,
            "badge_text": "REQUIRED",
            "reason": "Base underwriting requirement to verify borrower NOI and existing debt obligations.",
            "source_citation": None,
            "page_number": None,
            "bounding_box": None,
            "doc_id": None,
            "vault_uri": None,
            "sha256": None,
            "auto_waived_reason": None,
        },
        {
            "id": "item-2-bank-stmt",
            "label": "Oct 2026 Operating Bank Statement (90-Day Liquidity & Cash Flow)",
            "doctype": _DOCTYPES["bank_statement"],
            "status": "required",
            "emergent": False,
            "badge_text": "REQUIRED",
            "reason": "Base underwriting requirement to verify operating cash reserves and debt-service debits.",
            "source_citation": None,
            "page_number": None,
            "bounding_box": None,
            "doc_id": None,
            "vault_uri": None,
            "sha256": None,
            "auto_waived_reason": None,
        },
        {
            "id": "item-3-appraisal",
            "label": "MAI Commercial Property Appraisal ($2.60M Industrial Warehouse)",
            "doctype": _DOCTYPES["appraisal"],
            "status": "required",
            "emergent": False,
            "badge_text": "REQUIRED",
            "reason": "Collateral valuation required to confirm consolidated LTV <= 75.00%.",
            "source_citation": None,
            "page_number": None,
            "bounding_box": None,
            "doc_id": None,
            "vault_uri": None,
            "sha256": None,
            "auto_waived_reason": None,
        },
    ]
    return {
        "entity_id": CONSOL_COLLAB_ENTITY_ID,
        "borrower_name": CONSOL_COLLAB_BORROWER,
        "facility_title": CONSOL_COLLAB_FACILITY,
        "beat": 0,
        "done": False,
        "counts": {
            "total": 3,
            "satisfied": 0,
            "waived": 0,
            "required": 3,
        },
        "borrower_checklist": checklist,
        "borrower_banner": {
            "tone": "info",
            "headline": "Application Opened · 3 Baseline Documents Requested",
            "message": (
                "Upload your 2025 Audited Financials, Oct 2026 Operating Bank Statement, "
                "and MAI Appraisal to begin automated underwriting."
            ),
            "agent_note": (
                "Document Bridge (Sense A) will inspect your Debt Schedule and dynamically "
                "tailor payoff requirements."
            ),
        },
        "agent1_bridge_status": {
            "agent_name": "Agent 1 · Document Bridge (Sense A Intake & OCR)",
            "spiffe_id": BRIDGE_AGENT_SPIFFE_ID,
            "model": WORKHORSE_MODEL,
            "headline": "Awaiting Borrower Document Upload",
            "detail": (
                "Ready to run Edge PII Redaction, 7-Year WORM Audit Vault lock, "
                "and Document AI page/bbox anchor extraction."
            ),
            "documents_processed": 0,
            "active_anchors": [],
            "skill_revision": "consolidation-payoff@1.0.0",
        },
        "agent2_policy_status": {
            "agent_name": "Agent 2 · Bank Underwriting & Policy Agent (Sense B)",
            "spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "model": REASONING_MODEL,
            "headline": "Baseline 3-Document Checklist Active",
            "detail": (
                "Monitoring Operational Store for Note 6 Schedule of Existing Liabilities "
                "to compute Decimal LTV/DSCR and spawn creditor-specific payoff tasks."
            ),
            "checklist_mutations": [],
            "policy_rules_evaluated": [
                "Rule 0 Decimal LTV Cap <= 75.00%",
                "Rule 0 Decimal DSCR Floor >= 1.25x",
            ],
        },
        "cross_doc_discovery": None,
        "underwriter_overwatch": None,
        "agent_gym_payoff": None,
        "financial_summary": _compute_deterministic_financial_summary(),
    }


def _recompute_counts(state: dict[str, Any]) -> None:
    """Recompute checklist item counts (`total`, `satisfied`, `waived`, `required`)."""
    items = state.get("borrower_checklist", [])
    satisfied = sum(1 for item in items if item.get("status") == "satisfied")
    waived = sum(1 for item in items if item.get("status") == "waived")
    required = sum(1 for item in items if item.get("status") == "required")
    state["counts"] = {
        "total": len(items),
        "satisfied": satisfied,
        "waived": waived,
        "required": required,
    }


_COLLAB_STATE: dict[str, Any] = _build_initial_collab_state()


def reset_consolidation_collab_state() -> dict[str, Any]:
    """Reset the collaborative consolidation loan state machine back to Beat 0."""
    global _COLLAB_STATE
    _COLLAB_STATE = _build_initial_collab_state()
    return copy.deepcopy(_COLLAB_STATE)


def get_consolidation_collab_state() -> dict[str, Any]:
    """Return a deep copy of the current collaborative consolidation loan state."""
    return copy.deepcopy(_COLLAB_STATE)


def _execute_beat_1(
    state: dict[str, Any],
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    gym: AgentGymEngine,
) -> None:
    """Execute Beat 1: Upload 2025 Financials + Appraisal -> Discover 3-Row Debt Schedule -> Mutate Checklist (3 -> 6)."""
    debt_schedule_anchor = SourceAnchor(
        page_number=4,
        bounding_box=(0.12, 0.38, 0.84, 0.56),
        snippet=(
            "Note 6 — Schedule of Existing Liabilities to be Consolidated: "
            "(1) First National Bank Commercial Mortgage #FNB-4402: $1,200,000.00; "
            "(2) EquipLease Financial Corp Term Loan #8841: $350,000.00; "
            "(3) DocuBridge Commercial Bank Internal LOC #LOC-9012: $250,000.00. "
            "Total Consolidated Payoff: $1,800,000.00."
        ),
    )
    appraisal_anchor = SourceAnchor(
        page_number=1,
        bounding_box=(0.14, 0.22, 0.82, 0.36),
        snippet=(
            "MAI As-Is Market Valuation — Meridian Logistics Center (185,000 SF Class-A "
            "Industrial Facility): $2,600,000.00. Stabilized Annual NOI: $312,000.00."
        ),
    )

    with trace_step(
        "docubridge.collab.agent1_extract_debt_schedule",
        {
            "docubridge.zone": "2_mediation_quarantine",
            "docubridge.entity_id": CONSOL_COLLAB_ENTITY_ID,
            "docubridge.doc_id": "doc-meridian-2025-financials",
            "docubridge.agent_spiffe_id": BRIDGE_AGENT_SPIFFE_ID,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "extract_document",
            "gen_ai.agent.name": "document_bridge_sense_a",
            "gen_ai.request.model": WORKHORSE_MODEL,
            "gen_ai.usage.input_tokens": 640,
            "gen_ai.usage.output_tokens": 185,
            "docubridge.finops.traffic_type": "production_inference",
        },
    ):
        entry = LedgerEntry(
            doc_id="doc-meridian-2025-financials",
            doctype="credit-agreement",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted 2025 Audited Financials & Note 6 Debt Schedule",
            issuer=CONSOL_COLLAB_BORROWER,
            key_fields={
                "currency": "USD",
                "loan_amount": "$1,800,000.00",
                "appraised_value": "$2,600,000.00",
                "net_operating_income": "$312,000.00",
                "annual_debt_service": "$216,666.67",
            },
            source_anchors={
                "loan_amount": debt_schedule_anchor,
                "appraised_value": appraisal_anchor,
                "net_operating_income": appraisal_anchor,
                "annual_debt_service": debt_schedule_anchor,
            },
        )
        raw_bytes = (
            b"Meridian Logistics Center LLC - 2025 Audited Financials & Note 6 Debt Schedule. "
            b"Contact controller Jane Doe at 312-555-0194 or SSN 999-11-2222. "
            b"First National Bank #FNB-4402: $1,200,000.00. "
            b"EquipLease Financial Corp #8841: $350,000.00. "
            b"Internal Revolving LOC #LOC-9012: $250,000.00."
        )
        ingest_res = ingest_accepted_submission(
            entry,
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            context_id="ctx-meridian-consol-2026",
            raw_document_bytes=raw_bytes,
            filename="meridian-2025-financials-debt-schedule.pdf",
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
        )

    with trace_step(
        "docubridge.collab.agent2_mutate_requirements",
        {
            "docubridge.zone": "3_consolidation_operational",
            "docubridge.entity_id": CONSOL_COLLAB_ENTITY_ID,
            "docubridge.agent_spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "evaluate_underwriting_policy",
            "gen_ai.agent.name": "credit_underwriting_sense_b",
            "gen_ai.request.model": REASONING_MODEL,
            "gen_ai.usage.input_tokens": 520,
            "gen_ai.usage.output_tokens": 160,
            "docubridge.finops.traffic_type": "production_inference",
            "docubridge.emergent_items_added": 2,
            "docubridge.emergent_items_auto_waived": 1,
        },
    ):
        vault_uri = ingest_res.vault_record.vault_uri
        sha256_hex = ingest_res.vault_record.sha256
        state["borrower_checklist"] = [
            {
                "id": "item-1-financials",
                "label": "2025 Audited Financials & Note 6 Debt Schedule",
                "doctype": _DOCTYPES["financials"],
                "status": "satisfied",
                "emergent": False,
                "badge_text": "VERIFIED · PAGE 4",
                "reason": (
                    "Extracted 3 existing liabilities totaling $1,800,000.00 from Note 6 "
                    "(Page 4 [0.12, 0.38]) and locked in 7-year WORM Audit Vault."
                ),
                "source_citation": "Page 4 · Region [0.12, 0.38]",
                "page_number": 4,
                "bounding_box": [0.12, 0.38, 0.84, 0.56],
                "doc_id": "doc-meridian-2025-financials",
                "vault_uri": vault_uri,
                "sha256": sha256_hex,
                "auto_waived_reason": None,
            },
            {
                "id": "item-2-bank-stmt",
                "label": "Oct 2026 Operating Bank Statement (90-Day Liquidity & Cash Flow)",
                "doctype": _DOCTYPES["bank_statement"],
                "status": "required",
                "emergent": False,
                "badge_text": "REQUIRED",
                "reason": "Upload October 2026 operating account statement to verify cash flow and recent debt payments.",
                "source_citation": None,
                "page_number": None,
                "bounding_box": None,
                "doc_id": None,
                "vault_uri": None,
                "sha256": None,
                "auto_waived_reason": None,
            },
            {
                "id": "item-3-appraisal",
                "label": "MAI Commercial Property Appraisal ($2.60M Industrial Warehouse)",
                "doctype": _DOCTYPES["appraisal"],
                "status": "satisfied",
                "emergent": False,
                "badge_text": "VERIFIED · 69.23% LTV",
                "reason": (
                    "Confirmed $2,600,000.00 collateral valuation (Page 1 [0.14, 0.22]). "
                    "Deterministic LTV = 69.23% (<= 75.00% policy cap)."
                ),
                "source_citation": "Page 1 · Region [0.14, 0.22]",
                "page_number": 1,
                "bounding_box": [0.14, 0.22, 0.82, 0.36],
                "doc_id": "doc-meridian-2025-financials",
                "vault_uri": vault_uri,
                "sha256": sha256_hex,
                "auto_waived_reason": None,
            },
            {
                "id": "item-4-fnb-payoff",
                "label": "Payoff Letter — First National Bank Mortgage (#FNB-4402 · $1,200,000.00)",
                "doctype": _DOCTYPES["payoff_letter"],
                "status": "required",
                "emergent": True,
                "badge_text": "NEW · REQUIRED",
                "reason": (
                    "Spawned dynamically by Agent 2 from Note 6 Debt Schedule (Page 4): "
                    "external senior mortgage payoff statement required for closing wire."
                ),
                "source_citation": "Page 4 · Region [0.12, 0.38]",
                "page_number": 4,
                "bounding_box": [0.12, 0.38, 0.84, 0.56],
                "doc_id": "doc-meridian-2025-financials",
                "vault_uri": vault_uri,
                "sha256": sha256_hex,
                "auto_waived_reason": None,
            },
            {
                "id": "item-5-equip-payoff",
                "label": "Payoff Letter — EquipLease Financial Corp (#8841 · $350,000.00)",
                "doctype": _DOCTYPES["payoff_letter"],
                "status": "required",
                "emergent": True,
                "badge_text": "NEW · REQUIRED",
                "reason": (
                    "Spawned dynamically by Agent 2 from Note 6 Debt Schedule (Page 4): "
                    "external equipment loan payoff quote or verified settlement proof required."
                ),
                "source_citation": "Page 4 · Region [0.12, 0.38]",
                "page_number": 4,
                "bounding_box": [0.12, 0.38, 0.84, 0.56],
                "doc_id": "doc-meridian-2025-financials",
                "vault_uri": vault_uri,
                "sha256": sha256_hex,
                "auto_waived_reason": None,
            },
            {
                "id": "item-6-internal-loc",
                "label": "Internal Revolving LOC Payoff — DocuBridge Bank (#LOC-9012 · $250,000.00)",
                "doctype": _DOCTYPES["payoff_letter"],
                "status": "waived",
                "emergent": True,
                "badge_text": "AUTO-WAIVED BY AGENT 2",
                "reason": (
                    "Discovered on Note 6 Debt Schedule (Page 4), but automatically waived by "
                    "Agent 2: account #LOC-9012 is held on-us and verified directly via internal "
                    "core banking ledger ($250,000.00 balance, zero borrower upload needed)."
                ),
                "source_citation": "Page 4 · Region [0.12, 0.38]",
                "page_number": 4,
                "bounding_box": [0.12, 0.38, 0.84, 0.56],
                "doc_id": "doc-meridian-2025-financials",
                "vault_uri": vault_uri,
                "sha256": sha256_hex,
                "auto_waived_reason": "On-us facility #LOC-9012 verified directly against internal core banking feed.",
            },
        ]
        state["borrower_banner"] = {
            "tone": "warning",
            "headline": "Checklist Dynamically Updated (3 -> 6 Items) from Your Note 6 Debt Schedule",
            "message": (
                "We verified your 2025 Financials and $2.60M Appraisal (69.23% LTV, 1.44x DSCR). "
                "Because Note 6 (Page 4) lists 3 existing facilities to consolidate, we added "
                "2 external payoff letters (#FNB-4402 and #8841) and automatically waived your "
                "internal $250K LOC (#LOC-9012)."
            ),
            "agent_note": (
                "Next: Upload your Oct 2026 Operating Bank Statement and First National Bank "
                "Payoff Letter."
            ),
        }
        state["agent1_bridge_status"] = {
            "agent_name": "Agent 1 · Document Bridge (Sense A Intake & OCR)",
            "spiffe_id": BRIDGE_AGENT_SPIFFE_ID,
            "model": WORKHORSE_MODEL,
            "headline": "Extracted Note 6 Debt Schedule (Page 4 [0.12, 0.38]) & Locked WORM Vault",
            "detail": (
                "Redacted 2 PII tokens at edge, archived SHA-256 WORM copy, and extracted "
                "3 creditor rows ($1.20M First National, $350K EquipLease #8841, $250K Internal LOC)."
            ),
            "documents_processed": 1,
            "active_anchors": [
                {
                    "field": "Note 6 Debt Schedule ($1.80M)",
                    "citation": "Page 4 · Region [0.12, 0.38]",
                    "doc_title": "meridian-2025-financials-debt-schedule.pdf",
                    "vault_uri": vault_uri,
                    "sha256": sha256_hex,
                }
            ],
            "skill_revision": "consolidation-payoff@1.0.0",
        }
        state["agent2_policy_status"] = {
            "agent_name": "Agent 2 · Bank Underwriting & Policy Agent (Sense B)",
            "spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "model": REASONING_MODEL,
            "headline": "Spawned 3 Emergent Payoff Tasks (2 External Required, 1 On-Us Auto-Waived)",
            "detail": (
                "Computed deterministic LTV = 69.23% (<= 75% cap) and DSCR = 1.44x (>= 1.25x floor). "
                "Expanded borrower checklist from 3 to 6 items and auto-waived internal LOC #LOC-9012."
            ),
            "checklist_mutations": [
                "+ REQUIRED: First National Bank Payoff (#FNB-4402 · $1,200,000.00)",
                "+ REQUIRED: EquipLease Financial Corp Payoff (#8841 · $350,000.00)",
                "+ AUTO-WAIVED: Internal Revolving LOC (#LOC-9012 · $250,000.00 via Core Banking Feed)",
            ],
            "policy_rules_evaluated": [
                "Rule 0 Decimal LTV: $1.80M / $2.60M = 69.23% (PASS)",
                "Rule 0 Decimal DSCR: $312.0K / $216.7K = 1.44x (PASS)",
                "Policy 4.2 On-Us Consolidation Auto-Waiver (#LOC-9012)",
            ],
        }
        state["beat"] = 1
        state["done"] = False
        _recompute_counts(state)


def _execute_beat_2(
    state: dict[str, Any],
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    gym: AgentGymEngine,
) -> None:
    """Execute Beat 2: Upload Oct 2026 Bank Statement + FNB Payoff -> Spot $48,210 PAID IN FULL Wire on Page 2 -> Propose Waiver."""
    wire_anchor = SourceAnchor(
        page_number=2,
        bounding_box=(0.16, 0.54, 0.88, 0.63),
        snippet=(
            "2026-10-12 OUTGOING FEDWIRE #99481204 · BENEFICIARY: EQUIPLEASE FINANCIAL CORP · "
            "AMOUNT: $48,210.00 · OBI/MEMO: FINAL PAYOFF - LOAN #8841 - PAID IN FULL (ZERO BALANCE)"
        ),
    )
    fnb_anchor = SourceAnchor(
        page_number=1,
        bounding_box=(0.15, 0.28, 0.85, 0.44),
        snippet=(
            "First National Bank Official Payoff Statement — Loan #FNB-4402: "
            "Total Principal & Accrued Interest Payoff: $1,200,000.00 (Good through 2026-11-15)."
        ),
    )

    with trace_step(
        "docubridge.collab.agent1_extract_bank_statement",
        {
            "docubridge.zone": "2_mediation_quarantine",
            "docubridge.entity_id": CONSOL_COLLAB_ENTITY_ID,
            "docubridge.doc_id": "doc-meridian-oct2026-bank-stmt",
            "docubridge.agent_spiffe_id": BRIDGE_AGENT_SPIFFE_ID,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "extract_document",
            "gen_ai.agent.name": "document_bridge_sense_a",
            "gen_ai.request.model": WORKHORSE_MODEL,
            "gen_ai.usage.input_tokens": 710,
            "gen_ai.usage.output_tokens": 210,
            "docubridge.finops.traffic_type": "production_inference",
        },
    ):
        bank_entry = LedgerEntry(
            doc_id="doc-meridian-oct2026-bank-stmt",
            doctype="closing-disclosure",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted Oct 2026 Operating Bank Statement with Page 2 Payoff Wire",
            issuer="Meridian Operating Account",
            key_fields={
                "currency": "USD",
                "closing_fee": "$48,210.00",
            },
            source_anchors={
                "closing_fee": wire_anchor,
            },
        )
        bank_res = ingest_accepted_submission(
            bank_entry,
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            context_id="ctx-meridian-consol-2026",
            raw_document_bytes=(
                b"Meridian Logistics Center LLC - Oct 2026 Operating Bank Statement. "
                b"Page 2 [0.16, 0.54] 2026-10-12 Outgoing Wire $48,210.00 to EquipLease Financial Corp "
                b"Memo: FINAL PAYOFF - LOAN #8841 - PAID IN FULL."
            ),
            filename="meridian-oct2026-operating-bank-statement.pdf",
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
        )

        fnb_entry = LedgerEntry(
            doc_id="doc-meridian-fnb-payoff",
            doctype="addendum",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted First National Bank Payoff Statement (#FNB-4402)",
            issuer="First National Bank",
            key_fields={
                "currency": "USD",
                "principal_amount": "$1,200,000.00",
            },
            source_anchors={
                "principal_amount": fnb_anchor,
            },
        )
        fnb_res = ingest_accepted_submission(
            fnb_entry,
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            context_id="ctx-meridian-consol-2026",
            raw_document_bytes=(
                b"First National Bank Official Payoff Statement - Account #FNB-4402 "
                b"Payoff Amount: $1,200,000.00."
            ),
            filename="first-national-payoff-fnb4402.pdf",
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
        )

    with trace_step(
        "docubridge.collab.agent2_cross_doc_reconcile",
        {
            "docubridge.zone": "3_consolidation_operational",
            "docubridge.entity_id": CONSOL_COLLAB_ENTITY_ID,
            "docubridge.agent_spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "cross_document_reconcile",
            "gen_ai.agent.name": "credit_underwriting_sense_b",
            "gen_ai.request.model": REASONING_MODEL,
            "gen_ai.usage.input_tokens": 890,
            "gen_ai.usage.output_tokens": 240,
            "docubridge.finops.traffic_type": "conflict_escalation",
            "docubridge.cross_doc_match_account": "#8841",
            "docubridge.cross_doc_wire_amount": "$48,210.00",
        },
    ):
        bank_vault_uri = bank_res.vault_record.vault_uri
        bank_sha256 = bank_res.vault_record.sha256
        fnb_vault_uri = fnb_res.vault_record.vault_uri
        fnb_sha256 = fnb_res.vault_record.sha256

        for item in state["borrower_checklist"]:
            if item["id"] == "item-2-bank-stmt":
                item["status"] = "satisfied"
                item["badge_text"] = "VERIFIED · PAGE 2 WIRE SPOTTED"
                item["reason"] = (
                    "Verified 90-day operating liquidity AND spotted Oct 12 outgoing Fedwire "
                    "$48,210.00 ('FINAL PAYOFF - LOAN #8841 - PAID IN FULL') on Page 2 [0.16, 0.54]."
                )
                item["source_citation"] = "Page 2 · Region [0.16, 0.54]"
                item["page_number"] = 2
                item["bounding_box"] = [0.16, 0.54, 0.88, 0.63]
                item["doc_id"] = "doc-meridian-oct2026-bank-stmt"
                item["vault_uri"] = bank_vault_uri
                item["sha256"] = bank_sha256
            elif item["id"] == "item-4-fnb-payoff":
                item["status"] = "satisfied"
                item["badge_text"] = "VERIFIED · $1.20M PAYOFF"
                item["reason"] = (
                    "Verified First National Bank payoff letter (#FNB-4402 · $1,200,000.00) "
                    "on Page 1 [0.15, 0.28]."
                )
                item["source_citation"] = "Page 1 · Region [0.15, 0.28]"
                item["page_number"] = 1
                item["bounding_box"] = [0.15, 0.28, 0.85, 0.44]
                item["doc_id"] = "doc-meridian-fnb-payoff"
                item["vault_uri"] = fnb_vault_uri
                item["sha256"] = fnb_sha256
            elif item["id"] == "item-5-equip-payoff":
                item["badge_text"] = "WAIVER RECOMMENDED · SEE PAGE 2 WIRE"
                item["reason"] = (
                    "Agent 1 & Agent 2 matched Oct 12 outgoing wire ($48,210.00 'FINAL PAYOFF - "
                    "LOAN #8841 - PAID IN FULL') on Bank Statement Page 2 [0.16, 0.54] to "
                    "EquipLease #8841. Awaiting Underwriter 1-click waiver approval."
                )
                item["source_citation"] = "Page 2 · Region [0.16, 0.54]"
                item["page_number"] = 2
                item["bounding_box"] = [0.16, 0.54, 0.88, 0.63]
                item["doc_id"] = "doc-meridian-oct2026-bank-stmt"
                item["vault_uri"] = bank_vault_uri
                item["sha256"] = bank_sha256

        state["cross_doc_discovery"] = {
            "status": "waiver_recommended",
            "discovered_in_doc_id": "doc-meridian-oct2026-bank-stmt",
            "discovered_in_filename": "meridian-oct2026-operating-bank-statement.pdf",
            "page_number": 2,
            "bounding_box": [0.16, 0.54, 0.88, 0.63],
            "region_label": "Page 2 · Region [0.16, 0.54]",
            "wire_date": "2026-10-12",
            "wire_amount": "$48,210.00",
            "wire_beneficiary": "EquipLease Financial Corp",
            "wire_reference": "FINAL PAYOFF - LOAN #8841 - PAID IN FULL",
            "target_checklist_item_id": "item-5-equip-payoff",
            "recommendation": (
                "Waive standalone EquipLease #8841 payoff letter: 2025 Audited Financials "
                "(as of 2025-12-31) carried $350,000.00, but Oct 12, 2026 Operating Bank "
                "Statement Page 2 [0.16, 0.54] proves the remaining $48,210.00 balance was "
                "wired with memo 'FINAL PAYOFF - LOAN #8841 - PAID IN FULL'."
            ),
            "vault_uri": bank_vault_uri,
            "sha256": bank_sha256,
        }
        state["underwriter_overwatch"] = {
            "status": "awaiting_approval",
            "headline": "Underwriter Overwatch · 1-Click Cross-Document Waiver Ready",
            "recommendation": (
                "Approve Agent 2's recommendation to waive Item #5 (EquipLease Financial Corp "
                "#8841 Payoff Letter) based on verified Page 2 [0.16, 0.54] Fedwire receipt "
                "($48,210.00 'PAID IN FULL')."
            ),
            "actor_principal": SENIOR_UNDERWRITER_PRINCIPAL,
            "actor_role": "underwriter",
            "agent_spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "citation_label": "Page 2 · Region [0.16, 0.54]",
            "doc_title": "meridian-oct2026-operating-bank-statement.pdf",
            "vault_uri": bank_vault_uri,
            "sha256": bank_sha256,
        }
        state["borrower_banner"] = {
            "tone": "info",
            "headline": "Cross-Document Payoff Proof Spotted in Your Oct 2026 Bank Statement!",
            "message": (
                "We verified your First National Bank ($1.20M) payoff letter and spotted your "
                "Oct 12, 2026 outgoing wire ($48,210.00 'FINAL PAYOFF - LOAN #8841 - PAID IN FULL') "
                "to EquipLease Financial Corp on Page 2 of your Bank Statement. We proposed "
                "waiving the separate EquipLease payoff letter so you don't need to chase the lender."
            ),
            "agent_note": (
                "Underwriter Overwatch is reviewing the Page 2 [0.16, 0.54] wire anchor for "
                "1-click waiver sign-off."
            ),
        }
        state["agent1_bridge_status"]["headline"] = (
            "Spotted Oct 12 '$48,210.00 PAID IN FULL' Wire on Bank Statement Page 2 [0.16, 0.54]"
        )
        state["agent1_bridge_status"]["detail"] = (
            "Extracted First National $1.20M payoff quote AND detected outgoing Fedwire #99481204 "
            "to EquipLease Financial Corp referencing Loan #8841 on Page 2 [0.16, 0.54]."
        )
        state["agent1_bridge_status"]["documents_processed"] = 3
        state["agent1_bridge_status"]["active_anchors"].append(
            {
                "field": "EquipLease #8841 Final Payoff Wire ($48,210.00)",
                "citation": "Page 2 · Region [0.16, 0.54]",
                "doc_title": "meridian-oct2026-operating-bank-statement.pdf",
                "vault_uri": bank_vault_uri,
                "sha256": bank_sha256,
            }
        )
        state["agent2_policy_status"]["headline"] = (
            "Cross-Document Reconciliation Matched Wire #8841 -> Escalated Waiver to Overwatch"
        )
        state["agent2_policy_status"]["detail"] = (
            "Bi-temporal reconciliation resolved the 2025-12-31 Note 6 liability ($350K) against "
            "the 2026-10-12 bank wire ($48,210.00 'PAID IN FULL'). Queued 1-click waiver in Action Inbox."
        )
        state["agent2_policy_status"]["checklist_mutations"].append(
            "~ WAIVER PROPOSED: EquipLease #8841 matched to Oct 12 Bank Wire ($48,210.00 Page 2 [0.16, 0.54])"
        )
        state["beat"] = 2
        state["done"] = False
        _recompute_counts(state)


def _execute_beat_3(
    state: dict[str, Any],
    *,
    inbox: ActionInboxStore,
) -> None:
    """Execute Beat 3: Underwriter 1-click waiver approval via A2UI ActionInboxStore -> Done = True."""
    discovery = state.get("cross_doc_discovery") or {}
    bank_vault_uri = discovery.get(
        "vault_uri",
        "gs://docubridge-audit-vault/vault/ctx-meridian-consol-2026/doc-meridian-oct2026-bank-stmt/v1.pdf",
    )
    bank_sha256 = discovery.get(
        "sha256", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    )

    with trace_step(
        "docubridge.collab.underwriter_approve_waiver",
        {
            "docubridge.zone": "5_user_interaction_a2ui",
            "docubridge.entity_id": CONSOL_COLLAB_ENTITY_ID,
            "docubridge.agent_spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "docubridge.actor_principal": SENIOR_UNDERWRITER_PRINCIPAL,
            "docubridge.actor_role": "underwriter",
            "docubridge.review_dwell_ms": 4800,
            "docubridge.citation_opened": True,
            "docubridge.pre_committed": True,
            "docubridge.oversight_rsr_score": 0.98,
        },
    ):
        citation = ActionCitation(
            doc_id="doc-meridian-oct2026-bank-stmt",
            document_title="meridian-oct2026-operating-bank-statement.pdf",
            field_name="equiplease_8841_final_payoff_wire",
            page_number=2,
            bounding_box=(0.16, 0.54, 0.88, 0.63),
            excerpt="2026-10-12 OUTGOING FEDWIRE $48,210.00 - FINAL PAYOFF - LOAN #8841 - PAID IN FULL",
            vault_uri=bank_vault_uri,
        )
        steps = [
            ActionProgressStep(
                step_id="step_extract_schedule",
                step_number=1,
                label="Zone 2: Extract Note 6 Debt Schedule (Page 4)",
                external_label="Step 1: Debt Schedule Reviewed",
                zone="zone_2_extraction",
                status="completed",
                detail="Extracted 3 existing liabilities totaling $1,800,000.00.",
                external_detail="Reviewed existing loan schedule.",
            ),
            ActionProgressStep(
                step_id="step_cross_doc_wire",
                step_number=2,
                label="Zone 3: Reconcile Bank Statement Page 2 Payoff Wire (#8841)",
                external_label="Step 2: Verified Payoff Wire on Bank Statement",
                zone="zone_3_operational",
                status="input-required",
                detail="Matched Oct 12 $48,210.00 'PAID IN FULL' wire to EquipLease #8841.",
                external_detail="Verifying wire transfer receipt.",
                citations=[citation],
            ),
        ]
        inbox_item = inbox.create_action(
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            title="Waive EquipLease #8841 Payoff Letter via Bank Statement Page 2 Wire Proof",
            external_title="EquipLease Loan #8841 Payoff Verification",
            hint="Approve cross-document payoff waiver backed by Oct 12 $48,210.00 wire on Page 2 [0.16, 0.54].",
            external_hint="Underwriter verifying your Oct 12 payoff wire.",
            assigned_role="underwriter",
            target_view="v_underwriter_exposure",
            risk_level="MEDIUM",
            confidence_score=0.98,
            steps=steps,
            data_model={
                "resolution": {
                    "selectedValue": "WAIVE_VIA_BANK_STATEMENT_WIRE_PROOF",
                },
                "review": {
                    "memo": (
                        "Verified Page 2 [0.16, 0.54] outgoing wire ($48,210.00 'FINAL PAYOFF - "
                        "LOAN #8841 - PAID IN FULL') to EquipLease Financial Corp. Waiving "
                        "separate payoff letter requirement."
                    ),
                },
            },
            pause_for_review=True,
            blind_pre_commitment=True,
        )
        resolve_res = inbox.resolve_action(
            inbox_item.item_id,
            action_request=A2uiClientActionRequest(
                action={
                    "name": "accept",
                    "context": {
                        "decision": "accept",
                        "review_dwell_ms": 4800,
                        "citation_opened": True,
                        "pre_committed": True,
                        "review": {
                            "memo": (
                                "Approved cross-document payoff waiver for EquipLease #8841 "
                                "using Bank Statement Page 2 [0.16, 0.54] wire proof ($48,210.00)."
                            ),
                        },
                    },
                }
            ),
            actor_principal=SENIOR_UNDERWRITER_PRINCIPAL,
            actor_role="underwriter",
            channel="gemini_app",
            agent_spiffe_id=UNDERWRITER_AGENT_SPIFFE_ID,
        )

        for item in state["borrower_checklist"]:
            if item["id"] == "item-5-equip-payoff":
                item["status"] = "waived"
                item["badge_text"] = "WAIVED · PAID ON BANK STMT (PAGE 2)"
                item["reason"] = (
                    "Waived via Cross-Document Proof (approved by Senior Credit Officer): "
                    "Oct 12, 2026 outgoing wire ($48,210.00 'FINAL PAYOFF - LOAN #8841 - PAID IN FULL') "
                    "on Bank Statement Page 2 [0.16, 0.54] confirms zero remaining balance."
                )
                item["auto_waived_reason"] = (
                    "Cross-document payoff wire verified on Oct 2026 Bank Statement Page 2 [0.16, 0.54]."
                )

        if state["cross_doc_discovery"] is not None:
            state["cross_doc_discovery"]["status"] = "waiver_approved"

        state["underwriter_overwatch"] = {
            "status": "approved",
            "headline": "Waiver Approved by Senior Credit Officer (RSR = 0.98 · 0 Remaining Blockers)",
            "recommendation": (
                "Item #5 (EquipLease #8841 Payoff Letter) is officially WAIVED with immutable "
                "RFC 6902 audit trail and WORM source anchor citation (Page 2 [0.16, 0.54])."
            ),
            "inbox_item_id": inbox_item.item_id,
            "audit_id": resolve_res["audit_id"],
            "actor_principal": SENIOR_UNDERWRITER_PRINCIPAL,
            "actor_role": "underwriter",
            "agent_spiffe_id": UNDERWRITER_AGENT_SPIFFE_ID,
            "review_dwell_ms": 4800,
            "citation_opened": True,
            "pre_committed": True,
            "oversight_rsr_score": 0.98,
            "decision": resolve_res["decision"],
            "citation_label": "Page 2 · Region [0.16, 0.54]",
            "doc_title": "meridian-oct2026-operating-bank-statement.pdf",
            "vault_uri": bank_vault_uri,
            "sha256": bank_sha256,
            "resolution_summary": (
                "Loan package LOAN-MERIDIAN-CONSOL-2026 is 100% complete (4 Satisfied, 2 Waived, "
                "0 Required) and cleared for closing."
            ),
        }
        state["borrower_banner"] = {
            "tone": "success",
            "headline": "Application 100% Complete — Cleared to Close! (4 Verified · 2 Waived · 0 Remaining)",
            "message": (
                "Your Senior Underwriter approved the cross-document payoff waiver for EquipLease "
                "#8841 using your Oct 2026 Bank Statement Page 2 wire receipt ($48,210.00). "
                "All 6 consolidation requirements are satisfied or waived!"
            ),
            "agent_note": (
                "Click Beat 4 to distill this cross-document waiver into Agent Gym (v1.1.0) "
                "for zero-touch Day-2 underwriting."
            ),
        }
        state["agent2_policy_status"]["headline"] = (
            "All 6 Consolidation Requirements Resolved (4 Satisfied · 2 Waived · Done = True)"
        )
        state["agent2_policy_status"]["detail"] = (
            "Recorded RFC 6902 governance audit under SPIFFE OBO. Ready to distill "
            "'Example of One' cross-document payoff wire pattern into Agent Gym."
        )
        state["agent2_policy_status"]["checklist_mutations"].append(
            "✓ WAIVED BY OVERWATCH: EquipLease #8841 cleared via Page 2 [0.16, 0.54] wire proof (RSR 0.98)"
        )
        state["beat"] = 3
        state["done"] = True
        _recompute_counts(state)


def _execute_beat_4(
    state: dict[str, Any],
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    gym: AgentGymEngine,
) -> None:
    """Execute Beat 4: Distill cross-doc wire waiver in Agent Gym -> 4/4 Stress Gate -> Compile v1.1.0 -> Day-2 Auto-Approve."""
    wire_anchor = SourceAnchor(
        page_number=2,
        bounding_box=(0.16, 0.54, 0.88, 0.63),
        snippet=(
            "2026-10-12 OUTGOING FEDWIRE #99481204 · BENEFICIARY: EQUIPLEASE FINANCIAL CORP · "
            "AMOUNT: $48,210.00 · OBI/MEMO: FINAL PAYOFF - LOAN #8841 - PAID IN FULL"
        ),
    )
    signal, rule = gym.distill_from_quarantine_correction(
        entity_id=CONSOL_COLLAB_ENTITY_ID,
        doc_id="doc-meridian-oct2026-bank-stmt",
        doctype="consolidation-payoff",
        issuer="EquipLease Financial Corp",
        field_path="payoff_waiver_status",
        before_value="require_external_payoff_letter",
        after_value="waived_via_bank_statement_paid_in_full_wire",
        expert_memo=(
            "When a prior-year debt schedule lists an equipment loan (e.g., EquipLease #8841), "
            "inspect the recent 90-day Operating Bank Statement for an outgoing wire matching "
            "the creditor + loan number with memo 'FINAL PAYOFF' / 'PAID IN FULL'. If verified "
            "with a page/bbox anchor, auto-waive the separate payoff letter; never auto-waive "
            "recurring monthly installment debits."
        ),
        actor_principal=SENIOR_UNDERWRITER_PRINCIPAL,
        actor_role="underwriter",
        agent_spiffe_id=UNDERWRITER_AGENT_SPIFFE_ID,
        source_anchor=wire_anchor,
        category=TribalRuleCategory.EXTRACTION_HINT,
        citation_verified=True,
        review_dwell_ms=4800,
    )
    report = gym.evaluate_candidate_rule(rule.rule_id, auto_promote=True)
    compiled = gym.compile_rules_to_skill(
        doctype="consolidation-payoff",
        issuer_pattern="EquipLease*",
    )

    day2_anchor = SourceAnchor(
        page_number=2,
        bounding_box=(0.18, 0.51, 0.86, 0.61),
        snippet=(
            "2026-10-24 OUTGOING FEDWIRE #99810442 · BENEFICIARY: EQUIPLEASE FINANCIAL CORP · "
            "AMOUNT: $62,400.00 · MEMO: FINAL PAYOFF - LOAN #9914 - PAID IN FULL"
        ),
    )
    day2_entry = LedgerEntry(
        doc_id="doc-apex-consol-day2",
        doctype="consolidation-payoff",
        disposition=Disposition.pending,
        gate=Gate.hitl_review,
        overall_confidence=0.99,
        message="Day-2 Consolidation Package (Auto-promoted via Agent Gym Rule)",
        issuer="EquipLease Financial Corp",
        key_fields={
            "currency": "USD",
            "loan_amount": "$1,450,000.00",
            "payoff_waiver_status": "require_external_payoff_letter",
        },
        source_anchors={
            "loan_amount": day2_anchor,
            "payoff_waiver_status": day2_anchor,
        },
    )
    day2_ingest = ingest_accepted_submission(
        day2_entry,
        entity_id=CONSOL_DAY2_ENTITY_ID,
        context_id="ctx-apex-consol-day2",
        raw_document_bytes=(
            b"Apex Precision Manufacturing LLC - Day-2 Consolidation Package. "
            b"Page 2 [0.18, 0.51] Outgoing Wire $62,400.00 to EquipLease Financial Corp "
            b"FINAL PAYOFF - LOAN #9914 - PAID IN FULL."
        ),
        filename="apex-oct2026-operating-bank-statement.pdf",
        operational_store=store,
        knowledge_catalog=catalog,
        agent_gym=gym,
    )

    stress_cases_detail = [
        {
            "case_id": "stress-1-fedwire-paid-in-full",
            "name": "Fedwire Final Payoff ('PAID IN FULL' + Loan # Match)",
            "expected": "Auto-Waive Payoff Letter",
            "passed": True,
        },
        {
            "case_id": "stress-2-ach-payoff-settlement",
            "name": "ACH Commercial Payoff Settlement ('FINAL PAYOFF' Memo)",
            "expected": "Auto-Waive Payoff Letter",
            "passed": True,
        },
        {
            "case_id": "stress-3-cashiers-check-remittance",
            "name": "Cashier's Check Remittance Advice ('LOAN # PAID IN FULL')",
            "expected": "Auto-Waive Payoff Letter",
            "passed": True,
        },
        {
            "case_id": "stress-4-negative-monthly-installment-guard",
            "name": "Negative Guardrail: Recurring Monthly P&I Installment ($4,850/mo)",
            "expected": "Keep Payoff Letter REQUIRED (Never Waive Partial Payment)",
            "passed": True,
        },
    ]

    state["agent_gym_payoff"] = {
        "rule_id": rule.rule_id,
        "signal_id": signal.signal_id,
        "skill_name": compiled["doctype"],
        "skill_version": compiled["version"],
        "skill_revision_id": compiled["revision_id"],
        "skill_urn": compiled["skill_urn"],
        "stress_cases_passed": report.stress_cases_passed,
        "stress_cases_total": report.stress_cases_total,
        "tier1_deterministic_passed": report.tier1_rule0_passed,
        "tier2_autorater_score": report.tier2_autorater_score,
        "tier3_corridor_feasible": report.tier3_corridor_fa_zero,
        "stress_cases": stress_cases_detail,
        "day2_entity_id": CONSOL_DAY2_ENTITY_ID,
        "day2_borrower": CONSOL_DAY2_BORROWER,
        "day2_status": day2_ingest.entry.disposition.value,
        "day2_gate": day2_ingest.entry.gate.value,
        "day2_latency_ms": 142,
        "human_touchpoints": 0,
        "escalation_tokens": 0,
        "applied_extraction_hints": list(day2_ingest.applied_extraction_hints),
    }
    state["agent1_bridge_status"]["skill_revision"] = (
        f"{compiled['doctype']}@{compiled['version']} ({compiled['revision_id']})"
    )
    state["agent1_bridge_status"]["headline"] = (
        f"Hot-Reloaded {compiled['doctype']}@{compiled['version']} -> Day-2 Deal Auto-Approved in 142 ms"
    )
    state["agent1_bridge_status"]["detail"] = (
        f"Agent 1 (`{WORKHORSE_MODEL}`) now spots cross-document 'PAID IN FULL' payoff wires on "
        f"Day-2 deals (`{CONSOL_DAY2_ENTITY_ID}`) with 0 human touchpoints and 0 `{REASONING_MODEL}` escalation tokens."
    )
    state["agent2_policy_status"]["headline"] = (
        f"Agent Gym Promoted Rule {rule.rule_id} (4/4 Stress Cases) & Compiled Skill v{compiled['version']}"
    )
    state["agent2_policy_status"]["detail"] = (
        "Verified 4/4 synthetic stress cases (including Negative Guardrail blocking waivers on "
        "monthly P&I installments). Day-2 borrower Apex Precision Manufacturing LLC cleared zero-touch."
    )
    state["borrower_banner"] = {
        "tone": "success",
        "headline": "Day-2 Continual Learning Active · Next Borrower Auto-Cleared in 142 ms!",
        "message": (
            f"Agent Gym distilled your Underwriter's waiver into `{compiled['doctype']}@{compiled['version']}` "
            f"(4/4 stress tests passed). When Day-2 borrower {CONSOL_DAY2_BORROWER} uploaded a bank statement "
            "with a 'PAID IN FULL' wire, Document Bridge waived the payoff letter automatically in 142 ms."
        ),
        "agent_note": (
            "Zero human touchpoints · Zero gemini-4-argon escalation tokens · Full WORM & bi-temporal lineage."
        ),
    }
    state["beat"] = 4
    state["done"] = True
    _recompute_counts(state)


def advance_consolidation_collab_to_beat(
    target_beat: int,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore,
    gym: AgentGymEngine,
) -> dict[str, Any]:
    """Advance the collaborative consolidation loan scenario deterministically to `target_beat` (1..4)."""
    global _COLLAB_STATE
    if target_beat < _COLLAB_STATE.get("beat", 0):
        _COLLAB_STATE = _build_initial_collab_state()

    current_beat = int(_COLLAB_STATE.get("beat", 0))
    if current_beat < 1 <= target_beat:
        _execute_beat_1(_COLLAB_STATE, store=store, catalog=catalog, gym=gym)
    if current_beat < 2 <= target_beat:
        _execute_beat_2(_COLLAB_STATE, store=store, catalog=catalog, gym=gym)
    if current_beat < 3 <= target_beat:
        _execute_beat_3(_COLLAB_STATE, inbox=inbox)
    if current_beat < 4 <= target_beat:
        _execute_beat_4(_COLLAB_STATE, store=store, catalog=catalog, gym=gym)

    return copy.deepcopy(_COLLAB_STATE)


def register_consolidation_collab_routes(
    router: APIRouter,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore,
    gym: AgentGymEngine,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> None:
    """Register `/v1/demo/consolidation-collab/{state,advance,reset}` endpoints on the router."""
    del principal_roles, require_auth

    @router.get("/v1/demo/consolidation-collab/state")
    def get_consolidation_collab_state_endpoint() -> dict[str, Any]:
        """Return the current Beat (0..4) state of the Collaborative Commercial Consolidation Loan stage."""
        return get_consolidation_collab_state()

    @router.post("/v1/demo/consolidation-collab/advance")
    def advance_consolidation_collab_endpoint(
        request: Request,
        body: AdvanceConsolidationCollabRequest,
    ) -> dict[str, Any]:
        """Advance the Collaborative Commercial Consolidation Loan stage to Beat 1, 2, 3, or 4."""
        del request
        return advance_consolidation_collab_to_beat(
            body.beat,
            store=store,
            catalog=catalog,
            inbox=inbox,
            gym=gym,
        )

    @router.post("/v1/demo/consolidation-collab/reset")
    def reset_consolidation_collab_endpoint() -> dict[str, Any]:
        """Reset the Collaborative Commercial Consolidation Loan stage back to Beat 0 (0/3 items)."""
        return reset_consolidation_collab_state()
