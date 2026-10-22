"""Extraction field schemas and extraction result models.

**Consumer-facing review vocabulary (the normative mapping).** A consumer speaks
``extraction_confidence`` + ``fields_requiring_review`` as *dotted field paths*; the
Bridge wire speaks ``overall_confidence`` / ``field_confidence`` / ``flagged_fields``.
The names are NOT renamed on the wire — the mapping below is the contract, and it is
executable (``fields_requiring_review`` / ``field_reviews_for``), not prose:

| Consumer vocabulary      | Bridge wire field                          | Mapping |
|--------------------------|--------------------------------------------|---------|
| ``extraction_confidence``| ``overall_confidence``                     | the same scalar, 0.0–1.0 |
| per-field confidence     | ``field_confidence[name]``                 | keyed by the bare ``key_fields`` key (or a table name / dotted row path) |
| ``fields_requiring_review`` | ``flagged_fields`` **∪** ``field_reviews[].path`` | :func:`fields_requiring_review` returns the **dotted paths**: a bare name ``k`` maps to ``fields.key_fields.{k}``, a declared table ``t`` → ``fields.tables.{t}``, ``doctype`` → ``fields.doctype``, ``issuer`` → ``fields.issuer``; a name that already contains ``.`` or ``[`` passes through unchanged |
| *why* a field needs review | ``field_reviews[].reason``               | ``low_confidence`` / ``derived`` / ``absent_from_document`` / ``model_flagged`` |
| reason code on the ledger | ``LedgerEntry.reason_code == "flagged_fields"`` | unchanged; means "one or more fields require review" |

``fields_requiring_review`` is additionally accepted as an **inbound validation alias**
for ``flagged_fields`` (a consumer posting its own vocabulary on Path A used to fall
silently through to Path B); we still *dump* ``flagged_fields``.

Paths address the **Bridge extraction result**, never a consumer's own schema —
mapping Bridge paths onto ``rates[0].amount``-style consumer paths is the consumer's job.
A row cell is addressed ``fields.tables.rates[12].frequency``.

**Tables (S0-arch-10).** ``key_fields`` is flat text on purpose; a document that carries
*rows* (a carrier rate sheet: tier × age band × tobacco × amount × frequency) puts them in
``ExtractionFields.tables`` — table name -> list of rows, each row a flat
``column -> text`` map. Money stays **text** in both places: the consumer code-parses it
(the hallucination firewall — a model never emits a number that is then trusted as one).
Which tables and columns a doctype carries is declared by its skill schema
(``properties.tables``), exactly as ``key_fields`` is.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from contract.enums import ReviewReason


class GovIdKeyFields(BaseModel):
    """Typed key fields for gov-id documents."""

    model_config = ConfigDict(extra="forbid")

    name: str
    address: str
    doc_number: str
    issuing_authority: str
    expiry: str  # ISO date string; kept as str for lossless corpus round-trip


class UtilityBillKeyFields(BaseModel):
    """Typed key fields for utility-bill documents."""

    model_config = ConfigDict(extra="forbid")

    name: str
    service_address: str
    statement_date: str  # ISO date string; kept as str for lossless corpus round-trip


class SourceAnchor(BaseModel):
    """Exact document page and bounding-box location for an extracted value."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float] | None = None
    snippet: str | None = None
    artifact_uri: str | None = None

    @model_validator(mode="after")
    def _validate_bbox(self) -> SourceAnchor:
        if self.bounding_box is not None:
            x0, y0, x1, y1 = self.bounding_box
            if not (0.0 <= x0 <= x1 <= 1.0 and 0.0 <= y0 <= y1 <= 1.0):
                raise ValueError(
                    "bounding_box must satisfy 0.0 <= x0 <= x1 <= 1.0 and "
                    f"0.0 <= y0 <= y1 <= 1.0; got {self.bounding_box!r}"
                )
        return self


class ExtractionFields(BaseModel):
    """Wire shape for extraction.fields carried in ExtractionResult."""

    model_config = ConfigDict(extra="forbid")

    doctype: str  # Free string; label-space enforcement is bridge-side
    issuer: str | None = None
    key_fields: dict[
        str, str
    ] = {}  # Dict, not typed union; key sets differ per doctype
    # Row-shaped data (S0-arch-10): table name -> rows, each row column -> text.
    # Additive + defaulted, so every existing payload validates unchanged.
    tables: dict[str, list[dict[str, str]]] = {}
    # Source-page and bounding-box anchors keyed by field name or dotted path.
    source_anchors: dict[str, SourceAnchor] = {}


