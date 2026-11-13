"""Unit tests for ``bridge/extraction/chunking.py`` (S0-perf-1) — pure, credential-free.

Covers the stdlib page count (committed corpus PDFs, the multi-page generator, a PDF 1.5
compressed object stream, garbage, a decompression bomb, the raster scanned PDF), window
planning/bisection, the code-side merge rules (rows concatenated in order with no dedupe,
first-wins scalars with flagged disagreement, min confidence, ``legible`` conjunction) and
row-path rebasing (incl. coarsening an un-emitted row index with a warning), plus the
import-isolation guard (vendorable: no ``google*`` / ``agents`` / ``bridge.ports`` /
``bridge.edge``).
"""

from __future__ import annotations

import ast
import logging
import zlib
from pathlib import Path

import pytest

from bridge.extraction.chunking import (
    MAX_INFLATED_BYTES,
    ExtractionTruncated,
    bisect_window,
    merge_window_results,
    pdf_page_count,
    plan_windows,
    rebase_path,
)
from contract.enums import ReviewReason
from contract.extraction import ExtractionResult, fields_requiring_review
from tests.corpus_pdf import (
    CONFORMANCE_DOC_IDS,
    CONFORMANCE_PATH,
    PDF_DOC_IDS,
    build_conformance_pdf,
    build_multipage_pdf,
    build_pdf,
    build_quote_pdf,
    load_records,
)
from tests.corpus_scanned_pdf import build_scanned_quote_pdf

CHUNKING = Path(__file__).resolve().parents[1] / "bridge/extraction/chunking.py"


# --------------------------------------------------------------------------- #
# pdf_page_count                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("doc_id", [*PDF_DOC_IDS, *CONFORMANCE_DOC_IDS])
def test_committed_benefits_pdfs_are_one_page(doc_id):
    if doc_id in PDF_DOC_IDS:
        raw = build_quote_pdf(load_records()[doc_id])
    else:
        raw = build_conformance_pdf(load_records(CONFORMANCE_PATH)[doc_id])
    assert pdf_page_count(raw) == 1


def test_single_page_generator_is_one_page():
    assert pdf_page_count(build_pdf("t", ["a", "b"])) == 1


@pytest.mark.parametrize("n", [1, 2, 9, 26, 50])
def test_multipage_generator_counts(n):
    pdf = build_multipage_pdf([(f"title {i}", [f"line {i}"]) for i in range(n)])
    assert pdf_page_count(pdf) == n


def test_raster_scanned_pdf_is_one_page():
    record = load_records()["benefits-carrier-c-eur-fr"]
    assert pdf_page_count(build_scanned_quote_pdf(record)) == 1


def _objstm_pdf(body: bytes) -> bytes:
    """A PDF 1.5 whose page tree lives ONLY inside a Flate-compressed object stream."""
    stream = zlib.compress(body)
    return b"".join(
        [
            b"%PDF-1.5\n",
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
            b"9 0 obj\n<< /Type /ObjStm /N 4 /First 20 /Filter /FlateDecode /Length ",
            str(len(stream)).encode(),
            b" >>\nstream\n",
            stream,
            b"\nendstream\nendobj\n",
            b"%%EOF\n",
        ]
    )


def test_compressed_object_stream_page_tree_is_counted():
    body = (
        b"2 0 3 60 4 120 5 180 "
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >> "
        b"<< /Type /Page /Parent 2 0 R >> << /Type /Page /Parent 2 0 R >> "
        b"<< /Type /Page /Parent 2 0 R >>"
    )
    pdf = _objstm_pdf(body)
    assert b"/Type /Pages" not in pdf  # only visible after inflating
    assert pdf_page_count(pdf) == 3


def test_page_leaves_are_counted_without_a_pages_count():
    pdf = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj 2 0 obj << /Type /Page >> endobj"
    assert pdf_page_count(pdf) == 2


def test_nested_page_tree_takes_the_root_count():
    pdf = (
        b"%PDF-1.4\n2 0 obj << /Type /Pages /Kids [3 0 R 4 0 R] /Count 12 >> endobj "
        b"3 0 obj << /Type /Pages /Parent 2 0 R /Count 5 >> endobj "
        b"4 0 obj << /Count 7 /Type /Pages /Parent 2 0 R >> endobj"
    )
    assert pdf_page_count(pdf) == 12


@pytest.mark.parametrize(
    "data",
    [b"", b"not a pdf at all", b"%PDF-1.7\n%%EOF", bytes(range(256)) * 10, None, "str"],
)
def test_garbage_is_none_never_raises(data):
    assert pdf_page_count(data) is None


def test_corrupt_object_stream_is_skipped():
    pdf = b"%PDF-1.5\n9 0 obj << /Type /ObjStm /Filter /FlateDecode >> stream\nxx\nendstream"
    assert pdf_page_count(pdf) is None


