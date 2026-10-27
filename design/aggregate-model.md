---
type: atom
related:
  - "bridge.md"
  - "collect.md"
  - "party-memory.md"
tags: [bridge]
---

# Aggregate Model

> The unit of work is not a document — it's an **exchange**: a durable, multi-turn interaction with a party about a set of documents, made of one-or-more tasks.

**Four levels.** A2A models three (context → task → message); the underlying agent runtime models two (session → invocation). The Bridge binds them so the runtime never has to model the exchange:

| Concept | Realized as | Owner |
|---|---|---|
| **Party** | a stable counterparty reference | Backend — *not* a Bridge aggregate |
| **Exchange** | the A2A context (an aggregate the Bridge stores) | Bridge — groups the tasks |
| **Requirement item** | one slot in a Collect exchange | Agent (what) + Bridge (state) |
| **Task / Artifact** | one runtime session, one per task | Bridge |

> **"Artifact" here is the A2A/task artifact** — a versioned *metadata* payload (a ledger entry, the Requirements list). The document *bytes* are a separate, same-named concept — the ADK document artifact — covered in [documents as artifacts](artifacts.md). Both version; one carries meaning, the other bytes.

**Key invariants.** One task is one runtime session, keyed one-to-one; a task is scoped to the party's counterparty reference (falling back to the exchange context when none is supplied). Each task owns isolated, resumable state, so tasks in one exchange never clobber each other. One turn is one runtime invocation.

**Exchange = the A2A context (1:1).** There is no separate exchange ID. The Bridge mints the context at request time and returns it; both sides name the interaction identically. The A2A context token is the identity; task rows carry it as the grouping key.

**The Exchange is a derived view over its tasks by default; it materializes into a stored record only when genuine exchange-only state appears.** By default the Exchange is realized as a view assembled from the tasks sharing its A2A context — there is no standalone stored Exchange row, and all exchange-level state is computed from the exchange's tasks (one document per task). Bounded Collect (the **Address** case) fits this default: its [bounded Collect](collect.md) keeps only a **classified ledger** of accepted docs — an append-only projection over the exchange's tasks (one doc per task) — with nothing that cannot be derived from the tasks.

The Exchange **materializes into a standalone stored record** only when genuine **exchange-only state** appears that cannot be derived from the tasks: an agent-owned **Requirements artifact** (emergent, agent-mutated Collect), a **process-skill binding** stored on the exchange, **program membership**, or **reopen** state (an exchange resurrected after closure). Materialization is a one-way transition triggered by the first appearance of such state; once materialized, the record is the source of truth for that exchange and the view derives from record + tasks. Materialization must never mint a new id — exchange identity remains the A2A context token throughout. The emergent-Collect **RFP aggregate** is such a materialized record: it is stored through the Exchange store port, keyed by its own handle (the RFP id), carrying the exchange's A2A context token as an indexed, non-unique attribute (never a minted exchange id), and versioned for optimistic concurrency so a write derived from a superseded read is refused rather than lost. An RFP opened over the [A2A edge](a2a-edge.md) takes the exchange's A2A context token as its RFP id, so the `ExchangeTurn.context_id` the agent gets back names the record, and a follow-up on the same context addresses it. Bounded Collect (Address) stays a derived view; its default read path is assembled from tasks.

**Party is not an aggregate.** The internal agent already knows its counterparties, so it supplies a stable counterparty reference. Cross-exchange memory keys on that reference, not a Bridge-owned entity — see [party memory](party-memory.md).

**Interrupt/resume is recovered from the persisted session**, not an in-memory map — so multi-day HITL resume is crash-safe.

## Related
- [the Bridge](bridge.md), [Collect](collect.md), [party memory](party-memory.md)
