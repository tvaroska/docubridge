"""The ``docai`` extraction engine — Document AI OCR pre-pass feeding Gemini.

The ``docai`` engine is ``GeminiExtractor(prepass=DocAIPrepass())``
(``bridge/extraction/docai.py``, design/docai-prepass.md): the client's Document AI
processor OCRs a scanned/raster document once, and the text layer rides into the same
schema-driven Gemini request as a third part next to the original document.

The credential-free tests run ALWAYS, with injected fakes for both clients (the
``google-cloud-documentai`` SDK is not needed — the request is a plain dict). They
prove the processor config refuses loud, the request shape (bytes vs ``gs://`` by
reference), the ``[prompt, document, ocr]`` contents, once-per-extraction across a
schema retry, the SVG skip, error propagation with no fallback, the empty-page path,
Layout Parser flattening, the lazy import, and that the **scanned** carrier-C PDF
(``tests/corpus_scanned_pdf.py`` — no text layer) reaches the record's terminal
outcome under a replayed extraction. The live tests are gated on
``BRIDGE_TEST_DOCAI=1`` + ``BRIDGE_DOCAI_PROCESSOR`` and SKIP in the default suite.

Parity is **terminal-outcome, never ledger-identical** (CLAUDE.md; design/seams.md).
"""
from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.extraction.docai import (
    DOCAI_PROCESSOR_ENV,
    MAX_OCR_CHARS,
    DocAIPrepass,
    build_ocr_context,
    build_process_request,
    document_text,
    parse_processor,
    resolve_processor,
)
from bridge.extraction.gemini import GeminiExtractor
from bridge.extraction.resolve import ResolvedDocument
from bridge.normalize import NormalizedQuote, normalize_from_extraction
from bridge.seams.gcp import GcpExtraction
from bridge.standard import ROUTE_NONE, flag_quote, load_program_standard
from contract.extraction import ExtractionResult
from tests import corpus_scanned_pdf
from tests.corpus_pdf import load_records, quote_pdf_lines, quote_pdf_title

REPO = Path(__file__).resolve().parents[1]
ADDRESS_ROOT = REPO / "wiki/evals/address"
PROCESSOR = "projects/client-project/locations/eu/processors/abc123"
RECORD = load_records()[corpus_scanned_pdf.SCANNED_DOC_ID]
ARTIFACT = "https://carrier-c.example/quotes/benefits-carrier-c-eur-fr-scanned.pdf"
PROGRAM_STANDARD = "benefits-procurement"
SCANNED = corpus_scanned_pdf.build_scanned_quote_pdf(RECORD)
# What a working OCR engine reads off the scanned page: the lines it prints.
OCR_TEXT = "\n".join([quote_pdf_title(RECORD), *quote_pdf_lines(RECORD)])

_LIVE_DOCAI = os.environ.get("BRIDGE_TEST_DOCAI") == "1" and bool(
    os.environ.get(DOCAI_PROCESSOR_ENV)
)


