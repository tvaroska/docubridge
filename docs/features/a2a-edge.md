# A2A Edge — Hybrid routing architecture

The agent-to-agent edge routing that combines deterministic collection with LlmAgent-based
intent handling. Supports structured document submissions, off-script requests, HITL
parks, and reviewer resolutions over the same A2A surface.

Completed-work archive for this feature area. This holds the plan **substance**, not
links — `.claude/plans/*` are local and gitignored, so their reasoning must be captured
HERE (and decisions logged in `DECISIONS.md`).

**Supports CUJs:** CUJ-1 (Path-A assertion), CUJ-2/4/10 (structured collect), CUJ-5
(HITL park/resume), CUJ-6 (SLA), plus off-script intents (deletion/escalate/reject).

---

## Completed Work

<!-- Newest first. One entry per completed task; capture what a future reader needs
     WITHOUT the local plan file. -->

### 2026-10-01 — S0-arch-18: Unresolved process gets an empty label space

**What was built:**

The Bridge no longer falls back to a hardcoded list of known doctypes when a process skill is unresolved. Instead, `candidate_doctypes_for(process)` returns `[]` (an empty label space) when:
- No loaded process skill has that name, or
- A process skill exists but declares no `bridge-candidate-doctypes` in its SKILL.md

The empty label space causes the disposition gate to reject every document as `rejected / Gate.unsupported / unsupported_doctype`, ensuring unknown processes fail closed rather than accepting documents meant for other processes.

**Decision: empty label space, not a refusal.**

Chose `[]` over a JSON-RPC error for wire compatibility, failing closed through the existing gate (no new error path). Consumers still receive a `COMPLETED` exchange turn. This matches the neutral defaults for other unresolved-process accessors: `hitl_mode_for` → `"report"`, `satisfaction_for` → `""`.

**Key changes:**

- **`bridge/edge/skills.py`:** Deleted `KNOWN_DOCTYPES`. `candidate_doctypes_for` now loops through loaded skills and returns `list(skill.candidate_doctypes)` for a matching process skill, else `[]`.
- **`bridge/disposition.py`:** Empty label space message: `"The document type '<doctype>' is not supported for this process. This process accepts no document types."` (non-empty message byte-identical for corpus compatibility).
- **`tests/test_isolation_guard.py`:** Removed `bridge/edge/skills.py` from `SKILL_NAME_ALLOWLIST` (now only `bridge/edge/mock.py` and `bridge/calibration.py`).
- **Tests:** Added end-to-end unknown-process rejection test over both edge modes, empty label space test, and updated existing candidate-doctypes tests.

**R4 semantics preserved:**

`_llm_label_space` returns `[]` (legal, rejects every document as unsupported) vs `None` (refuses with `label_space_missing`). The distinction remains: `[]` is a resolved process with an empty label space; `None` is an unresolved process on the text-resolution path.

**Verification:**

- Full test suite green (1697 tests passed; only 2 tests updated for the new behavior).
- Isolation guard passes with allowlist at 2 entries.
- No `KNOWN_DOCTYPES` symbol remains in bridge/tests/agents.
- Behavior probe confirmed: `address-proof` → `['gov-id', 'utility-bill']`, `unknown-process` → `[]`.
- End-to-end over both edge modes: gov-id under `unknown-process` → `rejected / unsupported / unsupported_doctype`, zero LLM calls.
- Address corpus unchanged (`wiki/evals/`, `spec/`, `contract/` untouched).

### 2026-10-01 — S3-edge-1: Hybrid edge routing ADR

**What was decided:**

Ratified the **hybrid edge** architecture, superseding the S0-arch-1 deferral that kept
the edge deterministic-only. The A2A edge now routes by message shape and task state:

- **Deterministic legs (unchanged):** Path-A assertions and structured `CollectRequest`
  submissions continue to use the model-free Collector, completing synchronously in one
  turn with `SUBMITTED → COMPLETED`. Zero LLM calls on structured traffic.
  
- **LlmAgent leg (newly wired):** Text-only intents (deletion, escalate, off-script
  rejection), HITL handoffs when the process skill opts in (`bridge-hitl: park`), and
  reviewer resolutions route to the `document_bridge` LlmAgent. These emit genuine
  `WORKING` states and can park with `INPUT_REQUIRED` / `terminal=False`.

**The route table** (first match wins; deterministic mode = R1+R2 only):

| Route | Match (message shape + task state) | Leg | Wire outcome |
|---|---|---|---|
| **G** (guard) | Message on an `INPUT_REQUIRED` task with pending `request_human_review` | guard refuse | Anything other than `HumanReviewResolution` → `InvalidRequestError`, task unchanged, zero compute |
| **R1** (Path-A) | `ExtractionResult` data part | patha validate-only | Terminal turn, `COMPLETED`; model-free in both modes |
| **R2** (Collector) | `CollectRequest` and/or file parts | Collector | `SUBMITTED → COMPLETED`; zero LLM calls |
| **R3** (handoff) | R2 turn with `hitl_review` gate + skill `bridge-hitl: park` | Collector → LLM handoff | Persist + URI rewrite, then hand to LLM leg which calls `request_human_review` → `INPUT_REQUIRED` |
| **R4** (intent) | Text-only, no `CollectRequest`, no resolution part | LLM leg | Deletion / off-script reject / escalate / unrouted |
| **R5** (refuse) | `HumanReviewResolution` on task with no pending review | refuse | `InvalidRequestError` (no_pending_review) |
| **R6** (resume) | `HumanReviewResolution` matching pending call id, authorized reviewer | LLM leg resume | Fresh Runner, id-matched `FunctionResponse` → `COMPLETED` or `INPUT_REQUIRED` if other reviews pending |

**Key mechanisms:**

- **Flag:** `BRIDGE_EDGE_MODE=deterministic|hybrid` (default deterministic). Read once at
  app build; invalid value → `ValueError` fail-closed. It is a routing mode, not a seam.
  
