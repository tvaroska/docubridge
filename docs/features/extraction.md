# Extraction Features

Core extraction capabilities of the Document Bridge.

---

## Extraction Review Vocabulary

The Bridge provides a consumer-facing vocabulary for extraction review that maps the Bridge's internal wire fields to the terminology service agents use when asking for field-level review provenance.

**Consumer vocabulary mapping:**

| Consumer term | Bridge wire field | How they relate |
|---------------|-------------------|-----------------|
| `extraction_confidence` | `overall_confidence` | Same scalar (0.0–1.0) |
| per-field confidence | `field_confidence[name]` | Keyed by bare key_fields names |
| `fields_requiring_review` | `flagged_fields` + `field_reviews[].path` | Helper function returns dotted paths into the extraction result |
| review reason | `field_reviews[].reason` | Why a field needs review: `low_confidence`, `derived`, `absent_from_document`, or `model_flagged` |

**Key design decisions:**

- **Paths address the Bridge result, not consumer schemas.** A dotted path like `fields.key_fields.name` or `rates[0].amount` points into the Bridge's `ExtractionResult` structure. The consumer re-maps these onto their own domain schema.

- **Mapping, not renaming.** The Bridge keeps its original field names (`flagged_fields`, `overall_confidence`) on the wire for backward compatibility. An inbound validation alias lets consumers POST using their vocabulary (`fields_requiring_review`), and helper functions (`fields_requiring_review()`, `field_reviews_for()`) compute the mapping on demand.

- **Additive, not breaking.** The `field_reviews` array is optional and defaults to empty, so existing extraction results validate unchanged. The Bridge derives review entries from `flagged_fields` when the extraction engine doesn't provide them explicitly.

**Why fields need review:**

The `ReviewReason` enum documents provenance for flagged fields:
- `low_confidence` — extraction engine reported low confidence for this field
- `derived` — value was inferred or defaulted, not read from the document
- `absent_from_document` — field was requested but has no value in the source
- `model_flagged` — flagged without a specific reason (legacy/default)

**Reaching the ledger:**

Field-level review information flows through to the A2A wire via `LedgerEntry.field_reviews`, so a consumer driving a collect turn receives both the disposition (`hitl_review` when any field needs review) and the specific paths plus reasons that triggered it.

---

## Review contract (disposition gate)

