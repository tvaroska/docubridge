# Roadmap

The high-level milestone view of the build. Each milestone is defined by **what it proves on stage** (the demo beat / [CUJ](../spec/cujs.md) it unlocks), not by task detail — that lives in [`TODO.md`](../TODO.md), and the per-feature breakdown in [`features.md`](features.md).

The core commitment: **one deployed core on the Gemini platform running Address, Employee Benefits, Emergent RFP, and the 6-Zone Financial Services & Regulated Industry Enterprise Showcase (`LOAN-MERIDIAN-001`, `MORTGAGE-WESTBROOK-2026`, `terrepower-coastal-2027`) end-to-end.**

> _Current focus (see [`TODO.md`](../TODO.md) for live status):_ Cycle 1 milestones **M0–M7 are complete** — M7 ("It's deployed") closed 2026-10-01 — and the Benefits cycle (CUJ‑3/11/12), Emergent RFP cycle (CUJ‑9/13), Hybrid `document_bridge` LlmAgent edge (off-script routing, real-erase deletion, HITL park/resume), and the 6-Zone Enterprise Data Architecture + Agent Gym + Agent Gateway showcase (`v1.14.0-agent-gateway`) are all built and verified.

## Cycle 1 milestones — Core + Address

| # | Milestone | Proves | Unlocks (CUJ) | Phase |
|---|---|---|---|---|
| **M0** | Skeleton stands up | Repo, contracts, and the shared port harness exist; `bridge/` can't import `agents/` | — | P0 |
| **M1** | The Bridge judges one document | Sense-A disposition is deterministic and auditable; the LLM can't mint an acceptance | 1, 2 (core) | P1 |
| **M2** | Agents can talk to the Bridge | Canonical A2A wire + a stable mock double; domain rides inside A2A parts | 1, 2 (wire) | P2 |
| **M3** | Address collects, end-to-end | The reusable `Workflow` shape works; **code, not a model, mints "done"** | 1, 4, 10 | P3 |
| **M4** | It survives time | Park → restart → resume with zero compute; human-in-the-loop pause | 5, 7 | P4 |
| **M5** | It chases | The clock-driven follow-up ladder fires a **real** nudge — "mediation, not extraction" | 6 | P5 |
| **M6** | It's visible & human-usable | The three surfaces make the process legible; a human uploads via the portal; a doctype is added live | 2, 8 | P6 |
| **M7** | It's deployed | The same golden runs reproduce on GCP; the platform showcase is real, not a toy | 1, 4, 6, 7, 10 (deployed) | P7 |

### The headline demo beat (M3–M5)
Jordan Lee is asked for proof of address, submits two bills from the **same** company → the *agent* (not the Bridge) rejects the set and asks for one more; a second **distinct-issuer** bill arrives → satisfied → one clean normalized artifact is delivered. A blurry bill triggers resubmission; an expired ID routes to a human; a silent party gets chased on the SLA clock and escalates. All from a single "asks once" request.

## Definition of done — Cycle 1

- Every [`features.md`](features.md) item marked `C1` behaves as its acceptance line states.
- The Address eval corpus (`wiki/evals/address/expected.json` + `timeline.json`) reproduces on **fixture and Gemini** extraction adapters, against **mock and real** Bridge, **local and deployed**.
- CUJ‑1, 2, 4, 5, 6, 7, 8, 10 pass. Park/resume survives a real process restart. The proactive chase fires a real send.
- The port suite passes with `BRIDGE_TEST_GCP=1`; plan-time Terraform tests pass with no credentials.

## Beyond Cycle 1

| Cycle | Adds | CUJ |
|---|---|---|
| **Benefits** | Negotiate over a program, FX normalization, ADK↔LangGraph A2A interop, Bridge-as-client dial-out + Deliver | 3, 11, 12 |
| **RFP** | Emergent Collect (agent-mutated requirements, N suppliers), email reply intake, live use-case-add | 9, 13, 15 |
| **Later swaps** | Cold unsolicited inbound push (the Extract edge). Both Gemini platform Agent Engine migration steps are built: Option A (Agent Engine Sessions, Memory Bank, and ADK `App` runtime) and Option B (`BridgeA2aAgentEngine` hosting the A2A JSON-RPC server, `CollectExecutor`, and dynamic Agent Card on Agent Engine behind `BRIDGE_AGENT_ENGINE_A2A=1`) | — |

The architecture is whole from day one — outbound port, trust boundary, contracts — so these cycles add demand-driven capability, not new machinery. See [layout & scope](../design/architecture.md) and [open questions](../spec/open-questions.md).

## See also
- [`TODO.md`](../TODO.md) — the detailed phase plan · [`features.md`](features.md) — the feature catalog · [the Bridge](../design/bridge.md) — design source of truth
