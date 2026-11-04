"""Zone 6 — Knowledge Catalog Models, Glossary, System Assets & Default Access Policies."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contract.extraction import SourceAnchor

CatalogRole = Literal[
    "broker",
    "claims",
    "underwriter",
    "executive",
    "client",
    "carrier",
]
DeliveryChannel = Literal["gemini_app", "custom_web"]


class AssetLocationZone(StrEnum):
    """Architecture zone where a cataloged data asset resides."""

    INBOUND = "1_inbound_data"
    INTAKE_VALIDATION = "2_intake_validation"
    AUDIT_VAULT = "3_cloud_storage_audit_vault"
    OPERATIONAL_STORE = "3_alloydb_operational_store"
    BUSINESS_VIEW = "4_role_based_business_views"
    BIGQUERY_ICEBERG = "4_bigquery_analytics_iceberg"
    USER_INTERACTION_A2UI = "5_user_interaction_a2ui"
    KNOWLEDGE_CATALOG_GYM = "6_knowledge_catalog_agent_gym"


class QualityRule(BaseModel):
    """Deterministic data quality and validation rule registered in the catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    name: str
    description: str
    target_field: str
    severity: Literal["blocking", "warning"] = "blocking"


class BusinessGlossaryTerm(BaseModel):
    """Standardized business term and metric definition across domains."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    term_id: str
    display_name: str
    domain: Literal["placement", "claims", "underwriting", "analytics", "governance"]
    definition: str
    canonical_fields: tuple[str, ...] = ()
    calculation_formula: str | None = None
    quality_rules: tuple[QualityRule, ...] = ()
    confidential: bool = False


class CatalogAsset(BaseModel):
    """Unified directory entry answering 'What is Where' across all zones."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    name: str
    asset_type: Literal[
        "source_document",
        "vault_archive",
        "operational_table",
        "business_view",
        "bigquery_iceberg_table",
        "a2ui_surface",
    ]
    zone: AssetLocationZone
    physical_uri: str
    owner_domain: str
    classification: Literal[
        "public", "client_shared", "internal_only", "restricted_pii"
    ] = "internal_only"
    schema_fields: tuple[str, ...] = ()
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LineageNode(BaseModel):
    """A node in the end-to-end provenance graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    node_type: Literal[
        "source_pdf",
        "vault_object",
        "consolidated_fact",
        "business_view",
        "bigquery_table",
        "a2ui_widget",
    ]
    label: str
    uri: str
    zone: AssetLocationZone
    metadata: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    """Directed provenance edge from upstream source to downstream consumer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream_id: str
    downstream_id: str
    transformation: str


class LineageTrace(BaseModel):
    """End-to-end lineage chain from a downstream metric back to source PDF page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    field_path: str
    current_value: str
    source_doc_id: str
    source_doctype: str
    vault_uri: str
    source_anchor: SourceAnchor | None = None
    recorded_at: datetime
    effective_from: datetime
    consolidation_rule: str
    skill_version: str = "1.0.0"
    skill_revision_id: str = "rev-v1-0-baseline"
    skill_urn: str | None = None
    process_skill_revision_id: str = "rev-v1-0-baseline"
    compiled_rule_ids: tuple[str, ...] = ()
    ontology_revision_id: str = "ont-rev-v1-1-baseline"
    consolidation_edge_id: str | None = None
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]


class AccessPolicy(BaseModel):
    """Role- and channel-based governance policy defining who can see what."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: CatalogRole
    allowed_channels: tuple[DeliveryChannel, ...]
    allowed_views: tuple[str, ...]
    allow_internal_conflict_notes: bool
    allow_competitor_quotes: bool
    allow_raw_vault_uri: bool
    redacted_fields: tuple[str, ...] = ()


class AccessDecision(BaseModel):
    """Deterministic authorization decision evaluated against catalog policies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    role: CatalogRole
    channel: DeliveryChannel
    target_view: str
    reason: str
    agent_spiffe_id: str | None = None
    svid_verified: bool = False
    allow_internal_conflict_notes: bool = False
    allow_competitor_quotes: bool = False
    allow_raw_vault_uri: bool = False
    redacted_fields: tuple[str, ...] = ()


class AgentSemanticContext(BaseModel):
    """Grounded semantic context bundle supplied to a Domain Agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: CatalogRole
    channel: DeliveryChannel
    target_view: str
    agent_spiffe_id: str | None = None
    glossary_terms: tuple[BusinessGlossaryTerm, ...]
    visible_assets: tuple[CatalogAsset, ...]
    access_decision: AccessDecision


