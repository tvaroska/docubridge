"""LIVE RFP-scale extraction (S0-perf-1) — gated on ``BRIDGE_TEST_GEMINI=1``.

Drives the 26-page synthetic 1000-row rate sheet through the REAL Gemini client
(``GOOGLE_GENAI_USE_VERTEXAI=TRUE`` + ADC + ``GOOGLE_CLOUD_PROJECT`` /
``GOOGLE_CLOUD_LOCATION``) with the default knobs: the row COUNT must be exact and >= 99%
of rows must match the oracle at the same index after a test-local canonicalizer
(``$``/``USD``/label echoes stripped, casefolded — precedent S0-arch-9). Skips in the
default suite like every other live leg.
"""

from __future__ import annotations

import os

import pytest

from bridge.extraction.gemini import GeminiExtractor
from tests.perf.harness import rows_match
from tests.perf.scale_corpus import build_scale_pdf, rate_sheet_1000

pytestmark = pytest.mark.skipif(
    os.environ.get("BRIDGE_TEST_GEMINI") != "1",
    reason="live Gemini scale run; set BRIDGE_TEST_GEMINI=1 (+ Vertex credentials)",
)


async def test_live_rate_sheet_1000_rows():
    doc = rate_sheet_1000()
    pdf = build_scale_pdf(doc)
    extractor = GeminiExtractor(resolver=lambda n: (pdf, "application/pdf"))
    result = await extractor.extract("rate-sheet-1000.pdf")
    got = result.fields.tables.get("rates", [])
    want = doc.tables["rates"]
    assert len(got) == len(want) == 1000
    assert rows_match(got, want, canonical=True) >= 990
