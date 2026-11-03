"""Deterministic vector PDF generator for the Commercial Consolidation Loan live showcase.

Builds five multi-page native PDF-1.4 documents with ruled tables, header banners,
searchable Type1 text layers (`Helvetica`, `Helvetica-Bold`, `Courier`), and exact
page/region coordinates so both live multimodal Gemini extraction (`application/pdf`)
and browser PDF viewers (`#page=4`, `#page=2`) render real documents with zero
external font or rendering libraries.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DemoPdfAsset:
    """Metadata and rendered bytes for a showcase PDF document."""

    sample_id: str
    filename: str
    title: str
    subtitle: str
    batch_stage: str
    page_count: int
    highlight_page: int
    pdf_bytes: bytes
    sha256: str


def _escape_pdf_text(text: str) -> str:
    """Escape characters with special meaning inside PDF literal text strings."""
    ascii_clean = (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u00b7", "|")
        .encode("ascii", errors="replace")
        .decode("ascii")
    )
    return ascii_clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _PdfPageCanvas:
    """Minimalist PDF-1.4 page content stream builder (`612 x 792 pt` US Letter)."""

    def __init__(self) -> None:
        self._ops: list[str] = []

    def rect_fill(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rgb: tuple[float, float, float],
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            f"q {r:.3f} {g:.3f} {b:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f Q"
        )

    def rect_stroke(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rgb: tuple[float, float, float] = (0.82, 0.85, 0.89),
        line_width: float = 0.8,
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            f"q {line_width:.2f} w {r:.3f} {g:.3f} {b:.3f} RG "
            f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re S Q"
        )

    def hline(
        self,
        x1: float,
        x2: float,
        y: float,
        rgb: tuple[float, float, float] = (0.85, 0.87, 0.90),
        line_width: float = 0.6,
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            f"q {line_width:.2f} w {r:.3f} {g:.3f} {b:.3f} RG "
            f"{x1:.1f} {y:.1f} m {x2:.1f} {y:.1f} l S Q"
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        font: str = "F1",
        size: float = 10.0,
        rgb: tuple[float, float, float] = (0.12, 0.14, 0.18),
    ) -> None:
        r, g, b = rgb
        escaped = _escape_pdf_text(value)
        self._ops.append(
            f"BT /{font} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg "
            f"{x:.1f} {y:.1f} Td ({escaped}) Tj ET"
        )

    def build_stream(self) -> bytes:
        return ("\n".join(self._ops) + "\n").encode("ascii")


def _assemble_pdf(pages: list[_PdfPageCanvas], *, title: str) -> bytes:
    """Assemble page content streams into a standards-compliant PDF-1.4 byte buffer."""
    # Object layout:
    # 1: Catalog, 2: Pages, 3: Helvetica (F1), 4: Helvetica-Bold (F2), 5: Courier (F3),
    # 6: Info, then pairs of (Page, ContentStream) starting at object 7.
    objects: list[bytes] = []
    page_obj_ids: list[int] = []
    next_id = 7
    for _ in pages:
        page_obj_ids.append(next_id)
        next_id += 2

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids_str}] /Count {len(pages)} >>".encode("ascii")
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    objects.append(f"<< /Title ({_escape_pdf_text(title)}) >>".encode("ascii"))

    for idx, canvas in enumerate(pages):
        page_id = page_obj_ids[idx]
        stream_id = page_id + 1
        stream_bytes = canvas.build_stream()
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
            f"/Contents {stream_id} 0 R >>"
        ).encode("ascii")
        stream_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"endstream"
        )
        objects.append(page_dict)
        objects.append(stream_obj)

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for obj_num, body in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{obj_num} 0 obj\n".encode("ascii"))
        buf.write(body)
        buf.write(b"\nendobj\n")

    xref_pos = buf.tell()
    total_objs = len(objects) + 1
    buf.write(f"xref\n0 {total_objs}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {total_objs} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return buf.getvalue()


def _draw_chrome(
    c: _PdfPageCanvas,
    *,
    issuer_banner: str,
    doc_title: str,
    doc_ref: str,
    page_num: int,
    total_pages: int,
    accent_rgb: tuple[float, float, float] = (0.10, 0.45, 0.91),
) -> None:
    """Draw consistent institutional header bar and page footer."""
    c.rect_fill(0, 734, 612, 58, accent_rgb)
    c.text(44, 766, issuer_banner, font="F2", size=11.5, rgb=(1.0, 1.0, 1.0))
    c.text(44, 747, doc_title, font="F1", size=9.5, rgb=(0.90, 0.95, 1.0))
    c.text(430, 766, doc_ref, font="F3", size=8.5, rgb=(1.0, 1.0, 1.0))
    c.hline(44, 568, 46, rgb=(0.82, 0.85, 0.89))
    c.text(
        44,
        30,
        f"CONFIDENTIAL COMMERCIAL CREDIT RECORD  |  {doc_ref}",
        font="F1",
        size=8.0,
        rgb=(0.42, 0.45, 0.50),
    )
    c.text(
        492,
        30,
        f"Page {page_num} of {total_pages}",
        font="F2",
        size=8.5,
        rgb=(0.25, 0.28, 0.33),
    )


def _build_meridian_financials_pdf() -> bytes:
    """1. `01_Meridian_2025_Audited_Financials.pdf` (4 pages; Note 6 Debt Schedule on Page 4)."""
    pages: list[_PdfPageCanvas] = []
    ref = "DOC-MERIDIAN-FIN-2025"

    # Page 1: Auditor Cover + Borrower Profile + Synthetic PII for Edge DLP Scrubbing
    p1 = _PdfPageCanvas()
    _draw_chrome(
        p1,
        issuer_banner="GRANT & STERLING LLP  |  INDEPENDENT AUDITOR'S REPORT",
        doc_title="Meridian Logistics Center LLC - FY 2025 Audited Financial Statements",
        doc_ref=ref,
        page_num=1,
        total_pages=4,
        accent_rgb=(0.08, 0.22, 0.42),
    )
    p1.text(44, 696, "BORROWER ENTITY & AUDIT OPINION SUMMARY", font="F2", size=13)
    p1.rect_fill(44, 608, 524, 74, (0.96, 0.97, 0.99))
    p1.rect_stroke(44, 608, 524, 74)
    p1.text(
        58, 662, "Legal Borrower: Meridian Logistics Center LLC", font="F2", size=10.5
    )
    p1.text(
        58,
        645,
        "Collateral Asset: 18,500 m2 Class-A Industrial Distribution Facility (Chicago, IL)",
        font="F1",
        size=9.5,
    )
    p1.text(
        58,
        628,
        "Audit Period Ended: December 31, 2025  |  Opinion: Unqualified (Clean GAAP Opinion)",
        font="F1",
        size=9.5,
    )
    p1.text(
        58,
        614,
        "Controller Contact: Jane Doe  |  Direct Phone: 312-555-0194  |  Tax/SSN: 999-11-2222",
        font="F3",
        size=8.5,
        rgb=(0.35, 0.38, 0.42),
    )
    p1.text(44, 574, "Independent Auditor's Opinion", font="F2", size=11)
    opinion_lines = [
        "We have audited the consolidated balance sheet of Meridian Logistics Center LLC as of",
        "December 31, 2025, and the related statements of operations, cash flows, and Note 6",
        "Schedule of Existing Debt Obligations. In our opinion, the financial statements present",
        "fairly, in all material respects, the financial position of Meridian Logistics Center LLC.",
        "",
        "Key Credit Highlights (FY 2025 Audited):",
        "  * Stabilized Net Operating Income (NOI / CFADS): $312,000.00 USD",
        "  * Proposed Consolidated Debt Facility Requested: $1,800,000.00 USD",
        "  * Proposed Annual Consolidated Debt Service:     $216,666.67 USD",
        "  * Complete Existing Creditor Breakdown:          See Page 4, Note 6 Debt Schedule",
    ]
    y = 552
    for ln in opinion_lines:
        p1.text(44, y, ln, font="F1", size=9.8)
        y -= 18
    pages.append(p1)

    # Page 2: Consolidated Balance Sheet
    p2 = _PdfPageCanvas()
    _draw_chrome(
        p2,
        issuer_banner="MERIDIAN LOGISTICS CENTER LLC  |  CONSOLIDATED BALANCE SHEET",
        doc_title="As of December 31, 2025 (Amounts in USD)",
        doc_ref=ref,
        page_num=2,
        total_pages=4,
        accent_rgb=(0.08, 0.22, 0.42),
    )
    p2.text(
        44, 694, "CONSOLIDATED BALANCE SHEET (AS OF 2025-12-31)", font="F2", size=12.5
    )
    bs_rows = [
        ("ASSETS", ""),
        ("Operating Cash & Liquidity Reserves", "$412,500.00"),
        ("Accounts Receivable (Net Logistics Leases)", "$195,000.00"),
        ("Industrial Land, Warehouse & Dock Improvements (Net)", "$2,812,500.00"),
        ("TOTAL ASSETS", "$3,420,000.00"),
        ("LIABILITIES & MEMBERS' EQUITY", ""),
        ("Current Trade Payables & Accrued Property Taxes", "$0.00"),
        (
            "Existing Debt Facilities Scheduled for Consolidation (See Note 6, Page 4)",
            "$1,800,000.00",
        ),
        ("Members' Paid-In Capital & Retained Earnings", "$1,620,000.00"),
        ("TOTAL LIABILITIES & MEMBERS' EQUITY", "$3,420,000.00"),
    ]
    y = 660
    for label, amt in bs_rows:
        is_hdr = amt == "" or label.startswith("TOTAL")
        if is_hdr:
            p2.rect_fill(44, y - 6, 524, 22, (0.93, 0.95, 0.98))
        p2.text(52, y, label, font="F2" if is_hdr else "F1", size=9.8)
        if amt:
            p2.text(460, y, amt, font="F2" if is_hdr else "F3", size=9.8)
        p2.hline(44, 568, y - 8)
        y -= 28
    pages.append(p2)

    # Page 3: Consolidated Cash Flows & NOI
    p3 = _PdfPageCanvas()
    _draw_chrome(
        p3,
        issuer_banner="MERIDIAN LOGISTICS CENTER LLC  |  STATEMENT OF OPERATIONS & NOI",
        doc_title="FY 2025 Audited Net Operating Income & Debt Service Capacity",
        doc_ref=ref,
        page_num=3,
        total_pages=4,
        accent_rgb=(0.08, 0.22, 0.42),
    )
    p3.text(
        44, 694, "AUDITED OPERATING CASH FLOW & COVERAGE METRICS", font="F2", size=12.5
    )
    cf_rows = [
        ("Gross Industrial Warehouse Lease Revenue", "$684,000.00"),
        ("Less: Property Taxes, Insurance & Common Area Maintenance", "($214,000.00)"),
        ("Less: Facility Management & Utilities", "($158,000.00)"),
        ("STABILIZED NET OPERATING INCOME (ANNUAL NOI / CFADS)", "$312,000.00"),
        ("Proposed Consolidated Annual Principal & Interest Service", "$216,666.67"),
    ]
    y = 656
    for label, amt in cf_rows:
        bold = "STABILIZED" in label
        if bold:
            p3.rect_fill(44, y - 6, 524, 24, (0.90, 0.96, 0.92))
        p3.text(52, y, label, font="F2" if bold else "F1", size=10.0)
        p3.text(456, y, amt, font="F2" if bold else "F3", size=10.0)
        p3.hline(44, 568, y - 8)
        y -= 32
    pages.append(p3)

    # Page 4: Note 6 — Schedule of Existing Debt Obligations to be Consolidated
    # Placed in normalized vertical band [0.38..0.56] from top (PDF y ~ 340..500)
    p4 = _PdfPageCanvas()
    _draw_chrome(
        p4,
        issuer_banner="MERIDIAN LOGISTICS CENTER LLC  |  NOTES TO FINANCIAL STATEMENTS",
        doc_title="Note 6 - Schedule of Existing Liabilities to be Consolidated",
        doc_ref=ref,
        page_num=4,
        total_pages=4,
        accent_rgb=(0.08, 0.22, 0.42),
    )
    p4.text(
        44,
        694,
        "NOTE 6 - SCHEDULE OF EXISTING LIABILITIES TO BE CONSOLIDATED",
        font="F2",
        size=12.0,
    )
    p4.text(
        44,
        674,
        "Borrower requests a $1,800,000.00 Commercial Debt Consolidation Facility to retire",
        font="F1",
        size=9.5,
    )
    p4.text(
        44,
        658,
        "the following three (3) existing credit obligations recorded as of December 31, 2025:",
        font="F1",
        size=9.5,
    )

    # Highlighted Note 6 Table Box (PDF y = 345..520 -> normalized top y = 0.34..0.56)
    p4.rect_fill(44, 345, 524, 180, (0.96, 0.98, 1.0))
    p4.rect_stroke(44, 345, 524, 180, rgb=(0.10, 0.45, 0.91), line_width=1.4)
    p4.rect_fill(44, 499, 524, 26, (0.10, 0.45, 0.91))
    p4.text(52, 508, "CREDITOR / INSTITUTION", font="F2", size=8.8, rgb=(1, 1, 1))
    p4.text(225, 508, "ACCOUNT REF", font="F2", size=8.8, rgb=(1, 1, 1))
    p4.text(312, 508, "FACILITY TYPE", font="F2", size=8.8, rgb=(1, 1, 1))
    p4.text(476, 508, "BALANCE (USD)", font="F2", size=8.8, rgb=(1, 1, 1))

    liab_rows = [
        (
            "1. First National Bank",
            "#FNB-4402",
            "Senior Commercial Mortgage",
            "$1,200,000.00",
            "External Lender - Official Payoff Statement Required at Closing",
        ),
        (
            "2. EquipLease Financial Corp",
            "#8841",
            "Fleet & Material Term Loan",
            "$350,000.00",
            "External Lender - Balance as of 2025-12-31 (Subject to 2026 Paydown)",
        ),
        (
            "3. DocuBridge Commercial Bank",
            "#LOC-9012",
            "Internal Working Capital LOC",
            "$250,000.00",
            "On-Us Internal Facility - Verified Directly via Core Banking Ledger",
        ),
    ]
    ry = 476
    for cred, acct, ftype, bal, subnote in liab_rows:
        p4.text(52, ry, cred, font="F2", size=9.5)
        p4.text(225, ry, acct, font="F3", size=9.5, rgb=(0.08, 0.30, 0.68))
        p4.text(312, ry, ftype, font="F1", size=9.0)
        p4.text(476, ry, bal, font="F3", size=9.5)
        p4.text(52, ry - 13, subnote, font="F1", size=8.0, rgb=(0.38, 0.42, 0.48))
        p4.hline(50, 562, ry - 20)
        ry -= 40

    p4.rect_fill(44, 345, 524, 26, (0.90, 0.94, 0.99))
    p4.text(
        52,
        354,
        "TOTAL EXISTING OBLIGATIONS TO CONSOLIDATE (3 FACILITIES)",
        font="F2",
        size=9.5,
    )
    p4.text(472, 354, "$1,800,000.00", font="F2", size=10.0, rgb=(0.08, 0.30, 0.68))
    pages.append(p4)

    return _assemble_pdf(
        pages,
        title="Meridian Logistics Center LLC - 2025 Audited Financials & Note 6 Debt Schedule",
    )


def _build_meridian_appraisal_pdf() -> bytes:
    """2. `02_Meridian_MAI_Appraisal.pdf` (2 pages; $2.60M Valuation & $312K NOI on Page 1)."""
    pages: list[_PdfPageCanvas] = []
    ref = "DOC-MERIDIAN-MAI-2026"

    p1 = _PdfPageCanvas()
    _draw_chrome(
        p1,
        issuer_banner="CUSHMAN & INDUSTRIAL VALUATION PARTNERS  |  MAI APPRAISAL",
        doc_title="As-Is Commercial Real Estate Appraisal - Meridian Logistics Center",
        doc_ref=ref,
        page_num=1,
        total_pages=2,
        accent_rgb=(0.09, 0.44, 0.31),
    )
    p1.text(
        44, 694, "EXECUTIVE VALUATION SUMMARY (MAI CERTIFIED)", font="F2", size=12.5
    )
    # Valuation summary box at normalized [0.14, 0.22, 0.82, 0.36] (PDF y ~ 505..618)
    p1.rect_fill(44, 505, 524, 168, (0.95, 0.99, 0.96))
    p1.rect_stroke(44, 505, 524, 168, rgb=(0.09, 0.52, 0.35), line_width=1.4)
    p1.text(
        58,
        650,
        "Subject Property: Meridian Logistics Center (18,500 m2 Class-A Industrial)",
        font="F2",
        size=10.2,
    )
    p1.text(
        58, 628, "Borrower / Owner: Meridian Logistics Center LLC", font="F1", size=9.8
    )
    p1.text(58, 602, "AS-IS APPRAISED MARKET VALUE:", font="F2", size=11.0)
    p1.text(370, 602, "$2,600,000.00 USD", font="F2", size=12.0, rgb=(0.07, 0.45, 0.26))
    p1.text(
        58, 578, "STABILIZED ANNUAL NET OPERATING INCOME (NOI):", font="F2", size=10.2
    )
    p1.text(370, 578, "$312,000.00 USD", font="F3", size=10.8)
    p1.text(58, 554, "PROPOSED CONSOLIDATED LOAN FACILITY:", font="F1", size=9.8)
    p1.text(370, 554, "$1,800,000.00 USD", font="F3", size=10.2)
    p1.text(
        58,
        530,
        "Effective Valuation Date: October 15, 2026  |  Designation: MAI #44819",
        font="F1",
        size=9.0,
    )
    pages.append(p1)

    p2 = _PdfPageCanvas()
    _draw_chrome(
        p2,
        issuer_banner="CUSHMAN & INDUSTRIAL VALUATION PARTNERS  |  INCOME RECONCILIATION",
        doc_title="Direct Capitalization & Comparable Industrial Warehouse Sales",
        doc_ref=ref,
        page_num=2,
        total_pages=2,
        accent_rgb=(0.09, 0.44, 0.31),
    )
    p2.text(44, 694, "INCOME CAPITALIZATION RECONCILIATION", font="F2", size=12.0)
    p2.text(
        44,
        664,
        "Stabilized Annual NOI: $312,000.00 / Market Cap Rate 12.00% = $2,600,000.00",
        font="F1",
        size=10.0,
    )
    p2.text(
        44,
        640,
        "Sales Comparison Approach Support: $2,615,000.00 (3 Industrial Comparables)",
        font="F1",
        size=10.0,
    )
    p2.text(
        44,
        616,
        "Final Concluded As-Is Collateral Value: $2,600,000.00 USD",
        font="F2",
        size=10.5,
    )
    pages.append(p2)

    return _assemble_pdf(
        pages, title="Meridian Logistics Center LLC - MAI Commercial Property Appraisal"
    )


def _build_fnb_payoff_pdf() -> bytes:
    """3. `03_First_National_Payoff_FNB4402.pdf` (1 page; $1,200,000.00 Official Payoff Letter)."""
    p1 = _PdfPageCanvas()
    ref = "DOC-FNB-PAYOFF-4402"
    _draw_chrome(
        p1,
        issuer_banner="FIRST NATIONAL BANK  |  COMMERCIAL LOAN SERVICING",
        doc_title="Official Commercial Mortgage Payoff Demand Statement",
        doc_ref=ref,
        page_num=1,
        total_pages=1,
        accent_rgb=(0.15, 0.32, 0.58),
    )
    p1.text(
        44,
        694,
        "OFFICIAL PAYOFF STATEMENT - LOAN ACCOUNT #FNB-4402",
        font="F2",
        size=12.0,
    )
    p1.rect_fill(44, 480, 524, 190, (0.96, 0.98, 1.0))
    p1.rect_stroke(44, 480, 524, 190, rgb=(0.15, 0.32, 0.58), line_width=1.3)
    p1.text(58, 644, "Borrower: Meridian Logistics Center LLC", font="F2", size=10.5)
    p1.text(
        58, 622, "Creditor / Issuing Lender: First National Bank", font="F1", size=10.0
    )
    p1.text(
        58,
        600,
        "Loan Account Reference: #FNB-4402 (Senior Commercial Mortgage)",
        font="F3",
        size=10.0,
    )
    p1.text(
        58,
        570,
        "Principal Balance Remaining:          $1,194,200.00 USD",
        font="F3",
        size=10.0,
    )
    p1.text(
        58,
        550,
        "Accrued Interest & Release Recording:     $5,800.00 USD",
        font="F3",
        size=10.0,
    )
    p1.hline(58, 550, 538)
    p1.text(58, 518, "TOTAL OFFICIAL PAYOFF AMOUNT:", font="F2", size=11.0)
    p1.text(350, 518, "$1,200,000.00 USD", font="F2", size=12.0, rgb=(0.10, 0.35, 0.72))
    p1.text(
        58,
        494,
        "Payoff Quote Good Through: November 15, 2026  |  Fedwire ABA: 071000013",
        font="F1",
        size=9.2,
    )
    return _assemble_pdf(
        [p1], title="First National Bank - Official Payoff Statement #FNB-4402"
    )


def _build_meridian_bank_statement_pdf() -> bytes:
    """4. `04_Meridian_Oct2026_Bank_Statement.pdf` (2 pages; Page 2 $48,210.00 PAID IN FULL Wire)."""
    pages: list[_PdfPageCanvas] = []
    ref = "DOC-MERIDIAN-BANK-OCT2026"

    p1 = _PdfPageCanvas()
    _draw_chrome(
        p1,
        issuer_banner="METRO COMMERCIAL TREASURY BANK  |  OPERATING ACCOUNT STATEMENT",
        doc_title="Meridian Logistics Center LLC - Statement Period: 2026-10-01 to 2026-10-31",
        doc_ref=ref,
        page_num=1,
        total_pages=2,
        accent_rgb=(0.12, 0.29, 0.49),
    )
    p1.text(
        44,
        694,
        "COMMERCIAL OPERATING ACCOUNT SUMMARY (OCTOBER 2026)",
        font="F2",
        size=12.0,
    )
    p1.rect_fill(44, 578, 524, 98, (0.96, 0.97, 0.99))
    p1.rect_stroke(44, 578, 524, 98)
    p1.text(
        58,
        654,
        "Account Holder: Meridian Logistics Center LLC  |  Acct: ****-7741",
        font="F2",
        size=10.2,
    )
    p1.text(
        58, 632, "Opening Balance (2026-10-01):    $418,920.00 USD", font="F3", size=9.8
    )
    p1.text(
        58,
        612,
        "Total Operating Lease Credits:    +$96,400.00 USD",
        font="F3",
        size=9.8,
    )
    p1.text(
        58,
        592,
        "Ending Liquidity (2026-10-31):   $394,110.00 USD (See Page 2 Wire Schedule)",
        font="F2",
        size=9.8,
    )
    pages.append(p1)

    p2 = _PdfPageCanvas()
    _draw_chrome(
        p2,
        issuer_banner="METRO COMMERCIAL TREASURY BANK  |  WIRE & ACH DISBURSEMENT LEDGER",
        doc_title="Meridian Logistics Center LLC - Outgoing Wires & Debits (Page 2 of 2)",
        doc_ref=ref,
        page_num=2,
        total_pages=2,
        accent_rgb=(0.12, 0.29, 0.49),
    )
    p2.text(
        44, 694, "OCTOBER 2026 OUTGOING WIRE & ACH TRANSACTIONS", font="F2", size=12.0
    )
    p2.text(
        44,
        666,
        "2026-10-04  |  ACH DEBIT  |  MIDWEST INDUSTRIAL UTILITIES       |  $4,120.00",
        font="F3",
        size=9.0,
    )
    p2.text(
        44,
        642,
        "2026-10-08  |  ACH DEBIT  |  COOK COUNTY PROPERTY TAX ESCROW    |  $14,880.00",
        font="F3",
        size=9.0,
    )

    # Highlighted Outgoing Fedwire Row at normalized [0.16, 0.54, 0.88, 0.63] (PDF y = 290..368)
    p2.rect_fill(44, 292, 524, 78, (1.0, 0.97, 0.88))
    p2.rect_stroke(44, 292, 524, 78, rgb=(0.89, 0.45, 0.0), line_width=1.6)
    p2.text(
        54,
        350,
        "2026-10-12  |  OUTGOING FEDWIRE #99481204  |  AMOUNT: $48,210.00 USD",
        font="F2",
        size=10.2,
        rgb=(0.55, 0.24, 0.0),
    )
    p2.text(
        54,
        330,
        "BENEFICIARY: EQUIPLEASE FINANCIAL CORP  |  LOAN ACCOUNT: #8841",
        font="F2",
        size=9.8,
    )
    p2.text(
        54,
        308,
        "OBI / WIRE MEMO: FINAL PAYOFF - LOAN #8841 - PAID IN FULL (ZERO BALANCE)",
        font="F3",
        size=9.2,
        rgb=(0.08, 0.42, 0.22),
    )

    p2.text(
        44,
        256,
        "2026-10-19  |  ACH DEBIT  |  GREAT LAKES COMMERCIAL INSURANCE   |  $6,880.00",
        font="F3",
        size=9.0,
    )
    pages.append(p2)

    return _assemble_pdf(
        pages, title="Meridian Logistics Center LLC - Oct 2026 Operating Bank Statement"
    )


def _build_apex_day2_bank_statement_pdf() -> bytes:
    """5. `05_Apex_Precision_Day2_Bank_Statement.pdf` (2 pages; Day-2 $62,400.00 #9914 Wire)."""
    pages: list[_PdfPageCanvas] = []
    ref = "DOC-APEX-DAY2-OCT2026"

    p1 = _PdfPageCanvas()
    _draw_chrome(
        p1,
        issuer_banner="APEX PRECISION MANUFACTURING LLC  |  CONSOLIDATION PACKAGE (DAY 2)",
        doc_title="$1,450,000.00 Commercial Debt Consolidation & Oct 2026 Operating Statement",
        doc_ref=ref,
        page_num=1,
        total_pages=2,
        accent_rgb=(0.32, 0.16, 0.56),
    )
    p1.text(
        44,
        694,
        "DAY-2 BORROWER SUBMISSION: APEX PRECISION MANUFACTURING LLC",
        font="F2",
        size=11.5,
    )
    p1.rect_fill(44, 566, 524, 108, (0.97, 0.96, 1.0))
    p1.rect_stroke(44, 566, 524, 108, rgb=(0.45, 0.22, 0.78))
    p1.text(
        58,
        650,
        "Borrower: Apex Precision Manufacturing LLC (Entity ID: LOAN-APEX-CONSOL-DAY2)",
        font="F2",
        size=9.8,
    )
    p1.text(
        58,
        630,
        "Requested Consolidation Facility: $1,450,000.00 USD",
        font="F2",
        size=10.0,
    )
    p1.text(
        58,
        608,
        "Prior-Year Debt Schedule Liability: EquipLease Financial Corp Loan #9914",
        font="F1",
        size=9.5,
    )
    p1.text(
        58,
        586,
        "See Page 2 Outgoing Fedwire Ledger for Full Settlement Proof of Loan #9914.",
        font="F1",
        size=9.5,
    )
    pages.append(p1)

    p2 = _PdfPageCanvas()
    _draw_chrome(
        p2,
        issuer_banner="APEX PRECISION MANUFACTURING LLC  |  OCTOBER 2026 WIRE LEDGER",
        doc_title="Operating Bank Statement - Outgoing Settlement Wires (Page 2 of 2)",
        doc_ref=ref,
        page_num=2,
        total_pages=2,
        accent_rgb=(0.32, 0.16, 0.56),
    )
    p2.text(44, 694, "OCTOBER 2026 OUTGOING FEDWIRE SETTLEMENTS", font="F2", size=12.0)
    p2.rect_fill(44, 304, 524, 78, (0.94, 0.99, 0.95))
    p2.rect_stroke(44, 304, 524, 78, rgb=(0.09, 0.52, 0.32), line_width=1.6)
    p2.text(
        54,
        362,
        "2026-10-24  |  OUTGOING FEDWIRE #99810442  |  AMOUNT: $62,400.00 USD",
        font="F2",
        size=10.2,
        rgb=(0.07, 0.42, 0.24),
    )
    p2.text(
        54,
        342,
        "BENEFICIARY: EQUIPLEASE FINANCIAL CORP  |  LOAN ACCOUNT: #9914",
        font="F2",
        size=9.8,
    )
    p2.text(
        54,
        320,
        "OBI / WIRE MEMO: FINAL PAYOFF - LOAN #9914 - PAID IN FULL (ZERO BALANCE)",
        font="F3",
        size=9.2,
        rgb=(0.07, 0.42, 0.24),
    )
    pages.append(p2)

    return _assemble_pdf(
        pages,
        title="Apex Precision Manufacturing LLC - Day-2 Oct 2026 Operating Bank Statement",
    )


_CACHED_ASSETS: dict[str, DemoPdfAsset] | None = None


def get_demo_pdf_catalog() -> dict[str, DemoPdfAsset]:
    """Build and memoize all five multi-page showcase PDFs."""
    global _CACHED_ASSETS
    if _CACHED_ASSETS is not None:
        return _CACHED_ASSETS

    specs = [
        (
            "meridian-financials",
            "01_Meridian_2025_Audited_Financials.pdf",
            "2025 Audited Financials & Note 6 Debt Schedule",
            "4 pages · Page 4 lists 3 existing creditors ($1.80M) + Page 1 PII",
            "batch_1",
            4,
            4,
            _build_meridian_financials_pdf(),
        ),
        (
            "meridian-appraisal",
            "02_Meridian_MAI_Appraisal.pdf",
            "MAI Commercial Property Appraisal ($2.60M)",
            "2 pages · Page 1 certifies $2,600,000.00 value & $312,000.00 NOI",
            "batch_1",
            2,
            1,
            _build_meridian_appraisal_pdf(),
        ),
        (
            "fnb-payoff",
            "03_First_National_Payoff_FNB4402.pdf",
            "First National Bank Payoff Statement (#FNB-4402)",
            "1 page · Official $1,200,000.00 senior mortgage payoff demand",
            "batch_2",
            1,
            1,
            _build_fnb_payoff_pdf(),
        ),
        (
            "meridian-bank-stmt",
            "04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
            "Oct 2026 Operating Bank Statement",
            "2 pages · Page 2 contains Oct 12 $48,210.00 'PAID IN FULL' wire (#8841)",
            "batch_2",
            2,
            2,
            _build_meridian_bank_statement_pdf(),
        ),
        (
            "apex-day2-bank-stmt",
            "05_Apex_Precision_Day2_Bank_Statement.pdf",
            "Day-2 Deal: Apex Precision Bank Statement (#9914)",
            "2 pages · Page 2 contains $62,400.00 'PAID IN FULL' wire for zero-touch test",
            "day_2",
            2,
            2,
            _build_apex_day2_bank_statement_pdf(),
        ),
    ]
    catalog: dict[str, DemoPdfAsset] = {}
    for sid, fname, title, sub, stage, pcount, hpage, raw in specs:
        digest = hashlib.sha256(raw).hexdigest()
        catalog[sid] = DemoPdfAsset(
            sample_id=sid,
            filename=fname,
            title=title,
            subtitle=sub,
            batch_stage=stage,
            page_count=pcount,
            highlight_page=hpage,
            pdf_bytes=raw,
            sha256=digest,
        )
    _CACHED_ASSETS = catalog
    return catalog


def resolve_demo_pdf_by_name_or_id(identifier: str) -> DemoPdfAsset | None:
    """Look up a showcase PDF asset by `sample_id` or `filename`."""
    cleaned = identifier.strip()
    catalog = get_demo_pdf_catalog()
    if cleaned in catalog:
        return catalog[cleaned]
    for asset in catalog.values():
        if asset.filename.lower() == cleaned.lower():
            return asset
    return None


def list_demo_pdf_metadata() -> list[dict[str, Any]]:
    """Return JSON-serializable metadata for all five showcase PDFs."""
    return [
        {
            "sample_id": a.sample_id,
            "filename": a.filename,
            "title": a.title,
            "subtitle": a.subtitle,
            "batch_stage": a.batch_stage,
            "page_count": a.page_count,
            "highlight_page": a.highlight_page,
            "size_bytes": len(a.pdf_bytes),
            "sha256": a.sha256,
            "pdf_url": f"/v1/demo/consolidation-collab/pdfs/{a.filename}",
        }
        for a in get_demo_pdf_catalog().values()
    ]


def build_demo_pdfs_zip() -> bytes:
    """Bundle all five showcase PDFs into a single `.zip` archive for download."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for asset in get_demo_pdf_catalog().values():
            zf.writestr(asset.filename, asset.pdf_bytes)
    return buf.getvalue()
