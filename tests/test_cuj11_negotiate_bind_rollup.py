"""CUJ-11 golden run — negotiate → versioned revise → recompute → bind → rollup (beats 5,6).

The deterministic driver for the CUJ-11 payoff tail (S1-benefits-5): fan out a 4-carrier
program, normalize v0 quotes, open the negotiation, flag ONLY carrier-b, receive carrier-b's
revised quote as a new version on the same leg, recompute the comparison (carrier-b now
conformant — all four ROUTE_NONE), bind the winning leg (sense B), and roll up the program
with its audit trail. Drives entirely off the authored wiki/evals/benefits oracle.

This file is the deterministic driver the CUJ-11 T3 gate references at sprint close (like
tests/test_cuj3_dial_out_deliver.py for CUJ-3).
"""

from __future__ import annotations

import json
from pathlib import Path

from bridge.extraction.fixture import FixtureExtractor
from bridge.negotiate import (
    bind_leg,
    comparison_from_negotiation,
    load_negotiate_policy,
    open_negotiation,
    revise,
    roll_up,
)
from bridge.normalize import normalize_from_extraction
from bridge.program import fan_out
from bridge.standard import ROUTE_NEGOTIATE, ROUTE_NONE, load_program_standard

BENEFITS_CORPUS = Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
PROCESS = "benefits-procurement"

CORPUS_DOC = {
    "carrier-a": "benefits-carrier-a-usd-en",
    "carrier-b": "benefits-carrier-b-usd-en",
    "carrier-c": "benefits-carrier-c-eur-fr",
    "carrier-d": "benefits-carrier-d-gbp-de",
}
REVISED_CARRIER_B = "benefits-carrier-b-usd-en-v2"


async def test_cuj11_negotiate_bind_rollup_golden_run():
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    corpus_docs = {d["id"]: d for d in json.loads(BENEFITS_CORPUS.read_text())["documents"]}
    standard = load_program_standard(PROCESS)
    policy = load_negotiate_policy(PROCESS)
    assert policy.revise_cap == 2

    # 1. Fan out a 4-carrier program (each leg its own isolated context).
    program = fan_out("acme-2026", ["carrier-a", "carrier-b", "carrier-c", "carrier-d"])
    assert len(program.legs) == 4

    # 2. The carriers' v0 quotes arrive — extract + normalize to one schema.
    v0_quotes = {}
    for leg in program.legs:
        doc_id = CORPUS_DOC[leg.party]
        assert doc_id in corpus_docs
        v0_quotes[leg.party] = normalize_from_extraction(
            leg.party, await extractor.extract(doc_id)
        )

    # 3. Open the negotiation — v0 per leg, flagged; only carrier-b routes to negotiate.
    neg = open_negotiation(
        "acme-2026", [(leg, v0_quotes[leg.party]) for leg in program.legs], standard
    )
    before = comparison_from_negotiation(neg, standard)
    flagged_before = [
        leg.party for leg in before.legs if leg.verdict.route == ROUTE_NEGOTIATE
    ]
    assert flagged_before == ["carrier-b"]
    assert neg.leg_for("carrier-b").current.version == 0
    assert neg.leg_for("carrier-b").revise_count == 0

    # 4. Pushback yields carrier-b's revised quote — lands as v1 on the same leg.
    revised = normalize_from_extraction(
        "carrier-b", await extractor.extract(REVISED_CARRIER_B)
    )
    neg = revise(neg, "carrier-b", revised, standard, policy=policy)
    assert neg.leg_for("carrier-b").revise_count == 1
    assert neg.leg_for("carrier-b").current.version == 1

    # 5. The comparison recomputes as the version lands — carrier-b now conformant.
    after = comparison_from_negotiation(neg, standard)
    assert len(after.legs) == 4
    assert all(leg.verdict.route == ROUTE_NONE for leg in after.legs)

    # 6. Bind the winning leg (sense B — the agent's deal; the Bridge records).
    neg = bind_leg(neg, "carrier-b")
    assert neg.leg_for("carrier-b").bound_version == 1

    # 7. Roll up — the bound leg + version + the ordered audit trail.
    rollup = roll_up(neg)
    assert [b.party for b in rollup.bound] == ["carrier-b"]
    assert rollup.bound[0].version == 1  # v0 initial, v1 revised
    assert rollup.bound[0].premium == "1500.00"
    assert rollup.bound[0].conformant is True
    assert sorted(rollup.unbound) == ["carrier-a", "carrier-c", "carrier-d"]

    # Audit trail: monotonic seq, carries quote_revised + leg_bound in order.
    assert [e.seq for e in rollup.audit] == list(range(len(rollup.audit)))
    kinds = [e.kind for e in rollup.audit]
    assert "quote_revised" in kinds
    assert "leg_bound" in kinds
    assert kinds.index("quote_revised") < kinds.index("leg_bound")
