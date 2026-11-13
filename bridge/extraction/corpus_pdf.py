"""Stdlib-only deterministic PDF-1.4 renderer for benefits oracle records.

Synthesizes byte-deterministic PDF documents directly from `expected.json` and
`conformance.json` when no binary `.pdf` file is stored on local disk (`Decision 18`).
Imports stdlib only so `bridge/extraction/resolve.py` and `tests/corpus_pdf.py` share
one implementation with zero external dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_RATE_COLUMNS = (
    ("coverage_tier", "Tier "),
    ("tobacco", "Tobacco "),
    ("age_band", "Age "),
    ("amount", ""),
    ("frequency", ""),
    ("rate_basis", ""),
    ("benefit_amount", "Benefit "),
)

LEADING, FONT_SIZE, TITLE_SIZE = 18, 11, 16


def _esc(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
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


def build_multipage_pdf(
    pages: list[tuple[str, list[str]]], *, font_size: int = 9, leading: int = 14
) -> bytes:
    """Assemble an N-page, uncompressed, deterministic PDF 1.4 document."""
    if not pages:
        raise ValueError("build_multipage_pdf needs at least one page")
    n = len(pages)
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


def quote_pdf_title(record: dict[str, Any]) -> str:
    return f"Devis d'assurance collective — {record['party']} (synthétique)"


def quote_pdf_lines(record: dict[str, Any]) -> list[str]:
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


def build_quote_pdf(record: dict[str, Any]) -> bytes:
    return build_pdf(quote_pdf_title(record), quote_pdf_lines(record))


def conformance_pdf_title(record: dict[str, Any]) -> str:
    return f"Voluntary benefits rate quote — {record['party']} (synthetic)"


def _cell(
    record: dict[str, Any], table: str, index: int, column: str, value: str
) -> str:
    return record.get("pdf_overrides", {}).get(f"{table}[{index}].{column}", value)


def conformance_pdf_lines(record: dict[str, Any]) -> list[str]:
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
    lines.append(
        "Rates (tier | tobacco | age band | amount | frequency | basis | benefit):"
    )
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
            original = _cell(
                record, "design", i, "category_original", row["category_original"]
            )
            cells.append(f"Carrier term: {original}")
        cells.append(_cell(record, "design", i, "value", row["value"]))
        lines.append(" | ".join(cells))
    lines.append("Synthetic document — fictitious data, no real personal data.")
    return lines


def build_conformance_pdf(record: dict[str, Any]) -> bytes:
    return build_pdf(conformance_pdf_title(record), conformance_pdf_lines(record))


def resolve_corpus_pdf_bytes(corpus_root: Path, rel_name: str) -> bytes | None:
    """Synthesize bytes for a known benefits oracle PDF when not present on disk."""
    norm = rel_name.replace("\\", "/").lstrip("/")
    expected_file = corpus_root / "expected.json"
    if expected_file.is_file():
        data = json.loads(expected_file.read_text(encoding="utf-8"))
        for record in data.get("documents", []):
            if record.get("artifact") == norm:
                return build_quote_pdf(record)
    conformance_file = corpus_root / "conformance.json"
    if conformance_file.is_file():
        data = json.loads(conformance_file.read_text(encoding="utf-8"))
        for record in data.get("documents", []):
            if record.get("artifact") == norm:
                return build_conformance_pdf(record)
    return None
