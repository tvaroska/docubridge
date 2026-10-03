---
type: atom
related:
  - "collect.md"
  - "disposition.md"
  - "long-running.md"
  - "a2a-consumer.md"
  - "aggregate-model.md"
tags: [bridge]
---

# Collect scenarios (rejection · delays · keeping context)

> Concrete walkthroughs of the [Collect](collect.md) loop's three hard cases: **who rejects a document**, **how delays are handled**, and **how context is kept** across turns, restarts, and weeks. Each is a *turn* on the durable exchange, not an engine step. This note is the "what actually happens" companion to the [consumer wiring](a2a-consumer.md) and [disposition](disposition.md) atoms.

## The cast, and the two decisions (read this first)

Nothing below makes sense without the **two-decision split** — it is *the* invariant Collect turns on:

| Decision | Sense | Who owns it | Mechanism | Model may mint it? |
|---|---|---|---|---|
| Is *this document* valid / accepted? | **A — disposition** | the **Bridge** | `run_disposition_gate` (deterministic, per doc) | **never** |
| Is the *requirement set* complete ("done")? | **B — completeness** | the **app** (address agent) | `is_satisfied` (deterministic, over the ledger) | **never** |

"LLM routes, code decides" applies to **both** — the model may *route* (call the gate, chase the next proof) but can never *mint* an acceptance or a "done". Keep A and B distinct: **the app never rejects a document, and the Bridge never declares the set complete.**

Identifiers ([aggregate-model](aggregate-model.md)): **`context_id`** = the exchange (durable, 1:1 with the casefile) · **`task_id`** = one leg/document collection · **`message_id`** = one turn. Keeping context = keeping the `context_id` alive.

---

## Scenario 1 — A document is rejected

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

---

## Scenario 2 — Delays

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

---

## Scenario 3 — Keeping context

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

---

## Invariants these scenarios must never violate

- The **app never rejects a document** (Sense A is the Bridge's); the **Bridge never declares the set done** (Sense B is the app's).
- A **model may never mint** an acceptance or a "done".
- **Escalation ≠ rejection**; **`resubmit` is non-resumable** (awaits a fresh artifact, not a human decision).
- **Only `ACCEPTED` entries** count toward completeness; issuers are compared **already-canonical** (`"Power Co."` → `power-co`).
- **Parity is terminal-outcome** (terminal reason + accepted-issuer set), never a ledger-step match — the mock is a permanent contract double, not a throwaway.
- **The classified ledger has no timestamp** — persistent adapters need a deterministic sort key ([aggregate-model](aggregate-model.md)).

## Related
- [Collect (casefiles)](collect.md) · [disposition (Sense A)](disposition.md) · [long-running collection](long-running.md)
- [A2A consumer](a2a-consumer.md) (wiring, durable state restore, the three wirings) · [aggregate model](aggregate-model.md)
