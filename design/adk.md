---
type: concept
up: "bridge.md"
related:
  - "patterns.md"
  - "disposition.md"
  - "collect.md"
  - "gcp-substrate.md"
  - "../spec/standards.md"
tags: [bridge, adk]
---

# Running on Google ADK — as standard as possible

> The Bridge is the flagship showcase of the **Gemini Enterprise Agent Platform**. So it runs on **Google ADK**, and it uses ADK **the standard way**: every runtime concern maps to a native ADK construct, and we hand-roll nothing the platform already provides. Using the platform natively *is* the demonstration.

## The principle

**Prefer the ADK-native construct over bespoke plumbing — every time.** When a runtime concern (an agent, a pause for a human, a document, a durable session) has a first-class ADK answer, we use that answer as-is. A wrapper we write ourselves both *hides* the platform we are showcasing and becomes ours to maintain. So the default is always the stock construct; a custom layer needs a specific, recorded justification. When standard and custom nonetheless conflict, the [standardization rings](../spec/standards.md) decide *which layer bends* — and the ADK constructs here are **Ring 3**, the innermost ring and the preferred sink for whatever custom code is unavoidable (it costs only us).

Concretely, each concern lands on its native construct:

| Concern | ADK-native construct | Not this |
|---|---|---|
| An agent that reasons | `LlmAgent` | a hand-rolled `async` loop |
| Orchestration / turns | stock `Runner` | a service that hides a runner |
| A capability the agent invokes | a **tool** (`FunctionTool`) | inline branching in app code |
| A sub-capability that reasons | a subagent as **`AgentTool`** | inlining its reasoning into the parent |
| Calling another A2A agent (e.g. the Bridge) | **`RemoteA2aAgent`** (card-configured) | a hand-rolled A2A client + poll loop in a `FunctionTool` |
| Pause for a human | `LongRunningFunctionTool` + id-matched `FunctionResponse` | app-owned task-parking |
| A document (bytes) | **artifact** (`ArtifactService`, versioned) | an inline payload on session state |
| Durable session / state | `SessionService` | a bespoke store adapter |
| Local ↔ deployed | swap the service backend (`InMemory…` ↔ `Database…` on Cloud SQL / `Gcs…`) | a custom durability layer |

The reward for staying standard: the same agent code runs locally and deployed by swapping service backends — the deployed runtime is Cloud Run v2 hosting the ADK `App` (sessions, tasks and exchange records ride Cloud SQL behind their ports; see [gcp-substrate](gcp-substrate.md)) — and a reader who knows ADK already knows how the Bridge works.

## The one inversion we keep — and why it's still standard

The Bridge disposes documents for **KYC/compliance**, so the accept/reject of a genuine candidate document must be **deterministic and auditable** — never a model's improvised call. But real intake is messy: a **deletion request**, a **wrong/unrelated file**, an out-of-scope message. A purely deterministic pipeline can't route those; reasoning can.

The standard ADK shape resolves the tension cleanly:

- The agent is an **`LlmAgent`** — it *routes intent* and handles the messy edges (at the A2A edge: text-only intents, HITL park and review resumes; structured submissions take the deterministic Collector — [a2a-edge](a2a-edge.md)).
- The compliance decision stays a **pure function**, exposed to the agent as an **authoritative tool** (`run_disposition_gate`). The LLM must call it and relay its verdict; **it cannot mint an acceptance.**

This is still just "an agent with a tool" — the most ordinary ADK pattern there is. The LLM sits *in front of* the deterministic gate, not in place of it. Same guardrail as the [Collect](collect.md) loop, where the LLM chooses which document to request but a code gate owns "satisfied."

## The main agent orchestrates; extraction is a subagent

The `document_bridge` agent's job is *intent and completeness*, not OCR. So the one interpretive step — turning a document's bytes into structured fields — is peeled into its own **`extraction_agent`**, attached as an **`AgentTool`**. The main agent calls it, gets `{classification, key_fields, signals}` back, and keeps control to run the gate and decide.

A turn reads as: **receive** the artifact → **interpret the skill** (candidate doctypes, policy, satisfaction rule) → **extract** (subagent) → **decide** (the authoritative gate) → work out **what's still needed** → **return** the ledger. Control returns to the main agent at every step — that's why extraction is an `AgentTool`, not a `sub_agents` handoff.

This also settles *how completeness works* — **best-effort in the Bridge, decided by the app.** The Bridge holds the skill **and** the ledger, so it can *propose* "is this collection done?" and chase the delta: the satisfaction rule ("one gov-ID **or** two bills from different companies") lives in the skill as a **natural-language description** — no deterministic rules, no rules engine — which a one-shot model call (the satisfaction assessor, `bridge/satisfaction.py`) interprets best-effort on each deterministic collect turn when enabled (`BRIDGE_SATISFACTION=llm`), proposing items in the status vocabulary (`required` / `optional` / `satisfied`; "looks done" = nothing `required`) — the structured front door stays model-free by default and `done` is never minted — extending "config not code" ([thresholds in the skill](patterns.md)) while keeping the anti-DSL stance intact. But the Bridge holds **no final word on enforcement**: the assessment is advisory, and the **app must always be ready to make the completeness decision** ([Collect](collect.md)). The app's loop keeps that decision and gains a Bridge that pre-chases rather than sitting completeness-blind.

