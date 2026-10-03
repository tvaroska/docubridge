---
type: atom
related:
  - "patterns.md"
  - "disposition.md"
  - "a2ui-edge.md"
tags: [bridge]
---

# Extraction (Fulfillment) Graph

> The Path-B pipeline the Bridge runs when a **human** party supplies an upload / free-text / form via the A2UI portal: extract → gate → disposition, as a durable graph.

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

**Extraction is a [seam](seams.md), not a hardcoded engine.** The graph calls one extraction service that returns normalized fields plus per-field and overall confidence, legibility, and flagged fields; the gates below read those signals no matter which engine ran. The described engines are a deterministic **fixture** adapter (for tests) and **Gemini** (schema-driven constrained JSON per the [doctype skill](skills.md), multimodal, ~$0.10 / 1k pages); the **Document AI** engine (`docai`) is that same Gemini request with the client's existing Document AI processor run in front of it as an OCR [pre-pass](docai-prepass.md) — one processor for the deployment, no per-doctype processor or entity map. The engine is chosen once per deployment by `BRIDGE_EXTRACTION_ENGINE`, not per doctype. Schema validation remains a layer on top of whichever engine produced the result. This is the [thesis](bridge.md) made literal: mediation is the focus, extraction is covered by dedicated services, so it lives behind a swap.

**Path A skips this graph** — a structured response is validate-only (see [dual-path](dual-path.md)). Multilingual translate + FX normalization is a further capability along the same seam. FX conversion reads a **fixed rate table** — deterministic, and the Benefits normalize oracle is authored against its numbers; a live rate source is the alternative shape, a one-argument swap at the same conversion point.

## Related
- [patterns](patterns.md), [disposition](disposition.md), [A2UI edge](a2ui-edge.md)
