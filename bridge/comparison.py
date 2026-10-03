"""Program comparison — the apples-to-apples aggregate Deliver pushes (S1-benefits-4).

Composes the S1-benefits-2 normalize layer with the S1-benefits-3 standard-gap flagger into
one comparison object: N legs side by side in ONE currency / ONE language, each leg carrying
its normalized quote + its sense-A standard-gap verdict. This is the **data** the CUJ-3
Deliver activity pushes outward (by reference, as a contract.wire.ArtifactRef) and the
S1-benefits-7 A2UI view will render. Keep that boundary clear: this module is the aggregate;
the view that renders it is a separate task.

Pure: imports pydantic + bridge.normalize + bridge.standard only — NO google.adk, NO a2a,
NEVER agents/ (the bridge/ isolation guard, tests/test_isolation_guard.py). Synchronous —
it operates on already-normalized quotes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from bridge.normalize import TARGET_CURRENCY, TARGET_LANGUAGE, NormalizedQuote
from bridge.standard import ProgramStandard, StandardGapVerdict, flag_quote
from contract.wire import ArtifactRef


class ProgramComparisonLeg(BaseModel):
    """One leg of the comparison: a carrier's normalized quote + its gap verdict."""

    model_config = ConfigDict(extra="forbid")

    party: str
    quote: NormalizedQuote
    verdict: StandardGapVerdict


class ProgramComparison(BaseModel):
    """N legs side by side in one currency / one language, each with its gap verdict.

    ``currency`` / ``language`` are stamped from the normalize target so a reader knows the
    canonical basis without inspecting a leg. ``legs`` preserves the input quote order.
    """

    model_config = ConfigDict(extra="forbid")

    program_id: str
    currency: str  # == bridge.normalize.TARGET_CURRENCY
    language: str  # == bridge.normalize.TARGET_LANGUAGE
    legs: list[ProgramComparisonLeg]


def build_comparison(
    program_id: str,
    normalized_quotes: list[NormalizedQuote],
    standard: ProgramStandard,
) -> ProgramComparison:
    """Assemble the apples-to-apples comparison, flagging each leg against the standard.

    Runs ``flag_quote`` per normalized quote (sense A — surfaces the gap, never decides the
    deal) and stamps the canonical currency/language. Preserves leg order.
    """
    legs = [
        ProgramComparisonLeg(
            party=quote.party,
            quote=quote,
            verdict=flag_quote(quote, standard),
        )
        for quote in normalized_quotes
    ]
    return ProgramComparison(
        program_id=program_id,
        currency=TARGET_CURRENCY,
        language=TARGET_LANGUAGE,
        legs=legs,
    )


def comparison_artifact_ref(comparison: ProgramComparison) -> ArtifactRef:
    """Mint a by-reference handle for a comparison (bytes never travel on the wire).

    Produces a ``bridge://comparison/{program_id}`` URI so the Deliver activity carries the
    finished comparison as an ArtifactRef, not inline bytes (ART discipline).
    """
    return ArtifactRef(
        uri=f"bridge://comparison/{comparison.program_id}",
        mime_type="application/json",
        name=f"{comparison.program_id}-comparison",
    )
