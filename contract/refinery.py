"""Shared Pydantic wire models for the Schema Refinery & Versioned Ontology Graph.

Defines the contract types shared between ``bridge/`` and ``agents/`` for:
- Three-part Contrastive Descriptions (``what_it_is``, ``where_to_read``, ``distinguish_from``)
- Declarative Ontology Graph Edges (``[:SUPERSEDES]``, ``[:FEEDS_METRIC]``, ``[:SPLIT_FROM]``, etc.)
- OKF Semantic-Entity and Field specifications
- Taxonomy & Contrastive Description Proposals (``RefineryProposal``)
- 3-Tier Counterfactual & Calibration Assay Reports (``RefineryAssayReport``)
- Immutable Bi-Temporal Ontology Revisions (``OntologyRevision``)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EdgeRelation = Literal[
    "SUPERSEDES",
    "FEEDS_METRIC",
    "REQUIRES_CORROBORATION",
    "ALIAS_OF",
    "DISTINCT_FROM",
    "SPLIT_FROM",
    "MERGED_INTO",
    "ALIASED_FROM",
    "INCLUDES",
    "EXCLUDES",
]

ENTITY_COMMERCIAL_PROPERTY_QUOTE = "commercial-property-quote"
ENTITY_TRID_CLOSING_DISCLOSURE = "trid-closing-disclosure"
ENTITY_CRE_CREDIT_AGREEMENT = "cre-credit-agreement"
ENTITY_MAI_APPRAISAL = "mai-appraisal"
ENTITY_BENEFITS_QUOTE = "benefits-quote"
ENTITY_UTILITY_BILL = "utility-bill"
ENTITY_GOV_ID = "gov-id"
ENTITY_WIND_ENDORSEMENT = "wind-deductible-endorsement"
ENTITY_FEE_SCHEDULE_ADDENDUM = "fee-schedule-addendum"
ENTITY_RATE_GUARANTEE_ADDENDUM = "rate-guarantee-addendum"


class RefineryProposalKind(StrEnum):
    """Granular mutation primitives proposed by the Schema Refinery."""

    REFINE_DESCRIPTION = "REFINE_DESCRIPTION"
    UPSERT_EDGE = "UPSERT_EDGE"
    PROPOSE_NEW_ENTITY = "PROPOSE_NEW_ENTITY"
    SPLIT_ENTITY = "SPLIT_ENTITY"
    MERGE_ENTITY = "MERGE_ENTITY"
    CHANGE_FIELD_CARDINALITY = "CHANGE_FIELD_CARDINALITY"


class RefineryProposalStatus(StrEnum):
    """Lifecycle states for a Schema Refinery candidate proposal."""

    CANDIDATE = "CANDIDATE"
    ASSAY_PASSED = "ASSAY_PASSED"
    ASSAY_FAILED = "ASSAY_FAILED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class OntologyRevisionState(StrEnum):
    """Lifecycle states for a versioned OntologyRevision."""

    DRAFT = "DRAFT"
    ASSAY = "ASSAY"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class ContrastiveDescription(BaseModel):
    """Three-part contrastive semantic description for an entity or field.

    Separates positive semantic identity (``what_it_is``), physical layout anchor
    guidance (``where_to_read``), and negative look-alike boundaries
    (``distinguish_from``) so workhorse extraction models remain invariant to
    catalog ordering and multi-page distractor tables.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    what_it_is: str = Field(
        description="Positive semantic identity of the document type or field."
    )
    where_to_read: str = Field(
        default="",
        description="Visual layout anchor or structural section where the value appears.",
    )
    distinguish_from: str = Field(
        default="",
        description="Negative boundary distinguishing this concept from look-alike distractors.",
    )

    def render(self) -> str:
        """Synthesize a single self-contained prompt/schema description string."""
        parts: list[str] = [self.what_it_is.strip()]
        if self.where_to_read.strip():
            parts.append(f"Where to read: {self.where_to_read.strip()}")
        if self.distinguish_from.strip():
            parts.append(f"Distinguish from: {self.distinguish_from.strip()}")
        return " ".join(p for p in parts if p)


