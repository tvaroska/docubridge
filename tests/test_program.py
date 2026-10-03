"""Program aggregate tests (S1-benefits-1) — N-leg fan-out + per-leg isolation.

Proves the load-bearing beat-1 invariant: a program links N legs, each its own
party+context, and **competitors never share a context** — N legs yield N distinct
context ids, each party maps to its own context, and a duplicate party is rejected.
"""

from __future__ import annotations

import pytest

from bridge.program import Program, add_leg, fan_out, open_program


class TestOpenAndAdd:
    def test_open_program_is_empty(self):
        pg = open_program("acme-2026")
        assert pg.program_id == "acme-2026"
        assert pg.legs == ()

    def test_add_leg_links_a_leg(self):
        pg = add_leg(open_program("p"), "carrier-a")
        assert len(pg.legs) == 1
        assert pg.legs[0].party == "carrier-a"
        assert pg.legs[0].context_id  # minted, non-empty

    def test_add_leg_is_immutable(self):
        pg0 = open_program("p")
        pg1 = add_leg(pg0, "carrier-a")
        # Original is untouched (frozen dataclass, replace returns a new Program).
        assert pg0.legs == ()
        assert len(pg1.legs) == 1
        assert isinstance(pg1, Program)

    def test_add_leg_preserves_program_id(self):
        pg = add_leg(add_leg(open_program("acme-2026"), "a"), "b")
        assert pg.program_id == "acme-2026"


class TestFanOutIsolation:
    def test_fan_out_links_n_legs(self):
        pg = fan_out("acme-2026", ["carrier-a", "carrier-b", "carrier-c", "carrier-d"])
        assert len(pg.legs) == 4

    def test_distinct_contexts_per_leg(self):
        """N legs → N distinct context ids (competitors never share a context)."""
        pg = fan_out("acme-2026", ["a", "b", "c", "d"])
        contexts = [leg.context_id for leg in pg.legs]
        assert len(set(contexts)) == 4

    def test_distinct_parties_per_leg(self):
        pg = fan_out("acme-2026", ["a", "b", "c", "d"])
        assert len({leg.party for leg in pg.legs}) == 4

    def test_each_party_maps_to_its_own_context(self):
        pg = fan_out("acme-2026", ["a", "b", "c"])
        for party in ("a", "b", "c"):
            leg = pg.leg_for(party)
            assert leg.party == party
        # No two parties share a context.
        assert len(set(pg.contexts())) == len(pg.parties())

    def test_preserves_input_order(self):
        parties = ["carrier-c", "carrier-a", "carrier-b"]
        pg = fan_out("p", parties)
        assert list(pg.parties()) == parties


class TestDuplicateRejected:
    def test_fan_out_duplicate_party_raises(self):
        with pytest.raises(ValueError, match="duplicate party"):
            fan_out("p", ["x", "x"])

    def test_add_leg_twice_same_party_raises(self):
        pg = add_leg(open_program("p"), "x")
        with pytest.raises(ValueError, match="already has a leg"):
            add_leg(pg, "x")

    def test_leg_for_unknown_party_raises(self):
        pg = fan_out("p", ["a"])
        with pytest.raises(KeyError):
            pg.leg_for("missing")