# --- fakes ------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _ScriptedModels:
    """genai ``models`` double: scripted texts in order (the last repeats)."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        return _FakeResponse(self._texts[min(len(self.calls), len(self._texts)) - 1])


class _FakeGenai:
    def __init__(self, *texts: str) -> None:
        self.models = _ScriptedModels(list(texts))


class _FakeDocAI:
    """A ``DocumentProcessorServiceClient`` double: records requests, returns text."""

    def __init__(self, text: str = OCR_TEXT, *, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.requests: list[dict] = []

    def process_document(self, request):
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(document=SimpleNamespace(text=self._text))


def _replay() -> str:
    return json.dumps(RECORD["extraction"])


def _scanned_resolver(name: str) -> tuple[bytes, str]:
    return SCANNED, "application/pdf"


def _engine(genai, docai, *, resolver=_scanned_resolver, **kw) -> GeminiExtractor:
    return GeminiExtractor(
        client=genai,
        resolver=resolver,
        prepass=DocAIPrepass(PROCESSOR, client=docai),
        **kw,
    )


def _assert_expected_terminal_outcome(result: ExtractionResult) -> None:
    quote = normalize_from_extraction(RECORD["party"], result)
    verdict = flag_quote(quote, load_program_standard(PROGRAM_STANDARD))
    assert quote == NormalizedQuote.model_validate(RECORD["expected_normalized"])
    assert {gap.rule_id for gap in verdict.gaps} == set(RECORD["expected_gaps"])
    assert verdict.route == ROUTE_NONE


# --- processor config -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "location"),
    [
        (PROCESSOR, "eu"),
        ("projects/123456789/locations/us/processors/p1/processorVersions/v2", "us"),
    ],
)
def test_parse_processor_accepts_the_resource_name(name, location):
    ref = parse_processor(name)
    assert ref.name == name
    assert ref.location == location
    assert ref.endpoint == f"{location}-documentai.googleapis.com"


def test_parse_processor_keeps_the_project():
    assert parse_processor(PROCESSOR).project == "client-project"


@pytest.mark.parametrize(
    "bad", ["abc", "projects/p/processors/x", f"{PROCESSOR}/extra", ""]
)
def test_parse_processor_rejects_a_malformed_name(bad):
    with pytest.raises(ValueError, match="malformed Document AI processor"):
        parse_processor(bad)


def test_resolve_processor_refuses_without_config(monkeypatch):
    monkeypatch.delenv(DOCAI_PROCESSOR_ENV, raising=False)
    with pytest.raises(RuntimeError, match=DOCAI_PROCESSOR_ENV):
        resolve_processor()
    with pytest.raises(RuntimeError, match=DOCAI_PROCESSOR_ENV):
        DocAIPrepass()


def test_prepass_reads_env_and_builds_no_client(monkeypatch):
    monkeypatch.setenv(DOCAI_PROCESSOR_ENV, PROCESSOR)
    prepass = DocAIPrepass()
    assert prepass.processor == PROCESSOR
    assert prepass.endpoint == "eu-documentai.googleapis.com"
    assert prepass._client is None


def test_explicit_processor_wins_over_env(monkeypatch):
    monkeypatch.setenv(DOCAI_PROCESSOR_ENV, "projects/o/locations/us/processors/z")
    assert DocAIPrepass(PROCESSOR).processor == PROCESSOR


# --- engine selection -------------------------------------------------------


def test_gcp_extraction_docai_is_gemini_with_the_prepass(monkeypatch):
    monkeypatch.setenv(DOCAI_PROCESSOR_ENV, PROCESSOR)
    engine = GcpExtraction(engine="docai")._resolve_engine()
    assert isinstance(engine, GeminiExtractor)
    assert isinstance(engine._prepass, DocAIPrepass)
    assert engine._prepass.processor == PROCESSOR
    assert engine._prepass._client is None  # nothing built before the first extract


def test_gcp_extraction_docai_refuses_without_a_processor(monkeypatch):
    """Refuse loud — never a silent downgrade to plain Gemini."""
    monkeypatch.delenv(DOCAI_PROCESSOR_ENV, raising=False)
    with pytest.raises(RuntimeError, match=DOCAI_PROCESSOR_ENV):
        GcpExtraction(engine="docai")._resolve_engine()


def test_gcp_extraction_docai_refuses_a_malformed_processor(monkeypatch):
    monkeypatch.setenv(DOCAI_PROCESSOR_ENV, "bogus")
    with pytest.raises(ValueError, match="malformed"):
        GcpExtraction(engine="docai")._resolve_engine()


def test_extraction_engine_env_selects_docai(monkeypatch):
    monkeypatch.setenv("BRIDGE_EXTRACTION_ENGINE", "docai")
    monkeypatch.setenv(DOCAI_PROCESSOR_ENV, PROCESSOR)
    assert isinstance(GcpExtraction()._resolve_engine()._prepass, DocAIPrepass)


def test_gemini_engine_has_no_prepass(monkeypatch):
    monkeypatch.setenv(DOCAI_PROCESSOR_ENV, PROCESSOR)
    assert GcpExtraction(engine="gemini")._resolve_engine()._prepass is None


# --- request shape ----------------------------------------------------------


async def test_bytes_ride_as_raw_document():
    docai = _FakeDocAI()
    await DocAIPrepass(PROCESSOR, client=docai)(
        ResolvedDocument(data=SCANNED, mime_type="application/pdf")
    )
    assert docai.requests == [
        {
            "name": PROCESSOR,
            "raw_document": {"content": SCANNED, "mime_type": "application/pdf"},
        }
    ]


async def test_gs_reference_rides_as_gcs_document_with_no_download():
    docai = _FakeDocAI()
    await DocAIPrepass(PROCESSOR, client=docai)(
        ResolvedDocument(uri="gs://bucket/quote.pdf", mime_type="application/pdf")
    )
    assert docai.requests == [
        {
            "name": PROCESSOR,
            "gcs_document": {
                "gcs_uri": "gs://bucket/quote.pdf",
                "mime_type": "application/pdf",
            },
        }
    ]


def test_build_process_request_is_none_for_an_unsupported_mime():
    assert build_process_request(PROCESSOR, mime_type="image/svg+xml", data=b"<svg/>") is None


def test_build_process_request_needs_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        build_process_request(PROCESSOR, mime_type="application/pdf")
    with pytest.raises(ValueError, match="exactly one"):
        build_process_request(
            PROCESSOR, mime_type="application/pdf", data=b"x", uri="gs://b/o"
        )


# --- the pre-pass feeds Gemini ----------------------------------------------


async def test_ocr_text_is_a_third_part_next_to_the_document():
    """``[prompt, application/pdf Part, OCR text]`` — the document is still sent."""
    from google.genai import types as genai_types  # lazily, never at module level

    genai, docai = _FakeGenai(_replay()), _FakeDocAI()
    result = await _engine(genai, docai).extract(ARTIFACT)

    assert result.fields.doctype == "benefits-quote"
    contents = genai.models.calls[0]["contents"]
    assert len(contents) == 3
    assert isinstance(contents[0], str) and "extraction engine" in contents[0]
    assert isinstance(contents[1], genai_types.Part)
    assert contents[1].inline_data.mime_type == "application/pdf"
    assert contents[1].inline_data.data == SCANNED
    assert contents[2] == build_ocr_context(OCR_TEXT)
    assert "EUR 1000.00" in contents[2]
    assert "attached document stays authoritative" in contents[2]
    # Still the schema-driven request.
    assert "response_schema" in genai.models.calls[0]["config"]


async def test_scanned_pdf_reaches_the_records_terminal_outcome():
    """The raster carrier-C quote, OCR'd then replayed, lands the oracle outcome."""
    result = await _engine(_FakeGenai(_replay()), _FakeDocAI()).extract(ARTIFACT)
    _assert_expected_terminal_outcome(result)


