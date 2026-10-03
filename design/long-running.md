---
type: atom
related:
  - "a2a-edge.md"
  - "collect.md"
  - "proactive.md"
  - "aggregate-model.md"
tags: [bridge]
---

# Long-running collection (the durable A2A task)

> A real collection is **human-paced** — a party takes days or **weeks** to send what's asked. Over that horizon the exchange cannot be *a thing that is running*: not a held-open connection, not an in-process loop, not in-memory state. It is a **durable A2A task that mostly sits idle**, woken only by two events — a document arriving, or a clock firing. Canonical A2A ([A2A edge](a2a-edge.md)) and the ADK-native runtime give exactly the primitives for that.

## The A2A task *is* the long-running unit

The exchange is the [A2A context](aggregate-model.md); each leg's work is an **A2A task** with a lifecycle (`SUBMITTED → WORKING → INPUT_REQUIRED → COMPLETED/FAILED/CANCELED`). A task is a **server-side resource with no built-in TTL** — it may legitimately stay `WORKING` or `INPUT_REQUIRED` for weeks. Nobody holds it open; it is persisted and rehydrated on demand. This is why we speak canonical A2A rather than a bespoke request/response: A2A already models a task as a long-lived, resumable, independently-addressable resource, and gives us the reconnection verbs a weeks-long window needs.

**The mediation runs on wake, not in a loop.** Between a party's turns the Bridge does *nothing* and costs nothing. Work happens only when woken:

| Wake source | Trigger | Bridge does |
|---|---|---|
| **Party turn** | a document/response arrives inbound on the [A2A](a2a-edge.md)/A2UI edge | one [Collect](collect.md) turn — classify, disposition, update ledger, re-propose outstanding |
| **Clock** | a [Scheduler](proactive.md) alarm fires (SLA cadence/deadline) | advance the ladder `on_track → overdue → reminder → escalated`, record the event, re-arm the next alarm |
| **HITL resume** | a reviewer resolves (a HumanReviewResolution on the same A2A task, or the portal/dashboard resolve) | resume the parked task from its persisted session |

## Three things must survive weeks (and restarts)

Each has a durable home behind a [seam](seams.md), so a weeks-long collection survives process restarts:

| State | Durable home |
|---|---|
| Conversation / turn state, HITL suspension | persisted `DatabaseSessionService` (async sqlite locally, Cloud SQL Postgres deployed) — resume recovered from the session, not an in-memory map |
| Documents (versioned per resubmission) | `GcsArtifactService` |
| SLA timers + follow-up read-model | **Cloud Tasks** alarms + Bridge-owned relational store |

A local adapter (in-memory session/artifact store, an in-process `VirtualClock`) mirrors each seam for development; because it is not persisted it does not survive a restart, which is exactly why the durable adapters are the substrate for a weeks-long horizon. Interrupt/resume is recovered from the **persisted session**, so multi-day HITL resume is crash-safe ([aggregate-model](aggregate-model.md)).

The durable session driver has a hard requirement: `DatabaseSessionService` needs an **async** driver (e.g. `sqlite+aiosqlite:///`, not bare `sqlite:///`, which raises "the asyncio extension requires an async driver"), and `ResumabilityConfig(is_resumable=True)` lives on the `App`, not on a bare `agent=`/`node=` Runner — a durable run must go through the `App` path or a parked leg won't be resumable across a restart.

## Time is driven by the clock, not by anyone waiting

Chasing a silent party is the [proactive follow-up](proactive.md) ladder read from the process-skill SLA policy (cadence, deadline, max nudges, escalation). At weeks scale this is the right mechanism precisely because **a reminder is a scheduled callback, not a held connection**: on GCP a Cloud Tasks alarm fires days apart, does one turn of work, and re-arms — zero compute in between. HITL suspends the same way (`LongRunningFunctionTool`, zero compute).

## How the client learns of progress over weeks

Nobody keeps a stream open for weeks. Canonical A2A decouples "learning of progress" from "holding a connection" via three surfaces:

- **`tasks/get`** — poll status at any point, days later, no live stream needed.
- **`tasks/resubscribe`** — re-attach to the event stream after the inevitable disconnect (a canonical-A2A verb the `a2a-sdk` provides).
- **Push-notification config (webhooks)** — the *correct* long-running answer: the Bridge calls the servicer back on state change instead of anyone polling or streaming. Over a weeks-long window polling is wasteful and streams don't survive, so inbound-triggered push is a supported design capability ([a2a-edge](a2a-edge.md)); `tasks/get` and `tasks/resubscribe` remain the surfaces when a caller prefers to poll or re-attach.