The answer to "what level of human oversight, and when is manual review invoked?" is the disposition gate, published as a consumer contract. The normative text is [design/disposition.md — The review contract](../../design/disposition.md#the-review-contract); this is the summary plus the current calibration numbers.

| # | Condition (first match wins) | gate | reason_code |
|---|---|---|---|
| 1 | doctype outside the candidate label space | `unsupported` | `unsupported_doctype` |
| 2a | not legible | `resubmit` | `illegible` |
| 2b | `overall_confidence < 0.55` (`resubmit_below`) | `resubmit` | `low_quality` |
| 3a | any field requires review (flags ∪ `field_reviews`) | `hitl_review` | `flagged_fields` |
| 3b | `overall_confidence < 0.85` (`auto_approve_at`) | `hitl_review` | `needs_review` |
| 4 | otherwise | `auto_approve` | `auto_approved` |

- Comparisons are strict `<`: exactly 0.55 is HITL, exactly 0.85 is auto-approve. A flag beats the 0.85 comparison but not the 0.55 one.
- `hitl_review` is `pending` → the A2A task parks `INPUT_REQUIRED`. Escalation after `max_resubmissions` (3) quality-gate failures is `pending` / `escalated_for_review`.
- Thresholds live in each process skill's `policy.yaml` `disposition:` block (`address-proof`, `benefits-procurement`, `benefits-rfp`), all equal to the code defaults. A test guards that, and inverted / out-of-range / NaN thresholds are refused at construction and at policy load.

**Current corpus corridor** (`uv run python -m bridge.calibration`, 10 gate-bearing records: Address 8, benefits conformance 2):

| Threshold | Corridor | Lower edge set by | Upper edge set by |
|---|---|---|---|
| `resubmit_below` | (unbounded, 0.72] | none: the only resubmit record, `bill-aquautil-blurry`, is illegible and caught by the legibility gate first | `bill-aquautil-clear` (0.72, hitl) |
| `auto_approve_at` | (0.72, 0.94] | `bill-aquautil-clear` (unflagged hitl at 0.72) | `benefits-conformance-ci-usd` (auto at 0.94) |

- `DEFAULT_THRESHOLDS` 0.85 / 0.55 and all three process policies are **inside**.
- A consumer's 0.9 highlight is **also inside**. The corpus cannot tell 0.85 from 0.9, so neither number is better calibrated than the other.
- `--auto-approve-at 0.95 --resubmit-below 0.55` is OUTSIDE and the CLI exits 1. `--auto-approve-at 0.9 --resubmit-below 0.55` exits 0.
- Flagged records (`gov-id-expired` 0.93, `benefits-conformance-hi-usd-flagged` 0.90) bound only `resubmit_below`. The HI record is flagged only through its `field_reviews`. `passport-unsupported` bounds nothing.
- Excluded on purpose: `benefits/expected.json` and `rfp/expected.json` carry no expected gate.

**Reconciliation with Benefits Consumer.** the consumer's frontend `LOW_CONFIDENCE_THRESHOLD = 0.9` (`frontend/src/lib/review.ts`) is a display highlight, not a gate. The contract asks the consumer to render the Bridge's `gate` / `reason_code` / `field_reviews`. If it shows a confidence badge, the badge should use `auto_approve_at` (0.85), so that "highlighted" means exactly "not auto-approved on confidence". The contract values stay 0.85 / 0.55. Benefits Consumer S0-arch-2 adopts them, and the change on the consumer's side is a proposal (see DECISIONS.md, 2026-09-30 S0-docs-5).

---

## Structured Rows (Tables)

Flat `key_fields` (`dict[str, str]`) can't carry a carrier rate sheet, so row-shaped data travels next to it in `ExtractionFields.tables` / `LedgerEntry.tables`. That field maps a table name to a list of rows, and each row maps a column name to text. A doctype skill declares its tables in `properties.tables` of its `schema.json`. Path A validates rows against a typed model built from that declaration, and Path B pins the same tables in the Gemini response schema. Every cell is text, including amounts: the consumer code-parses money.

`benefits-quote` schema v2 declares:
- `rates`: coverage_tier, tobacco, age_band, amount, frequency (per row), rate_basis, benefit_amount.
  - frequency is one of MONTHLY / BI_WEEKLY / WEEKLY / SEMI_MONTHLY / ANNUAL.
  - rate_basis is one of FLAT / PER_1000 / PER_UNIT.
- `design`: category, category_original, value.
- New optional key fields: product_line, carrier_product_name, plan_year, commission_basis, commission_rate_pct, commission_notes.

Contract: minor bump to `contract_version` `1.1`. `decode_wire` now also strips unknown fields when a payload declares a newer minor of our major.

## Scale (RFP-size documents)

A 10-carrier × 5-product RFP brings back quotes whose rate sheets run to a thousand rows. One structured Gemini response that size is cut at the output budget (`finish_reason == MAX_TOKENS`). The input window is not the limit. Re-asking at the same budget reproduces the cut, so the document would be lost. The Gemini/docai engine therefore extracts long documents as **page windows**.

- **When.** An inline PDF longer than `BRIDGE_EXTRACTION_CHUNK_PAGES` (default 8) is windowed up front; the page count is read with the standard library (`bridge/extraction/chunking.py`, incl. PDF 1.5 compressed object streams). A single call cut at `BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS` (default 32768, now sent on every request) is bisected by page; a `gs://` object first gets one tiny page-count survey call. A truncated response never enters the schema retry.
- **Request shape.** `[prompt, document, (ocr_context), window_instruction]`. The whole document rides in every window (a `gs://` object cannot be split without downloading it). The instruction starts with the sentinel `[bridge-page-window a-b/N]` and asks for the rows printed on those pages plus the whole-document scalars. Short documents are unchanged: one call, `[prompt, document]`.
- **Concurrency.** Up to `BRIDGE_EXTRACTION_CHUNK_CONCURRENCY` (default 4) windows in flight; a hard cap of 128 window requests per extraction. Every model call runs via `asyncio.to_thread`, so the SSE / dashboard / portal hosts sharing the loop keep ticking.
- **Merge** (code-side): rows concatenate in page order, no dedupe; scalars first-wins, a later disagreement is flagged `model_flagged` ("page windows disagree: …"); window-local row paths in `flagged_fields` / `field_reviews` / `field_confidence` are rebased (`fields.tables.rates[3]` in the second window → `rates[43]`), an un-emitted index is coarsened to `fields.tables.rates` with a warning; confidence = min, `legible` = all.
- **Failures.** No partial success. Any failed window fails the extraction with the original exception type (a 503 stays a bare `RuntimeError`, an exhausted window a `ValidationError` noting `"<artifact> pages a-b"`). A single page that alone overflows the budget, `chunk_pages=0`, a non-paged document or the window cap raise `ExtractionTruncated` (a `ValueError`) naming the page and the budget.
- **Read-model.** `readmodel_snapshot` projects the store and RFP board once per SSE tick. The HITL and escalation queues are filters over that projection.

**The harness.** `uv run python -m tests.perf.harness [--json PATH] [--rows-per-entry 1000] [--carriers 10] [--products 5] [--live]` drives synthetic multi-page PDFs (`tests/perf/scale_corpus.py`, written outside `wiki/evals/`) through the real engine with a page-aware fake client that truncates past its budget (`tests/perf/fake_gemini.py`), then the ledger, a 10 × 5 RFP, the portal and the dashboard SSE. It exits 0 only if every correctness check passes. Fake-client run on 2026-09-30 (timings are this machine's, indicative only):

| Leg | Result |
|-----|--------|
| rate-sheet-1000 (26 pages, 108 KB PDF) | 4 window calls, 1000/1000 rows exact + ordered, max 4 in flight, 0.08 s, 11 loop ticks while extracting |
| unchunked "before" (`chunk_pages=0`) | `ExtractionTruncated` after exactly 1 (truncated) call |
| quote-50p (50 pages, 159 KB PDF) | 7 window calls, 1380 rate + 60 design rows exact, 0.13 s |
| ledger (1000-row quote) | accepted; `LedgerEntry` 154 KB, project 1.4 ms, dump 0.8 ms |
| RFP 10 × 5 at 1000 rows/entry | 50/50 received, waiting 0; 50 receives 0.20 s; `ExchangeTurn` 7.7 MB, dump 0.06 s, `decode_wire` 0.05 s |
| portal | upload + view encode < 10 ms; `GET /sessions/{sid}/view` 315 B |
| dashboard | snapshot 0.11 s / 8.6 MB; `GET /exchanges` 7.9 MB in 0.18 s; first SSE frame 8.6 MB at 0.77 s, carrying the RFP row with 50 cells |

**Live run (2026-09-30, Vertex 2.5-flash, default knobs):** the 26-page sheet returned 1000/1000 rows, at least 99% canonical match, in 326 s. Three of the four 8-page windows still hit `MAX_TOKENS` live (thinking tokens share the output budget) and were bisected to 4- and 2-page windows. The reactive path did real work.

The dashboard numbers are the next bottleneck: every SSE frame carries every entry's full tables. See the S0-perf-2 proposal in `DECISIONS.md`.

## Completed Work

### S0-perf-1 — RFP-scale harness + page-window chunking (2026-09-30)

**What.** Large documents no longer lose rows to output truncation; see [Scale](#scale-rfp-size-documents) above.

- `bridge/extraction/chunking.py` (new): `ExtractionTruncated`, the stdlib `pdf_page_count`, `plan_windows` / `bisect_window`, `rebase_path` and `merge_window_results`.
- `bridge/extraction/gemini.py`: explicit `max_output_tokens`, `MAX_TOKENS` detection ahead of the retry, proactive and reactive windows, the gs:// survey, bounded concurrency, `asyncio.to_thread` for every call, and three env knobs that refuse loud.
- `bridge/dashboard/readmodel.py`: one projection per snapshot.
- `tests/perf/`: the scale corpus, the page-aware fake, the harness, and scale + surface + gated live tests. `tests/corpus_pdf.py` gains `build_multipage_pdf`; `build_pdf` output is byte-unchanged.

**T2 evidence.** The harness exits 0 with 16/16 correctness checks passing (table above). The 8 Address corpus records each make one call shaped `[prompt, document]` (`tests/test_gemini_chunking.py`). `BRIDGE_EXTRACTION_CHUNK_PAGES=x` raises a `ValueError` naming the env. `GcpExtraction(engine="gemini")` resolves to `32768 8 4`.


### S0-arch-12 — Document AI OCR pre-pass (2026-09-30)

**What.** The `docai` extraction engine is now real. It is the schema-driven Gemini engine with the client's existing Document AI processor (`BRIDGE_DOCAI_PROCESSOR`) run once in front as an OCR pre-pass. The OCR text rides as a third part next to the document (`[prompt, document, ocr_text]`), and a retry keeps it. A Document AI error propagates with no plain-Gemini fallback. SVG and other unsupported mimes skip the pre-pass. A `gs://` input rides by reference.

- `bridge/extraction/docai.py` (new): processor parsing, the request builder, the Layout-Parser flattener, the bounded OCR part and `DocAIPrepass`.
- `bridge/extraction/gemini.py`: the `prepass=` hook. `bridge/ports/gcp/__init__.py`: `docai` → `GeminiExtractor(prepass=DocAIPrepass())`.
- `pyproject.toml` `gcp` extra: `google-cloud-documentai>=3.0`.
- Terraform: `docai_processor` → the runtime env plus a cross-project `roles/documentai.apiUser` grant (`manage_docai_grant`, `docai_grant` output).
- `tests/corpus_scanned_pdf.py`: a deterministic raster-only render of carrier-C with no text layer.
- `design/docai-prepass.md`: the shared contract (the consumer's `ADP_DOCAI_PROCESSOR` is the same setting).

**T2 evidence.**
- `uv run pytest tests/test_docai_extraction.py` gives 36 passed / 2 gated live skips.
- Config: an unset processor raises `RuntimeError`, a malformed one raises `ValueError`, and an `eu` processor uses `eu-documentai.googleapis.com`.
- The scanned PDF gives 0 pdftotext characters and 1 image, and two builds are byte-identical. The scanned leg reaches carrier-C's `expected_normalized`/`expected_gaps`.
- `terraform test` gives 21 passed, including the docai grant, out-of-band and validation runs.

T1: `ruff check` clean; `pytest` 942 passed / 36 skipped, 0 failed. The live Document AI leg was not run (no processor or credentials).

### S0-arch-10 — the `benefits-quote` schema carries a rate table (2026-09-30)

**What was built.**
- `contract/extraction.py` + `contract/wire.py`: an additive `tables` field on `ExtractionFields` and `LedgerEntry`.
  - `CONTRACT_VERSION` goes from `1` to `1.1`.
  - `decode_wire` is tolerant of a newer minor.
  - A bare table name in a review maps to the dotted path `fields.tables.{t}`.
- `bridge/doctypes.py`:
  - New readers: `table_properties`, `table_spec`, `doctype_tables`.
  - `tables_model`: declared tables are optional; required columns are mandatory; undeclared columns and tables are `extra_forbidden`.
  - A string `enum` in a key field or column now becomes a closed `Literal`.
- `bridge/patha.py`: validates `tables` too, so a bad row is rejected as `schema_invalid` at the edge.
- `bridge/extraction/gemini.py`:
  - The prompt catalog renders each table.
  - The response schema pins the union of tables (nullable string cells, enums kept only where every doctype declaring them agrees), plus key-field enums.
  - `_prune_nulls` drops null cells and null tables.
- `bridge/collect.py`: projects `tables` onto the ledger entry.
- `skills/benefits-quote`: schema and skill are now v2, with row-level rules in `validation.yaml`.

**Key approach.** A sibling field was chosen over widening `key_fields`:
- Every existing reader, the whole benefits/Address corpus, `normalize` and the flat `LedgerEntry.key_fields` stay byte-for-byte unchanged.
- The v1 required key fields are untouched, so the oracle still validates.

**T2 evidence.** `tests/test_structured_quote.py` (28 tests):
- The rate and design columns and every enum cover the consumer's `RateTier` / `DesignField` / `CoverageTier` / `TobaccoClass` / `RateFrequency` / `ProductLine`, with WEEKLY and SEMI_MONTHLY added.
- A mixed MONTHLY/WEEKLY/SEMI_MONTHLY rate sheet at PER_1000 basis, with design rows and commission, is `accepted` over the real A2A edge. The rows reach the consumer's decoded `ExchangeTurn` verbatim.
- Each of these is rejected at Path-A validation (and `FORTNIGHTLY` is `schema_invalid` over the edge): a bad frequency, a missing amount, an extra column, an undeclared table, a design row missing its value, and an out-of-vocabulary `product_line`.
- The pinned response schema passes `google.genai` `t_schema`.
- A fake-client Gemini extract goes through `ExtractionResult`, `LedgerEntry` and an encoded then decoded `ExchangeTurn` with its cells intact.
- A newer-minor payload decodes tolerantly; same- and older-version payloads stay strict.

T1: `ruff check` clean; `pytest` 821 passed / 31 skipped, 0 failed.

The live Gemini leg (`BRIDGE_TEST_GEMINI`) was not run here; the schema was only checked against genai's transformer.

### S0-arch-9 — cross-repo consumer conformance run (2026-09-30)

**What.** A carrier rate sheet goes in **by reference** (loopback `http://` or `gs://`) over the real A2A edge. It is extracted against the installed `benefits-quote` skill and lands on the consumer's decoded `ExchangeTurn` with per-field confidence and reviews. It is then mapped onto the VB consumer's own `BenefitsQuote`. The fixture leg and the replayed-gemini leg must reach the same terminal outcome.

- `contract/wire.py` 1.3: `LedgerEntry.overall_confidence` / `field_confidence` (additive), projected in `bridge/collect.py`.
- `wiki/evals/benefits/conformance.json` holds two records:
  - CI, clean → `accepted`/`auto_approve`;
  - HI, with a `per pay period` cell → `pending`/`hitl_review`/`flagged_fields`.
  - It also has two native-digital PDFs rendered by `tests/corpus_pdf.py`.
- `tests/conformance/`:
  - `consumer.py`: the mirrored consumer models plus a refuse-loud `map_entry_to_quote` / `ConformanceGap`;
  - `journey.py`: the loopback corpus server, the replay client and the runnable journey;
  - `test_benefits_conformance.py`.

**T2 evidence.**
- `uv run pytest tests/conformance` gives 46 passed / 3 gated.
- Both records × {fixture, gemini} × {http, gs} reach the oracle terminal and the oracle `expected_consumer_quote`, with parity per record.
- The Part checks: gs:// arrives as `file_data`; http arrives as inline `application/pdf` bytes fetched from the allowlisted loopback server.
- Gap probes: WEEKLY, PER_1000, an unmappable review, missing confidence and unmodelled fields all raise `ConformanceGap` with every gap named.
- The PDFs are byte-stable, and every oracle cell is printed on the page.
- The mirror vocabulary matches the wire enums.
- The AST isolation guard holds.
- Gated runs:
  - the real consumer's models accept the mapped quote (`BRIDGE_TEST_CONSUMER_REPO`);
  - live Gemini platform maps record A onto the quote (`BRIDGE_TEST_GEMINI`).

T1: `ruff check` clean; `pytest` 906 passed / 34 skipped, 0 failed.
