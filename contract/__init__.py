"""Shared Pydantic wire types — the only package both bridge/ and agents/ import."""

from contract.enums import (
    Disposition,
    Gate,
    LifecycleStatus,
    RequirementStatus,
    ReviewReason,
)
from contract.extraction import (
    ExtractionFields,
    ExtractionResult,
    FieldReview,
    GovIdKeyFields,
    UtilityBillKeyFields,
    field_reviews_for,
    fields_requiring_review,
    review_path,
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
    # Enums
    "Disposition",
    "ExchangeTurn",
    # Extraction
    "ExtractionFields",
    "ExtractionResult",
    "FieldReview",
    "Gate",
    "GovIdKeyFields",
    "HumanReviewResolution",
    "LedgerEntry",
    "LifecycleStatus",
    "Requirement",
    "RequirementStatus",
    "RequirementsList",
    "ReviewReason",
    "UtilityBillKeyFields",
    # Review-vocabulary projection (consumer-facing mapping)
    "field_reviews_for",
    "fields_requiring_review",
    "review_path",
]