DEFAULT_GLOSSARY_TERMS: tuple[BusinessGlossaryTerm, ...] = (
    BusinessGlossaryTerm(
        term_id="term.premium",
        display_name="Annualized Policy Premium",
        domain="placement",
        definition=(
            "Total annualized premium for a carrier quote or bound policy, "
            "computed deterministically in fixed-point Decimal."
        ),
        canonical_fields=("premium", "deductible", "coverage_limit"),
        calculation_formula="sum(active_facts.premium)",
        quality_rules=(
            QualityRule(
                rule_id="qr.non_negative_decimal",
                name="Non-negative Decimal currency",
                description="All financial amounts must be exact Decimal >= 0.",
                target_field="premium",
                severity="blocking",
            ),
            QualityRule(
                rule_id="qr.source_page_anchor",
                name="Source PDF page citation required",
                description="Every financial metric must link to a source PDF page.",
                target_field="premium",
                severity="blocking",
            ),
        ),
    ),
    BusinessGlossaryTerm(
        term_id="term.deductible_to_limit_ratio",
        display_name="Deductible-to-Limit Exposure Ratio",
        domain="underwriting",
        definition=(
            "Percentage ratio of total deductible to total coverage limit or "
            "total insured value across active policy schedules."
        ),
        canonical_fields=("deductible", "coverage_limit", "total_insured_value"),
        calculation_formula="(total_deductible / total_coverage_limit) * 100",
        quality_rules=(
            QualityRule(
                rule_id="qr.source_priority_hierarchy",
                name="Source Priority Hierarchy",
                description=(
                    "Signed Endorsement (40) > Bound Policy (30) > Carrier Quote (20) "
                    "> Application (10)."
                ),
                target_field="deductible",
                severity="blocking",
            ),
        ),
    ),
    BusinessGlossaryTerm(
        term_id="term.active_coverage_as_of_loss",
        display_name="Active Coverage on Loss Date",
        domain="claims",
        definition=(
            "Policy limits and deductibles that were legally effective on the "
            "reported date of loss using bi-temporal history."
        ),
        canonical_fields=("coverage_limit", "deductible"),
        calculation_formula="effective_from <= loss_date AND recorded_at <= query_time",
    ),
    BusinessGlossaryTerm(
        term_id="term.cross_document_conflict",
        display_name="Cross-Document Discrepancy Flag",
        domain="governance",
        definition=(
            "Internal audit alert raised when two documents for the same entity "
            "report conflicting values for the same canonical field."
        ),
        canonical_fields=("field_path", "winning_value", "conflicting_value"),
        confidential=True,
    ),
    BusinessGlossaryTerm(
        term_id="term.loan_to_value_ratio",
        display_name="Commercial Loan-to-Value (LTV) & Fee Exposure",
        domain="underwriting",
        definition=(
            "Percentage ratio of commercial loan principal to appraised collateral "
            "value alongside origination and rate-lock fee exposure."
        ),
        canonical_fields=("loan_amount", "appraised_value", "origination_fee"),
        calculation_formula="(loan_amount / appraised_value) * 100",
    ),
    BusinessGlossaryTerm(
        term_id="term.addendum_priority_hierarchy",
        display_name="Credit Addendum & Closing Disclosure Authority",
        domain="governance",
        definition=(
            "Deterministic banking authority hierarchy ensuring executed addenda "
            "and closing disclosures supersede base credit agreements and term sheets."
        ),
        canonical_fields=("origination_fee", "loan_amount"),
        calculation_formula=(
            "Signed Addendum / Closing Disclosure (40) > Credit Agreement (30) "
            "> Term Sheet / Appraisal (20) > Application (10)"
        ),
    ),
)

