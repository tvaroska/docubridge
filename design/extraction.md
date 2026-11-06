---
type: atom
up: "knowledge-model.md"
tags: [bridge, extraction, docai]
---

# Extraction — the graph, the engines, and what feeds them

> Extraction is a **port**, not a hardcoded engine: one capability axis (`fixture | gemini | docai`) chosen per deployment, one prompt and response schema built from the ontology, one deterministic gate reading the result. The Path-B graph below is what a human-supplied document runs through; everything after it is engine-agnostic.

## The Path-B fulfillment graph

The pipeline the Bridge runs when a **human** party supplies an upload / free-text / form via the [human edge](human-edge.md): extract → gate → disposition, as a durable graph (`bridge/pathb.py`).

```
receive_document → extract → quality_gate ─┬─ pass ─→ confidence_gate ─┬─ auto_approve → ACCEPT
                                           │                          └─ hitl_review → HITL (park)
                                           └── quality-gate fail ──┬─ attempts <= cap → RESUBMIT (park)
                                                                   └─ attempts  > cap → ESCALATE (park)
(wrong doctype ─→ unsupported ─→ REJECT)
```

- **Quality gate first.** Confidence below the resubmit threshold (or critical fields unreadable) → request an image **resubmission** (actionable message, capped at 3 retries, then escalation) — re-enters extraction on the **same** task as a new artifact version.
- **Confidence gate.** At or above the auto-approve threshold with no flagged fields → auto-approve; else route to **HITL review**.
- **HITL suspends the workflow** with **zero compute** for minutes/hours/days and resumes on a webhook — recovered from the persisted session, so it survives a restart.

