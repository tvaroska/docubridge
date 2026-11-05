---
type: eval
related:
  - "../../../spec/demos/benefits-demo.md"
tags: [bridge, eval]
---

# Eval — Benefits

The Negotiate arc — fan-out to multiple carriers, normalize (FX, multilingual), compare, negotiate/revise, bind, program rollup. See [Benefits demo](../../../spec/demos/benefits-demo.md) for the design narrative. This corpus is the **oracle** for normalize (beat 3), the standard-gap disposition (beat 4, each record's `expected_gaps`) and the revise loop (beat 5, Carrier B's revised `-v2` quote). The comparison view and the CUJ-3 / CUJ-11 legs derive from it. **Evals are not skills** — runtime config lives in `skills/`.

## Use-case — normalize (beat 3)

Four carrier quotes, plus Carrier B's revised (version-2) quote, arrive in their own native currency + language (structured JSON and PDF intake) — 5 records. The normalize layer (`bridge/normalize.py`) collapses them to **one schema / one currency / one language** so the downstream comparison compares like with like. FX is a fixed rate table (USD 1.00, EUR 1.10, GBP 1.25); translation is a fixed lookup for the synthetic fr/de free-text. Each document's `expected_normalized` block is the canonical `NormalizedQuote` and is asserted by `tests/test_normalize.py` (the beat-3 "one schema/currency/language" proof).

`currency` / `language` ride inside `key_fields` (Option A — the extraction wire carries no first-class currency/language; see DECISIONS.md).

## Example inputs + expected outputs

| id | doctype | native currency/language | expected normalized outcome | what it proves |
|---|---|---|---|---|
| `benefits-carrier-a-usd-en` | benefits-quote | USD / en | premium 1200.00, deductible 600.00 (unchanged), text unchanged | idempotency — FX + translation no-op |
| `benefits-carrier-b-usd-en` | benefits-quote | USD / en | premium 1500.00, deductible 750.00 (unchanged), text unchanged | second USD/en carrier normalizes to itself |
| `benefits-carrier-c-eur-fr` | benefits-quote | EUR / fr | premium 1100.00, deductible 550.00 (×1.10), fr→en text | FX EUR→USD + fr translation; arrives as a **native-digital PDF** (`images/benefits-carrier-c-eur-fr.pdf`) — the gemini leg reads those bytes |
| `benefits-carrier-d-gbp-de` | benefits-quote | GBP / de | premium 1000.00, deductible 500.00 (×1.25), de→en text | FX GBP→USD + de translation |
| `benefits-carrier-b-usd-en-v2` | benefits-quote | USD / en | premium 1500.00, deductible 750.00 (unchanged), text unchanged; exclusions drop orthodontics; `expected_gaps` [] | the revise loop: Carrier B's version-2 quote closes v0's only gap (`no_orthodontics_exclusion`) and recomputes conformant |

`expected_gaps` is `[]` for every record except `benefits-carrier-b-usd-en` (`no_orthodontics_exclusion`, under the default `skills/benefits-procurement/assets/standard.yaml`).

## Use-case: consumer conformance (S0-arch-9)

A carrier rate sheet enters over the A2A edge **by reference** (a loopback `http://` URL or a `gs://` object), is extracted against the installed `benefits-quote` skill schema, lands on the `ExchangeTurn` with per-field confidence and review flags, and is mapped onto a VB consumer's own `BenefitsQuote`. The fixture leg and the replayed gemini leg must reach the same terminal outcome (`tests/conformance/`, `uv run python -m tests.conformance.journey`).

These records live in a **separate file**, `conformance.json`, not in `expected.json`. `expected.json`'s five-record count is load-bearing: the normalize/standard tests assert it, and the comparison and CUJ-3/CUJ-11 legs are derived from it. `conformance.json` has the same `{"documents": [...]}` shape, so `FixtureExtractor(corpus_path=...)` reads it unchanged. Each record's `id` equals its PDF stem, which is the parity pivot: both reference shapes key to the same fixture record. Each record also carries a `consumer_intake` block, the context the document never carries (client, program, incumbency). `expected_consumer_quote` is the mapped quote minus `source`, because the uri differs per leg and per reference shape.

