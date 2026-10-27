"""CUJ-3 golden run — dial-out fan-out → normalize → compare → Deliver (S1-benefits-4).

The deterministic driver for CUJ-3: the Bridge fans out a 4-carrier program, dials each
leg with Agent-Card discovery over the a2a_client port's local (fake-transport) adapter,
the carriers' quotes arrive and normalize to one currency/language, the comparison flags
carrier-b, and the finished comparison is Delivered outward by reference. Every send is
idempotent / crash-safe and carries no bearer token.

This file is the deterministic driver the CUJ-3 T3 gate references at sprint close.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from bridge.comparison import build_comparison, comparison_artifact_ref
from bridge.extraction.fixture import FixtureExtractor
from bridge.normalize import normalize_from_extraction
from bridge.outbound import (
    Delivery,
    DialOut,
    FakePeerDirectory,
    PeerCard,
)
from bridge.ports import Port, PortMode, get_adapter
from bridge.program import fan_out
from bridge.standard import ROUTE_NEGOTIATE, ROUTE_NONE, load_program_standard

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)
PROCESS = "benefits-procurement"

# Corpus doc id per carrier party (see wiki/evals/benefits/expected.json).
CORPUS_DOC = {
    "carrier-a": "benefits-carrier-a-usd-en",
    "carrier-b": "benefits-carrier-b-usd-en",
    "carrier-c": "benefits-carrier-c-eur-fr",
    "carrier-d": "benefits-carrier-d-gbp-de",
}


async def test_cuj3_dial_out_deliver_golden_run():
    client = get_adapter(Port.a2a_client, PortMode.local).build_client()

    # 1. Fan out a 4-carrier program (each leg its own isolated context).
    program = fan_out("acme-2026", ["carrier-a", "carrier-b", "carrier-c", "carrier-d"])
    assert len(program.legs) == 4

    # 2. A peer directory advertising the process for each leg's card_url.
    directory = FakePeerDirectory(
        {
            leg.context_id: PeerCard(leg.party, leg.context_id, (PROCESS,))
            for leg in program.legs
        }
    )

    # 3. Dial each leg (Agent-Card discovery → message/send). One receipt per leg,
    #    all distinct keys; a re-drive of one leg is crash-safe (resent, no double send).
    receipts = [
        client.dial(
            DialOut(
                context_id=leg.context_id,
                party=leg.party,
                process=PROCESS,
                card_url=leg.context_id,
                message=f"request benefits-quote for program:acme-2026 ({leg.party})",
            ),
            directory=directory,
        )
        for leg in program.legs
    ]
    assert len(receipts) == 4
    assert all(r.resent is False for r in receipts)
    assert len({r.idempotency_key for r in receipts}) == 4

    redrive = client.dial(
        DialOut(
            context_id=program.legs[0].context_id,
            party=program.legs[0].party,
            process=PROCESS,
            card_url=program.legs[0].context_id,
            message="re-drive after crash",
        ),
        directory=directory,
    )
    assert redrive.resent is True  # crash-safe fan-out: no double send

    # 4. The carriers' quotes arrive — extract each party + normalize to one schema.
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    corpus_docs = {
        d["id"]: d for d in json.loads(BENEFITS_CORPUS.read_text())["documents"]
    }
    quotes = []
    for leg in program.legs:
        doc_id = CORPUS_DOC[leg.party]
        assert doc_id in corpus_docs
        result = await extractor.extract(doc_id)
        quotes.append(normalize_from_extraction(leg.party, result))

    # 5. Build the comparison — reproduces the corpus oracle (carrier-b flagged, one currency).
    standard = load_program_standard(PROCESS)
    comparison = build_comparison("acme-2026", quotes, standard)
    assert comparison.currency == "USD"
    assert comparison.language == "en"
    flagged = [
        leg.party for leg in comparison.legs if leg.verdict.route == ROUTE_NEGOTIATE
    ]
    assert flagged == ["carrier-b"]
    for leg in comparison.legs:
        if leg.party != "carrier-b":
            assert leg.verdict.route == ROUTE_NONE

    # 6. Deliver the comparison outward — by reference (no bytes), idempotent.
    ref = comparison_artifact_ref(comparison)
    delivery = Delivery(
        context_id="acme-2026",
        recipient="broker",
        artifact=ref,
        summary="4-leg benefits comparison; carrier-b flagged",
    )
    deliver_receipt = client.deliver(delivery)
    assert deliver_receipt.resent is False
    assert deliver_receipt.delivery.artifact.uri == "bridge://comparison/acme-2026"
    # Re-driving the Deliver is idempotent.
    assert client.deliver(delivery).resent is True

    # 7. No bearer token anywhere in the journey.
    for cls in (DialOut, Delivery):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not any(tok in n for n in names for tok in ("token", "cred", "bearer"))
