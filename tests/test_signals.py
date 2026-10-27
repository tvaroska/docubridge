"""Tests for the four-signal SignalProvider (CORE-4)."""

import json
from pathlib import Path

import pytest

from bridge.extraction.agent import extract_document
from bridge.extraction.fixture import FixtureExtractor
from bridge.signals import (
    ExtractionSignalProvider,
    SignalProvider,
    dispose_signals,
    signals_from_result,
)
from contract.enums import Gate, ReviewReason
from contract.extraction import ExtractionFields, ExtractionResult, FieldReview

# Load the corpus for oracle tests
CORPUS_PATH = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
with open(CORPUS_PATH) as f:
    CORPUS_DATA = json.load(f)
CORPUS_RECORDS = CORPUS_DATA["documents"]


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_provider_produces_four_signals_from_corpus(record):
    """ExtractionSignalProvider produces the four signals from the corpus."""
    provider = ExtractionSignalProvider()
    signals = await provider.signals_for(
        record["artifact"],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Check signals match extraction block
    assert signals.legible == record["extraction"]["legible"]
    assert signals.overall_confidence == record["extraction"]["overall_confidence"]
    assert signals.flagged_fields == record["extraction"]["flagged_fields"]
    assert signals.doctype == record["extraction"]["fields"]["doctype"]

    # Check type_match computed property
    expected_type_match = record["extraction"]["fields"]["doctype"] in [
        "gov-id",
        "utility-bill",
    ]
    assert signals.type_match == expected_type_match

    # Check canonical issuer
    assert signals.issuer == record["issuer"]


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_signals_to_gate_reproduces_corpus_verdicts(record):
    """dispose_signals reproduces the corpus expected_disposition/expected_gate."""
    provider = ExtractionSignalProvider()
    signals = await provider.signals_for(
        record["artifact"],
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    verdict = dispose_signals(signals)

    # Check disposition and gate match expected
    assert verdict.disposition.value == record["expected_disposition"]
    assert verdict.gate.value == record["expected_gate"]


async def test_signal_provider_is_abstract():
    """SignalProvider is abstract and cannot be instantiated."""
    with pytest.raises(TypeError):
        SignalProvider()  # type: ignore


async def test_injected_extractor_works():
    """ExtractionSignalProvider works with an injected extractor (no port)."""
    # Construct with explicit fixture
    provider = ExtractionSignalProvider(extractor=FixtureExtractor())

    signals = await provider.signals_for(
        "bill-powerco-clean",
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    # Check canonical issuer
    assert signals.issuer == "power-co"
    assert signals.doctype == "utility-bill"
    assert signals.legible is True


async def test_extract_document_surfaces_canonical_issuer():
    """extract_document surfaces the canonical issuer in classification block."""
    result = await extract_document("bill-powerco-clean")

    # Check canonical issuer is in classification
    assert result["classification"]["issuer"] == "power-co"
    assert result["classification"]["doctype"] == "utility-bill"


async def test_engine_stated_reviews_reach_the_gate_without_flagged_fields():
    """S0-debt-2: an engine that states WHY (field_reviews) but leaves flagged_fields
    empty must NOT auto-approve — the review view is the fallback for signal 3."""
    result = ExtractionResult(
        fields=ExtractionFields(doctype="gov-id", key_fields={"name": "Jordan Lee"}),
        overall_confidence=0.99,
        legible=True,
        flagged_fields=[],
        field_reviews=[
            FieldReview(path="fields.key_fields.name", reason=ReviewReason.derived),
        ],
    )
    signals = signals_from_result(result, candidate_doctypes=["gov-id"])
    assert signals.flagged_fields == ["fields.key_fields.name"]

    verdict = dispose_signals(signals)
    assert verdict.gate is Gate.hitl_review
    assert verdict.reason_code == "flagged_fields"


async def test_raw_flagged_fields_win_over_the_projection():
    """The bare names stay bare when the engine supplied them (corpus parity)."""
    result = ExtractionResult(
        fields=ExtractionFields(doctype="gov-id", key_fields={"name": "Jordan Lee"}),
        overall_confidence=0.7,
        legible=True,
        flagged_fields=["name"],
        field_reviews=[
            FieldReview(path="fields.doctype", reason=ReviewReason.low_confidence),
        ],
    )
    signals = signals_from_result(result, candidate_doctypes=["gov-id"])
    assert signals.flagged_fields == ["name"]
