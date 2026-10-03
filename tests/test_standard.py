"""Standard-gap flagger tests (S1-benefits-3, beat 4) — sense A, surfaces the gap.

Proves the beat-4 transform: every normalized carrier quote is flagged against the
declared program-standard rules; a flagged gap routes to negotiate; a conformant quote
routes nowhere. The load-bearing gate is the **corpus oracle** — every document in
``wiki/evals/benefits/expected.json`` reproduces its ``expected_gaps`` block through
``flag_quote`` (iterated), and Carrier B alone is non-conformant across the program.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.extraction.fixture import FixtureExtractor
from bridge.normalize import NormalizedQuote, normalize_from_extraction
from bridge.standard import (
    ROUTE_NEGOTIATE,
    ROUTE_NONE,
    ProgramStandard,
    StandardGapVerdict,
    StandardRule,
    flag_program,
    flag_quote,
    load_program_standard,
)

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)


def _quote(**overrides) -> NormalizedQuote:
    """A fully-conformant baseline normalized quote; override fields per test."""
    base = {
        "party": "carrier-x",
        "currency": "USD",
        "language": "en",
        "premium": "1200.00",
        "deductible": "600.00",
        "coverage_limits": "dental, vision",
        "exclusions": "cosmetic surgery",
        "effective_date": "2026-01-01",
        "expiry_date": "2026-12-31",
        "original_currency": "USD",
        "original_language": "en",
    }
    base.update(overrides)
    return NormalizedQuote(**base)


class TestMaxCheck:
    def test_conforms_under_limit(self):
        rule = StandardRule(id="deductible_cap", field="deductible", check="max", limit=1000)
        assert flag_quote(_quote(deductible="750.00"), ProgramStandard((rule,), True)).conformant

    def test_flags_over_limit(self):
        rule = StandardRule(id="deductible_cap", field="deductible", check="max", limit=1000)
        verdict = flag_quote(_quote(deductible="1500.00"), ProgramStandard((rule,), True))
        assert not verdict.conformant
        gap = verdict.gaps[0]
        assert gap.rule_id == "deductible_cap"
        assert gap.expected == "<= 1000"
        assert gap.actual == "1500.00"


class TestMustIncludeCheck:
    def test_conforms_when_present(self):
        rule = StandardRule(id="must_cover_dental", field="coverage_limits", check="must_include", value="dental")
        assert flag_quote(_quote(coverage_limits="dental, vision"), ProgramStandard((rule,), True)).conformant

    def test_flags_when_absent(self):
        rule = StandardRule(id="must_cover_dental", field="coverage_limits", check="must_include", value="dental")
        verdict = flag_quote(_quote(coverage_limits="vision only"), ProgramStandard((rule,), True))
        assert not verdict.conformant
        assert verdict.gaps[0].rule_id == "must_cover_dental"


class TestMustNotIncludeCheck:
    def test_conforms_when_absent(self):
        rule = StandardRule(id="no_orthodontics_exclusion", field="exclusions", check="must_not_include", value="orthodontics")
        assert flag_quote(_quote(exclusions="cosmetic surgery"), ProgramStandard((rule,), True)).conformant

    def test_flags_when_present(self):
        rule = StandardRule(id="no_orthodontics_exclusion", field="exclusions", check="must_not_include", value="orthodontics")
        verdict = flag_quote(
            _quote(exclusions="cosmetic surgery, orthodontics"), ProgramStandard((rule,), True)
        )
        assert not verdict.conformant
        assert verdict.gaps[0].rule_id == "no_orthodontics_exclusion"


class TestRouting:
    def test_gap_routes_to_negotiate(self):
        rule = StandardRule(id="deductible_cap", field="deductible", check="max", limit=1000)
        verdict = flag_quote(_quote(deductible="2000.00"), ProgramStandard((rule,), True))
        assert verdict.conformant is False
        assert verdict.route == ROUTE_NEGOTIATE == "negotiate"

    def test_conformant_routes_none(self):
        rule = StandardRule(id="deductible_cap", field="deductible", check="max", limit=1000)
        verdict = flag_quote(_quote(deductible="500.00"), ProgramStandard((rule,), True))
        assert verdict.conformant is True
        assert verdict.route == ROUTE_NONE == "none"
        assert verdict.gaps == []


class TestSenseA:
    def test_verdict_only_routes_never_binds(self):
        # Sense A: the verdict carries no accept/reject/bind field — only a route.
        assert set(StandardGapVerdict.model_fields) == {"party", "conformant", "gaps", "route"}

    def test_only_negotiate_or_none_routes_emitted(self):
        std = load_program_standard("benefits-procurement")
        routes = {
            flag_quote(_quote(deductible="500.00"), std).route,
            flag_quote(_quote(deductible="9000.00"), std).route,
        }
        assert routes <= {ROUTE_NEGOTIATE, ROUTE_NONE}


class TestLoader:
    def test_loads_declared_standard(self):
        std = load_program_standard("benefits-procurement")
        assert [r.id for r in std.rules] == [
            "deductible_cap",
            "must_cover_dental",
            "no_orthodontics_exclusion",
        ]
        assert std.overridable is True

    def test_unknown_skill_raises(self):
        with pytest.raises(ValueError, match="not found"):
            load_program_standard("no-such-skill")


class TestErrors:
    def test_unknown_check_raises(self):
        with pytest.raises(ValueError, match="unknown check"):
            StandardRule.from_dict({"id": "r", "field": "deductible", "check": "bogus", "limit": 1})

    def test_unknown_field_raises(self):
        rule = StandardRule(id="r", field="nonexistent", check="max", limit=1000)
        with pytest.raises(ValueError, match="unknown field"):
            flag_quote(_quote(), ProgramStandard((rule,), True))


class TestCorpusOracle:
    """The load-bearing beat-4 gate: every corpus quote reproduces expected_gaps."""

    async def test_every_quote_reproduces_expected_gaps(self):
        data = json.loads(BENEFITS_CORPUS.read_text())
        docs = data["documents"]
        # 4 base carrier quotes + carrier-b's revised v2 quote (S1-benefits-5); every
        # document (base or revised) must reproduce its own expected_gaps block — the
        # revised carrier-b closes the gap, so its expected_gaps is [].
        assert len(docs) == 5, "expected 4 carrier quotes + 1 revised in the benefits corpus"

        std = load_program_standard("benefits-procurement")
        extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
        for doc in docs:
            party = doc["party"]
            result = await extractor.extract(doc["id"])
            verdict = flag_quote(normalize_from_extraction(party, result), std)
            violated = {g.rule_id for g in verdict.gaps}
            assert violated == set(doc["expected_gaps"]), f"gap mismatch for {doc['id']}"

    async def test_carrier_b_is_the_sole_flagged_leg(self):
        data = json.loads(BENEFITS_CORPUS.read_text())
        # One leg per carrier: the program's initial (v0) quotes. The revised carrier-b
        # version (`-v2`, S1-benefits-5) is a new version of the same leg, not a fifth leg.
        docs = [d for d in data["documents"] if not d["id"].endswith("-v2")]
        std = load_program_standard("benefits-procurement")
        extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)

        quotes = [
            normalize_from_extraction(d["party"], await extractor.extract(d["id"]))
            for d in docs
        ]
        verdicts = flag_program(quotes, std)
        negotiate = [v for v in verdicts if v.route == ROUTE_NEGOTIATE]
        none = [v for v in verdicts if v.route == ROUTE_NONE]
        assert len(negotiate) == 1
        assert negotiate[0].party == "carrier-b"
        assert negotiate[0].gaps[0].rule_id == "no_orthodontics_exclusion"
        assert len(none) == 3