**Path A skips this graph** — a structured response is validate-only (see [dual-path fulfillment](a2a-edge.md#dual-path-fulfillment)). Multilingual translate + FX normalization is a further capability along the same port. FX conversion reads a **fixed rate table** — deterministic, and the Benefits normalize oracle is authored against its numbers; a live rate source is the alternative shape, a one-argument swap at the same conversion point.

## The engines behind the port

The graph calls one extraction service that returns normalized fields plus per-field and overall confidence, legibility, and flagged fields (`contract/extraction.py::ExtractionResult`); the gates read those signals no matter which engine ran, and schema validation remains a layer on top of whichever engine produced the result. The engine is chosen once per deployment by `BRIDGE_EXTRACTION_ENGINE`, not per doctype ([ports](ports.md)):

| Engine | Module | What it is |
|---|---|---|
| `fixture` | `bridge/extraction/fixture.py` | deterministic corpus lookup over `wiki/evals/`; the test and local-dev engine |
| `gemini` | `bridge/extraction/gemini.py` | the deployed schema-driven engine: multimodal, constrained JSON under a pinned `response_schema`, bounded schema-validated retry, no fixture fallback |
| `docai` | `bridge/extraction/docai.py` + `gemini.py` | the **same** Gemini request with the client's existing Document AI processor run in front of it as an OCR / Layout / Form Parser pre-pass |

This is the [thesis](bridge.md) made literal: mediation is the focus, extraction is covered by dedicated services, so it lives behind a swap.

## Prompt and response schema from the ontology

The Gemini engine carries no literal field list. `bridge/doctypes.py::doctype_schemas` reads every installed doctype through the `skill_registry` port — rendered from its OKF bundle when `catalog/doctypes/<name>.okf.md` exists, else from the skill's `bridge-schema` asset — and `bridge/extraction/gemini_schema.py` turns that catalog into two things: `build_extraction_prompt` (header + the per-doctype field catalog with each entity's and field's contrastive description + promoted Agent Gym `EXTRACTION_HINT`s + footer) and `build_response_schema` (the **union** of the declared fields and tables, pinned as the request's structured-output schema). The same reader builds the Path-A typed validator, so prompt and validator cannot disagree ([ontology](ontology.md)).

**Target-scoped slicing & two-stage extraction (`ADR-15`).** `extract(artifact_name, *, target_doctype=…, candidate_doctypes=…, requested_fields=…, context_set=…, ontology_revision=…)` narrows the catalog with `bridge/doctypes.py::slice_ontology_for_extraction`: the seeds (the target doctype, the process's candidate doctypes, or every entity in a `context_set`) plus their 1-hop neighbourhood over the active revision's `[:SUPERSEDES]`, `[:SPLIT_FROM]`, `[:DISTINCT_FROM]`, `[:ALIASED_FROM]`, `[:MERGED_INTO]` and `[:REQUIRES_CORROBORATION]` edges, optionally filtered to `requested_fields`. Extraction inside `ExtractionPort` executes in two stages so models never perform arithmetic:
1. **Stage A — Multimodal Transcription & Entity Slicing:** The engine transcribes raw printed strings, `SourceAnchor` page/polygon coordinates, and multi-entity `records: list[ExtractedRecord]` (`entity_id`, `entity_type`, `page_range`, `key_fields`, `source_anchors` — e.g. per-tier / per-option schedules inside a single PDF).
2. **Stage B — Deterministic Field Derivations (`bridge/extraction/derivations.py`):** Pure-Python code (`apply_field_derivations` / `apply_result_derivations`) runs promoted `EXTRACTION_HINT` rules, polarity inversions (e.g. `100% - member_coinsurance_pct` → `employer_coinsurance_pct` with `polarity="MEMBER_PAYS"`), and contractual zero-balance derivations (`PAID IN FULL` wire → `remaining_liability_balance = "$0.00"`), recording a SHA-256 `FieldDerivation` receipt in `fields.derivations` and stamping `ontology_revision_id` and `applied_rule_ids` onto `ExtractionResult`.

With no scope given, the slice is the full installed catalog. Either way `fields.doctype` stays a **free string with no enum**: the Bridge *classifies*, so an out-of-label-space document must remain reportable or the `unsupported` outcome silently disappears. Because one document carries one doctype's fields, every union property is nullable and the parse prunes null-valued entries before validating. The catalog, the resolved document and the pinned schema are computed **once per extraction** and reused across retry attempts; `GeminiExtractor._catalog()` rebuilds the prompt only when the catalog content or the promoted hint set changed.

**Input is a resolved reference.** Documents cross the wire by reference ([artifacts](artifacts.md)); `bridge/extraction/resolve.py` turns the counterparty's URI into a `ResolvedDocument` — inline bytes (a corpus name, or an `http(s)` URL fetched under a default-deny egress guard), or a native `gs://` pointer the model reads itself with no download on the Bridge side. The resolve happens once per extraction and refuses loud.

## Large documents — page windows and anchor rebasing

A thousand-row rate sheet is one very large structured *output*; the output budget, not the input window, is what runs out (`finish_reason == MAX_TOKENS`). So every request states an explicit `max_output_tokens` (`BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS`), a truncated response never enters the schema retry, and `bridge/extraction/chunking.py` asks for less output per request: an inline PDF longer than `chunk_pages` (`BRIDGE_EXTRACTION_CHUNK_PAGES`, default 8; `0` disables) is extracted as **page windows** up front, and a truncated single call is **bisected by page** (a `gs://` object first gets one page-count survey call). Each window request carries the **whole document** plus a window instruction — `[prompt, document, (ocr_context), window_instruction]` — because a by-reference object cannot be split without the download the resolver exists to avoid. Windows run concurrently (`BRIDGE_EXTRACTION_CHUNK_CONCURRENCY`, default 4) under a hard window cap.

The merge (`merge_window_results`) is code-side and conservative: table rows concatenate in page order with no dedupe; whole-document scalars take the first window's value and a later disagreement is flagged for review, never overwritten; window-local row paths in `field_reviews` and `field_confidence` are rebased onto the merged table (`rebase_path`); each window's `SourceAnchor` (`contract/extraction.py`: 1-based `page_number`, optional normalized `bounding_box`, snippet, artifact URI) is **rebased by the window's first page** so a value found on window-local page 2 of a window starting at page 9 is anchored to page 10 of the document; confidence is the minimum and `legible` the conjunction. There is no partial success — any failed window fails the extraction, and a single page that alone overflows the budget raises `ExtractionTruncated`.

## Document AI pre-pass — the `docai` engine

The `docai` engine is **not a second extractor**. Document AI reads the pixels; Gemini still classifies, maps carrier terms onto the doctype's fields through the ontology catalog, and answers under the pinned response schema. One engine, one mapping, built once and shared by any consumer that has the same need.

**Why a pre-pass, not a parallel adapter.** A per-doctype Document AI adapter would need a custom processor and an entity→field map for **every** doctype, and it would duplicate what the Gemini engine already does: classify the document, map the carrier's own terms onto the doctype's fields, and report the review signals. What Document AI adds that Gemini multimodal lacks is a **machine-read text layer for a scanned/raster page**. So Document AI contributes exactly that, and it is added to the same request:

- `BRIDGE_EXTRACTION_ENGINE=gemini`: request contents `[prompt, document]`.
- `BRIDGE_EXTRACTION_ENGINE=docai`: request contents `[prompt, document, ocr_text]`.

Everything else is shared, unchanged: the doctype catalog, the pinned `response_schema`, the schema-validated retry, null-pruning, and by-reference inputs.

**The pre-pass contract** (`bridge/extraction/docai.py::DocAIPrepass`):

| | |
|---|---|
| **Input envelope** | The resolved document: inline bytes **or** a `gs://` reference, plus its mime type (`ResolvedDocument`). |
| **Supported mimes** | `application/pdf`, `image/png`, `image/jpeg`, `image/tiff`, `image/gif`, `image/bmp`, `image/webp`. Anything else, notably the Address corpus's text SVGs, skips the pre-pass with **no** Document AI call. The request then has the plain `gemini` shape. |
| **Request** | `process_document` on the processor. Bytes are sent as `raw_document {content, mime_type}`. A `gs://` reference is sent as `gcs_document {gcs_uri, mime_type}`: Document AI reads the object and the Bridge downloads nothing (the same principle as Gemini's `Part.from_uri`). |
| **Output** | Plain text: `document.text`. OCR and Form Parser fill it, and Form Parser key/value pairs are already inside it. A Layout Parser response leaves `text` empty, so the text is flattened from `document_layout.blocks`: text blocks and their children in reading order, then table rows (`cell \| cell`) and list entries. No entity map is built. The pre-pass is doctype-agnostic because doctype is an *output* of extraction. |
| **Anchors** | `extract_docai_source_anchors` turns Document AI's page refs and normalized vertices into per-field `SourceAnchor`s (page + bounding box), the same shape the window merge rebases and the Agent Gym records on an expert correction. |
| **Injection** | A bounded text part (≤ 100 000 chars; truncation is stated in the part) sits **next to** the original document, never instead of it. The part tells the model the attached document stays authoritative for layout, legibility and what is actually printed. A value that appears only in the OCR text is never to be copied. |
| **Cardinality** | Once per extraction, before the retry loop. The OCR part lives in the base contents, so a schema retry keeps it and never re-OCRs. |
| **Failure** | A Document AI error (403, 404, quota) **propagates unchanged**, and no Gemini call is made. There is **no fallback to plain Gemini**: a silent capability downgrade is a silent cap. |
| **Empty text** | A blank or unreadable page is not an error. A warning is logged, no OCR part is added, and Gemini still sees the document and reports `legible`. |

**Processor by resource name.** `BRIDGE_DOCAI_PROCESSOR` is the full resource name of the processor the client already runs: `projects/{project}/locations/{location}/processors/{id}`, optionally with `/processorVersions/{version}`; `{project}` may be a project id or number. The name is validated when the engine is resolved, so a misconfigured deploy fails on the first extract, before any artifact fetch — unset: `RuntimeError` naming `BRIDGE_DOCAI_PROCESSOR`; malformed: `ValueError` naming the expected shape. Calls go to the processor's **regional endpoint**, `{location}-documentai.googleapis.com` (`us`, `eu`); using the default endpoint for an `eu` processor is the classic "processor not found". The per-doctype `bridge-docai-processor` skill metadata key is **not** consulted by the pre-pass: the processor is configured for the whole deployment, because doctype is unknown until extraction has run.

**Cross-project IAM.** The processor lives in the **client's** project, so the grant does too: `roles/documentai.apiUser` for the `bridge-runtime` workload identity, on the processor's project — least privilege (processing on existing processors, never `documentai.admin`), never added to the Bridge's own project roles. In Terraform, `docai_processor` drives both the runtime env and the grant (`modules/iam`, `docai_api_user`); `manage_docai_grant = false` is for an applying identity without `setIamPolicy` on the client's project — no grant is applied and the `docai_grant` output (`{project, role, member}`) is what the client applies on their side. For `gs://` inputs the reading identity must be able to read the object; a 403 on a live by-reference run means the processor project's Document AI service agent needs `roles/storage.objectViewer` on the bucket.

**Operating notes.** *Data residency:* the document goes to the client's processor in the processor's region, the same place the client's own Document AI traffic already goes; the Gemini leg is unchanged. *Cost:* choosing `docai` applies to the whole deployment, so native-digital PDFs are OCR'd too — one Document AI page charge per page on top of the Gemini call; keep `gemini` where scans are rare, `docai` where they are the norm. *Latency:* one synchronous online-processing call before the Gemini call.

**Sharing with a consumer engine.** The module depends only on the standard library, a lazy `google.cloud.documentai` import, and the `ResolvedDocument` envelope; the request builder, the text flattener and the OCR-part builder take primitives (`mime_type`, `data`, `uri`), so a consumer engine can vendor them unchanged. A consumer with its own Gemini extraction path adopts the same contract — its `ADP_DOCAI_PROCESSOR` is `BRIDGE_DOCAI_PROCESSOR`, the same grant, the same injection (`[prompt, document, ocr_text]`), the same no-fallback rule — so both engines read a scanned quote the same way, and delegating extraction to the Bridge over A2A changes nothing about the result.

## Containers and office documents

Two stdlib-only prepass modules sit in front of the engines for packaged and office-format submissions:

- **`bridge/extraction/containers.py`** unpacks nested `.zip` archives, RFC-5322 `.eml` messages and OLE2/CFBF `.msg` envelopes (`unpack_submission_container`) with bounded recursion and decompression guards, SHA-256 leaf deduplication and an envelope provenance chain per leaf (`UnpackedArtifact`, `ContainerUnpackResult`). An HTML reply table inside the envelope body is parsed into `ReplyItemAdvisory` rows (`parse_html_reply_advisories`) and mapped onto the requirements list (`apply_advisories_to_requirements`) as **advisory** sense-B input — the calling agent still decides.
- **`bridge/extraction/office_prepass.py`** reads `.docx` separating accepted text from `<w:del>` tracked changes (`extract_docx_with_redlines`), `.rtf` (`extract_rtf_text`) and `.xlsx`/`.xlsm` with a malformed-`sharedStrings.xml` fallback and hidden-row filtering (`extract_xlsx_resilient`); runs a deterministic filename-vs-content issuer check (`detect_issuer_mismatch`); and fans one bundled submission out into N sub-entity extractions (`expand_bundled_extractions`).

Neither module calls a model; both produce inputs the engines and the disposition gate consume unchanged.

## Related
- [knowledge model](knowledge-model.md) · [ontology](ontology.md) · [skills](skills.md) · [ports](ports.md) (the extraction capability axis and its knobs) · [artifacts](artifacts.md) · [disposition](disposition.md) · [extraction feature notes](../docs/features/extraction.md)
