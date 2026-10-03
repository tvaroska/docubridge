---
type: concept
related:
  - "a2ui-edge.md"
  - "zones.md"
  - "../spec/demos/demo-suite.md"
tags: [bridge]
---

# Frontend

> The process is invisible unless you can *see* it. Three surfaces — one per actor — make the mediation legible on stage. Only **one** is an [A2UI](a2ui-edge.md) host: the **provider portal**, which validates the A2UI protocol — that edge is strictly **Bridge → document provider** (content-not-pixels, [Path B](dual-path.md), for humans without an agent). The two **internal** surfaces (agent console, ops dashboard) are **showcase observability/control**, not protocol: the service agent does **not** speak A2UI — its console just reads the running system over SSE. Their split across the trust boundary still demonstrates the [two-zone model](zones.md).

## The three surfaces (one per actor)

### 1. Processing-Agent Console — *internal*
The counterparty agent's mind, made visible — **and the surface that drives it.** Two jobs:

- **Initiate + index (the driver, as a GUI).** A **"new session from a prepared request"** button starts a run (via a small `console_server` that builds the ADK `App` + `Runner`), and a **session list** shows each run's live status — click one to open it. This is the visual form of the service agent's [initiation surface](service-agent-architecture.md); the scriptable form is the **CLI driver** (`python -m agents.address …`, which drives the golden/eval runs). Neither is an ADK agent — both are `Runner` hosts that fire the opening `CollectRequest` and feed the wake `FunctionResponse` on resume.
- **Per-turn viewer.** For the open session it shows the **ledger it was handed**, its **reasoning**, the **satisfaction check**, and the **requirements list it returns** (`required | optional | satisfied | waived`) with `done`. This is where **sense B** is watchable — the audience sees the *agent* (not the Bridge, and not this console) reject the two-same-issuer set and ask for one more. Sense B itself lives in the [gate node](service-agent-architecture.md), not here. A folded-in **Timeline** shows the turn-by-turn negotiation.

Lives at `frontend/agent-console/` (internal zone, SSE). The session-index overlaps the **Servicer Ops Dashboard** (below) in-flight read-model by design: the console lists *this agent's* sessions to drive them, the dashboard is the operator's cross-exchange window.

### 2. Servicer Ops Dashboard — *internal*
The read-model. Exchanges in flight, each exchange's **classified ledger** filling live, disposition outcomes, and the **HITL** and **escalation** queues (approve/reject, reviewer resolves the mid-confidence bill). The operator's window into a running Bridge. Lives at `frontend/ops-dashboard/` (internal zone, SSE).

### 3. End-user / Provider Portal — *external*
The human counterparty's [A2UI Path-B](a2ui-edge.md) surface: upload a document, see its disposition and what's still outstanding. Lives in the **external zone** — the only surface across the boundary, which is the point. At `frontend/provider-portal/`.

## Presentation modes

- **Split-Screen Theater** (default) — provider portal (left, external) ↔ agent console + ops dashboard (right, internal), the Gateway boundary drawn between them. One story, both zones, the seam visible.
- **Timeline** — the exchange as an event stream (request → arrivals → chase → escalate → deliver); scrub it.
- **Architecture X-Ray** — overlay the live traffic on the component/seam diagram; watch calls light up the extraction seam, scheduler, Gateway.

## Time-warp

A presenter control over the [virtual clock](proactive.md): **fast-forward** an SLA window so `overdue → escalated` fires on cue, **step / pause** through beats, **replay** a run. Turns the demo's slowest, most important proof (proactive follow-up) into a few seconds of stage time. At `frontend/timewarp/` — plain REST, runs locally, not deployed.

## Surface capabilities by demo

| Demo | Surface capabilities exercised |
|---|---|
| Address | all three surfaces + time-warp; ledger fill, sense-B reject, HITL/escalation; the **Architecture X-Ray** overlay |
| Benefits | console **negotiate/bind** controls; dashboard **program comparison view**; portal multi-format quotes; outbound/party-channel views |
| RFP | console shows the **Requirements list being mutated** live; dashboard **carrier × product-line matrix** (overdue/escalated highlight, wrong-doc flags) with a manual **"Chase now"**; portal **carrier checklist** addressed by leg with upload-into-a-row; the matrix is also the consumer read API ([RFP views](rfp-views.md)) |

## Build components

- Processing-Agent Console (`frontend/agent-console/`, internal zone, SSE) — with a `console_server` that builds the `App`/`Runner`, exposes new-session-from-prepared-request + a session index, and streams status. The **CLI driver** (`python -m agents.address …`) is the scriptable sibling for golden/eval runs.
- Servicer Ops Dashboard (`frontend/ops-dashboard/`, internal zone, SSE).
- Provider Portal — A2UI Path-B (`frontend/provider-portal/`, external zone).
- Time-warp presenter control over the virtual clock (`frontend/timewarp/`, plain REST, runs locally — not deployed).
- Terraform to deploy portal (external zone) + console/dashboard (internal zone).

**Shared code and tests.** Each app maps snake_case → camelCase through `@docbride/shared`'s `domain/contract.ts`. Shared theme/Panel/Badges/`domain/rfp.ts` live in `frontend/shared/`. Each app composes its own view types. Tests: vitest component/domain units in every app and in shared; Playwright e2e (`frontend/e2e/`) stubbed at the network boundary; `tests/address/test_console.py` for `console_server`.

## Related
- [A2UI edge](a2ui-edge.md), [network zones](zones.md), [demos](../spec/demos/demo-suite.md)