**The caller pauses on the same native construct — for free.** A [native consumer](a2a-consumer.md) (`RemoteA2aAgent`) reads the Bridge's `INPUT_REQUIRED` (the park status the [edge](a2a-edge.md) already emits for a not-yet-complete collection) as a **`LongRunningFunctionTool` pause**: the caller's invocation ends with **zero compute**, the peer `task_id`/`context_id` persist on the [session](aggregate-model.md), and a later `FunctionResponse` (woken by push / a `tasks/get` check) resumes to the same task. So the *same* zero-compute suspend the Bridge uses internally for HITL spans the caller↔Bridge edge too — no second durability model to build. In-connection progress rides standard `TaskStatusUpdateEvent.status.message` (non-empty message required — an empty one is dropped by the SDK). On the hybrid edge an escalate parks INPUT_REQUIRED with lifecycle=escalated (non-terminal; only tasks/cancel terminates).

## The servicer loop is event-driven, not process-bound

An in-process `async` collect loop with a `max_turns` cap cannot span weeks — the servicer process would have to stay alive throughout. Because `next_requirements` is a pure function of `(status, ledger)`, the servicer does not need to *sit* in the loop. The continuation lives in the **durable exchange**, not a process stack:

```
open exchange (persist context) → go idle
  ↑                                   │
  └── woken by push / tasks/get ──────┘  recompute next_requirements → post turn → go idle
```

Same [Collect](collect.md) contract, same `next_requirements` logic — only the loop's *continuation* moves from the process into the persisted task.

## Lifecycle policy

Weeks-scale drives four lifecycle decisions:

- **No TTL, no auto-abandon — the app closes.** There is no wall-clock cap on a task or exchange (weeks are legal by design), and the Bridge never auto-closes a stalled collection. The [escalation ladder](proactive.md) runs its course (`on_track → overdue → reminder → escalated`) and then **holds at `escalated`** — a durable status that surfaces the stall on the read-model / dashboard escalation queue, taking no terminal action. **Terminal close is the app's explicit `cancel_task`** (→ `CANCELED`), keeping terminal authority with the app, parallel to sense-B completeness ownership. There is no `abandoned_sla` state and no timer on the aggregate. Trade-off, stated plainly: a stalled leg can remain open indefinitely at `escalated` — the safeguard is **visibility, not auto-expiry**; the durable `escalated` status is load-bearing (it must persist across restarts and drive the read-model, since it is the only signal a weeks-long collection has stalled).
- **Context lifetime is decoupled from credential lifetime.** The A2A context identifier is durable and long-lived (no expiry, subject to the no-TTL rule above). Access is re-authorized **per inbound turn** at the Agent Gateway ([zones](zones.md)), scoped to the leg's context, not carried as a long-lived secret. The out-of-band link handed to a party resolves to a leg and is renewable / re-issuable **without orphaning the context**. No long-TTL bearer token may be the sole key to a weeks-long exchange; credential expiry is a recoverable event (re-issue the link), not a terminal one.
- **Inbound push notification is a supported capability.** Over a weeks-long window the servicer registers a webhook (standard A2A `PushNotificationConfig`) and the Bridge calls it back on task-state change, instead of anyone polling or holding a stream. This is the servicer opting in to callbacks about *its own* exchange — distinct from the Bridge acting as an outbound *client* (Agent-Card discovery, dialing counterparties), which is a separate concern. `tasks/get` and `tasks/resubscribe` remain available for callers that prefer to poll or re-attach.
- **Artifacts are retained via the backend's object-lifecycle policy.** Artifact versions (resubmissions) are retained for at least the life of the exchange, and beyond terminal state by a **backend-enforced retention window** — a `Gcs` object-lifecycle policy on deploy, **not bespoke Bridge GC**. The architecture guarantees the mechanism; the concrete post-terminal window (and any compliance floor) is a per-deployment concern **set at deploy time**, not fixed in the architecture.

## Related
- [A2A edge](a2a-edge.md), [A2A consumer](a2a-consumer.md), [Collect](collect.md), [proactive follow-up](proactive.md), [aggregate model](aggregate-model.md)
