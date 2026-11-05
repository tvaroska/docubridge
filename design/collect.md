---
type: atom
up: "patterns.md"
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

## Scenarios — rejection · delays · keeping context

> Concrete walkthroughs of the loop's three hard cases: **who rejects a document**, **how delays are handled**, and **how context is kept** across turns, restarts, and weeks. Each is a *turn* on the durable exchange, not an engine step. This section is the "what actually happens" companion to the [consumer wiring](a2a-consumer.md) and [disposition](disposition.md) atoms.

### The cast, and the two decisions (read this first)

Nothing below makes sense without the **two-decision split** — it is *the* invariant Collect turns on:

| Decision | Sense | Who owns it | Mechanism | Model may mint it? |
|---|---|---|---|---|
| Is *this document* valid / accepted? | **A — disposition** | the **Bridge** | `run_disposition_gate` (deterministic, per doc) | **never** |
| Does the set on hand satisfy the *declared strategy*? | **A′ — sufficiency verdict** (ADR-14; built by `P9-4`) | the **Bridge** — a verdict with evidence, from `skills/<process>/assets/strategy.yaml` plus request-level requirements | deterministic strategy evaluation over the ledger; `sufficiency = {satisfied, strategy_revision, evidence[], outstanding[]}` on every turn | **never** |
| Is the *requirement set* complete ("done")? May an item be *waived*? | **B — completeness** | the **app** (address agent) — accepts the verdict or overrides it; alone sets `waived` | `is_satisfied` (deterministic, over the ledger and the verdict) | **never** |

"LLM routes, code decides" applies to **all three** — the model may *route* (call the gate, chase the next proof) but can never *mint* an acceptance, a sufficiency verdict, or a "done". Keep them distinct: **the app never rejects a document, the Bridge never declares the set complete, and the Bridge never waives.** Until `P9-4` lands, the A′ row is realised as today's advisory proposal (above) and the strategy lives in app code; the ownership is decided, the mechanism is in progress.

Identifiers ([aggregate-model](aggregate-model.md)): **`context_id`** = the exchange (durable, 1:1 with the casefile) · **`task_id`** = one leg/document collection · **`message_id`** = one turn. Keeping context = keeping the `context_id` alive.

### Scenario 1 — A document is rejected

**Who rejects: the Bridge, always (Sense A).** A rejection is a `disposition` on a ledger entry, not an act of the app. The address agent has *no reject verb*; its only lever is Sense B — keeping a requirement **outstanding**.

```
party → Bridge:  utility bill (blurry scan)
Bridge:          parse/classify → run_disposition_gate → REJECTED (illegible)
Bridge:          ledger entry {doctype: utility-bill, disposition: rejected, reason: illegible}
Bridge → app:    ExchangeTurn{ status: { ledger:[…rejected…], outstanding:[utility-bill], terminal:false } }
app  (is_satisfied): only ACCEPTED entries count → still not done → keep utility-bill outstanding
app  → Bridge:   chase the outstanding proof   (a resume turn, same context_id)
```

The app runs `is_satisfied`, which counts **only `ACCEPTED`** entries — a rejected doc simply doesn't advance completeness, so the loop chases again. No app-side rejection logic exists or is needed.

Three sub-cases that are easy to conflate:

- **Rejected → resubmit** (a *fresh document* is awaited). `resubmit` is **deliberately non-resumable** — it does not park awaiting a *human decision*; it awaits a *new artifact* from the party. The leg's `task` stays open/outstanding; the next party arrival with a better scan is a new turn on the same `context_id`.
- **Escalation ≠ rejection.** An escalation (e.g. suspected fraud, ambiguous issuer) routes to a human/HITL path; it is **not** a terminal reject and must not be collapsed into one. The document is neither accepted nor discarded — it's parked for a decision (see Scenario 2, park).
- **Terminal reject of the *exchange*.** Distinct from a per-doc reject: the *app* may stop the whole exchange (give up) by declining to keep chasing — but that is a Sense-B "not done, stopping", still not a Sense-A document verdict. Parity is asserted on the **terminal reason + accepted-issuer set**, never a ledger-step match (mock↔real).

> **Trap:** "the address agent rejected the document." It didn't — it can't. Restate as *"the Bridge dispositioned the document REJECTED; the app kept the requirement outstanding."*

### Scenario 2 — Delays

A casefile fills over [days or weeks](long-running.md). Which delay mechanism the caller gets is **selected by the Bridge's task status**, not chosen by the consumer:

| Timescale | Bridge emits | Consumer behavior | Wire |
|---|---|---|---|
| seconds–minutes (connected) | `working` + non-empty `status.message` | streamed **progress** (thought events); connection held (≤ client `timeout`, 600 s) | `message/stream`, `TaskStatusUpdateEvent` |
| hours–weeks (idle) | **`input-required`** (park) / `auth-required` | native **pause** — invocation *ends*, zero compute; resumes on a later turn | `input-required` → `LongRunningFunctionTool` pause |
| after disconnect | (unchanged) | **re-attach / snapshot** | `tasks/resubscribe`, `tasks/get` |
| weeks-scale wakeup | terminal or park transition | **callback** | `PushNotificationConfig` |
| terminal | `completed` + artifact | deliverable returned | `ExchangeTurn` on `completed` |

