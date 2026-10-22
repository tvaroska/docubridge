---
type: atom
related:
  - "a2a-consumer.md"
  - "long-running.md"
  - "gcp-substrate.md"
  - "service-agent-architecture.md"
  - "seams.md"
tags: [bridge, deploy]
---

# Deploying the resumable Collect loop

> How the durable park/resume path ([a2a-consumer](a2a-consumer.md), [service-agent-architecture](service-agent-architecture.md)) runs on GCP. The governing move is to keep **restore** (come back to the same paused state) separate from **wake** (the trigger that re-enters the loop): restore is stock-ADK config that is deployment-agnostic and runs headless with no HTTP; wake is a small custom piece whose home depends on the runtime. Substrate: [gcp-substrate](gcp-substrate.md).

## The one distinction that decides everything: restore ≠ wake

A parked leg is `input-required` → a `LongRunningFunctionTool` pause → **the invocation ends, zero compute** ([long-running](long-running.md)). Resuming it is two independent problems, and only one of them is deployment-sensitive:

| | **Restore** — return to the identical paused state | **Wake** — the inbound trigger that calls `run_async` again |
|---|---|---|
| What it needs | Persistent Session store (state + events) + persistent Task store (task + artifacts + `context_id`) + `ResumabilityConfig(is_resumable=True)` | A webhook **receiver** for the `PushNotificationConfig` doorbell + a `task_id`→session index, which then calls `run_async` |
| Stock vs custom | **Stock ADK** — plain `google.adk.runners.Runner` + `run_async`; no custom Runner subclass. The only custom bit is the send-path `RequestInterceptor` on `RemoteA2aAgent` (resume re-detection under `Workflow` event ordering) | **Custom** — `a2a-sdk` is sender-only; neither SDK ships a receiver; `adk web` can't host one |
| Deployment-sensitive? | **No** — a config/seam swap, a no-op for the agent | **Yes** — this is where Cloud Run vs Agent Engine actually differ |

Because restore runs with **no HTTP**, the deployment target is not load-bearing for it; the target choice only shapes **where the wake receiver lives**.

## The substrate

The resume path maps onto the GCP substrate ([gcp-substrate](gcp-substrate.md)) as a set of seam swaps ([seams](seams.md)), each a no-op for the agent:

| Seam | Local (dev) | GCP (deploy) | Role in resume |
|---|---|---|---|
| **Compute / runtime** | local process | **Cloud Run v2** edge + **Vertex AI Agent Engine** (`BRIDGE_AGENT_ENGINE_ID`, `BRIDGE_AGENT_ENGINE_REMOTE=1`, `BRIDGE_AGENT_ENGINE_A2A=1`) | hosts the stock `Runner`, remote `ReasoningEngine`, and `BridgeA2aAgentEngine`; scale-to-zero = **zero cost across a weeks-long park** |
| **Sessions** | `InMemorySessionService` → `DatabaseSessionService` (SQLite) | `VertexAiSessionService` on Vertex AI Agent Engine when `BRIDGE_AGENT_ENGINE_ID` is set, or `DatabaseSessionService` → Cloud SQL Postgres (`GcpSessions`; asyncpg DSN from `DATABASE_URL`, in-memory refused) | **restore**: reloads state + event history by `(app, user, session_id)`; peer `task_id`/`context_id` ride `event.custom_metadata`, auto-repopulated |
| **Memory bank** | `InMemoryMemoryService` → `SqliteMemoryService` (`BRIDGE_MEMORY_DB_PATH`) | `VertexAiMemoryBankService` on Vertex AI Agent Engine (`GcpMemoryBank`, `BRIDGE_AGENT_ENGINE_ID`) | durable cross-session ADK `BaseMemoryService` wired into `LlmLeg` |
| **Task store** | `InMemoryTaskStore` → `DatabaseTaskStore` (SQLite) | Cloud SQL Postgres (`DatabaseTaskStore`); a managed task store would be a one-adapter swap behind this seam | **restore**: task + artifacts + `context_id` survive restart |
| **Resumability** | `ResumabilityConfig(is_resumable=True)` on the `App` | same (`@experimental`) | **restore**: the switch that makes a park survive a restart |
| **Scheduler** | virtual clock | **Cloud Tasks** (`bridge-followups` queue) | the clock alarm that can *wake* a leg (SLA/escalation), alongside the party-turn webhook |

