---
type: concept
down:
  - "a2a-edge.md"
  - "a2ui-edge.md"
  - "dual-path.md"
related:
  - "bridge.md"
  - "zones.md"
tags: [bridge]
---

# Two Edges

> The Bridge is **not a pure A2A server** — it meets parties at their level of digital maturity through two edges: an **A2A edge for agents** and an **A2UI edge for humans**.

Parties vary. Some run their own agent; some are a person with a PDF. Rather than force everyone onto one protocol, the Bridge exposes two front doors and normalizes both into the same internal task/artifact model — so the servicer's agent gets a uniform interface regardless of who's on the other end.

- **[A2A edge](a2a-edge.md)** — for agent counterparties (and the internal servicer agent). Task lifecycle, artifact versioning, event streaming, a dynamic Agent Card.
- **[A2UI edge](a2ui-edge.md)** — for parties without an agent. The Bridge emits declarative A2UI and ingests a structured response back. It owns **content and protocol, not pixels** — any host renders it; the reference renderer is demo furniture.

The choice is invisible to the requester: a party responds indistinguishably in one of two fulfillment modes, decided only by what kind of Part arrives — see [dual-path fulfillment](dual-path.md).

Both edges enter through the same trust boundary — [Agent Gateway ingress](zones.md) — and the Bridge stays the source of truth for exchange identity on both.

**Email is a transport, not a third edge.** A carrier answering a solicited request by email reaches the same collection path: [email intake](email-intake.md) lands the message create-only, correlates it to its open leg by token, and files the attachments through the same disposition gate as `ArtifactRef`s by reference. Its webhook is the one public surface outside the Gateway (Graph cannot present a Google identity) and authenticates each notification by the subscription's clientState.

## Read next
- [A2A edge](a2a-edge.md) — the agent-facing server
- [A2UI edge](a2ui-edge.md) — the human-facing declarative surface
- [dual-path fulfillment](dual-path.md) — Path A validate-only vs Path B extract

## Related
- [the Bridge](bridge.md), [network zones](zones.md)
