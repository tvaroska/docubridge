---
type: concept
down:
  - "address-demo.md"
  - "benefits-demo.md"
  - "rfp-demo.md"
  - "../../design/collect.md"
related:
  - "../../design/bridge.md"
  - "../../design/patterns.md"
tags: [bridge]
---

# Demo Implementations

> **Three demo implementations, one core.** The [Bridge core](../../design/bridge.md) is the independent project; each demo is a **self-contained implementation** — skills plus a thin driver — running against the single deployed core. The showcase narrative **leads with breadth — the reusability thesis**: the same core serves two *different* industries, with a whole demo added live and no redeploy. That breadth is the clearest single proof of what the Agent Platform enables. Benefits is the depth example, not the sole headline.

**Separation of concerns.** The core is independent; the demos are peers to each other and never edit the core — if a demo needs a core change, that change is generic and lives in the core. A **showcase runner** boots one core with all demos loaded. Adding a demo is adding configuration, not forking the platform.

| Demo | Role | Pattern | Scale |
|---|---|---|---|
| **[RFP / procurement](rfp-demo.md)** | **Breadth proof — the headline** | [Collect](../../design/collect.md) (emergent) | N suppliers · added *live*, no redeploy |
| **[Benefits](benefits-demo.md)** | **Depth** — the full arc "one comparable view" | Negotiate | N carriers · 3 formats/languages/currencies |
| **[Address](address-demo.md)** | **Warm-up** — proves the pull spine, two edges + [proactive follow-up](../../design/proactive.md), and a *bounded* [Collect](../../design/collect.md) | Request (pull) + Collect (bounded) | 1 party · gov ID **or** 2 bills from different companies · chase the missing bill → reminder → escalation |

- **Benefits** carries one demo end to end: fan-out → normalize → compare/standard-gap → negotiate → bind. This is where "one comparable view" earns its keep.
- **RFP** proves industry-agnostic reuse — a different industry that generalizes the Collect pattern from bounded (Address) to emergent/agent-mutated at N-supplier scale, config-only. It bears the headline.
- **Address** is the objection-handler at small scale — dual-mode fulfillment on the pull spine, a *bounded* Collect (alternative satisfaction: one gov ID **or** two bills from different companies, with agent-owned completeness — the Bridge best-efforts an advisory assessment), plus the proactive chase→escalate beat end to end. It introduces classification and a classified ledger in bounded form (no typed slots — the completeness *decision* is the agent's, with the Bridge best-efforting an advisory assessment over the ledger). RFP *generalizes* to agent-mutated Requirements over the same pattern.

## One core, many demos at once

> Separate demo implementations risk reading as three apps. They aren't: the separation is at **packaging** time, coexistence is at **runtime**. Three proofs that one deployed core handles every use-case *together*:

- **One Agent Card, all demos.** The [Agent Card](../../design/a2a-edge.md) is generated from *installed* skills — load Address + Benefits + RFP into one core and the card advertises every doctype/process at once: a single agent that speaks all three use-cases.
- **One dashboard, mixed exchanges.** The [dashboard](../../design/a2ui-edge.md) groups by *exchange*, not by demo — an Address request, a Benefits program, and an RFP casefile run **concurrently** on one view, over one shared exchange/task store. One dashboard host serves all three types — `/exchanges` for Address and RFP exchanges, `/programs` for Benefits programs.
- **Live-add without disturbing what's running.** Add RFP *while Address and Benefits exchanges are already in flight* — the new use-case joins the same card and dashboard, no redeploy, no interruption.

The artifact that drives this is the **showcase runner** — not a fourth demo, but the harness that boots one core with all demos loaded and exercises them together (`scripts/run-showcase.sh`; Driver `tests/test_showcase.py::test_exercises_all_three_concurrently_on_one_core`).

Every demo runs on the **deployed GCP path** inside the [network zones](../../design/zones.md) — the managed services and trust boundary are part of what's demonstrated.

## Read next
- [Address demo](address-demo.md) — the bounded-Collect warm-up scenario, beat by beat
- [Benefits demo](benefits-demo.md) — the depth scenario: fan-out → normalize → compare → negotiate → bind
- [RFP demo](rfp-demo.md) — the breadth scenario: emergent Collect, added live, no redeploy
- [Collect](../../design/collect.md) — the RFP demo's pattern (the breadth proof)

## Related
- [the Bridge](../../design/bridge.md), [patterns](../../design/patterns.md)
