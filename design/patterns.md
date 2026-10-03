---
type: concept
down:
  - "fulfillment-graph.md"
  - "disposition.md"
  - "collect.md"
related:
  - "bridge.md"
  - "skills.md"
tags: [bridge]
---

# Exchange Patterns

> The pipeline is **not one fixed workflow**. Each demo runs one of a few **built-in patterns** — a closed set of flows the Bridge owns in code. The process skill *selects* the pattern; it does not define one.

The **core is the solicited spine** — the servicer always asks first:

| Pattern | Flow | Example |
|---|---|---|
| **Request (pull)** | agent asks → party supplies → normalize → deliver | Address, ID-only branch (CUJ-10) |
| **Negotiate** | agent requests → party responds → revise loop → finalize | Benefits, over a program (CUJ-11) |
| **Deliver** | agent sends → format per channel → confirm | outbound document (CUJ-3) — the [outbound direction](outbound.md) |
| **Collect** | agent sets requirements → Bridge assembles a document *set*, looping until the agent judges it complete | [Address](collect.md) bounded (CUJ-10) · [RFP / casefile](collect.md) emergent (CUJ-13) |
| **Extract** | party pushes → normalize → deliver | cold, *unsolicited* inbound edge (a secondary edge, out of scope — distinct from [outbound](outbound.md)) |

Whatever the pattern, a human-fulfilled step runs the same [extraction graph](fulfillment-graph.md), and every arriving artifact passes [disposition](disposition.md) before it counts.

**The pattern set is closed — there is no workflow DSL.** These few patterns are the domain primitives of document mediation; industries don't differ in *flow*, they differ in *which documents* and *what policy*. So `pattern` is a **selector over a closed set of built-in flows** (code the Bridge team owns), and industry-agnosticism comes from the [skills](skills.md) layered over them — doctype schemas, process policy, candidate doctypes — plus, for [Collect](collect.md) and Negotiate, the **agent's per-turn decisions** in the multi-turn exchange. A new industry is new *skills*, not new *flow*; *policy* lives in the skill. For Collect, the process skill selects the flow and supplies its label space and item key; the emergent Collect aggregate names no skill. If a future use-case ever needs author-defined flow, the design delegates to an existing engine behind a [seam](seams.md) rather than building a bespoke one.

## Host-controlled wiring (no hardcoded in-memory services)

A `bridge/` factory never constructs an `InMemory*` session / task / artifact store
unconditionally — the host owns durability wiring (CLI URIs, seam adapters, env), so an
in-memory default appears only behind a config gate: the injectable `param or InMemory…()`
idiom, or a DSN/env-gated backend swap (an `if`/`else` branch or the fall-through of an
early-return guard clause). An unconditional in-memory store in a core factory silently
puts a per-replica store on the deployed path — the session-affinity trap ([seams](seams.md)).
The fence is `tests/test_service_wiring_guard.py`.

## Read next
- [extraction graph](fulfillment-graph.md) — the extract → gate → disposition pipeline (the spine)
- [disposition](disposition.md) — is this submission acceptable, and where does it route
- [Collect](collect.md) — assembling a document set the agent judges complete

## Related
- [the Bridge](bridge.md), [skills](skills.md), [CUJ catalog](../spec/cujs.md), [outbound direction](outbound.md)
