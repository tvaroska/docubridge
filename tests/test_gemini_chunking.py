"""``GeminiExtractor`` page-window chunking (S0-perf-1) — credential-free, fake client.

The engine-level contract: a short document is still ONE call with ``[prompt, document]``
(every Address corpus record); a long inline PDF goes straight to page windows (the
WHOLE document + a window instruction per request, concurrently, resolver + prepass once);
a truncated single call is bisected by page and never echoed back through the retry loop
(a ``gs://`` object first gets a page-count survey); a truncated single page /
``chunk_pages=0`` / the window cap raise ``ExtractionTruncated``; schema retries and
transport errors keep their S0-bug-2 semantics per window; the event loop is never
blocked; the env knobs refuse loud.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bridge.extraction.docai import build_ocr_context
from bridge.extraction.gemini import (
    CHUNK_CONCURRENCY_ENV,
    CHUNK_PAGES_ENV,
    MAX_OUTPUT_TOKENS_ENV,
    SURVEY_MAX_OUTPUT_TOKENS,
    SURVEY_PROMPT,
    SURVEY_SCHEMA,
    ExtractionTruncated,
    GeminiExtractor,
    build_window_instruction,
)
from bridge.extraction.resolve import ResolvedDocument
from tests.perf.fake_gemini import PageAwareFakeClient
from tests.perf.scale_corpus import build_scale_pdf, rate_sheet_1000, scale_doc

CORRECTION_MARK = "was REJECTED by schema validation"
CORPUS_PATH = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
CORPUS_RECORDS = json.loads(CORPUS_PATH.read_text())["documents"]


def _small(rate_pages: int, rows: int = 40):
    return scale_doc("small", "Synthetic small quote", [rows] * rate_pages, [])


class _CountingResolver:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def __call__(self, name: str):
        self.calls += 1
        return self.value


def _extractor(doc, client=None, **kwargs):
    pdf = build_scale_pdf(doc)
    client = client or PageAwareFakeClient(doc)
    resolver = kwargs.pop("resolver", _CountingResolver((pdf, "application/pdf")))
    return GeminiExtractor(client=client, resolver=resolver, **kwargs), client, resolver


def _has_correction(call) -> bool:
    return any(isinstance(c, str) and CORRECTION_MARK in c for c in call["contents"])


# -- short documents: unchanged single call ----------------------------------------


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_address_corpus_records_are_one_call_prompt_document(record):
    """The real corpus SVGs through the default resolver: ONE call, ``[prompt, doc]``."""
    calls = []

    class _Models:
        def generate_content(self, *, model, contents, config):
            calls.append({"contents": contents, "config": config})
            return type("R", (), {"text": json.dumps(record["extraction"])})()

    client = type("C", (), {"models": _Models()})()
    result = await GeminiExtractor(client=client).extract(record["artifact"])

    assert len(calls) == 1
    contents = calls[0]["contents"]
    assert len(contents) == 2
    assert isinstance(contents[0], str) and contents[1].lstrip().startswith("<svg")
    assert calls[0]["config"]["max_output_tokens"] == 32768
    assert result.fields.doctype == record["extraction"]["fields"]["doctype"]


async def test_short_pdf_is_one_call_without_a_window_instruction():
    doc = _small(7)  # 8 pages == chunk_pages: not chunked
    extractor, client, _ = _extractor(doc)
    result = await extractor.extract("short.pdf")
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["kind"] == "single" and len(call["contents"]) == 2
    assert call["config"]["max_output_tokens"] == 32768
    assert result.fields.tables["rates"] == doc.tables["rates"]


# -- proactive windows -------------------------------------------------------------


async def test_long_pdf_is_extracted_as_page_windows():
    doc = rate_sheet_1000()
    pdf = build_scale_pdf(doc)
    prepass_calls = []

    def prepass(resolved):
        prepass_calls.append(resolved)
        return "OCR TEXT OF THE WHOLE DOCUMENT"

    extractor, client, resolver = _extractor(doc, prepass=prepass)
    result = await extractor.extract("rate-sheet-1000.pdf")

    assert resolver.calls == 1 and len(prepass_calls) == 1
    assert [c["window"] for c in client.calls] == [(1, 8), (9, 16), (17, 24), (25, 26)]
    ocr = build_ocr_context("OCR TEXT OF THE WHOLE DOCUMENT")
    for call in client.calls:
        prompt, document, ocr_part, instruction = call["contents"]
        assert prompt == client.calls[0]["contents"][0]  # the same catalog prompt
        assert document.inline_data.data == pdf  # the WHOLE document, never a split
        assert ocr_part == ocr
        a, b = call["window"]
        assert instruction == build_window_instruction(a, b, 26)
        assert call["config"]["max_output_tokens"] == 32768
    assert result.fields.tables["rates"] == doc.tables["rates"]
    assert result.fields.key_fields == doc.key_fields
    assert not result.flagged_fields


async def test_windows_run_concurrently_bounded_by_chunk_concurrency():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, latency_s=0.05)
    extractor, _, _ = _extractor(doc, client=client, chunk_pages=2)
    await extractor.extract("rate-sheet-1000.pdf")
    assert len(client.window_calls) == 13
    assert 1 < client.max_in_flight <= 4


async def test_chunk_concurrency_one_is_sequential():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, latency_s=0.01)
    extractor, _, _ = _extractor(doc, client=client, chunk_concurrency=1)
    await extractor.extract("rate-sheet-1000.pdf")
    assert client.max_in_flight == 1


async def test_the_event_loop_is_not_blocked_by_a_slow_call():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, latency_s=0.3)
    extractor, _, _ = _extractor(doc, client=client)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await extractor.extract("rate-sheet-1000.pdf")
    finally:
        task.cancel()
    assert ticks >= 5


async def test_single_call_runs_off_the_loop_too():
    doc = _small(2)
    client = PageAwareFakeClient(doc, latency_s=0.2)
    extractor, _, _ = _extractor(doc, client=client)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await extractor.extract("small.pdf")
    finally:
        task.cancel()
    assert len(client.calls) == 1 and ticks >= 5


# -- reactive bisection ------------------------------------------------------------


async def test_truncated_single_call_is_bisected_never_retried():
    doc = rate_sheet_1000()
    extractor, client, _ = _extractor(doc, chunk_pages=100)
    result = await extractor.extract("rate-sheet-1000.pdf")
    assert client.calls[0]["kind"] == "single" and client.calls[0]["truncated"]
    assert sorted(c["window"] for c in client.window_calls) == [(1, 13), (14, 26)]
    assert len(client.calls) == 3
    assert not any(_has_correction(c) for c in client.calls)  # no echo of a cut body
    assert result.fields.tables["rates"] == doc.tables["rates"]


async def test_truncated_window_is_bisected_recursively():
    doc = rate_sheet_1000()
    # 8 pages x 40 rows x 60 + 400 = 19,600 > 12,000; 4 pages = 10,000 fits
    extractor, client, _ = _extractor(doc, max_output_tokens=12000)
    result = await extractor.extract("rate-sheet-1000.pdf")
    truncated = [c["window"] for c in client.calls if c["truncated"]]
    assert sorted(truncated) == [(1, 8), (9, 16), (17, 24)]
    assert not any(_has_correction(c) for c in client.calls)
    assert result.fields.tables["rates"] == doc.tables["rates"]


async def test_a_single_page_that_overflows_raises_extraction_truncated():
    doc = _small(3)
    extractor, client, _ = _extractor(doc, chunk_pages=100, max_output_tokens=1000)
    with pytest.raises(ExtractionTruncated) as info:
        await extractor.extract("small.pdf")
    assert "pages 2-2" in str(info.value)
    assert "page 2 alone overflows" in str(info.value)
    assert any(MAX_OUTPUT_TOKENS_ENV in note for note in info.value.__notes__)
    assert not any(_has_correction(c) for c in client.calls)


async def test_chunking_disabled_raises_after_one_call():
    doc = rate_sheet_1000()
    extractor, client, _ = _extractor(doc, chunk_pages=0)
    with pytest.raises(ExtractionTruncated, match="chunk_pages=0"):
        await extractor.extract("rate-sheet-1000.pdf")
    assert len(client.calls) == 1


async def test_non_paged_document_truncation_raises():
    class _Cut:
        def __init__(self):
            self.models = self
            self.calls = 0

        def generate_content(self, *, model, contents, config):
            self.calls += 1
            return type(
                "R",
                (),
                {
                    "text": '{"fields": {"doc',
                    "candidates": [type("C", (), {"finish_reason": "MAX_TOKENS"})()],
                },
            )()

    client = _Cut()
    extractor = GeminiExtractor(
        client=client, resolver=lambda n: (b"<svg/>", "image/svg+xml")
    )
    with pytest.raises(ExtractionTruncated, match="not paged"):
        await extractor.extract("x.svg")
    assert client.calls == 1


async def test_genai_enum_finish_reason_is_detected():
    from google.genai import types as genai_types

    class _Cut:
        def __init__(self):
            self.models = self

        def generate_content(self, *, model, contents, config):
            return type(
                "R",
                (),
                {
                    "text": "{",
                    "candidates": [
                        type(
                            "C",
                            (),
                            {"finish_reason": genai_types.FinishReason.MAX_TOKENS},
                        )()
                    ],
                },
            )()

    extractor = GeminiExtractor(
        client=_Cut(), resolver=lambda n: (b"<svg/>", "image/svg+xml"), chunk_pages=0
    )
    with pytest.raises(ExtractionTruncated):
        await extractor.extract("x.svg")


async def test_by_reference_pdf_is_surveyed_then_windowed():
    doc = rate_sheet_1000()
    resolver = _CountingResolver(
        ResolvedDocument(
            uri="gs://synthetic-bucket/rate-sheet-1000.pdf", mime_type="application/pdf"
        )
    )
    extractor, client, _ = _extractor(doc, resolver=resolver)
    result = await extractor.extract("gs://synthetic-bucket/rate-sheet-1000.pdf")

    kinds = [c["kind"] for c in client.calls]
    assert kinds[:2] == ["single", "survey"] and kinds[2:] == ["window", "window"]
    survey = client.survey_calls[0]
    assert survey["contents"][0] == SURVEY_PROMPT and len(survey["contents"]) == 2
    assert survey["config"]["response_schema"] == SURVEY_SCHEMA
    assert survey["config"]["max_output_tokens"] == SURVEY_MAX_OUTPUT_TOKENS
    for call in client.window_calls:
        assert call["contents"][1].file_data.file_uri.startswith("gs://")
    assert resolver.calls == 1
    assert result.fields.tables["rates"] == doc.tables["rates"]


async def test_a_failed_survey_raises_extraction_truncated():
    class _Client:
        def __init__(self):
            self.models = self
            self.calls = []

        def generate_content(self, *, model, contents, config):
            self.calls.append(contents)
            if contents[0] == SURVEY_PROMPT:
                return type("R", (), {"text": '{"page_count": "many"}'})()
            return type(
                "R",
                (),
                {
                    "text": "{",
                    "candidates": [type("C", (), {"finish_reason": "MAX_TOKENS"})()],
                },
            )()

    extractor = GeminiExtractor(
        client=_Client(),
        resolver=lambda n: ResolvedDocument(
            uri="gs://b/x.pdf", mime_type="application/pdf"
        ),
    )
    with pytest.raises(ExtractionTruncated, match="survey failed"):
        await extractor.extract("gs://b/x.pdf")


# -- per-window retry + failures ---------------------------------------------------


async def test_a_bad_window_is_repaired_by_its_own_retry():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, faults={(9, 16): "bad_then_good"})
    extractor, _, _ = _extractor(doc, client=client)
    result = await extractor.extract("rate-sheet-1000.pdf")
    assert len(client.calls) == 5
    retried = [c for c in client.calls if _has_correction(c)]
    assert [c["window"] for c in retried] == [(9, 16)]
    # the correction turn is appended AFTER the window instruction
    assert retried[0]["contents"][2] == build_window_instruction(9, 16, 26)
    assert result.fields.tables["rates"] == doc.tables["rates"]


async def test_an_always_bad_window_fails_the_whole_extraction():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, faults={(17, 24): "always_bad"})
    extractor, _, _ = _extractor(doc, client=client, max_attempts=2)
    with pytest.raises(ValidationError) as info:
        await extractor.extract("rate-sheet-1000.pdf")
    assert any("rate-sheet-1000.pdf pages 17-24" in n for n in info.value.__notes__)


async def test_a_transport_error_propagates_bare():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, faults={(9, 16): "error"})
    extractor, _, _ = _extractor(doc, client=client)
    with pytest.raises(RuntimeError, match="503") as info:
        await extractor.extract("rate-sheet-1000.pdf")
    assert type(info.value) is RuntimeError  # never an ExceptionGroup


async def test_several_failing_windows_raise_the_first_by_page_order():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, faults={(17, 24): "error", (9, 16): "error"})
    extractor, _, _ = _extractor(doc, client=client)
    with pytest.raises(RuntimeError) as info:
        await extractor.extract("rate-sheet-1000.pdf")
    notes = getattr(info.value, "__notes__", [])
    assert any("2 page windows" in n and "pages 9-16" in n for n in notes)


# -- caps + knobs ------------------------------------------------------------------


async def test_the_window_cap_refuses_before_any_call():
    doc = rate_sheet_1000()
    extractor, client, _ = _extractor(doc, chunk_pages=1, max_windows=10)
    with pytest.raises(ExtractionTruncated, match="max_windows=10"):
        await extractor.extract("rate-sheet-1000.pdf")
    assert client.calls == []


async def test_the_window_cap_bounds_reactive_bisection():
    doc = rate_sheet_1000()
    extractor, client, _ = _extractor(doc, chunk_pages=100, max_windows=1)
    with pytest.raises(ExtractionTruncated, match="max_windows=1 cap"):
        await extractor.extract("rate-sheet-1000.pdf")
    assert len(client.window_calls) <= 1


def test_defaults(monkeypatch):
    for env in (MAX_OUTPUT_TOKENS_ENV, CHUNK_PAGES_ENV, CHUNK_CONCURRENCY_ENV):
        monkeypatch.delenv(env, raising=False)
    e = GeminiExtractor(client=object())
    assert (
        e._max_output_tokens,
        e._chunk_pages,
        e._chunk_concurrency,
        e._max_windows,
    ) == (
        32768,
        8,
        4,
        128,
    )


def test_env_knobs_are_read(monkeypatch):
    monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, "16000")
    monkeypatch.setenv(CHUNK_PAGES_ENV, "0")
    monkeypatch.setenv(CHUNK_CONCURRENCY_ENV, " 2 ")
    e = GeminiExtractor(client=object())
    assert (e._max_output_tokens, e._chunk_pages, e._chunk_concurrency) == (16000, 0, 2)


def test_a_kwarg_wins_over_the_env(monkeypatch):
    monkeypatch.setenv(CHUNK_PAGES_ENV, "not-a-number")
    e = GeminiExtractor(client=object(), chunk_pages=3)
    assert e._chunk_pages == 3


@pytest.mark.parametrize(
    "env,raw",
    [
        (MAX_OUTPUT_TOKENS_ENV, "0"),
        (MAX_OUTPUT_TOKENS_ENV, "lots"),
        (CHUNK_PAGES_ENV, "x"),
        (CHUNK_PAGES_ENV, "-1"),
        (CHUNK_CONCURRENCY_ENV, "0"),
        (CHUNK_CONCURRENCY_ENV, "1.5"),
    ],
)
def test_a_bad_env_value_refuses_naming_the_env(monkeypatch, env, raw):
    monkeypatch.setenv(env, raw)
    with pytest.raises(ValueError, match=env):
        GeminiExtractor(client=object())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_output_tokens": 0},
        {"chunk_pages": -1},
        {"chunk_concurrency": 0},
        {"max_windows": 0},
        {"chunk_pages": True},
    ],
)
def test_a_bad_kwarg_refuses(kwargs):
    with pytest.raises(ValueError, match=next(iter(kwargs))):
        GeminiExtractor(client=object(), **kwargs)
