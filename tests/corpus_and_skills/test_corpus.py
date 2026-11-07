"""TEST-2: Eval-corpus parity tests.

Canonical consolidated test module for the address eval corpus.
See wiki/evals/address/description.md ("Consumers (parity)").
"""

import re
from pathlib import Path

import pytest

from bridge.classification import canonicalize_issuer
from bridge.collect import Collector, DocumentSubmission
from bridge.disposition import run_disposition_gate
from bridge.extraction.fixture import FixtureExtractor
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionResult
from contract.wire import CollectRequest
from tests.corpus_and_skills._corpus import (
    CANDIDATE_DOCTYPES,
    PARTY,
    SYNTHETIC_IDENTITY,
    corpus_records,
    load_corpus,
    load_timeline,
)

# Parametrize over corpus records
CORPUS_RECORDS = corpus_records()

# The local demo's upload picker (provider portal); its CORPUS_IDS must stay corpus ids.
PORTAL_UPLOAD_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "frontend/provider-portal/src/components/UploadPrompt.tsx"
)


# ========== A. Well-formedness (parametrized) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
def test_record_wellformed(record):
    """Every corpus record has required keys and validates."""
    # Assert required keys present
    required_keys = {
        "id",
        "doctype",
        "issuer_raw",
        "issuer",
        "expected_disposition",
        "expected_gate",
        "extraction",
        "artifact",
        "synthetic",
        "note",
    }
    assert required_keys <= set(record.keys()), f"Missing keys in {record['id']}"

    # Assert enums construct
    Disposition(record["expected_disposition"])
    Gate(record["expected_gate"])

    # Assert extraction validates
    ExtractionResult.model_validate(record["extraction"])


# ========== B. Disposition/gate parity through pure gate (parametrized) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
def test_gate_parity(record):
    """Every record's disposition/gate matches through run_disposition_gate."""
    extraction = record["extraction"]
    result = run_disposition_gate(
        doctype=record["doctype"],
        candidate_doctypes=CANDIDATE_DOCTYPES,
        legible=extraction["legible"],
        overall_confidence=extraction["overall_confidence"],
        flagged_fields=extraction.get("flagged_fields", []),
    )

    assert result["disposition"] == record["expected_disposition"], (
        f"{record['id']}: expected disposition {record['expected_disposition']}, "
        f"got {result['disposition']}"
    )
    assert result["gate"] == record["expected_gate"], (
        f"{record['id']}: expected gate {record['expected_gate']}, got {result['gate']}"
    )


# ========== C. Determinism-on-repeat through pure gate (parametrized) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
def test_gate_deterministic(record):
    """run_disposition_gate is deterministic on identical inputs."""
    extraction = record["extraction"]
    args = {
        "doctype": record["doctype"],
        "candidate_doctypes": CANDIDATE_DOCTYPES,
        "legible": extraction["legible"],
        "overall_confidence": extraction["overall_confidence"],
        "flagged_fields": extraction.get("flagged_fields", []),
    }

    result1 = run_disposition_gate(**args)
    result2 = run_disposition_gate(**args)

    assert result1 == result2, f"{record['id']}: gate not deterministic"


# ========== D. Full collect-path parity + determinism (parametrized, async, port) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_collect_path_parity(record):
    """Every record produces expected disposition/gate/issuer through Collector, deterministically."""
    collector = Collector(FixtureExtractor())
    # Fixed context_id for determinism
    request = CollectRequest(process="address-proof", context_id="det-test")

    # Run collect twice with identical inputs
    turn1 = await collector.collect(
        request,
        documents=[
            DocumentSubmission(doc_id=record["id"], artifact_name=record["artifact"])
        ],
        candidate_doctypes=CANDIDATE_DOCTYPES,
    )
    turn2 = await collector.collect(
        request,
        documents=[
            DocumentSubmission(doc_id=record["id"], artifact_name=record["artifact"])
        ],
        candidate_doctypes=CANDIDATE_DOCTYPES,
    )

    # Assert terminal and single entry
    assert turn1.terminal is True
    assert len(turn1.status.entries) == 1
    assert turn2.terminal is True
    assert len(turn2.status.entries) == 1

    entry1 = turn1.status.entries[0]
    turn2.status.entries[0]

    # Assert expected values
    assert entry1.disposition.value == record["expected_disposition"], (
        f"{record['id']}: expected disposition {record['expected_disposition']}, "
        f"got {entry1.disposition.value}"
    )
    assert entry1.gate.value == record["expected_gate"], (
        f"{record['id']}: expected gate {record['expected_gate']}, "
        f"got {entry1.gate.value}"
    )
    assert entry1.issuer == record["issuer"], (
        f"{record['id']}: expected issuer {record['issuer']}, got {entry1.issuer}"
    )

    # Assert determinism: with fixed context_id, full turns are equal
    assert turn1.model_dump() == turn2.model_dump(), (
        f"{record['id']}: collect not deterministic on repeat"
    )


# ========== E. Fixture ↔ corpus resolution (parametrized, async, port) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_fixture_resolves_every_id(record):
    """Every corpus artifact and id resolves through FixtureExtractor."""
    extractor = FixtureExtractor()
    expected = ExtractionResult.model_validate(record["extraction"])

    # Resolve by artifact name
    result_by_artifact = await extractor.extract(record["artifact"])
    assert result_by_artifact == expected, (
        f"{record['id']}: extraction via artifact {record['artifact']} mismatch"
    )

    # Resolve by bare id
    result_by_id = await extractor.extract(record["id"])
    assert result_by_id == expected, f"{record['id']}: extraction via id mismatch"


