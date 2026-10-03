"""Normalize layer — one schema / one currency / one language (S1-benefits-2, beat 3).

Every carrier ``benefits-quote`` arrives in its own native currency and language. Beat 3
of the Benefits demo (CUJ-11) collapses them to a single canonical shape so the
apples-to-apples comparison downstream (S1-benefits-3 flags the standard gaps,
S1-benefits-7 renders the side-by-side view) compares like with like. This module is the
pure transform that does it: FX-convert the monetary fields to one currency, translate the
free-text fields to one language, and pass the ISO dates through — emitting a
``NormalizedQuote`` in the canonical schema.

**Deterministic by fixed tables.** ``FX_RATES`` and ``TRANSLATIONS`` are fixed lookup
tables — they *are* the oracle: whatever values they carry MUST match the
``expected_normalized`` block in ``wiki/evals/benefits/expected.json`` (the corpus is
authored against these numbers, and ``tests/test_normalize.py`` asserts the round-trip
over every quote). A fixed rate table gives a run that reproduces bit-for-bit; monetary
values stay **strings** end-to-end (the corpus round-trip idiom in ``contract.extraction``
— parsing/formatting through ``decimal.Decimal`` only inside the money helper, never a
float on ``NormalizedQuote``, so the oracle is platform-stable).

**currency / language ride in ``key_fields`` (Option A).** The wire extraction shape
(``contract.extraction.ExtractionFields``) is ``{doctype, issuer, key_fields:
dict[str,str]}`` — it has no first-class ``currency``/``language``. Rather than change the
``contract/`` wire (which ripples to ``decode_wire`` + every consumer), this task carries
``currency`` and ``language`` INSIDE ``key_fields`` and reconciles the doctype skill's
``schema.json`` / ``validation.yaml`` to match. ``normalize_from_extraction`` reads them
out of ``key_fields``. See DECISIONS.md for the Option-A rationale (kept off the
``contract/`` critical path); Option B (first-class on the wire) is a separately scheduled
``contract/`` change.

**FX as a fixed table, live FX seam DEFERRED (no silent cap).** ``spec/demos/benefits-demo.md``
names "a fixed rate table drives the deterministic demo; a live FX seam is the alternative
shape". This module builds ONLY the fixed table plus a thin ``FxRates`` callable swap point
(``normalize_quote(..., fx=fixed_fx)``): swapping in a live-FX-seam adapter later is a
one-arg change. A full local+GCP FX seam is **designed-but-unbuilt this cycle** — logged
here (per the repo's "No silent caps" rule), not added to the ``Seam`` enum.

Pure: imports stdlib + pydantic + ``contract/`` only — NO ``google.adk``, NO ``a2a``,
NEVER ``agents/`` (the ``bridge/`` isolation guard, ``tests/test_isolation_guard.py``).
The transform is synchronous — it operates on already-extracted fields; only the corpus
oracle test is async (it calls the extraction seam).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from contract.extraction import ExtractionResult

# --- Canonical target: one currency, one language. ---
TARGET_CURRENCY = "USD"
TARGET_LANGUAGE = "en"

# --- Fixed FX rate table: native currency code → multiplier to TARGET_CURRENCY. ---
# These are the deterministic oracle — they MUST match `expected_normalized` in the
# benefits corpus. (Round synthetic amounts keep the math obvious: EUR 1000.00 x 1.10 =
# 1100.00; GBP 800.00 x 1.25 = 1000.00.)
FX_RATES: dict[str, float] = {
    "USD": 1.00,
    "EUR": 1.10,
    "GBP": 1.25,
}

# --- Fixed translation table: language → {native phrase: english}. ---
# Deterministic lookup for the synthetic `coverage_limits` / `exclusions` free-text in the
# fr/de quotes. An UNMAPPED phrase passes through UNCHANGED — this is why an English quote
# is a translation no-op (idempotency), and why an unknown phrase degrades visibly (the
# native text survives) rather than crashing.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "fr": {
        "soins dentaires et optique": "dental and vision care",
        "chirurgie esthétique": "cosmetic surgery",
    },
    "de": {
        "Zahn- und Sehkraftversorgung": "dental and vision care",
        "kosmetische Chirurgie": "cosmetic surgery",
    },
}

# Monetary key_fields that get FX-converted; free-text fields that get translated.
_MONEY_FIELDS = ("premium", "deductible")
_TEXT_FIELDS = ("coverage_limits", "exclusions")

# A thin FX swap point: any code → multiplier. Default reads the fixed table; a live-FX
# seam adapter would satisfy the same signature (the deferred alternative shape).
FxRates = Callable[[str], float]


def fixed_fx(code: str) -> float:
    """Return the fixed multiplier for a currency ``code`` (the default ``FxRates``).

    Raises ``KeyError`` naming the known codes on an unknown currency (mirroring
    ``FixtureExtractor.extract``'s error style — degrade loudly, never silently).
    """
    try:
        return FX_RATES[code]
    except KeyError:
        known = sorted(FX_RATES)
        raise KeyError(
            f"no FX rate for currency {code!r}; known codes: {known}"
        ) from None


class NormalizedQuote(BaseModel):
    """The canonical, comparable quote — one schema / one currency / one language.

    Monetary fields are kept as **strings** (the corpus round-trip idiom): FX math runs
    through ``decimal.Decimal`` inside the money helper and formats back to a 2-dp string,
    so no float ever lands on the model (the oracle stays platform-stable). ``currency`` is
    always ``TARGET_CURRENCY`` and ``language`` always ``TARGET_LANGUAGE`` after
    normalization; ``original_currency`` / ``original_language`` preserve provenance.
    """

    model_config = ConfigDict(extra="forbid")

    party: str
    currency: str  # == TARGET_CURRENCY
    language: str  # == TARGET_LANGUAGE
    premium: str
    deductible: str
    coverage_limits: str
    exclusions: str
    effective_date: str
    expiry_date: str
    # Provenance (what the quote arrived as).
    original_currency: str
    original_language: str


def _convert_money(amount: str, rate: float) -> str:
    """FX-convert a native amount string → a 2-dp ``TARGET_CURRENCY`` string.

    Strips a leading currency symbol / grouping commas / whitespace defensively, parses
    with ``decimal.Decimal``, multiplies by ``rate`` (via ``Decimal(str(rate))`` so float
    imprecision never enters the oracle), and quantizes HALF_UP to 2 dp. Raises
    ``ValueError`` on an unparseable amount.
    """
    cleaned = amount.strip().lstrip("$€£").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable monetary amount {amount!r}") from exc
    converted = value * Decimal(str(rate))
    return str(converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _translate(text: str, language: str) -> str:
    """Translate ``text`` to ``TARGET_LANGUAGE`` via the fixed table.

    A pass-through when ``language == TARGET_LANGUAGE`` or the phrase is unmapped (the
    native text survives — a visible degrade, not a crash).
    """
    if language == TARGET_LANGUAGE:
        return text
    return TRANSLATIONS.get(language, {}).get(text, text)


def normalize_quote(
    *,
    party: str,
    currency: str,
    language: str,
    key_fields: Mapping[str, str],
    fx: FxRates = fixed_fx,
) -> NormalizedQuote:
    """Normalize one native quote to the canonical ``NormalizedQuote``.

    FX-converts ``premium`` / ``deductible`` via ``fx(currency)``, translates
    ``coverage_limits`` / ``exclusions`` via the fixed table (pass-through for en /
    unmapped phrases), passes the ISO dates through, and stamps the canonical
    currency/language while recording the originals.

    Raises ``KeyError`` (from ``fx``) on an unknown currency and ``KeyError`` if a required
    ``key_fields`` entry is missing.
    """
    rate = fx(currency)
    return NormalizedQuote(
        party=party,
        currency=TARGET_CURRENCY,
        language=TARGET_LANGUAGE,
        premium=_convert_money(key_fields["premium"], rate),
        deductible=_convert_money(key_fields["deductible"], rate),
        coverage_limits=_translate(key_fields["coverage_limits"], language),
        exclusions=_translate(key_fields["exclusions"], language),
        effective_date=key_fields["effective_date"],
        expiry_date=key_fields["expiry_date"],
        original_currency=currency,
        original_language=language,
    )


def normalize_from_extraction(
    party: str,
    result: ExtractionResult,
    fx: FxRates = fixed_fx,
) -> NormalizedQuote:
    """Normalize a corpus/wire ``ExtractionResult`` for ``party`` (Option A read path).

    Reads ``currency`` / ``language`` / the amounts out of ``result.fields.key_fields``
    (Option A — they ride inside ``key_fields``, not first-class on the wire). Raises a
    clear ``ValueError`` if ``currency`` / ``language`` are absent — that means the
    extraction didn't follow the ``benefits-quote`` doctype schema.
    """
    kf = result.fields.key_fields
    for required in ("currency", "language"):
        if required not in kf:
            raise ValueError(
                f"extraction for party {party!r} is missing {required!r} in key_fields; "
                f"benefits-quote extractions carry currency/language in key_fields "
                f"(Option A). Got keys: {sorted(kf)}"
            )
    return normalize_quote(
        party=party,
        currency=kf["currency"],
        language=kf["language"],
        key_fields=kf,
        fx=fx,
    )