class FieldReview(BaseModel):
    """One field that requires review, with its provenance (sense A).

    ``path`` is a dotted path into the *Bridge* extraction result (see the module
    docstring's mapping table), not into a consumer's schema.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    reason: ReviewReason
    confidence: float | None = (
        None  # The engine's own number for that field, when known
    )
    detail: str | None = None  # One short free-text note


class ExtractionResult(BaseModel):
    """Full extraction block from the corpus (extraction.*).

    ``field_reviews`` is ADDITIVE, optional and defaulted on purpose: every corpus
    block, canned payload and carrier-agent dump validates unchanged, and
    ``bridge/edge/pathroute.py`` can keep using this model as a Path-A shape selector.
    Never add a REQUIRED field here — that silently reroutes Path A to Path B.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fields: ExtractionFields
    overall_confidence: float
    field_confidence: dict[str, float] = {}  # Defaults matter: passport omits this
    legible: bool
    flagged_fields: list[str] = Field(
        default=[],
        # Inbound-only alias: a consumer speaking `fields_requiring_review` validates
        # here instead of failing extra="forbid" (and silently misrouting to Path B).
        # Deliberately NO serialization_alias — we keep DUMPING `flagged_fields`, so
        # the corpus and every round-trip stay byte-stable.
        validation_alias=AliasChoices("flagged_fields", "fields_requiring_review"),
    )
    field_reviews: list[FieldReview] = []

    @model_validator(mode="before")
    @classmethod
    def _reject_conflicting_review_keys(cls, data: Any) -> Any:
        """Fail loud when a payload carries BOTH review keys with different values.

        Pydantic would otherwise silently pick the first alias choice — a silent data
        loss. This repo degrades loudly (cf. ``bridge/standard.py``'s unknown-check).
        Equal values carry no ambiguity: the redundant spelling is dropped so the
        payload validates (``extra="forbid"`` would otherwise reject the unconsumed
        alias key with a confusing "extra inputs" error).
        """
        both = (
            isinstance(data, dict)
            and "flagged_fields" in data
            and "fields_requiring_review" in data
        )
        if not both:
            return data
        if data["flagged_fields"] != data["fields_requiring_review"]:
            raise ValueError(
                "payload carries both flagged_fields and fields_requiring_review "
                "with different values; send one (they are the same field)"
            )
        return {k: v for k, v in data.items() if k != "fields_requiring_review"}


def review_path(name: str, fields: ExtractionFields) -> str:
    """Map a flagged field NAME onto a dotted path into the extraction result.

    Already-pathy names (containing ``.`` or ``[``) pass through unchanged — that is
    how a consumer's own dotted path survives a round trip. ``fields`` is accepted so
    the mapping can stay field-aware as the result shape grows.
    """
    if "." in name or "[" in name:
        return name
    if name in ("doctype", "issuer"):
        return f"fields.{name}"
    if name in fields.tables and name not in fields.key_fields:
        return f"fields.tables.{name}"
    return f"fields.key_fields.{name}"


def field_reviews_for(result: ExtractionResult) -> list[FieldReview]:
    """The full review view: engine-stated reviews first, then one derived per flag.

    Engine-supplied ``field_reviews`` win (in order); a ``flagged_fields`` name whose
    path is already covered adds nothing. An engine-supplied ``path`` runs through
    :func:`review_path` too (idempotent for an already-dotted path) so an engine that
    answers with the bare ``key_fields`` name collapses onto its own flag instead of
    yielding the same field twice. A derived entry gets ``absent_from_document``
    when the document carries no such field, else ``model_flagged`` — ``low_confidence``
    and ``derived`` are never INFERRED (inferring low-confidence here would also leak
    bridge-side ``DispositionThresholds`` policy into the shared contract package).
    Output order is stable (first-seen), deduped on ``path``.
    """
    reviews: list[FieldReview] = []
    seen: set[str] = set()
    for review in result.field_reviews:
        path = review_path(review.path, result.fields)
        if path in seen:
            continue
        seen.add(path)
        reviews.append(
            review if path == review.path else review.model_copy(update={"path": path})
        )
    for name in result.flagged_fields:
        path = review_path(name, result.fields)
        if path in seen:
            continue
        seen.add(path)
        # An already-dotted path addresses something we cannot look up, so we cannot
        # claim the document lacks it: only a bare name we KNOW is not in the result
        # earns `absent_from_document`.
        absent = (
            path != name
            and name not in result.fields.key_fields
            and name not in result.fields.tables
            and name not in ("doctype", "issuer")
        )
        reviews.append(
            FieldReview(
                path=path,
                reason=ReviewReason.absent_from_document
                if absent
                else ReviewReason.model_flagged,
                confidence=result.field_confidence.get(name),
            )
        )
    return reviews


def fields_requiring_review(result: ExtractionResult) -> list[str]:
    """The consumer's ``fields_requiring_review``: dotted paths, stable order."""
    return [r.path for r in field_reviews_for(result)]