| id | product line | rates | expected terminal outcome | what it proves |
|---|---|---|---|---|
| `benefits-conformance-ci-usd` | CRITICAL_ILLNESS, USD / en | 4 FLAT rows mixing MONTHLY, BI_WEEKLY and ANNUAL; 2 design rows with carrier terms | accepted / auto_approve; review [] | a clean rate sheet maps losslessly onto the consumer quote |
| `benefits-conformance-hi-usd-flagged` | HOSPITAL_INDEMNITY, USD / en | 3 FLAT rows; row 1's frequency is **printed** `per pay period` (`pdf_overrides`), oracle cell BI_WEEKLY; 1 design row with no carrier term | pending / hitl_review / flagged_fields; Bridge review `fields.tables.rates[1].frequency`; consumer review `rates[1].frequency`, `design[0].category_original` | a stated field review survives the gate and the mapping, and the consumer's own design rule adds its flag |

Neither record carries `premium`/`deductible`. That is fine on Path B, which has no required key fields. The same page asserted on **Path A** would be rejected as `schema_invalid` (see DECISIONS.md). **Boundary:** native-digital PDFs only; a scanned/raster (OCR) PDF is the `docai` engine's envelope (Gemini + the client's Document AI OCR pre-pass, [design](../../../design/extraction.md)). No raster PDF is committed here. `tests/test_docai_extraction.py` renders carrier-C's scanned twin at test time (`tests/corpus_scanned_pdf.py`).

## Files

- **`expected.json`** — the five carrier-quote records (four carriers + Carrier B's revised v2) with their `expected_normalized` and `expected_gaps` oracle blocks.
- **`images/benefits-carrier-c-eur-fr.pdf`** — the carrier-C quote as a **native-digital** PDF (a real text layer), and the record's `artifact`. It is generated from that record's own `key_fields` in `expected.json` by `tests/corpus_pdf.py` (stdlib only, no dependency) and the output is byte-deterministic, so `uv run python -m tests.corpus_pdf` regenerates it in place and `tests/test_pdf_extraction.py` asserts byte-equality with the committed file — the page and the oracle cannot diverge silently. The fixture leg still reads `expected.json` (`FixtureExtractor` strips the `.pdf` suffix, so **both legs accept the same artifact name**); the gemini leg reads the bytes, inline as `application/pdf`. **Boundary:** native-digital PDF only; a scanned/raster (OCR) PDF is the `docai` engine's envelope (Gemini + the client's Document AI OCR pre-pass, [design](../../../design/extraction.md)). No raster PDF is committed here. `tests/test_docai_extraction.py` renders carrier-C's scanned twin at test time (`tests/corpus_scanned_pdf.py`).
- **`conformance.json`**: the two consumer-conformance records (see above), with `extraction`, `consumer_intake`, `expected_terminal` and `expected_consumer_quote`.
- **`images/benefits-conformance-ci-usd.pdf`**, **`images/benefits-conformance-hi-usd-flagged.pdf`**: the conformance rate sheets as native-digital PDFs. Both are rendered from their `conformance.json` records by the same `tests/corpus_pdf.py` (`uv run python -m tests.corpus_pdf` regenerates all three PDFs, byte-stable). `tests/conformance/test_benefits_conformance.py` asserts byte-equality with the committed files, and checks that every oracle cell, after `pdf_overrides`, is printed on the page. Money prints bare (`12.40`), because the consumer's money parser strips only `$`, `,` and spaces.
- **`images/.gitkeep`** — retained; carriers a, b, b-v2 and d are text-only records.

## Related
- [Benefits demo](../../../spec/demos/benefits-demo.md), [evals index](../README.md)