## Off-script intents — the flexibility we can't script

A document exchange is never just "valid doc → accept." Real parties send a **deletion/erasure request**, a **wrong or unrelated file**, a question, an out-of-scope message — and intents we simply haven't thought of yet. **We can't enumerate that set up front**, and that is exactly why the root is an **`LlmAgent`** rather than a deterministic graph: a graph must name every branch at build time; a reasoning agent **routes intents it was never pre-wired for**, falling through to the right tool (`handle_deletion_request`, `reject_submission`) or escalating for review. Crucially, the flexibility lives only on the *edges* — the KYC accept/reject stays behind the deterministic gate the LLM can't overrule — so absorbing the unknown never costs us auditability at the core. A new intent discovered later is **a new tool plus a line of instruction, not a re-drawn flowchart.** At the agent edge these intents arrive as text-only messages routed to the LlmAgent leg ([a2a-edge](a2a-edge.md)).

## Human review is the platform's pause, not ours

When a submission needs a person — low confidence, a flagged field, an escalation — the Bridge pauses on the platform's own construct: a **`LongRunningFunctionTool`** (`request_human_review`), resumed by an id-matched `FunctionResponse`. We deliberately **do not reimplement** this — no app-owned task-parking, no custom suspend/resume table, no bespoke `INPUT_REQUIRED` bookkeeping. Leaning on the native pause **is** the point: human-in-the-loop is a headline capability of the Agent Runtime, so showing the Bridge suspend and resume *through the platform* is itself part of the demonstration. The pause's durability rides the `SessionService` backend — InMemory locally, Cloud SQL `DatabaseSessionService` on deploy — the same swap as every other concern, so we showcase the platform rather than out-build it. On the agent edge the pause is reached from a Collector hitl_review verdict for processes whose skill declares bridge-hitl: park, and a reviewer resumes it with a HumanReviewResolution on the same A2A task → an id-matched FunctionResponse on a fresh Runner.

## Documents cross the wire by reference

An ADK **artifact** lives inside the Bridge — versioned, session-scoped, addressed by filename. An A2A **`FilePart`** is how a document travels between agents. The [edge](edges.md) is the one place those two worlds meet, and it keeps them apart the standard way: **bytes never ride the A2A message.**

- **Inbound.** A submission carries a `FileWithUri`; the edge fetches it and `save_artifact`s it — a resubmission is just the next artifact **version**, so history is free.
- **Outbound.** The deliverable is the **classified ledger plus artifact references** — meaning first, bytes on demand. The servicer's agent reads the `CollectionStatus`, then pulls accepted-document bytes from a **scoped fetch endpoint** (authorized on the same per-party leg boundary), served from `InMemory` locally and a GCS **signed URL** on deploy.

This keeps the A2A message small and the trust boundary honest: a reference is not a capability — every fetch is scoped to the leg that asked. The app-facing contract (`ExchangeTurn`/`CollectionStatus`) is unchanged in shape; the artifact reference is an *additive* handle on the ledger it already reads. The full lifecycle — versioning, deletion, the two meanings of "artifact" — is [its own page](artifacts.md).

## The stack

The application stack is chosen to run *with* the platform it showcases — the platform bets (Agent Runtime, Skill Registry, Memory Bank, Agent Gateway, Agent Identity; A2A + A2UI; Gemini + Document AI extraction; Cloud Tasks; Terraform) are hard requirements, not a local prototype that converges on them later. There is no framework-agnostic layer that swaps in the platform afterward.

| Layer | Choice |
|---|---|
| **Core + processing agents** | **Python 3.12+** |
| **Agent runtime** | **Google ADK**, pinned `google-adk >= 2.11.0, < 3`. The Bridge is an `LlmAgent` (`document_bridge`) with tools; extraction is an `AgentTool` subagent; HITL is a `LongRunningFunctionTool`; documents are versioned ADK artifacts; sessions swap `InMemory…`/sqlite ↔ `DatabaseSessionService` on Cloud SQL, artifacts `InMemory…` ↔ `GcsArtifactService` (deploy). |
| **Agent protocol (A2A edge)** | **Canonical A2A on the standard `a2a-sdk`** — JSON-RPC 2.0, spec methods (`message/send`, `message/stream`, `tasks/get`/`list`/`cancel`/`resubscribe`), A2A `Message`/`Task`/`Artifact`/`Part`. Domain types (`CollectRequest`, `ExchangeTurn{CollectionStatus}`, `RequirementsList`, `LedgerEntry`) are the payload carried inside A2A parts/artifacts. No bespoke REST dialect. |
| **Edge / HTTP surface** | **FastAPI** (async) — A2UI endpoints and the scoped artifact-fetch endpoint the A2A SDK does not cover. |
| **Schemas / validation** | **Pydantic** — doctype schemas, Gemini constrained JSON, per-document validation, disposition signals. |
| **Data store** | **Cloud SQL for PostgreSQL** — indexed relational keys + a JSONB document column; one implementation serves both the local and GCP adapters. |
| **Frontend (3 surfaces + A2UI renderer)** | **React + TypeScript + Vite**, **MUI (Material UI)**. |
| **HITL / graph durability** | **ADK runtime session durability** — suspend/resume via `LongRunningFunctionTool` + id-matched `FunctionResponse`, recovered from the persisted `SessionService`. No bespoke workflow engine; delegate behind a port only if the fulfillment graph ever outgrows session-persisted states. |
| **IaC** | **Terraform** (organized by substrate primitive — see [gcp-substrate](gcp-substrate.md)). |
| **Tests** | **pytest** (the shared port suite, run against local + GCP adapters) · **Playwright** (frontend/e2e). |
| **Tooling** | **uv** (Python) · **pnpm** (frontend). |

