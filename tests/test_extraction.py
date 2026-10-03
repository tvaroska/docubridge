"""Tests for the extraction seam and fixture engine."""

import json
from pathlib import Path

import pytest

from bridge.disposition import run_disposition_gate
from bridge.extraction.fixture import FixtureExtractor
from bridge.seams import Seam, SeamMode, get_adapter
from contract.extraction import ExtractionResult

# Load the corpus for oracle tests
CORPUS_PATH = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
with open(CORPUS_PATH) as f:
    CORPUS_DATA = json.load(f)
CORPUS_RECORDS = CORPUS_DATA["documents"]


@pytest.mark.parametrize("record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS])
async def test_corpus_round_trip_parity(record):
    """Fixture extractor reproduces corpus extraction blocks exactly."""
    extractor = FixtureExtractor()
    result = await extractor.extract(record["artifact"])
    expected = ExtractionResult.model_validate(record["extraction"])

    # Check field-by-field
    assert result.fields.doctype == expected.fields.doctype
    assert result.fields.issuer == expected.fields.issuer
    assert result.fields.key_fields == expected.fields.key_fields
    assert result.legible == expected.legible
    assert result.overall_confidence == expected.overall_confidence
    assert result.flagged_fields == expected.flagged_fields
    assert result.field_confidence == expected.field_confidence


async def test_lookup_by_id_and_filename():
    """Lookup works by both id and full artifact path."""
    extractor = FixtureExtractor()

    # Lookup by id
    result_id = await extractor.extract("gov-id-clean")

    # Lookup by filename
    result_filename = await extractor.extract("images/gov-id-clean.svg")

    # Should be the same
    assert result_id == result_filename
    assert result_id.fields.doctype == "gov-id"


async def test_determinism():
    """Two extract calls with the same key return equal results."""
    extractor = FixtureExtractor()

    result1 = await extractor.extract("gov-id-clean")
    result2 = await extractor.extract("gov-id-clean")

    assert result1 == result2


@pytest.mark.parametrize("record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS])
async def test_signals_feed_gate_to_expected_disposition(record):
    """Extraction signals wire cleanly into the disposition gate."""
    extractor = FixtureExtractor()
    result = await extractor.extract(record["artifact"])

    # Run the gate with the extracted signals
    verdict = run_disposition_gate(
        doctype=result.fields.doctype,
        legible=result.legible,
        overall_confidence=result.overall_confidence,
        flagged_fields=result.flagged_fields,
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Check that the verdict matches expected
    assert verdict["disposition"] == record["expected_disposition"]
    assert verdict["gate"] == record["expected_gate"]


async def test_unknown_key_raises_loudly():
    """Unknown keys raise KeyError with a helpful message."""
    extractor = FixtureExtractor()

    with pytest.raises(KeyError) as exc_info:
        await extractor.extract("no-such-doc.svg")

    error_message = str(exc_info.value)
    assert "no fixture extraction for" in error_message
    assert "no-such-doc.svg" in error_message
    assert "known ids:" in error_message


@pytest.mark.seam("extraction")
async def test_seam_adapter_parity():
    """Seam adapter (local) returns same results as direct fixture."""
    # Get adapter via seam harness
    adapter = get_adapter(Seam.extraction, SeamMode.local)

    # Check seam identity
    assert adapter.seam is Seam.extraction

    # Extract via adapter
    adapter_result = await adapter.extract("gov-id-clean")

    # Extract via direct fixture
    fixture = FixtureExtractor()
    fixture_result = await fixture.extract("gov-id-clean")

    # Should be the same
    assert adapter_result == fixture_result
