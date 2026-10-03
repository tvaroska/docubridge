---
type: atom
related:
  - "demos/demo-suite.md"
tags: [bridge]
---

# Open Questions

> Design questions that are genuinely undecided or intentionally settled at deploy time. Resolved decisions live in the relevant design pages; this note tracks only what is still open, with the current leaning where one exists.

## Settled since last revision

- **Outbound is first-class** (not "a separate design direction"). The Bridge chases, delivers, and dials counterparties over the existing A2A-client seam; the pull spine remains the core motion. First build slice is the outbound seam + a real party-channel nudge in the Address cycle. See [outbound direction](../design/outbound.md).
- **ADK is authoritative at `google-adk 2.7.0`** — no pre-planning verification; the experimental-surfaces list is the risk register ([adk](../design/adk.md)).
- **Three demos only** — the "Maturity" demo is dropped ([frontend](../design/frontend.md)).
- **CUJ ids now resolve** to the [CUJ catalog](cujs.md).

## Artifact retention window

The retention *mechanism* is a backend object-lifecycle policy (not bespoke GC), covering at least the exchange life. The concrete post-terminal **window** is a compliance/ops concern left to deploy-time policy, not fixed in the architecture. See [long-running collection](../design/long-running.md).

## Feature request (incoming) — carrier PDF → structured extraction, and who owns it (Aon benefits consumer)

**Source:** Aon Digital Placement (`~/aon`), CUJ-8 "full demo" journey (step 1: "got PDFs from carriers"). The original ask: the Bridge owns carrier-quote **PDF → strict structured JSON** so the consumer normalizes to its `BenefitsQuote` schema — validated fields (rates by tier, benefit design by category, effective dates), extraction confidence + `fields_requiring_review` per field, schema-validated retry on malformed output, bytes by reference as artifacts ([artifacts](../design/artifacts.md)).

**What changed since the ask was filed.** Both sides now carry an extraction engine for this document class. The consumer runs its own (`~/aon/engine/extraction/gcp.py::GcpExtractor` — Vertex Gemini with a pinned response schema, a bounded schema-validated retry that feeds each rejection reason back to the model, no fixture fallback, `fields_requiring_review`, native PDF bytes and `gs://` read by reference), reached in-process through its own document-bridge interface: a carrier document reference in, a validated `BenefitsQuote` out.

**What the Bridge offers for this document class.** The capability is a doctype skill over the one generic, schema-driven engine ([skills](../design/skills.md)): the `benefits-quote` doctype skill the [Benefits demo](demos/benefits-demo.md) runs on, its schema including the rate and benefit-design tables. Its extraction is schema-validated with a bounded retry that echoes each rejection back, and an exhausted retry raises — never a fixture fallback. A native PDF is read **by reference** (`gs://`, or an `http(s)` URL such as a signed URL, fetched under an egress guard); a scanned page is read through an OCR pre-pass ([Document AI pre-pass](../design/docai-prepass.md)); a large rate sheet is extracted in page windows and merged without dropping a row ([seams](../design/seams.md)). Per-field confidence and review flags travel in the consumer's dotted-path vocabulary — mapped, deliberately not renamed on the wire ([standards](standards.md) `contract_version` semantics) — and a conformance run takes a carrier PDF in by reference over the A2A edge and maps the result onto the consumer's `BenefitsQuote` shape, on the fixture and Gemini legs at terminal-outcome parity.

**The live question.** Two extraction engines now exist for the same document class. Does the consumer **delegate** extraction to the Bridge over A2A, or keep its own engine and use the Bridge only for the collection spine? Settle:

- **Contract shape.** Where the Bridge's vocabulary is wider than the consumer's `BenefitsQuote` (rate frequencies or a rate basis the consumer does not model), the mapping refuses rather than coerces; which side widens is open.
- **Provenance ownership.** Which side supplies intake context the document itself never carries (e.g. the placement/program identifier), and "no fixture fallback on exhausted retries" as a discipline both engines hold.

## Related
- [demo implementations](demos/demo-suite.md), [A2A edge](../design/a2a-edge.md), [long-running collection](../design/long-running.md), [Benefits demo](demos/benefits-demo.md)