async def test_prepass_runs_once_and_the_ocr_part_survives_the_retry():
    genai, docai = _FakeGenai("{not json", _replay()), _FakeDocAI()
    result = await _engine(genai, docai).extract(ARTIFACT)

    assert result.fields.doctype == "benefits-quote"
    assert len(genai.models.calls) == 2
    assert len(docai.requests) == 1
    retry = genai.models.calls[1]["contents"]
    assert len(retry) == 4  # prompt, document, OCR, correction
    assert retry[2] == build_ocr_context(OCR_TEXT)
    assert "REJECTED" in retry[3]


async def test_svg_skips_document_ai():
    """The Address corpus (text SVG) keeps the plain-Gemini request shape."""
    canned = json.dumps(
        {
            "fields": {"doctype": "gov-id", "key_fields": {"name": "Jordan Lee"}},
            "overall_confidence": 0.9,
            "field_confidence": {},
            "legible": True,
            "flagged_fields": [],
        }
    )
    genai, docai = _FakeGenai(canned), _FakeDocAI()
    engine = GeminiExtractor(
        client=genai,
        corpus_root=ADDRESS_ROOT,
        prepass=DocAIPrepass(PROCESSOR, client=docai),
    )
    await engine.extract("images/gov-id-clean.svg")

    assert docai.requests == []
    contents = genai.models.calls[0]["contents"]
    assert len(contents) == 2
    assert isinstance(contents[1], str) and "<svg" in contents[1]


async def test_document_ai_error_propagates_with_no_gemini_call():
    genai = _FakeGenai(_replay())
    docai = _FakeDocAI(error=RuntimeError("403 PERMISSION_DENIED documentai"))
    with pytest.raises(RuntimeError, match="403"):
        await _engine(genai, docai).extract(ARTIFACT)
    assert genai.models.calls == []


