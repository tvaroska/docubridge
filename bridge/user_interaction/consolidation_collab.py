"""Collaborative Commercial Consolidation Loan (4-Beat Split-Screen & Live PDF Intake).

Implements the interactive Dual-Zone Borrower Portal + Bank Underwriter & Dual-Agent
Board scenario (`LOAN-MERIDIAN-CONSOL-2026` — Meridian Logistics Center LLC `$1.80M`
Commercial Debt Consolidation Facility) across both deterministic beat progression
and live multimodal Gemini PDF uploads (`consolidation_live.py`).
"""

from __future__ import annotations

import copy
import uuid
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
from bridge.user_interaction.consolidation_live import clear_custom_pdf_cache
from bridge.user_interaction.consolidation_live_routes import (
    register_live_ebc_and_pdf_routes,
)
from bridge.user_interaction.consolidation_pdfs import get_demo_pdf_catalog
from bridge.user_interaction.consolidation_state import (
    BRIDGE_AGENT_SPIFFE_ID,
    CONSOL_COLLAB_BORROWER,
    CONSOL_COLLAB_ENTITY_ID,
    CONSOL_COLLAB_FACILITY,
    CONSOL_DAY2_BORROWER,
    CONSOL_DAY2_ENTITY_ID,
    PDF_URLS,
    SENIOR_UNDERWRITER_PRINCIPAL,
    STRESS_CASES_DETAIL,
    UNDERWRITER_AGENT_SPIFFE_ID,
    build_beat_1_checklist,
    build_initial_collab_state,
    compute_deterministic_financial_summary,
)
from bridge.user_interaction.schemas import AdvanceConsolidationCollabRequest
from bridge.user_interaction.surfaces import ingest_accepted_submission
from contract.extraction import SourceAnchor
from contract.wire import Disposition, Gate, LedgerEntry

_compute_deterministic_financial_summary = compute_deterministic_financial_summary
_build_initial_collab_state = build_initial_collab_state


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


_COLLAB_STATE: dict[str, Any] = build_initial_collab_state()


def _get_mutable_collab_state() -> dict[str, Any]:
    """Return the active collaboration state reference for live route updates."""
    return _COLLAB_STATE