- **HITL handoff opt-in:** Skill frontmatter `bridge-hitl: park | report` (default
  `report`). The Benefits Consumer Benefits process uses `report` (consumer owns review, turn is
  terminal), Address uses `park` (Bridge owns review via LRFT, turn parks).
  
- **Ledger from tool responses only:** `project_turn_from_events` reads only
  `function_response` parts from disposition gate, HITL request, deletion, rejection,
  escalation, and executor-fed resolutions. Model text parts are never relayed. An
  `accepted` disposition can come only from a gate `auto_approve` response (args pinned
  by executor before the call) or a reviewer resolution — never from the model directly.
  
- **LLM-outcome → wire mapping:** `status.message` text is always Bridge-authored (tool
  message or `summarize_turn`). `reason_code` and `review_call_id` ride the A2A
  `status.message.metadata` envelope extension (not a domain-contract change). Verdicts:
  gate approve/resubmit/unsupported → terminal; HITL request → `INPUT_REQUIRED`; deletion
  ack (`deletion_completed` / `nothing_to_delete`), off-script reject
  (`off_script_rejected`), and unrouted (`off_script_unrouted`) → terminal; escalate
  (`escalated_for_review`) → non-terminal `INPUT_REQUIRED` with `lifecycle=escalated`.
  
- **Session key == artifact key:** LLM-leg ADK session =
  `(app_name="document_bridge", user_id=<party>, session_id=<A2A context_id>)`, so
  `handle_deletion_request` erases exactly the artifacts the Collector saved, and
  addressing = isolation (no cross-context artifact access).

**Contract delta:**

Only one additive type — `contract.HumanReviewResolution` (shape: `call_id`,
`resolution` = accepted/rejected, `message`, optional `reviewer` audit label;
`extra="forbid"`). `ExchangeTurn`, `CollectionStatus`, `LedgerEntry`, `CollectRequest`
unchanged. `CONTRACT_VERSION` "1.3" → "1.4" (additive).

**Lifecycle per leg:**

- Deterministic legs (R1/R2): `SUBMITTED → COMPLETED` (rationale: no genuine in-connection
  work phase — collect completes synchronously).
- LLM leg: `SUBMITTED → WORKING → COMPLETED | INPUT_REQUIRED` (resume: `WORKING → …`).
  The model call is genuine work, so `WORKING` is honest there.

**Non-collapsing completing path:**

On the LLM leg, HITL park or escalate is one durable task that parks and resumes on the
same `task_id`/`context_id`, with ADK session durable via the `sessions` seam and
`ResumabilityConfig(is_resumable=True)`. On the deterministic completing path, each round
genuinely completes, so a new task per round under the same exchange `context_id` remains
correct — the `context_id` is the aggregate, the per-round task is the completed unit.

**Files touched:**

- `DECISIONS.md` (new entry with full route table, wire mapping, supersedes note)
- `TODO.md` (flipped `- [x]`, amended S3-edge-3/5/6 Files lines for flag/.env, offscript
  reason code, skill hitl key)
- `design/a2a-edge.md` (hybrid statement, route table, wire mapping, lifecycle per leg,
  status-free present-tense)
- `design/adk.md` (off-script intents route to LLM leg; HITL park from Collector verdict
  with `bridge-hitl: park`; `ResumabilityConfig` on edge App)
- `design/long-running.md` (HITL resume trigger includes `HumanReviewResolution`; escalate
  parks `INPUT_REQUIRED` with `lifecycle=escalated`)
- `design/a2a-consumer.md` (fixed `_status_for` description; `INPUT_REQUIRED` signals
  HITL park or escalate on hybrid edge)

**Gotchas learned:**

- Guard G precedes R1 (deviation from initial TODO order): a task parked for a reviewer
  is locked to its resolution. Otherwise a resubmission routed to the Collector would
  complete the task and orphan the pending LRFT call, so the reviewer's resolution could
  never land.
- Path-A never hands off to the LLM leg — model-free in both modes.
- The "409" for call id mismatch is `InvalidParamsError` (`review_call_id_mismatch`),
  since A2A JSON-RPC has no HTTP 409.
- LLM text never appears on the wire — `status.message` is always Bridge-authored.
- Design docs written status-free/present-tense ahead of code (design SoT rule).

**Verification:**

- T1: `uv run ruff check` clean; `uv run pytest -q` → 1226 passed / 42 skipped (no code
  changed — baseline unchanged).
- T2: All acceptance checks passed (flag present in docs, design docs status-free,
  `_status_for` misstatement fixed, all routes G/R1–R6 with owning tests, reason codes
  match code vocabulary, scope limited to docs-only files).
- Every route has an owning test (existing or planned in S3-edge-2..7).

**What this enables:**

Sprint 3 tasks S3-edge-2..8 implement the route table: LlmAgent runtime (S3-edge-2),
executor router (S3-edge-3), gate inversion (S3-edge-4), off-script routing + real
deletion (S3-edge-5), HITL park/resume (S3-edge-6), corpus parity both modes (S3-edge-7),
spec proposals (S3-edge-8).

---

### 2026-10-01 — S3-edge-2: LLM-leg runtime + model-free test double

The LLM leg's execution runtime, independently testable without Gemini or network access.

**What was built:**

`bridge/edge/llm_leg.py` — The `LlmLeg` runtime that runs the document_bridge LlmAgent
via a plain ADK Runner (never imports `google.adk.a2a` or `A2aAgentExecutor`):

- `run(party, context_id, message) -> LlmLegResult(key, events, projection)` —
  host-controlled wiring with session/artifact services injected via the sessions seam
- `SessionKey(app_name, user_id, session_id)` where `(app_name, user_id, session_id) =
  ("document_bridge", <party>, <context_id>)` — session key == artifact key (D9), enabling
  `handle_deletion_request` to erase exactly the artifacts the Collector saved