The KYC accept/reject is a **deterministic pure-function tool** (`run_disposition_gate`), exposed to the `LlmAgent` as an authoritative tool — the model routes intent, the gate decides, and a model may never mint a compliance acceptance (the inversion described above). This is standard agent+tool shape, not a deviation from "as standard as possible." Canonical A2A is chosen so that *any* A2A-conformant agent can talk to the Bridge; a bespoke REST dialect would interoperate only with our own client and hide the protocol the showcase advertises.

> **Note — LangGraph is demo furniture, not the core.** The core's fulfillment graph uses ADK runtime session durability, not a bespoke engine. LangGraph appears only outside the Bridge core: the Benefits demo builds one simulated carrier counterparty agent in LangGraph (alongside one in ADK) to prove A2A framework interoperability — the ADK-built Bridge negotiating multi-turn with a non-ADK agent over canonical A2A.

## Shared contract types — one package, one isolation invariant

The reusable Bridge core (`bridge/`) is separate from the processing agents (`agents/`) that consume it. The load-bearing invariant is **`bridge/` never imports `agents/`**: the core is a reusable showcase artifact, the agents are consumers, and a consumer importing the producer would be backward.

Both projects nonetheless share the **domain contract types** — the Pydantic models carried inside A2A `Message`/`Part`/`Artifact` (`CollectRequest`, `ExchangeTurn`, `CollectionStatus`, `RequirementsList`, `LedgerEntry`, extraction field schemas). These are the wire contract under validation and must stay synchronized. The design: a **single top-level `contract/` uv package** that both `agents/` and `bridge/` depend on as a path dependency (`[tool.uv.sources]`, editable). Neither project imports the other; both import the shared third package (explicitly permitted by the invariant). The package is lightweight — `pydantic` only, no ADK or `a2a-sdk` — so contract changes rarely force re-locks of the consumers. Heavier logic that must agree across the two projects (canonicalization, disposition, `is_satisfied`) is kept in parity **by discipline** — enforced by the shared port suite and an isolation guard test, never by import.

## Experimental ADK surfaces — pin and isolate

The "prefer the native construct" principle commits the design to a few ADK surfaces that are experimental in the pinned release. These are handled as a plain design discipline: **pin the versions, isolate each surface behind a port or a Ring-3 construct, and keep a living list of them** so a breaking SDK change surfaces immediately in the shared port suite. The list is design guidance, not a gate to pass:

- **`RemoteA2aAgent`** (`@a2a_experimental`) — the native agent→agent A2A consumer ([a2a-consumer](a2a-consumer.md)). Pin the integration mode (`use_legacy=True`) until the newer integration-extension path is validated against the mock.
- **`ResumabilityConfig(is_resumable=True)`** (`@experimental`) — the durable pause/resume configuration for long-running HITL; it lives on the ADK `App`, not on a bare Runner, so a parked leg survives a restart. The edge's LlmAgent App carries ResumabilityConfig(is_resumable=True).

`Workflow` itself is a public, non-experimental graph primitive and is not on this list. Every experimental surface is covered by the shared port suite (mock and real exercise the same surfaces), the SDK versions are pinned and guarded in CI, and each surface has an owner and a revisit trigger (validate before flipping a mode; re-audit on any `google-adk` minor bump for breaking changes or graduation to stable).

## Read next
- [disposition](disposition.md) — the KYC decision the gate tool wraps
- [Collect](collect.md) — the same LLM-drives / code-gates split on the client side
- [A2A consumer](a2a-consumer.md) — the native construct for calling *another* A2A agent (`RemoteA2aAgent`)
- [artifacts](artifacts.md) — documents as versioned ADK artifacts, and how they cross the wire
- [GCP substrate](gcp-substrate.md) — the service backends that swap in on deploy

## Related
- [exchange patterns](patterns.md), [the Bridge](bridge.md), [A2A consumer](a2a-consumer.md)
