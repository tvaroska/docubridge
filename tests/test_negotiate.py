"""Negotiate + versioned revise loop → bind → rollup unit tests (S1-benefits-5, beats 5,6).

Mirrors tests/test_comparison.py / test_standard.py: drives the pure Bridge-side aggregate
directly (hand-built and corpus-fed quotes) — v0 seeding, the versioned revise + recompute,
sense-B bind, the rollup + audit trail, the cap→escalate boundary, and the policy loader.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge.extraction.fixture import FixtureExtractor
from bridge.negotiate import (
    AuditEvent,
    BoundLeg,
    NegotiatePolicy,
    NegotiationProgram,
    ReviseCapExceeded,
    bind_leg,
    comparison_from_negotiation,
    load_negotiate_policy,
    open_negotiation,
    revise,
    roll_up,
)
from bridge.normalize import normalize_from_extraction, normalize_quote
from bridge.offscript import REASON_ESCALATED
from bridge.pathb import ESCALATE_MESSAGE
from bridge.program import Leg, fan_out
from bridge.standard import ROUTE_NEGOTIATE, ROUTE_NONE, load_program_standard

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)

CORPUS_DOC = {
    "carrier-a": "benefits-carrier-a-usd-en",
    "carrier-b": "benefits-carrier-b-usd-en",
    "carrier-c": "benefits-carrier-c-eur-fr",
    "carrier-d": "benefits-carrier-d-gbp-de",
}
REVISED_CARRIER_B = "benefits-carrier-b-usd-en-v2"


def _hand_quote(party: str, exclusions: str) -> object:
    return normalize_quote(
        party=party,
        currency="USD",
        language="en",
        key_fields={
            "premium": "1000.00",
            "deductible": "500.00",
            "coverage_limits": "dental, vision",
            "exclusions": exclusions,
            "effective_date": "2026-01-01",
            "expiry_date": "2026-12-31",
        },
    )


async def _corpus_program() -> tuple[NegotiationProgram, object]:
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    standard = load_program_standard("benefits-procurement")
    parties = ["carrier-a", "carrier-b", "carrier-c", "carrier-d"]
    program = fan_out("acme-2026", parties)
    quotes = {
        p: normalize_from_extraction(p, await extractor.extract(CORPUS_DOC[p]))
        for p in parties
    }
    neg = open_negotiation(
        "acme-2026", [(leg, quotes[leg.party]) for leg in program.legs], standard
    )
    return neg, standard


class TestOpenNegotiation:
    async def test_seeds_v0_per_leg_carrier_b_flagged(self):
        neg, _ = await _corpus_program()
        assert [leg.party for leg in neg.legs] == [
            "carrier-a",
            "carrier-b",
            "carrier-c",
            "carrier-d",
        ]
        for leg in neg.legs:
            assert len(leg.versions) == 1
            assert leg.current.version == 0
            assert leg.revise_count == 0
            assert leg.bound_version is None

        by_party = {leg.party: leg for leg in neg.legs}
        assert by_party["carrier-b"].current.verdict.route == ROUTE_NEGOTIATE
        for party in ("carrier-a", "carrier-c", "carrier-d"):
            assert by_party[party].current.verdict.route == ROUTE_NONE

    async def test_audit_leg_opened_reflagged_monotonic(self):
        neg, _ = await _corpus_program()
        # Two events per leg (leg_opened + reflagged), in leg order, seq monotonic 0..7.
        assert [e.seq for e in neg.audit] == list(range(len(neg.audit)))
        assert len(neg.audit) == 8
        kinds = [(e.kind, e.party) for e in neg.audit]
        assert kinds[0] == ("leg_opened", "carrier-a")
        assert kinds[1] == ("reflagged", "carrier-a")
        assert kinds[2] == ("leg_opened", "carrier-b")
        assert all(isinstance(e, AuditEvent) for e in neg.audit)

    def test_open_negotiation_accepts_mapping(self):
        standard = load_program_standard("benefits-procurement")
        neg = open_negotiation(
            "p1", {"carrier-z": _hand_quote("carrier-z", "orthodontics")}, standard
        )
        assert neg.legs[0].party == "carrier-z"
        assert neg.legs[0].context_id == "carrier-z"
        assert neg.legs[0].current.verdict.route == ROUTE_NEGOTIATE


class TestRevise:
    async def test_appends_v1_reflags_conformant(self):
        neg, standard = await _corpus_program()
        policy = load_negotiate_policy("benefits-procurement")
        prior_b = neg.leg_for("carrier-b")
        prior_v0 = prior_b.current  # capture to prove immutability

        revised = normalize_from_extraction(
            "carrier-b",
            await FixtureExtractor(corpus_path=BENEFITS_CORPUS).extract(
                REVISED_CARRIER_B
            ),
        )
        neg2 = revise(neg, "carrier-b", revised, standard, policy=policy)

        leg_b = neg2.leg_for("carrier-b")
        assert len(leg_b.versions) == 2
        assert leg_b.revise_count == 1
        assert leg_b.current.version == 1
        assert leg_b.current.verdict.route == ROUTE_NONE
        assert leg_b.current.verdict.conformant is True

        # Prior version immutable: v0 still flagged, unchanged object.
        assert leg_b.versions[0].verdict.route == ROUTE_NEGOTIATE
        assert prior_v0.verdict.route == ROUTE_NEGOTIATE
        assert prior_b.revise_count == 0  # original program untouched

        # Audit appended quote_revised + reflagged for v1.
        new_events = neg2.audit[len(neg.audit) :]
        assert [(e.kind, e.version) for e in new_events] == [
            ("quote_revised", 1),
            ("reflagged", 1),
        ]
        assert [e.seq for e in neg2.audit] == list(range(len(neg2.audit)))

    async def test_recompute_via_comparison(self):
        neg, standard = await _corpus_program()
        policy = load_negotiate_policy("benefits-procurement")
        before = comparison_from_negotiation(neg, standard)
        assert [
            leg.party for leg in before.legs if leg.verdict.route == ROUTE_NEGOTIATE
        ] == ["carrier-b"]

        revised = normalize_from_extraction(
            "carrier-b",
            await FixtureExtractor(corpus_path=BENEFITS_CORPUS).extract(
                REVISED_CARRIER_B
            ),
        )
        neg = revise(neg, "carrier-b", revised, standard, policy=policy)
        after = comparison_from_negotiation(neg, standard)
        assert [
            leg.party for leg in after.legs if leg.verdict.route == ROUTE_NEGOTIATE
        ] == []

    def test_cap_exceeded_escalates_not_rejects(self):
        standard = load_program_standard("benefits-procurement")
        quote = _hand_quote("carrier-z", "orthodontics")
        neg = open_negotiation("p1", [(Leg("carrier-z", "ctx"), quote)], standard)
        policy = NegotiatePolicy(revise_cap=1)

        neg = revise(neg, "carrier-z", quote, standard, policy=policy)  # revise #1 (OK)
        with pytest.raises(ReviseCapExceeded) as exc:
            revise(neg, "carrier-z", quote, standard, policy=policy)  # revise #2 (cap)

        assert exc.value.reason_code == REASON_ESCALATED
        assert exc.value.message == ESCALATE_MESSAGE
        assert exc.value.party == "carrier-z"


class TestBindAndRollup:
    async def test_bind_records_current_version_by_default(self):
        neg, standard = await _corpus_program()
        policy = load_negotiate_policy("benefits-procurement")
        revised = normalize_from_extraction(
            "carrier-b",
            await FixtureExtractor(corpus_path=BENEFITS_CORPUS).extract(
                REVISED_CARRIER_B
            ),
        )
        neg = revise(neg, "carrier-b", revised, standard, policy=policy)

        neg = bind_leg(neg, "carrier-b")
        leg_b = neg.leg_for("carrier-b")
        assert leg_b.bound_version == 1
        assert neg.audit[-1].kind == "leg_bound"
        assert neg.audit[-1].version == 1

    def test_bind_allows_non_conformant_sense_b(self):
        standard = load_program_standard("benefits-procurement")
        quote = _hand_quote("carrier-z", "orthodontics")  # flagged
        neg = open_negotiation("p1", [(Leg("carrier-z", "ctx"), quote)], standard)
        neg = bind_leg(neg, "carrier-z")  # Bridge records, never decides — allowed
        rollup = roll_up(neg)
        assert rollup.bound[0].party == "carrier-z"
        assert rollup.bound[0].conformant is False  # advisory, did not block

    def test_bind_specific_version(self):
        standard = load_program_standard("benefits-procurement")
        quote = _hand_quote("carrier-z", "orthodontics")
        neg = open_negotiation("p1", [(Leg("carrier-z", "ctx"), quote)], standard)
        policy = NegotiatePolicy(revise_cap=2)
        neg = revise(
            neg,
            "carrier-z",
            _hand_quote("carrier-z", "cosmetic surgery"),
            standard,
            policy=policy,
        )
        neg = bind_leg(neg, "carrier-z", version=0)  # bind the original, not latest
        assert neg.leg_for("carrier-z").bound_version == 0

    def test_bind_unknown_version_raises(self):
        standard = load_program_standard("benefits-procurement")
        quote = _hand_quote("carrier-z", "orthodontics")
        neg = open_negotiation("p1", [(Leg("carrier-z", "ctx"), quote)], standard)
        with pytest.raises(ValueError, match="no version 5"):
            bind_leg(neg, "carrier-z", version=5)

    async def test_rollup_bound_unbound_and_audit(self):
        neg, standard = await _corpus_program()
        policy = load_negotiate_policy("benefits-procurement")
        revised = normalize_from_extraction(
            "carrier-b",
            await FixtureExtractor(corpus_path=BENEFITS_CORPUS).extract(
                REVISED_CARRIER_B
            ),
        )
        neg = revise(neg, "carrier-b", revised, standard, policy=policy)
        neg = bind_leg(neg, "carrier-b")

        rollup = roll_up(neg)
        assert [b.party for b in rollup.bound] == ["carrier-b"]
        assert isinstance(rollup.bound[0], BoundLeg)
        assert rollup.bound[0].version == 1
        assert rollup.bound[0].premium == "1500.00"
        assert rollup.bound[0].conformant is True
        assert sorted(rollup.unbound) == ["carrier-a", "carrier-c", "carrier-d"]

        # The full ordered audit trail rides along.
        assert rollup.audit == neg.audit
        assert [e.seq for e in rollup.audit] == list(range(len(rollup.audit)))
        kinds = {e.kind for e in rollup.audit}
        assert {"leg_opened", "reflagged", "quote_revised", "leg_bound"} <= kinds


class TestLoadNegotiatePolicy:
    def test_reads_revise_cap(self):
        assert load_negotiate_policy("benefits-procurement").revise_cap == 2

    def test_missing_block_raises(self):
        # address-proof's policy.yaml has no negotiate: block.
        with pytest.raises(ValueError, match="negotiate:"):
            load_negotiate_policy("address-proof")


def test_negotiate_is_pure_no_corpus():
    """The revise loop is a pure transform — verified with hand-built quotes (no IO)."""
    standard = load_program_standard("benefits-procurement")
    flagged = _hand_quote("carrier-z", "orthodontics")
    fixed = _hand_quote("carrier-z", "cosmetic surgery")

    neg = open_negotiation("p1", [(Leg("carrier-z", "ctx"), flagged)], standard)
    assert neg.legs[0].current.verdict.route == ROUTE_NEGOTIATE

    neg = revise(
        neg, "carrier-z", fixed, standard, policy=NegotiatePolicy(revise_cap=2)
    )
    assert neg.legs[0].current.verdict.route == ROUTE_NONE

    neg = bind_leg(neg, "carrier-z")
    rollup = roll_up(neg)
    assert rollup.bound[0].version == 1
    assert rollup.bound[0].conformant is True
