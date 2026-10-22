"""Zone 5 User Interaction: deterministic multi-domain showcase scenario seeding."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridge.agent_gym import AgentGymEngine, agent_gym_from_env
from bridge.knowledge_catalog import KnowledgeCatalog, knowledge_catalog_from_env
from bridge.operational_store import OperationalStore, operational_store_from_env
from bridge.user_interaction.surfaces import ingest_accepted_submission
from bridge.vault import AuditVaultStore, vault_from_env
from contract.enums import Disposition, Gate
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry


def seed_demo_scenarios(
    *,
    vault_store: AuditVaultStore | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
) -> dict[str, Any]:
    """Seed baseline bi-temporal facts and 6-hop lineage across Banking, Mortgage, and Insurance."""
    vault = vault_store or vault_from_env()
    store = operational_store or operational_store_from_env()
    catalog = knowledge_catalog or knowledge_catalog_from_env(operational_store=store)
    gym = agent_gym_from_env(agent_gym)

    t0 = datetime(2026, 10, 19, 18, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 19, 20, 0, 0, tzinfo=UTC)
    eff_date = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
    seeded_entities: list[str] = []

    # 1. Banking: Commercial Real Estate Loan (LOAN-MERIDIAN-001)
    if not store.list_facts("LOAN-MERIDIAN-001"):
        ts_entry = LedgerEntry(
            doc_id="doc-meridian-ts-v1",
            doctype="term-sheet",
            issuer="Syndicated Lenders Group",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted CRE Term Sheet & MAI Appraisal",
            key_fields={
                "currency": "USD",
                "loan_amount": "$45,000,000.00",
                "appraised_value": "$62,500,000.00",
                "origination_fee": "$50,000.00",
                "net_operating_income": "$4,185,000.00",
            },
            source_anchors={
                "origination_fee": SourceAnchor(
                    page_number=1,
                    bounding_box=(0.14, 0.31, 0.48, 0.36),
                    snippet="Base Origination Fee: $50,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-ts-v1/v1/ts.pdf",
                ),
                "loan_amount": SourceAnchor(
                    page_number=2,
                    bounding_box=(0.15, 0.28, 0.52, 0.33),
                    snippet="Senior Term Loan Facility: $45,000,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-ts-v1/v1/ts.pdf",
                ),
                "appraised_value": SourceAnchor(
                    page_number=11,
                    bounding_box=(0.18, 0.52, 0.60, 0.58),
                    snippet="As-Stabilized Appraised Collateral Value: $62,500,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-ts-v1/v1/ts.pdf",
                ),
            },
            recorded_at=t0,
            effective_from=eff_date,
        )
        ingest_accepted_submission(
            ts_entry,
            entity_id="LOAN-MERIDIAN-001",
            context_id="ctx-meridian-001",
            raw_document_bytes=b"%PDF-1.7 meridian-term-sheet",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
            recorded_at=t0,
            effective_from=eff_date,
        )
        add_entry = LedgerEntry(
            doc_id="doc-meridian-addendum-v2",
            doctype="closing-disclosure",
            issuer="Syndicated Lenders Group",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted Schedule B Fee Addendum",
            key_fields={
                "currency": "USD",
                "origination_fee": "$100,000.00",
            },
            source_anchors={
                "origination_fee": SourceAnchor(
                    page_number=4,
                    bounding_box=(0.12, 0.44, 0.58, 0.51),
                    snippet="Schedule B Origination & Rate-Lock Fee: $100,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-addendum-v2/v1/addendum.pdf",
                ),
            },
            recorded_at=t1,
            effective_from=eff_date,
        )
        ingest_accepted_submission(
            add_entry,
            entity_id="LOAN-MERIDIAN-001",
            context_id="ctx-meridian-001",
            raw_document_bytes=b"%PDF-1.7 meridian-schedule-b-addendum",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
            recorded_at=t1,
            effective_from=eff_date,
        )
        seeded_entities.append("LOAN-MERIDIAN-001")

    # 2. Mortgage: Residential Jumbo Mortgage & TRID Closing (MORTGAGE-WESTBROOK-2026)
    if not store.list_facts("MORTGAGE-WESTBROOK-2026"):
        le_entry = LedgerEntry(
            doc_id="doc-westbrook-le-v1",
            doctype="term-sheet",
            issuer="FirstNational Home Loans",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted Initial TRID Loan Estimate & Form 1004 Appraisal",
            key_fields={
                "currency": "USD",
                "loan_amount": "$1,850,000.00",
                "appraised_value": "$2,400,000.00",
                "origination_fee": "$4,250.00",
                "annual_debt_service": "$138,400.00",
            },
            source_anchors={
                "origination_fee": SourceAnchor(
                    page_number=2,
                    bounding_box=(0.11, 0.24, 0.49, 0.29),
                    snippet="Section A. Origination Charges (0.23% Points): $4,250.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/MORTGAGE-WESTBROOK-2026/doc-westbrook-le-v1/v1/le.pdf",
                ),
                "loan_amount": SourceAnchor(
                    page_number=1,
                    bounding_box=(0.14, 0.19, 0.51, 0.24),
                    snippet="30-Year Fixed Jumbo Loan Amount: $1,850,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/MORTGAGE-WESTBROOK-2026/doc-westbrook-le-v1/v1/le.pdf",
                ),
                "appraised_value": SourceAnchor(
                    page_number=6,
                    bounding_box=(0.16, 0.61, 0.62, 0.67),
                    snippet="Form 1004 Uniform Residential Appraisal Market Value: $2,400,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/MORTGAGE-WESTBROOK-2026/doc-westbrook-le-v1/v1/le.pdf",
                ),
            },
            recorded_at=t0,
            effective_from=eff_date,
        )
        ingest_accepted_submission(
            le_entry,
            entity_id="MORTGAGE-WESTBROOK-2026",
            context_id="ctx-westbrook-2026",
            raw_document_bytes=b"%PDF-1.7 westbrook-trid-loan-estimate",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
            recorded_at=t0,
            effective_from=eff_date,
        )
        cd_entry = LedgerEntry(
            doc_id="doc-westbrook-cd-v2",
            doctype="closing-disclosure",
            issuer="FirstNational Home Loans",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted Final TRID Closing Disclosure & Rate-Lock Extension Addendum",
            key_fields={
                "currency": "USD",
                "origination_fee": "$8,500.00",
            },
            source_anchors={
                "origination_fee": SourceAnchor(
                    page_number=3,
                    bounding_box=(0.11, 0.48, 0.59, 0.54),
                    snippet="Closing Cost Details — Section A Origination + 45-Day Rate-Lock Extension: $8,500.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/MORTGAGE-WESTBROOK-2026/doc-westbrook-cd-v2/v1/cd.pdf",
                ),
            },
            recorded_at=t1,
            effective_from=eff_date,
        )
        ingest_accepted_submission(
            cd_entry,
            entity_id="MORTGAGE-WESTBROOK-2026",
            context_id="ctx-westbrook-2026",
            raw_document_bytes=b"%PDF-1.7 westbrook-trid-closing-disclosure",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
            recorded_at=t1,
            effective_from=eff_date,
        )
        seeded_entities.append("MORTGAGE-WESTBROOK-2026")

    # 3. Insurance: Commercial Property Placement (terrepower-coastal-2027)
    if not store.list_facts("terrepower-coastal-2027"):
        quote_entry = LedgerEntry(
            doc_id="doc-terrepower-quote-v1",
            doctype="quote",
            issuer="Chubb Syndicate 2987",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted Chubb Primary Property Quote & SOV",
            key_fields={
                "currency": "USD",
                "coverage_limit": "$50,000,000.00",
                "total_insured_value": "$142,500,000.00",
                "deductible": "$50,000.00",
                "premium": "$418,500.00",
            },
            source_anchors={
                "deductible": SourceAnchor(
                    page_number=1,
                    bounding_box=(0.14, 0.31, 0.48, 0.36),
                    snippet="All Other Perils Base Deductible: $50,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/terrepower-coastal-2027/doc-terrepower-quote-v1/v1/quote.pdf",
                ),
                "coverage_limit": SourceAnchor(
                    page_number=2,
                    bounding_box=(0.15, 0.28, 0.52, 0.33),
                    snippet="Primary Layer Property Coverage Limit: $50,000,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/terrepower-coastal-2027/doc-terrepower-quote-v1/v1/quote.pdf",
                ),
                "total_insured_value": SourceAnchor(
                    page_number=11,
                    bounding_box=(0.18, 0.52, 0.60, 0.58),
                    snippet="Schedule of Values (SOV) Total Insured Value: $142,500,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/terrepower-coastal-2027/doc-terrepower-quote-v1/v1/quote.pdf",
                ),
            },
            recorded_at=t0,
            effective_from=eff_date,
        )
        ingest_accepted_submission(
            quote_entry,
            entity_id="terrepower-coastal-2027",
            context_id="ctx-terrepower-2027",
            raw_document_bytes=b"%PDF-1.7 terrepower-chubb-quote",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
            recorded_at=t0,
            effective_from=eff_date,
        )
        end_entry = LedgerEntry(
            doc_id="doc-terrepower-endorsement-v2",
            doctype="endorsement",
            issuer="Chubb Syndicate 2987",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            overall_confidence=0.99,
            message="Accepted Schedule B Coastal Wind Endorsement",
            key_fields={
                "currency": "USD",
                "deductible": "$100,000.00",
            },
            source_anchors={
                "deductible": SourceAnchor(
                    page_number=4,
                    bounding_box=(0.12, 0.44, 0.58, 0.51),
                    snippet="Schedule B Named Windstorm Deductible Override: $100,000.00",
                    artifact_uri="gs://docubridge-audit-vault/vault/terrepower-coastal-2027/doc-terrepower-endorsement-v2/v1/endorsement.pdf",
                ),
            },
            recorded_at=t1,
            effective_from=eff_date,
        )
        ingest_accepted_submission(
            end_entry,
            entity_id="terrepower-coastal-2027",
            context_id="ctx-terrepower-2027",
            raw_document_bytes=b"%PDF-1.7 terrepower-wind-endorsement",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
            recorded_at=t1,
            effective_from=eff_date,
        )
        seeded_entities.append("terrepower-coastal-2027")

    return {
        "seeded_entities": seeded_entities,
        "available_scenarios": [
            "LOAN-MERIDIAN-001",
            "MORTGAGE-WESTBROOK-2026",
            "terrepower-coastal-2027",
        ],
    }
