"""Classified-ledger projection (CORE-6) + the one-turn collect path (COLL-4).

Deterministic Bridge-core layer: projects dispositioned documents into a
CollectionStatus/ExchangeTurn. Imports contract + bridge.* only — NO google.adk,
NEVER agents/. The one inversion (the gate) stays the sole disposition authority;
this module only *assembles* verdicts, it never mints one.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from bridge.classification import canonicalize_issuer
from bridge.disposition import DispositionVerdict
from bridge.seams import Seam, get_adapter, resolve_mode
from bridge.seams.protocols import ExtractionSeam
from bridge.signals import dispose_signals, signals_from_result
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


class DocumentSubmission(BaseModel):
    """One submitted document for a collect turn."""
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    artifact_name: str                 # extraction-seam lookup key
    artifact: ArtifactRef | None = None


def project_ledger_entry(
    *, doc_id: str, result: ExtractionResult, verdict: DispositionVerdict,
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
    entries: list[LedgerEntry], outstanding: RequirementsList | None = None,
) -> CollectionStatus:
    """Assemble the Bridge-owned classified ledger (what we have)."""
    return CollectionStatus(
        entries=entries,
        outstanding=outstanding if outstanding is not None else project_outstanding(entries),
    )


class Collector:
    """The deterministic one-turn collect path (COLL-4). Reads the extraction seam,
    disposes per the pure gate, projects the classified ledger, and optionally promotes
    accepted entries through the Audit Vault, Operational Store, and Knowledge Catalog."""

    def __init__(
        self,
        extractor: ExtractionSeam | None = None,
        *,
        redact_pii: bool | None = None,
        vault_store: AuditVaultStore | None = None,
        operational_store: OperationalStore | None = None,
        knowledge_catalog: KnowledgeCatalog | None = None,
    ) -> None:
        self._extractor = extractor  # None -> resolve the extraction seam lazily
        self._redact_pii = redact_pii
        self._vault_store = vault_store
        self._operational_store = operational_store
        self._knowledge_catalog = knowledge_catalog

    async def collect(
        self, request: CollectRequest, *,
        documents: list[DocumentSubmission],
        candidate_doctypes: list[str],
        results_out: dict[str, ExtractionResult] | None = None,
    ) -> ExchangeTurn:
        """Run one collect turn: extract -> (redact) -> dispose -> project -> (promote).

        ``results_out`` (optional, additive): when given, filled with each document's
        genuine ``ExtractionResult`` keyed by ``doc_id`` — the edge's HITL handoff seeds
        the LLM leg's gate pin from them without re-extracting. ``None`` (the default)
        leaves the behaviour identical.
        """
        extractor = self._extractor or get_adapter(Seam.extraction, resolve_mode(Seam.extraction))
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
        for sub in documents:
            result = await extractor.extract(sub.artifact_name)
            if should_redact:
                result, _ = redact_personal_data(result)
            if results_out is not None:
                results_out[sub.doc_id] = result
            signals = signals_from_result(result, candidate_doctypes=candidate_doctypes)
            verdict = dispose_signals(signals)
            entry = project_ledger_entry(
                doc_id=sub.doc_id, result=result, verdict=verdict, artifact=sub.artifact,
            )
            if (
                entry.disposition is Disposition.accepted
                and (
                    self._vault_store is not None
                    or self._operational_store is not None
                    or self._knowledge_catalog is not None
                )
            ):
                from bridge.user_interaction import ingest_accepted_submission

                ingested = ingest_accepted_submission(
                    entry,
                    entity_id=entity_id,
                    context_id=context_id,
                    filename=sub.artifact_name,
                    vault_store=self._vault_store,
                    operational_store=self._operational_store,
                    knowledge_catalog=self._knowledge_catalog,
                )
                entry = ingested.entry
            entries.append(entry)
        status = project_collection_status(entries)
        return ExchangeTurn(
            context_id=context_id,
            status=status,
            requirements=None,     # app-owned; the Bridge never supplies it
            terminal=True,         # one-turn completed path (no loop / no park)
        )
