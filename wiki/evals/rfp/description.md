---
type: eval
related:
  - "../../../spec/demos/rfp-demo.md"
tags: [bridge, eval, scaffold]
---

# Eval — RFP *(scaffold)*

> **Scaffold.** This page fixes the eval structure for the RFP demo; the corpus and expected outputs are authored alongside the demo. **Evals are not skills** — runtime config lives in `skills/`.

## Use-case *(to be written)*

Emergent Collect at scale in a different industry — requirements that mutate on learned facts, conditional items, N suppliers, added *live* with no redeploy. Same multi-turn loop as Address, with a smarter per-turn reply. See [RFP demo](../../../spec/demos/rfp-demo.md) for the design narrative.

## Example inputs + expected outputs *(to be authored)*

_No corpus yet._ When authored, each case = an input document/response (`images/…`) with fixed signals → expected disposition/gate, plus expected requirements-list mutation per turn (the emergent-Collect dimension). Mirror the [Address eval](../address/description.md) shape; populate `expected.json` and this table together.

| id (image) | doctype | signals | **expected outcome** | what it proves |
|---|---|---|---|---|
| _tbd_ | _tbd_ | _tbd_ | _tbd_ | _tbd_ |

## Files

- **`expected.json`** — placeholder (`documents: []`); fill with the eval corpus.
- **`images/`** — empty; add synthetic, no-PII example documents.

## Related
- [RFP demo](../../../spec/demos/rfp-demo.md), [evals index](../README.md)
