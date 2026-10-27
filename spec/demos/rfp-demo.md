---
type: atom
related:
  - "demo-suite.md"
  - "../../design/collect.md"
  - "../../design/skills.md"
  - "../../design/disposition.md"
tags: [bridge]
---

# RFP Demo — scenario

> The **breadth headline**: a *different industry* running the **emergent** [Collect](../../design/collect.md) — agent-mutated Requirements, conditional items, N suppliers — added **live** to the running core with **no redeploy**. It generalizes the pattern [Address](address-demo.md) runs bounded; it does not invent it. The single clearest proof of the reusability thesis: one deployed core, a whole new use-case uploaded as configuration while other exchanges are in flight.

## What's different from Address

**Same [multi-turn loop](../../design/collect.md), smarter reply.** RFP is the *identical* Bridge machinery as [Address](address-demo.md) — collect → parse → report ledger → agent returns a requirements list → chase → repeat. The only difference is the agent's per-turn reply: Address runs a fixed policy; RFP runs an emergent, mutating one. **No new pattern, no code change** — Collect is a built-in flow, and there is no DSL.

Address is a *bounded* Collect: a fixed satisfaction rule over a **classified ledger**, no persisted agent artifact. RFP is the **emergent** form, and it earns the two versioned artifacts:

| Artifact | Owner | Source of truth for |
|---|---|---|
| **Requirements** | processing agent | *what's needed* and *is it done* — mutated at runtime |
| **Collection status** | Bridge | *what we have* — per-item state, rejections, outstanding |

Here **slots legitimately appear** — they are agent-owned Requirement *items* in the Requirements artifact, not a Bridge invention (contrast [Address's ledger](address-demo.md)).

## Scenario — procurement casefile

A procurement team's agent runs an **RFP** to onboard a new supplier, *Nimbus Logistics* (synthetic). The agent posts an initial **Requirements** set — proposal, pricing sheet, insurance certificate, W-9 — and the Bridge chases, classifies, and reports. The requirement list **grows as facts emerge**.

| Beat | What happens | Proves |
|---|---|---|
| 0 · Live-add | With Address + Benefits exchanges already running, upload the **RFP doctype + process skills** to the core. The [Agent Card](../../design/a2a-edge.md) regenerates; the [dashboard](../../design/a2ui-edge.md) gains RFP exchanges — **no redeploy, no interruption.** | **the reusability headline** · one core speaks a new industry live |
| 1 · Post Requirements | Agent posts the initial Requirements; Bridge opens the casefile and begins chasing outstanding items. | agent-owned Requirements · Bridge-owned Collection status |
| 2 · Messy intake | Nimbus submits a bundle: proposal + pricing, one **unlabeled** scan, and the insurance cert is **the wrong document**. Bridge **classifies** each against the candidate doctypes, matches an open item, normalizes, dispositions per item, flags the wrong/unrequested one. | **open-set classification** · per-item disposition · Collection status live |
| 3 · Emergent requirement | The proposal reveals Nimbus is an **international** supplier → the agent **adds** a tax-residency form to Requirements ("self-employed → tax return" style conditional — the *agent* adds an item, not a Bridge trigger). Bridge chases the delta. | **agent-mutated Requirements** · conditional logic stays sense B |
| 4 · Complete | Nimbus supplies the missing cert and the new form; agent evaluates → waives one nice-to-have, asserts **done** → finalize. | agent judges completeness of a *set* (sense B) and decides done · the Bridge best-efforts an advisory assessment and chases the delta |

## Why it's the headline

Same core serves two different industries; a whole demo added live with no redeploy — the clearest single proof of what the Agent Platform enables. The Terraform delta is ≈ none: a new industry is **skills, not infra**.

## What we need to make it work

**Skills:**
- **Process skill `rfp`** — pattern `Collect (emergent)`, policy, candidate doctypes (the label space for classification). The pattern lives in the skill selection, not new code (see below).
- **Doctype skills** — `rfp-proposal`, `pricing-sheet`, `insurance-cert`, `w9`, `tax-residency`, … each schema + prompt + validation, reusable across processes.

**Breadth capabilities:**
- **The Collect graph** — post Requirements → chase → classify → normalize → disposition per item → update Collection status → agent evaluates → loop on the delta ([Collect](../../design/collect.md)).
- **Two versioned artifacts** — agent-owned Requirements (mutable at runtime) + Bridge-owned Collection status.
- **Open-set classification** — route unlabeled docs to a candidate item, or flag wrong/unrequested.
- **No pattern work.** Collect is a **built-in flow** ([closed pattern set](../../design/patterns.md)); RFP selects it via `pattern` and adds skills. There is no workflow DSL and nothing to "lift into the skill" — "adding a demo is configuration" holds because the flow already exists and the emergent behavior lives in the agent's per-turn reply.
- **Showcase runner** — boots one core with all demos loaded and exercises Address + Benefits + RFP concurrently on one Agent Card and one dashboard — the proof of multi-use-case coexistence.

**Core machinery the demo relies on:** both edges, dual-path, extraction port, disposition, HITL, proactive, ports, network zones, programs.

**Demo data:** **synthetic** supplier bundle — proposal, pricing sheet, a wrong "insurance cert," an unlabeled scan, plus the later tax-residency form. Doubles as fixtures + test corpus.

**Validation:** golden runs for live-add (card regenerates, existing exchanges untouched), messy intake (classification + wrong-doc flag), an emergent-requirement round, and completeness assertion — against fixture and Gemini adapters.

## Design parameters
- **Requirements-list schema** — a small fixed shape the agent returns each turn: per item `item`, `status: required|optional|satisfied|waived`, an optional `party` (whose response it awaits — over N suppliers a requirement is party × item), a doctype hint, and a reason code + message; the list carries the agent's `done`. The request also carries the due date that anchors the chase ladder. Data, not a language (see [Collect](../../design/collect.md)).

## Related
- [demo implementations](demo-suite.md), [Collect](../../design/collect.md), [skills](../../design/skills.md), [disposition](../../design/disposition.md)
