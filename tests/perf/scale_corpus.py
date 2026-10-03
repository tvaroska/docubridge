"""Deterministic synthetic scale documents for the S0-perf-1 harness — and their oracle.

Every value is invented (no PII): the carrier is "Synthetic Mutual", every page prints
"synthetic" and a "Page k of N" footer. A :class:`ScaleDoc` records what each page
prints — key-field lines, rate rows, design rows — so the same object renders the PDF
(:func:`build_scale_pdf`) AND answers "which rows are on pages a-b" (the fake Gemini
client and the correctness checks read it).

Rate rows print like the conformance sheets (``tests/corpus_pdf.py``):
``Tier EE | Tobacco UNI | Age 30-34 | 12.40 | MONTHLY | FLAT | Benefit 10000`` — money
bare, closed-vocabulary columns printed as their schema value, so a live model can echo
them exactly.

``uv run python -m tests.perf.scale_corpus <outdir>`` writes both PDFs (for manual
``pdftotext`` inspection or a live run). Never into ``wiki/evals/`` (protected).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from tests.corpus_pdf import build_multipage_pdf

DOCTYPE = "benefits-quote"
PARTY = "Synthetic Mutual"

TIERS = ("EE", "EE_SPOUSE", "EE_CHILDREN", "FAMILY")
TOBACCO = ("UNI", "NON_TOBACCO", "TOBACCO")
AGE_BANDS = (
    "18-24", "25-29", "30-34", "35-39", "40-44", "45-49",
    "50-54", "55-59", "60-64", "65-69", "70-74", "75-79",
)
BENEFITS = (5000, 10000, 15000, 20000, 25000, 30000, 50000)
FREQUENCIES = ("MONTHLY", "BI_WEEKLY", "WEEKLY")
COMBOS = len(TIERS) * len(TOBACCO) * len(AGE_BANDS) * len(BENEFITS)  # 1008

PROVISIONS = (
    "Heart Attack", "Stroke", "Cancer", "Kidney Failure", "Major Organ Transplant",
    "Coronary Bypass", "Paralysis", "Coma", "Blindness", "Deafness",
    "Loss of Speech", "Benign Brain Tumor", "Wellness Benefit", "Waiting Period",
    "Pre-existing Condition Limit", "Recurrence Benefit", "Portability",
    "Spouse Coverage", "Child Coverage", "Guaranteed Issue",
)

RATE_HEADER = "Rates (tier | tobacco | age band | amount | frequency | basis | benefit):"
DESIGN_HEADER = "Benefit design (provision | carrier term | value):"


def rate_rows(n: int) -> list[dict[str, str]]:
    """``n`` unique synthetic rate rows: tier x tobacco x age band x benefit amount.

    The first 1008 rows are the distinct combinations; past that the benefit amounts
    shift by 250 per block so every row stays unique (the 50-page quote has 1380).
    ``amount`` is an integer formula printed ``%.2f``; ``frequency`` cycles
    MONTHLY/BI_WEEKLY/WEEKLY; ``rate_basis`` alternates FLAT/PER_1000.
    """
    rows: list[dict[str, str]] = []
    for i in range(n):
        block, j = divmod(i, COMBOS)
        tier, rest = divmod(j, len(TOBACCO) * len(AGE_BANDS) * len(BENEFITS))
        tob, rest = divmod(rest, len(AGE_BANDS) * len(BENEFITS))
        age, ben = divmod(rest, len(BENEFITS))
        cents = 150 + (i * 37) % 9000 + tier * 113 + tob * 41
        rows.append({
            "coverage_tier": TIERS[tier],
            "tobacco": TOBACCO[tob],
            "age_band": AGE_BANDS[age],
            "amount": f"{cents / 100:.2f}",
            "frequency": FREQUENCIES[i % len(FREQUENCIES)],
            "rate_basis": "PER_1000" if i % 2 else "FLAT",
            "benefit_amount": str(BENEFITS[ben] + 250 * block),
        })
    return rows


def design_rows(n: int) -> list[dict[str, str]]:
    """``n`` unique synthetic benefit-design rows."""
    return [
        {
            "category": f"{PROVISIONS[i % len(PROVISIONS)]} (schedule {i // len(PROVISIONS) + 1})",
            "category_original": f"Synthetic term {i + 1}",
            "value": str((i + 1) * 250),
        }
        for i in range(n)
    ]


def rate_line(row: dict[str, str]) -> str:
    return " | ".join([
        f"Tier {row['coverage_tier']}",
        f"Tobacco {row['tobacco']}",
        f"Age {row['age_band']}",
        row["amount"],
        row["frequency"],
        row["rate_basis"],
        f"Benefit {row['benefit_amount']}",
    ])


def design_line(row: dict[str, str]) -> str:
    return f"{row['category']} | Carrier term: {row['category_original']} | {row['value']}"


def key_fields(product_line: str = "CRITICAL_ILLNESS") -> dict[str, str]:
    """Whole-document key fields (Path-A-complete for ``benefits-quote``)."""
    return {
        "currency": "USD",
        "language": "en",
        "premium": "1250.00",
        "deductible": "0.00",
        "coverage_limits": "Lump sum up to the scheduled benefit amount",
        "exclusions": "Self-inflicted injury; war",
        "effective_date": "2027-01-01",
        "expiry_date": "2027-12-31",
        "product_line": product_line,
        "carrier_product_name": "Synthetic CI Plus",
        "plan_year": "2027",
        "commission_basis": "level",
        "commission_rate_pct": "20",
    }


_KEY_LABELS = {
    "currency": "Currency (ISO 4217)",
    "language": "Language (BCP-47)",
    "premium": "Premium",
    "deductible": "Deductible",
    "coverage_limits": "Coverage limits",
    "exclusions": "Exclusions",
    "effective_date": "Effective date",
    "expiry_date": "Expiry date",
    "product_line": "Product line",
    "carrier_product_name": "Carrier product name",
    "plan_year": "Plan year",
    "commission_basis": "Commission basis",
    "commission_rate_pct": "Commission rate (%)",
}


@dataclass(frozen=True)
class PageContent:
    """What one page prints (and therefore what a window over it must return)."""

    title: str
    lines: list[str]
    key_fields: dict[str, str] = field(default_factory=dict)
    rates: list[dict[str, str]] = field(default_factory=list)
    design: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ScaleDoc:
    """A synthetic multi-page quote + its per-page oracle."""

    name: str
    pages: list[PageContent]
    doctype: str = DOCTYPE
    issuer: str = PARTY

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def key_fields(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for page in self.pages:
            merged.update(page.key_fields)
        return merged

    def tables_for(self, first: int, last: int) -> dict[str, list[dict[str, str]]]:
        """The rows printed on pages ``first``-``last`` (1-based, inclusive); no empty tables."""
        chosen = self.pages[first - 1:last]
        out: dict[str, list[dict[str, str]]] = {}
        rates = [r for p in chosen for r in p.rates]
        design = [r for p in chosen for r in p.design]
        if rates:
            out["rates"] = rates
        if design:
            out["design"] = design
        return out

    @property
    def tables(self) -> dict[str, list[dict[str, str]]]:
        return self.tables_for(1, self.total_pages)


def _page(title: str, k: int, total: int, body: list[str], **content) -> PageContent:
    footer = f"Page {k} of {total} - synthetic document, fictitious data, no real personal data."
    return PageContent(title=title, lines=[*body, footer], **content)


def scale_doc(
    name: str,
    title: str,
    rates_per_page: list[int],
    designs_per_page: list[int],
    product_line: str = "CRITICAL_ILLNESS",
) -> ScaleDoc:
    """Page 1 = key fields, then one page per ``rates_per_page`` / ``designs_per_page`` entry."""
    total = 1 + len(rates_per_page) + len(designs_per_page)
    kf = key_fields(product_line)
    pages = [
        _page(title, 1, total, [f"Carrier: {PARTY}"]
              + [f"{_KEY_LABELS[k]}: {v}" for k, v in kf.items()], key_fields=kf)
    ]
    all_rates = rate_rows(sum(rates_per_page))
    at = 0
    for n in rates_per_page:
        chunk = all_rates[at:at + n]
        at += n
        k = len(pages) + 1
        pages.append(_page(title, k, total, [RATE_HEADER, *map(rate_line, chunk)], rates=chunk))
    all_design = design_rows(sum(designs_per_page))
    at = 0
    for n in designs_per_page:
        chunk = all_design[at:at + n]
        at += n
        k = len(pages) + 1
        pages.append(_page(title, k, total, [DESIGN_HEADER, *map(design_line, chunk)],
                           design=chunk))
    return ScaleDoc(name=name, pages=pages)


def rate_sheet_1000(product_line: str = "CRITICAL_ILLNESS") -> ScaleDoc:
    """26 pages: key fields on page 1, then 25 pages x 40 rate rows = 1000 rows."""
    return scale_doc("rate-sheet-1000", f"Voluntary benefits rate sheet - {PARTY} (synthetic)",
                     [40] * 25, [], product_line)


def quote_50_pages(product_line: str = "CRITICAL_ILLNESS") -> ScaleDoc:
    """50 pages: key fields, 46 pages x 30 rate rows (1380), 3 pages x 20 design rows (60)."""
    return scale_doc("quote-50p", f"Voluntary benefits quote - {PARTY} (synthetic)",
                     [30] * 46, [20] * 3, product_line)


def build_scale_pdf(doc: ScaleDoc) -> bytes:
    """Render ``doc`` as a native-digital, uncompressed, deterministic PDF."""
    return build_multipage_pdf([(p.title, p.lines) for p in doc.pages])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m tests.perf.scale_corpus <outdir>", file=sys.stderr)
        return 2
    outdir = Path(args[0]).resolve()
    if "wiki/evals" in outdir.as_posix():
        print("refusing to write into wiki/evals/ (protected corpus)", file=sys.stderr)
        return 2
    outdir.mkdir(parents=True, exist_ok=True)
    for doc in (rate_sheet_1000(), quote_50_pages()):
        dest = outdir / f"{doc.name}.pdf"
        dest.write_bytes(build_scale_pdf(doc))
        print(f"wrote {dest} ({dest.stat().st_size} bytes, {doc.total_pages} pages)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
