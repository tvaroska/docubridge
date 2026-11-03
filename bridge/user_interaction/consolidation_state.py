"""Initial state and deterministic Decimal underwriting math for Consolidation Collab."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.user_interaction.consolidation_live import build_initial_live_telemetry

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

DOCTYPES: dict[str, str] = {
    "financials": "-".join(("cre", "credit", "agreement")),
    "appraisal": "-".join(("mai", "appraisal")),
    "bank_statement": "closing-disclosure",
    "payoff_letter": "consolidation-payoff",
}

PDF_URLS: dict[str, str] = {
    "financials": "/v1/demo/consolidation-collab/pdfs/01_Meridian_2025_Audited_Financials.pdf",
    "appraisal": "/v1/demo/consolidation-collab/pdfs/02_Meridian_MAI_Appraisal.pdf",
    "fnb_payoff": "/v1/demo/consolidation-collab/pdfs/03_First_National_Payoff_FNB4402.pdf",
    "bank_stmt": "/v1/demo/consolidation-collab/pdfs/04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
    "apex_day2": "/v1/demo/consolidation-collab/pdfs/05_Apex_Precision_Day2_Bank_Statement.pdf",
}


def compute_deterministic_financial_summary() -> dict[str, Any]:
    """Compute exact Decimal LTV, DSCR, and liability schedule totals (`Rule 0` firewall)."""
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


def build_initial_collab_state() -> dict[str, Any]:
    """Return the Beat 0 initial state for the Collaborative Consolidation Loan stage."""
    checklist: list[dict[str, Any]] = [
        {
            "id": "item-1-financials",
            "label": "2025 Audited Financials & Note 6 Debt Schedule",
            "doctype": DOCTYPES["financials"],
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
            "pdf_url": PDF_URLS["financials"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-2-bank-stmt",
            "label": "Oct 2026 Operating Bank Statement (90-Day Liquidity & Cash Flow)",
            "doctype": DOCTYPES["bank_statement"],
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
            "pdf_url": PDF_URLS["bank_stmt"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-3-appraisal",
            "label": "MAI Commercial Property Appraisal ($2.60M Industrial Warehouse)",
            "doctype": DOCTYPES["appraisal"],
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
            "pdf_url": PDF_URLS["appraisal"],
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
        "financial_summary": compute_deterministic_financial_summary(),
        "live_telemetry": build_initial_live_telemetry(),
    }


STRESS_CASES_DETAIL: list[dict[str, Any]] = [
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


def build_beat_1_checklist(vault_uri: str, sha256_hex: str) -> list[dict[str, Any]]:
    """Construct the 6-item borrower checklist spawned from Note 6 Debt Schedule (Page 4)."""
    return [
        {
            "id": "item-1-financials",
            "label": "2025 Audited Financials & Note 6 Debt Schedule",
            "doctype": DOCTYPES["financials"],
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
            "pdf_url": PDF_URLS["financials"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-2-bank-stmt",
            "label": "Oct 2026 Operating Bank Statement (90-Day Liquidity & Cash Flow)",
            "doctype": DOCTYPES["bank_statement"],
            "status": "required",
            "emergent": False,
            "badge_text": "REQUIRED",
            "reason": (
                "Upload October 2026 operating account statement to verify cash flow "
                "and recent debt payments."
            ),
            "source_citation": None,
            "page_number": None,
            "bounding_box": None,
            "doc_id": None,
            "vault_uri": None,
            "sha256": None,
            "pdf_url": PDF_URLS["bank_stmt"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-3-appraisal",
            "label": "MAI Commercial Property Appraisal ($2.60M Industrial Warehouse)",
            "doctype": DOCTYPES["appraisal"],
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
            "pdf_url": PDF_URLS["appraisal"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-4-fnb-payoff",
            "label": "Payoff Letter — First National Bank Mortgage (#FNB-4402 · $1,200,000.00)",
            "doctype": DOCTYPES["payoff_letter"],
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
            "pdf_url": PDF_URLS["financials"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-5-equip-payoff",
            "label": "Payoff Letter — EquipLease Financial Corp (#8841 · $350,000.00)",
            "doctype": DOCTYPES["payoff_letter"],
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
            "pdf_url": PDF_URLS["financials"],
            "auto_waived_reason": None,
        },
        {
            "id": "item-6-internal-loc",
            "label": "Internal Revolving LOC Payoff — DocuBridge Bank (#LOC-9012 · $250,000.00)",
            "doctype": DOCTYPES["payoff_letter"],
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
            "pdf_url": PDF_URLS["financials"],
            "auto_waived_reason": (
                "On-us facility #LOC-9012 verified directly against internal core banking feed."
            ),
        },
    ]
