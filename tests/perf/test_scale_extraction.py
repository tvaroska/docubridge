"""RFP-scale extraction through the real ``GeminiExtractor`` + the page-aware fake (S0-perf-1).

The two scale documents reproduce their oracle EXACTLY (count + order), a flagged row
deep in the sheet merges onto its GLOBAL row path, the unchunked "before" leg reproduces
the live failure (one truncated call, ``ExtractionTruncated``, no futile retries), and the
docai engine shape (OCR pre-pass) still runs its pre-pass once for a 50-page document.
"""

from __future__ import annotations

from bridge.extraction.docai import build_ocr_context
from bridge.extraction.gemini import ExtractionTruncated, GeminiExtractor
from contract.extraction import fields_requiring_review
from tests.perf.fake_gemini import PageAwareFakeClient
from tests.perf.scale_corpus import build_scale_pdf, quote_50_pages, rate_sheet_1000


def _extractor(doc, client, **kwargs):
    pdf = build_scale_pdf(doc)
    return GeminiExtractor(
        client=client, resolver=lambda n: (pdf, "application/pdf"), **kwargs
    )


async def test_rate_sheet_1000_rows_exact_and_ordered():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc)
    result = await _extractor(doc, client).extract("rate-sheet-1000.pdf")
    assert len(client.window_calls) == 4 and len(client.calls) == 4
    rates = result.fields.tables["rates"]
    assert len(rates) == 1000
    assert rates == doc.tables["rates"]
    assert len({tuple(r.items()) for r in rates}) == 1000
    assert result.fields.key_fields == doc.key_fields
    assert result.flagged_fields == []


async def test_quote_50_pages_rates_and_design_exact():
    doc = quote_50_pages()
    client = PageAwareFakeClient(doc)
    result = await _extractor(doc, client).extract("quote-50p.pdf")
    assert doc.total_pages == 50
    assert len(client.window_calls) == 7
    assert result.fields.tables["rates"] == doc.tables["rates"]
    assert len(result.fields.tables["rates"]) == 1380
    assert result.fields.tables["design"] == doc.tables["design"]
    assert len(result.fields.tables["design"]) == 60


async def test_a_flagged_row_merges_onto_its_global_path():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc, flag_rows=(777,))
    result = await _extractor(doc, client).extract("rate-sheet-1000.pdf")
    path = "fields.tables.rates[777].frequency"
    assert result.flagged_fields == [path]
    assert result.field_confidence[path] == 0.41
    assert [r.path for r in result.field_reviews] == [path]
    assert fields_requiring_review(result) == [path]
    # the fake emitted it WINDOW-LOCALLY (pages 17-24 start at row 600 -> local 177)
    assert result.fields.tables["rates"][777] == doc.tables["rates"][777]


async def test_unchunked_leg_raises_after_exactly_one_call():
    doc = rate_sheet_1000()
    client = PageAwareFakeClient(doc)
    extractor = _extractor(doc, client, chunk_pages=0)
    try:
        await extractor.extract("rate-sheet-1000.pdf")
    except ExtractionTruncated as exc:
        assert "max_output_tokens=32768" in str(exc)
    else:  # pragma: no cover - the assertion that matters
        raise AssertionError("expected ExtractionTruncated")
    assert len(client.calls) == 1 and client.calls[0]["truncated"]


async def test_docai_shaped_extraction_runs_one_prepass_for_fifty_pages():
    doc = quote_50_pages()
    client = PageAwareFakeClient(doc)
    seen = []

    async def prepass(resolved):
        seen.append(resolved.mime_type)
        return "synthetic OCR text"

    result = await _extractor(doc, client, prepass=prepass).extract("quote-50p.pdf")
    assert seen == ["application/pdf"]
    ocr = build_ocr_context("synthetic OCR text")
    assert all(c["contents"][2] == ocr for c in client.window_calls)
    assert len(result.fields.tables["rates"]) == 1380
