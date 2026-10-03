---
type: atom
related:
  - "demo-suite.md"
  - "../../design/patterns.md"
  - "../../design/a2a-edge.md"
  - "../../design/a2ui-edge.md"
  - "../../design/disposition.md"
tags: [bridge]
---

# Benefits Demo — scenario

> The **depth** demo: a broker's agent gathers group-benefits quotes from **N carriers** and needs **one comparable view** to decide on. This is the [Negotiate](../../design/patterns.md) pattern over a **program** — fan-out → normalize → compare/standard-gap → negotiate → bind → rollup. The payoff is the moment N messy, multi-currency, multi-language quotes render as one apples-to-apples table with the gaps flagged.

## Scenario — group benefits procurement

A benefits broker's agent must source a group health plan for client *Acme Corp* (synthetic) from **four carriers** in different regions. It opens one **program** and fans out one leg per carrier — `request benefits-procurement for program:acme-2026, party:carrier-{a,b,c,d}`. Each leg is its **own [context + party](../../design/aggregate-model.md)**; competitors never share a context ([isolation by per-leg addressing](../../design/a2a-edge.md)). The agent asks once per carrier and waits for a single normalized comparison.

| Beat | What happens | Proves |
|---|---|---|
| 1 · Fan-out | Agent opens `program:acme-2026`, requests a quote on four isolated legs. | program context linking N legs · per-leg party isolation |
| 2 · Mixed responses | **Two carriers answer via their own agents (Path A):** Carrier A's agent is built with **Google ADK**, Carrier B's with **LangGraph** — both respond over A2A (USD). Carriers C, D upload PDFs (Path B) in **EUR / French** and **GBP / German**. | [dual-path](../../design/dual-path.md) at fan-out scale · **A2A framework interoperability** (ADK ↔ LangGraph ↔ the Bridge) · heterogeneous inputs |
| 3 · Normalize | Multilingual extraction + **FX normalization** map every quote to one schema, one currency, one language. | the normalization that makes comparison possible |
| 3b · Hard documents | Carrier C's quote also arrives as a **scanned** PDF with no text layer: an OCR pre-pass reads the pixels while extraction still maps onto the doctype schema, and the scan lands the same normalized quote and gaps as the native PDF. A carrier rate sheet runs to **1000 rows** over two dozen pages: extraction returns every row, in order, and a flagged row is reported at its position in the whole sheet — a truncated answer fails loud, never silently short. | extraction holds on raster and rate-sheet-scale documents · no silent row loss |
| 4 · Compare + standard-gap | The [program comparison view](../../design/a2ui-edge.md) renders four legs side by side; each is flagged against the **program standard** via pack-declared rules (e.g. "deductible ≤ X", "must cover Y") — [sense A](../../design/disposition.md). Carrier B's quote shows a coverage gap. | **the golden payoff** · declared-rule standard-gap (Bridge surfaces, never decides the deal) |
| 5 · Negotiate | Agent pushes back on Carrier B's gap over A2A; **B's LangGraph agent** returns a **revised quote** → a new artifact **version** on the same leg; the comparison updates live. | Negotiate pattern · versioned revise loop · **multi-turn A2A with a non-ADK counterparty** |
| 6 · Bind + rollup | Agent binds the winning leg (**sense B — the deal is the agent's**); the Bridge records per-leg bind → **program rollup** → audit trail. | bind → rollup → audit · the mediator never decides acceptance |

## What we need to make it work

**Skills:**
- **Process skill `benefits-procurement`** — pattern `Negotiate`, policy (SLA, nudges, revise-loop cap), candidate doctypes `[benefits-quote]`, and the **program standard** — a separate declared-standard asset (`assets/standard.yaml`) of rules the comparison flags against, agent-overridable per client.
- **Doctype skill `benefits-quote`** — schema (premium, deductible, coverage limits, exclusions, currency, language, effective dates, product line, commission terms, plus a **rate table** and a **benefit-design table**), prompt, validation.

**Depth capabilities:**
- **Programs** — a provider-facing program context that links N legs and carries the rollup; per-leg isolation enforced by addressing ([Programs](../../design/a2a-edge.md)). Program-scale Gateway addressing.
- **Multilingual extraction + FX normalization** — the [extraction graph](../../design/fulfillment-graph.md)'s translate + convert layer: map every quote to one schema/currency/language.
- **Scanned-document extraction** — an OCR pre-pass in front of the same schema-driven request, so a raster PDF with no text layer extracts against the doctype schema unchanged ([Document AI pre-pass](../../design/docai-prepass.md)).
- **Large-document extraction** — a long document is extracted in page windows and merged in page order, so a rate sheet of a thousand rows returns every row and a truncated response fails loud ([seams](../../design/seams.md)).
- **Declared-rule standard-gap disposition** — flag each normalized quote against the program standard (sense A); routes to negotiation.
- **Negotiate + versioned revise loop** — artifact versioning on the same leg; the comparison recomputes as versions land.
- **Program comparison A2UI view** — N legs side by side, one currency and schema, gaps flagged.
- **Bind → program rollup → audit** — per-leg bind, program-level rollup, an audit trail.

**Simulated provider agents (the interoperability proof):**
- **Framework-diverse counterparties.** The Path-A carriers are stood up as **real A2A agents in the external zone**, each its own A2A client + agent logic responding to the Bridge's [A2A edge](../../design/a2a-edge.md). Deliberately built in **two different frameworks** — Carrier A in **Google ADK**, Carrier B in **LangGraph** — to prove A2A is framework-agnostic: the ADK-built Bridge negotiates, multi-turn, with a **non-ADK** counterparty and never notices the difference. This is the platform-evaluator headline for depth: *interoperability, not just fan-out.*
- **The LangGraph carrier drives the negotiate loop** (beat 5): it holds the coverage gap, receives the pushback over A2A, and returns a revised quote — so the interop proof covers the **multi-turn revise loop**, not a one-shot response. Both simulated agents are demo furniture (like the A2UI reference renderer), not part of the Bridge core.

**Core machinery the demo relies on:** both edges, dual-path, extraction seam (fixture, Gemini, and Gemini with a Document AI OCR pre-pass), disposition gates, HITL, proactive follow-up, seams, network zones.

**Demo data:** **synthetic** quote documents — four carriers, three currencies (USD/EUR/GBP), three languages (English/French/German); the **LangGraph** carrier (B) carries both the deliberate coverage gap and the revise round. Two Path-A structured quotes (A, B), two Path-B PDFs (C, D), plus a **scanned** rendition of Carrier C's PDF (raster, no text layer) and a synthetic multi-page **1000-row rate sheet**. Doubles as extraction fixtures + test corpus. An **FX rate source** (fixed rates for a deterministic run).

**Validation:** golden runs per branch (structured vs PDF intake, each language/currency, standard-gap flag, revise loop, bind + rollup) against fixture and Gemini adapters, **plus a cross-framework interop test**: the same A2A conversation must pass whether the counterparty agent is the ADK or the LangGraph build. The scanned rendition reaches the same normalized quote and gaps as the native PDF; the 1000-row sheet returns every row exactly and in order, and an unwindowed run fails loud rather than returning a short table.

## Design parameters
- **FX rate source** — a fixed rate table drives the deterministic demo and is the normalize oracle; a live rate source is a one-argument swap at the same conversion point.
- **Program standard rules** — a separate declared-standard asset in the process skill, agent-overridable per client.

## Related
- [demo implementations](demo-suite.md), [patterns](../../design/patterns.md), [A2A edge / Programs](../../design/a2a-edge.md), [A2UI edge / comparison](../../design/a2ui-edge.md), [disposition](../../design/disposition.md)
