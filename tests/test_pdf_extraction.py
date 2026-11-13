"""Native-digital PDF extraction — the committed benefits PDF asset (S0-arch-6).

The credential-free tests run ALWAYS (no ``BRIDGE_TEST_GEMINI`` gate): they prove the
committed corpus PDF is a real, regenerable PDF carrying the oracle's values, that it
resolves as ``application/pdf``, that it rides to Gemini as an **inline bytes Part**
(the SVG text-part branch must not swallow it), and that the fixture leg and the
replayed gemini leg reach the SAME terminal outcome from the SAME artifact string.
The live test is gated on ``BRIDGE_TEST_GEMINI=1`` and SKIPS in the default suite,
exactly like ``tests/test_gemini_extraction.py::test_live_corpus_terminal_outcome_parity``.

Scope boundaries this module deliberately does NOT cross: resolving a PDF from a
remote reference (a ``gs://`` object or a party's signed URL — that is the shared
resolver's, exercised by ``tests/test_artifact_resolver.py``), the whole-journey A2A
conformance run (S0-arch-9), and
**scanned/raster** PDFs needing OCR (the ``docai`` engine's envelope — Gemini with the
Document AI OCR pre-pass, exercised by ``tests/test_docai_extraction.py``).

Parity is **terminal-outcome, never ledger-identical** (CLAUDE.md; design/ports.md):
confidences are deliberately not compared between the legs.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

import bridge.edge.artifacts as edge_artifacts
from bridge.extraction.fixture import FixtureExtractor
from bridge.extraction.gemini import GeminiExtractor, _corpus_resolver, _mime_for
from bridge.normalize import NormalizedQuote, normalize_from_extraction
from bridge.standard import ROUTE_NONE, flag_quote, load_program_standard
from contract.extraction import ExtractionResult
from tests import corpus_pdf

_GEMINI = os.environ.get("BRIDGE_TEST_GEMINI") == "1"

BENEFITS_ROOT = Path(__file__).resolve().parents[1] / "wiki/evals/benefits"
CORPUS = json.loads((BENEFITS_ROOT / "expected.json").read_text(encoding="utf-8"))
PDF_DOC_ID = "benefits-carrier-c-eur-fr"
RECORD = next(d for d in CORPUS["documents"] if d["id"] == PDF_DOC_ID)
ARTIFACT = RECORD["artifact"]  # "images/benefits-carrier-c-eur-fr.pdf"
PDF_PATH = BENEFITS_ROOT / ARTIFACT
PROGRAM_STANDARD = "benefits-procurement"


class _FakeResponse:
    """Matches the genai ``GenerateContentResponse.text`` accessor the engine reads."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self._text)


class _FakeClient:
    """A minimal genai ``Client``-shaped double: ``.models.generate_content`` returns
    canned JSON. An injected fake client wins → no creds, no network. A local copy of
    the private fakes in ``tests/test_gemini_extraction.py`` (cheaper and clearer than
    exporting them)."""

    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


def _replayed_client() -> _FakeClient:
    """A fake client that answers with the record's own corpus extraction payload."""
    return _FakeClient(json.dumps(RECORD["extraction"]))


def _terminal_outcome(result: ExtractionResult):
    """Normalize + gate one leg's extraction — the terminal outcome both legs share."""
    quote = normalize_from_extraction(RECORD["party"], result)
    verdict = flag_quote(quote, load_program_standard(PROGRAM_STANDARD))
    return quote, verdict


def _assert_expected_terminal_outcome(result: ExtractionResult) -> None:
    quote, verdict = _terminal_outcome(result)
    assert quote == NormalizedQuote.model_validate(RECORD["expected_normalized"])
    assert {gap.rule_id for gap in verdict.gaps} == set(RECORD["expected_gaps"])
    assert verdict.route == ROUTE_NONE


# --- the committed asset ----------------------------------------------------


def test_committed_pdf_is_a_real_pdf():
    """The corpus synthesizes actual PDF bytes on demand with zero committed .pdf files."""
    assert not PDF_PATH.exists(), (
        f"committed binary PDF must be moved to GCS: {PDF_PATH}"
    )
    data = corpus_pdf.build_quote_pdf(RECORD)
    assert data.startswith(b"%PDF-1."), data[:16]
    assert b"\nstartxref\n" in data
    assert data.endswith(b"%%EOF\n")
    assert len(data) > 500


def test_generator_reproduces_the_committed_pdf_byte_for_byte(tmp_path):
    """The drift guard: the asset is regenerable byte-deterministically from the corpus record."""
    regenerated = corpus_pdf.write_quote_pdf(RECORD, tmp_path / "regenerated.pdf")
    assert regenerated.read_bytes() == corpus_pdf.build_quote_pdf(RECORD)


def test_pdf_carries_the_oracle_values():
    """The page prints the oracle, not a decorative blob.

    The content stream is uncompressed, so the cp1252 text literals are greppable.
    """
    page = corpus_pdf.build_quote_pdf(RECORD).decode("cp1252")
    for field, value in RECORD["extraction"]["fields"]["key_fields"].items():
        assert value in page, f"{field}={value!r} is not printed on the page"
    assert RECORD["party"] in page
    # Money is rendered as an ASCII ISO-4217 code + dot decimal: ``_convert_money``
    # strips $€£/commas but NOT a currency code prefix nor a comma decimal.
    assert "EUR 1000.00" in page
    assert "EUR 500.00" in page


