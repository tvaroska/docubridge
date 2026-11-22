"""Classified-ledger projection (CORE-6) + the one-turn collect path (COLL-4).

Deterministic Bridge-core layer: projects dispositioned documents into a
CollectionStatus/ExchangeTurn. Imports contract + bridge.* only — NO google.adk,
NEVER agents/. The one inversion (the gate) stays the sole disposition authority;
this module only *assembles* verdicts, it never mints one.
"""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from bridge.classification import canonicalize_issuer
from bridge.disposition import DispositionVerdict
from bridge.ports import Port, get_adapter, resolve_mode
from bridge.signals import ExtractorLike, dispose_signals, signals_from_result
from bridge.strategy import evaluate_process_sufficiency
from bridge.telemetry import trace_step
from bridge.vault import redact_personal_data
from contract.enums import Disposition, RequirementStatus
from contract.extraction import ExtractionResult, field_reviews_for
from contract.wire import (
    ArtifactRef,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
)

if TYPE_CHECKING:
    from bridge.knowledge_catalog import KnowledgeCatalog
    from bridge.operational_store import OperationalStore
    from bridge.vault import AuditVaultStore

_LOG = logging.getLogger(__name__)


def _supports_kwarg(fn: object, kwarg: str) -> bool:
    """Return True if callable ``fn`` accepts keyword argument ``kwarg``."""
    try:
        sig = inspect.signature(fn)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        or (
            p.name == kwarg
            and p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        )
        for p in sig.parameters.values()
    )


class DocumentSubmission(BaseModel):
    """One submitted document for a collect turn."""

    model_config = ConfigDict(extra="forbid")
    doc_id: str
    artifact_name: str  # extraction-port lookup key
    artifact: ArtifactRef | None = None
    raw_bytes: bytes | None = None


def project_ledger_entry(
    *,
    doc_id: str,
    result: ExtractionResult,
    verdict: DispositionVerdict,
    artifact: ArtifactRef | None = None,
) -> LedgerEntry:
    """Project one dispositioned document into a classified LedgerEntry (sense A).

    ``field_reviews`` carries the consumer-facing review view onto the A2A wire:
    dotted paths into the extraction result plus *why* each field needs review
    (``contract.extraction.field_reviews_for``). ``reason_code`` is unchanged —
    it still says ``flagged_fields`` when one or more fields require review.
    ``overall_confidence`` / ``field_confidence`` carry the engine's own numbers to the
    consumer (its ``extraction_confidence`` + per-field confidence), unchanged.
    """
    return LedgerEntry(
        doc_id=doc_id,
        doctype=result.fields.doctype,
        disposition=verdict.disposition,
        gate=verdict.gate,
        issuer=canonicalize_issuer(result.fields.issuer),
        key_fields=result.fields.key_fields,
        tables=result.fields.tables,
        reason_code=verdict.reason_code,
        message=verdict.message,
        artifact=artifact,
        field_reviews=field_reviews_for(result),
        overall_confidence=result.overall_confidence,
        field_confidence=dict(result.field_confidence),
        source_anchors=dict(result.fields.source_anchors),
    )


def project_outstanding(entries: list[LedgerEntry]) -> RequirementsList:
    """Per-entry ADVISORY outstanding list: each non-accepted entry -> a
    still-required Requirement carrying its reason_code + message.

    The default ``CollectionStatus.outstanding`` and the fallback of the COLL-2
    best-effort completeness proposal (``bridge.satisfaction.propose_outstanding``,
    which a model interprets from the process skill's prose satisfaction
    description when an assessor is bound). done is ALWAYS False here — the Bridge
    never mints completeness (sense B is the app's).
    """
    outstanding = [
        Requirement(
            item=e.doctype,
            status=RequirementStatus.required,
            doctype_hint=e.doctype,
            reason_code=e.reason_code,
            message=e.message,
        )
        for e in entries
        if e.disposition is not Disposition.accepted
    ]
    return RequirementsList(requirements=outstanding, done=False)


def project_collection_status(
    entries: list[LedgerEntry],
    outstanding: RequirementsList | None = None,
) -> CollectionStatus:
    """Assemble the Bridge-owned classified ledger (what we have)."""
    return CollectionStatus(
        entries=entries,
        outstanding=outstanding
        if outstanding is not None
        else project_outstanding(entries),
    )


class _CandidateAwareExtractor(Protocol):
    async def extract(
        self,
        artifact_name: str,
        *,
        candidate_doctypes: Sequence[str] | None = None,
    ) -> ExtractionResult: ...