- `resolve_party(store, party, context_id)` — explicit party wins; else the context's
  single owning party from the shared store; zero or >1 → `_unscoped` (fail closed)
- Fresh `Runner` per invocation (never cached — S3-edge-6 resumes on a fresh Runner over
  the durable session)

Pure projection from ADK events to `ExchangeTurn` (D4/D6):

- `project_from_events(events, *, context_id, seed_entries) -> LlmProjection(turn,
  reason_code, status_text, review_call_ids)` — reads only `function_call` (to pair args)
  and `function_response` parts, never model text or thought
- Skips partial/empty events, user-authored function responses, and non-projected tools
  (`extraction_agent` response included)
- Projected tools: `run_disposition_gate`, `request_human_review`,
  `handle_deletion_request`, `reject_submission`, `escalate_submission`
- Malformed gate response is skipped (never guessed); `reject_submission` never mints a
  `rejected` entry
- Precedence: review (rank 4) > escalate (3) > last of {delete, reject} (2); gate-only →
  `reason_code=None` (caller uses `summarize_turn`)

Agent construction accepts model or test double:

- `build_document_bridge(model: str | BaseLlm | None)` /
  `build_extraction_agent(...)` — the same `BaseLlm` instance is shared by root and
  extraction sub-agent
- `build_app(model, *, is_resumable=True)` — carries `ResumabilityConfig` imported from
  the registered FQN `google.adk.apps.app`

Model-free test double:

- `tests/_scripted_llm.py::ScriptedLlm(BaseLlm)` — content-keyed script (`on_text`
  substring match, `on_tool` function-response name), deliberately false `after_tool_text`
  ("Done — the document was accepted."), call counter, and validation that every scripted
  call names a tool in `llm_request.tools_dict`
- Enables 19 model-free tests (projection, session keys, resumability, isolation guards)
  without network or `GOOGLE_API_KEY`

**Key decisions & gotchas:**

