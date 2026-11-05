---
type: area
up: "bridge.md"
down:
  - "aggregate-model.md"
  - "patterns.md"
  - "disposition.md"
  - "artifacts.md"
  - "outbound.md"
tags: [bridge, model]
---

# Model — what the Bridge is

> The domain model: the aggregate an exchange is built on, the closed set of flows it runs, the verdict every document receives, the shape documents take on the wire, and the direction the Bridge itself initiates.

This area describes the Bridge independently of any protocol, host or platform service. An **exchange** is one durable A2A context about a *set* of documents; it holds tasks (one per leg or document collection), each task holds messages (one per turn), and the servicer's agent supplies the party reference rather than the Bridge owning a party aggregate. Over that aggregate the Bridge runs a **closed set of built-in patterns** — Request, Negotiate, Deliver, Collect — selected by a process skill, never authored by one; Collect is the multi-turn conversation in which a casefile fills over days or weeks. Every arriving document passes a **deterministic disposition gate** before it counts, travels as a **versioned artifact moved by reference**, and the Bridge's own **outbound** motion — chases, deliveries, dialing counterparties — rides the same A2A vocabulary.

The invariants this area turns on: the Bridge owns **sense A** (is *this document* a valid, accepted instance of a doctype) and the calling agent owns **sense B** (is the *set* complete, which quote to bind); a model may route but never mint an acceptance or a "done"; the requirements payload is a flat list, not a rule language, and there is no workflow DSL; parity between a mock and a real counterparty is terminal-outcome, never ledger-identical; and a reference to a document is not a capability to read it — bytes leave only through a leg-scoped fetch.

## Read next

- [aggregate model](aggregate-model.md) — what the exchange / task / session / party objects are and how their identifiers relate; read this first.
- [exchange patterns](patterns.md) — which flows exist, why the set is closed, and how a process skill selects one.
  - [Collect](collect.md) — how a document *set* is assembled turn by turn, who decides completeness, and what happens on rejection, delay and restart.
  - [long-running collection](long-running.md) — how a task stays open for weeks with no held connection and no process sitting in the loop.
- [disposition](disposition.md) — how one document's verdict is computed, which signals feed it, and where each verdict routes.
- [documents as artifacts](artifacts.md) — how documents are stored, versioned, scoped to a leg and fetched by reference.
- [outbound direction](outbound.md) — how the Bridge chases, delivers and dials counterparties as a first-class A2A client.
