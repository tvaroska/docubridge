"""Tests for the classified-ledger projection + one-turn collect path (P1-4)."""

import json
from pathlib import Path

import pytest

from bridge.collect import (
    Collector,
    DocumentSubmission,
    project_collection_status,
    project_ledger_entry,
    project_outstanding,
)
from bridge.disposition import DispositionVerdict
from bridge.extraction.fixture import FixtureExtractor
from contract.enums import Disposition, Gate, RequirementStatus
from contract.extraction import ExtractionFields, ExtractionResult
from contract.wire import (
    CONTRACT_MAJOR,
    CONTRACT_VERSION,
    ArtifactRef,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    _minor,
    decode_wire,
)

# Load the corpus for oracle tests
CORPUS_PATH = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
with open(CORPUS_PATH) as f:
    CORPUS_DATA = json.load(f)
CORPUS_RECORDS = CORPUS_DATA["documents"]


# ========== Projection units (pure — no I/O) ==========


def test_project_ledger_entry_copies_all_fields():
    """project_ledger_entry copies every field from result + verdict to LedgerEntry."""
    result = ExtractionResult(
        fields=ExtractionFields(
            doctype="utility-bill",
            issuer="power-co",
            key_fields={"name": "Jordan Lee", "service_address": "14 Elm Row"},
        ),
        overall_confidence=0.97,
        legible=True,
        flagged_fields=[],
    )
    verdict = DispositionVerdict(
        disposition=Disposition.rejected,
        gate=Gate.resubmit,
        reason_code="illegible",
        message="The document was unreadable — please resubmit a clearer copy.",
    )
    artifact = ArtifactRef(uri="gs://bucket/doc.pdf", mime_type="application/pdf")

    entry = project_ledger_entry(
        doc_id="doc-123",
        result=result,
        verdict=verdict,
        artifact=artifact,
    )

    assert entry.doc_id == "doc-123"
    assert entry.doctype == "utility-bill"
    assert entry.disposition == Disposition.rejected
    assert entry.gate == Gate.resubmit
    assert entry.issuer == "power-co"
    assert entry.key_fields == {"name": "Jordan Lee", "service_address": "14 Elm Row"}
    assert entry.reason_code == "illegible"
    assert (
        entry.message == "The document was unreadable — please resubmit a clearer copy."
    )
    assert entry.artifact == artifact


def test_project_ledger_entry_canonicalizes_raw_issuer():
    """project_ledger_entry canonicalizes a RAW issuer at projection time."""
    result = ExtractionResult(
        fields=ExtractionFields(
            doctype="utility-bill",
            issuer="Power Co.",  # RAW issuer (not pre-canonicalized)
            key_fields={},
        ),
        overall_confidence=0.95,
        legible=True,
        flagged_fields=[],
    )
    verdict = DispositionVerdict(
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        reason_code="auto_approved",
        message="The document was automatically approved.",
    )

    entry = project_ledger_entry(doc_id="doc-456", result=result, verdict=verdict)

    # Proves canonicalization at projection time, not just fixture-preserved
    assert entry.issuer == "power-co"


def test_project_outstanding_filters_non_accepted():
    """project_outstanding: each non-accepted entry -> a Requirement; done always False."""
    entries = [
        LedgerEntry(
            doc_id="doc-1",
            doctype="gov-id",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="auto_approved",
            message="Approved.",
        ),
        LedgerEntry(
            doc_id="doc-2",
            doctype="utility-bill",
            disposition=Disposition.rejected,
            gate=Gate.resubmit,
            reason_code="illegible",
            message="Please resubmit a clearer copy.",
        ),
        LedgerEntry(
            doc_id="doc-3",
            doctype="utility-bill",
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            reason_code="flagged_fields",
            message="Fields require review: expiry.",
        ),
    ]

    outstanding = project_outstanding(entries)

    # Only the two non-accepted entries surface as requirements
    assert len(outstanding.requirements) == 2
    assert outstanding.done is False

    # Check the rejected entry
    req_rejected = outstanding.requirements[0]
    assert req_rejected.item == "utility-bill"
    assert req_rejected.status == RequirementStatus.required
    assert req_rejected.doctype_hint == "utility-bill"
    assert req_rejected.reason_code == "illegible"
    assert req_rejected.message == "Please resubmit a clearer copy."

    # Check the pending entry
    req_pending = outstanding.requirements[1]
    assert req_pending.item == "utility-bill"
    assert req_pending.status == RequirementStatus.required
    assert req_pending.doctype_hint == "utility-bill"
    assert req_pending.reason_code == "flagged_fields"
    assert req_pending.message == "Fields require review: expiry."


def test_project_outstanding_all_accepted():
    """project_outstanding: all-accepted list -> empty requirements, done still False."""
    entries = [
        LedgerEntry(
            doc_id="doc-1",
            doctype="gov-id",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="auto_approved",
            message="Approved.",
        ),
        LedgerEntry(
            doc_id="doc-2",
            doctype="utility-bill",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="auto_approved",
            message="Approved.",
        ),
    ]

    outstanding = project_outstanding(entries)

    assert outstanding.requirements == []
    assert outstanding.done is False  # Bridge never mints completeness


