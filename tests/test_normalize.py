"""Normalize layer tests (S1-benefits-2, beat 3) — one schema / currency / language.

Proves the beat-3 transform: every carrier ``benefits-quote`` FX-converts + translates to
one canonical ``NormalizedQuote``. The load-bearing gate is the **corpus oracle** — every
document in ``wiki/evals/benefits/expected.json`` reproduces its ``expected_normalized``
block through ``normalize_from_extraction`` (iterated, so adding a quote later is free).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.extraction.fixture import FixtureExtractor
from bridge.normalize import (
    TARGET_CURRENCY,
    TARGET_LANGUAGE,
    NormalizedQuote,
    fixed_fx,
    normalize_from_extraction,
    normalize_quote,
)

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)


class TestFxAndTranslate:
    def test_eur_fr_converts_and_translates(self):
        q = normalize_quote(
            party="carrier-c",
            currency="EUR",
            language="fr",
            key_fields={
                "premium": "1000.00",
                "deductible": "500.00",
                "coverage_limits": "soins dentaires et optique",
                "exclusions": "chirurgie esthétique",
                "effective_date": "2026-01-01",
                "expiry_date": "2026-12-31",
            },
        )
        assert q.currency == TARGET_CURRENCY == "USD"
        assert q.language == TARGET_LANGUAGE == "en"
        assert q.premium == "1100.00"  # 1000.00 * 1.10
        assert q.deductible == "550.00"  # 500.00 * 1.10
        assert q.coverage_limits == "dental and vision care"
        assert q.exclusions == "cosmetic surgery"
        assert q.original_currency == "EUR"
        assert q.original_language == "fr"

    def test_gbp_de_converts_and_translates(self):
        q = normalize_quote(
            party="carrier-d",
            currency="GBP",
            language="de",
            key_fields={
                "premium": "800.00",
                "deductible": "400.00",
                "coverage_limits": "Zahn- und Sehkraftversorgung",
                "exclusions": "kosmetische Chirurgie",
                "effective_date": "2026-01-01",
                "expiry_date": "2026-12-31",
            },
        )
        assert q.premium == "1000.00"  # 800.00 * 1.25
        assert q.deductible == "500.00"  # 400.00 * 1.25
        assert q.coverage_limits == "dental and vision care"
        assert q.exclusions == "cosmetic surgery"

    def test_dates_pass_through(self):
        q = normalize_quote(
            party="carrier-c",
            currency="EUR",
            language="fr",
            key_fields={
                "premium": "1000.00",
                "deductible": "500.00",
                "coverage_limits": "x",
                "exclusions": "y",
                "effective_date": "2026-03-15",
                "expiry_date": "2027-03-14",
            },
        )
        assert q.effective_date == "2026-03-15"
        assert q.expiry_date == "2027-03-14"

    def test_leading_currency_symbol_is_stripped(self):
        q = normalize_quote(
            party="carrier-c",
            currency="EUR",
            language="fr",
            key_fields={
                "premium": "€1,000.00",
                "deductible": "500.00",
                "coverage_limits": "x",
                "exclusions": "y",
                "effective_date": "2026-01-01",
                "expiry_date": "2026-12-31",
            },
        )
        assert q.premium == "1100.00"

    def test_unmapped_phrase_passes_through(self):
        q = normalize_quote(
            party="carrier-c",
            currency="EUR",
            language="fr",
            key_fields={
                "premium": "1000.00",
                "deductible": "500.00",
                "coverage_limits": "phrase inconnue",
                "exclusions": "y",
                "effective_date": "2026-01-01",
                "expiry_date": "2026-12-31",
            },
        )
        # Unknown phrase degrades visibly (native text survives), does not crash.
        assert q.coverage_limits == "phrase inconnue"


class TestIdempotency:
    def test_usd_en_normalizes_to_itself(self):
        kf = {
            "premium": "1200.00",
            "deductible": "600.00",
            "coverage_limits": "dental, vision",
            "exclusions": "none",
            "effective_date": "2026-01-01",
            "expiry_date": "2026-12-31",
        }
        q = normalize_quote(
            party="carrier-a", currency="USD", language="en", key_fields=kf
        )
        assert (q.currency, q.language) == ("USD", "en")
        assert q.premium == "1200.00"
        assert q.deductible == "600.00"
        assert q.coverage_limits == "dental, vision"
        assert q.exclusions == "none"
        assert q.original_currency == "USD"
        assert q.original_language == "en"


class TestErrors:
    def test_unknown_currency_raises_listing_known_codes(self):
        with pytest.raises(KeyError, match="known codes"):
            normalize_quote(
                party="carrier-x",
                currency="JPY",
                language="en",
                key_fields={
                    "premium": "1000.00",
                    "deductible": "500.00",
                    "coverage_limits": "x",
                    "exclusions": "y",
                    "effective_date": "2026-01-01",
                    "expiry_date": "2026-12-31",
                },
            )

    def test_fixed_fx_unknown_raises(self):
        with pytest.raises(KeyError, match="JPY"):
            fixed_fx("JPY")

    def test_missing_currency_in_key_fields_raises(self):
        from contract.extraction import (
            ExtractionFields,
            ExtractionResult,
        )

        result = ExtractionResult(
            fields=ExtractionFields(
                doctype="benefits-quote",
                key_fields={
                    "language": "en",
                    "premium": "1000.00",
                    "deductible": "500.00",
                    "coverage_limits": "x",
                    "exclusions": "y",
                    "effective_date": "2026-01-01",
                    "expiry_date": "2026-12-31",
                },
            ),
            overall_confidence=0.9,
            legible=True,
        )
        with pytest.raises(ValueError, match="currency"):
            normalize_from_extraction("carrier-a", result)


class TestDeterminism:
    def test_normalize_twice_is_equal(self):
        kf = {
            "premium": "1000.00",
            "deductible": "500.00",
            "coverage_limits": "soins dentaires et optique",
            "exclusions": "chirurgie esthétique",
            "effective_date": "2026-01-01",
            "expiry_date": "2026-12-31",
        }
        q1 = normalize_quote(
            party="c", currency="EUR", language="fr", key_fields=kf
        )
        q2 = normalize_quote(
            party="c", currency="EUR", language="fr", key_fields=kf
        )
        assert q1 == q2


class TestCorpusOracle:
    """The load-bearing beat-3 gate: every corpus quote reproduces expected_normalized."""

    async def test_every_quote_reproduces_expected_normalized(self):
        data = json.loads(BENEFITS_CORPUS.read_text())
        docs = data["documents"]
        # 4 base carrier quotes + carrier-b's revised v2 quote (S1-benefits-5); every
        # document (base or revised) must reproduce its own expected_normalized block.
        assert len(docs) == 5, "expected 4 carrier quotes + 1 revised in the benefits corpus"

        extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
        for doc in docs:
            party = doc["party"]
            result = await extractor.extract(doc["id"])
            actual = normalize_from_extraction(party, result)
            expected = NormalizedQuote.model_validate(doc["expected_normalized"])
            assert actual == expected, f"normalize mismatch for {doc['id']}"