def reset_consolidation_collab_state() -> dict[str, Any]:
    """Reset the collaborative consolidation loan state machine back to Beat 0."""
    global _COLLAB_STATE
    clear_custom_pdf_cache()
    _COLLAB_STATE = build_initial_collab_state()
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
    """Execute Beat 1: Upload 2025 Financials + Appraisal -> Mutate Checklist (3 -> 6)."""
    pdf_cat = get_demo_pdf_catalog()
    fin_bytes = pdf_cat["meridian-financials"].pdf_bytes
    debt_schedule_anchor = SourceAnchor(
        page_number=4,
        bounding_box=(0.12, 0.38, 0.84, 0.56),
        snippet=(
            "Note 6 — Schedule of Existing Liabilities to be Consolidated: "
            "(1) First National Bank Commercial Mortgage #FNB-4402: $1,200,000.00; "
            "(2) EquipLease Financial Corp Term Loan #8841: $350,000.00; "
            "(3) DocuBridge Commercial Bank Internal LOC #LOC-9012: $250,000.00."
        ),
    )
    appraisal_anchor = SourceAnchor(
        page_number=1,
        bounding_box=(0.14, 0.22, 0.82, 0.36),
        snippet=(
            "MAI As-Is Market Valuation — Meridian Logistics Center (18,500 m2 Class-A "
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
        ingest_res = ingest_accepted_submission(
            entry,
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            context_id=f"ctx-meridian-consol-{uuid.uuid4().hex[:8]}",
            raw_document_bytes=fin_bytes,
            filename="01_Meridian_2025_Audited_Financials.pdf",
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
        state["borrower_checklist"] = build_beat_1_checklist(vault_uri, sha256_hex)
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
            "documents_processed": 2,
            "active_anchors": [
                {
                    "field": "Note 6 Debt Schedule ($1.80M)",
                    "citation": "Page 4 · Region [0.12, 0.38]",
                    "doc_title": "01_Meridian_2025_Audited_Financials.pdf",
                    "pdf_url": PDF_URLS["financials"],
                    "page_number": 4,
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
    """Execute Beat 2: Upload Oct 2026 Bank Statement + FNB Payoff -> Propose Waiver."""
    pdf_cat = get_demo_pdf_catalog()
    bank_bytes = pdf_cat["meridian-bank-stmt"].pdf_bytes
    fnb_bytes = pdf_cat["fnb-payoff"].pdf_bytes
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
            key_fields={"currency": "USD", "closing_fee": "$48,210.00"},
            source_anchors={"closing_fee": wire_anchor},
        )
        run_ctx = f"ctx-meridian-b2-{uuid.uuid4().hex[:8]}"
        bank_res = ingest_accepted_submission(
            bank_entry,
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            context_id=run_ctx,
            raw_document_bytes=bank_bytes,
            filename="04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
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
            key_fields={"currency": "USD", "principal_amount": "$1,200,000.00"},
            source_anchors={"principal_amount": fnb_anchor},
        )
        fnb_res = ingest_accepted_submission(
            fnb_entry,
            entity_id=CONSOL_COLLAB_ENTITY_ID,
            context_id=run_ctx,
            raw_document_bytes=fnb_bytes,
            filename="03_First_National_Payoff_FNB4402.pdf",
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
                item["pdf_url"] = PDF_URLS["bank_stmt"]
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
                item["pdf_url"] = PDF_URLS["fnb_payoff"]
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
                item["pdf_url"] = PDF_URLS["bank_stmt"]

        state["cross_doc_discovery"] = {
            "status": "waiver_recommended",
            "discovered_in_doc_id": "doc-meridian-oct2026-bank-stmt",
            "discovered_in_filename": "04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
            "pdf_url": PDF_URLS["bank_stmt"],
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
            "doc_title": "04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
            "pdf_url": PDF_URLS["bank_stmt"],
            "page_number": 2,
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
        state["agent1_bridge_status"]["documents_processed"] = 4
        state["agent1_bridge_status"]["active_anchors"].append(
            {
                "field": "EquipLease #8841 Final Payoff Wire ($48,210.00)",
                "citation": "Page 2 · Region [0.16, 0.54]",
                "doc_title": "04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
                "pdf_url": PDF_URLS["bank_stmt"],
                "page_number": 2,
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
    bank_pdf_url = discovery.get("pdf_url", PDF_URLS["bank_stmt"])

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
            document_title="04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
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
                "resolution": {"selectedValue": "WAIVE_VIA_BANK_STATEMENT_WIRE_PROOF"},
                "review": {
                    "memo": (
                        "Verified Page 2 [0.16, 0.54] outgoing wire ($48,210.00 'FINAL PAYOFF - "
                        "LOAN #8841 - PAID IN FULL') to EquipLease Financial Corp."
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
            "doc_title": "04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
            "pdf_url": bank_pdf_url,
            "page_number": 2,
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
                "Distill this cross-document waiver into Agent Gym (v1.1.0) "
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
    """Execute Beat 4: Distill cross-doc wire waiver -> 4/4 Stress Gate -> Compile v1.1.0 -> Day-2 Auto-Approve."""
    pdf_cat = get_demo_pdf_catalog()
    day2_bytes = pdf_cat["apex-day2-bank-stmt"].pdf_bytes
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
        context_id=f"ctx-apex-consol-day2-{uuid.uuid4().hex[:8]}",
        raw_document_bytes=day2_bytes,
        filename="05_Apex_Precision_Day2_Bank_Statement.pdf",
        operational_store=store,
        knowledge_catalog=catalog,
        agent_gym=gym,
    )

    state["agent_gym_payoff"] = {
        "rule_id": rule.rule_id,
        "signal_id": signal.signal_id,
        "skill_name": compiled["doctype"],
        "skill_version": compiled["version"],
        "skill_revision_id": compiled["revision_id"],
        "skill_urn": compiled["skill_urn"],
        "skill_markdown": compiled.get("skill_markdown", ""),
        "stress_cases_passed": report.stress_cases_passed,
        "stress_cases_total": report.stress_cases_total,
        "tier1_deterministic_passed": report.tier1_rule0_passed,
        "tier2_autorater_score": report.tier2_autorater_score,
        "tier3_corridor_feasible": report.tier3_corridor_fa_zero,
        "stress_cases": copy.deepcopy(STRESS_CASES_DETAIL),
        "day2_entity_id": CONSOL_DAY2_ENTITY_ID,
        "day2_borrower": CONSOL_DAY2_BORROWER,
        "day2_status": day2_ingest.entry.disposition.value,
        "day2_gate": day2_ingest.entry.gate.value,
        "day2_latency_ms": 142,
        "day2_pdf_url": PDF_URLS["apex_day2"],
        "day2_vault_uri": day2_ingest.vault_record.vault_uri,
        "day2_sha256": day2_ingest.vault_record.sha256,
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
        _COLLAB_STATE = build_initial_collab_state()

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
    """Register `/v1/demo/consolidation-collab/*` and `/ebc` endpoints on the router."""
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

    register_live_ebc_and_pdf_routes(
        router,
        get_state_ref=_get_mutable_collab_state,
        store=store,
        catalog=catalog,
        inbox=inbox,
        gym=gym,
        execute_beat_1_fn=_execute_beat_1,
        execute_beat_2_fn=_execute_beat_2,
        execute_beat_3_fn=_execute_beat_3,
        execute_beat_4_fn=_execute_beat_4,
        recompute_counts_fn=_recompute_counts,
    )


__all__ = [
    "CONSOL_COLLAB_BORROWER",
    "CONSOL_COLLAB_ENTITY_ID",
    "CONSOL_COLLAB_FACILITY",
    "CONSOL_DAY2_BORROWER",
    "CONSOL_DAY2_ENTITY_ID",
    "advance_consolidation_collab_to_beat",
    "get_consolidation_collab_state",
    "register_consolidation_collab_routes",
    "reset_consolidation_collab_state",
]