**Fast path (mock ~10 s hold):**
```
app → Bridge:   message/send (CollectRequest)
Bridge:         Task{submitted} → working ("Collecting…") → hold → completed + ExchangeTurn
app:            is_satisfied → done → present
```

**Slow path (party is slow — chase then park):**
```
app → Bridge:   collect proof of address
Bridge:         working ("Follow-up sent: statement overdue.")     ← faked chase (mock) / real chase
Bridge:         working ("Reminder sent — awaiting a 2nd issuer.")
Bridge → app:   input-required  ("Awaiting additional proof")       ← PARK: invocation ends, 0 compute
                … hours/days pass; no process sits in the loop …
party → Bridge: second utility bill arrives                         ← the WAKE is a party turn
Bridge:         disposition ACCEPTED → completed + ExchangeTurn
app (resumed):  is_satisfied → done → present
```

Key points:
- **What wakes a parked leg is a *party turn to the Bridge*** (or a [clock alarm](proactive.md) / HITL resume), **not** the app polling. The app's resume is often an empty ack ("keep going"), not new data — the deliberate overload of `input-required`.
- **Progress must carry a non-empty `status.message`** — an empty status update is silently dropped by ADK (`a2a-sdk` 1.x gotcha, [a2a-consumer](a2a-consumer.md)).
- **Timeout is a chase, not a failure.** A party missing a deadline produces another chase turn / a park, never a Sense-A reject.

### Scenario 3 — Keeping context

"Keeping context" = every turn continues the **same `context_id`** so the ledger accumulates. The Bridge is the stateful aggregate (keyed on `context_id`, cumulative ledger); the *consumer* must re-send under that same id. The durable `Workflow` graph holds context at three scales, in increasing durability:

**Across rounds within one invocation.**
The graph runs the Bridge (`RemoteA2aAgent`) as a **collect node** on the **shared, durable session**. Because collect and the gate share one `InvocationContext`, `RemoteA2aAgent` sees its own prior event and **reuses the `context_id` natively** across rounds — no hand-threading. The `task_id` is *not* reused in the completing (`park=false`) path — each round opens a new task under the same context (re-sending to a *completed* task is invalid A2A).

**Across a park/resume on the shared session.**
A real `input-required` **park/resume** works on this shared durable session: the pause suspends the graph and resumes from saved node state. The parked A2A `task_id`/`context_id` persist on the session (they ride `event.custom_metadata`), and an id-matched `FunctionResponse` resumes the same task.

**Across a process restart (weeks-scale).**
Restore is *mostly platform-default* — see [a2a-consumer](a2a-consumer.md) "Durable state restore":
- **Platform-default stores:** persistent `DatabaseSessionService` (session state + events) + `DatabaseTaskStore` (task + artifacts + `context_id`); the Runner auto-matches a `FunctionResponse` to the paused call and rebuilds the invocation.
- **Resumability switch:** `ResumabilityConfig(is_resumable=True)` — the switch that makes a park survive a restart. It lives on the `App`, so a durable run goes through the `App` path.
- **The wake:** a webhook receiver + a `task_id`→session index. The webhook is a *doorbell, not a restore* — it delivers only "task X changed"; it neither carries the caller's state nor puts the agent back where it paused. The restore is done by the platform-default stores; the wake just tells the receiver to resume.

```
[week 1] app parks the leg   → session + task persisted; context_id on both
[process dies / redeploys]
[week 3] party submits doc   → Bridge task → completed  (push doorbell)
          receiver: task_id → (user, session) → build FunctionResponse → run_async(...)
          Runner: reload session, re-match paused call, resume → is_satisfied → done
```

> **Contract trap (accumulation).** `is_satisfied` reads the ledger the Bridge returns and assumes it is the **full accumulated set**, not a per-turn delta. A Bridge that returned a delta would make the gate under-count. Keep the "ledger is cumulative per `context_id`" contract explicit — the gate overwrites, it does not append.

### Invariants these scenarios must never violate

- The **app never rejects a document** (Sense A is the Bridge's); the **Bridge never declares the set done** (Sense B is the app's).
- A **model may never mint** an acceptance or a "done".
- **Escalation ≠ rejection**; **`resubmit` is non-resumable** (awaits a fresh artifact, not a human decision).
- **Only `ACCEPTED` entries** count toward completeness; issuers are compared **already-canonical** (`"Power Co."` → `power-co`).
- **Parity is terminal-outcome** (terminal reason + accepted-issuer set), never a ledger-step match — the mock is a permanent contract double, not a throwaway.
- **The classified ledger has no timestamp** — persistent adapters need a deterministic sort key ([aggregate-model](aggregate-model.md)).

## Related
- [patterns](patterns.md), [aggregate model](aggregate-model.md), [disposition](disposition.md), [long-running collection](long-running.md), [A2A consumer](a2a-consumer.md) (wiring, durable state restore)
