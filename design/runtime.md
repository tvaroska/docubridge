---
type: area
up: "bridge.md"
down:
  - "adk.md"
  - "ports.md"
  - "zones.md"
  - "proactive.md"
  - "deploy-resume.md"
tags: [bridge, runtime, gcp]
---

# Runtime — how the Bridge runs

> The half that makes the Bridge a **showcase of the Gemini platform**, not a local prototype: the managed services, the trust boundary, and the deploy path are part of what's demonstrated — and part of what a servicer would actually run in production.

This area covers the Bridge as a running system. It is built on **Google ADK** with native constructs wherever a standard one exists — `App`, `Runner`, `LlmAgent`, `AgentTool`, `LongRunningFunctionTool`, the session / artifact / memory services — so a reader who knows ADK already knows how the Bridge works. Every managed-service boundary is a **port** with a local adapter and a GCP adapter designed together and exercised by one shared test suite: sessions, task store, exchange store, skill registry, memory bank, scheduler, outbound A2A client, extraction, email ingress, audit vault, operational store, action inbox, agent gym. The deployment is a **two-zone network model** — external parties enter through the Agent Gateway, the Bridge sits in the DMZ, the internal zone authenticates as workloads — and a durable **Scheduler/Timer** port drives the chase ladder so a stalled request escalates with no process sitting in the loop. Deploying the resumable Collect loop separates **restore** (stock ADK config, deployment-agnostic) from **wake** (a small custom receiver whose home depends on the runtime). Terraform is organized by substrate primitive; the ports provision the platform services on top of it ([ports](ports.md)).

The invariants: the same agent code runs locally and deployed, with only service backends swapped; a `bridge/` factory never constructs an in-memory store unconditionally — the host owns durability wiring, because a per-replica store on a multi-replica substrate is the session-affinity trap; isolation is enforced at the network layer, never by application-level tenant scoping, and a party agent can address only its own leg's context; a GCP adapter refuses an in-memory fallback rather than degrading silently; parity between adapters is terminal-outcome, never ledger-identical; and the Gateway is deployment-only — local dev runs no gateway, so the deploy path carries validated deploy-spec builders instead of a port.

## Read next

- [running on Google ADK](adk.md) — which native ADK constructs the Bridge uses, which surfaces are experimental, and why staying standard pays.
- [managed-service ports](ports.md) — what each of the 13 ports abstracts, what its local and GCP adapters are, how the shared suite proves parity, and which Terraform primitive homes each platform service.
- [two-zone network model](zones.md) — where the trust boundary is, how Gateway scoping and Agent Identity enforce it, and what the reserved party-memory port may and may not do.
- [proactive follow-up](proactive.md) — how reminders, SLA and escalation fire from a durable timer, and what the virtual clock is for.
- [deploying the resumable Collect loop](deploy-resume.md) — how a parked leg survives a restart on Cloud Run or Agent Engine, and where the wake receiver lives.
