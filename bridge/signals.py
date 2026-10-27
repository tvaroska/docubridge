"""Four-signal disposition model (CORE-4): one SignalProvider interface, pluggable impls."""

import abc

from pydantic import BaseModel, ConfigDict

from bridge.classification import canonicalize_issuer, is_in_label_space
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    DispositionThresholds,
    DispositionVerdict,
    dispose,
)
from bridge.ports import Port, get_adapter, resolve_mode
from bridge.ports.protocols import ExtractionPort
from contract.extraction import ExtractionResult, fields_requiring_review


class Signals(BaseModel):
    """The four disposition signals for one document (design/disposition.md:21-30)."""

    model_config = ConfigDict(extra="forbid")
    # process context (needed for the type-match signal)
    doctype: str
    candidate_doctypes: list[str]
    # signal 1 — legibility
    legible: bool
    # signal 3 — extraction confidence + fields requiring review
    overall_confidence: float
    flagged_fields: list[str] = []
    # sense-A canonical issuer (per-doc normalization)
    issuer: str | None = None
    # signal 4 — completeness/failed-rules. Sense-A declared-rule flags live here;
    # completeness proper is sense-B (app decides). Left EMPTY this phase (rules = P3-3).
    failed_rules: list[str] = []

    @property
    def type_match(self) -> bool:  # signal 2 — right kind?
        """Return True if the detected document type matches the expected document type."""
        return is_in_label_space(self.doctype, self.candidate_doctypes)


def signals_from_result(
    result: ExtractionResult, *, candidate_doctypes: list[str]
) -> Signals:
    """Project a raw ExtractionResult into the four-signal bundle (canonicalizes issuer).

    The raw ``flagged_fields`` list wins whenever it is non-empty (so every existing
    signal bundle and corpus terminal outcome is byte-identical); the dotted-path
    projection is a FALLBACK for an engine that states ``field_reviews`` but leaves
    ``flagged_fields`` empty — without it the confidence gate would auto-approve a
    document the engine itself said needs review.
    """
    return Signals(
        doctype=result.fields.doctype,
        candidate_doctypes=candidate_doctypes,
        legible=result.legible,
        overall_confidence=result.overall_confidence,
        flagged_fields=result.flagged_fields or fields_requiring_review(result),
        issuer=canonicalize_issuer(result.fields.issuer),
    )


class SignalProvider(abc.ABC):
    """One contract; pluggable impls (fixture / model-reported / computed)."""

    @abc.abstractmethod
    async def signals_for(
        self, artifact_name: str, *, candidate_doctypes: list[str]
    ) -> Signals:
        """Produce the four disposition signals for a document artifact."""


class ExtractionSignalProvider(SignalProvider):
    """Signals derived from the extraction port; canonicalizes the issuer Bridge-side."""

    def __init__(self, extractor: ExtractionPort | None = None) -> None:
        self._extractor = extractor  # None -> resolve the extraction port lazily

    async def signals_for(
        self, artifact_name: str, *, candidate_doctypes: list[str]
    ) -> Signals:
        """Extract quality, freshness, and schema completeness signals from a document."""
        extractor = self._extractor or get_adapter(
            Port.extraction, resolve_mode(Port.extraction)
        )
        r = await extractor.extract(artifact_name)
        return signals_from_result(r, candidate_doctypes=candidate_doctypes)


def dispose_signals(
    signals: Signals, thresholds: DispositionThresholds = DEFAULT_THRESHOLDS
) -> DispositionVerdict:
    """Run the deterministic gate over a Signals bundle (each signal drives its route)."""
    return dispose(
        doctype=signals.doctype,
        candidate_doctypes=signals.candidate_doctypes,
        legible=signals.legible,
        overall_confidence=signals.overall_confidence,
        flagged_fields=signals.flagged_fields,
        thresholds=thresholds,
    )