- **D4 clarification (G1):** In ADK 2.7.1 a `LongRunningFunctionTool` returning non-None
  (today's `request_human_review` returns a dict) does NOT pause a resumable invocation —
  ADK emits the function_response and calls the model again. Only a `None` return pauses
  (call carries `long_running_tool_ids`, no response). The projection treats a
  `request_human_review` call with id in `event.long_running_tool_ids` and no response as
  pending (Bridge-authored text), not ignored — a framework-flagged call. S3-edge-6
  decides whether to return `None` so the invocation truly ends.
- **G3:** Shared `BaseLlm` sees both agents' requests; `tools_dict` tells them apart
  (root: 6 tools incl. `extraction_agent`; sub-agent: `extract_document`).
- **G6:** Resumable runs emit an empty trailing event; the projection tolerates
  `content is None` / empty `parts`.
- **P7-8 guard:** Only `or`-gated defaults in `llm_leg.py`; no `InMemory*()` constructions
  (session service from the sessions seam, artifact store from `build_artifact_store`).

**Files created/modified:**

- Created: `bridge/edge/llm_leg.py`, `tests/_scripted_llm.py`, `tests/test_llm_leg.py`
- Modified: `bridge/agent.py`, `bridge/extraction/agent.py`, `bridge/edge/artifacts.py`
  (`owning_parties` helper), `tests/test_agent.py`

**Verification:**

- T1: Full suite green → 1259 passed / 42 skipped (+31 new tests in `test_llm_leg.py`, +2
  in `test_agent.py`)
- T2: Model-free gate script (no network, no API key) → `1 pending hitl_review True 2`;
  real-erase via session key works; `DatabaseSessionService` wired from seam
- Isolation guards green (no `google.adk.a2a` imports in `bridge/`, no `agents/` imports
  from `bridge/`)

---

### 2026-10-01 — S3-edge-3: Executor router behind `BRIDGE_EDGE_MODE`

The routing dispatcher that makes the hybrid edge real. Routes each A2A message to the
deterministic Collector, Path-A validate-only, or the LLM leg based on message shape and
task state.

**What was built:**

The flag, route table, and executor dispatch that complete the hybrid edge (S3-edge-1
ratified the architecture, S3-edge-2 built the LLM leg runtime; this task wires them):

- **`BRIDGE_EDGE_MODE` flag** — `deterministic` (default, unset/blank) or `hybrid`.
  Read once at app build (`build_a2a_app`); invalid value → `ValueError` fail-closed
  (mirrors the seam `resolve_mode` idiom). Not a seam itself — it's a routing mode, not
  a substrate swap point.

- **Route classification** — pure `classify_route(message, mode, task_state,
  has_pending_review)` returns `Route` enum (GUARD_REFUSE, PATH_A, COLLECT, LLM,
  RESUME_REVIEW, NO_PENDING_REVIEW). Deterministic mode: exactly matches the old
  `classify_path` (assertion → PATH_A, everything else including text-only → COLLECT),
  guaranteeing byte-for-byte parity. Hybrid mode (first match wins): parked task with
  pending review → route to RESUME_REVIEW if resolution part present, else GUARD_REFUSE;
  resolution part alone → NO_PENDING_REVIEW; assertion → PATH_A; CollectRequest or file
  parts (url/raw) → COLLECT; non-empty text → LLM; else COLLECT.

- **Executor dispatch** — `CollectExecutor.execute` dispatches on the route. Path-A and
  Collector bodies moved verbatim (no event-sequence changes to preserve byte parity);
  R4 (LLM leg) runs `LlmLeg.run(party=None, context_id=<A2A context_id>, message=<joined
  text>)` with lifecycle `SUBMITTED → WORKING → COMPLETED|INPUT_REQUIRED`. Bridge-authored
  status text only (tool message > `summarize_turn` > `LLM_UNROUTED_TEXT`); model text
  never surfaces. LLM-leg exceptions → FAILED state with no artifact so tasks never hang
  in WORKING.

- **Hybrid-mode wiring** — `build_a2a_app(..., edge_mode=None, llm_leg=None)`
  constructs or binds an `LlmLeg` only in hybrid mode (shares the app's artifact store).
  Deterministic mode never imports or constructs the leg (lazy import in `server.py`,
  TYPE_CHECKING-only in `executor.py`) so `import bridge.edge` stays light. New
  `executor.bind_edge(mode, llm_leg)` mirrors `bind_store` as the single source of truth.

- **Both-mode test parametrization** — every A2A-driven test, conformance journey, and
  golden run parametrized over deterministic/hybrid with `ScriptedLlm(max_calls=0)` and
  teardown assertion `call_count == 0`. Structured traffic (assertions, CollectRequests,
  file submissions) routes to the model-free Collector in both modes. Byte-for-byte
  parity test confirms identical `ExchangeTurn` JSON across modes for every conformance
  record.

**Key mechanisms:**

- **Resolution part detection:** A data part with metadata tag `kind ==
  KIND_HUMAN_REVIEW_RESOLUTION` (the one constant added to `bridge/edge/wire.py`). The
  full `HumanReviewResolution` type and decoder land in S3-edge-6.

- **Unreachable-by-design routes:** `has_pending_review` is a stub returning `False`
  until S3-edge-6 lands the session read, so GUARD_REFUSE and RESUME_REVIEW are
  unit-tested in the classifier only. NO_PENDING_REVIEW is reachable and raises
  `InvalidRequestError`.

- **A2A context_id reuse:** The LLM leg keys its session and turn on the A2A
  `context.context_id` (D9 from S3-edge-1), enabling deletion to erase exactly what the
  Collector saved. The Collector still mints its own turn context_id when the
  CollectRequest has none (unchanged).

**Gotchas learned:**

- **G1 — Deterministic byte-parity hinges on the classifier.** In deterministic mode
  `classify_route` must return exactly what `classify_path` implied and never consult
  task state, pending review, or the LLM leg. Pinned by a test comparing against
  `classify_path` across task states.

- **G2 — `raw` parts route to COLLECT in hybrid** so inline bytes keep today's
  `decode_submissions` rejection. Otherwise a text+raw message would silently go to the
  model.

- **G3 — "0 LLM calls" fails loud.** `ScriptedLlm(max_calls=0)` raises on the first
  call (task → FAILED, assertion breaks); teardown also asserts `call_count == 0`.
  Mutation-checked: routing structured messages to LLM turned 45 tests red.

- **G4 — LLM-leg exceptions → FAILED, not a hung WORKING task.** Exception handler logs
  and emits `updater.failed(LLM_FAILED_TEXT)` then returns, so the task never gets
  stuck.

- **G5 — Lazy LLM-leg import.** Deterministic mode must never construct the leg. Proven
  by patching `bridge.edge.llm_leg.LlmLeg.__init__` (not a server-module name) because
  of the lazy import in `server.py`.

- **G6 — A2A vs ExchangeTurn context_id.** The byte-parity test pins
  `context_id="conformance-parity"` in the CollectRequest so scoped artifact URIs
  (which embed the context_id) match across modes.

**Files created/modified:**

- Modified: `bridge/edge/pathroute.py` (EdgeMode, resolve_edge_mode, Route,
  classify_route), `bridge/edge/executor.py` (bind_edge, route dispatch, _execute_llm_leg),
  `bridge/edge/server.py` (edge_mode/llm_leg params, hybrid wiring), `bridge/edge/wire.py`
  (KIND_HUMAN_REVIEW_RESOLUTION constant), `bridge/edge/intake.py` + `__init__.py`
  (docstrings, exports), `.env.example` (BRIDGE_EDGE_MODE)
- Created: `tests/test_pathroute.py`, `tests/conformance/conftest.py`
- Modified tests: `tests/test_edge_a2a.py`, `tests/address/test_golden.py`,
  `tests/conformance/journey.py`, `tests/conformance/test_benefits_conformance.py`

**Verification:**

- T1: Full suite green → 1379 passed / 45 skipped (baseline 1259/42). New: +58
  router units, +25 edge tests, +4 golden, +33 conformance. The +3 skips are only
  hybrid copies of tests already gated on env (BRIDGE_TEST_GEMINI, BRIDGE_TEST_CONSUMER_REPO).
- T2: Byte-identical ExchangeTurn parity across modes for all conformance records; 0
  LLM calls on structured traffic; live script confirms structured → COMPLETED with 0
  calls, text-only → WORKING → INPUT_REQUIRED on escalate, bogus flag → ValueError,
  default → deterministic with no leg construction.

---

### 2026-10-01 — S3-edge-4: Gate inversion at the edge

The gate inversion mechanism that prevents the LLM from forging disposition gate
arguments. The model may call `run_disposition_gate` but never supplies its inputs — the
Bridge pins all five arguments from session-recorded extraction signals or Collector
seeds.

**What was built:**

Core gate pinning module and wiring across the agent, extraction, projection, and
executor:

- **`bridge/gatepin.py`** — new core module containing the recorded-signals model
  (`RecordedSignals`), state keys (`bridge:gate_signals`, `bridge:label_space`,
  `bridge:gate_pins`), and the pinning callback `pin_gate_args` that overwrites all five
  gate arguments in place. The callback is a `before_tool_callback` attached to the root
  `document_bridge` agent, always on (the CORE-2 invariant belongs to the agent, not
  only to the edge). A no-op for every other tool.

- **Signal recording** — `extract_document` records genuine extraction signals into
  session state (`tool_context.state[SIGNALS_KEY]`), forwarded to the parent session by
  ADK's AgentTool state-delta forwarding. The flagged-field fallback and issuer
  canonicalization match the Collector byte-for-byte (`signals_from_result`). Supports
  both extraction-sourced signals (genuine `ExtractionResult` from the sub-agent) and
  Collector-sourced seeds (R3 handoff seeding).

- **Pin mechanism** — the most recently recorded un-gated record is consumed once per
  gate call (marked `gated_by=<function_call_id>`). Re-recording a doc_id (a
  resubmission) moves it to the end un-gated, making it gateable again. With no un-gated
  record or no label space, the call is refused with a Bridge-authored
  `{"refused": true, reason_code, message}` that never validates as a `DispositionVerdict`.

- **Projection enforcement** — `project_from_events` mints a gate entry only from a
  pinned response whose verdict equals the gate recomputed over the pinned signals.
  doc_id/doctype/issuer come from the pin, never from model args or text. The pin
  (`{call_id: {"doc_id", "signals"}}`) rides the gate response event's
  `actions.state_delta`. An unpinned or inconsistent response mints nothing. Removed the
  interim doc_id source (`last_extraction_request`).

- **Bridge-decided label space** — R4 (`_execute_llm_leg`) passes
  `label_space=self._llm_label_space(text)`: the deterministic `parse_text_request`
  resolver plus the process skill's candidate doctypes. No match → `None` → every gate
  call refused, fail-closed. R3 Collector seeds carry their request's label space.

- **Seeding API** — `LlmLeg.run(label_space=None, seed_signals=(), seed_entries=())` is
  the seeding API for R3 handoffs. `gatepin.seed_state_delta(session.state, ...)` (pure)
  rides `Runner.run_async(state_delta=...)` to make seeded signals visible before the
  first tool runs.

**Key decisions:**

- Pin all five gate args including `candidate_doctypes` (the label space) — forging
  `["passport", ...]` on the passport record still yields the Bridge-decided label space
  only.
- The source is session state only, never call args or AgentTool text. AgentTool
  forwards the sub-run's state_delta, so extraction signals recorded inside
  `extract_document` reach the parent `document_bridge` session.
- D4 clarification: reading the pin from the gate response event's `actions.state_delta`
  is reading Bridge-authored callback output, not model content. The projection recomputes
  the verdict to ensure consistency.
- Selection is the most recent un-gated record, consumed once; re-recording resets it.
- Label space is Bridge-decided: deterministic process resolver on R4, request label
  space on R3 seeds. No match → refuse.

**Adversary corpus:**

96-case adversary matrix (8 Address corpus records × {a: text claim, b: forged gate,
c-reject, c-escalate} × {in-process seeded, in-process extracted, over A2A}) proves the
pin holds:

- No accepted entry on the 4 non-auto_approve records under any adversary
- Accepted entry on the 4 auto_approve records only from the genuine pinned gate
  (adversary b — the positive control) or the Collector seed
- Forged `overall_confidence=1.0, flagged_fields=[]` and `candidate_doctypes=["passport", ...]`
  never produce an accept on hitl/resubmit/unsupported records
- Model text ("Done — the document was accepted.") never surfaces in the turn or A2A
  status message
- Mutation-checked: disabling the pin → 12 failures (4 non-auto records × 3 harnesses);
  projection trusting unpinned responses → test failures

**Gotchas learned:**

- Parallel gate calls in one model turn fail closed: at most one pin per record, the
  second call is refused `gate_signals_missing`.
- A gate call in the same model turn as the extraction is refused (signals not recorded
  yet).
- A stale un-gated record from an earlier invocation can still be pinned later — it
  holds genuine signals for its own doc_id, so this is not a forgery.
- The model chooses the artifact name for extraction; a genuine extraction of a
  different real artifact is not a forgery (artifact-to-party scoping out of scope).

**Files created/modified:**

- Created: `bridge/gatepin.py`, `tests/test_edge_gate_inversion.py` (+133 tests)
- Modified: `bridge/extraction/agent.py` (record signals), `bridge/agent.py` (pin
  callback, instruction), `bridge/edge/llm_leg.py` (seeding API, projection pin check),
  `bridge/edge/executor.py` (label space resolver), `tests/test_llm_leg.py` (gate tests
  rewritten), `tests/test_agent.py` (+2 tests), `design/a2a-edge.md` (gate pinning
  paragraph)

**Verification:**

- T1: Full suite green → 1514 passed / 45 skipped (baseline 1379/45). +133
  `test_edge_gate_inversion.py`, +2 `test_agent.py`, gate tests in `test_llm_leg.py`
  rewritten, skips unchanged. Guard tests green. Import chain intact.
- T2: 96-case adversary matrix passes in ~9s (model-free). Mutation checks confirm
  tests bite. Model-free script reproduces expected verdicts. Regression suite green.
  No forbidden imports or status language in design docs.

---

### 2026-10-01 — S3-edge-5: Off-script outcomes on the wire and deletion scoping

The wire-level metadata carrying off-script outcome reason codes, real-erase deletion
over A2A, and session-scoped erasure that confines deletion to the requesting leg's own
artifacts.

**What was built:**

Wire metadata for LLM-leg outcomes:

- The R4 final status message (`COMPLETED` or `INPUT_REQUIRED`) carries
  `status.message.metadata["reason_code"]` identifying the off-script outcome:
  `deletion_completed`, `nothing_to_delete`, `off_script_rejected`,
  `escalated_for_review`, `off_script_unrouted` (text-only with no tool calls), and
  `awaiting_human_review` (when the projection reports a pending review call).
  
- WORKING and FAILED messages carry no metadata. Gate-only turns (where the model called
  only the disposition gate) leave metadata unset. Deterministic legs remain unchanged
  with no metadata, preserving byte-identical behavior for structured traffic.

- The `llm_status` helper in `bridge/edge/executor.py` is the single decision point for
  LLM-leg status text and reason code, maintaining the existing text precedence logic
  while adding reason code selection from the projection's precedence winner.

Deletion confined to session scope:

- `handle_deletion_request` erases only filenames listed under exactly
  `(document_bridge, party, context_id)`, addressing the turn's context. All versions of
  the named artifact are deleted, and the scoped fetch URI returns 404 afterwards.
  
- Filenames in ADK's user namespace (`user:` prefix) are refused because they span every
  context of a party and are never inside one leg. The membership check against session
  keys prevents deletion of other contexts' artifacts or traversal-looking names.
  
- When more than one party owns a context (ambiguous ownership), deletion fails closed
  with `nothing_to_delete` rather than guessing which party's artifact to erase.

Test coverage:

- `tests/test_edge_offscript_a2a.py` exercises every D6 off-script row over A2A: the
  real-erase/404 proof, all-versions deletion, the scoping matrix (cross-context,
  cross-party, ambiguous ownership, user-namespace refusal), escalate surviving
  `tasks/get` and restart through a durable task store, deterministic-mode negative (no
  erase), and the reason code on every outcome.

**Key mechanisms:**

- `META_REASON_CODE = "reason_code"` constant in `bridge/edge/wire.py` identifying the
  metadata key (envelope extension space, not a contract field change)
  
- `REASON_UNROUTED = "off_script_unrouted"` constant in `bridge/offscript.py` for
  model-text-only outcomes where no tool ran
  
- `USER_NAMESPACE_PREFIX = "user:"` constant and refusal logic in
  `bridge/offscript.py::_erase_artifact` as the first guard before any store access
  
- The durable `DatabaseTaskStore` preserves `status.message.metadata` through
  engine restart, allowing escalated tasks to retain their reason code across service
  restarts

**Gotchas learned:**

- The double's `after_tool_text` ("Done — the document was accepted.") runs after every
  tool response including deletion, but never surfaces in status messages, the turn, or
  streamed updates — the projection and executor correctly filter all model text.
  
- Deletion addresses the `ExchangeTurn.context_id`, which the Collector mints when a
  `CollectRequest` omits one. A client deletes on the context named in the turn or the
  scoped URI's first path segment, not necessarily the A2A context of the submitting
  task.
  
- The a2a-sdk accepts follow-up messages on `INPUT_REQUIRED` (escalated) tasks, routing
  them back to the LLM leg on the same task id for continued handling.

**Deferred issues (proposed but not fixed):**

- User-namespace artifacts at the persist/fetch layer: `filename_for` currently passes
  client-supplied `user:` filenames to `save_artifact`, allowing cross-context reads at
  fetch time. Proposed as a Sprint 0 security task to neutralize such filenames at
  persist and return 404 on fetch.
  
- Deployed GCS store limitation: `owning_parties()` returns empty set on
  `GcsArtifactService`, causing party-scoped artifacts to fail closed as
  `nothing_to_delete`. Per-party discovery on GCS deferred to Phase 7-9.

---

### 2026-10-01 — S3-edge-6: HITL park/resume over A2A (G / R3 / R6)

The Human-in-the-Loop park/resume mechanism that pauses an LLM-leg invocation on a
`request_human_review` call, parks the task with `INPUT_REQUIRED`, and resumes on a fresh
Runner when an authorized reviewer sends a `HumanReviewResolution`.

**What was built:**

Routes G (guard), R3 (handoff), and R6 (resume) from the S3-edge-1 route table:

- **True zero-compute park.** `request_human_review` returns `None` (not a dict), which
  makes ADK pause the LRFT. The call's id appears in `event.long_running_tool_ids` with
  no `function_response`, which `project_from_events` projects as a pending HITL entry
  with `terminal=False`. Status text is always the Bridge-authored
  `PAUSED_REVIEW_MESSAGE`, never model text.

- **R3 handoff from Collector.** When a Collector turn has ≥1 `hitl_review` entry under a
  `bridge-hitl: park` process (opt-in per skill), the executor hands off to the LLM leg
  with Collector-seeded signals (`source="collector"`), the request's label space, and the
  original entries persisted in session state (`bridge:review_tasks[task_id]`). The model
  receives Bridge-authored text naming each HITL entry and calls `request_human_review` to
  park. Task state: `WORKING → INPUT_REQUIRED` with
  `status.message.metadata.reason_code=awaiting_human_review` and `review_call_id` (the
  first pending call id). Task metadata carries `bridge_review_session` (session key + all
  pending call ids) for scoping resolutions to the correct task.

- **R6 resume via fresh Runner.** A `HumanReviewResolution` part sent by an authorized
  reviewer on a parked task triggers resume: the executor rebuilds a Runner from the same
  session DSN, feeds an id-matched `FunctionResponse` as `role="user"` content, and drains
  events. The turn is rebuilt from the seeded Collector entries with each resolved call's
  target entry transformed (`disposition=accepted|rejected`, reviewer message appended;
  gate `hitl_review` and reason_code preserved). Multiple reviews in one submission
  resolve one at a time; the task returns to `INPUT_REQUIRED` until the last resolution.
  Same `invocation_id` and `task_id` throughout.

- **Pre-dispatch guard.** `EdgeRequestHandler.admit` runs before the SDK touches the task,
  handling G, R5, and all R6 validation (decode, authorization, call id match). Refusals
  are JSON-RPC errors (`InvalidParamsError` -32602 for malformed/mismatched,
  `InvalidRequestError` for unauthorized/no-pending-review) with the task unchanged. This
  prevents the task from being marked FAILED by an exception inside `execute`.

- **Reviewer authorization (fail-closed).** `build_a2a_app(reviewers=...)` or
  `BRIDGE_EDGE_REVIEWERS` (comma/space-separated principal names); unset or empty refuses
  every resolution. The principal is only `call_context.user` from the injected Starlette
  `AuthenticationBackend`. The payload `reviewer` field is an audit label only. The edge
  ships no backend; tests use a header-based one defined in-file. A deployed hybrid edge
  without a backend refuses all resolutions (correct fail-closed behavior).

- **Contract 1.4.** Additive `HumanReviewResolution` type with `extra="forbid"` (`call_id`,
  `resolution: accepted|rejected`, `message`, optional `reviewer` audit label). Tolerant
  reader strips newer minor fields.

- **Skill opt-in.** `SKILL.md` frontmatter `bridge-hitl: park|report` (default `report`).
  `address-proof` uses `park` (Bridge owns review via LRFT); Benefits processes use
  `report` (consumer owns review, turn is terminal).

**Key mechanisms:**

- **Pending detection.** `pending_review_calls(events)` returns call ids in
  `long_running_tool_ids` with no response from any author. `resolved_review_calls(events)`
  returns user-authored responses (only the executor writes these).

- **Per-task scoping.** Task metadata `bridge_review_session = {user_id, session_id,
  call_ids}` is server-authored via `TaskUpdater.update_status`. Pending-for-task =
  `call_ids ∩ pending_review_calls(session.events)`, so a call parked on another task of
  the same context refuses. `DatabaseTaskStore` round-trips metadata.

- **Durable seed.** Collector entries are persisted in `state_delta` under
  `bridge:review_tasks[task_id]` (`LlmLeg.run(review_task_id=)`). Resume rebuilds the turn
  from them with `resolve_hitl` transform applied to resolved entries.

- **Degrade on model no-park.** If the model calls no `request_human_review` (or the leg
  raises), R3 degrades to the report-mode Collector turn (`COMPLETED`), keeping the edge
  never-worse.

**Gotchas learned:**

- **Owner-scoped task store.** a2a-sdk task stores scope by `resolve_user_scope(context)`
  (the principal name), so a non-owner gets `TaskNotFoundError`. The resolving principal
  is therefore the task owner's: the consumer agent relays its human reviewer's decision.

- **Pre-dispatch guard deviation.** The plan raised refusals inside `execute`, but an
  exception there marks the task FAILED via `ActiveTask._run_producer`. The guard runs
  before the SDK touches the task, preventing mutation. This also fixes the pre-existing
  R5 path.

- **Duplicate-resolution race.** Two identical resolutions racing past the guard can still
  fail the task: the second reaches ADK with an already-answered id.

- **Several reviews in one turn.** They resolve one at a time; resuming one of several
  paused calls yields no events until the last is answered.

- **History in later turns.** A new R3 park on a context with another task's paused call
  starts a new invocation. The model history then contains the unanswered call.
  `ScriptedLlm` is indifferent; a real model's tolerance is unverified.

**Files created/modified:**

- Created: `bridge/edge/reviewers.py`, `tests/test_edge_hitl_a2a.py` (+13 tests)
- Modified: `contract/wire.py` (HumanReviewResolution, CONTRACT_VERSION "1.4"),
  `contract/__init__.py` (export), `bridge/edge/wire.py` (META_REVIEW_CALL_ID,
  META_REVIEW_SESSION, encode/decode_human_review_resolution), `bridge/edge/skills.py`
  (hitl field, hitl_mode_for), `bridge/offscript.py` (returns None),
  `bridge/edge/llm_leg.py` (pending/resolved helpers, resume, durable seed),
  `bridge/collect.py` (results_out), `bridge/edge/executor.py` (R3 handoff, R6 resume,
  admit guard), `bridge/edge/server.py` (reviewers, auth_backend),
  `bridge/edge/pathroute.py` (EdgeRequestHandler), `skills/address-proof/SKILL.md`
  (bridge-hitl: park), `.env.example` (BRIDGE_EDGE_REVIEWERS), tests/test_contract.py,
  tests/test_offscript.py, tests/test_llm_leg.py

**Verification:**

- T1: Full suite green (ruff clean, pytest all passed, isolation guards green)
- T2: All 13 test cases in `test_edge_hitl_a2a.py` passed (park, zero-compute,
  rebuild/resume, mismatched/resolved/cross-task id refusal, authorization, two-review
  sequence, rejected resolution, report-mode no-handoff, deterministic never-parks,
  degrade, tolerant decode). Ad-hoc driver exercised park → INPUT_REQUIRED, tasks/get
  zero-compute, rebuild → wrong/non-authorized refusal → correct resolution → COMPLETED
  with same task_id/context_id.

**Proposed follow-ups (not implemented):**

- **Deploy-time verified principal backend** (Gateway/IAP identity). Until it exists, a
  deployed hybrid edge refuses every resolution (correct fail-closed behavior).
- **Separate reviewer identity** (reviewer who is not the task owner) requires an
  owner-resolver/delegation design on the task store.
- **Spec proposal:** State that the resolving principal is the task owner's (A2A owner
  scoping) and that refusals are pre-dispatch JSON-RPC errors.

### 2026-10-01 — S3-edge-7: CUJ golden parity, hybrid vs deterministic

`tests/address/test_golden_llm_edge.py` proves the two edge modes reach the same
terminal outcome on the Address corpus (test-only; no `bridge/` change).

**What was built:**

- **Corpus matrix over A2A.** The 8 `expected.json` records go through both edges via
  the in-process `a2a-sdk` client. The projection compared is task state, `terminal`,
  status `reason_code`, and per entry doc_id/doctype/issuer/disposition/gate/reason_code.
  Each outcome is also pinned to the corpus. The 6 non-HITL records match exactly, with
  0 hybrid LLM calls.
- **HITL.** The 2 `hitl_review` records stay `pending` on the deterministic edge
  (report). On the hybrid edge they park `INPUT_REQUIRED` (`awaiting_human_review` +
  `review_call_id`), and a reviewer resolution then takes them to COMPLETED. The final
  entries equal the deterministic entries with only the disposition replaced.
  `bill-aquautil-clear` resolves to the `timeline.json` `hitl_resolved` value
  (`accepted`). `gov-id-expired` (no timeline beat) is resolved both ways, and the final
  disposition follows the reviewer.
- **Driver parity.** CUJ-1, CUJ-10 distinct, CUJ-10 same-issuer and the CUJ-4 first turn
  run through `run_collection`. Both modes give the same outcome tuples, the CUJ judges
  hold, and the hybrid LLM leg is called 0 times.
- **The full CUJ-4 loop** (blurry, then clear on one context; ids grounded in the
  timeline's resubmit exchange) is accepted within the resubmit cap.
- **The `BRIDGE_TEST_GEMINI=1` leg** repeats every body on live Gemini, plus 32 live
  adversaries that assert only model-independent invariants.
- **Corpus helpers.** `tests/address/_corpus.py` gains `record_by_id`,
  `hitl_record_ids` and `timeline_hitl_resolutions`.

**Verification:**

- T1: ruff clean. pytest 1590 passed / 91 skipped. The new skips are only the
  `BRIDGE_TEST_GEMINI` gate.
- T2: the new file gives 16 passed (fixture leg). A model-free cross-check script
  matched det == hyb on the 6 non-HITL records, and on both HITL records showed park →
  resolution with the Collector reason restored. Three `bridge/` mutations (no park,
  structured traffic to the LLM, resolution dropped) each turned the expected cases red.

**Proposed follow-ups (not implemented):**

- An Address consumer reviewer-relay (`HumanReviewResolution` on an
  `awaiting_human_review` park).
- A `timeline.json` beat for `gov-id-expired`.
- A model-independent R3 handoff. Live Gemini sometimes skips `request_human_review`,
  and the edge then degrades to report.
- `extract_document` should refuse an unknown artifact name instead of raising. A live
  injection run failed one task this way.

### 2026-10-01 — S3-edge-8: Spec proposal and Sprint 3 close-out

Documentation and verification task with zero code change. Wrote PROPOSAL-only entries in `DECISIONS.md` for a new CUJ-14 "Off-script intent mediated agent-to-agent", CUJ-5's Driver gaining `tests/test_edge_hitl_a2a.py`, and CUJ-1/10 Judges gaining "identical on deterministic and hybrid edges". Updated `docs/features.md` (hybrid-edge rows WIRE-7/WIRE-8) and `docs/upstream-adk-issues.md` (Issues 3-5: `user:` namespace, LRFT pause/resume, AgentTool text return).

**What was verified:**

- **T3 gate green: 11/11 built CUJs pass both judges in both edge modes.**
  - **Deterministic half:** each of the 11 CUJ node sets exits 0 under `BRIDGE_EDGE_MODE=deterministic` and again under `=hybrid`, with credentials stripped.
  - **LLM half:** jeep (Gemini 2.5 Flash) judge on each CUJ's both-mode runs, criteria = journey row + Judge text, all scored `pass=true` and `score >= 0.8` (perfect 1.00 on all 11).
  - **Proposed CUJ-14:** 51 tests passed in hybrid mode (`tests/test_edge_offscript_a2a.py` + the adversary trap), jeep `pass=true` / `score=1.0`.
  - **Sensitivity proof:** `BRIDGE_EDGE_MODE=bogus` failed exactly CUJ-8's driver with `ValueError: 'bogus' is not a valid EdgeMode`, and 65 passed.
- **Scope:** only `DECISIONS.md`, `TODO.md`, `docs/features.md`, `docs/upstream-adk-issues.md`, and `docs/features/a2a-edge.md` changed. No `spec/`, `wiki/`, `bridge/`, `contract/`, `agents/`, `terraform/`, `tests/`, or `frontend/` change.
- **ID validation:** every proposed node id (24 ids from P-1..P-5 blocks) collected.

**Proposals (not applied — spec/ is protected):**

- **CUJ-14** ("Off-script intent mediated agent-to-agent"): Driver = `tests/test_edge_offscript_a2a.py` (deletion → 404, reject → `off_script_rejected`, escalate → `INPUT_REQUIRED`+`escalated`). Judge: real erase, no soft delete, no LLM-minted disposition on a genuine document. On apply, `docs/features.md` CORE-8 and ART-1 gain CUJ 14.
- **CUJ-5 Driver gains agent-to-agent tests** (`tests/test_edge_hitl_a2a.py`).
- **CUJ-1/10 Judges gain "identical on deterministic and hybrid edges"** (model-free structured traffic).
- **CUJ-4 Driver gains real-edge both-mode companions** (`tests/address/test_golden_llm_edge.py::test_cuj4_resubmit_then_clear_loop`).

**ADK friction (Issues 3-5):**

- **Issue 3:** `user:` filename prefix silently escalates artifact scope to party-wide. Workaround: `bridge/offscript.py` `USER_NAMESPACE_PREFIX` guard.
- **Issue 4:** LRFT pause/resume: (a) only `None` pauses, (b) already-answered ids re-accepted, (c) one of several resumes yields no events until all answered. Workaround: `pending_review_calls`/`resolved_review_calls` + `CollectExecutor.admit` pre-dispatch.
- **Issue 5:** `AgentTool` returns sub-agent text, not tool output. Workaround: signals via session state + `before_tool_callback` pin (`bridge/gatepin.py::record_extraction`).

**Carried proposals (for /replan):**

- S3-edge-5 G6/G7 (GCS `owning_parties`, persist/fetch `user:` side)
- S3-edge-6 (deploy-verified principal, separate reviewer identity, resolving-principal-is-owner spec note)
- S3-edge-7 D-1/F-1/F-2 (consumer reviewer-relay, model-independent R3, `extract_document` refusal)
- S0-docs-9 (upstream-doc Issue 1 stale path)

**Verification:**

- T1: ruff clean, pytest 1590 passed / 91 skipped (matches S3-edge-7 exit count).
- T2: all 8 acceptance criteria passed (scope, ID validation, T3 both halves, sensitivity, features catalog status-free, upstream doc grounded, TODO marked done).

**Sprint 3 complete.** 0 open S3-edge tasks. Run `/replan` to close the sprint and start the next.
