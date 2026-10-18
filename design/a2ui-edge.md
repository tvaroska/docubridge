---
type: atom
related:
  - "edges.md"
  - "fulfillment-graph.md"
tags: [bridge]
---

# A2UI Edge (humans)

> The human-facing front door — for parties without an agent. The Bridge emits declarative **A2UI** and ingests a structured response back.

**Content and protocol, not pixels.** The Bridge describes *what* to show (upload a file, paste free text, fill a form) and *what* it gets back (a structured response), but never *how it looks*. Any host frontend or portal renders the A2UI in its own design system. A reference renderer is demo furniture — it is not the product.

**What it powers.** The A2UI edge is how [Path B fulfillment](dual-path.md) works: a human uploads a PDF, pastes text, or fills a form; the Bridge feeds it into the [extraction graph](fulfillment-graph.md). The same surface pattern renders the other human-facing views:

- **HITL review** — a reviewer works flagged extractions side-by-side with the source.
- **Party status** — "what you sent, what's accepted, what's still missing, what's next."
- **Internal dashboard** — grouped by actionable state, per **exchange**, not per raw task.
- **Program comparison** — N carrier legs side by side, one currency and schema, standard gaps flagged (the golden payoff).

All surfaces are projections of the [read-model](aggregate-model.md) over the task/artifact layer, updated live. The concrete actor frontends, presentation modes, and time-warp surface built on this edge are detailed in [frontend](frontend.md).

**Relaying agent-authored explanations.** The party needs to know not just *what* is outstanding or rejected but *why* and *what to do next* — and that reasoning is a servicing-agent judgment the Bridge holds no authority to author. The servicing agent supplies each explanation as a stable **`reason_code`** (a code-minted machine key, e.g. `unsupported-doctype`) plus a **`message`** (LLM-authored, human-facing prose). The Bridge **relays these into A2UI content without interpreting them** — it maps `{status, message, doctype_hint}` (and, per document, `{disposition, message}`) into a declarative content block (the "party status" view: what you sent, what's accepted, what's still missing, what's next), describing *what* to show but never *how it looks*. The Bridge is transport and layout over the agent's words; it adds no policy and no prose, keying only mechanical chase behavior on `reason_code`. See [Collect](collect.md) for the two-owner artifact split these fields ride on.

## Related
- [two edges](edges.md), [extraction graph](fulfillment-graph.md), [frontend](frontend.md), [Agent Gym & Programmable Trust](agent-gym.md)
