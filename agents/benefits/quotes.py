"""Framework-neutral quote source for the simulated carrier agents (S1-benefits-6).

Both carriers — Carrier A (Google ADK) and Carrier B (LangGraph) — serve quotes read
**read-only** from the single Benefits eval oracle
(``wiki/evals/benefits/expected.json``). There is exactly one source of quote truth
in the repo (the corpus), so the carriers never hardcode or duplicate quote data; they
load the authored ``extraction`` block for a doc id and hand back a
``contract.ExtractionResult`` — the same shape the Bridge's ``FixtureExtractor`` would
produce. This keeps the interop proof honest: the carriers emit exactly what the oracle
says, and the Bridge-side ``normalize``/``standard`` reproduces each carrier's
``expected_normalized`` / ``expected_gaps``.

External zone: imports stdlib + ``contract/`` only — never ``bridge/`` (the reverse
isolation guard, ``tests/test_isolation_guard.py``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from contract.extraction import ExtractionResult

# ``agents/benefits/quotes.py`` → parents[0]=benefits, [1]=agents, [2]=repo root.
_CORPUS_PATH = Path(__file__).resolve().parents[2] / "wiki/evals/benefits/expected.json"

# Corpus doc ids the carriers serve (the oracle authored these).
CARRIER_A_DOC = "benefits-carrier-a-usd-en"  # conformant Path-A one-shot (ADK)
CARRIER_B_V0_DOC = "benefits-carrier-b-usd-en"  # holds the orthodontics gap (LangGraph)
CARRIER_B_V2_DOC = "benefits-carrier-b-usd-en-v2"  # revised: gap closed


@lru_cache(maxsize=1)
def _corpus() -> dict[str, dict]:
    """Load + index the benefits corpus by doc id (read-only, cached)."""
    raw = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    return {doc["id"]: doc for doc in raw["documents"]}


def load_quote(doc_id: str) -> ExtractionResult:
    """Return the authored ``ExtractionResult`` for ``doc_id`` from the oracle.

    Raises ``KeyError`` naming the known ids on an unknown doc id (degrade loudly —
    the same known-keys error style as ``bridge.normalize.fixed_fx``).
    """
    docs = _corpus()
    try:
        doc = docs[doc_id]
    except KeyError:
        raise KeyError(
            f"no benefits quote for doc id {doc_id!r}; known ids: {sorted(docs)}"
        ) from None
    return ExtractionResult.model_validate(doc["extraction"])
