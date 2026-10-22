"""Program comparison aggregate (S1-benefits-4) — authored against the corpus oracle.

Normalizes all four benefits corpus quotes, builds the comparison, and asserts it
reproduces the oracle: one currency/language across every leg, and carrier-b the sole
non-conformant leg (``no_orthodontics_exclusion`` → route negotiate). The comparison is
composed from the S1-benefits-2 normalize + S1-benefits-3 flag outputs, so it is checked
against the authored oracle, not fitted to a test.
"""

from __future__ import annotations

import json
from pathlib import Path

from bridge.comparison import (
    ProgramComparison,
    build_comparison,
    comparison_artifact_ref,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.normalize import normalize_from_extraction
from bridge.standard import ROUTE_NEGOTIATE, ROUTE_NONE, load_program_standard

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)


async def _corpus_quotes():
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    docs = json.loads(BENEFITS_CORPUS.read_text())["documents"]
    # One leg per carrier: the program's initial (v0) quotes. The revised carrier-b
    # version (`-v2`, S1-benefits-5) is a new version of the same leg, not a fifth leg.
    base_docs = [d for d in docs if not d["id"].endswith("-v2")]
    return [
        normalize_from_extraction(doc["party"], await extractor.extract(doc["id"]))
        for doc in base_docs
    ]


class TestBuildComparison:
    async def test_reproduces_corpus_oracle(self):
        quotes = await _corpus_quotes()
        standard = load_program_standard("benefits-procurement")
        comparison = build_comparison("acme-2026", quotes, standard)

        assert isinstance(comparison, ProgramComparison)
        assert comparison.program_id == "acme-2026"
        assert comparison.currency == "USD"
        assert comparison.language == "en"
        assert len(comparison.legs) == 4

        # Every leg normalized to the one canonical currency/language.
        for leg in comparison.legs:
            assert leg.quote.currency == "USD"
            assert leg.quote.language == "en"

        # carrier-b is the sole flagged leg — the coverage gap.
        flagged = [
            leg.party for leg in comparison.legs if leg.verdict.route == ROUTE_NEGOTIATE
        ]
        assert flagged == ["carrier-b"]

        by_party = {leg.party: leg for leg in comparison.legs}
        carrier_b = by_party["carrier-b"]
        assert carrier_b.verdict.conformant is False
        assert [g.rule_id for g in carrier_b.verdict.gaps] == [
            "no_orthodontics_exclusion"
        ]

        for party in ("carrier-a", "carrier-c", "carrier-d"):
            leg = by_party[party]
            assert leg.verdict.route == ROUTE_NONE
            assert leg.verdict.conformant is True
            assert leg.verdict.gaps == []

    async def test_preserves_leg_order(self):
        quotes = await _corpus_quotes()
        standard = load_program_standard("benefits-procurement")
        comparison = build_comparison("acme-2026", quotes, standard)
        assert [leg.party for leg in comparison.legs] == [q.party for q in quotes]

    async def test_artifact_ref_is_by_reference(self):
        quotes = await _corpus_quotes()
        standard = load_program_standard("benefits-procurement")
        comparison = build_comparison("acme-2026", quotes, standard)
        ref = comparison_artifact_ref(comparison)
        assert ref.uri == "bridge://comparison/acme-2026"
        assert ref.name == "acme-2026-comparison"
        assert ref.mime_type == "application/json"
        # No bytes field on the handle — bytes never travel on the wire.
        assert set(type(ref).model_fields) == {"uri", "mime_type", "name"}


def test_build_comparison_is_pure_no_corpus():
    """build_comparison is a pure transform — verified with a hand-built quote (no IO)."""
    from bridge.normalize import normalize_quote

    quote = normalize_quote(
        party="carrier-z",
        currency="USD",
        language="en",
        key_fields={
            "premium": "1000.00",
            "deductible": "500.00",
            "coverage_limits": "dental, vision",
            "exclusions": "orthodontics",
            "effective_date": "2026-01-01",
            "expiry_date": "2026-12-31",
        },
    )
    standard = load_program_standard("benefits-procurement")
    comparison = build_comparison("p1", [quote], standard)
    assert comparison.legs[0].verdict.route == ROUTE_NEGOTIATE  # excludes orthodontics
    assert [g.rule_id for g in comparison.legs[0].verdict.gaps] == [
        "no_orthodontics_exclusion"
    ]
