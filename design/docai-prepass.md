---
type: atom
up: "ports.md"
related:
  - "ports.md"
  - "skills.md"
  - "artifacts.md"
  - "gcp-substrate.md"
tags: [bridge, extraction]
---

# Document AI pre-pass — the `docai` extraction engine

> The `docai` engine is **not a second extractor**. It is the schema-driven Gemini engine with the client's **existing Document AI processor** run in front of it as an OCR / Layout / Form Parser **pre-pass**. Document AI reads the pixels; Gemini still classifies, maps carrier terms onto the doctype's fields through the [skill](skills.md) catalog, and answers under the pinned response schema. One engine, one mapping, built once and shared by any consumer that has the same need.

## Why a pre-pass, not a parallel adapter

The [extraction port](ports.md) is a capability axis (`fixture|gemini|docai`). A per-doctype Document AI adapter would need a custom processor and an entity→field map for **every** doctype, and it would duplicate what the Gemini engine already does from the doctype skill: classify the document, map the carrier's own terms onto the doctype's fields, and report the review signals. What Document AI adds that Gemini multimodal lacks is a **machine-read text layer for a scanned/raster page**. So Document AI contributes exactly that, and it is added to the same request.

- `BRIDGE_EXTRACTION_ENGINE=gemini`: request contents `[prompt, document]`.
- `BRIDGE_EXTRACTION_ENGINE=docai`: request contents `[prompt, document, ocr_text]`.

Everything else is shared, unchanged: the doctype catalog, the pinned `response_schema`, the schema-validated retry, null-pruning, and by-reference inputs.

## The pre-pass contract

| | |
|---|---|
| **Input envelope** | The resolved document: inline bytes **or** a `gs://` reference, plus its mime type (`ResolvedDocument`). |
| **Supported mimes** | `application/pdf`, `image/png`, `image/jpeg`, `image/tiff`, `image/gif`, `image/bmp`, `image/webp`. Anything else, notably the Address corpus's text SVGs, skips the pre-pass with **no** Document AI call. The request then has the plain `gemini` shape. |
| **Request** | `process_document` on the processor. Bytes are sent as `raw_document {content, mime_type}`. A `gs://` reference is sent as `gcs_document {gcs_uri, mime_type}`: Document AI reads the object and the Bridge downloads nothing (the same principle as Gemini's `Part.from_uri`). |
| **Output** | Plain text: `document.text`. OCR and Form Parser fill it, and Form Parser key/value pairs are already inside it. A Layout Parser response leaves `text` empty, so the text is flattened from `document_layout.blocks`: text blocks and their children in reading order, then table rows (`cell \| cell`) and list entries. No entity map is built. The pre-pass is doctype-agnostic because doctype is an *output* of extraction. |
| **Injection** | A bounded text part (≤ 100 000 chars; truncation is stated in the part) sits **next to** the original document, never instead of it. The part tells the model the attached document stays authoritative for layout, legibility and what is actually printed. A value that appears only in the OCR text is never to be copied. |
| **Cardinality** | Once per extraction, before the retry loop. The OCR part lives in the base contents, so a schema retry keeps it and never re-OCRs. |
| **Failure** | A Document AI error (403, 404, quota) **propagates unchanged**, and no Gemini call is made. There is **no fallback to plain Gemini**: a silent capability downgrade is a silent cap. |
| **Empty text** | A blank or unreadable page is not an error. A warning is logged, no OCR part is added, and Gemini still sees the document and reports `legible`. |

## Processor by resource name

- `BRIDGE_DOCAI_PROCESSOR` is the full resource name of the processor the client already runs: `projects/{project}/locations/{location}/processors/{id}`, optionally with `/processorVersions/{version}`. `{project}` may be a project id or number.
- The name is validated when the engine is resolved, so a misconfigured deploy fails on the first extract, before any artifact fetch:
  - unset: `RuntimeError` naming `BRIDGE_DOCAI_PROCESSOR`;
  - malformed: `ValueError` naming the expected shape.
- Calls go to the processor's **regional endpoint**, `{location}-documentai.googleapis.com` (`us`, `eu`). Using the default endpoint for an `eu` processor is the classic "processor not found".
- The per-doctype `bridge-docai-processor` skill metadata key is **not** consulted by the pre-pass. The pre-pass processor is configured for the whole deployment, because doctype is unknown until extraction has run.

## Cross-project IAM

The processor lives in the **client's** project, so the grant does too.

- **Grant:** `roles/documentai.apiUser` for the `bridge-runtime` workload identity, on the processor's project. This is least privilege: it allows processing on existing processors and never `documentai.admin`. It is never added to the Bridge's own project roles.
- **Terraform:**
  - `docai_processor` drives both the runtime env and the grant (`modules/iam`, `docai_api_user`).
  - `manage_docai_grant = false` is for an applying identity that has no `setIamPolicy` on the client's project. No grant is applied, and the `docai_grant` output (`{project, role, member}`) is what the client applies on their side.
- **`gs://` inputs:** the reading identity must be able to read the object. If a live by-reference run returns 403, grant the processor project's Document AI service agent `roles/storage.objectViewer` on the bucket.

## Operating notes

- **Data residency.** The document goes to the client's processor in the processor's region, the same place the client's own Document AI traffic already goes. The Gemini leg is unchanged.
- **Cost.** Choosing `docai` applies to the whole deployment, so **native-digital PDFs are OCR'd too**, which is one Document AI page charge per page on top of the Gemini call. Keep `gemini` where scans are rare, and use `docai` where they are the norm.
- **Latency.** The pre-pass is one synchronous online-processing call before the Gemini call.

## Sharing with a consumer engine

The module (`bridge/extraction/docai.py`) depends only on the standard library, a lazy `google.cloud.documentai` import, and the `ResolvedDocument` envelope. The request builder, the text flattener and the OCR-part builder take primitives (`mime_type`, `data`, `uri`), so a consumer engine can vendor them unchanged.

A consumer with its own Gemini extraction path, such as the consumer's `engine/extraction/`, adopts the same contract:

- Its `ADP_DOCAI_PROCESSOR` is `BRIDGE_DOCAI_PROCESSOR`.
- It uses the same grant (`roles/documentai.apiUser` on the client's processor project).
- It keeps the same injection (`[prompt, document, ocr_text]`) and the same no-fallback rule.

Both engines then read a scanned quote the same way, and delegating extraction to the Bridge over A2A changes nothing about the result.

## Read next
- [ports](ports.md): the extraction capability axis
- [skills](skills.md): doctype skills, the catalog the Gemini leg maps onto
- [artifacts](artifacts.md): how a by-reference document reaches the engine
- [gcp-substrate](gcp-substrate.md): the Extraction row and its IAM