async def test_empty_ocr_text_adds_no_part_and_warns(caplog):
    genai, docai = _FakeGenai(_replay()), _FakeDocAI(text="  \n ")
    with caplog.at_level(logging.WARNING, logger="bridge.extraction.docai"):
        await _engine(genai, docai).extract(ARTIFACT)

    assert len(docai.requests) == 1
    assert len(genai.models.calls[0]["contents"]) == 2
    assert any("returned no text" in r.getMessage() for r in caplog.records)


async def test_async_client_is_awaited():
    class _AsyncDocAI(_FakeDocAI):
        async def process_document(self, request):  # type: ignore[override]
            return super().process_document(request)

    text = await DocAIPrepass(PROCESSOR, client=_AsyncDocAI())(
        ResolvedDocument(data=SCANNED, mime_type="application/pdf")
    )
    assert text == OCR_TEXT


# --- pure helpers -----------------------------------------------------------


def _text_block(text: str, *children) -> SimpleNamespace:
    return SimpleNamespace(
        text_block=SimpleNamespace(text=text, blocks=list(children))
    )


def test_document_text_prefers_document_text():
    assert document_text(SimpleNamespace(text="OCR page")) == "OCR page"


def test_document_text_flattens_layout_parser_blocks():
    """A Layout Parser response: empty ``text``, nested ``document_layout.blocks``."""
    table = SimpleNamespace(
        table_block=SimpleNamespace(
            header_rows=[
                SimpleNamespace(
                    cells=[
                        SimpleNamespace(blocks=[_text_block("Premium")]),
                        SimpleNamespace(blocks=[_text_block("Deductible")]),
                    ]
                )
            ],
            body_rows=[
                SimpleNamespace(
                    cells=[
                        SimpleNamespace(blocks=[_text_block("EUR 1000.00")]),
                        SimpleNamespace(blocks=[_text_block("EUR 500.00")]),
                    ]
                )
            ],
        )
    )
    listing = SimpleNamespace(
        list_block=SimpleNamespace(
            list_entries=[SimpleNamespace(blocks=[_text_block("chirurgie esthétique")])]
        )
    )
    document = SimpleNamespace(
        text="",
        document_layout=SimpleNamespace(
            blocks=[
                _text_block("Devis", _text_block("Carrier: carrier-c")),
                table,
                listing,
            ]
        ),
    )
    assert document_text(document) == (
        "Devis\nCarrier: carrier-c\nPremium | Deductible\n"
        "EUR 1000.00 | EUR 500.00\nchirurgie esthétique"
    )


def test_document_text_of_nothing_is_empty():
    assert document_text(None) == ""
    assert document_text(SimpleNamespace(text="", document_layout=None)) == ""


def test_build_ocr_context_is_bounded_and_says_so():
    short = build_ocr_context("EUR 1000.00")
    assert "EUR 1000.00" in short and "truncated" not in short

    runaway = "x" * (MAX_OCR_CHARS + 500)
    bounded = build_ocr_context(runaway)
    assert f"truncated to the first {MAX_OCR_CHARS} characters" in bounded
    assert "x" * MAX_OCR_CHARS in bounded
    assert "x" * (MAX_OCR_CHARS + 1) not in bounded


# --- structure --------------------------------------------------------------


def _google_import_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    if isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.module:
        names = [node.module]
    return [n for n in names if n == "google" or n.startswith("google.")]


def test_docai_imports_documentai_lazily_not_at_module_level():
    tree = ast.parse((REPO / "bridge/extraction/docai.py").read_text())
    for stmt in tree.body:
        assert not _google_import_names(stmt), (
            f"google imported at module level: {ast.dump(stmt)} — must be lazy"
        )
    nested = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom)) and _google_import_names(n)
    ]
    assert nested, "expected a lazy google.cloud.documentai import inside a method"


def test_docai_imports_no_edge_seams_or_agents():
    """Portable/vendorable: stdlib + ``bridge.extraction.resolve`` only."""
    tree = ast.parse((REPO / "bridge/extraction/docai.py").read_text())
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            assert not stmt.module.startswith(
                ("bridge.edge", "bridge.seams", "agents")
            ), stmt.module
            if stmt.module.startswith("bridge"):
                assert stmt.module == "bridge.extraction.resolve", stmt.module