> **Naming reconciliation (Option A + Option B).** Vertex AI Agent Engine (`aiplatform.googleapis.com` `ReasoningEngine`) hosts managed `VertexAiSessionService`, `VertexAiMemoryBankService`, and remote `App` execution (`async_stream_query`, **Option A**), as well as the full Bridge A2A JSON-RPC 2.0 server (`BridgeA2aAgentEngine` in `bridge/edge/agent_engine_a2a.py` via `BRIDGE_AGENT_ENGINE_A2A=1`, **Option B**). **Cloud Run v2** remains the thin HTTP/webhook/REST edge for custom non-A2A routes (`wake.py`, A2UI portal, `/artifacts/{context_id}/{name}`, email webhook, RFP board) and proxies `GET /.well-known/agent-card.json` and `POST /` to `BridgeA2aAgentEngine`.

## Restore — works on the target as a config swap

Nothing in restore is bespoke. Deployed, it is the headless resume path with the stores swapped for their managed adapters:

```python
from google.adk.runners import Runner              # stock ADK, no subclass
sessions = get_adapter(Seam.sessions, resolve_mode(Seam.sessions))
runner = Runner(
    app=build_address_app(card_url),               # carries ResumabilityConfig
    session_service=sessions.build_session_service(),  # gcp: DatabaseSessionService → Cloud SQL
    artifact_service=GcsArtifactService(...),
)
await runner.run_async(user_id, session_id, new_message=<FunctionResponse content>)
```

**Asterisk:** `ResumabilityConfig(is_resumable=True)` is `@experimental`. Cloud Run is the runtime. The Bridge's own `App` sets the flag. Guard tests: `tests/test_agent.py::test_build_app_is_resumable_by_default` and `tests/test_llm_leg.py::test_llm_leg_app_is_resumable`.

## Wake — the differentiator

Wake needs two **custom** pieces: a receiver for the A2A push doorbell, and a `task_id`→session index; the receiver builds the `FunctionResponse` and calls the stock `run_async`. **The doorbell delivers only "task X changed" — it neither carries state nor re-enters the loop; restore does that.**

The receiver is `agents/address/wake.py`.

- **Cloud Run.** You own the FastAPI container, so the receiver is just another route. Scale-to-zero means zero compute while parked; the inbound doorbell HTTP cold-starts the container → look up session by `task_id` → `run_async` → resume. A weeks-idle resumable park is what Cloud Run's request-driven, scale-to-zero model is for. It is the runtime.
- **Vertex Agent Engine** was not chosen: its managed runtime exposes no inbound HTTP route you own, so the wake receiver could not live there.

The two custom pieces are the whole of the wake delta; everything downstream of the receiver is stock.

## How the path composes

- **Restore runs headless** via `run_async` — no HTTP, no deploy dependency — so it is exercised independently of any GCP target.
- **Deploying to GCP** is a seam swap: the GCP adapters are selected by `BRIDGE_SEAM_MODE`. The shared suite runs them with `BRIDGE_TEST_GCP=1`. Restore-across-restart is covered in `tests/address/test_park_resume.py` (lines 96, 150) and `tests/test_durable_seams.py` (line 52).
- **Wake** adds the webhook receiver + `task_id`→session index — the only custom deploy surface — so a leg can be woken by an external event, not just resumed by a hand-fed `FunctionResponse`.

## Related
- [A2A consumer](a2a-consumer.md) — the `RemoteA2aAgent` construct, park/resume, and the state-restore breakdown
- [long-running collection](long-running.md) — the idle-driven, weeks-scale lifecycle the park serves
- [service-agent reference architecture](service-agent-architecture.md) — the durable `Workflow` graph being deployed
- [GCP substrate](gcp-substrate.md) · [seams](seams.md) — the managed adapters and the local↔GCP swap discipline