def test_project_collection_status_derives_outstanding():
    """project_collection_status derives outstanding when not supplied."""
    entries = [
        LedgerEntry(
            doc_id="doc-1",
            doctype="gov-id",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="auto_approved",
            message="Approved.",
        ),
    ]

    status = project_collection_status(entries)

    assert status.entries == entries
    assert status.outstanding is not None
    assert status.outstanding.done is False
    assert status.outstanding.requirements == []


def test_project_collection_status_respects_explicit_outstanding():
    """project_collection_status respects an explicitly supplied outstanding."""
    entries = [
        LedgerEntry(
            doc_id="doc-1",
            doctype="gov-id",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="auto_approved",
            message="Approved.",
        ),
    ]
    # Explicitly pass a custom outstanding
    from contract.wire import Requirement, RequirementsList

    custom_outstanding = RequirementsList(
        requirements=[
            Requirement(
                item="custom",
                status=RequirementStatus.required,
            ),
        ],
        done=False,
    )

    status = project_collection_status(entries, outstanding=custom_outstanding)

    assert status.entries == entries
    assert status.outstanding == custom_outstanding


# ========== One-turn collect path (COLL-4) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_corpus_single_doc_collect(record):
    """Corpus parametrized single-doc collect: each record disposes correctly."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof")

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(doc_id=record["id"], artifact_name=record["artifact"])
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Check terminal and single entry
    assert turn.terminal is True
    assert len(turn.status.entries) == 1

    entry = turn.status.entries[0]

    # Check disposition and gate match expected
    assert entry.disposition.value == record["expected_disposition"]
    assert entry.gate.value == record["expected_gate"]

    # Check issuer (canonical)
    assert entry.issuer == record["issuer"]

    # Check key_fields
    assert entry.key_fields == record["extraction"]["fields"].get("key_fields", {})

    # Check reason_code and message are non-empty
    assert entry.reason_code is not None
    assert entry.reason_code != ""
    assert entry.message is not None
    assert entry.message != ""

    # Check requirements is None (app-owned)
    assert turn.requirements is None


async def test_outstanding_reflects_disposition_accepted():
    """Outstanding reflects disposition: accepted record -> empty requirements."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof")

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(
                doc_id="gov-id-clean", artifact_name="images/gov-id-clean.svg"
            )
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Accepted entry -> no outstanding requirements
    assert turn.status.outstanding is not None
    assert turn.status.outstanding.requirements == []
    assert turn.status.outstanding.done is False


async def test_outstanding_reflects_disposition_rejected():
    """Outstanding reflects disposition: rejected record -> one requirement with reason_code/message."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof")

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(
                doc_id="bill-blurry", artifact_name="images/bill-aquautil-blurry.svg"
            )
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Rejected entry -> one outstanding requirement
    assert turn.status.outstanding is not None
    assert len(turn.status.outstanding.requirements) == 1
    assert turn.status.outstanding.done is False

    req = turn.status.outstanding.requirements[0]
    entry = turn.status.entries[0]

    # Requirement carries the same reason_code/message as the entry
    assert req.reason_code == entry.reason_code
    assert req.message == entry.message
    assert req.status == RequirementStatus.required


async def test_context_id_minting():
    """context_id minting: None in request -> non-empty string in turn."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof", context_id=None)

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(
                doc_id="gov-id-clean", artifact_name="images/gov-id-clean.svg"
            )
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Minted context_id is non-empty
    assert turn.context_id is not None
    assert turn.context_id != ""


async def test_context_id_echo():
    """context_id echo: supplied in request -> echoed unchanged in turn."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof", context_id="ctx-abc-123")

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(
                doc_id="gov-id-clean", artifact_name="images/gov-id-clean.svg"
            )
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Context ID is echoed unchanged
    assert turn.context_id == "ctx-abc-123"


async def test_multi_doc_preserves_order_and_accumulates():
    """Multi-doc set preserves order + accumulates: two docs in order -> two entries in order."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof")

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(
                doc_id="bill-1", artifact_name="images/bill-powerco-clean.svg"
            ),
            DocumentSubmission(
                doc_id="bill-2", artifact_name="images/bill-powerco-clean-2.svg"
            ),
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Two entries in the same order as the documents
    assert len(turn.status.entries) == 2

    # Check first entry
    entry1 = turn.status.entries[0]
    assert entry1.doc_id == "bill-1"
    assert entry1.disposition == Disposition.accepted
    assert entry1.issuer == "power-co"

    # Check second entry
    entry2 = turn.status.entries[1]
    assert entry2.doc_id == "bill-2"
    assert entry2.disposition == Disposition.accepted
    assert entry2.issuer == "power-co"

    # Both accepted -> no outstanding requirements
    assert turn.status.outstanding.requirements == []


