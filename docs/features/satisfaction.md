# Satisfaction Proposal (COLL-2)

Feature area for the Bridge's **best-effort satisfaction proposal** — the model-interpreted, advisory completeness assessment that reads the process skill's prose rule and proposes outstanding/satisfied requirements in the status vocabulary. COLL-2: "1 gov-ID → 'looks done'; 1 bill → chases a 2nd distinct-issuer; app can always override; Bridge never enforces."

Completed-work archive for this feature area (`docs/features/`). This holds the implementation **substance** and rationale (with architectural decisions logged in `DECISIONS.md`).

**Supports CUJs:** CUJ-10 (Collect), CUJ-1 (Request).

**Requirements (THE WHAT) live in `spec/`, already authored — do not duplicate here:**
- `spec/cujs.md` — CUJ-10.
- `spec/demos/address-demo.md` — "The Bridge … best-efforts an advisory assessment of what's still outstanding, and chases it."
- `spec/demos/demo-suite.md` — COLL-2 acceptance.

**Acceptance:** 1 gov-ID → "looks done"; 1 bill → chases a 2nd distinct-issuer; app can always override; Bridge never enforces; default posture model-free.

**Architecture note:** the satisfaction proposal mechanism is the resolution of the design drift where `design/` said the Bridge reads the process skill's `metadata.bridge-satisfaction` prose rule, but nothing in `bridge/` actually did. The spec required this behavior (`spec/demos/address-demo.md`, `spec/demos/demo-suite.md`), so the fix was to build it, not reword the design. See `design/collect.md`, `design/adk.md`, `design/skills.md`, `design/disposition.md`.

---

## Completed Work

<!-- Newest first. One entry per completed task; capture what a future reader needs WITHOUT the local plan file. -->

### S0-arch-16 — The Bridge's best-effort satisfaction proposal (COLL-2), model-interpreted, opt-in (2026-10-01)

**Problem.** `design/adk.md`, `design/skills.md`, `design/collect.md` and `design/disposition.md` said the Bridge reads the process skill's prose satisfaction rule (`metadata.bridge-satisfaction`) and proposes what's outstanding. Nothing in `bridge/` read it; `project_outstanding` listed non-accepted entries and disclaimed COLL-2. The protected spec required this behavior: `spec/demos/address-demo.md` ("best-efforts an advisory assessment of what's still outstanding, and chases it"), `spec/demos/demo-suite.md`, COLL-2 in `docs/features.md`. Rewording design/ to the per-entry list would have contradicted spec/.

**What was built.** See `design/collect.md`, `design/adk.md`, `design/skills.md`, `design/disposition.md` and `DECISIONS.md` 2026-10-01 S0-arch-16.

**Core mechanism (`bridge/satisfaction.py`, `bridge/satisfaction_llm.py`):**
- **Model-interpreted, never a rule engine.** `LlmSatisfactionAssessor` makes one stateless `BaseLlm.generate_content_async(LlmRequest, stream=False)` call in JSON mode (`response_schema = {items: [ProposedItem]}`, temperature 0). No `Runner` or session service. The model resolves like `document_bridge`'s (`resolve_model` → `BRIDGE_MODEL`, default `gemini-2.5-flash`), constructed lazily on first use. No OR/count/distinct-issuer logic is hand-coded in `bridge/`, and the prompt is skill-agnostic.
- **Opt-in via `BRIDGE_SATISFACTION` env.** Unset/blank/`off` → no assessor (every turn model-free, per-entry outstanding list); `llm` → the model assessor; anything else → `ValueError` at `build_a2a_app`. Resolved once in `CollectExecutor.bind_assessor`. Default posture is byte-identical and model-free — the T1 suite, conformance tests and hybrid `ScriptedLlm(max_calls=0)` tests are unchanged.
- **No contract change.** The proposal rides `CollectionStatus.outstanding` in the COLL-3 vocabulary (`required`/`optional`/`satisfied`, never `waived`). Every item has `reason_code="satisfaction_proposal"`. `done` is **always** False (Bridge never mints done). `bridge.satisfaction.looks_done(outstanding)` = ≥1 item and nothing `required` (advisory; the app's gate owns done).
- **Replace-on-success, fall back on failure.** `propose_outstanding` returns the per-entry `project_outstanding(entries)` list when: no rule, no assessor, any exception (timeout default 30s), invalid JSON/schema, or empty proposal after sanitizing. Sanitizing drops blank items, nulls hints outside the label space, truncates messages to 300 chars, caps at 20 items. Failure logs only the exception type, never ledger contents.
- **Data minimization.** The model sees `doc_id`, `doctype`, `disposition`, canonical `issuer`, `reason_code`, `message` per entry, plus the rule and label space. Never sees `key_fields`, `tables`, confidences or artifact refs.

**Where applied:**
- The edge executor's **deterministic legs only**: R2 Collector turns (`_execute_collect`, after artifact-URI rewrite and before HITL branch) and Path-A validate-only turns.
- The LLM leg (R4) and HITL resume projections keep the per-entry list.
- The `Collector` class stays deterministic and model-free (composition happens in the executor).
- `_propose` checks for the assessor FIRST, so the default edge never reads skills for this.
- Task state is untouched (keys on `terminal`).

**Scope:**
- The turn's ledger only. The Collector is one-turn; no cross-turn cumulative Bridge ledger was built.
- `summarize_turn` counts only `required`/`optional` as outstanding (per-entry items are all `required`, so existing summary strings are unchanged).

**Skill integration (`bridge/edge/skills.py`):**
- `SkillMeta.satisfaction` (from `metadata.bridge-satisfaction`, stripped, default `""`).
- `satisfaction_for(process)` — unresolved process → `""` (never assessed).
- Doctype skills carry no satisfaction rule.

**Not wired (known, no task filed):**
- `BRIDGE_SATISFACTION=llm` is not set in terraform for the deployed runtime.
- Nothing in `bridge/` chases from `status.outstanding` today (the SLA ladder is policy-driven), so the proposal is surfaced to the app but does not yet drive the nudge copy.

**Verification.** `tests/test_satisfaction.py`: 29 model-free tests + 1 live test gated on `BRIDGE_TEST_GEMINI=1`. The live test passed against Vertex `gemini-2.5-flash`: one gov-id → looks done; one bill → `required` `utility-bill` "from a different company than 'power-co'"; two same-issuer bills → not looks done; distinct issuers → looks done; `done` False throughout. The T1 suite (1590 tests), conformance tests and `ScriptedLlm(max_calls=0)` hybrid tests all stayed green with zero model calls on structured traffic.

**Gotchas:**
- The S0-arch-15 core-names-no-skill guard holds: the assessor prompt and all string constants are skill-agnostic.
- The assessor is not a seam (no local/GCP adapter pair). It's resolved like the extraction agent's model.
- Best-effort never fails a turn and is never worse than today's per-entry fallback.
- The proposal never touches `terminal`, task state, or the disposition gate.
- A "looks done" proposal does not prevent the app from rejecting the set (e.g. `agents.address.gate.is_satisfied` still enforces the distinct-issuer constraint — the app's gate owns done, the Bridge's proposal is advisory).
