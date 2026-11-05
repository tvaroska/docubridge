---
type: area
up: "bridge.md"
down:
  - "a2a-edge.md"
  - "a2a-consumer.md"
  - "service-agent-architecture.md"
  - "human-edge.md"
  - "email-intake.md"
  - "rfp-views.md"
tags: [bridge, edges]
---

# Edges — how you talk to the Bridge

> The Bridge is **not a pure A2A server** — it meets parties at their level of digital maturity: an **A2A edge for agents**, a **human edge** for parties with a PDF and no agent, and **email as a transport** into the same collection path.

Parties vary. Some run their own agent; some are a person with a PDF; some answer a solicitation by email. Rather than force everyone onto one protocol, the Bridge exposes two front doors and one transport and normalizes all of them into the same internal task/artifact model, so the servicer's agent gets a uniform interface regardless of who's on the other end. The **A2A edge** is a canonical JSON-RPC server with a dynamic Agent Card; on the calling side a servicer's agent consumes it as a native `RemoteA2aAgent` inside a durable `Workflow` graph (collect → gate → present) — the reusable **service-agent shape**. The **human edge** emits declarative A2UI for upload / free-text / form and renders the operator and presenter surfaces around it. **Email intake** lands a solicited reply create-only and correlates it to its open leg by token. The **RFP views** are the read API an emergent-Collect consumer and its surfaces share.

The invariants: a party responds **indistinguishably to the requester** in one of two fulfillment modes, decided only by what kind of part arrives — a structured assertion is validate-only (Path A), an upload is extracted (Path B); both edges enter through the same trust boundary ([Agent Gateway ingress](zones.md)) and the Bridge stays the source of truth for exchange identity on both; what crosses the A2A edge is standard A2A task status plus a ledger projection — never ADK events, never model text; `INPUT_REQUIRED` is the resumable park signal a consumer turns into a zero-compute pause; and the human edge owns **content and protocol, not pixels** — any host renders it.

## Read next

- [A2A edge](a2a-edge.md) — what the server accepts, how it routes structured vs. text traffic, how Path A and Path B fulfillment differ, and how HITL parks and resumes.
- [A2A consumer](a2a-consumer.md) — how an agent calls the Bridge: `RemoteA2aAgent`, the `input-required` pause, status updates and durable restore.
- [service-agent reference architecture](service-agent-architecture.md) — what the reusable whole-agent shape is: a `Workflow` graph (collect · gate · present) on one durable session.
- [human edge](human-edge.md) — what the A2UI edge emits and ingests, and which surfaces (portal, console, dashboard, time-warp) sit on it.
- [email intake](email-intake.md) — how a solicited email reply is landed, correlated, filed and dispositioned without becoming a third edge.
- [RFP views](rfp-views.md) — what the carrier × product-line read API returns and who consumes it.