DEFAULT_SYSTEM_ASSETS: tuple[CatalogAsset, ...] = (
    CatalogAsset(
        asset_id="asset.cloud_storage_audit_vault",
        name="Cloud Storage Audit Vault",
        asset_type="vault_archive",
        zone=AssetLocationZone.AUDIT_VAULT,
        physical_uri="gs://docubridge-audit-vault/vault/",
        owner_domain="enterprise-governance",
        classification="internal_only",
        schema_fields=(
            "context_id",
            "doc_id",
            "version",
            "sha256",
            "vault_uri",
            "locked_at",
        ),
    ),
    CatalogAsset(
        asset_id="asset.alloydb_operational_store",
        name="AlloyDB Bi-Temporal Operational Store",
        asset_type="operational_table",
        zone=AssetLocationZone.OPERATIONAL_STORE,
        physical_uri="alloydb://docubridge-enterprise-data/operational/bridge_operational_facts",
        owner_domain="data-engineering",
        classification="internal_only",
        schema_fields=(
            "fact_id",
            "entity_id",
            "doc_id",
            "doctype",
            "field_path",
            "field_value",
            "numeric_value",
            "recorded_at",
            "effective_from",
            "vault_uri",
        ),
    ),
    CatalogAsset(
        asset_id="asset.view_broker_placement",
        name="Broker View (v_broker_placement)",
        asset_type="business_view",
        zone=AssetLocationZone.BUSINESS_VIEW,
        physical_uri="alloydb://docubridge-enterprise-data/views/v_broker_placement",
        owner_domain="placement",
        classification="client_shared",
        schema_fields=(
            "entity_id",
            "quotes",
            "coverage_gaps",
            "financials",
        ),
    ),
    CatalogAsset(
        asset_id="asset.view_claims_coverage",
        name="Claims View (v_claims_coverage)",
        asset_type="business_view",
        zone=AssetLocationZone.BUSINESS_VIEW,
        physical_uri="alloydb://docubridge-enterprise-data/views/v_claims_coverage",
        owner_domain="claims",
        classification="client_shared",
        schema_fields=(
            "entity_id",
            "loss_date",
            "active_coverages",
            "active_limit_total",
            "active_deductible_total",
        ),
    ),
    CatalogAsset(
        asset_id="asset.view_underwriter_exposure",
        name="Underwriter View (v_underwriter_exposure)",
        asset_type="business_view",
        zone=AssetLocationZone.BUSINESS_VIEW,
        physical_uri="alloydb://docubridge-enterprise-data/views/v_underwriter_exposure",
        owner_domain="underwriting",
        classification="internal_only",
        schema_fields=(
            "entity_id",
            "exposure_items",
            "financials",
            "flagged_items_count",
        ),
    ),
    CatalogAsset(
        asset_id="asset.bigquery_iceberg_facts",
        name="BigQuery Analytics Sync (Open Iceberg)",
        asset_type="bigquery_iceberg_table",
        zone=AssetLocationZone.BIGQUERY_ICEBERG,
        physical_uri="bigquery://docubridge_lakehouse.bq_operational_facts_iceberg",
        owner_domain="enterprise-analytics",
        classification="internal_only",
        schema_fields=(
            "sync_id",
            "entity_id",
            "fact_id",
            "field_path",
            "numeric_value",
            "recorded_at",
            "effective_from",
            "vault_uri",
            "page_number",
        ),
    ),
    CatalogAsset(
        asset_id="asset.a2ui_gemini_app",
        name="Gemini App (Internal A2UI Surface)",
        asset_type="a2ui_surface",
        zone=AssetLocationZone.USER_INTERACTION_A2UI,
        physical_uri="a2ui://gemini-app/internal",
        owner_domain="agent-platform",
        classification="internal_only",
    ),
    CatalogAsset(
        asset_id="asset.a2ui_custom_web",
        name="Custom Web Portal (External A2UI Surface)",
        asset_type="a2ui_surface",
        zone=AssetLocationZone.USER_INTERACTION_A2UI,
        physical_uri="a2ui://custom-web/external",
        owner_domain="digital-client-portal",
        classification="client_shared",
    ),
    CatalogAsset(
        asset_id="asset.gym_feedback_diff_log",
        name="Agent Gym Feedback & Diff Log",
        asset_type="operational_table",
        zone=AssetLocationZone.KNOWLEDGE_CATALOG_GYM,
        physical_uri="alloydb://docubridge-enterprise-data/gym/feedback_diff_log",
        owner_domain="agent-gym-governance",
        classification="internal_only",
        schema_fields=(
            "signal_id",
            "source_zone",
            "issuer",
            "doctype",
            "field_path",
            "patches",
        ),
    ),
    CatalogAsset(
        asset_id="asset.gym_benchmark_test_suite",
        name="Agent Gym Benchmark & Synthetic Test Suite",
        asset_type="operational_table",
        zone=AssetLocationZone.KNOWLEDGE_CATALOG_GYM,
        physical_uri="alloydb://docubridge-enterprise-data/gym/benchmark_test_suite",
        owner_domain="agent-gym-governance",
        classification="internal_only",
        schema_fields=(
            "case_id",
            "perturbation_type",
            "baseline_passed",
            "candidate_passed",
        ),
    ),
    CatalogAsset(
        asset_id="asset.gym_tribal_rulebook",
        name="Active Tribal Rulebook & Entity Alias Map",
        asset_type="operational_table",
        zone=AssetLocationZone.KNOWLEDGE_CATALOG_GYM,
        physical_uri="alloydb://docubridge-enterprise-data/gym/active_tribal_rulebook",
        owner_domain="agent-gym-governance",
        classification="internal_only",
        schema_fields=(
            "rule_id",
            "category",
            "issuer_pattern",
            "doctype",
            "instruction",
            "status",
        ),
    ),
    CatalogAsset(
        asset_id="asset.gym_trust_policy_registry",
        name="Programmable Trust Policy Registry",
        asset_type="operational_table",
        zone=AssetLocationZone.KNOWLEDGE_CATALOG_GYM,
        physical_uri="alloydb://docubridge-enterprise-data/gym/trust_policy_registry",
        owner_domain="agent-gym-governance",
        classification="internal_only",
        schema_fields=(
            "slice_id",
            "issuer_pattern",
            "autonomy_level",
            "consecutive_matches",
        ),
    ),
)

