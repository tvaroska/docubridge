"""Wire types for Bridge A2A contract."""

from __future__ import annotations

from datetime import datetime
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from contract.enums import Disposition, Gate, LifecycleStatus, RequirementStatus
from contract.extraction import FieldReview, SourceAnchor

# Major.minor. A minor bump is ADDITIVE only (a new defaulted field); a reader on an
# older minor strips what it does not know (``decode_wire``). "1.1" = S0-arch-10
# ``tables`` on ``LedgerEntry``; "1.2" = S0-arch-11 RFP collection (``Requirement.party``,
# ``CollectRequest.requirements`` + ``due_at``); "1.3" = S0-arch-9
# ``LedgerEntry.overall_confidence`` + ``field_confidence``; "1.4" = S3-edge-6
# ``HumanReviewResolution`` (a new additive type).
CONTRACT_VERSION = "1.4"


class ArtifactRef(BaseModel):
    """By-reference document handle; bytes never travel on the wire."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    mime_type: str | None = None
    name: str | None = None


class LedgerEntry(BaseModel):
    """One classified document in the ledger."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    doctype: str
    disposition: Disposition
    gate: Gate
    issuer: str | None = None  # Canonical issuer, e.g. 'power-co'
    key_fields: dict[str, str] = {}
    # Row-shaped extracted data (S0-arch-10), e.g. a carrier rate sheet; values stay
    # text. Additive + defaulted (contract minor 1.1).
    tables: dict[str, list[dict[str, str]]] = {}
    reason_code: str | None = None
    message: str | None = None
    artifact: ArtifactRef | None = None
    # Per-field review view (dotted paths + why) — the consumer's
    # `fields_requiring_review` as it reaches the A2A ledger. Additive + defaulted,
    # so no contract-major bump; `reason_code` stays "flagged_fields".
    field_reviews: list[FieldReview] = []
    # The engine's confidence, carried to the consumer (S0-arch-9): the consumer's
    # `extraction_confidence` + per-field confidence. Additive + defaulted (minor 1.3);
    # `None` means "no engine number" (e.g. a schema_invalid entry), not a real zero.
    overall_confidence: float | None = None
    field_confidence: dict[str, float] = {}
    # Enterprise Data Architecture extensions (all additive + defaulted):
    # - source_anchors: page & bounding-box provenance per extracted field/path
    # - recorded_at / effective_from: bi-temporal timestamps (when learned vs. effective)
    # - vault_uri: immutable Cloud Storage Audit Vault URI once promoted on acceptance
    source_anchors: dict[str, SourceAnchor] = {}
    recorded_at: datetime | None = None
    effective_from: datetime | None = None
    vault_uri: str | None = None
    # Skill Registry & Process-Instance Lineage (additive + defaulted):
    skill_version: str | None = None
    skill_revision_id: str | None = None
    skill_urn: str | None = None
    process_skill_revision_id: str | None = None
    compiled_rule_ids: list[str] = []


class Requirement(BaseModel):
    """A single requirement in the app-owned requirements list."""

    model_config = ConfigDict(extra="forbid")

    item: str
    status: RequirementStatus
    # Whose response the item awaits, e.g. a carrier on an RFP distribution list; the
    # requirement is then (party x item). Additive + defaulted (contract minor 1.2).
    party: str | None = None
    doctype_hint: str | None = None
    reason_code: str | None = None
    message: str | None = None


class RequirementsList(BaseModel):
    """App-owned requirements view (what's needed / done)."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[Requirement] = []
    done: bool = False  # App decides "done"; Bridge never mints sense-B completeness


class CollectionStatus(BaseModel):
    """Bridge-owned classified ledger (what we have)."""

    model_config = ConfigDict(extra="forbid")

    entries: list[LedgerEntry] = []
    outstanding: RequirementsList | None = (
        None  # Bridge's best-effort still-needed proposal (advisory)
    )


class CollectRequest(BaseModel):
    """Top-level wire type: what the collect node sends to the Bridge."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    process: str  # Process-skill key, e.g. 'address-proof'
    party: str | None = (
        None  # Counterparty reference; falls back to exchange context when absent
    )
    context_id: str | None = (
        None  # Exchange/A2A context; None on first turn, minted by Bridge
    )
    message: str | None = None  # Optional free-text instruction
    # The app's requirements for this turn (the "app -> Bridge" leg of the Collect loop),
    # e.g. an RFP's carrier distribution list x product lines; and the due date that
    # anchors the SLA ladder. Additive + defaulted (contract minor 1.2).
    requirements: RequirementsList | None = None
    due_at: datetime | None = None


