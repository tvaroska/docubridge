---
type: atom
related:
  - "../design/a2a-edge.md"
  - "../design/a2a-consumer.md"
  - "../design/seams.md"
  - "../design/adk.md"
  - "../design/dual-path.md"
tags: [bridge]
---

# Standardization Rings

> Custom code is sometimes unavoidable. The question is never *whether* to be standard — it's *which layer bends when standard and custom conflict.* Three concentric rings, in strict priority order, answer that. This note is the design-spec home and the place the **Ring 1 wire standard** is written down.

**The rings are concentric layers.** Picture concentric circles: the outer ring touches the widest world, the inner core touches only us. The rule in one line: when standard and custom conflict, **deviate at the lowest layer that will absorb it** — keep the wire (Ring 1) and the seams (Ring 2) standard, and push every deviation into implementation (Ring 3).

## The three rings — priority order

| Ring | What | Who depends on it (blast radius) | Custom-code stance |
|---|---|---|---|
| **Ring 1 — Communication (the wire)** | Canonical A2A + A2UI: JSON-RPC 2.0, spec methods, standard task states, artifacts. Domain types travel **inside** standard parts. | The **ecosystem** — third-party agents we will never see. | Last resort; always a recorded risk. |
| **Ring 2 — Platform services (the seams)** | The seams — Sessions, Task store, Exchange store, Skill registry, Memory bank, Scheduler/Timer, A2A client, Extraction, Email ingress — each typed against the **real** platform ABC where the platform has one; Email ingress is a Bridge-owned external boundary that gets the same seam treatment. | Our **deployment story** — the mock→real / local→GCP swap-is-a-no-op property. | Tolerated only as an *adapter behind the ABC*. See [seams](../design/seams.md). |
| **Ring 3 — Implementation (the constructs)** | `RemoteA2aAgent`, `Workflow`, `AgentTool`, interceptors, encode/decode glue. | Only **us** — maintenance + coupling. | The **preferred sink** for unavoidable custom code. See [adk](../design/adk.md). |

**The rule, one line:** *push every deviation inward.* A shim at Ring 3 that lets Rings 1–2 stay standard is a correct application of this rule, not a violation of the "prefer the platform-native construct" principle ([adk](../design/adk.md)). A deviation that leaks *outward* — e.g. a non-standard field on the A2A wire — is the expensive kind and must be justified as such. The ordering tracks **blast radius**: the standard-ness of each ring protects a wider circle than the one inside it.

This is what makes the design legible rather than hypocritical: unavoidable custom glue — the consumer's send-path request interceptor, resume-detection, the ADK encode/decode unwrapping — lives at **Ring 3**; the seams stay on the real ABCs; the wire stays canonical A2A. Custom code is the principle resolving a conflict correctly, not breaking it.

## Ring 1 — the wire standard

The Ring-1 standard is the **A2A contract between a service agent (consumer) and the Document Bridge**. One `context_id` = the [exchange](../design/aggregate-model.md); each leg = a durable A2A task. The consumer asks once with a structured request; the Bridge answers with a classified ledger and an app-owned requirements view, over standard A2A. The disciplines this contract enforces are **D1–D5**:

- **D1 — Canonical envelope.** Every request/response is a stock A2A `Message`/`Task`/`Artifact`/`Part` over JSON-RPC 2.0 on the standard `a2a-sdk`. No bespoke REST dialect. See [a2a-edge](../design/a2a-edge.md).
- **D2 — Domain inside parts (contract hygiene).** Domain types (`CollectRequest`, `ExchangeTurn{CollectionStatus}`, `RequirementsList`, `LedgerEntry`) travel as **JSON `DataPart`s inside** the standard envelope — the envelope is standard, the domain is the payload. Carries a `contract_version` marker; unknown-field policy is `extra="forbid"` for our two-party edge (a **tolerant reader** is the posture for genuinely external callers); the trust boundary is enforced at the network layer, not by application-level tenant scoping — a party addresses only its own leg's context through the gateway ([zones](../design/zones.md)) — and the one application-level scope is the leg: every artifact fetch is authorized against the leg that asked ([artifacts](../design/artifacts.md)).
- **D3 — Lifecycle state machine.** `SUBMITTED → WORKING → INPUT_REQUIRED (park) | COMPLETED`, using **standard** task states only. In-connection progress streams as `TaskStatusUpdateEvent` with a **non-empty `status.message`** (an empty one is silently dropped).
- **D4 — Durable task + artifact-on-park.** The multi-turn loop is **one durable task that parks and resumes**, not a new task per round (a new-task-per-round model would be a Ring-3 `AgentTool` artifact leaking outward, and is rejected). On park, emit the collected-so-far artifact first, with `last_chunk=true` even on a partial (a partial artifact event streams to the caller but is not persisted, so a downstream gate can't see it otherwise).
- **D5 — Two-owner split.** The **app decides "done"** (`is_satisfied` over the classified ledger — *LLM routes, code decides*); the Bridge never mints completeness via a terminal flag. The Bridge emits its advisory `CollectionStatus` **and** the app-owned `RequirementsList`; consumers select artifact parts **by shape** (status vs requirements key), never by position (`datas[0]` is fragile). Rejection reasons relay as `reason_code` + `message`. See [collect](../design/collect.md), [disposition](../design/disposition.md).

**The one sanctioned Ring-1 deviation.** Using `INPUT_REQUIRED` for *"parked, awaiting a **third party**"* (not "prompt the caller") bends wire semantics an external reader may misread — the most expensive class of deviation under this rule. It is accepted only because it is the sole state that unlocks ADK's native zero-compute pause, and it stays a **tracked risk**. Everything else at Ring 1 stays literally canonical.

**Format is orthogonal to capability.** Whether a request arrives as structured `CollectRequest` JSON or free text is an `input_modes` + content-routing concern on a single per-process `AgentSkill` ([dual-path](../design/dual-path.md)), **not** a second wire dialect or a per-format skill.

## Ring 2 & Ring 3 — pointers

Ring 2 is fully specified in [seams](../design/seams.md) (every seam, two adapters each, one shared test suite). Ring 3 is the construct layer in [adk](../design/adk.md) and [patterns](../design/patterns.md) — where custom code is *meant* to live, priced as internal coupling and watched via the living list of experimental surfaces.

## Related
- [A2A edge](../design/a2a-edge.md) · [A2A consumer](../design/a2a-consumer.md) · [seams](../design/seams.md) · [ADK constructs](../design/adk.md)
