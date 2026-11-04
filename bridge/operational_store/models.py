"""Pydantic data models and constants for the Zone 3 & Zone 4 Operational Store."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from contract.extraction import SourceAnchor

OPERATIONAL_DB_ENV = "BRIDGE_OPERATIONAL_DB_URL"
FACTS_TABLE_NAME = "bridge_operational_facts"
_OP_TIMEOUT_S = 30.0

_MONEY_KEYS = frozenset(
    {
        # Insurance & Benefits
        "premium",
        "deductible",
        "coverage_limit",
        "coverage_limits",
        "insured_value",
        "total_insured_value",
        "claim_amount",
        "reserve_amount",
        "sublimit",
        "net_pay",
        # Banking & Commercial Loan Underwriting
        "loan_amount",
        "principal_amount",
        "facility_limit",
        "appraised_value",
        "collateral_value",
        "origination_fee",
        "closing_fee",
        "rate_lock_fee",
        "annual_debt_service",
        "net_operating_income",
    }
)

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _utc_dt(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _utc_iso(dt: datetime) -> str:
    """Format a datetime as a fixed-width UTC ISO string for lexicographic SQL pushdown."""
    return _utc_dt(dt).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


class SourceTrace(BaseModel):
    """Click-to-source-page traceability attached to every view metric and row."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    doctype: str
    vault_uri: str | None = None
    page_number: int = 1
    bounding_box: tuple[float, float, float, float] | None = None
    snippet: str | None = None
    skill_version: str = "1.0.0"
    skill_revision_id: str = "rev-v1-0-baseline"
    skill_urn: str | None = None
    process_skill_revision_id: str = "rev-v1-0-baseline"
    compiled_rule_ids: tuple[str, ...] = ()
    ontology_revision_id: str = "ont-rev-v1-1-baseline"
    consolidation_edge_id: str | None = None


class ConsolidatedFact(BaseModel):
    """One append-only, bi-temporal fact row in the AlloyDB Operational Store."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    entity_id: str
    context_id: str
    doc_id: str
    doctype: str
    issuer: str | None = None
    field_path: str
    field_value: str
    numeric_value: str | None = None  # Decimal string (2 dp) when monetary
    currency: str = "USD"
    source_priority: int = 10
    recorded_at: datetime
    effective_from: datetime
    version: int = 1
    source_anchor: SourceAnchor | None = None
    vault_uri: str | None = None
    flagged_for_review: bool = False
    skill_version: str = "1.0.0"
    skill_revision_id: str = "rev-v1-0-baseline"
    skill_urn: str | None = None
    process_skill_revision_id: str = "rev-v1-0-baseline"
    compiled_rule_ids: tuple[str, ...] = ()
    ontology_revision_id: str = "ont-rev-v1-1-baseline"
    consolidation_edge_id: str | None = None

    def trace(self) -> SourceTrace:
        """Build the click-to-source-page trace for this fact."""
        anchor = self.source_anchor
        return SourceTrace(
            doc_id=self.doc_id,
            doctype=self.doctype,
            vault_uri=(
                (anchor.artifact_uri if anchor and anchor.artifact_uri else None)
                or self.vault_uri
            ),
            page_number=anchor.page_number if anchor else 1,
            bounding_box=anchor.bounding_box if anchor else None,
            snippet=anchor.snippet if anchor else self.field_value,
            skill_version=self.skill_version,
            skill_revision_id=self.skill_revision_id,
            skill_urn=self.skill_urn,
            process_skill_revision_id=self.process_skill_revision_id,
            compiled_rule_ids=self.compiled_rule_ids,
        )


class FactConflict(BaseModel):
    """A cross-document value conflict flagged by the Consolidation Rules Engine."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    field_path: str
    winning_fact_id: str
    winning_value: str
    winning_doc_id: str
    conflicting_fact_id: str
    conflicting_value: str
    conflicting_doc_id: str
    reason: str
    winning_trace: SourceTrace
    conflicting_trace: SourceTrace


class FinancialSummary(BaseModel):
    """Deterministic Decimal financial rollup across active facts."""

    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    total_premium: str = "0.00"
    total_deductible: str = "0.00"
    total_coverage_limit: str = "0.00"
    total_insured_value: str = "0.00"
    deductible_to_limit_pct: str | None = None


class BrokerQuoteRow(BaseModel):
    """One carrier or submission row in the Role-Based Broker View."""

    model_config = ConfigDict(extra="forbid")

    issuer: str
    doc_id: str
    doctype: str
    premium: str | None = None
    deductible: str | None = None
    coverage_limits: str | None = None
    exclusions: str | None = None
    effective_from: datetime
    recorded_at: datetime
    traces: dict[str, SourceTrace] = {}


class BrokerView(BaseModel):
    """Zone 4 Role-Based Broker View: quotes, coverage gaps, conflicts & source links."""

    model_config = ConfigDict(extra="forbid")

    view_name: str = "v_broker_placement"
    entity_id: str
    as_of_learned: datetime
    quotes: list[BrokerQuoteRow] = []
    coverage_gaps: list[str] = []
    conflicts: list[FactConflict] = []
    financials: FinancialSummary


class ClaimsActiveCoverage(BaseModel):
    """One active coverage item in the Role-Based Claims View on ``loss_date``."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    field_value: str
    numeric_value: str | None = None
    issuer: str | None = None
    doc_id: str
    doctype: str
    effective_from: datetime
    recorded_at: datetime
    trace: SourceTrace


class ClaimsView(BaseModel):
    """Zone 4 Role-Based Claims View: active limits and deductibles on ``loss_date``."""

    model_config = ConfigDict(extra="forbid")

    view_name: str = "v_claims_coverage"
    entity_id: str
    loss_date: datetime
    as_of_learned: datetime
    active_coverages: list[ClaimsActiveCoverage] = []
    active_limit_total: str = "0.00"
    active_deductible_total: str = "0.00"
    endorsement_count: int = 0


class UnderwriterExposureItem(BaseModel):
    """One exposure/schedule metric in the Role-Based Underwriter View."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    field_value: str
    numeric_value: str | None = None
    doc_id: str
    doctype: str
    flagged_for_review: bool = False
    trace: SourceTrace


class UnderwriterView(BaseModel):
    """Zone 4 Role-Based Underwriter View: insured values, exposure & risk ratios."""

    model_config = ConfigDict(extra="forbid")

    view_name: str = "v_underwriter_exposure"
    entity_id: str
    as_of_learned: datetime
    exposure_items: list[UnderwriterExposureItem] = []
    financials: FinancialSummary
    flagged_items_count: int = 0


class BigQuerySyncRecord(BaseModel):
    """One CDC row exported from AlloyDB to BigQuery Open Iceberg tables."""

    model_config = ConfigDict(extra="forbid")

    sync_id: str
    operation: str = "APPEND"
    iceberg_table: str = "docubridge_lakehouse.bq_operational_facts_iceberg"
    entity_id: str
    fact_id: str
    doc_id: str
    doctype: str
    issuer: str | None = None
    field_path: str
    field_value: str
    numeric_value: str | None = None
    currency: str
    recorded_at: datetime
    effective_from: datetime
    vault_uri: str | None = None
    page_number: int = 1


class BigQuerySyncBatch(BaseModel):
    """Batch of append-only CDC records synced from AlloyDB to BigQuery Analytics."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    exported_at: datetime
    iceberg_table: str = "docubridge_lakehouse.bq_operational_facts_iceberg"
    row_count: int
    watermark_recorded_at: datetime | None = None
    records: list[BigQuerySyncRecord] = []