class OntologyEdge(BaseModel):
    """Typed directed relationship edge in the Zone 6 Ontology Graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    source_id: str
    relation: EdgeRelation
    target_id: str
    priority_rank: int | None = None
    on_fields: tuple[str, ...] = ()
    metric_operation: str | None = None
    condition_expr: str | None = None
    effective_from_revision: str = "ont-rev-v1-1-baseline"


class OntologyFieldSpec(BaseModel):
    """Declarative field specification inside an OKF semantic-entity bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    field_type: str = "string"
    required: bool = False
    synonyms: tuple[str, ...] = ()
    description: ContrastiveDescription
    feeds_metric: str | None = None
    monetary: bool = False


class OntologyEntitySpec(BaseModel):
    """Declarative OKF semantic-entity specification for a document type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    okf_version: str = "0.2"
    entry_type: str = "semantic-entity"
    context_set: str = "enterprise-document-intake"
    revision_id: str = "ont-rev-v1-1-baseline"
    source_priority: int = 20
    aliases: tuple[str, ...] = ()
    declares_issuer: bool = True
    issuer_description: str = (
        "Canonical legal name of the issuing carrier, lender, or authority."
    )
    entity_description: ContrastiveDescription
    fields: dict[str, OntologyFieldSpec] = Field(default_factory=dict)
    edges: tuple[OntologyEdge, ...] = ()


class RefineryAssayReport(BaseModel):
    """3-Tier Assay Gate scorecard for a candidate ontology or description mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    proposal_id: str
    candidate_revision_id: str
    baseline_revision_id: str
    # Tier 1: Deterministic Graph & Schema Invariants (Rule 0)
    dag_acyclic: bool = True
    decimal_formulas_valid: bool = True
    corridor_fa_zero: bool = True
    paired_removal_intact: bool = True
    # Tier 2: Counterfactual Historical Deal Backtest (BigQuery / AlloyDB replay)
    replayed_deal_count: int = Field(default=3, ge=0)
    negative_flip_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    conflicts_resolved_delta: int = 0
    quarantine_reduction_delta: int = 0
    replayed_deals: tuple[str, ...] = ()
    # Tier 3: Perturbation Stability & Calibration Alignment. Alpha / AC2 are
    # measured over real extraction runs (raters = catalog permutations, items =
    # document x field) and are None whenever no live extractor evaluated them.
    perturbation_evaluated: bool = False
    perturbation_items: int = Field(default=0, ge=0)
    perturbation_permutations: int = Field(default=0, ge=0)
    perturbation_krippendorff_alpha: float | None = Field(default=None, ge=-1.0, le=1.0)
    workhorse_teacher_ac2: float | None = Field(default=None, ge=-1.0, le=1.0)
    rogan_gladen_pass_rate: float = Field(default=0.96, ge=0.0, le=1.0)
    bimodal_jaccard_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    bimodal_jaccard_distance: float = Field(default=1.0, ge=0.0, le=1.0)
    passed: bool = False
    summary: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RefineryProposal(BaseModel):
    """Candidate ontology mutation or contrastive description refinement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    kind: RefineryProposalKind
    status: RefineryProposalStatus = RefineryProposalStatus.CANDIDATE
    target_entity_id: str
    new_entity_id: str | None = None
    target_field_name: str | None = None
    domain: str = "insurance"
    title: str
    rationale: str
    before_description: ContrastiveDescription | None = None
    after_description: ContrastiveDescription | None = None
    proposed_entity: OntologyEntitySpec | None = None
    added_edges: tuple[OntologyEdge, ...] = ()
    migrated_fields: tuple[str, ...] = ()
    evidence_metrics: dict[str, Any] = Field(default_factory=dict)
    assay_report_id: str | None = None
    promoted_revision_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OntologyRevision(BaseModel):
    """Immutable bi-temporal Ontology Graph snapshot stored in Zone 6 Knowledge Catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    version: str
    parent_revision_id: str | None = None
    state: OntologyRevisionState = OntologyRevisionState.ACTIVE
    content_sha256: str
    summary: str = "Baseline enterprise document ontology graph"
    entities: dict[str, OntologyEntitySpec] = Field(default_factory=dict)
    edges: tuple[OntologyEdge, ...] = ()
    promoted_proposal_ids: tuple[str, ...] = ()
    effective_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    effective_to: datetime | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
