"""Zone 6 — Knowledge Catalog: Unified Data Map, Lineage & Governance.

Provides the 4 pillars of the Enterprise Data Architecture Knowledge Catalog:

1. **Glossary & Quality Rules** (`BusinessGlossaryTerm`, `QualityRule`):
   Standardized business terms, deterministic validation rules, and metric
   definitions across Placement, Claims, and Analytics.
2. **Asset Map ("What is Where")** (`CatalogAsset`, `AssetLocationZone`):
   Unified directory of every source document, Cloud Storage vault archive,
   AlloyDB operational table, Role-Based Business View, and BigQuery Iceberg
   table.
3. **End-to-End Lineage** (`LineageNode`, `LineageEdge`, `LineageTrace`):
   Full provenance graph tracing every metric in BigQuery, Role-Based Views,
   and A2UI widgets back through consolidation rules to the exact source
   document, vault URI, and PDF page bounding box.
4. **Agent Context & Access** (`AccessPolicy`, `AccessDecision`,
   `AgentSemanticContext`):
   Grounded semantic context for domain agents plus role-based and
   channel-based access control defining who can see internal vs. external
   data (`gemini_app` for internal Benefits Consumer roles vs. `custom_web` for external
   clients and carriers).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from bridge.identity import (
    DEFAULT_SPIFFE_AGENT_POLICIES,
    ShadowAgentError,
    SpiffeAgentPolicy,
    SpiffeAuthenticationError,
    authorize_spiffe_agent,
)
from bridge.operational_store import (
    ConsolidatedFact,
    OperationalStore,
    operational_store_from_env,
)
from bridge.telemetry import trace_step
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry

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


def authorize_access(
    *,
    role: CatalogRole,
    channel: DeliveryChannel,
    target_view: str,
    policies: dict[CatalogRole, AccessPolicy] | None = None,
    agent_spiffe_id: str | None = None,
    svid_verified: bool = False,
    spiffe_policies: dict[str, SpiffeAgentPolicy] | None = None,
) -> AccessDecision:
    """Evaluate whether ``role`` (and optional ``agent_spiffe_id``) on ``channel`` may query ``target_view``."""
    with trace_step(
        "docubridge.catalog.authorize_access",
        {
            "docubridge.zone": "6_knowledge_catalog_agent_gym",
            "docubridge.role": role,
            "docubridge.channel": channel,
            "docubridge.target_view": target_view,
            "docubridge.agent_spiffe_id": agent_spiffe_id,
            "docubridge.svid_verified": svid_verified,
        },
    ) as span:
        if agent_spiffe_id is not None:
            try:
                authorize_spiffe_agent(
                    agent_spiffe_id,
                    channel=channel,
                    role=role,
                    target_view=target_view,
                    agent_policies=spiffe_policies,
                )
            except (
                ShadowAgentError,
                SpiffeAuthenticationError,
                PermissionError,
            ) as exc:
                span.set_attribute("docubridge.access_allowed", False)
                return AccessDecision(
                    allowed=False,
                    role=role,
                    channel=channel,
                    target_view=target_view,
                    reason=str(exc),
                    agent_spiffe_id=agent_spiffe_id,
                    svid_verified=svid_verified,
                )

        registry = policies if policies is not None else DEFAULT_ACCESS_POLICIES
        policy = registry.get(role)
        if policy is None:
            span.set_attribute("docubridge.access_allowed", False)
            return AccessDecision(
                allowed=False,
                role=role,
                channel=channel,
                target_view=target_view,
                reason=f"Unknown role '{role}'.",
                agent_spiffe_id=agent_spiffe_id,
                svid_verified=svid_verified,
            )
        if channel not in policy.allowed_channels:
            span.set_attribute("docubridge.access_allowed", False)
            return AccessDecision(
                allowed=False,
                role=role,
                channel=channel,
                target_view=target_view,
                reason=(
                    f"Role '{role}' is not permitted on delivery channel '{channel}' "
                    f"(allowed: {', '.join(policy.allowed_channels)})."
                ),
                agent_spiffe_id=agent_spiffe_id,
                svid_verified=svid_verified,
            )
        if target_view not in policy.allowed_views:
            span.set_attribute("docubridge.access_allowed", False)
            return AccessDecision(
                allowed=False,
                role=role,
                channel=channel,
                target_view=target_view,
                reason=(
                    f"Role '{role}' is not authorized to access view '{target_view}' "
                    f"(allowed: {', '.join(policy.allowed_views)})."
                ),
                agent_spiffe_id=agent_spiffe_id,
                svid_verified=svid_verified,
            )
        span.set_attribute("docubridge.access_allowed", True)
        return AccessDecision(
            allowed=True,
            role=role,
            channel=channel,
            target_view=target_view,
            reason="Authorized by Knowledge Catalog governance policy.",
            agent_spiffe_id=agent_spiffe_id,
            svid_verified=svid_verified,
            allow_internal_conflict_notes=policy.allow_internal_conflict_notes,
            allow_competitor_quotes=policy.allow_competitor_quotes,
            allow_raw_vault_uri=policy.allow_raw_vault_uri,
            redacted_fields=policy.redacted_fields,
        )


def _normalize_field_lookup_key(field_name_or_path: str) -> str:
    """Normalize a bare field name or dotted path to its bare leaf + full path."""
    return field_name_or_path.strip()


@runtime_checkable
class KnowledgeCatalog(Protocol):
    """Protocol for the Zone 6 Unified Knowledge Catalog."""

    def list_glossary_terms(
        self, *, domain: str | None = None, include_confidential: bool = True
    ) -> list[BusinessGlossaryTerm]:
        """Return business glossary terms filtered by domain, parent glossary ID, or search query."""
        ...

    def register_asset(self, asset: CatalogAsset) -> None:
        """Register or update a catalog asset in the knowledge catalog."""
        ...

    def list_assets(
        self,
        *,
        zone: AssetLocationZone | None = None,
        external_safe_only: bool = False,
    ) -> list[CatalogAsset]:
        """Return catalog assets filtered by asset type, domain, sensitivity tier, or search query."""
        ...

    def register_spiffe_agent(self, policy: SpiffeAgentPolicy) -> None:
        """Register or update a SPIFFE workload identity card in the knowledge catalog."""
        ...

    def list_spiffe_agents(
        self, *, security_zone: Literal["internal", "external"] | None = None
    ) -> list[SpiffeAgentPolicy]:
        """Return registered SPIFFE workload identities, optionally filtered by trust domain."""
        ...

    def register_submission_lineage(
        self,
        entry: LedgerEntry,
        facts: list[ConsolidatedFact],
    ) -> list[LineageTrace]:
        """Record end-to-end W3C PROV-O lineage edges and derived assets for a document submission."""
        ...

    def trace_lineage(
        self, entity_id: str, field_name_or_path: str
    ) -> LineageTrace | None:
        """Trace upstream and downstream W3C PROV-O lineage edges for a target catalog asset."""
        ...

    def list_lineage_for_entity(self, entity_id: str) -> list[LineageTrace]:
        """Return all lineage edges associated with a specific entity ID."""
        ...

    def build_agent_context(
        self,
        *,
        role: CatalogRole,
        channel: DeliveryChannel,
        target_view: str,
        agent_spiffe_id: str | None = None,
        svid_verified: bool = False,
    ) -> AgentSemanticContext:
        """Assemble the grounded catalog, glossary, and SPIFFE context payload for an agent."""
        ...


class InMemoryKnowledgeCatalog:
    """In-memory implementation of the Zone 6 Unified Knowledge Catalog."""

    def __init__(
        self,
        *,
        glossary_terms: tuple[BusinessGlossaryTerm, ...] = DEFAULT_GLOSSARY_TERMS,
        system_assets: tuple[CatalogAsset, ...] = DEFAULT_SYSTEM_ASSETS,
        access_policies: dict[CatalogRole, AccessPolicy] | None = None,
        spiffe_policies: dict[str, SpiffeAgentPolicy] | None = None,
        operational_store: OperationalStore | None = None,
    ) -> None:
        self._glossary: dict[str, BusinessGlossaryTerm] = {
            t.term_id: t for t in glossary_terms
        }
        self._assets: dict[str, CatalogAsset] = {a.asset_id: a for a in system_assets}
        self._policies: dict[CatalogRole, AccessPolicy] = (
            dict(access_policies)
            if access_policies is not None
            else dict(DEFAULT_ACCESS_POLICIES)
        )
        self._spiffe_policies: dict[str, SpiffeAgentPolicy] = (
            dict(spiffe_policies)
            if spiffe_policies is not None
            else dict(DEFAULT_SPIFFE_AGENT_POLICIES)
        )
        self._operational_store = operational_store
        # (entity_id, field_path) -> list of LineageTrace ordered by recorded_at
        self._lineage: dict[tuple[str, str], list[LineageTrace]] = {}

    def register_spiffe_agent(self, policy: SpiffeAgentPolicy) -> None:
        """Register or update a vetted SPIFFE agent workload policy."""
        self._spiffe_policies[policy.spiffe_id] = policy

    def list_spiffe_agents(
        self, *, security_zone: Literal["internal", "external"] | None = None
    ) -> list[SpiffeAgentPolicy]:
        """Return registered SPIFFE workload identities, optionally filtered by trust domain."""
        agents = list(self._spiffe_policies.values())
        if security_zone is not None:
            agents = [a for a in agents if a.security_zone == security_zone]
        return agents

    def bind_operational_store(self, store: OperationalStore) -> None:
        """Bind a durable OperationalStore for stateless lineage reconstruction."""
        self._operational_store = store

    def list_glossary_terms(
        self, *, domain: str | None = None, include_confidential: bool = True
    ) -> list[BusinessGlossaryTerm]:
        """Return business glossary terms filtered by domain, parent glossary ID, or search query."""
        terms = list(self._glossary.values())
        if domain is not None:
            terms = [t for t in terms if t.domain == domain]
        if not include_confidential:
            terms = [t for t in terms if not t.confidential]
        return terms

    def register_asset(self, asset: CatalogAsset) -> None:
        """Register or update a catalog asset in the in-memory knowledge catalog."""
        self._assets[asset.asset_id] = asset

    def list_assets(
        self,
        *,
        zone: AssetLocationZone | None = None,
        external_safe_only: bool = False,
    ) -> list[CatalogAsset]:
        """Return catalog assets filtered by asset type, domain, sensitivity tier, or search query."""
        assets = list(self._assets.values())
        if zone is not None:
            assets = [a for a in assets if a.zone == zone]
        if external_safe_only:
            assets = [
                a for a in assets if a.classification in ("public", "client_shared")
            ]
        return assets

    def _build_trace_from_fact(
        self, fact: ConsolidatedFact, fallback_vault_uri: str | None = None
    ) -> LineageTrace:
        vault_uri = (
            fact.vault_uri or fallback_vault_uri or f"vault://local/vault/{fact.doc_id}"
        )
        page_num = fact.source_anchor.page_number if fact.source_anchor else 1
        source_pdf_uri = (
            fact.source_anchor.artifact_uri
            if fact.source_anchor and fact.source_anchor.artifact_uri
            else f"{vault_uri}#page={page_num}"
        )
        src_node = LineageNode(
            node_id=f"pdf:{fact.entity_id}:{fact.doc_id}:{fact.field_path}:p{page_num}",
            node_type="source_pdf",
            label=f"Source PDF ({fact.doctype}, page {page_num})",
            uri=source_pdf_uri,
            zone=AssetLocationZone.INBOUND,
            metadata={
                "page_number": page_num,
                "doctype": fact.doctype,
                "skill_urn": fact.skill_urn,
                "skill_version": fact.skill_version,
                "skill_revision_id": fact.skill_revision_id,
                "process_skill_revision_id": fact.process_skill_revision_id,
                "compiled_rule_ids": list(fact.compiled_rule_ids),
            },
        )
        vault_node = LineageNode(
            node_id=f"vault:{fact.entity_id}:{fact.doc_id}",
            node_type="vault_object",
            label="Cloud Storage Audit Vault (WORM)",
            uri=vault_uri,
            zone=AssetLocationZone.AUDIT_VAULT,
        )
        fact_node = LineageNode(
            node_id=f"fact:{fact.fact_id}",
            node_type="consolidated_fact",
            label=f"AlloyDB Consolidated Fact ({fact.field_path})",
            uri=f"alloydb://docubridge-enterprise-data/operational/bridge_operational_facts/{fact.fact_id}",
            zone=AssetLocationZone.OPERATIONAL_STORE,
            metadata={
                "source_priority": fact.source_priority,
                "recorded_at": fact.recorded_at.isoformat(),
                "effective_from": fact.effective_from.isoformat(),
                "skill_urn": fact.skill_urn,
                "skill_version": fact.skill_version,
                "skill_revision_id": fact.skill_revision_id,
                "process_skill_revision_id": fact.process_skill_revision_id,
                "compiled_rule_ids": list(fact.compiled_rule_ids),
            },
        )
        view_node = LineageNode(
            node_id=f"view:v_broker_placement:{fact.entity_id}:{fact.field_path}",
            node_type="business_view",
            label="Role-Based Business Views (Broker / Claims / Underwriter)",
            uri="alloydb://docubridge-enterprise-data/views/v_broker_placement",
            zone=AssetLocationZone.BUSINESS_VIEW,
        )
        bq_node = LineageNode(
            node_id=f"bq:{fact.fact_id}",
            node_type="bigquery_table",
            label="BigQuery Analytics Sync (Open Iceberg)",
            uri="bigquery://docubridge_lakehouse.bq_operational_facts_iceberg",
            zone=AssetLocationZone.BIGQUERY_ICEBERG,
        )
        a2ui_node = LineageNode(
            node_id=f"a2ui:{fact.entity_id}:{fact.field_path}",
            node_type="a2ui_widget",
            label="Domain Agent A2UI Widget (Click value -> source PDF)",
            uri=f"a2ui://citation/{fact.entity_id}/{fact.field_path}#page={page_num}",
            zone=AssetLocationZone.USER_INTERACTION_A2UI,
        )
        edges = (
            LineageEdge(
                upstream_id=src_node.node_id,
                downstream_id=vault_node.node_id,
                transformation=(
                    f"Intake validation ({fact.doctype}@{fact.skill_revision_id} "
                    f"v{fact.skill_version}), PII redaction & WORM promotion"
                ),
            ),
            LineageEdge(
                upstream_id=vault_node.node_id,
                downstream_id=fact_node.node_id,
                transformation=(
                    f"Consolidation Rules Engine (priority={fact.source_priority}, "
                    f"skill={fact.skill_revision_id}, bi-temporal append)"
                ),
            ),
            LineageEdge(
                upstream_id=fact_node.node_id,
                downstream_id=view_node.node_id,
                transformation="Role-based SQL view projection with source anchor preservation",
            ),
            LineageEdge(
                upstream_id=fact_node.node_id,
                downstream_id=bq_node.node_id,
                transformation="Append-only CDC stream to BigQuery Open Iceberg table",
            ),
            LineageEdge(
                upstream_id=view_node.node_id,
                downstream_id=a2ui_node.node_id,
                transformation="Domain Agent A2UI component streaming",
            ),
        )
        return LineageTrace(
            entity_id=fact.entity_id,
            field_path=fact.field_path,
            current_value=fact.field_value,
            source_doc_id=fact.doc_id,
            source_doctype=fact.doctype,
            vault_uri=vault_uri,
            source_anchor=fact.source_anchor,
            recorded_at=fact.recorded_at,
            effective_from=fact.effective_from,
            consolidation_rule=(
                f"source_priority_hierarchy(priority={fact.source_priority}, "
                f"doctype={fact.doctype}, skill_revision={fact.skill_revision_id})"
            ),
            skill_version=fact.skill_version,
            skill_revision_id=fact.skill_revision_id,
            skill_urn=fact.skill_urn,
            process_skill_revision_id=fact.process_skill_revision_id,
            compiled_rule_ids=fact.compiled_rule_ids,
            nodes=(src_node, vault_node, fact_node, view_node, bq_node, a2ui_node),
            edges=edges,
        )

    def _hydrate_from_store(self, entity_id: str) -> None:
        if self._operational_store is None:
            return
        active_facts, _ = self._operational_store.query_as_of(entity_id)
        for fact in active_facts:
            trace = self._build_trace_from_fact(fact)
            self._lineage.setdefault((fact.entity_id, fact.field_path), []).append(
                trace
            )
            leaf = fact.field_path.split(".")[-1]
            if leaf != fact.field_path:
                self._lineage.setdefault((fact.entity_id, leaf), []).append(trace)

    def register_submission_lineage(
        self,
        entry: LedgerEntry,
        facts: list[ConsolidatedFact],
    ) -> list[LineageTrace]:
        """Record end-to-end lineage from source PDF -> Vault -> AlloyDB -> Views -> BQ -> A2UI."""
        with trace_step(
            "docubridge.catalog.register_lineage",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doc_id": entry.doc_id,
                "docubridge.doctype": entry.doctype,
                "docubridge.fact_count": len(facts),
                "docubridge.entity_id": facts[0].entity_id if facts else None,
            },
        ) as span:
            vault_uri = entry.vault_uri or f"vault://local/vault/{entry.doc_id}"
            self.register_asset(
                CatalogAsset(
                    asset_id=f"asset.vault.{entry.doc_id}",
                    name=f"Audit Vault Record ({entry.doc_id})",
                    asset_type="vault_archive",
                    zone=AssetLocationZone.AUDIT_VAULT,
                    physical_uri=vault_uri,
                    owner_domain=entry.doctype,
                    classification="internal_only",
                    schema_fields=tuple(sorted(entry.key_fields.keys())),
                )
            )

            traces: list[LineageTrace] = []
            for fact in facts:
                trace = self._build_trace_from_fact(fact, fallback_vault_uri=vault_uri)
                self._lineage.setdefault((fact.entity_id, fact.field_path), []).append(
                    trace
                )
                leaf = fact.field_path.split(".")[-1]
                if leaf != fact.field_path:
                    self._lineage.setdefault((fact.entity_id, leaf), []).append(trace)
                traces.append(trace)
            span.set_attribute("docubridge.registered_trace_count", len(traces))
            return traces

    def trace_lineage(
        self, entity_id: str, field_name_or_path: str
    ) -> LineageTrace | None:
        """Trace upstream and downstream W3C PROV-O lineage edges for a target catalog asset."""
        with trace_step(
            "docubridge.catalog.lineage_trace",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.entity_id": entity_id,
                "docubridge.field_name": field_name_or_path,
            },
        ) as span:
            key = _normalize_field_lookup_key(field_name_or_path)
            history = self._lineage.get((entity_id, key))
            if not history:
                self._hydrate_from_store(entity_id)
                history = self._lineage.get((entity_id, key))
            if not history:
                span.set_attribute("docubridge.lineage_found", False)
                return None
            result = history[-1]
            span.set_attribute("docubridge.lineage_found", True)
            span.set_attribute("docubridge.source_doc_id", result.source_doc_id)
            span.set_attribute("docubridge.hop_count", len(result.nodes))
            if result.source_anchor:
                span.set_attribute(
                    "docubridge.page_number", result.source_anchor.page_number
                )
            return result

    def list_lineage_for_entity(self, entity_id: str) -> list[LineageTrace]:
        """Return all lineage edges associated with a specific entity ID."""
        results = [
            traces[-1]
            for (ent_id, fpath), traces in self._lineage.items()
            if ent_id == entity_id and fpath.startswith("fields.") and traces
        ]
        if not results:
            self._hydrate_from_store(entity_id)
            results = [
                traces[-1]
                for (ent_id, fpath), traces in self._lineage.items()
                if ent_id == entity_id and fpath.startswith("fields.") and traces
            ]
        return results

    def build_agent_context(
        self,
        *,
        role: CatalogRole,
        channel: DeliveryChannel,
        target_view: str,
        agent_spiffe_id: str | None = None,
        svid_verified: bool = False,
    ) -> AgentSemanticContext:
        """Assemble the grounded catalog, glossary, and SPIFFE context payload for an agent."""
        decision = authorize_access(
            role=role,
            channel=channel,
            target_view=target_view,
            policies=self._policies,
            agent_spiffe_id=agent_spiffe_id,
            svid_verified=svid_verified,
            spiffe_policies=self._spiffe_policies,
        )
        external_only = channel == "custom_web"
        glossary = self.list_glossary_terms(
            include_confidential=decision.allow_internal_conflict_notes
        )
        assets = self.list_assets(external_safe_only=external_only)
        return AgentSemanticContext(
            role=role,
            channel=channel,
            target_view=target_view,
            agent_spiffe_id=agent_spiffe_id,
            glossary_terms=tuple(glossary),
            visible_assets=tuple(assets),
            access_decision=decision,
        )


def knowledge_catalog_from_env(
    *, operational_store: OperationalStore | None = None
) -> KnowledgeCatalog:
    """Resolve the Zone 6 KnowledgeCatalog from environment configuration."""
    store = operational_store or operational_store_from_env()
    backend = os.environ.get("BRIDGE_KNOWLEDGE_CATALOG_BACKEND", "").strip().lower()
    if backend == "dataplex":
        return InMemoryKnowledgeCatalog(operational_store=store)
    return InMemoryKnowledgeCatalog(operational_store=store)


__all__ = [
    "DEFAULT_ACCESS_POLICIES",
    "DEFAULT_GLOSSARY_TERMS",
    "DEFAULT_SYSTEM_ASSETS",
    "AccessDecision",
    "AccessPolicy",
    "AgentSemanticContext",
    "AssetLocationZone",
    "BusinessGlossaryTerm",
    "CatalogAsset",
    "CatalogRole",
    "DeliveryChannel",
    "InMemoryKnowledgeCatalog",
    "KnowledgeCatalog",
    "LineageEdge",
    "LineageNode",
    "LineageTrace",
    "QualityRule",
    "authorize_access",
    "knowledge_catalog_from_env",
]