# --- the resolve + request path ---------------------------------------------


def test_corpus_resolver_reads_the_pdf_as_application_pdf():
    """The engine's default resolver hands the PDF back as ``application/pdf``."""
    data, mime = _corpus_resolver(BENEFITS_ROOT)(ARTIFACT)
    assert mime == "application/pdf"
    assert data == corpus_pdf.build_quote_pdf(RECORD)


def test_pdf_suffix_maps_to_application_pdf_on_both_mime_maps():
    """``.pdf`` is ``application/pdf`` on the engine's map and the edge's map.

    Both names now read ONE table (``bridge/extraction/resolve.py::SUFFIX_MIME``);
    this asserts the two long-standing entry points still agree. Resolving a PDF
    through ``bridge/edge/artifacts.py::resolve_bytes`` is
    ``tests/test_artifact_resolver.py``'s.
    """
    assert _mime_for("x.pdf") == "application/pdf"
    assert edge_artifacts._mime_for("images/x.pdf") == "application/pdf"


async def test_pdf_rides_as_an_inline_bytes_part_not_text():
    """THE finding's assertion: a PDF reaches Gemini as inline bytes, not as text.

    ``_document_part`` special-cases ``image/svg+xml`` to a *text* part (the Address
    corpus is text SVG); every other mime — notably ``application/pdf`` — must ride as
    a ``google.genai.types.Part`` carrying ``inline_data``.
    """
    from google.genai import types as genai_types  # lazily, never at module level

    client = _replayed_client()
    extractor = GeminiExtractor(client=client, corpus_root=BENEFITS_ROOT)

    result = await extractor.extract(ARTIFACT)

    assert result.fields.doctype == "benefits-quote"
    call = client.models.calls[0]
    part = call["contents"][1]
    assert isinstance(part, genai_types.Part), type(part)
    assert part.inline_data is not None
    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == corpus_pdf.build_quote_pdf(RECORD)
    # The request is still schema-driven (S0-arch-5).
    assert call["config"]["response_mime_type"] == "application/json"
    assert "response_schema" in call["config"]


# --- terminal-outcome parity, credential-free -------------------------------


async def test_pdf_replay_reaches_the_same_terminal_outcome_on_both_legs():
    """Both legs, the SAME artifact string, the same terminal outcome.

    ``FixtureExtractor._normalize_key`` strips ``.pdf``, so the fixture leg accepts the
    very artifact path the gemini leg resolves to bytes — that shared name is the
    parity pivot. Confidences are deliberately NOT compared: parity is
    terminal-outcome, never ledger-identical (CLAUDE.md; design/ports.md).
    """
    gemini_leg = await GeminiExtractor(
        client=_replayed_client(), corpus_root=BENEFITS_ROOT
    ).extract(ARTIFACT)
    fixture_leg = await FixtureExtractor(
        corpus_path=BENEFITS_ROOT / "expected.json"
    ).extract(ARTIFACT)

    for result in (gemini_leg, fixture_leg):
        _assert_expected_terminal_outcome(result)


# --- live PDF read (gated) --------------------------------------------------


def _money(raw: str) -> Decimal:
    """Canonicalize a live model's money echo to a 2dp Decimal.

    Rendering variance in the echo (``EUR 1000,00``, ``€1000``) is not a
    terminal-outcome difference; ``bridge/`` and the oracle stay untouched.
    """
    cleaned = raw.upper().replace("EUR", "").replace("€", "").replace(",", "").strip()
    return Decimal(cleaned).quantize(Decimal("0.01"))


def _free_text(raw: str) -> str:
    """Canonicalize free text to the case-sensitive ``TRANSLATIONS`` table's keys."""
    return raw.strip().strip(".").lower()


@pytest.mark.skipif(
    not _GEMINI, reason="set BRIDGE_TEST_GEMINI=1 to run the live Gemini engine"
)
async def test_live_pdf_terminal_outcome_parity():
    """The REAL Gemini engine reads the committed PDF and lands the same outcome.

    Non-vacuous: the model is given no doctype, so classifying the bytes as
    ``benefits-quote`` against the installed skill catalog means it read the page.
    A small test-local canonicalizer absorbs echo-rendering variance before the
    terminal-outcome assertions — the ``TRANSLATIONS`` table and the oracle are NOT
    loosened, and confidences are not asserted.
    """
    result = await GeminiExtractor(corpus_root=BENEFITS_ROOT).extract(ARTIFACT)

    assert result.fields.doctype == "benefits-quote"
    assert result.legible is True
    kf = dict(result.fields.key_fields)
    assert kf["currency"].upper() == "EUR"
    assert kf["language"].lower().startswith("fr")
    assert _money(kf["premium"]) == Decimal("1000.00")
    assert _money(kf["deductible"]) == Decimal("500.00")
    assert kf["effective_date"] == "2026-01-01"
    assert kf["expiry_date"] == "2026-12-31"

    kf["premium"] = str(_money(kf["premium"]))
    kf["deductible"] = str(_money(kf["deductible"]))
    for field in ("coverage_limits", "exclusions"):
        kf[field] = _free_text(kf[field])
    canonical = result.model_copy(
        update={"fields": result.fields.model_copy(update={"key_fields": kf})}
    )

    _assert_expected_terminal_outcome(canonical)
