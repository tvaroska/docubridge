---
type: atom
related:
  - "a2a-edge.md"
  - "adk.md"
  - "long-running.md"
  - "collect.md"
  - "collect-scenarios.md"
tags: [bridge]
---

# A2A Consumer (calling the Bridge)

> The other side of the [A2A edge](a2a-edge.md): how a servicer's (or processing) agent **consumes** the Bridge over canonical A2A. The principle is the same as everywhere else — **prefer the platform-native construct** ([adk](adk.md)). For an ADK agent that calls another A2A agent, that construct is **`RemoteA2aAgent`**, not a hand-rolled A2A client.

> **This is not the outbound client.** "Bridge-as-client" (the Bridge *dialing out* to counterparties, Agent-Card discovery — [a2a-edge](a2a-edge.md)) is a different direction. Here it is *our* agent calling the Bridge inbound — the pull spine the core is built around.

## The native construct

An agent consumes the Bridge by instantiating **`RemoteA2aAgent`** against the Bridge's [Agent Card](a2a-edge.md). It resolves the card, manages the A2A client, converts `Message`/`Task`/`Part` to ADK events, and — crucially — maps the A2A task lifecycle onto ADK's own pause/resume. We hand-roll none of that.

| Concern (consumer side) | ADK-native construct | Not this |
|---|---|---|
| Calling another A2A agent | **`RemoteA2aAgent`** (card-configured) | a hand-rolled A2A client + `tasks/get` poll loop wrapped in a `FunctionTool` |
| Awaiting a long collection | native pause on `input-required` (→ `LongRunningFunctionTool`) | a blocking poll loop that holds the caller's turn |
| Learning of progress | streamed `TaskStatusUpdateEvent` (rendered as *thought*) | a bespoke status side-channel |

The reusability thesis is unchanged — *the difference between demos is the agent, not the Bridge* ([core vs demos](bridge.md)). Each demo points a `RemoteA2aAgent` at the same unchanged Bridge card; the mock→real and local→GCP swaps are a **different card URL**, not different agent code.

## Service-agent architecture (the general shape)

`RemoteA2aAgent` is *what* consumes the Bridge; the **target shape of the whole agent** is the same for every service agent — Address, Benefits, RFP, and any internal agent that calls the Bridge. That reusable whole-agent shape has its own reference atom — **[service-agent reference architecture](service-agent-architecture.md)** — the pattern to instantiate for a new use case. In brief:

