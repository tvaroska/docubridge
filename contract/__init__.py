"""Shared Pydantic wire types — the only package both bridge/ and agents/ import."""

from contract.enums import (
    Disposition,
    Gate,
    LifecycleStatus,
    RequirementStatus,
    ReviewReason,
)
from contract.extraction import (
    ExtractedRecord,
    ExtractionFields,
    ExtractionResult,
    FieldDerivation,
    FieldReview,
    GovIdKeyFields,
    SourceAnchor,
    UtilityBillKeyFields,
    field_reviews_for,
    fields_requiring_review,
    review_path,
)
from contract.refinery import (
    ContrastiveDescription,
    EdgeRelation,
    OntologyEdge,
    OntologyEntitySpec,
    OntologyFieldSpec,
    OntologyRevision,
    OntologyRevisionState,
    RefineryAssayReport,
    RefineryProposal,
    RefineryProposalKind,
    RefineryProposalStatus,
)
from contract.wire import (
    CONTRACT_VERSION,
    ArtifactRef,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    HumanReviewResolution,
    LedgerEntry,
    Requirement,
    RequirementsList,
)

__all__ = [
    # Wire
    "CONTRACT_VERSION",
    "ArtifactRef",
    "CollectRequest",
    "CollectionStatus",
    # Refinery & Ontology
    "ContrastiveDescription",
    # Enums
    "Disposition",
    "EdgeRelation",
    "ExchangeTurn",
    # Extraction
    "ExtractedRecord",
    "ExtractionFields",
    "ExtractionResult",
    "FieldDerivation",
    "FieldReview",
    "Gate",
    "GovIdKeyFields",
    "HumanReviewResolution",
    "LedgerEntry",
    "LifecycleStatus",
    "OntologyEdge",
    "OntologyEntitySpec",
    "OntologyFieldSpec",
    "OntologyRevision",
    "OntologyRevisionState",
    "RefineryAssayReport",
    "RefineryProposal",
    "RefineryProposalKind",
    "RefineryProposalStatus",
    "Requirement",
    "RequirementStatus",
    "RequirementsList",
    "ReviewReason",
    "SourceAnchor",
    "UtilityBillKeyFields",
    # Review-vocabulary projection (consumer-facing mapping)
    "field_reviews_for",
    "fields_requiring_review",
    "review_path",
]
