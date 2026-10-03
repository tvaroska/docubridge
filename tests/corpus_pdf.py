"""Corpus PDF authoring tool — writes the committed benefits PDF assets (S0-arch-6).

This module **authors** committed corpus assets: the synthetic carrier-quote PDFs in
``wiki/evals/benefits/images/`` — the carrier-C normalize quote (``expected.json``) and
the two consumer-conformance rate sheets (``conformance.json``, S0-arch-9). It lives in ``tests/`` because the corpus oracle's
consumers do (it is a corpus tool, not a Bridge capability — ``bridge/`` must never
depend on it), and it deliberately pulls **no dependency**: the PDF is assembled from
the standard library only (no ``reportlab``/``pypdf``), so the asset's provenance is
this file rather than a binary blob of unknown origin.

The output is **byte-deterministic** — no ``/CreationDate``, no ``/ID``, no
compression, no clock read — so the committed asset can be regenerated
(``uv run python -m tests.corpus_pdf``) and diffed, and
``tests/test_pdf_extraction.py`` asserts byte-equality with the committed file. That
byte-equality is the drift guard: the page can only ever print the values the corpus
record itself declares, because the page is rendered *from* the oracle file
(``tests/conformance/test_benefits_conformance.py`` holds the same guard for the
conformance PDFs).

Content-stream text literals are encoded **cp1252** (the page's Type1 font declares
``/WinAnsiEncoding``) with ``(``, ``)`` and ``\\`` escaped, so the French accents
round-trip through a text extractor.

The rendered document is a **native-digital** PDF (a real text layer). Scanned/raster
PDFs needing OCR are the ``docai`` engine's envelope (the Document AI pre-pass); their
raster twin of this page is ``tests/corpus_scanned_pdf.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

BENEFITS_ROOT = Path(__file__).resolve().parents[1] / "wiki/evals/benefits"
CORPUS_PATH = BENEFITS_ROOT / "expected.json"
CONFORMANCE_PATH = BENEFITS_ROOT / "conformance.json"

# The corpus records that carry a committed PDF artifact. One entry today; the
# renderer is corpus-driven, so adding carrier D later is one more id here.
PDF_DOC_IDS = ("benefits-carrier-c-eur-fr",)

# The consumer-conformance records (S0-arch-9) — every one carries a PDF, and each id
# equals its PDF stem (the fixture/gemini parity pivot).
CONFORMANCE_DOC_IDS = (
    "benefits-conformance-ci-usd",
    "benefits-conformance-hi-usd-flagged",
)

# Rate-row columns in print order, with the label printed before each cell ("" = none).
_RATE_COLUMNS = (
    ("coverage_tier", "Tier "),
    ("tobacco", "Tobacco "),
    ("age_band", "Age "),
    ("amount", ""),
    ("frequency", ""),
    ("rate_basis", ""),
    ("benefit_amount", "Benefit "),
)

# Page geometry / typography (points). Fixed constants — determinism over prettiness.
LEADING, FONT_SIZE, TITLE_SIZE = 18, 11, 16


def _esc(text: str) -> bytes:
    """Encode one line for a PDF content stream (cp1252 == WinAnsiEncoding)."""
    raw = text.encode("cp1252", errors="replace")
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):  # ( ) \  must be escaped
            out.append(0x5C)
        out.append(b)
    return bytes(out)


def _content_stream(title: str, lines: list[str]) -> bytes:
    ops = bytearray()
    ops += b"BT\n/F1 %d Tf\n72 720 Td\n%d TL\n" % (TITLE_SIZE, LEADING)
    ops += b"(" + _esc(title) + b") Tj\n"
    ops += b"/F1 %d Tf\n" % FONT_SIZE
    for line in lines:
        ops += b"T*\n(" + _esc(line) + b") Tj\n"
    ops += b"ET\n"
    return bytes(ops)


def build_pdf(title: str, lines: list[str]) -> bytes:
    """Assemble a one-page, uncompressed, deterministic PDF 1.4 document."""
    content = _content_stream(title, lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")  # binary marker line
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def build_multipage_pdf(
    pages: list[tuple[str, list[str]]], *, font_size: int = 9, leading: int = 14
) -> bytes:
    """Assemble an N-page, uncompressed, deterministic PDF 1.4 document (S0-perf-1).

    One ``(title, lines)`` per page: one content stream + one ``/Page`` each, a root
    ``/Pages`` node with the ``/Kids`` array and ``/Count N``. Same discipline as
    :func:`build_pdf` (stdlib only, no clock, no compression) — the scale harness's
    synthetic rate sheets are generated, never committed. :func:`build_pdf` is left
    untouched (its output is byte-equality-guarded against committed corpus assets).
    """
    if not pages:
        raise ValueError("build_multipage_pdf needs at least one page")
    n = len(pages)
    # 1 catalog, 2 pages root, 3 font, then (page, content) pairs from object 4.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n)).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % n,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]
    for i, (title, lines) in enumerate(pages):
        ops = bytearray()
        ops += b"BT\n/F1 %d Tf\n36 756 Td\n%d TL\n" % (font_size + 3, leading)
        ops += b"(" + _esc(title) + b") Tj\n"
        ops += b"/F1 %d Tf\n" % font_size
        for line in lines:
            ops += b"T*\n(" + _esc(line) + b") Tj\n"
        ops += b"ET\n"
        content = bytes(ops)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * i)
        )
        objects.append(
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def quote_pdf_title(record: dict) -> str:
    """The quote page's title line — French, the document's own language."""
    return f"Devis d'assurance collective — {record['party']} (synthétique)"


def quote_pdf_lines(record: dict) -> list[str]:
    """Render a corpus record's ``key_fields`` as the quote page's body lines.

    Every value comes from the record itself, so the page cannot print anything the
    oracle does not declare. Two rendering rules are load-bearing:

    * **Money is ``EUR 1000.00``** — an ASCII ISO-4217 code, a dot decimal, no
      thousands separator. ``bridge/normalize.py::_convert_money`` strips ``$€£``,
      commas and whitespace, but NOT a currency *code* prefix and NOT a comma decimal
      separator, and a live model echoes what it reads. ``1 000,00 €`` would be a
      rendering choice that breaks the terminal outcome.
    * **Free text is the corpus string byte-for-byte** — ``coverage_limits`` /
      ``exclusions`` are keys in the case-sensitive ``TRANSLATIONS`` table, where
      unmapped text passes through unchanged.

    The explicit ISO-4217 / BCP-47 hint lines are what let a classifying model return
    ``currency=EUR`` / ``language=fr`` from the page alone.
    """
    kf = record["extraction"]["fields"]["key_fields"]
    currency = kf["currency"]
    return [
        f"Assureur / Carrier: {record['party']}",
        f"Devise (ISO 4217) / Currency: {currency}",
        f"Langue (BCP-47) / Language: {kf['language']}",
        f"Prime annuelle / Premium: {currency} {kf['premium']}",
        f"Franchise / Deductible: {currency} {kf['deductible']}",
        f"Couverture / Coverage limits: {kf['coverage_limits']}",
        f"Exclusions: {kf['exclusions']}",
        f"Date d'effet / Effective date: {kf['effective_date']}",
        f"Date d'expiration / Expiry date: {kf['expiry_date']}",
        "Document synthétique — données fictives, aucune donnée personnelle réelle.",
    ]


def build_quote_pdf(record: dict) -> bytes:
    """Build the PDF bytes for one corpus quote record."""
    return build_pdf(quote_pdf_title(record), quote_pdf_lines(record))


def write_quote_pdf(record: dict, dest: Path) -> Path:
    """Write ``record``'s quote PDF to ``dest`` (parents created). Returns ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build_quote_pdf(record))
    return dest