# --- the scanned asset ------------------------------------------------------


def test_scanned_pdf_is_deterministic_and_has_no_text_layer(tmp_path):
    again = corpus_scanned_pdf.write_scanned_quote_pdf(RECORD, tmp_path / "s.pdf")
    assert again.read_bytes() == SCANNED
    assert SCANNED.startswith(b"%PDF-1.")
    assert SCANNED.endswith(b"%%EOF\n")
    assert b"/Subtype /Image" in SCANNED and b"/Im0" in SCANNED
    assert b"/Font" not in SCANNED
    content = SCANNED[SCANNED.rindex(b"5 0 obj") :]
    assert b"BT" not in content and b"Tj" not in content
    if shutil.which("pdftotext"):
        text = subprocess.run(
            ["pdftotext", str(again), "-"], capture_output=True, check=True
        ).stdout
        assert text.strip() == b"", text[:80]


def test_scanned_pdf_prints_ink_for_every_line():
    """Every body line lands on the page (non-vacuous raster)."""
    pixels = corpus_scanned_pdf.render_page("T", ["A", "B"])
    assert pixels.count(bytes([corpus_scanned_pdf.INK])) > 0
    blank = corpus_scanned_pdf.render_page(" ", [" "])
    assert bytes([corpus_scanned_pdf.INK]) not in blank


def test_missing_glyph_raises_never_drops():
    with pytest.raises(ValueError, match="no glyph"):
        corpus_scanned_pdf.render_page("Devis", ["Prime: 1000 ¤"])


def test_font_covers_every_character_the_quote_prints():
    printed = set(quote_pdf_title(RECORD) + "".join(quote_pdf_lines(RECORD)))
    assert printed <= set(corpus_scanned_pdf.GLYPHS), printed - set(
        corpus_scanned_pdf.GLYPHS
    )


# --- live (gated) -----------------------------------------------------------


def _money(raw: str) -> Decimal:
    cleaned = raw.upper().replace("EUR", "").replace("€", "").replace(",", "").strip()
    return Decimal(cleaned).quantize(Decimal("0.01"))


def _free_text(raw: str) -> str:
    return raw.strip().strip(".").lower()


_LIVE_REASON = (
    "set BRIDGE_TEST_DOCAI=1 and BRIDGE_DOCAI_PROCESSOR (plus Vertex Gemini creds for "
    "the full extraction) to run the live Document AI pre-pass"
)


@pytest.mark.skipif(not _LIVE_DOCAI, reason=_LIVE_REASON)
async def test_live_docai_ocr_reads_the_scanned_page():
    text = await DocAIPrepass()(
        ResolvedDocument(data=SCANNED, mime_type="application/pdf")
    )
    assert text is not None
    for value in ("1000.00", "500.00", "2026-01-01"):
        assert value in text, (value, text[:400])


@pytest.mark.skipif(not _LIVE_DOCAI, reason=_LIVE_REASON)
async def test_live_docai_engine_terminal_outcome():
    """The real docai engine (Document AI OCR + Vertex Gemini) on the scanned quote.

    Same test-local canonicalizer as ``test_pdf_extraction.py``'s live test —
    echo-rendering variance only; ``bridge/`` and the oracle are never loosened.
    """
    engine = GcpExtraction(engine="docai")._resolve_engine()
    engine._resolver = _scanned_resolver
    result = await engine.extract(ARTIFACT)

    assert result.fields.doctype == "benefits-quote"
    kf = dict(result.fields.key_fields)
    assert _money(kf["premium"]) == Decimal("1000.00")
    assert _money(kf["deductible"]) == Decimal("500.00")
    kf["premium"] = str(_money(kf["premium"]))
    kf["deductible"] = str(_money(kf["deductible"]))
    for field in ("coverage_limits", "exclusions"):
        kf[field] = _free_text(kf[field])
    canonical = result.model_copy(
        update={"fields": result.fields.model_copy(update={"key_fields": kf})}
    )
    _assert_expected_terminal_outcome(canonical)