async def test_round_trip():
    """Round-trip: ExchangeTurn survives model_dump -> model_validate."""
    collector = Collector(FixtureExtractor())
    request = CollectRequest(process="address-proof")

    turn = await collector.collect(
        request,
        documents=[
            DocumentSubmission(
                doc_id="gov-id-clean", artifact_name="images/gov-id-clean.svg"
            )
        ],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Round-trip through JSON
    from contract.wire import ExchangeTurn

    dumped = turn.model_dump(mode="json")
    reconstructed = ExchangeTurn.model_validate(dumped)

    # Check key fields match
    assert reconstructed.context_id == turn.context_id
    assert reconstructed.terminal == turn.terminal
    assert reconstructed.requirements == turn.requirements
    assert len(reconstructed.status.entries) == len(turn.status.entries)
    assert reconstructed.status.entries[0].doc_id == turn.status.entries[0].doc_id
    assert (
        reconstructed.status.entries[0].disposition
        == turn.status.entries[0].disposition
    )


def test_project_ledger_entry_carries_the_review_view():
    """S0-debt-2: the ledger carries dotted paths + WHY; reason_code is unchanged."""
    result = ExtractionResult(
        fields=ExtractionFields(
            doctype="gov-id",
            key_fields={"name": "Jordan Lee"},
        ),
        overall_confidence=0.72,
        field_confidence={"name": 0.55},
        legible=True,
        flagged_fields=["name", "expiry"],
    )
    verdict = DispositionVerdict(
        disposition=Disposition.pending,
        gate=Gate.hitl_review,
        reason_code="flagged_fields",
        message="The following fields require human review: name, expiry.",
    )

    entry = project_ledger_entry(doc_id="doc-r", result=result, verdict=verdict)

    assert entry.reason_code == "flagged_fields"
    assert [(r.path, r.reason.value, r.confidence) for r in entry.field_reviews] == [
        ("fields.key_fields.name", "model_flagged", 0.55),
        ("fields.key_fields.expiry", "absent_from_document", None),
    ]
    # Additive + defaulted: a LedgerEntry round-trips with the new field.
    assert LedgerEntry.model_validate(entry.model_dump(mode="json")) == entry


def test_project_ledger_entry_has_no_reviews_for_a_clean_document():
    """No flags, no engine reviews → an empty review view (the corpus's normal case)."""
    result = ExtractionResult(
        fields=ExtractionFields(doctype="gov-id", key_fields={"name": "Jordan Lee"}),
        overall_confidence=0.97,
        legible=True,
        flagged_fields=[],
    )
    verdict = DispositionVerdict(
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        reason_code="auto_approved",
        message="Accepted automatically.",
    )
    entry = project_ledger_entry(doc_id="doc-c", result=result, verdict=verdict)
    assert entry.field_reviews == []


# ========== Engine confidence on the wire (S0-arch-9, contract minor 1.3) ==========


def _clean_verdict() -> DispositionVerdict:
    return DispositionVerdict(
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        reason_code="auto_approved",
        message="Accepted automatically.",
    )


def test_project_ledger_entry_carries_engine_confidence():
    """The consumer's ``extraction_confidence`` + per-field confidence reach the ledger."""
    result = ExtractionResult(
        fields=ExtractionFields(doctype="gov-id", key_fields={"name": "Jordan Lee"}),
        overall_confidence=0.91,
        field_confidence={"name": 0.88, "expiry": 0.93},
        legible=True,
    )
    entry = project_ledger_entry(
        doc_id="doc-k", result=result, verdict=_clean_verdict()
    )
    assert entry.overall_confidence == 0.91
    assert entry.field_confidence == {"name": 0.88, "expiry": 0.93}
    # A copy, not the engine's own dict.
    assert entry.field_confidence is not result.field_confidence


def test_ledger_entry_confidence_defaults_mean_no_engine_number():
    """``None`` (not 0.0) distinguishes "no engine number" from a real zero."""
    entry = LedgerEntry(
        doc_id="d",
        doctype="gov-id",
        disposition="rejected",
        gate="unsupported",
    )
    assert entry.overall_confidence is None
    assert entry.field_confidence == {}


def _turn_payload(version: str, *, extra_entry_key: bool = False) -> dict:
    turn = ExchangeTurn(
        context_id="c",
        status=CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="d",
                    doctype="gov-id",
                    disposition="accepted",
                    gate="auto_approve",
                )
            ]
        ),
    ).model_dump(mode="json")
    turn["contract_version"] = version
    entry = turn["status"]["entries"][0]
    del entry["overall_confidence"]
    del entry["field_confidence"]
    if extra_entry_key:
        entry["added_in_a_later_minor"] = "x"
    return turn


def test_pre_1_3_turn_without_confidence_decodes_with_defaults():
    turn = decode_wire(ExchangeTurn, _turn_payload("1.2"))
    entry = turn.status.entries[0]
    assert entry.overall_confidence is None
    assert entry.field_confidence == {}


def test_newer_minor_turn_with_unknown_entry_key_decodes_tolerantly():
    newer = f"{CONTRACT_MAJOR}.{_minor(CONTRACT_VERSION) + 1}"
    turn = decode_wire(ExchangeTurn, _turn_payload(newer, extra_entry_key=True))
    assert turn.status.entries[0].doc_id == "d"
