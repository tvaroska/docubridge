---
type: atom
related:
  - "patterns.md"
  - "aggregate-model.md"
  - "disposition.md"
  - "long-running.md"
  - "collect-scenarios.md"
tags: [bridge]
---

# Collect (casefiles)

> Collect breaks the "one exchange ≈ one document" assumption: a casefile assembles a **set** of documents against a requirements list — and the **app decides** what's needed and when it's complete. New in the ADK design: the Bridge no longer sits completeness-blind — it makes a **best-effort** proposal from the skill's satisfaction description and chases the delta — but it holds **no final word**; the app still decides. It runs as a **multi-turn conversation**, not a workflow: no DSL, no engine.

**The loop is the mechanism.** The exchange is already a durable multi-turn A2A conversation ([context → task → messages](aggregate-model.md)). Collect just rides it — every judgment is a *turn*, not code:

```
Bridge: collect → parse/classify → disposition (per doc) → update ledger
Bridge: best-effort read of the skill's rule → propose what's still outstanding
Bridge → app:  Collection status + "still needed / looks done"  (advisory)
app  → Bridge: the decision — requirements list + done?         (app owns this)
Bridge: chase the outstanding items ─┐
        ▲────────────────────────────┘  repeat until the app says done
```

**The loop is idle-driven over long horizons.** A real casefile fills over days or **weeks**, so the loop is not a process that stays running — the continuation lives in the durable exchange, and each turn is triggered by a party arrival, a [clock alarm](proactive.md), or an HITL resume. Because the per-turn reply (`next_requirements`) is a pure function of `(status, ledger)`, neither side needs to *sit* in the loop between turns. See [long-running collection](long-running.md). On the calling side the loop runs as a [native `RemoteA2aAgent` consumer](a2a-consumer.md) that pauses on `INPUT_REQUIRED` between turns rather than blocking. The Address loop runs as a durable **`google.adk.workflow.Workflow`** graph (collect node → deterministic **`is_satisfied`** gate → present, looping back to collect if not done), with one durable exchange **`context_id`** threaded across rounds on the shared durable session.

**The Requirements payload is a list, not a language.** Each turn the agent returns a flat, fixed-schema to-do list — **data, not rules**:

```
requirements:
  - { item: "proof of identity", status: required, doctype_hint: gov-id }
  - { item: "proof of address",  status: required }
  - { item: "bank reference",    status: optional }
done: false
```

The item shape is fixed: `item`, `status`, and optionally `party`, `doctype_hint`, `reason_code` + `message`. `party` names whose response the item awaits — over N suppliers a requirement is party × item, so one list covers every leg. `reason_code` + `message` say why an item stands where it does (the same relay pair the ledger uses). A `CollectRequest` carries the turn's list plus a due date, which anchors the [chase ladder](proactive.md).

The status vocabulary is the whole language: `required | optional | satisfied | waived`. This stays DSL-free because **conditionals are never encoded** — "self-employed → tax return" is *reasoned* next turn once the fact is learned from the parsed ledger, not a Bridge-evaluated trigger. The same holds for the skill's satisfaction rule: it is a **natural-language description** the interpreting agent reasons over, **not a formal rule language** — so the Bridge's best-effort completeness (below) keeps the loop DSL-free.

**The proposal, concretely.** The Bridge's best-effort completeness proposal rides `CollectionStatus.outstanding` (a `RequirementsList`) in the same vocabulary: `required` (still needed — the message says what, e.g. a bill from a different issuer), `optional`, `satisfied`; the Bridge never proposes `waived` (that is the app's call). "Looks done" = at least one item and nothing `required`. `done` is **always** false — the app decides. A model reads the process skill's prose satisfaction description (`bridge-satisfaction`) against the turn's classified ledger (doctype, disposition, canonical issuer — never the extracted field values); its proposal **replaces** the per-entry list (one `required` item per non-accepted entry) when the assessment succeeds and **falls back** to it otherwise (no description, assessor disabled, model failure or malformed output) — never worse, never a failed turn. It is scoped to the turn's ledger, applies to the deterministic legs (Collector and Path-A turns), and is opt-in (`BRIDGE_SATISFACTION=llm`); off, every structured turn stays model-free.

**Two versioned artifacts, two owners:**

| Artifact | Owner | Source of truth for |
|---|---|---|
| **Requirements** | **app** — decides *what's needed* and *is it done* (the Bridge proposes a best-effort list from the skill's satisfaction description, but never enforces) | *what's needed* and *is it done* |
| **Collection status** | Bridge | *what we have* — the classified ledger: per-doc state, rejections, outstanding, and the best-effort "still needed" proposal |

**The Sense A/B line, precisely.** The Bridge says "this parsed doc is a valid `gov-id`" (sense A); deciding "that `gov-id` satisfies *proof of identity*, and the set is now complete" is sense B. Sense A is **always** the Bridge, and authoritative. Sense B — completeness of the set — **the app decides**; the Bridge now makes a **best-effort** assessment from the skill's prose rule (proposing what's outstanding, chasing) but holds **no final word**. So the Bridge maps docs → doctypes authoritatively, *proposes* doctypes → `done`, and leaves the call to the app rather than guessing. It classifies, chases, dispositions, and reports live throughout ([disposition](disposition.md)).

**`required` / `optional` is a chase knob, not a judgment.** Those statuses tune the Bridge's mechanical behavior — chase `required` hard on the SLA, nudge `optional` gently or not at all, never block on it. Sense A on the Bridge side, driven by the Bridge's best-effort read of the skill's rule and refined by the app's decision.

**Consequences.** Intake gains **classification** (identify an unlabeled doc's doctype against the candidate label space, or flag it wrong/unrequested) before extraction; the demo is a [process skill](skills.md) whose pattern is Collect and whose candidate doctypes make the requirement a *set*.

**Bounded vs emergent — the two forms set *how much the Bridge can pre-chase*.** The *bounded* form is **Address**: a fixed **alternative-satisfaction** rule — prove an address with either one government ID or two bills from different companies — over a candidate-doctype label space, with **no Bridge-owned typed slots**. Because the rule is known up front it lives in the **skill** as prose, so the **Bridge can best-effort completeness** (the OR, the count-to-two, the distinct-issuer check — sense B) over its own **classified ledger** and chase the missing bill hard — but the **app still decides done**. The *emergent* form — requirements that mutate on learned facts, conditional items, N-supplier scale, a new industry, added live — is **RFP**; there the rule **can't be pinned up front**, so the Bridge's best-effort is thinner and the **app carries more of the reasoning**, supplying the mutating requirements list turn by turn. The decision owner never changes (the app); what shifts is how much the Bridge can pre-chase. The emergent form generalizes the bounded slice; it does not invent the pattern.

**Address and RFP are the *same* loop.** Both are exactly the multi-turn conversation above — the only difference is *how much the skill pins down up front*: Address gives a **fixed** rule the Bridge can best-effort and chase hard; RFP gives an **emergent** one the app mutates turn by turn. Either way the **app decides done** and the **Bridge code is the same** — the reusability thesis without a DSL. `Request (pull)` is just a one-turn Collect (one item, no follow-up).

## Related
- [patterns](patterns.md), [aggregate model](aggregate-model.md), [disposition](disposition.md), [A2A consumer](a2a-consumer.md)