class Collector:
    """The deterministic one-turn collect path (COLL-4). Reads the extraction port,
    disposes per the pure gate, projects the classified ledger, and optionally promotes
    accepted entries through the Audit Vault, Operational Store, and Knowledge Catalog."""

    def __init__(
        self,
        extractor: ExtractorLike | None = None,
        *,
        redact_pii: bool | None = None,
        vault_store: AuditVaultStore | None = None,
        operational_store: OperationalStore | None = None,
        knowledge_catalog: KnowledgeCatalog | None = None,
    ) -> None:
        self._extractor = extractor  # None -> resolve the extraction port lazily
        self._redact_pii = redact_pii
        self._vault_store = vault_store
        self._operational_store = operational_store
        self._knowledge_catalog = knowledge_catalog

    async def _maybe_promote_entry(
        self,
        entry: LedgerEntry,
        sub: DocumentSubmission,
        *,
        entity_id: str,
        context_id: str,
    ) -> LedgerEntry:
        if entry.disposition is not Disposition.accepted or (
            self._vault_store is None
            and self._operational_store is None
            and self._knowledge_catalog is None
        ):
            return entry
        doc_bytes = sub.raw_bytes
        doc_mime = sub.artifact.mime_type if sub.artifact is not None else None
        if doc_bytes is None:
            from bridge.extraction.resolve import resolve_document

            uri = (
                sub.artifact.uri
                if sub.artifact is not None and sub.artifact.uri
                else sub.artifact_name
            )
            try:
                resolved = await resolve_document(uri)
            except (ValueError, OSError):
                resolved = None
            if resolved is not None and resolved.data is not None:
                doc_bytes = resolved.data
                doc_mime = doc_mime or resolved.mime_type
        if not doc_bytes:
            _LOG.warning(
                "Skipping Audit Vault promotion for doc_id=%s: no document bytes resolved",
                sub.doc_id,
            )
            return entry
        from bridge.user_interaction import ingest_accepted_submission

        return ingest_accepted_submission(
            entry,
            entity_id=entity_id,
            context_id=context_id,
            raw_document_bytes=doc_bytes,
            filename=sub.artifact_name,
            mime_type=doc_mime,
            vault_store=self._vault_store,
            operational_store=self._operational_store,
            knowledge_catalog=self._knowledge_catalog,
        ).entry

    async def collect(
        self,
        request: CollectRequest,
        *,
        documents: list[DocumentSubmission],
        candidate_doctypes: list[str],
        results_out: dict[str, ExtractionResult] | None = None,
        as_of: date | datetime | None = None,
    ) -> ExchangeTurn:
        """Run one collect turn: extract -> (redact) -> dispose -> project -> (promote).

        ``results_out`` (optional, additive): when given, filled with each document's
        genuine ``ExtractionResult`` keyed by ``doc_id`` — the edge's HITL handoff seeds
        the LLM leg's gate pin from them without re-extracting. ``None`` (the default)
        leaves the behaviour identical.
        """
        extractor: ExtractorLike = self._extractor or get_adapter(
            Port.extraction, resolve_mode(Port.extraction)
        )
        context_id = request.context_id or uuid.uuid4().hex
        entity_id = request.party or context_id
        should_redact = (
            self._redact_pii
            if self._redact_pii is not None
            else (
                os.environ.get("BRIDGE_REDACT_PII", "").strip() == "1"
                or self._vault_store is not None
                or self._operational_store is not None
            )
        )
        entries: list[LedgerEntry] = []
        with trace_step(
            "docubridge.intake.collect_turn",
            {
                "docubridge.process": request.process,
                "docubridge.entity_id": entity_id,
                "docubridge.context_id": context_id,
                "docubridge.document_count": len(documents),
            },
        ) as turn_span:
            for sub in documents:
                with trace_step(
                    "docubridge.intake.collect_document",
                    {
                        "docubridge.process": request.process,
                        "docubridge.entity_id": entity_id,
                        "docubridge.doc_id": sub.doc_id,
                        "docubridge.artifact_name": sub.artifact_name,
                    },
                ) as doc_span:
                    if _supports_kwarg(extractor.extract, "candidate_doctypes"):
                        result = await cast(
                            _CandidateAwareExtractor, extractor
                        ).extract(
                            sub.artifact_name,
                            candidate_doctypes=candidate_doctypes or None,
                        )
                    else:
                        result = await extractor.extract(sub.artifact_name)
                    if should_redact:
                        result, _ = redact_personal_data(result)
                    if results_out is not None:
                        results_out[sub.doc_id] = result
                    signals = signals_from_result(
                        result,
                        candidate_doctypes=candidate_doctypes,
                        as_of=as_of,
                    )
                    verdict = dispose_signals(signals)
                    entry = project_ledger_entry(
                        doc_id=sub.doc_id,
                        result=result,
                        verdict=verdict,
                        artifact=sub.artifact,
                    )
                    doc_span.set_attribute("docubridge.doctype", entry.doctype)
                    doc_span.set_attribute(
                        "docubridge.disposition", entry.disposition.value
                    )
                    doc_span.set_attribute("docubridge.gate", entry.gate.value)
                    if entry.overall_confidence is not None:
                        doc_span.set_attribute(
                            "docubridge.confidence", entry.overall_confidence
                        )
                    entry = await self._maybe_promote_entry(
                        entry,
                        sub,
                        entity_id=entity_id,
                        context_id=context_id,
                    )
                    entries.append(entry)
            status = project_collection_status(entries)
            sufficiency = evaluate_process_sufficiency(
                request.process,
                entries,
                caller_requirements=request.requirements,
                as_of=as_of,
            )
            turn_span.set_attribute(
                "docubridge.accepted_count",
                sum(1 for e in entries if e.disposition is Disposition.accepted),
            )
            turn_span.set_attribute(
                "docubridge.outstanding_count",
                len(status.outstanding.requirements)
                if status.outstanding is not None
                else 0,
            )
            if sufficiency is not None:
                turn_span.set_attribute(
                    "docubridge.sufficiency_satisfied", sufficiency.satisfied
                )
        return ExchangeTurn(
            context_id=context_id,
            status=status,
            requirements=None,  # app-owned; the Bridge never supplies it
            sufficiency=sufficiency,
            terminal=True,  # one-turn completed path (no loop / no park)
        )