def test_decompression_bomb_is_skipped_not_followed():
    bomb = b"<< /Type /Pages /Count 4 >>" + b"\0" * (MAX_INFLATED_BYTES + 1024)
    assert pdf_page_count(_objstm_pdf(bomb)) is None


# --------------------------------------------------------------------------- #
# plan / bisect                                                               #
# --------------------------------------------------------------------------- #


def test_plan_windows():
    assert plan_windows(26, 8) == [(1, 8), (9, 16), (17, 24), (25, 26)]
    assert plan_windows(8, 8) == [(1, 8)]
    assert plan_windows(1, 8) == [(1, 1)]
    assert len(plan_windows(50, 8)) == 7


@pytest.mark.parametrize("total,size", [(0, 8), (5, 0), (-1, 1)])
def test_plan_windows_refuses_nonsense(total, size):
    with pytest.raises(ValueError, match="plan_windows"):
        plan_windows(total, size)


def test_bisect_window():
    assert bisect_window(1, 26) == ((1, 13), (14, 26))
    assert bisect_window(9, 16) == ((9, 12), (13, 16))
    assert bisect_window(3, 4) == ((3, 3), (4, 4))
    assert bisect_window(1, 3) == ((1, 2), (3, 3))
    with pytest.raises(ValueError, match="single-page"):
        bisect_window(5, 5)


def test_extraction_truncated_is_a_value_error():
    assert issubclass(ExtractionTruncated, ValueError)


# --------------------------------------------------------------------------- #
# rebase_path                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "fields.tables.rates[3].frequency",
            ("fields.tables.rates[43].frequency", False),
        ),
        ("tables.rates[0]", ("tables.rates[40]", False)),
        ("rates[2].amount", ("rates[42].amount", False)),
        ("fields.tables.design[1].value", ("fields.tables.design[1].value", False)),
        ("fields.key_fields.premium", ("fields.key_fields.premium", False)),
        ("fields.tables.other[1]", ("fields.tables.other[1]", False)),  # unknown table
        ("fields.tables.rates[9].amount", ("fields.tables.rates", True)),  # not emitted
    ],
)
def test_rebase_path(path, expected):
    assert (
        rebase_path(
            path,
            offsets={"rates": 40},
            window_rows={"rates": 5, "design": 2},
            tables={"rates", "design"},
        )
        == expected
    )


# --------------------------------------------------------------------------- #
# merge_window_results                                                        #
# --------------------------------------------------------------------------- #


def _rows(table: str, start: int, n: int) -> list[dict[str, str]]:
    return [{"id": f"{table}-{i}"} for i in range(start, start + n)]


def _result(
    *,
    key_fields=None,
    tables=None,
    issuer="Synthetic Mutual",
    doctype="benefits-quote",
    overall=0.9,
    confidence=None,
    legible=True,
    flagged=(),
    reviews=(),
) -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "fields": {
                "doctype": doctype,
                "issuer": issuer,
                "key_fields": key_fields
                if key_fields is not None
                else {"currency": "USD"},
                "tables": tables or {},
            },
            "overall_confidence": overall,
            "field_confidence": confidence or {},
            "legible": legible,
            "flagged_fields": list(flagged),
            "field_reviews": list(reviews),
        }
    )


def test_one_window_is_returned_unchanged():
    only = _result(tables={"rates": _rows("r", 0, 3)})
    assert merge_window_results([(1, 1, only)]) is only


def test_no_windows_refuses():
    with pytest.raises(ValueError, match="at least one window"):
        merge_window_results([])


def test_rows_concatenate_in_order_without_dedupe():
    dup = {"id": "same"}
    merged = merge_window_results(
        [
            (1, 8, _result(tables={"rates": [*_rows("r", 0, 3), dup]})),
            (
                9,
                16,
                _result(
                    tables={
                        "rates": [dup, *_rows("r", 3, 2)],
                        "design": _rows("d", 0, 2),
                    }
                ),
            ),
            (17, 17, _result(tables={})),
        ]
    )
    rates = merged.fields.tables["rates"]
    assert len(rates) == 7
    assert rates.count(dup) == 2  # a duplicate is visible to review, never dropped
    assert [r["id"] for r in rates if r != dup] == [f"r-{i}" for i in range(5)]
    assert merged.fields.tables["design"] == _rows("d", 0, 2)