DEFAULT_ACCESS_POLICIES: dict[CatalogRole, AccessPolicy] = {
    "broker": AccessPolicy(
        role="broker",
        allowed_channels=("gemini_app",),
        allowed_views=(
            "v_broker_placement",
            "v_claims_coverage",
            "v_underwriter_exposure",
        ),
        allow_internal_conflict_notes=True,
        allow_competitor_quotes=True,
        allow_raw_vault_uri=True,
    ),
    "claims": AccessPolicy(
        role="claims",
        allowed_channels=("gemini_app",),
        allowed_views=("v_claims_coverage", "v_broker_placement"),
        allow_internal_conflict_notes=True,
        allow_competitor_quotes=False,
        allow_raw_vault_uri=True,
    ),
    "underwriter": AccessPolicy(
        role="underwriter",
        allowed_channels=("gemini_app",),
        allowed_views=(
            "v_underwriter_exposure",
            "v_broker_placement",
            "v_claims_coverage",
        ),
        allow_internal_conflict_notes=True,
        allow_competitor_quotes=True,
        allow_raw_vault_uri=True,
    ),
    "executive": AccessPolicy(
        role="executive",
        allowed_channels=("gemini_app",),
        allowed_views=(
            "v_broker_placement",
            "v_claims_coverage",
            "v_underwriter_exposure",
        ),
        allow_internal_conflict_notes=True,
        allow_competitor_quotes=True,
        allow_raw_vault_uri=True,
    ),
    "client": AccessPolicy(
        role="client",
        allowed_channels=("custom_web",),
        allowed_views=("v_broker_placement", "v_claims_coverage"),
        allow_internal_conflict_notes=False,
        allow_competitor_quotes=True,
        allow_raw_vault_uri=False,
        redacted_fields=("broker_commission_pct", "internal_underwriting_notes"),
    ),
    "carrier": AccessPolicy(
        role="carrier",
        allowed_channels=("custom_web",),
        allowed_views=("v_broker_placement",),
        allow_internal_conflict_notes=False,
        allow_competitor_quotes=False,
        allow_raw_vault_uri=False,
        redacted_fields=(
            "broker_commission_pct",
            "internal_underwriting_notes",
            "competitor_carrier_quotes",
        ),
    ),
}