def conformance_pdf_title(record: dict) -> str:
    """The conformance rate sheet's title line (English, the document's language)."""
    return f"Voluntary benefits rate quote — {record['party']} (synthetic)"


def _cell(record: dict, table: str, index: int, column: str, value: str) -> str:
    """A cell as PRINTED: a ``pdf_overrides`` entry (consumer-style path) wins."""
    return record.get("pdf_overrides", {}).get(f"{table}[{index}].{column}", value)


def conformance_pdf_lines(record: dict) -> list[str]:
    """Render a conformance record's key fields and tables as the page's body lines.

    Every value comes from the record, so the page cannot print anything the oracle
    does not declare — except a ``pdf_overrides`` cell, which is how a record says
    "the page prints this cell differently from the oracle value" (e.g. a frequency
    printed as ``per pay period`` that the engine reads as ``BI_WEEKLY`` and flags).

    **Money prints bare** (``12.40``, no currency code): the consumer's money parser
    strips only ``$``, ``,`` and spaces, and a live model echoes what it reads. The
    ISO-4217 / BCP-47 hint lines carry the currency and language instead.
    """
    kf = record["extraction"]["fields"]["key_fields"]
    tables = record["extraction"]["fields"].get("tables", {})
    lines = [
        f"Carrier: {record['party']}",
        f"Currency (ISO 4217): {kf['currency']}",
        f"Language (BCP-47): {kf['language']}",
        f"Product line: {kf['product_line']}",
        f"Carrier product name: {kf['carrier_product_name']}",
        f"Plan year: {kf['plan_year']}",
        f"Effective date: {kf['effective_date']}",
        f"Expiry date: {kf['expiry_date']}",
    ]
    if "commission_basis" in kf:
        lines.append(f"Commission basis: {kf['commission_basis']}")
    if "commission_rate_pct" in kf:
        lines.append(f"Commission rate (%): {kf['commission_rate_pct']}")
    if "commission_notes" in kf:
        lines.append(f"Commission notes: {kf['commission_notes']}")
    lines.append("Rates (tier | tobacco | age band | amount | frequency | basis | benefit):")
    for i, row in enumerate(tables.get("rates", [])):
        cells = [
            f"{label}{_cell(record, 'rates', i, column, row[column])}"
            for column, label in _RATE_COLUMNS
            if column in row
        ]
        lines.append(" | ".join(cells))
    lines.append("Benefit design (provision | carrier term | value):")
    for i, row in enumerate(tables.get("design", [])):
        cells = [_cell(record, "design", i, "category", row["category"])]
        if "category_original" in row:
            original = _cell(record, "design", i, "category_original", row["category_original"])
            cells.append(f"Carrier term: {original}")
        cells.append(_cell(record, "design", i, "value", row["value"]))
        lines.append(" | ".join(cells))
    lines.append("Synthetic document — fictitious data, no real personal data.")
    return lines


def build_conformance_pdf(record: dict) -> bytes:
    """Build the PDF bytes for one conformance record."""
    return build_pdf(conformance_pdf_title(record), conformance_pdf_lines(record))


def write_conformance_pdf(record: dict, dest: Path) -> Path:
    """Write ``record``'s conformance PDF to ``dest`` (parents created)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build_conformance_pdf(record))
    return dest


def load_records(corpus_path: Path = CORPUS_PATH) -> dict[str, dict]:
    """The corpus documents, keyed by id."""
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    return {record["id"]: record for record in data["documents"]}


def main() -> None:
    """Regenerate every committed corpus PDF in place (byte-stable)."""
    records = load_records()
    for doc_id in PDF_DOC_IDS:
        record = records[doc_id]
        dest = BENEFITS_ROOT / record["artifact"]
        write_quote_pdf(record, dest)
        print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    conformance = load_records(CONFORMANCE_PATH)
    for doc_id in CONFORMANCE_DOC_IDS:
        record = conformance[doc_id]
        dest = BENEFITS_ROOT / record["artifact"]
        write_conformance_pdf(record, dest)
        print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