def test_scalars_first_wins_and_a_disagreement_is_flagged():
    merged = merge_window_results(
        [
            (1, 8, _result(key_fields={"currency": "USD", "premium": "100.00"})),
            (
                9,
                16,
                _result(
                    key_fields={"currency": "EUR", "plan_year": "2027"},
                    issuer="Other Mutual",
                ),
            ),
            (17, 20, _result(key_fields={"currency": "USD"}, issuer=None)),
        ]
    )
    assert merged.fields.key_fields == {
        "currency": "USD",
        "premium": "100.00",
        "plan_year": "2027",
    }
    assert merged.fields.issuer == "Synthetic Mutual"
    assert merged.flagged_fields[:2] == ["issuer", "currency"]
    by_path = {r.path: r for r in merged.field_reviews}
    currency = by_path["fields.key_fields.currency"]
    assert currency.reason is ReviewReason.model_flagged
    assert currency.detail.startswith("page windows disagree: ")
    assert "pages 1-8 report 'USD'" in currency.detail
    assert "pages 9-16 report 'EUR'" in currency.detail
    assert "pages 9-16 report 'Other Mutual'" in by_path["fields.issuer"].detail
    assert "fields.key_fields.currency" in fields_requiring_review(merged)


def test_doctype_disagreement_is_flagged():
    merged = merge_window_results(
        [
            (1, 2, _result()),
            (3, 4, _result(doctype="utility-bill")),
        ]
    )
    assert merged.fields.doctype == "benefits-quote"
    assert merged.flagged_fields == ["doctype"]
    assert "'utility-bill'" in merged.field_reviews[0].detail


def test_agreeing_windows_flag_nothing():
    merged = merge_window_results([(1, 2, _result()), (3, 4, _result())])
    assert merged.flagged_fields == []
    assert merged.field_reviews == []


def test_confidence_is_min_and_legible_is_all():
    merged = merge_window_results(
        [
            (1, 8, _result(overall=0.95, confidence={"currency": 0.99, "issuer": 0.9})),
            (
                9,
                16,
                _result(
                    overall=0.71,
                    legible=False,
                    confidence={"currency": 0.5, "issuer": 0.8},
                ),
            ),
        ]
    )
    assert merged.overall_confidence == 0.71
    assert merged.legible is False
    # key field: the WINNING window's own number; anything else: the minimum
    assert merged.field_confidence["currency"] == 0.99
    assert merged.field_confidence["issuer"] == 0.8


def test_row_paths_are_rebased_across_windows():
    path = "fields.tables.rates[{}].frequency"
    merged = merge_window_results(
        [
            (
                1,
                8,
                _result(
                    tables={"rates": _rows("r", 0, 40)},
                    flagged=[path.format(2)],
                    confidence={path.format(2): 0.4},
                ),
            ),
            (
                9,
                16,
                _result(
                    tables={"rates": _rows("r", 40, 40)},
                    flagged=[path.format(3)],
                    confidence={path.format(3): 0.41},
                    reviews=[
                        {
                            "path": path.format(3),
                            "reason": "low_confidence",
                            "confidence": 0.41,
                        }
                    ],
                ),
            ),
        ]
    )
    assert merged.flagged_fields == [path.format(2), path.format(43)]
    assert merged.field_confidence[path.format(43)] == 0.41
    assert merged.field_confidence[path.format(2)] == 0.4
    assert [r.path for r in merged.field_reviews] == [path.format(43)]
    assert fields_requiring_review(merged) == [path.format(43), path.format(2)]
    assert merged.fields.tables["rates"][43] == {"id": "r-43"}


def test_an_unemitted_row_index_is_coarsened_with_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="bridge.extraction.chunking")
    merged = merge_window_results(
        [
            (1, 8, _result(tables={"rates": _rows("r", 0, 4)})),
            (
                9,
                16,
                _result(
                    tables={"rates": _rows("r", 4, 2)},
                    flagged=["fields.tables.rates[7].amount"],
                ),
            ),
        ]
    )
    assert merged.flagged_fields == ["fields.tables.rates"]
    assert "pages 9-16" in caplog.text
    assert "coarsened to 'fields.tables.rates'" in caplog.text


def test_reviews_dedupe_on_path_first_wins():
    review = {
        "path": "fields.key_fields.premium",
        "reason": "low_confidence",
        "confidence": 0.3,
    }
    later = {**review, "confidence": 0.2, "detail": "later"}
    merged = merge_window_results(
        [
            (1, 1, _result(reviews=[review])),
            (2, 2, _result(reviews=[later])),
        ]
    )
    assert len(merged.field_reviews) == 1
    assert merged.field_reviews[0].confidence == 0.3


# --------------------------------------------------------------------------- #
# isolation                                                                   #
# --------------------------------------------------------------------------- #


def test_chunking_imports_only_stdlib_and_contract():
    tree = ast.parse(CHUNKING.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    banned = ("google", "agents", "bridge.ports", "bridge.edge", "pypdf", "fitz")
    assert not [n for n in names if n.split(".")[0] in banned or n.startswith(banned)]
    assert {n.split(".")[0] for n in names} <= {
        "__future__",
        "logging",
        "re",
        "zlib",
        "collections",
        "contract",
    }