class ExchangeTurn(BaseModel):
    """Top-level wire type: the Bridge's per-turn deliverable."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    context_id: str
    status: CollectionStatus
    requirements: RequirementsList | None = None
    terminal: bool = Field(
        default=False,
        description=(
            "Bridge transport turn state (COMPLETED vs INPUT_REQUIRED). "
            "NOT the app's sense-B 'done' — that lives on RequirementsList.done."
        ),
    )
    lifecycle: LifecycleStatus = LifecycleStatus.on_track


class HumanReviewResolution(BaseModel):
    """Top-level wire type: a reviewer's resolution of a parked human review.

    Sent by the app on the SAME A2A task that went ``INPUT_REQUIRED`` with
    ``reason_code=awaiting_human_review``; ``call_id`` echoes that status message's
    ``metadata.review_call_id``. ``resolution`` mirrors the portal's HITL bodies.

    ``reviewer`` is an audit label only — it is NEVER used for authorization. The
    Bridge authorizes the reviewer from the authenticated A2A call-context principal,
    never from this payload.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    call_id: str
    resolution: Literal["accepted", "rejected"]
    message: str
    reviewer: str | None = None


# --------------------------------------------------------------------------- #
# Version-gated tolerant decode (D2).                                          #
#                                                                             #
# The wire models above stay ``extra="forbid"`` — strict *within* our contract #
# major, which honours spec D2's "extra=forbid for our two-party edge" and     #
# catches first-party typos. But spec D2 also reserves a **tolerant reader**   #
# for genuinely external callers, and a producer running a *newer* contract    #
# major is exactly that. ``decode_wire`` inspects ``contract_version`` (until  #
# now carried but never read) and, on a major mismatch OR a NEWER minor (a     #
# minor bump is additive by rule), strips fields the model doesn't declare     #
# before validating — so an added field degrades gracefully instead of         #
# hard-failing. Same- or older-version decode is unchanged (strict).           #
# --------------------------------------------------------------------------- #


def _major(version: object) -> str:
    """Major component of a ``contract_version`` string (``"1.4"`` -> ``"1"``)."""
    return str(version).split(".", 1)[0]


CONTRACT_MAJOR = _major(CONTRACT_VERSION)


def _minor(version: object) -> int:
    """Minor component of a ``contract_version`` (``"1"`` -> 0, ``"1.4"`` -> 4).

    An unparsable minor reads as 0 — the strict path — so a garbled version never
    buys a payload tolerance it did not declare.
    """
    parts = str(version).split(".")
    try:
        return int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0


def _needs_tolerance(version: object) -> bool:
    """A different major, or our major at a newer (additive) minor."""
    if _major(version) != CONTRACT_MAJOR:
        return True
    return _minor(version) > _minor(CONTRACT_VERSION)


def _strip_to_known(annotation: Any, value: Any) -> Any:
    """Recursively drop dict keys not declared on nested contract models.

    Walks the declared type (``BaseModel`` subtrees, ``list[...]``, ``X | None``,
    ``dict``, enums, scalars) and returns ``value`` with any keys the models don't
    declare removed, so a subsequent ``extra="forbid"`` validate cannot hard-fail
    on a field a newer producer added. Leaves scalar/enum/``dict[...]`` leaves and
    non-dict values untouched.
    """
    origin = get_origin(annotation)

    if origin is Union or origin is UnionType:
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            stripped = _strip_to_known(arg, value)
            if stripped is not value:  # a model arm claimed and rewrote it
                return stripped
        return value

    if origin in (list, tuple) and isinstance(value, list):
        args = get_args(annotation)
        item_t = args[0] if args else Any
        return [_strip_to_known(item_t, v) for v in value]

    if origin is dict:
        return value  # open-ended mapping (e.g. key_fields) — a leaf, keep as-is

    if (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and isinstance(value, dict)
    ):
        by_key: dict[str, tuple[str, Any]] = {}
        for name, field in annotation.model_fields.items():
            by_key[name] = (name, field.annotation)
            if field.alias:
                by_key[field.alias] = (name, field.annotation)
            # A `validation_alias=AliasChoices(...)` is invisible to `field.alias`, so
            # without this the tolerant decode would strip an external caller's
            # accepted spelling (e.g. `fields_requiring_review`) back out — exactly
            # the caller the tolerance exists for.
            if isinstance(field.validation_alias, AliasChoices):
                for choice in field.validation_alias.choices:
                    if isinstance(choice, str):
                        by_key[choice] = (name, field.annotation)
            elif isinstance(field.validation_alias, str):
                by_key[field.validation_alias] = (name, field.annotation)
        known: dict[str, Any] = {}
        for key, val in value.items():
            if key in by_key:
                known[key] = _strip_to_known(by_key[key][1], val)
        return known

    return value


def decode_wire[M: BaseModel](model_cls: type[M], data: Any) -> M:
    """Validate an inbound wire payload, tolerating a newer producer's added fields.

    Same-major payloads at our minor or older validate strictly (``extra="forbid"`` —
    unchanged). If the payload declares a *different* major ``contract_version``, or a
    *newer minor* of ours (minors are additive by rule), unknown fields are
    stripped across the model tree first, so the decode degrades gracefully instead
    of hard-failing (spec D2 tolerant-reader for external callers). Raises
    ``ValidationError`` when the payload is genuinely the wrong shape (missing a
    required field), preserving the select-by-shape disambiguation at the edge.
    """
    if not isinstance(data, dict):
        return model_cls.model_validate(data)
    if _needs_tolerance(data.get("contract_version", CONTRACT_VERSION)):
        return model_cls.model_validate(_strip_to_known(model_cls, data))
    return model_cls.model_validate(data)