# ========== E2. Portal samples ↔ corpus (single, async) ==========


def portal_sample_ids() -> list[str]:
    """The demo portal's upload-picker ids (`CORPUS_IDS` in UploadPrompt.tsx)."""
    source = PORTAL_UPLOAD_PROMPT.read_text()
    match = re.search(r"const CORPUS_IDS\s*=\s*\[(.*?)\];", source, re.DOTALL)
    assert match, f"CORPUS_IDS array not found in {PORTAL_UPLOAD_PROMPT}"
    ids = re.findall(r'"([^"]+)"', match.group(1))
    assert ids, f"CORPUS_IDS in {PORTAL_UPLOAD_PROMPT} is empty"
    return ids


async def test_portal_samples_resolve_to_corpus():
    """Every id the portal's upload picker offers is a corpus doc and resolves
    through FixtureExtractor to that doc's exact extraction block."""
    sample_ids = portal_sample_ids()
    assert len(sample_ids) == len(set(sample_ids)), "duplicate portal sample ids"
    records = {r["id"]: r for r in corpus_records()}
    extractor = FixtureExtractor()
    for sample_id in sample_ids:
        assert sample_id in records, (
            f"portal sample {sample_id!r} ({PORTAL_UPLOAD_PROMPT.name}) "
            f"is not a corpus id in expected.json"
        )
        expected = ExtractionResult.model_validate(records[sample_id]["extraction"])
        assert await extractor.extract(sample_id) == expected, (
            f"portal sample {sample_id!r} does not resolve to its corpus extraction"
        )


# ========== F. Canonicalization parity (parametrized) ==========


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
def test_issuer_canonicalization_parity(record):
    """Issuer canonicalization matches corpus expectation."""
    # Skip records with null issuer_raw (gov-id records)
    if record["issuer_raw"] is None:
        assert record["issuer"] is None
        return

    canonical = canonicalize_issuer(record["issuer_raw"])
    assert canonical == record["issuer"], (
        f"{record['id']}: canonicalize_issuer({record['issuer_raw']!r}) -> {canonical!r}, "
        f"expected {record['issuer']!r}"
    )


# ========== G. Category coverage (single, non-parametrized) ==========


def test_category_coverage():
    """Corpus covers all 3 dispositions and all 4 gates."""
    records = corpus_records()
    dispositions = {r["expected_disposition"] for r in records}
    gates = {r["expected_gate"] for r in records}

    # All three dispositions
    assert dispositions == {"accepted", "rejected", "pending"}, (
        f"Corpus missing dispositions: expected {{accepted, rejected, pending}}, got {dispositions}"
    )

    # All four gates
    assert gates == {"auto_approve", "hitl_review", "resubmit", "unsupported"}, (
        f"Corpus missing gates: expected {{auto_approve, hitl_review, resubmit, unsupported}}, got {gates}"
    )


# ========== H. No-PII (single + per-record) ==========


def test_no_pii():
    """Corpus contains only synthetic data with the sanctioned identity."""
    corpus = load_corpus()

    # Assert correct party
    assert corpus["party"] == PARTY, f"Expected party {PARTY}, got {corpus['party']}"

    # Assert all records are synthetic
    for record in corpus_records():
        assert record["synthetic"] is True, f"{record['id']}: not marked synthetic"

        # If extraction has key_fields with name, it must be the sanctioned identity
        extraction = record["extraction"]
        key_fields = extraction.get("fields", {}).get("key_fields", {})
        if "name" in key_fields:
            assert key_fields["name"] == SYNTHETIC_IDENTITY, (
                f"{record['id']}: name {key_fields['name']!r} != sanctioned {SYNTHETIC_IDENTITY!r}"
            )


# ========== I. Timeline validity (single) ==========


def test_timeline_validity():
    """Timeline references valid corpus ids and has well-formed SLA blocks."""
    timeline = load_timeline()
    corpus_ids = {r["id"] for r in corpus_records()}

    for exchange in timeline["exchanges"]:
        # Check party
        assert exchange["party"] == PARTY, (
            f"Exchange {exchange['id']}: expected party {PARTY}, got {exchange['party']}"
        )

        # Check SLA block
        sla = exchange["sla"]
        for field in [
            "overdue_ticks",
            "reminder_ticks",
            "escalate_ticks",
            "resubmit_cap",
        ]:
            assert isinstance(sla[field], int), (
                f"Exchange {exchange['id']}: sla.{field} not an integer"
            )

        # Check beats
        for beat in exchange["beats"]:
            # If beat references a document, it must exist in corpus
            if "document" in beat:
                assert beat["document"] in corpus_ids, (
                    f"Exchange {exchange['id']}, beat tick {beat['tick']}: "
                    f"document {beat['document']} not in corpus"
                )

                # If beat has expected_disposition, it must match corpus
                # (Do NOT check expected_gate — timeline uses shorthand "hitl" != "hitl_review")
                if "expected_disposition" in beat:
                    corpus_record = next(
                        r for r in corpus_records() if r["id"] == beat["document"]
                    )
                    assert (
                        beat["expected_disposition"]
                        == corpus_record["expected_disposition"]
                    ), (
                        f"Exchange {exchange['id']}, beat tick {beat['tick']}, "
                        f"document {beat['document']}: "
                        f"expected_disposition {beat['expected_disposition']} != "
                        f"corpus {corpus_record['expected_disposition']}"
                    )