> a **`Workflow`** (native graph) whose nodes are **(1) a `RemoteA2aAgent` collect node** (calls the Bridge over canonical A2A), **(2) a deterministic gate node** (the agent's Sense-B "done" function — `is_satisfied` for Address; the mutating-requirements decision for RFP), branching back to collect or forward, and **(3) a presenter node** (renders the deliverable — a deterministic code pass-through, or an `LlmAgent` for a natural-language summary) — all on **one shared, durable session**.

The reusability thesis cuts one level deeper: the difference between demos is the **nodes and the gate**, not the graph *kind*. Two invariants make this a *shape*, not a suggestion: the gate node is a **deterministic pure function** (a model may never mint "done" — Sense B, [collect](collect.md)), and the graph sits **on top** of the LLM (any LLM appears as a *node*, e.g. the presenter) because "`Workflow` cannot yet be used as an `LlmAgent` sub-agent" (`google-adk` 2.7.0). Full node-by-node spec, the fixed/varies table, and the instantiation gotchas live in [service-agent-architecture](service-agent-architecture.md).

## Wiring: why the graph carries the loop

`RemoteA2aAgent` is *what* consumes the Bridge; **how it hangs off the caller** is a second, consequential choice, because it decides whether **reasoning control** comes back to run the gate. The [Collect](collect.md) loop's completeness decision (**`is_satisfied` — the app owns "done"**, Sense B) runs *in the caller, after the Bridge returns its ledger*, then chases the next proof or stops. The consumer hangs off the caller as a node in a native **`Workflow`** graph: the graph *is* the control flow, so control returns to the gate node natively, and every node runs on **one shared, durable session** (nodes get the parent `InvocationContext`; `RemoteA2aAgent` sees its own prior event and reuses the `context_id`). That shared session is what makes native park/resume possible: an `input-required` pause hits `ctx.should_pause_invocation`, the loop suspends and resumes from saved state.

**The root problem is the session, not control-return.** The failure mode of a wrapper that runs the Bridge in a fresh throwaway child session (a new `Runner` + `InMemorySessionService` per call) is that it has no durable session for a `LongRunningFunctionTool` pause to resume against — so it structurally blocks native park/resume, and forces hand-threading the exchange `context_id` and a `skip_summarization` dependency. The graph avoids all of this by giving control a durable **place to return to**.

`Workflow` (node/edge graph, `google.adk.workflow`) is the go-forward API and is *not* `@experimental`; `LoopAgent`/`SequentialAgent` are `@deprecated` **in favor of `Workflow`**. The full graph shape — the collect → gate → present conditional cycle, the node-by-node spec, and the source-verified instantiation gotchas — lives in [service-agent-architecture](service-agent-architecture.md). Two consumer-relevant facts about running `RemoteA2aAgent` inside that graph:

- A `RemoteA2aAgent` (a non-`LlmAgent` `BaseAgent`) runs directly as a `Workflow` node (`BaseAgent` subclasses the workflow `BaseNode`). Its collected `ExchangeTurn` artifact must be emitted `last_chunk=True`, or the *partial* event streams to the live caller but is never persisted to the shared session, so the gate reading `ctx.session.events` can't see it (see [service-agent-architecture](service-agent-architecture.md)).
- `RemoteA2aAgent`'s built-in resume detection assumes the resolved `FunctionResponse` is the *last* session event, which the graph orchestrator breaks by appending a workflow event after it. A send-path `RequestInterceptor` re-detects the pending resume and stamps the parked A2A `task_id`/`context_id` so the peer resumes the *same* task.

The composition constraint holds: **`Workflow` cannot yet be used as an `LlmAgent` sub-agent** — so a graph sits **on top** (LLM as a *node*, e.g. a presenter), not behind an LLM "front". Of the durability surface, only `ResumabilityConfig` (the persistent-checkpoint half) is `@experimental` — see [Durable state restore](#durable-state-restore).

## Two mechanisms for long-running work

A collection runs for [days or weeks](long-running.md). `RemoteA2aAgent` supports that through two standard mechanisms, and the **Bridge's task status is what selects which one the caller gets**:

1. **Hold and wait (seconds → minutes).** While connected, the caller consumes the task's update stream; `submitted`/`working` states surface as ADK *thought* events (progress), never the final answer. Bounded by the client timeout (ADK default 600s). Fine for a seconds-scale hold; **cannot** span weeks.
2. **Pause and resume (hours → weeks, zero compute).** When the Bridge parks awaiting an external event, its task returns **`input-required`** (or `auth-required` for a credential blocker). `RemoteA2aAgent` turns that into a **`LongRunningFunctionTool` pause**: the caller's invocation *ends* (zero compute), the peer `task_id` + `context_id` persist on the [session](aggregate-model.md), and a later `FunctionResponse` resumes to the *same* task. This is the **same** native durability the Bridge uses for [HITL](adk.md) — reused across the caller↔Bridge edge for free. (A real park/resume needs the durable graph wiring above, which hosts it on one shared, durable session.)

## Waiting is `input-required` — and the edge emits it

Mechanism 2 needs the Bridge to express "parked, resume later" as **`input-required`**, not a prolonged `WORKING`. The [A2A edge](a2a-edge.md) emits this by design: the task status follows the turn's `terminal` flag (`_status_for`: `ExchangeTurn.terminal=False` → `INPUT_REQUIRED`, otherwise `COMPLETED`) — a Collector turn is terminal even with a pending item, so a Bridge-side park is the hybrid edge's `bridge-hitl: park` handoff, an escalate, or an RFP's open turn. The consumer's contract is therefore small — **treat `INPUT_REQUIRED` as a resumable pause, not a failure.** Which verdicts park, at which thresholds, and why is [the review contract](disposition.md#the-review-contract).

On the hybrid edge INPUT_REQUIRED also signals a Bridge-owned HITL park (pending/awaiting_human_review, review_call_id on status.message.metadata) — resumed by sending a HumanReviewResolution on the same task (the resume-stamping interceptor keeps the task_id) — and an escalate (lifecycle=escalated), resumed by a new submission or closed by tasks/cancel.

## Progress is `TaskStatusUpdateEvent.status.message`

Live progress rides the standard streaming event: a **`TaskStatusUpdateEvent` carrying a non-empty `status.message`** over `message/stream`. `RemoteA2aAgent` renders it as a thought event.

- **`a2a-sdk` 1.x gotcha:** the SDK carries an always-present *empty* proto `Message`; ADK collapses it to `None` and only surfaces a status update when a **real, non-empty** `Message` is attached. **An empty `status.message` is silently dropped** — mock and real Bridge must attach real parts.

## Timescales compose

| Timescale | Mechanism | Wire |
|---|---|---|
| seconds–minutes, connected | hold + streamed status | `message/stream`, `TaskStatusUpdateEvent` |
| hours–weeks, idle | pause + resume | `input-required` → `LongRunningFunctionTool`; woken by push |
| after disconnect | re-attach / snapshot | `tasks/resubscribe`, `tasks/get` |
| weeks-scale wakeup | callback | `PushNotificationConfig` |
| terminal | deliverable | `ExchangeTurn` artifact on `COMPLETED` |

## Durable state restore

A weeks-scale pause (mechanism 2) only pays off if the caller can **come back to the same state** after its process has died and restarted. That restore is mostly **platform-native** — the custom surface is small and well-bounded, source-verified against `google-adk` 2.7.0 + `a2a-sdk`.

The webhook is a **doorbell, not a restore**: `PushNotificationConfig` delivers only "task X changed" — it neither carries the caller's state nor puts the agent back where it paused. Restore is a *separate* mechanism (persistent stores + resumability); the wake signal is *necessary but not sufficient*.

- **DEFAULT (config swap, no custom code):** `DatabaseSessionService` (sqlite locally, Cloud SQL deployed) (session state + event history, reloadable by `(app, user, session_id)`); `DatabaseTaskStore` (task + artifacts + `context_id`); `tasks/get`/`tasks/resubscribe`; peer ids ride `event.custom_metadata`, auto-repopulated on resume; the Runner auto-matches a `FunctionResponse` to the paused call; push-notification **sender** on the Bridge.
- **EXPERIMENTAL:** `ResumabilityConfig(is_resumable=True)` — the switch that makes a park survive a restart (`@experimental`, 2.7.0; pin + port-cover, [adk](adk.md) SDK-risk).
- **CUSTOM (small, mechanical):** a **webhook receiver** (a2a-sdk is sender-only; `adk web` can't host one) + a **`task_id`→session index**; the receiver then calls `runner.run_async(...)` and everything downstream is DEFAULT.

Durable park/resume across a restart needs only the DEFAULT stores + the EXPERIMENTAL switch — **no HTTP, resumable by feeding a `FunctionResponse` to `run_async` directly.** The webhook path adds only the two CUSTOM pieces, and only when a leg must outlive the process. How this maps onto the deployed substrate (Cloud Run runtime, Cloud SQL sessions, where the wake receiver lives) is [its own atom](deploy-resume.md).

## Extraction review vocabulary

A consumer typically asks two things of an extraction: *how sure are you overall*, and *which fields must a human look at*. Its own names for those (`extraction_confidence`, `fields_requiring_review` as dotted paths like `commission.rate_pct`) are **mapped**, not adopted: the Bridge wire keeps `overall_confidence` / `field_confidence` / `flagged_fields`, and the mapping is executable — `contract.extraction.fields_requiring_review(result)` / `field_reviews_for(result)`.

| Consumer vocabulary | Bridge wire field | Mapping |
|---|---|---|
| `extraction_confidence` | `overall_confidence` | the same scalar, 0.0–1.0 |
| per-field confidence | `field_confidence[name]` | keyed by the bare `key_fields` key (or a table name / dotted row path) |
| `fields_requiring_review` | `flagged_fields` ∪ `field_reviews[].path` | dotted paths: a bare name `k` → `fields.key_fields.{k}`, a table `t` → `fields.tables.{t}`, `doctype`/`issuer` → `fields.{name}`; a name already containing `.` or `[` (e.g. `fields.tables.rates[12].frequency`) passes through unchanged |
| *why* a field needs review | `field_reviews[].reason` | `low_confidence` / `derived` / `absent_from_document` / `model_flagged` |
| reason code on the ledger | `LedgerEntry.reason_code == "flagged_fields"` | means "one or more fields require review" |

Two consequences follow from the shape of that table. First, **paths address the Bridge extraction result, never the consumer's schema** — the Bridge cannot import a consumer's types, so re-mapping `fields.key_fields.premium` onto `rates[0].amount` is the consumer's step. Second, a consumer may *post* its own spelling: `fields_requiring_review` is accepted as an inbound validation alias on `ExtractionResult`, so a structured assertion in the consumer's vocabulary classifies as Path A instead of silently falling through to the portal path; the Bridge still dumps `flagged_fields`. A payload carrying both spellings with different values is refused loudly. The per-field review view reaches the wire on `LedgerEntry.field_reviews`.

## Structured rows (tables)

Flat `key_fields` cannot carry a carrier rate sheet, so row-shaped data rides beside it: `ExtractionFields.tables` / `LedgerEntry.tables` is *table name → list of rows*, each row a flat `column → text` map. The doctype skill schema declares which tables and columns exist (`properties.tables`, each an `array` of text-column objects); Path A builds a typed row model from it (required columns mandatory, undeclared columns and tables rejected, a string `enum` column closed), and Path B pins the same tables in the Gemini response schema. **Every cell is text** — amounts included — because the consumer code-parses money; the Bridge never emits a number a consumer then trusts as one.

`benefits-quote` (schema v2) declares two tables, aligned field-for-field with a VB consumer's normalized quote: `rates` (`coverage_tier`, `tobacco`, `age_band`, `amount`, **per-row** `frequency` — `MONTHLY` / `BI_WEEKLY` / `WEEKLY` / `SEMI_MONTHLY` / `ANNUAL`, since carriers mix them — `rate_basis` `FLAT` / `PER_1000` / `PER_UNIT`, `benefit_amount`) and `design` (`category`, `category_original`, `value`). Commission and product line are scalar key fields (`commission_basis`, `commission_rate_pct`, `commission_notes`, `product_line`). Mapping these onto the consumer's own models (`commission_rate_pct` → `commission.rate_pct`, `tables.rates[i]` → `rates[i]`) is the consumer's step.

The field is additive, so the contract took a **minor** bump (`contract_version` `1.1`). By rule a minor is additive only: `decode_wire` strips undeclared fields when a payload declares a newer minor of our major (as it always did for a different major), while same- or older-version payloads still decode strictly.

## Mapping onto a consumer quote (conformance)

A consumer that reads only the `ExchangeTurn` must be able to build its own quote from it without inventing anything. So the engine's numbers ride the ledger: `LedgerEntry.overall_confidence` (the consumer's `extraction_confidence`) and `LedgerEntry.field_confidence` (per-field), additive and defaulted (contract minor `1.3`). `overall_confidence` is `None` — not `0.0` — when there is no engine number (a `schema_invalid` entry), so "unknown" never reads as "certainly wrong".

The consumer-side mapping, wire → a VB consumer's `BenefitsQuote`:

| Bridge wire (`LedgerEntry`) | Consumer field |
|---|---|
| `issuer` | `carrier` |
| `key_fields.{currency, language, product_line, plan_year, effective_date}` | same name (`plan_year` parsed to an int) |
| `key_fields.expiry_date` | `expiration_date` |
| `key_fields.carrier_product_name` | `carrier_term_original` |
| `key_fields.commission_{basis, rate_pct, notes}` | `commission.{basis, rate_pct, notes}` |
| `tables.rates[i].{col}` | `rates[i].{col}` (money code-parsed from text) |
| `tables.design[i].{col}` | `design[i].{col}` |
| `artifact` | `source.artifact` (with `source.path = a2a`) |
| `overall_confidence` | `extraction_confidence` |
| `field_reviews[].path` | `fields_requiring_review` (same table, `fields.`-prefixed; a flag on `rate_basis` addresses the row `rates[i]`) |

**Which side supplies what.** The document supplies everything above. The consumer supplies the intake context the document never carries — client, program, incumbency, and a plan-year fallback — and the Bridge never guesses it.

**Refuse loud, never coerce.** Where the Bridge's vocabulary is wider than the consumer's, the mapping names every offending path and refuses, rather than coercing or dropping:
- a row `frequency` outside the consumer's vocabulary (e.g. `WEEKLY`, `SEMI_MONTHLY`);
- a non-`FLAT` `rate_basis` (`PER_1000`, `PER_UNIT`): dropping it would change what `amount` means, so only `FLAT` (or absent) drops losslessly;
- a review path the consumer has no field for (`fields.doctype`, `premium`, …): a review flag must survive the mapping;
- a missing `overall_confidence`.

The Bridge side stays green in each case: the cells are carried verbatim and the disposition is unaffected. The gap is the consumer's vocabulary, and it is reported as such. The executable form of all of this — the journey over the A2A edge by reference, on the fixture and gemini legs at terminal-outcome parity, and a mirror of the consumer's model with a gated drift check against the real one — lives in `tests/conformance/`.

## Consumer contract summary

- **Consumer construct:** `RemoteA2aAgent` is the canonical consumer; waits are `input-required` (native pause), progress is `TaskStatusUpdateEvent.status.message`; the integration-extension mode is pinned (`use_legacy=True` until the new extension is validated against the mock).
- **Durable construct:** the consumer hangs off the caller as a node in a native `Workflow` graph (service-agent shape above and in [service-agent-architecture](service-agent-architecture.md)); state restore is mostly DEFAULT.
- **In the edge:** `_status_for` maps a non-terminal turn (`ExchangeTurn.terminal=False`) → `INPUT_REQUIRED` ([a2a-edge](a2a-edge.md)); native HITL pause/resume ([adk](adk.md)).
- **Experimental surfaces:** `RemoteA2aAgent` is `@a2a_experimental` and `ResumabilityConfig` is `@experimental` in `google-adk` 2.7.0 — pin versions and isolate behind the shared port suite so a breaking change is caught immediately.

## Related
- [A2A edge](a2a-edge.md) (server side), [running on ADK](adk.md), [long-running collection](long-running.md), [Collect](collect.md), [Collect scenarios](collect-scenarios.md) (rejection · delays · keeping context)
