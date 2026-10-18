# Decisions

Append-only log of decisions and learnings. Newest at the bottom.

---

## 2026-08-21 — Gen-3 realign

Migrated the repo to the gen-3 canonical layout on branch `gen3-realign`
(`wiki/bridge-*.md` → `spec/` + `design/`; `PLAN.md` → `TODO.md`). Three decisions
made during the migration:

- **Eval corpus stays in `wiki/evals/`.** The Address oracle (`expected.json`,
  `timeline.json`, `images/`) is a *runtime fixture*, not just docs — it is hardcoded
  into production code (`bridge/extraction/fixture.py`, `bridge/edge/artifacts.py`) and
  ~10 test files via literal `parents[N] / "wiki/evals/address"` paths. Moving it would
  touch production code and risk the test suite for no design benefit, so it was left in
  place and registered as a protected path in `CRITICAL.md`.

- **`bridge-patterns.md` → `design/patterns.md`.** "Exchange Patterns" (the closed set of
  built-in flows the Bridge owns in code) reads as both a requirement and a design doc;
  classified as design (how the Bridge is built), matching the gen-3 `patterns` → `design/`
  heuristic.

- **Task IDs keep the native `P<phase>-<seq>` scheme.** Not converted to the gen-3
  `S<sprint>-<feature>-<seq>` default: the IDs are already well-formed, and are linked to
  30 `.claude/plans/P*.md` filenames plus commit-message history. Renaming would orphan
  those links for no correctness gain. `TODO.md` uses a per-phase counter comment.

---

## 2026-08-21 — S0-sec-1: drop the skill-install HTTP write surface

Fixed the unauthenticated arbitrary-file-write (path traversal via `POST /admin/skills`
→ `LocalSkillRegistry.install_skill`) by **removing the endpoint** rather than only
sanitizing it. Rationale: live doctype-add (SKILL-4/CUJ-8) never needed a running write
service — `loaded_skills()` re-scans `BRIDGE_SKILLS_DIR` on every discovery GET, so a
skill folder dropped into the dir (via the `skill_registry` seam) is already reflected
with no redeploy. The seam is kept (thin directory glue; load-bearing for the P7 GCS
swap and the uniform seam invariant); only the HTTP write wrapper was deleted.

- Removed `POST /admin/skills`, its handler, `SKILLS_INSTALL_PATH`, and the export.
- Hardened `install_skill` anyway (defense-in-depth for the authenticated remote-install
  endpoint that returns at P7 behind per-party Gateway scoping): name must match
  `^[a-z0-9-]+$` and every asset must resolve inside the skill dir.
- Trade-off accepted: no remote skill-install over HTTP this cycle. Given auth/Gateway
  scoping was already P7-deferred, an unauthenticated write endpoint was premature;
  filesystem-drop-now, authenticated-endpoint-at-P7 is the cleaner sequence.
- Tests: rewrote the CUJ-8 beat (`tests/test_live_doctype_add.py`) to drive the seam
  drop between two GETs on the same app; added a traversal-rejection test to the
  skill_registry seam parity suite. Full suite green (490 passed).

---

## 2026-08-21 — S0-arch-1: log the LlmAgent-behind-the-edge deferral (not wire it)

The `document_bridge` `LlmAgent` mediator is built + unit-tested but reachable by no
serving surface — the agent-facing A2A edge deliberately serves the deterministic
one-turn `Collector` (`bridge/edge/executor.py`, Decision 1). The review flagged this as
a **silent-cap violation**: the deferral was logged only in a code comment. The task
offered two fixes — wire the LlmAgent behind the edge, or log the deferral explicitly.

**Chose: log the deferral.** Wiring the LlmAgent would need Gemini at the edge and emit
free text rather than a domain `ExchangeTurn` data part, breaking the P2 exit posture
("the app reads standard A2A task status + a ledger projection, never ADK events") and
directly contradicting the deliberate Decision 1. The actual defect is the *silence*, not
the deterministic edge — so the proportionate fix is to make the scope-out visible, per
the repo's "No silent caps" cross-cutting rule.

- Added an explicit "Deferred to later cycles" entry in `TODO.md` carrying the F2/F3/F4
  consequences: off-script routing, live real-erase deletion (ART-1), and the
  `request_human_review` HITL zero-compute park are reachable through the LlmAgent's tools
  but have **no agent-to-agent A2A entry point** this cycle. Today: HITL park is proven
  via portal Path-B (CUJ-5) + the durable park/resume path; off-script + real-erase are
  unit-tested on the LlmAgent directly.
- Reconciled the `design/a2a-edge.md:24` drift ("internals are an LlmAgent + tools" vs. a
  deterministic `Collector` on every served path) with an "as-built scope" paragraph.
- Cross-referenced both from the `CollectExecutor` docstring so the deferral is no longer
  logged only in code.
- Learning: for a "not-wired" finding on a deliberately deferred surface, documenting the
  deferral is the fix — the isolation/testability rationale for the deterministic edge is
  load-bearing, so silently *or* loudly replacing it would regress the P2 contract.
- No code behavior changed (docstring + docs only); full suite green (490 passed).

## 2026-08-21 — S0-docs-1: fold resolved intake question into design; propose spec removal

The `/review` audit flagged drift: `spec/open-questions.md:20-24` still lists "Request
intake — structured vs. free-text on the A2A edge" as open, but it shipped as P2-3/WIRE-5
exactly along its stated leaning — one `AgentSkill` per process skill, two `input_modes`
(`application/json` + `text/plain`), content-routed at the edge, free-text parsed into a
`CollectRequest`.

**Done.** Folded the resolution into `design/a2a-edge.md` (the design SoT): the "Request
intake — format vs capability" section now reads as decided/as-built and points at the
implementation — `SkillMeta.input_modes` (`bridge/edge/skills.py`) → dynamic Agent Card
(`bridge/edge/card.py`); content routing in `bridge/edge/intake.py:decode_intake`
(structured JSON wins when both present, else text fallback). Recorded the one as-built
divergence from the leaning: the text→`CollectRequest` parse is a **deterministic** default
(`parse_text_request`), not the LlmAgent parse-by-model the leaning named — that parser is
the documented swap point and is **deferred, not dropped** (same deferral family as the
LlmAgent-behind-the-edge S0-arch-1 entry).

**PROPOSAL (not applied — spec/ is protected).** Remove the "Request intake — structured
vs. free-text on the A2A edge" entry (spec/open-questions.md:20-24) from
`spec/open-questions.md` and let the design page carry the settled decision. Not edited
during `/implement` per the spec-proposal-only rule; leaving it for a spec-owner pass.

- No code changed (design + planning docs only); full suite green (490 passed, 13 skipped).
- Learning: for a "resolved-but-still-open-question" drift, the /implement side of the fix
  is the design fold-in + a recorded proposal; the actual spec deletion stays a protected
  spec-owner action.

## 2026-08-21 — S0-docs-2: propose Driver:/Judge: fields for the in-cycle CUJs

The `/review` audit found `spec/cujs.md` is a **prose table** — no CUJ carries the
`Driver:` / `Judge:` fields that `/verify` (Tier-3 gate) extracts per CUJ
(`skills/workflow/commands/verify.md:26-33`). So `/verify` reports all 13 CUJs as
"not runnable — missing Driver/Judge" and skips them, even though the in-cycle journeys
already have real, green pytest coverage. The gap is documentation wiring, not tests.

**Done (verification, not spec edit).** Confirmed each in-cycle CUJ maps to an existing,
green driver test — ran the 8 driver files: **64 passed**. `Driver:` = a code e2e harness
node id (per `verify.md:37-42`); `Judge:` = deterministic pass criterion + a hard-fail
trap. Verified mapping:

| CUJ | Driver (pytest node id) | Judge (deterministic + hard-fail trap) |
|---|---|---|
| CUJ-1 | `tests/address/test_golden.py::test_cuj1_path_a_instant_parity_mock_and_real` | terminal accepted+satisfied identical on mock and real edge; trap: bytes on wire / ADK events read |
| CUJ-2 | `tests/test_portal.py::test_blurry_resubmit_then_clear_hitl_then_resolve_accept` (+ `::test_clean_doc_accepts_one_step`) | Path-B upload→extract→dispose reaches accept; trap: relay not byte-identical (`::test_relay_message_reason_code_byte_identical`) |
| CUJ-4 | `tests/address/test_golden.py::test_cuj4_resubmit_full_loop` (+ `tests/test_portal.py::test_resubmit_cap_escalates_not_rejects`) | blurry→clear accepts within ≤3; trap: >3 resubmits / cap escalates-not-rejects violated |
| CUJ-5 | `tests/test_portal.py::test_hitl_reviewer_can_reject` + `::test_blurry_resubmit_then_clear_hitl_then_resolve_accept`; park-zero-compute `tests/address/test_park_resume.py::test_dur3_park_is_zero_compute` | flagged extraction parks then resumes on matched response; trap: park uses compute (DUR-3) / HITL-without-park not 409 (`::test_hitl_without_park_409`) |
| CUJ-6 | `tests/test_outbound.py::test_ladder_with_nudge_notifier_end_to_end` (+ `tests/test_sla.py::test_timeline_parity`) | on_track→overdue→reminder(real nudge)→escalated, each recorded, next alarm re-armed; trap: nudge carries bearer token (`::test_nudge_carries_no_bearer_token`) |
| CUJ-7 | `tests/address/test_wake.py::test_restart_survival_via_doorbell` (+ `tests/address/test_park_resume.py::test_dur4_crash_safe_resume_path_b_two_bills`) | parked leg survives restart to same terminal outcome; trap: throwaway-child-session / new-task-per-round |
| CUJ-8 | `tests/test_live_doctype_add.py::test_live_doctype_add_regenerates_card` | skill dropped into `BRIDGE_SKILLS_DIR` → next discovery GET regenerates Agent Card, no redeploy; trap: card unchanged |
| CUJ-10 | `tests/address/test_golden.py::test_cuj10_distinct_issuer_accept_real_edge` + `::test_cuj10_same_issuer_reject_real_edge` (+ `tests/address/test_gate.py`) | one gov-id OR two distinct-issuer bills satisfy; same-issuer pair rejects; trap: same-issuer counted as two |

Out-of-cycle CUJs (3, 9, 11, 12, 13) stay driverless by design (Benefits/RFP unbuilt).
CUJ-5 was previously covered-but-untagged — it now has an explicit driver in the mapping.

**PROPOSAL (not applied — spec/ is protected).** A spec-owner should add, per in-cycle
CUJ in `spec/cujs.md`, a block carrying `Steps` / `Success Criteria` / `Driver:` /
`Judge:` / `Supported By:` (the five fields `verify.md:26-33` extracts), using the Driver
node ids above. Not edited during `/implement` per the spec-proposal-only rule (same
handling as the S0-docs-1 entry above).

- No code changed (planning docs only); full suite green (490 passed, 13 skipped).
- Learning: a "catalog-not-machine-driveable" doc gap is fixed on the /implement side by
  *verifying* the drivers exist and pass, then recording the exact field mapping as a
  proposal — the drivers are the load-bearing part; the spec text is a spec-owner edit.

---

## 2026-08-24 — Learning: session-affinity trap for the A2A task_store (from vibeflix-audit)

Reviewed `weimeilin79/vibeflix-audit` (a Google-authored A2A-on-GCP reference). Its
headline production bug is directly relevant: the A2A edge is `POST message:send` then
poll `GET tasks/{id}`, and on a multi-replica substrate **without session affinity** the
stock `InMemoryTaskStore` is per-replica, so write and poll land on different replicas —
they measured an **86.8% poll-404 rate** on Agent Runtime, and a full run fell from ~5m to
~1m44s after moving to a single shared store.

Our design already dodges the trap: the GCP `task_store` adapter is Cloud SQL (a shared
relational store), not per-replica in-memory. Recorded two hardening actions so it can't
regress:
- **design/seams.md** — added "Why the task-store GCP adapter must be a shared store (the
  session-affinity trap)": rationale + the requirement that parity include a
  **cross-replica read-after-write** case (terminal-outcome parity alone passes trivially
  single-process and would miss a per-replica store) + terminal-state writes get retry +
  loud logging.
- **TODO.md P7** — added a "Session-affinity discipline" note + an exit-gate line for the
  cross-replica read-after-write case against the Cloud SQL adapter.

Explicitly NOT adopted from the reference: their `--max-instances=1` single-instance app
holding shared task state over HTTPS (a pre-GA stopgap for Agent Gateway HTTP-only egress
governance) — our Terraform-first, shared-datastore-behind-an-autoscaled-service posture is
cleaner. Their other overrides (mTLS card repointing, brief injection, 180s poll fallback,
streaming disabled) compensate for pre-GA bugs on `google-adk 2.3 / a2a-sdk 0.3.26`; we pin
`google-adk >=2.7,<3`, so each must be re-verified against our pin before porting, not copied.

---

## 2026-08-24 — Learning: Gateway/Identity hardening for P7 (from vibeflix-audit)

Two P7 acceptance points added to TODO.md under "Gateway/Identity hardening", grounded in
our existing two-zone model (`design/zones.md`):
- **Gateway HTTP-only egress trap.** The peer's Agent Gateway governs HTTP egress only, so
  their shared task store had to run over HTTPS through a single-instance app. We must keep
  the Bridge→Cloud SQL path on private IP and confirm Gateway governs ingress scoping
  without forcing internal datastore traffic onto an HTTP hop. Do not inherit the
  "app owns the task store over HTTPS" indirection.
- **Agent Card URL vs Gateway-authorized host.** Card host mismatch → 403 egress-denied
  (peer repoints to the `.mtls` host in `_resolve_agent_card`). Verify our `a2a_client`
  outbound resolves to the Gateway-authorized host; re-verify against `google-adk >=2.7`
  before porting any repointing workaround (their pin was 2.3).
- Noted per-workload Agent Identity as a follow-up if serving surfaces split (P7 currently
  names one `bridge-runtime` SA; the peer uses no shared mesh SA).

---

## 2026-09-03 — S0-bug-1: version-gated tolerant reader (not blanket extra="ignore")

**Decision.** Fixed the "wire types defeat the tolerant-reader posture" finding by making
`contract_version` load-bearing instead of relaxing the models. `contract/wire.py` models
stay `extra="forbid"`; a new `decode_wire(model_cls, data)` inspects the payload's
`contract_version` and strips unknown fields (recursively, across the model tree) **only**
when its major differs from ours, then validates. Same-major decode is unchanged (strict).

**Why this over blanket `extra="ignore"`.** Spec D2 (`spec/standards.md:35`) *deliberately*
chose `extra="forbid"` for our two-party edge and reserves the tolerant reader "for
genuinely external callers". A producer on a newer contract major *is* that external caller,
so version-gating satisfies the finding AND the spec verbatim — **no spec change required**,
and first-party typos / same-version producer bugs still hard-fail loudly. Blanket
`extra="ignore"` would have contradicted D2 and needed a spec proposal. (User chose this
direction when asked.)

**Gotchas / learnings.**
- Tolerance is additive only: a newer producer *adding* fields degrades gracefully; a
  *removed* required field still (correctly) fails — we can't fabricate missing data.
- Select-by-shape disambiguation is preserved: a wrong-shaped blob still fails on a missing
  required field even under tolerant decode (a CollectRequest never decodes as an
  ExchangeTurn), so the edge's shape-scan loops are unaffected.
- All inbound wire decodes now route through `decode_wire`: `bridge/edge/wire.py`,
  `agents/address/wire.py`, `agents/address/turn_reader.py`, `agents/address/driver.py`.
  It lives in `contract/` so `agents/` uses it without importing `bridge/` (isolation intact).
- `from __future__ import annotations` in `contract/wire.py` is safe: pydantic resolves
  `model_fields[...].annotation` to real types, so `get_origin/get_args` still work.

## 2026-09-03 — S0-sec-2 / S0-sec-3: push-webhook SSRF guard + loopback serve defaults

**Decision.** For S0-sec-2 (SSRF via A2A `pushNotificationConfig`), ship a default-deny
egress guard now rather than deferring the whole finding to P7. New
`bridge/edge/pushguard.py`: `SsrfGuardPushConfigStore` wraps any
`PushNotificationConfigStore` and validates the webhook URL in `set_info` — reject any
host that is / resolves-to a non-global IP (`ipaddress.is_global`: loopback, link-local
incl. `169.254.169.254`, private, reserved), with a `BRIDGE_PUSH_ALLOWLIST`
(host / host:port) escape hatch. Rejection raises `InvalidParamsError` so the client gets
a clean JSON-RPC error at registration. Wired into both `build_a2a_app` and
`build_mock_bridge_app`. For S0-sec-3, all `serve()`/`serve_mock()` defaults flip
`0.0.0.0` → `127.0.0.1`.

**Why guard at registration, default-deny.** Registration is the last cheap point before
the URL is stored and later POSTed; rejecting there gives the caller immediate feedback.
Default-deny (only *global* IPs pass) blocks the canonical metadata/internal-host attack
without an operator having to enumerate bad ranges.

**Scope / silent-cap note.** S0-sec-2's own text says "add allowlisting at P7". This ships
the *reachable* guard now; the remaining P7 work is (a) check-at-send re-validation to
close the DNS-rebinding window (a host public at `set_info`, private at send time), and
(b) per-party auth + Gateway egress policy (`design/zones.md`). No spec change.

**Gotchas / learnings.**
- Unresolvable hosts are ALLOWED: they can't reach an internal target, so the POST simply
  fails. This is why the existing push tests (`http://receiver/hook`, an unresolvable
  name) stay green with no env changes — only names/literals resolving to a private range
  are blocked.
- The wrapper MUST delegate `get_info_for_dispatch` (not just the abstract trio) so the
  inner store's multi-owner dispatch override is preserved — the SDK's base fallback
  silently drops notifications in multi-owner deployments.
- `guarded_push_config_store` is idempotent (won't double-wrap) so injected+seam paths and
  edge/mock both stay single-wrapped.
- `bridge/` touched → CRITICAL escalation (Opus + self-review before commit); commit
  path-scoped to leave the unrelated pre-existing `spec/open-questions.md` edit alone.

## 2026-09-03 — S0-arch-2: add the reverse isolation guard (agents/ ↛ bridge/)

**Decision.** Enforce the reverse package-isolation invariant in
`tests/test_isolation_guard.py`: `agents/` must never import `bridge/`. Previously only
`bridge/→agents/` was guarded; the reverse was documented + load-bearing but latent.

**Rationale.** The mock→real Bridge swap property (P2-5/P3-5) depends on consumers
talking to the Bridge **only over the A2A wire + shared `contract/`** — never by reaching
into the core. An `agents/`-imports-`bridge/` regression would silently break that
interchangeability; a guard turns it into a red test at the point of introduction.

**Approach.** Generalized the AST detector `_imports_agents(path)` → `_imports_package(path,
package)` and added `_scan(source_pkg, forbidden_pkg)`, so both directions share one code
path. No violation existed at commit time (`grep -rn "import bridge" agents/` = none), so
this is gap-closing, not a fix.

**Gotchas / learnings.**
- `contract/` imports must NEVER be flagged in either direction — it is the one shared
  package. Covered by a positive-control assertion.
- Match on `package` exactly OR `package.` prefix so near-miss names (`bridgehead`,
  `agentspew`) don't trip. Relative imports (`level > 0`) are never violations.
- `bridge/` touched (one-line docstring naming the enforcing test) → CRITICAL escalation
  (Opus + self-review before commit). No behavior change in bridge/.

## 2026-09-06 — S0-arch-3: seam-harness hygiene (deferred notes, abstractmethods, de-overloaded marker)

**Decision.** Cleared the three related seam-harness P2s in one pass:
- **(a)** Labelled `ExchangeStoreSeam` + `MemoryBankSeam` (and their `Local*` adapters)
  **"deferred, empty by design"** — no domain methods this cycle, no parity test, kept as
  named seams for the P7 GCP swap. Replaced the stale "filled in at P4" text (P4 shipped;
  these stayed intentionally empty).
- **(b)** Added `@abc.abstractmethod` to every non-empty seam ABC method so the base
  classes are no longer directly instantiable, matching the lone pre-existing
  `ExtractionSeam.extract`. The two empty seams keep zero abstract methods on purpose so
  their `Local*` adapters stay instantiable.
- **(c)** Removed `@pytest.mark.seam(...)` from 37 non-parity integration tests; the marker
  now sits only on the six true one-parametrized-test-per-seam parity tests (sessions,
  task_store, skill_registry, scheduler, a2a_client, extraction), matching its pyproject
  definition ("shared seam-parity tests parametrized over local/gcp adapters").

**Rationale.** The marker's meaning had drifted into a general "touches-this-seam" category
tag, diluting the load-bearing invariant that each seam has exactly ONE shared parametrized
parity test. Restoring the 1:1 mapping makes the invariant greppable again; making the ABCs
abstract turns "forgot to implement a seam method" into an instantiation-time `TypeError`
instead of a latent `NotImplementedError` at call time.

**Gotchas / learnings.**
- Safe to abstract only because every non-empty seam ABC is subclassed solely by its
  `Local*` adapter (each implements all methods) and the GCP path in `get_adapter` raises
  before instantiating — no direct ABC construction anywhere.
- Empty deferred seams MUST NOT gain abstract methods, or their empty `Local*` adapters
  would stop instantiating; "empty by design" is enforced by keeping the ABC method-free.
- No `-m seam` selection exists in CI or the repo, so stripping the marker changed which
  tests are *categorised*, not which *run* — full suite stayed green (515 passed, 13 skipped).
- `bridge/` touched → CRITICAL escalation (Opus + self-review before commit). Committed
  path-scoped to leave the unrelated pre-existing `spec/open-questions.md` edit alone.

## 2026-09-06 — S0-arch-4: record the D3/D4 as-built edge divergence (not emit WORKING)

**Decision.** Reconciled the two Ring-1 divergences at the live A2A edge by **recording the
as-built divergence in `design/a2a-edge.md`** (option 2), not by emitting a `WORKING`
progress state + park at the edge (option 1). Added an "As-built lifecycle divergence
(D3/D4)" subsection to the design doc and a cross-reference docstring in
`CollectExecutor._emit_turn`.

**Rationale.** Both divergences fall out of the already-committed Decision 1 (the edge
serves the deterministic one-turn `Collector`, not the LlmAgent):
- **D3 (no WORKING).** The Sense-A collect completes synchronously in one call, so there is
  no genuine in-connection work phase. A synthetic `WORKING` event would manufacture
  progress that didn't happen and leak an *outward* Ring-1 wire semantic an external reader
  would misread — spec/standards.md itself flags outward wire deviations as the most
  expensive class. `SUBMITTED → COMPLETED|INPUT_REQUIRED` is the honest lifecycle for a
  one-turn deterministic path.
- **D4 (one durable task).** The single-durable-task discipline is fully exercised where the
  multi-turn loop actually runs — the durable park/resume path (`terminal=False` →
  `INPUT_REQUIRED` → same task resumed via `agents/address/interceptor.py`) and mock/portal
  Path-B. On the deterministic completing path each round genuinely completes, so a new task
  per round under the same exchange `context_id` is correct, not a leak. The `context_id` is
  the durable aggregate; the per-round task is the completed unit.

Same posture as S0-arch-1: for a divergence that follows from a deliberate, load-bearing
deferral, the fix is to make it visible (design doc + code cross-ref), not to manufacture
the deferred behavior. Collapsing the completing path onto one long-lived task travels with
the LlmAgent-behind-the-edge follow-up.

**Gotchas / learnings.**
- No behavior change — docstring + docs only; suite unchanged at 515 passed, 13 skipped.
- `WORKING` stays reachable in the contract (`cancel` transitions a WORKING/INPUT_REQUIRED
  task); it is simply never *emitted* on the deterministic collect path. Verified by grep.
- `bridge/edge/executor.py` touched (docstring only) → CRITICAL escalation (Opus +
  self-review before commit); committed path-scoped, leaving the unrelated pre-existing
  `spec/open-questions.md` edit alone.

## 2026-09-06 — S0-debt-1: prove the A2A cap→escalate boundary with a golden run

**Decision.** Exercise `bridge/pathb.py` §2b's cap→escalate transition through the
agent-to-agent journey by scripting a new mock scenario (`resubmit-escalate`: three
resubmit parks then a terminal escalate) and adding a golden run that drives it via the
durable park→resume loop. No `pathb.py`/`gate.py` changes — they are the reference for
the boundary shape; the gap was a missing A2A-journey scenario + golden run.

**Rationale.** The ceiling counting is unit-tested, but the demo claim is that escalate
is reachable *over the wire*, on the same edge the real Bridge serves. The mock double is
the maintained contract-checked stand-in, so scripting the ladder there and driving it
through `run_collection(durable=True)` is the faithful A2A-journey proof. Escalate is
`PENDING`/route-to-human, never `rejected` (§2a) — the terminal turn asserts exactly that.

**Gotchas / learnings.**
- The durable resume loop **replays** the workflow graph, so `STATE_GATE_ROUNDS` always
  equals `max_rounds` regardless of scenario length (confirmed: the 2-step `resubmit`
  scenario also reports `gate_rounds == max_rounds`). It is NOT a "turns taken" metric on
  the durable path. A first-draft assertion `gate_rounds == 4` failed (got 6) and was
  dropped. The durable-path proof that the cap was crossed is the escalate **content**:
  `ScriptedBridgeExecutor` steps one scenario entry per send and clamps overrun, so
  emitting `steps[3]` is structural proof of ≥4 sends (the three parks + escalate).
- Reason code / message sourced from `bridge.offscript.REASON_ESCALATED` +
  `bridge.pathb.ESCALATE_MESSAGE` so the A2A wording can't drift from the Path-B engine.
- Touches `bridge/` → CRITICAL escalation (Opus + self-review before commit). Committed
  path-scoped; the unrelated pre-existing `spec/open-questions.md` edit was left alone.

## 2026-09-06 — Configure ruff as the project linter (T1 lint gate)

**Decision.** Added `ruff>=0.16,<0.17` to dev deps and a `[tool.ruff]` config in
`pyproject.toml`: `target-version = py312`, `line-length = 88`, lint
`select = ["E4","E7","E9","F","I","UP","B","SIM","RUF"]`, `known-first-party =
[contract, bridge, agents, skills, tests]`, per-file-ignore `F401` on `**/__init__.py`
(re-exports). `uv run ruff check` is now the T1 lint gate (recorded in CLAUDE.md).

**Rationale (choices made with the user).**
- **Ruleset = "pragmatic core"** — pyflakes + a curated pycodestyle subset + isort,
  pyupgrade, bugbear, simplify, ruff-native. High signal, moderate one-time churn
  (mostly import reordering). Broader sets (ANN/D/PL/S) were declined — too much
  pre-existing debt for no correctness gain.
- **line-length = 88, but E501 NOT in the lint select.** Enforcing E501=88 as a green
  gate would have required a 99-file `ruff format` reformat **plus ~132 manual noqas**
  (the longest lines are LLM prompt strings / docstrings / comments the formatter cannot
  wrap). Disproportionate for a config task. Instead: the formatter owns width (targets
  88 if ever run — we did NOT run it), the linter owns correctness. Standard split.
- **Ignored `UP042`** (str+Enum → `enum.StrEnum`): changes `__str__`/wire-string
  behavior of the shared `contract/` enums — out of scope; do not touch wire semantics.
- **Ignored `RUF002`**: the `→ / ≤ / §` characters in docstrings are intentional domain
  notation, not ambiguous-unicode typos.

**Gotchas / learnings.**
- The prior ambient `uvx ruff` runs left stale inline `# noqa: BLE001/D102/ANN001/PT011`
  from a broader ruleset; under the narrower select these became `RUF100` (unused-noqa)
  and were auto-removed. Grep on concise output double-counts codes that appear inside
  `RUF100` messages ("non-enabled: X") — match the *leading* code only.
- **`UP047` autofix on `contract/wire.py` was broken:** it added `def decode_wire[M:
  BaseModel]` but left the body/signature on the old `_M` TypeVar, leaving `M` dangling
  (tests still passed because `_M` survived). Completed the PEP 695 migration by hand
  (`[M: BaseModel]`, `type[M] -> M`, dropped the now-dead `_M` TypeVar + `TypeVar`
  import). Lesson: review UP-class autofixes on protected paths — a green suite hid the
  half-migration. This is why `contract/`/`bridge/` fixes got a manual diff review.
- 127 safe + 25 unsafe autofixes applied (RUF005 concat→unpack, SIM102 nested-if,
  RUF022 `__all__` sort, etc. — all behavior-preserving; RUF005 tuple-concat and
  tuple-unpack are equivalent). Remaining hand-fixes: `F821` (added `TYPE_CHECKING`
  `httpx` import in `driver.py`), `SIM102` combines, `RUF043` raw `match=` string, two
  intentional `SIM105` defensive `try/except/pass` kept with narrow `# noqa: SIM105`.
- Full suite unchanged: 516 passed, 13 skipped. `ruff format --check` reports 96 files
  would reformat — expected and intentionally not acted on (no formatting gate).

## 2026-09-06 — S0-docs-3: design/outbound.md sequencing → status-free + TODO.md

**Decision.** Removed the "Sequencing — what lands when" section (with its "designed,
not yet built" phrasing and per-cycle "Outbound built | Cycle" table) from
`design/outbound.md`, replacing it with a status-free "One architecture, demand-driven
build" statement and a pointer to `TODO.md` for build sequencing.

**Rationale.** Design docs are the status-free HOW (as built); live status and
per-cycle sequencing belong in `TODO.md` only (see the user strip-status invariant).
The cycle-build table duplicated sequencing that already lives in `TODO.md` (P5 silent-cap
note + "Deferred to later cycles") and `docs/roadmap.md`, so it was pure drift risk with
nothing to lose by removing it.

**Gotcha / learning.** The "move sequencing to TODO.md" half of the task was already
satisfied by prior phases — verify before duplicating. When stripping a table from a
design doc, confirm the sequencing has a live home elsewhere rather than assuming you
must re-add it; here re-adding would have created a second source of truth.

## 2026-09-06 — Replan: close Sprint 0, open Sprint 1 (Benefits), defer P7

**Decision.** Sprint 0 hardening closed (all sec/arch/docs/bug/debt findings done + archived,
T3 gate green). Next sprint is the **Benefits demo cycle (CUJ-3/11/12)** — dial-out + Deliver,
Negotiate/FX, ADK↔LangGraph interop — **not** the previously-queued P7 GCP deploy.

**Rationale.** User chose Benefits over P7 at the replan priority prompt. The outbound seam,
trust boundary, and contracts are already whole ([outbound](design/outbound.md)), so Benefits adds demand-driven
capability against existing machinery.

**Consequence / no silent cap.** P7 (GCP substrate + deployed-path parity, roadmap M7) is now
**deferred, not dropped**: Address runs **local-only** until P7 is scheduled — no GCP seam
adapters, no Terraform, no deployed parity. Logged in TODO.md's "Deferred to later cycles"
(the full P7 phase plan is retained) and reflected in the header. `docs/roadmap.md` is left
unchanged (status-free milestone view — it describes the plan, not live sprint status).

**T3 evidence at close.** 73/73 deterministic in-cycle CUJ drivers green (CUJ-1,2,4,5,6,7,8,10),
all hard-fail traps intact. External jeep judge not re-run for this docs-only sprint close
(last green 2026-08-22; no CUJ-supporting code changed since — the only Sprint 0 task closed
after that, S0-docs-3, touched a design doc only).

## 2026-09-06 — S1-benefits-1: Benefits skills + Program aggregate (beat 1 · CUJ-11)

**Decision.** Authored the two Benefits skills and a pure in-memory Program fan-out
aggregate — config + a small Bridge aggregate only, no extraction/FX/flagger/dial-out/
Negotiate/bind/rollup (those are S1-benefits-2..7 and consume this).

**Skill-name collision (load-bearing).** The spec names BOTH the process skill and the
doctype skill `benefits-quote`. The local skill registry (`LocalSkillRegistry`) is a flat
directory keyed by directory name with `SKILL.md` frontmatter `name == dir name`, so two
folders cannot both be `benefits-quote`. Resolution: keep the **doctype** skill
`benefits-quote` (the more load-bearing name — it appears in the process skill's
`candidate_doctypes`, the card's `doctype:<name>` tag, and becomes the
classification/extraction label consumed by S1-benefits-2+ and the eval corpus) and name
the **process** skill `benefits-procurement`. `CollectRequest.process` for this exchange
is `"benefits-procurement"`; the process skill declares `bridge-candidate-doctypes:
benefits-quote`. The registry was NOT changed to key by `(kind, name)` — that would break
`get_skill(name)`'s single-return contract and the seam parity test; distinct names need
zero `bridge/` change for the skills.

**SPEC PROPOSAL (not applied — spec/ is protected).** In `spec/demos/benefits-demo.md`
and `spec/cujs.md` (CUJ-11), rename the *process* skill reference from `benefits-quote` to
`benefits-procurement` (doctype stays `benefits-quote`), noting the flat-registry
"name == directory" constraint that forces distinct names. Left for a spec-owner pass.

**Program scope (beat 1).** `bridge/program.py` is a pure, side-effect-free aggregate:
`Leg(party, context_id)`, `Program(program_id, legs)`, `open_program`, `add_leg`,
`fan_out`. Beat-1 proves fan-out + the isolation invariant (N legs → N distinct minted
`context_id`s; each party a distinct leg/context — competitors never share a context;
duplicate party raises `ValueError`). No seam, no persistence, no ADK, no wire type
(isolation guard intact — imports stdlib only). Context ids minted with `uuid.uuid4().hex`
(the edge idiom).

**No silent cap — deferrals.** `program_id` on the wire (adding it to
`contract.CollectRequest`) is deferred to S1-benefits-4 (dial-out fan-out); per-leg bind /
program rollup / materialized exchange record are S1-benefits-5. Program identity stays a
Bridge-side aggregate this task; it does not ride the wire yet.

**Gotcha / deviation from plan — second process skill breaks free-text intake.** Adding a
second **process** skill removed `bridge.edge.intake.parse_text_request`'s "if exactly one
process skill exists, default to it" fallback (that fallback was documented as a
single-skill-reality stopgap). The plan's gotcha #2 anticipated only WRONG-skill matches
in `tests/test_edge_a2a.py` (avoided by tag naming) and expected no other regressions —
but 22 mock-driven tests (`tests/address/test_wake.py`, `test_workflow.py`) seed
scenario-only free text (`scenario:<name>`) that named no process, so they relied on the
sole-skill fallback and began raising `ValueError`. (JSON-seeded and golden tests were
unaffected — they carry `process` explicitly.) **Fix:** compose the seed/resume text to
carry BOTH the process token and the scenario marker
(`agents/address/driver.py::_intake_text`, used by `_seed_content` + `_resume_content`;
plus the one direct-seed in `test_workflow.py`). This restores mock→real parity per the
design's own `_seed_content` note ("a bare process string is the real-edge fallback; each
edge reads what it understands") rather than reintroducing a guess-the-process fallback
that would let the mock silently accept text the real edge rejects. Real production runs
(message=None) are unchanged: text is still `req.process`.

**Verification.** T1: full suite 539 passed, 13 skipped; `ruff check` clean; isolation
guard green. T2: all four acceptance checks pass (skills load with correct kinds/
candidates; card advertises `benefits-procurement` with `doctype:benefits-quote` and no
standalone `benefits-quote` skill; `load_sla_policy` max_nudges==2 + monotone schedule +
`negotiate.revise_cap==2`; `fan_out` yields 4 distinct contexts/parties and rejects
duplicates).

**CRITICAL escalation.** Touches `bridge/` (new `bridge/program.py`) → auto-escalates
(stronger model + mandatory reviewer before commit). Staged, not committed; reviewer
commits path-scoped, leaving the unrelated pre-existing `spec/open-questions.md` edit alone.

## 2026-09-06 — S1-benefits-2: Option A (currency/language in key_fields) + live-FX-seam deferred

**Decision.** Multilingual extraction + FX normalization shipped as `bridge/normalize.py`
with the normalize oracle (4 carrier quotes in `wiki/evals/benefits/expected.json`) and a
fixed FX rate table (deterministic demo). Two load-bearing choices:

**1. Currency/language carried in `key_fields` (Option A, no contract change).**
`contract.ExtractionFields` has no top-level `currency`/`language` fields, but the
`benefits-quote` doctype schema declared them as required. S1-benefits-2 is the first task
to need both (normalize requires them to FX-convert + translate). Option A: carry
`currency` and `language` **inside `key_fields`** (`dict[str,str]`) with no `contract/`
change. Rationale: keeps this task off the `contract/` critical path (a contract change
ripples to `decode_wire` + every consumer); the wire model already transports arbitrary
`key_fields`. Reconciled `skills/benefits-quote/assets/schema.json` + `validation.yaml` to
match (moved currency/language into `key_fields.properties` + `key_fields.required`; tests
stayed green). Alternative (not chosen): add optional `currency`/`language` to
`contract.ExtractionFields` as top-level wire fields — heavier, `contract/`-critical,
deferred unless a reviewer insists.

**2. Live-FX-seam deferred (no silent cap).** `spec/demos/benefits-demo.md` Design
parameters: "a fixed rate table drives the deterministic demo; a live FX seam is the
alternative shape." Task delivered a **thin FX swap point** (`FxRates = Callable[[str],
float]` protocol with a default `fixed_fx` reading the fixed rate table; `normalize_quote`
takes `fx: FxRates = fixed_fx`), but did NOT build a full local+GCP FX seam this cycle —
that is over-engineering the stated "fixed FX rate table" requirement. Live-FX-seam
(real exchange rate lookup) is **logged as designed-but-unbuilt** in the
`bridge/normalize.py` module docstring (no silent cap). A live seam swap is a one-argument
change when needed.

**Gotchas / learnings.**
- `FixtureExtractor._load_corpus` is lru_cached by the path **string**, so benefits corpus
  at `wiki/evals/benefits/expected.json` and Address corpus at
  `wiki/evals/address/expected.json` cache independently — you MUST pass the explicit
  corpus path when extracting benefits quotes (the default is Address). Tests reuse the
  corpus-path idiom (`Path(__file__).resolve().parents[N] / ...`).
- Monetary values kept as strings end-to-end (corpus round-trip idiom); `decimal.Decimal`
  used only inside the money helper, then formatted back to a 2-dp string. Do NOT expose
  floats on `NormalizedQuote` (float FX math is non-deterministic across platforms).
- Subset assertion in `tests/test_benefits_skills.py` means adding currency/language to
  `key_fields.required` is safe (it asserts a ≤ subset); removing the *top-level*
  currency/language properties is not checked by any test — verified by running the file.
- `extra="forbid"` on `NormalizedQuote` — provenance fields explicit, no silent expansion.

**SPEC PROPOSAL (not applied — spec/ is protected).** In `spec/demos/benefits-demo.md`,
add a note that currency/language ride in `key_fields` on the extraction wire (Option A)
— or, if the spec-owner wants them first-class on the wire, that is a
`contract.ExtractionFields` addition (Option B) to be scheduled separately.

## 2026-09-06 — S1-benefits-3: standard-gap flagger (sense A) + the rule that isolates Carrier B

**Decision.** Shipped the beat-4 standard-gap flagger as `bridge/standard.py` — a pure,
synchronous transform that flags each **normalized** quote against the declared
program-standard rules and emits a sense-A `StandardGapVerdict` (`{party, conformant, gaps,
route}`, route ∈ {`negotiate`, `none`}). The Bridge SURFACES the gap; it never decides the
deal (no accept/reject/bind field — bind is the agent's sense-B call, S1-benefits-5).

**Rule set re-authored to actually flag someone (load-bearing).** The S1-benefits-1
placeholder rules (`deductible_cap`, `must_cover_dental`) flag NOBODY over the corpus — all
4 carriers have deductible ≤ 1000 and include "dental". Beat 4 requires Carrier B to show
*the* coverage gap and drive the revise loop. Added **`no_orthodontics_exclusion`**
(`must_not_include: orthodontics`) — Carrier B is the sole carrier excluding orthodontics,
so it is the one flagged leg. Rejected `must_cover_prescription` (would also flag C and D,
muddying "Carrier B shows *the* gap"). Also re-pointed `field:` from `key_fields.*` to
`NormalizedQuote` attribute names: flagging runs post-normalization (one currency), so a
numeric cap like deductible ≤ 1000 is comparable across carriers.

**Finalized check vocabulary: `max` / `must_include` / `must_not_include`.** Unknown check
(at load) or unknown field (at eval) degrades LOUDLY (`ValueError` naming it), mirroring
`fixed_fx`'s known-codes style — a misconfigured standard fails visibly, never silently.
Loader is a local `_load_standard_doc` mirroring `bridge.sla._load_policy_doc` (follows the
`metadata.bridge-program-standard` pointer) — `bridge/sla.py` left untouched, off the change
surface.

**No silent cap — deferrals.** (a) `route` stays a Bridge-internal string constant;
promoting it to a `contract/` enum so it rides the A2A wire to trigger dial-out/Negotiate is
**S1-benefits-4/5**. (b) The versioned re-flag on a revised quote is **S1-benefits-5** (this
task produces the initial verdict only). (c) Agent standard-override is supported by
construction (`flag_quote` takes an explicit `ProgramStandard`, `overridable: true`), but
wiring an override *channel* is a caller concern, out of scope. Logged in the module
docstring too.

**wiki/evals oracle authored, not fitted.** Added an `expected_gaps` array to each corpus
doc (carrier-b → `["no_orthodontics_exclusion"]`, others `[]`) — the correct expected gaps
under the default standard, additive to S1-benefits-2's `expected_normalized` (existing
values untouched). The flagger reproduces them; the oracle was authored to correct values,
not edited to make a test pass.

**Gotchas / learnings.**
- `StandardGap.expected` for a `max` gap renders the rule's `limit` (`f"<= {limit}"`), so
  it is `<= 1000.0` when the limit came through `float()` in `from_dict` and `<= 1000` when a
  test constructs the rule with an int — the string reflects the operand type, only the
  `rule_id`/routing are the load-bearing assertions.
- No seam / no `contract/` change (same posture as `bridge/normalize.py`): a pure transform
  consuming `NormalizedQuote` + a declared skill asset. No spec edit needed — the spec
  (`spec/demos/benefits-demo.md` beat 4) already describes this exactly.
- Touches `bridge/` (new `bridge/standard.py`) + `wiki/evals/` (new `expected_gaps` field) →
  CRITICAL escalation (Opus + mandatory reviewer before commit). Staged path-scoped; the
  unrelated pre-existing `spec/open-questions.md` edit left alone. Full suite: 566 passed,
  13 skipped; `ruff check` clean; isolation guard green.

## 2026-09-06 — S1-benefits-4: dial-out + Deliver on the one fake transport (CUJ-3)

**Decision.** Built the two remaining outbound activities — **dial-out** (Bridge-as-client
originating an A2A exchange: Agent-Card discovery → `message/send`, OUT-3) and **Deliver**
(push the finished comparison outward by reference, OUT-4) — on the SAME `a2a_client` seam
(SEAM-7) local fake transport that already carries the nudge. Generalized `OutboundClient`
to dispatch any `OutboundMessage` (a `runtime_checkable` Protocol exposing `idempotency_key`)
through one private `_dispatch()`, so the crash-safe re-drive semantics are identical across
`send` / `dial` / `deliver`; the `nudge:` / `dial:` / `deliver:` key prefixes namespace the
receipt store so keys never collide. Added `bridge/comparison.py` (the apples-to-apples
aggregate Deliver pushes) composing S1-benefits-2 normalize + S1-benefits-3 flag.

**Discovery gates, does not decorate.** `dial()` resolves the peer `PeerCard` via an injected
`PeerDirectory` (local `FakePeerDirectory`, mirroring `FakeTransport`) and **rejects**
(`ValueError`) a peer that does not advertise the requested `process` — the "Agent-Card
discovery" half of the task. Without it the task would degenerate to a blind send; the
rejection path is tested (`tests/test_dial_out.py`).

**Kept off the critical path (no `contract/` / `wiki/evals/` change).** Deliver reuses the
existing `contract.wire.ArtifactRef` (by-reference — bytes never travel on the wire); the
comparison itself is a **Bridge-side** pydantic model (`bridge/comparison.py`), not a wire
type — same posture as S1-benefits-2/3 keeping models Bridge-internal. The corpus already
carries `expected_normalized` + `expected_gaps`; the comparison composes them (no corpus
field added).

**No silent cap — deferrals** (logged in the `bridge/outbound.py` module docstring): the
**real** ADK dial via `RemoteA2aAgent` + consuming a counterparty `input-required` park →
**S1-benefits-6**; the **authenticated egress transport** (workload Agent Identity, card-vs-
Gateway-host reconciliation) → **P7 GCP adapter** (registry still raises `NotImplementedError`);
the **multi-turn Negotiate revise loop** + versioned artifacts + bind/rollup → **S1-benefits-5**
(this task delivers the *initial* comparison only).

**SPEC PROPOSAL (not applied — spec/ is protected).** `program_id`-on-the-wire (adding it to
`contract.CollectRequest`) was flagged by S1-benefits-1's DECISIONS entry for this task, but
the local fan-out/dial-out works without it — the `Program` aggregate carries program identity
Bridge-side. It is only needed when a **real** counterparty must read it; deferred to
S1-benefits-6. If a spec-owner/reviewer wants the comparison first-class on the wire, that is a
separate `contract/` addition to schedule.

**No bearer token — everywhere.** `DialOut` and `Delivery` carry no token/credential field
(mirrors `test_nudge_carries_no_bearer_token`); tested by dataclass-field assertions.

**Gotchas / learnings.**
- Kept `send(nudge)` / `NudgeReceipt` bit-for-bit — the nudge tests
  (`t.outbox == [nudge]`, `c.sent() == [r]`, exact field set) stayed green unchanged.
- `FixtureExtractor` is lru-cached by path string and defaults to the Address corpus — the
  T2 clean-interpreter snippet MUST pass `corpus_path=wiki/evals/benefits/expected.json`
  (adjusted from the plan's illustrative snippet per its own §6 note).
- Extended the ONE `a2a_client` parity test rather than adding a second (DECISIONS 2026-09-06
  S0-arch-3 — exactly one `@pytest.mark.seam` per seam); GCP branch still raises.
- Touches `bridge/` (new `bridge/comparison.py`; edits to `bridge/outbound.py`,
  `bridge/__init__.py`) → CRITICAL escalation (Opus + mandatory reviewer before commit).
  Staged path-scoped; reviewer commits, leaving the unrelated pre-existing
  `spec/open-questions.md` edit alone. Full suite: 581 passed, 13 skipped; `ruff check`
  clean; isolation guard green.

## 2026-09-06 — S1-benefits-5: versioned revise loop → bind → rollup (beats 5,6 · CUJ-11)

**Decision.** Shipped the CUJ-11 payoff tail as ONE cohesive Bridge-side aggregate,
`bridge/negotiate.py`, composing the prior beats (`program` fan-out, `normalize`,
`standard`, `comparison`) with **no `contract/` change and no new `Seam`** — the same
posture as beats 1-4. The module holds the whole beats-5,6 surface: the domain-object-level
versioned revise loop (a leg holds an ordered tuple of `QuoteVersion`s; attempt N is version
N, v0 initial / v1 first revision — `design/artifacts.md`), the comparison recompute
(`comparison_from_negotiation` pulls each leg's current version and reuses
`build_comparison` — flagging is not reimplemented), per-leg **sense-B** bind (recording-only,
no accept/reject gate, non-conformant binds allowed), the program rollup, the append-only
audit trail (monotonic `seq`, kinds `leg_opened`/`quote_revised`/`reflagged`/`leg_bound`),
and the `negotiate:` policy loader. Every function is pure and returns new immutable state
(the `program.py` replace-and-return idiom).

**One-module choice.** bind / rollup / audit / revise all operate on the same versioned-leg
state, so one cohesive aggregate is the right seam (not four modules). Pure imports only:
stdlib + pydantic + `bridge.normalize`/`standard`/`comparison` + `bridge.sla._load_policy_doc`
+ the two escalate constants; NEVER `agents/` (isolation guard green).

**Policy loader reuses `bridge.sla._load_policy_doc`** (the same trick `bridge/standard.py`
used) so `bridge/sla.py` stayed off the change surface. `load_negotiate_policy` reads the
`negotiate:` block and validates `revise_cap` is an int `>= 1` (rejecting `bool` too),
raising a `ValueError` naming the skill on a missing/invalid block — mirrors
`load_sla_policy`'s error style. `benefits-procurement` → `revise_cap == 2`.

**Cap → escalate, never reject (sourced, not restated).** `revise()` enforces `revise_cap`;
exceeding it raises `ReviseCapExceeded(ValueError)` whose `reason_code` /`message` are
sourced from `bridge.offscript.REASON_ESCALATED` / `bridge.pathb.ESCALATE_MESSAGE`, so the
wording can't drift from the Path-B engine (the same sourcing S0-debt-1 used to prove the
§2b cap→escalate boundary). This is a route-to-human escalate, not a reject and not a silent
crash. The golden run revises once (within cap = 2); a dedicated unit test + the T2.3 snippet
drive the boundary and assert the escalate `reason_code`.

**Over-the-wire pushback DEFERRED to S1-benefits-6 (logged, not silent).** This task builds
the revise-loop machinery Bridge-side and drives it off the corpus revised quote; the real
multi-turn A2A send + a live counterparty that returns the revision belong to -6 (ADK Carrier
A / LangGraph Carrier B). The pushback is deliberately NOT routed through
`OutboundClient.dial`: `DialOut.idempotency_key` is `dial:{context_id}`, so a second dial to
the same leg dedupes as `resent` — wrong semantics for a distinct negotiate turn, and fixing
it means editing the CRITICAL `outbound.py` dedup for behavior -6 replaces. `outbound.py` was
left untouched; the deferral is logged in the `bridge/negotiate.py` module docstring.

**Corpus revised-doc authored (CRITICAL — `wiki/evals/`).** Added carrier-b's revised quote
`benefits-carrier-b-usd-en-v2` to `wiki/evals/benefits/expected.json`: identical to the v0
carrier-b doc except `exclusions: "cosmetic surgery"` (drops orthodontics), with its authored
`extraction` / `expected_normalized` / `expected_gaps: []`. Authored to correct values, not
fitted to a test; the four existing docs are byte-for-byte unchanged (only the top-level
`note` was updated — verified `git diff` = additive + the note line). The additive fifth doc
required updating the beats-2/3/4 corpus-oracle tests: the iterate-every-doc oracles now
assert `len(docs) == 5` and still check every doc reproduces its own block (the revised doc
reproduces `expected_normalized` + `[]` gaps); the program-level "one leg per carrier" checks
(`test_comparison`, `test_carrier_b_is_the_sole_flagged_leg`) filter out the `-v2` revision so
a program still has four legs (a revision is a new version of a leg, not a fifth leg).

**No SPEC PROPOSAL needed.** `spec/demos/benefits-demo.md` beats 5,6 already describe this
exactly (versioned revise → recompute → bind/rollup → audit). The prior S1-benefits-1/2/4
proposals (process-skill rename, currency/language Option A, program_id-on-the-wire) still
stand for a spec-owner pass; the over-the-wire-pushback-in-6 and domain-object-level-
versioning-over-the-corpus notes are recorded here (this entry), not written to spec.

**Verification.** T1: full suite 596 passed, 13 skipped; `ruff check` clean; isolation guard
green. T2: all four checks pass — CUJ-11 golden run green; the recompute+bind+rollup snippet
prints `OK` (carrier-b flagged before / all ROUTE_NONE after, bound at version 1, audit
carries `quote_revised` + `leg_bound`); the cap snippet escalates with
`reason_code == escalated_for_review`; the corpus diff is additive (45 insertions, the note
line the only deletion).

**CRITICAL escalation.** Touches `bridge/` (new `bridge/negotiate.py`, edit
`bridge/__init__.py`) + `wiki/evals/` (additive revised-quote oracle) → auto-escalates
(stronger model + mandatory reviewer before commit). Staged path-scoped; reviewer commits,
leaving the unrelated pre-existing `spec/open-questions.md` edit alone.

## 2026-09-06 — S1-benefits-5: versioned revise, cap→escalate sourcing, wire-pushback-in-6

**Decision.** Shipped the CUJ-11 payoff tail (beats 5,6: versioned revise loop → bind →
rollup) as one cohesive `bridge/negotiate.py` module composing the prior beats with no
`contract/` change and no new `Seam`. Four load-bearing choices:

**One-module choice.** bind / rollup / audit / revise all operate on the same
versioned-leg state, so one cohesive aggregate (`bridge/negotiate.py`) is the right seam
(not four modules). Pure imports: stdlib + pydantic + `bridge.normalize`/`standard`/
`comparison` + `bridge.sla._load_policy_doc` + the two escalate constants; NEVER
`agents/` (isolation guard green). Policy loader reuses `bridge.sla._load_policy_doc`
(the same trick `bridge/standard.py` used) so `bridge/sla.py` stayed off the change
surface.

**Cap → escalate sourcing (not restatement).** `revise()` enforces `revise_cap`;
exceeding it raises `ReviseCapExceeded(ValueError)` whose `reason_code` / `message` are
sourced from `bridge.offscript.REASON_ESCALATED` / `bridge.pathb.ESCALATE_MESSAGE`, so
the wording can't drift from the Path-B engine (the same sourcing S0-debt-1 used). This
is a route-to-human escalate, not a reject and not a silent crash. Tested: the golden
run revises once (within cap = 2); unit tests + T2 acceptance drive the boundary and
assert the escalate `reason_code`.

**Over-the-wire pushback deferred to S1-benefits-6 (logged, not silent).** This task
builds the revise-loop machinery Bridge-side and drives it off the corpus revised quote;
the real multi-turn A2A send + a live counterparty that returns the revision belong to
-6 (ADK Carrier A / LangGraph Carrier B). The pushback is deliberately NOT routed through
`OutboundClient.dial`: `DialOut.idempotency_key` is `dial:{context_id}`, so a second
dial to the same leg dedupes as `resent` — wrong semantics for a distinct negotiate
turn, and fixing it means editing the CRITICAL `outbound.py` dedup for behavior -6
replaces. `bridge/outbound.py` was left untouched; the deferral is logged in the
`bridge/negotiate.py` module docstring (no silent cap).

**Corpus revised-doc authored (CRITICAL — `wiki/evals/`).** Added carrier-b's revised
quote `benefits-carrier-b-usd-en-v2` to `wiki/evals/benefits/expected.json`: identical
to the v0 carrier-b doc except `exclusions: "cosmetic surgery"` (drops orthodontics),
with its authored `extraction` / `expected_normalized` / `expected_gaps: []`. Authored
to correct values, not fitted to a test; the four existing docs are byte-for-byte
unchanged (only the top-level `note` was updated). The additive fifth doc required
updating the beats-2/3/4 corpus-oracle tests: the iterate-every-doc oracles now filter
out the `-v2` revision so a program still has four legs (a revision is a new version of
a leg, not a fifth leg).

**Gotchas / learnings.**
- Versioning is domain-object-level over the corpus this cycle — a leg holds an ordered
  tuple of `QuoteVersion`s (attempt N is version N, v0 initial / v1 first revision per
  `design/artifacts.md`), driven off `FixtureExtractor` extracting both the v0 and v2
  corpus docs. There is no ADK `ArtifactService` call this task; the real versioned
  artifact flow travels with the ADK/LangGraph counterparties (S1-benefits-6).
- `bridge/` touched → CRITICAL escalation (stronger model + mandatory review before
  commit); `wiki/evals/` touched → CRITICAL escalation. Staged path-scoped; the
  unrelated pre-existing `spec/open-questions.md` edit left alone.
- Full suite: 596 passed, 13 skipped; `ruff check` clean; isolation guard green.
  T2: CUJ-11 golden run (`tests/test_cuj11_negotiate_bind_rollup.py`) passes;
  recompute+bind+rollup snippet shows carrier-b flagged before / all `ROUTE_NONE` after;
  cap snippet escalates with `reason_code == escalated_for_review`.

## 2026-09-06 — S1-benefits-6: cross-framework carriers as real A2A servers (CUJ-12)

**Decision.** Stood up the two simulated benefits carriers as genuine external-zone A2A
servers under `agents/benefits/` — **Carrier A in Google ADK**, **Carrier B in
LangGraph** — proving the SAME A2A quote conversation succeeds against both builds and
that an ADK consumer can dial the LangGraph counterparty. The two internals are
deliberately different frameworks: Carrier A's is a deterministic `google.adk`
`BaseAgent` (no `LlmAgent`, no Gemini) run through a stock `Runner` +
`InMemorySessionService`; Carrier B's is a compiled `langgraph` `StateGraph` (a
classifier conditional edge routes a first quote → `quote_v0`, a revise pushback →
`revise_v2`). Both wrap their internal in an a2a-sdk `AgentExecutor` and emit the SAME
framework-neutral wire (`agents/benefits/wire.py`): one `benefits-quote` JSON data Part
tagged `metadata.kind` (select-by-metadata, D5), decoded back to `contract.ExtractionResult`.

**Quote truth stays the corpus oracle.** Both carriers serve quotes read-only from
`wiki/evals/benefits/expected.json` (`agents/benefits/quotes.py`): Carrier A →
`benefits-carrier-a-usd-en` (conformant), Carrier B v0 → `benefits-carrier-b-usd-en`
(holds `no_orthodontics_exclusion`), Carrier B revised → `benefits-carrier-b-usd-en-v2`
(gap closed). No quote data is hardcoded or duplicated; the corpus is unchanged this task.
The revise loop is **stateless** — v0-vs-revised is decided purely from the turn text
(`wire.classify_turn` on a `revise` marker), which keeps the LangGraph edge a clean
classify→node and both carriers deterministic (no server-side turn counter).

**Self-contained server assembly (reverse isolation guard).** `agents/benefits/server.py`
re-implements the minimal a2a-sdk 1.1.x assembly (card routes + JSON-RPC routes over a
`DefaultRequestHandler` + `InMemoryTaskStore` on a `Starlette` app) rather than importing
`bridge/edge/server.py` — `agents/` must never import `bridge/`. The isolation guard stays
green. New-task-first gotcha handled the same way as `bridge/edge/executor.py` (enqueue a
`ph.new_task(...)` before the first `TaskUpdater` call).

**Dependency.** Added `langgraph>=1.2,<2` (resolved 1.2.11); it resolves additively with
no ADK/a2a downgrade.

**Gotchas / learnings.**
- Bridge-core authenticated egress transport (the real dial-out over a live network with
  credentials) remains **P7**. -6's "real dial + live counterparty" is embodied as
  external-zone carrier servers + an ADK `RemoteA2aAgent` consumer over an in-process
  `httpx.ASGITransport` — no `bridge/outbound.py` / `bridge/negotiate.py` change (their
  deferral docstrings are untouched; no silent cap).
- ADK `RemoteA2aAgent` rejects a non-loopback card RPC URL ("must use https, or http on a
  loopback host"): the ADK-consumer test dials Carrier B under `http://localhost` (the
  in-process client cases use arbitrary hosts freely). `use_legacy=True` per the TEST-4
  experimental pin; the injected in-process `httpx_client` is the test seam.
- A generic `application/json` A2A data part surfaces through `RemoteA2aAgent` as an ADK
  genai part with the JSON wrapped in `<a2a_datapart_json>…</a2a_datapart_json>`; the
  consumer test reuses `agents/address/wire.py::decode_part_payload` (tag-stripping) to
  decode it back to an `ExtractionResult`.
- No task added to `TODO.md` (S1-benefits-6 pre-existed from `/new-feature`); counter
  untouched. Staged path-scoped (`agents/benefits/**`, `tests/test_cuj12_interop.py`,
  `pyproject.toml`, `uv.lock`, `TODO.md`, `DECISIONS.md`); the unrelated pre-existing
  `spec/open-questions.md` working-tree edit left alone.
- Full suite: 602 passed, 13 skipped (GCP-gated); `ruff check` clean; isolation guard green.
  T2: `tests/test_cuj12_interop.py` all 6 pass — same-conversation parametrized over
  both builds, LangGraph multi-turn revise (v0 negotiate→v2 none, responses provably
  differ), ADK-consumer-dials-LangGraph, framework-diversity structural guard, no-token-
  on-wire. Deterministic — no Gemini/API keys.

## 2026-09-07 — S1-benefits-7: program comparison view — recompute-on-read design

**Decision.** Added the program comparison view (backend read-model surface + frontend
side-by-side legs display) with a **recompute-on-read** design for the in-memory program
board (`bridge/dashboard/board.py`). The board stores `NegotiationProgram` objects (not
snapshotted `ProgramComparison`s) and recomputes the comparison on every read via
`comparison_from_negotiation(program, standard)` — this is what satisfies "recomputes live
as revised versions land." When a revised program is put into the board, the next GET or
SSE snapshot reflects the recomputed comparison with updated verdicts/gaps.

**Backward compatibility.** Extended `bridge/dashboard/readmodel.py` and `server.py` with
optional `board` parameters: when `board is None` (the default), the routes and SSE
snapshot behavior are **byte-identical** to the P6-4 dashboard (no `programs` key added).
When a board is injected, `GET /programs` serves the recomputed comparisons and
`GET /events` SSE includes them in the snapshot. The existing `test_dashboard.py` suite
(board=None path) stayed green with zero changes.

**Content-not-pixels relay (A2UI-1 discipline).** The frontend view
(`ProgramComparison.tsx`) relays each gap's `message` and `rule_id` verbatim from the
Bridge — the view arranges and labels only, authors no prose. This mirrors the dashboard's
existing relay posture and honors the A2UI-1 "content-not-pixels" boundary.

**Home = Ops Dashboard, not provider portal.** Per `design/frontend.md`, the Benefits
demo surfaces the program comparison in the **Ops Dashboard** (internal, read-model-over-
SSE showcase), not the provider portal (the only true A2UI Path-B human-input host). The
console and dashboard are observability surfaces, not A2UI protocol hosts. The task title
says "A2UI view" loosely; the actual home is the internal dashboard read-model.

**Deferred, not dropped (no silent cap).** A **durable** program store (persisting
programs beyond in-memory state) is logged as deferred-not-dropped in the
`bridge/dashboard/board.py` module docstring (mirroring the deferral-logging style in
`bridge/negotiate.py` and `bridge/outbound.py`).

**Gotchas / learnings.**
- `FixtureExtractor` is lru-cached by corpus-path **string** and defaults to the Address
  corpus — benefits tests must pass `corpus_path=<benefits expected.json>` explicitly, or
  they get Address docs (same gotcha logged in S1-benefits-2/4/5).
- `model_dump(mode="json")` on `ProgramComparison` already yields snake_case with nested
  `quote` + `verdict.gaps`; the frontend `mapComparison` handles snake→camel. No custom
  dict assembly needed for `project_comparison`.
- Gap detection in the view flags on `verdict.route === "negotiate"` (or equivalently
  `gaps.length > 0` — per `bridge/standard.py`, `ROUTE_NEGOTIATE` iff gaps present).
  `ROUTE_NONE === "none"` is conformant.
- pnpm workspace members are `frontend/*` per `pnpm-workspace.yaml`; `@docbride/shared` is
  `workspace:*` (source-only, no build step — Vite/Vitest resolve the TS). Run frontend
  commands from repo root with `pnpm --filter ops-dashboard <script>`.
- Dashboard SSE has no terminal state — it closes on `max_ticks` (see `sse_frames`
  docstring). Tests bound `max_ticks` (e.g. 50) so `/events` doesn't hang.
- `bridge/dashboard/` touched → CRITICAL escalation. Staged path-scoped; the unrelated
  pre-existing `spec/open-questions.md` working-tree edit left alone.
- Full suite: 608 passed (added 6 cases), 13 skipped; `ruff check` clean; isolation guard
  green; `pnpm --filter ops-dashboard build` + `test` green. T2: side-by-side 4 legs all
  USD/en, carrier-b flagged before revise / conformant after, SSE reflects recompute.

## 2026-09-07 — Applied S0-docs-2: Driver:/Judge: fields added to spec/cujs.md

Under **direct spec-owner authorization** (not `/implement` — the proposal-only rule is
scoped to automated implementation), added a "Drivers & Judges (verification wiring)"
section to `spec/cujs.md` carrying a `Driver:` (e2e harness node id) + `Judge:`
(deterministic criterion + hard-fail trap) per CUJ. This closes the S0-docs-2 proposal
(2026-08-21) so `/verify` Phase-1 can machine-extract per-CUJ drivers instead of reading
the mapping out of this log.

Coverage now spans all **built** CUJs — the Address set (1,2,4,5,6,7,8,10) from the
original S0-docs-2 mapping **plus** the Benefits set (3,11,12), whose driver node ids were
confirmed green in the 2026-09-07 `/verify` run (73/73 deterministic driver tests pass;
jeep LLM judge 1.00 on all 11 runnable CUJs). CUJ-9 and CUJ-13 stay `Driver: — (unbuilt)`
(RFP cycle). Fields are verification metadata, not status — spec/ stays status-free.

## 2026-09-07 — Remove test-only `build_runner` from bridge core; injectable carrier task_store

**Trigger.** Audit of App/Runner construction across `agents/` + `bridge/` for the
"test-only factory living in production code with hardcoded in-memory services" smell.

**Findings.** The unit of deployment every serving surface consumes is the ADK `App`
(`root_agent`) — `adk run/web/api_server` and the Cloud Run host build the `Runner` and
wire session/artifact services themselves (from CLI URIs / seam adapters). A `Runner`
pre-pinned to `InMemory*` inside the core is therefore not a production on-ramp; it
duplicates/conflicts with what the host supplies. Two offenders shared this shape:
- `bridge/agent.py::build_runner` — hardcoded `InMemorySessionService` (not even a
  param), zero production callers, only `tests/test_agent.py`.
- `agents/benefits/server.py::build_carrier_app` — hardcoded `InMemoryTaskStore`.

**Decision.**
1. **`build_runner` → relocated to the test.** Deleted from `bridge/agent.py` (+ the now
   -unused `Runner`/`InMemory*`/`BaseArtifactService` imports); the two tests build the
   Runner via a test-local `_build_runner()` in `tests/test_agent.py`. The LlmAgent
   mediator has no serving surface this cycle (see S0-arch-1), so its host belongs with
   the tests that exercise it, not in the reusable core. `build_app` stays — it *is* the
   deployment artifact.
2. **Benefits carriers → made `task_store` injectable, NOT relocated.** CUJ-12's thesis is
   "carriers as *real* A2A servers" and `serve_carrier` is the (pragma-marked) deploy
   hook, so relocating the app assembly into tests would contradict the design. Instead
   `build_carrier_app` now takes `task_store: TaskStore | None = None` (default in-mem),
   mirroring the clean `bridge/edge/server.py::build_a2a_app` reference idiom. The private
   `_produce_quote` per-quote `InMemorySessionService` is left as-is — fresh-per-quote is
   correct for a stateless carrier, not a host-controlled durability seam.

**Verification.** `ruff check` clean; full suite 607 passed / 13 skipped; isolation guard
green; no dangling `build_runner` references outside the test-local helper.

## 2026-09-07 — P7-1: Terraform scaffold — substrate-by-primitive + credential-free plan-time testing

**Decision.** Built the foundational GCP Terraform infrastructure as three primitive
modules (network, iam, runtime) with a plan-time test harness that validates resource
shape **without GCP credentials**. Two load-bearing architectural choices were locked in:

**1. Substrate-by-primitive module layout (not one-module-per-platform-service).**
Module boundaries are stable infrastructure primitives — `network`, `iam`, `runtime`,
and (from P7-2 onward) `database`, `tasks`, `secrets`, `gateway`, `frontend`. The
Bridge's platform services (Agent Runtime, Sessions/Memory Bank, Skill Registry, Gateway,
Identity, Extraction) are deliberately **not** modeled as same-named Terraform modules;
they are realized through the **seams' GCP adapters** — config + IAM + client code in
`bridge/seams/gcp/`. This keeps the IaC layer stable across substrate swaps and allows
the six later P7 primitives to attach as small deltas without restructuring the module
set (`design/gcp-substrate.md` §45). The root `main.tf` documents the extension seams
(explicit wiring points each later primitive will consume).

**2. Credential-free plan-time testing with `mock_provider` (the P7 exit criterion).**
Every `*.tftest.hcl` declares `mock_provider "google"` + `mock_provider "google-beta"`
and uses `command = plan` run blocks, so `terraform test` validates the planned resource
shape with **no GCP authentication** — no `GOOGLE_APPLICATION_CREDENTIALS`, no
`gcloud auth`. Assertions target input-derived / statically-known values (CIDRs, ingress
mode, IAM role strings, resource counts) rather than provider-computed IDs, so the tests
state real intent and remain deterministic across runs. This is the P7 plan-time gate:
developers iterate on HCL without project access, CI runs in any environment, and
resource shape is locked before any live apply (which happens only at P7-9,
deployed-path parity).

**What was built:**

- **Root module**: `versions.tf` (Terraform >= 1.7, google/google-beta >= 6.0),
  `variables.tf` (project_id, region, service_name, container_image, enable_gateway,
  labels), `main.tf` (provider config + three module calls + documented extension seams),
  `outputs.tf`, `terraform.tfvars.example`.

- **`modules/network/`**: Custom-mode VPC with the **two-zone model** (`design/zones.md`)
  — external subnet (DMZ/ingress, 10.10.0.0/24) and internal subnet (trusted,
  10.20.0.0/24), both with private-IP Google access. Firewall isolation rule enforces
  the zone boundary. Outputs network/subnet self-links for later modules (P7-2 uses
  `internal_subnet_self_link` for Cloud SQL private-IP PSA).

- **`modules/iam/`**: `bridge-runtime` service account with base project grants
  (`roles/cloudsql.client`, `roles/cloudtasks.enqueuer`, `roles/aiplatform.user`) plus
  `roles/iam.serviceAccountTokenCreator` scoped **to the SA resource itself** (via
  `google_service_account_iam_member`, not a project-level binding) for Cloud Tasks OIDC
  callbacks. `extra_roles` variable allows later primitives to extend grants without
  editing the module.

- **`modules/runtime/`**: Cloud Run v2 service skeleton hosting the ADK App (FastAPI +
  a2a-sdk edge + A2UI portal) — does **NOT** wrap Vertex Agent Engine (Agent Engine only
  appears behind the Sessions seam, P7-6). Template runs as bridge-runtime SA with
  placeholder container image (real image wired at deploy/P7-9). **Direct VPC egress**
  into the internal subnet (`egress = "PRIVATE_RANGES_ONLY"`) for the private-IP Cloud
  SQL path (P7-2) — datastore traffic stays on VPC, never forced onto an HTTP hop
  (avoiding the Gateway HTTP-only egress trap from the vibeflix-audit, DECISIONS
  2026-08-24). **Computed ingress** from `enable_gateway`:
  `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` when true (production, behind Gateway P7-5),
  `INGRESS_TRAFFIC_ALL` when false (local/dev default).

- **Test harness** (`terraform/tests/*.tftest.hcl`): per-module tests via
  `module { source = "./modules/..." }` override plus a root integration test, all
  using `mock_provider` + `command = plan`. Assertions on CIDRs, ingress mode, SA
  grants, resource counts (input-derived, not computed IDs). The `enable_gateway`
  branch tested both ways (two runtime runs asserting the computed ingress value).

- **.gitignore updates**: Added `terraform/**/.terraform/`, `*.tfstate*`, `crash.log`.
  Deleted `terraform/.gitkeep` (dir now has real content).

**Gotchas / learnings.**

- **Token-creator scope is critical.** `roles/iam.serviceAccountTokenCreator` is granted
  on the SA **resource itself** via `google_service_account_iam_member`, not as a
  project-level `google_project_iam_member`. This scopes the grant so Cloud Tasks can
  mint OIDC tokens as bridge-runtime but nothing else can. A project-level grant would
  be a privilege-escalation vector.

- **Expose computed values as module outputs for plan-time assertions.** To assert the
  `enable_gateway` branch in a `command = plan` test, expose `computed_ingress` (the
  ternary value) as a runtime module output. Asserting the resource's `ingress`
  attribute directly also works, but the output makes the branch explicit and stable.

- **Assert on statically-known values, not mocked IDs.** `mock_provider` synthesizes
  opaque values for computed attributes; prefer asserting on attributes you set from
  variables (CIDRs, role strings, service_account email) so the test states real intent.

- **No live apply in P7-1.** Everything is plan/test only. `terraform apply` and real
  project provisioning happen at P7-9 (deployed-path parity gate against a live project
  with `BRIDGE_TEST_GCP=1`).

**Acceptance verified (all six criteria passed):**
1. `terraform -chdir=terraform fmt -check -recursive` clean
2. `terraform -chdir=terraform init -backend=false` succeeds (no GCP auth)
3. `terraform -chdir=terraform validate` passes
4. `terraform -chdir=terraform test` green with NO credentials configured
5. Negative check confirms creds not needed (still exit 0 with credentials unset)
6. No live resources created (mock_provider only, no apply)

The foundation is ready for P7-2 (database + shared-store adapters) through P7-7
(frontend deploy) to attach as small deltas to the scaffold.

## 2026-09-07 — P7-2: Cloud SQL shared stores on private IP + cross-replica parity mandatory

**Decision.** Built the GCP seam adapters for `sessions` / `task_store` / `exchange_store`
as **shared Cloud SQL Postgres stores** (not per-replica in-memory) with the database
on **private IP only** and a mandatory **cross-replica read-after-write parity test**.
Three load-bearing choices:

**1. Same ADK/a2a class, Postgres DSN + retry wrapper (not a new store implementation).**
`GcpSessions` / `GcpTaskStore` build the identical `DatabaseSessionService` /
`DatabaseTaskStore` / `DatabasePushNotificationConfigStore` the local adapter builds — the
parity claim is "same class, different DSN." The only differences: (a) DSN source =
`DATABASE_URL` secret env (not a local file), (b) driver = async Postgres
(`postgresql+asyncpg://...`, not sqlite), (c) terminal-state writes wrapped in a retry +
loud-logging layer (`_TerminalRetryTaskStore`). Rationale: the mock→real swap is
transparent to the agent when the class is identical; inventing a new store implementation
would break seam parity and require re-testing every ADK/a2a interaction.

**2. Cross-replica read-after-write parity case is mandatory (proves shared-store, not
just terminal-outcome).** Terminal-outcome parity alone (same task reaches the same end
state) passes trivially single-process and would NOT catch a per-replica in-memory store
— it would green-light exactly the 86.8%-poll-404 session-affinity trap the
`vibeflix-audit` reference measured. The parity claim MUST include: write a terminal task
via one adapter handle, read it back via a second independent handle to the **same backing
store**, with no dispose between (two concurrent engines/pools = two-replica shape). For
`local` this uses a `tmp_path` file sqlite DSN (always runs, proves shared-store property;
in-memory would fail). For `gcp` it uses the Postgres DSN (skipped unless
`BRIDGE_TEST_GCP=1` + provisioned Cloud SQL). This is the load-bearing session-affinity
guard and a named P7-9 exit criterion.

**3. Cloud SQL on private IP only, never an HTTP hop.** The `database` module enforces
`ipv4_enabled = false` + `private_network = <vpc>` so the instance has **no public IP**.
The Bridge→Cloud SQL path is private-IP over the VPC via Direct VPC egress (`egress =
"PRIVATE_RANGES_ONLY"`, P7-1 runtime) — datastore traffic never touches an HTTP hop.
Rationale: the Gateway HTTP-only egress trap from `vibeflix-audit` forced their shared
task store over HTTPS through a single-instance app; we dodge the trap by keeping the
datastore connection on private IP, preserving the two-zone trust boundary
(`design/zones.md`) without surrendering to an HTTP-mediated indirection.

**Gotchas / learnings.**

- **Terminal-state write retry + loud logging.** `_TerminalRetryTaskStore` wraps the inner
  `DatabaseTaskStore` and, **only when task.status.state is terminal** (completed / failed
  / canceled / rejected per a2a-sdk's `TERMINAL_TASK_STATES`), retries `save()` on
  exception with bounded attempts (default 3) and loud logging (`logging.getLogger` WARNING
  on each retry, ERROR if all attempts fail then re-raise). Non-terminal saves pass
  straight through. Rationale: a lost terminal write hangs the poller to its deadline, so
  it fails far louder than a dropped intermediate `working` write (`design/seams.md`). The
  wrapper is unit-testable without GCP — fake inner store raising once then succeeding
  proves the retry.

- **No in-memory fallback (the trap guard).** The GCP adapter refuses to build when no DSN
  is configured (`DATABASE_URL` env unset, no `db_url` arg) and raises a clear
  `RuntimeError` mentioning "the session-affinity trap" — it will NOT silently fall back
  to an in-memory store. An in-memory gcp store on multi-replica is exactly the bug this
  task exists to prevent, so refusing-to-build is a correctness invariant, not a style
  choice.

- **No secrets in HCL (CRITICAL).** The `secrets` module creates the secret **container
  only** — no `google_secret_manager_secret_version` resource (structural guard: the
  plan-time test asserts no such resource exists in the module). The DSN value is injected
  out-of-band at deploy (P7-9). The `database` module omits the DB user password (IAM auth
  or a sensitive deploy-time variable). No plaintext DSN / password / credential in any
  `.tf` / `.tfvars` — this is a CRITICAL-path invariant enforced by the
  no-`secret_version` test.

- **PSA (Private Services Access) lives in the `database` module, not `network`.** The PSA
  reserved range (`google_compute_global_address`, `purpose = "VPC_PEERING"`) + the
  `google_service_networking_connection` peering are Cloud-SQL-specific, so they live in
  the **`database` module** (consuming `module.network.network_id`), keeping the generic
  `network` module untouched. This preserves the substrate-by-primitive module layout —
  P7-1's three primitives stay stable; `database` + `secrets` attach as small deltas.

- **`ExchangeStoreSeam` is empty by design.** No domain methods this cycle (the exchange
  rides the durable session + task_store), no `@pytest.mark.seam` test. So
  `GcpExchangeStore` is an empty `pass` class mirroring `LocalExchangeStore` — created for
  the uniform seam invariant (every seam has a local + gcp adapter), no methods, no parity
  test.

- **`pyproject.toml` gcp extra is lazy-loaded.** The gcp adapters import `asyncpg` only
  transitively via `create_async_engine("postgresql+asyncpg://...")`, and only when a gcp
  store is actually built. Importing `bridge.seams.gcp` does NOT pull asyncpg at module
  load (lazy imports inside methods mirror the local pkg). The default (non-GCP) test
  suite does not need asyncpg installed.

**Acceptance.** Full suite green (`uv run pytest`); `ruff check` clean; isolation guard
green; cross-replica read-after-write (local) passes (write via handle A to file DSN, read
via independent handle B — the session-affinity guard); terminal-retry wrapper unit test
passes (success-after-one-failure + WARNING logged, exhausted-retries + ERROR + raise,
non-terminal-no-retry); gcp adapter refuses in-memory + rejects sync Postgres DSN;
registry dispatch correct (`Seam.sessions` / `task_store` / `exchange_store` return
`Gcp*` classes, unimplemented seams raise); Terraform plan-time credential-free (`fmt`,
`init`, `validate`, `test` all exit 0 with no GCP auth); no live apply (P7-9).

## 2026-09-07 — P7-3: GCS artifacts is an ADK swap (not a seam); authorize-before-sign + lifecycle-not-GC

**Decision.** Delivered GCS-backed artifacts and skill registry with three load-bearing
choices that distinguish them from the P7-2 shared-store seam pattern:

**1. GcsArtifactService is a native ADK backend swap, NOT a seam.** The artifact backend
is an ADK `InMemoryArtifactService` ↔ `GcsArtifactService` swap (exactly like Sessions
swaps `InMemory…` ↔ `VertexAISessionService`), not a `bridge/seams/` adapter. The
seam-in-waiting lives in `bridge/edge/artifacts.py` (`build_artifact_store()` factory)
and is wired in `server.py`/`executor.py` via the `artifact_store or build_artifact_store()`
idiom (injectable param wins). There is **no** `@pytest.mark.seam` artifact parity test,
no `Seam.artifact` enum entry — the swap point is the factory + the P7-8 wiring guard.
Rationale: ADK already defines the artifact abstraction boundary; mirroring Sessions'
native swap keeps the artifact path off the seam registry and preserves the P2 injection
property (tests inject a store; prod builds one from env).

**2. Authorize before signing (leg-scoping preserved under signed URLs).** The signed-URL
fetch handler MUST 404 on a foreign/unknown (context_id, filename) **before** minting a
signed URL — otherwise a guessed path becomes a capability. `build_fetch_app(..., signer)`
probes `_load_any_party(store, context_id, filename, v)` to confirm the blob exists and
discover its owning `user_id` (the leg-scoping check); **404 if absent**, then
`RedirectResponse(signer(...), 307)` to the time-limited V4 signed URL. Bytes never ride
the response (client fetches directly from GCS). This preserves "a reference is not a
capability" (`design/artifacts.md`) — a signed URL for a guessed path is NOT mintable,
same enforcement as the local byte-streaming path.

**3. Lifecycle Delete rule owns retention (ART-3); Bridge keeps zero GC code.** The GCS
object-lifecycle Delete rule (`lifecycle_rule` on the artifacts bucket, `age =
retention_days` default 365) implements ART-3 (retain during the exchange, delete only
after the post-exchange window). The Bridge has **ZERO** artifact GC/TTL runtime code —
the ONLY Bridge-side deletion is the off-script explicit erase
(`bridge/offscript.py::handle_deletion_request`, a user action, unchanged). Object
versioning (`versioning.enabled = true` on both buckets) means a resubmission creates a
new version (ART-1 resubmission history) — the lifecycle rule expires superseded versions
after the window. Rationale: GC is a substrate primitive (GCS lifecycle), not Bridge
runtime logic, so the retention policy belongs in Terraform (the `gcs` module), not
`artifacts.py`.

**Gotchas / learnings.**

- **skill_registry IS a seam.** `GcpSkillRegistry(SkillRegistrySeam)` in
  `bridge/seams/gcp/__init__.py` implements the seam by download-to-temp + reuse
  `skill_meta_from_dir` (same format, different store — mirrors P7-2). The format is the
  seam contract (`design/seams.md`); frontmatter parsing is NOT re-implemented. Bucket
  name from `BRIDGE_SKILLS_BUCKET` env; refuses (RuntimeError) if unset (no in-memory
  fallback). Reuses the S0-sec-1 traversal defense (name `^[a-z0-9-]+$`, assets must not
  escape `<name>/`) before upload.

- **Signing needs no new IAM.** IAM SignBlob on Cloud Run rides the SA's existing
  self-scoped `roles/iam.serviceAccountTokenCreator` (P7-1 iam module,
  `tasks_token_creator` binding). The gcs module's ONLY new grant is bucket object access
  (`roles/storage.objectAdmin` per-bucket to the SA — least-privilege, mirrors the
  secrets module).

- **Buckets never public (load-bearing security).** `public_access_prevention = "enforced"` +
  `uniform_bucket_level_access = true` on both buckets. Signed URLs (V4, time-limited) are
  the **only** external byte path. This is a security invariant, not a style choice —
  asserted in `terraform/tests/gcs.tftest.hcl`.

- **Lazy imports everywhere.** Importing `bridge.seams.gcp` or `bridge.edge.artifacts`
  must NOT pull `google.cloud.storage` (mirrors P7-2's asyncpg). Lazy-import `from
  google.cloud.storage import Client` and `from bridge.edge.skills import skill_meta_from_dir`
  inside methods only. Tested by `sys.modules` assertion.

- **No secrets in HCL.** Bucket names are plain runtime envs (not secrets). No bucket
  objects, no keys, no credentials in `.tf` / `.tfvars`. Plan-time tests need no GCP auth.

- **Same format, different store.** `GcpSkillRegistry` downloads each skill prefix to a
  `tempfile.TemporaryDirectory`, then calls `skill_meta_from_dir(tmp/<name>)` — the
  identical parser the local adapter uses. Format is the seam contract; `skills-ref
  validate` gates both sides.

- **P7-8 wiring-guard compatibility.** The `artifact_store or build_artifact_store()`
  injection seam (param wins) is preserved — `build_artifact_store()` may construct
  `InMemoryArtifactService` internally (the reachable-via-`or` fallback the guard
  permits), but injected stores always win (all existing tests unaffected).

- **Runtime bucket-name envs are PLAIN env, not secret_key_ref** (unlike the DB DSN —
  bucket names are not sensitive). Each gated `for_each = var.<x>_bucket != "" ? [1] : []`
  dynamic block in the runtime module so the P7-1 no-bucket default keeps runtime tests
  green.

**Acceptance.** Full suite green (`uv run pytest`); `ruff check` clean; isolation guard
green; Terraform plan-time credential-free (`fmt`/`init`/`validate`/`test` all exit 0
with no GCP auth); `skill_registry` gcp adapter wired (`get_adapter(Seam.skill_registry,
SeamMode.gcp)` → `GcpSkillRegistry`, no longer raises); refuse-without-bucket loud
(RuntimeError mentioning env); traversal defense before I/O (`install_skill("../evil",
...)` → `ValueError` before network); lazy imports verified (`google.cloud.storage` not
in `sys.modules`); signed-URL endpoint tests green (307 redirect when signer + blob
exists, 404 on missing, no-signer streams bytes); store factory swaps on env
(`BRIDGE_ARTIFACT_BUCKET` set → `GcsArtifactService`, unset → `InMemoryArtifactService`).
Live GCS roundtrip deferred to P7-9.

## 2026-09-07 — P7-4: Cloud Tasks scheduler is poll→push (advance raises); OIDC not bearer; no new IAM

**Decision.** Delivered the Cloud Tasks scheduler adapter (`GcpScheduler`) + the
`modules/tasks` Terraform queue module with three load-bearing architectural choices that
distinguish the scheduler seam from the P7-2 shared-store pattern:

**1. Poll→push impedance match — `advance()` raises `NotImplementedError` (the honest
divergence).** The `Scheduler` protocol has `advance(ticks)` / `advance_to(tick)` methods
that fast-forward a virtual clock (used by tests and `bridge/timewarp/host.py` to drive
the follow-up ladder without waiting). These methods have **no meaning** against Cloud
Tasks — a real queue fires callbacks at wall-clock `schedule_time`; there is no "skip
ahead". Rather than fake a poll loop or a background poller, `GcpScheduler.advance()` /
`advance_to()` raise `NotImplementedError` with a clear message ("Cloud Tasks is
wall-clock/push-driven; virtual advance is local-only"). This is the honest divergence.
Parity is **terminal-outcome** (the ladder reaches `escalated`), not ledger-identical
(both adapters manipulate the same timeline). The follow-up ladder logic
(`FollowupLadder.on_fired` → stamp → re-arm) is adapter-agnostic; only *who calls
`on_fired`* differs (test/timewarp `advance` loop locally; OIDC callback on deployed
path). `timewarp/host.py` stays on `VirtualClock` (dev tool, never runs against gcp
adapter).

**2. OIDC token authorization, not bearer token / API key.** The Cloud Task's HTTP target
carries an `OidcToken(service_account_email=<bridge-runtime SA>, audience=<callback URL>)`,
NOT a bearer token or API key in the body/headers. This OIDC auth is what the P7-1
SA-scoped `roles/iam.serviceAccountTokenCreator` grant enables (Cloud Tasks mints tokens
as bridge-runtime to call bridge-runtime). The receiving Cloud Run service validates the
OIDC token (per-party `run.invoker`, P7-5). No credential/secret rides the task body
(mirrors `test_nudge_carries_no_bearer_token` — the outbound nudge path already forbids
bearer tokens in HTTP requests).

**3. P7-4 consumes existing IAM; it adds none.** The task title says "IAM Cloud Tasks OIDC
token-creator grant on `bridge-runtime`" — that grant **already exists** from P7-1.
`terraform/modules/iam` grants the SA `roles/cloudtasks.enqueuer` (base role, enqueue
permission) and a **self-scoped** `roles/iam.serviceAccountTokenCreator` (granted on the
SA resource itself via `google_service_account_iam_member`, not project-level —
least-privilege). P7-4 delivers the `modules/tasks` queue module + the `GcpScheduler`
adapter that **consume** these grants; the diff adds **no** IAM resource or binding. The
`tasks` module has a header comment stating this (mirrors `modules/gcs/`).
`terraform/tests/iam.tftest.hcl` already asserts both grants (unchanged in P7-4).

**Gotchas / learnings.**

- **Idempotent re-arm via deterministic task name.** Re-arming the same `key` (e.g.
  `followup:<context_id>`) must target the **same Cloud Tasks task**, not create a
  duplicate (crash-safe re-arm). Achieve by sanitizing `key` to the `[A-Za-z0-9_-]`
  charset (replace `:` / `/` / other non-allowed chars) into a deterministic task name.
  Cloud Tasks rejects duplicate names (`AlreadyExists`) and dedups recently-deleted names
  for ~1h, so re-arm = **delete then create** (or create, on `AlreadyExists`
  delete+create). This mirrors `VirtualClock`'s "same key overwrites" in-memory dict.

- **Callback-URL self-reference is a Terraform cycle; resolve with a runtime var.** The
  follow-up callback URL is the runtime's own `service_uri` (the Cloud Tasks POST target),
  which is only known post-apply — wiring `module.runtime.service_uri` back into
  `module.runtime.env` creates a dependency cycle. Resolved by exposing a
  `followup_callback_url` runtime variable (default `""`) injected as a plain env when
  set. At plan time / P7-1 default it's `""` (no env, keeping runtime tftest green). The
  P7-9 deploy step sets it from the resolved URL (mirrors the DB DSN VALUE out-of-band
  injection pattern from P7-2).

- **Runtime envs are PLAIN, gated dynamic blocks.** `BRIDGE_TASKS_QUEUE` /
  `BRIDGE_RUNTIME_SA_EMAIL` / `BRIDGE_FOLLOWUP_CALLBACK_URL` are not secrets (queue path
  and SA email are resource identifiers; callback URL is the runtime's own public
  endpoint) — plain `env { name/value }`, each `for_each = var.x != "" ? [1] : []` so the
  P7-1 no-tasks default leaves the container env untouched (mirrors P7-3 bucket-name
  pattern; NOT a `secret_key_ref`).

- **Refuse-without-config, never silent in-process fallback.** Missing queue path /
  callback URL / SA email raises `RuntimeError` naming the env (no silent in-process
  alarm store). Rationale: an in-process alarm store on multi-replica Cloud Run would
  silently drop armed alarms on the replica that didn't arm them — the scheduler analogue
  of the session-affinity trap (`_resolve_dsn`'s refuse-in-memory is the precedent).

- **Receiver endpoint + live firing are P7-9 (documented deferral, not silent).** P7-4
  delivers the **arming half** (the adapter mints the correct OIDC-authenticated Cloud
  Task with the right `schedule_time` / callback URL / idempotent re-arm). The HTTP
  endpoint that receives the Cloud Tasks POST and re-drives `FollowupLadder.on_fired` →
  re-arm is **NOT built in this task**. Rationale: (a) it is the deployed-wiring /
  follow-up-worker concern riding the same "LlmAgent / follow-up behind the edge" deferral
  logged in TODO.md "Deferred to later cycles" and DECISIONS.md 2026-08-21 (S0-arch-1);
  (b) the live end-to-end firing (arm → the queue POSTs → the ladder reaches `escalated`)
  is the P7-9 deployed-path parity gate, exactly as P7-2/P7-3 left their Cloud SQL / GCS
  roundtrips to P7-9. Documented in the `GcpScheduler` docstring (loud, not silent).

- **Lazy imports everywhere.** Importing `bridge.seams.gcp` must NOT pull
  `google.cloud.tasks` (mirrors P7-2 asyncpg / P7-3 google-cloud-storage discipline).
  Lazy-import `from google.cloud import tasks_v2` inside methods only. Tested by
  `sys.modules` assertion in `tests/test_gcp_scheduler.py`.

- **No secrets/values in HCL.** Queue path / callback URL / SA email are plain runtime
  envs (resource identifiers, not credentials). No task objects in HCL (tasks are runtime
  data armed through the adapter, never Terraform-managed). Plan-time tests need no GCP
  auth.

- **One seam marker per seam.** Keep `@pytest.mark.seam("scheduler")` on
  `test_scheduler_local_builds_virtualclock` only (the ONE parity test); don't add a
  second marked test (DECISIONS.md 2026-09-06 S0-arch-3). Other un-parametrized tests
  (`idempotent_rearm`, `cancel`, `same_tick`, `backwards`) already early-return on gcp —
  left as-is.

- **`now()` is wall time on gcp, virtual counter locally.** Both return `int`; the ladder
  only orders/compares them. Don't try to make gcp `now()` a virtual counter — wall-clock
  epoch seconds (`int(time.time())`) is the honest implementation.

**Acceptance.** Full suite green (`uv run pytest`); `ruff check` clean; isolation guard
green; Terraform plan-time credential-free (`fmt`/`init`/`validate`/`test` all exit 0
with no GCP auth, incl. new `tasks.tftest.hcl`); `scheduler` gcp adapter wired
(`get_adapter(Seam.scheduler, SeamMode.gcp)` → `GcpScheduler`, previously raised);
refuse-without-config loud (`RuntimeError` naming env); poll→push divergence honest
(`advance(1)` / `advance_to(1)` raise `NotImplementedError` mentioning wall-clock/push);
lazy imports verified (`google.cloud.tasks` not in `sys.modules`); seam parity +
structural tests green (gcp branch returns `GcpScheduler`, mints right OIDC task with
`schedule_time` from `fire_at`, POST to callback URL, idempotent re-arm by task name,
cancel deletes, advance raises); IAM already granted (grep confirms enqueuer +
token-creator in `modules/iam`; P7-4 diff adds no IAM); Terraform queue module plan green
(queue name, location, retry_config declared, queue_path output correct); local behavior
untouched (follow-up ladder + SLA + outbound nudge run on `VirtualClock`, unchanged).
Live roundtrip (real Cloud Tasks fire + callback endpoint) deferred to P7-9.

---

## 2026-09-07 — P7-5: Gateway ingress + Agent Identity a2a_client (adk >=2.7 same-origin, no .mtls repoint)

**Decision.** Delivered the `modules/gateway` Terraform module (external HTTPS LB →
Serverless NEG on the runtime + per-party `run.invoker` scoping), the `GcpA2AClient`
a2a_client GCP adapter (an Agent-Identity transport over `OutboundClient`), and the
served-card `BRIDGE_PUBLIC_URL` env. Four load-bearing choices:

**1. adk >=2.7 same-origin supersedes the 2.3-era `.mtls` card-repoint hack.** google-adk
`>=2.7` (verified 2.7.1) `RemoteA2aAgent._validate_card_rpc_targets` enforces that a
network-fetched Agent Card may only aim RPC traffic at its own origin. The vibeflix-audit
`.mtls` card-repoint (rewrite a fetched card's rpc URL to a sidecar) would now HARD-FAIL,
so it is NOT ported. Instead the outbound half **reconciles explicitly and refuses loud**:
`reconcile_card_host(advertised_url, gateway_host)` returns the URL on an origin match and
raises `ValueError` naming BOTH hosts on a mismatch (never a silent repoint). The served
half advertises the Gateway-authorized origin so a consumer's `RemoteA2aAgent` passes the
same check — `bridge.edge.card.resolved_card_url()` honors `BRIDGE_PUBLIC_URL` (explicit
url still wins; env-unset → `DEFAULT_URL`, local/test unchanged), threaded through
`build_a2a_app` into BOTH the card url and the executor `base_url`.

**2. Agent Identity = a per-call OIDC ID token, NEVER a message bearer field.** The
transport mints a workload OIDC ID token with `audience == the reconciled URL` and POSTs
it as an `Authorization: Bearer` header per call. No outbound message dataclass (`Nudge`,
`DialOut`, `Delivery`) carries a token/credential field — asserted by
`test_outbound_carries_no_bearer_token_field` (mirrors the scheduler's OIDC-not-bearer
invariant). Both the ID-token fetcher (`google.oauth2.id_token`) and the httpx sender are
**injectable**, so the unit + parity tests run credential-free; the real fetcher/sender
path is exercised only under `BRIDGE_TEST_GCP` (a P7-9 input).

**3. Gateway governs INGRESS ONLY — the Bridge→Cloud SQL private-IP path is never pushed
onto an HTTP hop.** The module builds only the external-facing LB (Serverless NEG, backend
service, url map, managed SSL cert, HTTPS proxy, global address, forwarding rule on
:443) + per-party `run.invoker` IAM; it touches no egress/datastore/private-network
resource. Enforced by T2.8 (`grep -RniE "firewall|egress|vpc_access|sql|database|cloudsql"
terraform/modules/gateway/` → no match) and by `runtime.tftest.hcl`/`root.tftest.hcl`
preserving the private-IP invariant when the gateway is enabled. All resources are
count/for_each-gated on `enable_gateway` (default false); `gateway.tftest.hcl` proves
disabled=nothing, enabled=ingress LB, per-party invoker — all credential-free
(mock_provider, `command = plan`).

**4. Refuse-without-config, loud.** `build_client()` resolves `gateway_host` (then
`sa_email`) from `BRIDGE_GATEWAY_HOST` / `BRIDGE_RUNTIME_SA_EMAIL`, raising `RuntimeError`
naming the missing env — it NEVER falls back to a silent fake transport that would swallow
outbound sends on the deployed path (the fake-transport-swallow / session-affinity trap
analogue). Nudge/Delivery (no `card_url`) are deferred-live-half no-ops so terminal-outcome
parity holds (the `OutboundClient` still mints receipts); DialOut (has `card_url`) does the
full reconcile→mint→POST. Lazy imports: importing `bridge.seams.gcp` pulls neither
`google.oauth2` nor `google.auth.transport.requests` (mirrors the P7-2/3/4 lazy-import
discipline; httpx/base-google.auth are already pulled by the parent `bridge` package's
a2a-sdk/adk deps, so they are deliberately not asserted absent).

**Deviation from plan.** `build_client(*, gateway_host, sa_email, id_token_fetcher,
sender)` does NOT take a `directory` param — the peer directory is passed to
`client.dial(dialout, directory=...)`, matching the local adapter idiom (build the client,
dial with a directory) rather than baking the directory into the client at build time.

**Acceptance.** T1: full `uv run pytest` green (660 passed, 16 skipped); `ruff check`
clean; `terraform fmt -check`/`validate`/`test` all exit 0 credential-free (13 passed incl.
the 3 new `gateway.tftest.hcl` runs); isolation guard green;
`test_gcp_unimplemented_seams_raise_until_their_p7_task` updated (a2a_client dropped — its
gcp adapter now lands). T2.1–T2.8 all executed and observed green: adapter resolves to
`GcpA2AClient`; refuses without config naming `BRIDGE_GATEWAY_HOST`; reconcile match
passes / mismatch refuses loud naming both hosts; local↔gcp terminal-outcome parity +
mismatch case (`test_a2a_client_seam.py` + `test_gcp_a2a_client.py` 11 passed, 2 skipped);
OIDC token minted with the authorized audience + no bearer field; card url honors
`BRIDGE_PUBLIC_URL` and falls back to `DEFAULT_URL`; gateway module credential-free; no
egress/datastore token in the gateway module. Live authenticated wire send (real Gateway +
counterparty) deferred to P7-9 / S1-benefits-6, gated on `BRIDGE_TEST_GCP`.

---

## 2026-09-07 — P7-5: Gateway ingress + Agent Identity a2a_client (adk >=2.7 same-origin, no .mtls repoint)

**Decision.** Delivered the `modules/gateway` Terraform module (external HTTPS LB →
Serverless NEG on the runtime + per-party `run.invoker` scoping), the `GcpA2AClient`
a2a_client GCP adapter (an Agent-Identity transport over `OutboundClient`), and the
served-card `BRIDGE_PUBLIC_URL` env. Four load-bearing choices:

**1. adk >=2.7 same-origin supersedes the 2.3-era `.mtls` card-repoint hack.** Re-verified
against the installed `google-adk 2.7.1` (`a2a-sdk 1.1.2`):
`RemoteA2aAgent._validate_card_rpc_targets` (new since 2.3) now enforces that a
network-fetched Agent Card may only advertise RPC URLs with **the same origin the card
was fetched from** (`card_origin != source_origin` → raises `AgentCardResolutionError`).
The vibeflix-audit `.mtls` card-repoint hack (rewrite a fetched card's RPC URL to a
sidecar) would now **HARD-FAIL**, so it is deliberately NOT ported. Instead:
- **Consumer side** (Address's `RemoteA2aAgent` → Bridge): the served Bridge Agent Card
  must advertise the Gateway-authorized origin — satisfied by `BRIDGE_PUBLIC_URL` env →
  `resolved_card_url()` → card `url` + executor `base_url`.
- **Outbound side** (our `a2a_client` seam): `reconcile_card_host(advertised_url,
  gateway_host)` returns the URL on an origin match and raises `ValueError` naming BOTH
  hosts on a mismatch (never a silent repoint). A mismatch is a misconfiguration.

**2. Agent Identity = a per-call OIDC ID token, NEVER a message bearer field.** The
transport mints a workload OIDC ID token with `audience == the reconciled URL` via
`google.oauth2.id_token.fetch_id_token` and POSTs it as an `Authorization: Bearer`
header. No outbound message dataclass (`Nudge`, `DialOut`, `Delivery`) carries a
token/credential field — asserted by `test_outbound_carries_no_bearer_token_field`
(mirrors the scheduler's OIDC-not-bearer invariant). Both the ID-token fetcher and the
httpx sender are **injectable**, so the unit + parity tests run credential-free; the real
fetcher/sender path is exercised only under `BRIDGE_TEST_GCP` (a P7-9 input).

**3. Gateway governs INGRESS ONLY — the Bridge→Cloud SQL private-IP path is never pushed
onto an HTTP hop.** The module builds only the external-facing LB (Serverless NEG,
backend service, url map, managed SSL cert, HTTPS proxy, global address, forwarding rule
on :443) + per-party `run.invoker` IAM; it touches no egress/datastore/private-network
resource. Enforced by T2.8 (`grep -RniE "firewall|egress|vpc_access|sql|database|cloudsql"
terraform/modules/gateway/` → no match) and by `runtime.tftest.hcl`/`root.tftest.hcl`
preserving the private-IP invariant when the gateway is enabled. All resources are
count/for_each-gated on `enable_gateway` (default false); `gateway.tftest.hcl` proves
disabled=nothing, enabled=ingress LB, per-party invoker — all credential-free
(mock_provider, `command = plan`). The Bridge→Cloud SQL connection stays the runtime
module's private-IP Direct VPC egress (`PRIVATE_RANGES_ONLY`, P7-1), never an HTTP hop
(sidesteps the Gateway HTTP-only egress trap structurally).

**4. Refuse-without-config, loud.** `build_client()` resolves `gateway_host` (then
`sa_email`) from `BRIDGE_GATEWAY_HOST` / `BRIDGE_RUNTIME_SA_EMAIL`, raising `RuntimeError`
naming the missing env — it NEVER falls back to a silent fake transport that would swallow
outbound sends on the deployed path (the fake-transport-swallow / session-affinity trap
analogue). Nudge/Delivery (no `card_url`) are deferred-live-half no-ops so terminal-outcome
parity holds (the `OutboundClient` still mints receipts); DialOut (has `card_url`) does the
full reconcile→mint→POST. Lazy imports: importing `bridge.seams.gcp` pulls neither
`google.oauth2` nor `google.auth.transport.requests` (mirrors the P7-2/3/4 lazy-import
discipline).

**Gotchas / learnings.**

- **The re-verify finding (§0 in the plan) changes everything.** Before coding, the task
  required re-verifying the card-url repointing question against `google-adk >=2.7`. The
  finding: ADK now hard-enforces same-origin validation (card RPC URLs must match the
  origin the card was fetched from), so the `.mtls` repoint hack would HARD-FAIL. The
  correct fix is served-card-origin + outbound refuse-loud reconciliation, NOT client-side
  repointing. This is logged in the `GcpA2AClient` docstring and the
  `reconcile_card_host` test docstring so the decision is traceable.

- **Gateway module header comment is load-bearing.** States the module governs **INGRESS
  ONLY** — external HTTPS LB → runtime. No egress rule, no VPC connector, no datastore
  path. The private-IP invariant (Bridge→Cloud SQL stays Direct VPC egress on private IP)
  is enforced by T2.8 grep + the runtime tftest preserving `PRIVATE_RANGES_ONLY` egress
  when `enable_gateway = true`.

- **Terraform runtime envs are PLAIN, gated dynamic blocks.** `BRIDGE_GATEWAY_HOST` /
  `BRIDGE_PUBLIC_URL` are not secrets (hosts/origins are resource identifiers, not
  credentials) — plain `env { name/value }`, each `for_each = var.x != "" ? [1] : []` so
  the P7-1 no-gateway default leaves the container env untouched. The actual values are
  injected out-of-band at P7-9 (may not be known at plan time, like the DB DSN VALUE
  injection pattern).

- **Live send is deferred, not dropped (no silent cap).** The real authenticated wire send
  (real network call to a live counterparty with credentials) is the P7-9 deployed-path
  parity input / S1-benefits-6 live A2A carriers, gated on `BRIDGE_TEST_GCP`. P7-5 proves
  the arming half (reconcile + mint + refuse-loud) credential-free. Documented in the
  `GcpA2AClient` docstring, mirroring `GcpScheduler`'s "arming half / gated live roundtrip"
  language.

- **Exactly ONE `@pytest.mark.seam("a2a_client")`** (DECISIONS 2026-09-06 S0-arch-3):
  extended the existing parity test's gcp branch; put everything else in
  `tests/test_gcp_a2a_client.py` WITHOUT the seam marker.

- **Touches CRITICAL paths** (`bridge/`, `terraform/`) → auto-escalated (stronger model +
  mandatory reviewer before commit). Staged path-scoped; the unrelated pre-existing
  `spec/open-questions.md` working-tree edit left alone.

**Acceptance verified.** T1: full `uv run pytest` green (660 passed, 16 skipped); `ruff
check` clean; `terraform fmt -check`/`validate`/`test` all exit 0 credential-free (13
passed incl. the 3 new `gateway.tftest.hcl` runs); isolation guard green. T2.1–T2.8 all
executed and observed green: adapter resolves to `GcpA2AClient`; refuses without config
naming `BRIDGE_GATEWAY_HOST`; reconcile match passes / mismatch refuses loud naming both
hosts; local↔gcp terminal-outcome parity + mismatch case (`test_a2a_client_seam.py` +
`test_gcp_a2a_client.py` 11 passed, 2 skipped); OIDC token minted with the authorized
audience + no bearer field; card url honors `BRIDGE_PUBLIC_URL` and falls back to
`DEFAULT_URL`; gateway module credential-free; no egress/datastore token in the gateway
module. Live authenticated wire send (real Gateway + counterparty) deferred to P7-9 /
S1-benefits-6, gated on `BRIDGE_TEST_GCP`.

## 2026-09-07 — P7-6: Gemini extraction engine behind the seam (capability axis, not local↔gcp)

**Decision.** Shipped the deployed extraction adapter as `bridge/extraction/gemini.py`
(`GeminiExtractor`) + a `GcpExtraction(ExtractionSeam)` adapter in `bridge/seams/gcp/`,
registered in `GCP_ADAPTERS`. `get_adapter(Seam.extraction, SeamMode.gcp)` now returns
`GcpExtraction` instead of raising; only `memory_bank` (deferred, empty by design) still
raises in the registry.

**The axis is different from every other P7 seam (load-bearing).** Extraction is the
**capability** axis (`fixture|gemini|docai`, deployed either way and selected by config —
`design/seams.md`), NOT a local↔gcp store swap like the other seven seams. So the "gcp"
adapter is really "the Gemini engine behind the seam." `GcpExtraction._resolve_engine`
selects by `BRIDGE_EXTRACTION_ENGINE` (arg wins, else env, default `gemini`): `gemini` →
`GeminiExtractor`; `docai` → loud `NotImplementedError` (documented later swap — a visible
scope-out, never a silent gap); `fixture` → EXPLICIT opt-in only (the gcp adapter never
SILENTLY falls back to fixture); anything else → `ValueError` (refuse-loud on misconfig).

**Terminal-outcome parity, never ledger-identical.** A real Gemini call will not reproduce
the corpus's exact confidence floats, so "reproduce the Address eval corpus on the deployed
extraction path" = the extracted signals feed `run_disposition_gate` to the SAME
`expected_disposition`/`expected_gate` for all 8 corpus docs (mirroring
`test_extraction.py::test_signals_feed_gate_to_expected_disposition`). The live corpus test
is gated on `BRIDGE_TEST_GEMINI=1` (the extraction analogue of `BRIDGE_TEST_GCP`) and skips
in the default suite; float-equality is deliberately NOT asserted.

**Engine design (mirrors `FixtureExtractor` posture + the injectable GCP-adapter seam).**
Two independently-testable halves: a pure `_parse_extraction(text) -> ExtractionResult`
(no client/network; tolerates a stray ```json fence; `extra="forbid"` fails loud on a
stray field) and the client call in `extract`. Injectable `client` (default `None` → lazy
`google.genai.Client()` on first use, exactly like `_GcpCloudTasksScheduler(client=...)`)
and injectable `resolver` (default a corpus-relative byte reader anchored at
`wiki/evals/address`, the same swap point `bridge/edge/artifacts.py::resolve_bytes`
documents for the deployed signed-URL fetch). SVGs ride as a **text part** (the corpus
images are text SVGs; Gemini may reject raw SVG bytes as an image part); other mimes ride
as an inline bytes `Part`. Model from `BRIDGE_MODEL` (default `gemini-2.5-flash`, the
existing `DEFAULT_MODEL`). `google.genai` is imported lazily INSIDE methods; the module is
edge-free/cycle-free like `fixture.py` (no `bridge.edge`/`bridge.seams`/`agents` imports).

**No new dependency.** `google.genai` ships with `google-adk`; the `gcp` extra is not
needed. `pyproject.toml` untouched. `bridge/extraction/agent.py` untouched — it already
resolves via `get_adapter(Seam.extraction, resolve_mode(...))`, so `BRIDGE_SEAM_MODE=gcp`
now routes to Gemini with no edit.

**Gotcha — the "importing the package pulls no genai" invariant is not literally testable.**
`bridge/__init__.py` imports `bridge.agent` → `google.adk` → `google.genai`, so genai is
already in `sys.modules` on any `import bridge.*` (pre-existing, unconditional). The
meaningful structural guarantee — the one the lazy-import discipline actually buys — is that
`gemini.py` adds NO module-level google import (AST-checked) and constructs no client
eagerly (`GeminiExtractor()._client is None`). The test asserts exactly that instead of a
meaningless `sys.modules` check.

**No SPEC PROPOSAL needed.** `design/seams.md` already describes extraction as the
`fixture|gemini|docai` capability axis with `BRIDGE_TEST_GEMINI` gating; no `contract/`,
`spec/`, or `terraform/` change (the `aiplatform.user` grant already exists from P7-1).

**Verification.** T1: full suite 674 passed, 24 skipped; `ruff check` clean; isolation
guard green. T2: `get_adapter(extraction, gcp)` → `GcpExtraction Seam.extraction`; `docai`
→ `NotImplementedError` naming Document AI; credential-free tests 13 passed / 8 gated-skip;
the `BRIDGE_TEST_GEMINI=1` live gate collects all 8 corpus docs and (with no Gemini creds in
this environment) fails at `Client()` with "No API key was provided" — the credential-gated
live terminal-outcome run is the P7-9 provisioned proof, mirroring how
`GcpScheduler`/`GcpA2AClient` gate their live half on `BRIDGE_TEST_GCP`.

**CRITICAL escalation.** Touches `bridge/` (new `bridge/extraction/gemini.py`; edits to
`bridge/extraction/__init__.py`, `bridge/seams/gcp/__init__.py`, `bridge/seams/registry.py`)
→ auto-escalates (stronger model + mandatory reviewer before commit). Reads (never edits)
the protected `wiki/evals/address/expected.json` oracle. Staged path-scoped, not committed;
reviewer commits + flips `P7-6` to `- [x]`, leaving the unrelated pre-existing
`spec/open-questions.md` edit alone.

## 2026-09-07 — P7-7: frontend deploy module (portal public LB+CDN, console/dashboard private)

**Decision.** Shipped the frontend deploy Terraform module (`terraform/modules/frontend/`)
that provisions three GCS buckets + an external HTTPS Load Balancer for the provider-portal,
honoring the two-zone model: external-zone portal is **public by design** (public web assets
on a public bucket with `allUsers:objectViewer` grant, served via LB+CDN), internal-zone
console/dashboard are **private** (enforced PAP, no public grant, staged as private buckets
— live internal serving via IAP / internal LB is a follow-up when serving surfaces split).
Plus build-time `.env.production.example` files so deployed endpoint URLs are baked into the
bundles at build time (P7-9).

**Why public portal / private console+dashboard (the zone-boundary decision).** The
external-zone portal (`design/zones.md`) is a public-facing SPA serving untrusted
counterparties; serving it from a **public GCS bucket** behind an external HTTPS LB + Cloud
CDN is the correct standard pattern for public web assets — NOT a violation of the "never
public / signed-URLs-only" invariant, which governs artifact/skill **byte data** (the
`modules/gcs/` buckets from P7-3), not public web assets. `google_compute_backend_bucket`
requires the bucket to be readable by the LB, which for a public static site means
`allUsers:objectViewer` — this is incompatible with `public_access_prevention = "enforced"`,
so the portal bucket must NOT enforce PAP and DOES grant `allUsers:objectViewer`. The
internal-zone console + dashboard (`design/zones.md` "the internal zone never accepts
unauthenticated calls") are provisioned **private** (`uniform_bucket_level_access = true`,
`public_access_prevention = "enforced"`, no `allUsers` grant, NOT on the public LB). Their
deployed serving surface (IAP / internal LB) is a follow-up wired when the internal serving
surfaces split into their own services (TODO.md P7 "Per-workload identity if serving
surfaces split"); provisioning them as private staging buckets here honors the zone
boundary. Header comment in `modules/frontend/main.tf` logs the deferral (no silent cap).

**Why build-time endpoint wiring, not runtime config.** Each app reads its backend endpoint
via a **build-time** `import.meta.env.VITE_*_URL` read (see `frontend/*/src/api.ts`), so
"wire to the deployed edge/dashboard endpoints" (the task requirement) is realized by
passing the deployed URLs as build env vars AT BUILD TIME (when Vite bundles the app), NOT
by a terraform resource or runtime config fetch. Terraform provisions the buckets + LB only;
the P7-9 upload + build step reads the terraform outputs (bucket names / LB URL), builds
each app with the deployed endpoint injected (`VITE_PORTAL_URL=https://<deployed-host> pnpm
build`), and `gsutil rsync`es the `dist/` assets into the provisioned buckets. The
`.env.production.example` files (NOT `.env.production`, which Vite auto-loads) are commented
templates for the P7-9 deploy step to populate.

**Why two separate `ssl_domains` vars.** The gateway LB fronts the runtime Cloud Run service
(the A2A edge / A2UI endpoint); the frontend LB fronts the portal static bucket. Use a
distinct root var (`frontend_ssl_domains`) so they can point at different hostnames (e.g.,
gateway = `api.example.com`, portal = `portal.example.com`).

**Gotchas / learnings.**

- **PAP-vs-public conflict is real.** A `google_compute_backend_bucket` cannot serve a
  `public_access_prevention = "enforced"` bucket to anonymous users. The portal bucket must
  be public (that is the *external zone*); the internal buckets stay enforced-private. Do
  not "fix" the portal bucket to enforced PAP — that breaks public serving. Do not relax
  the internal buckets to public — that breaks `design/zones.md`.

- **Keep `frontend` ingress-only + no datastore/egress resource.** Same discipline as the
  gateway module header (`modules/gateway/`): the frontend module must not create any
  VPC/firewall/private-network or datastore resource. Mirrors the T2.8 gateway invariant.

- **No OBJECTS / no secrets in HCL.** The module provisions buckets + LB only; the built
  `dist/` assets are uploaded at deploy (P7-9) via `gsutil rsync`, never Terraform-managed.
  This is the "no bucket OBJECTS in HCL" invariant (`modules/gcs/` header, CRITICAL.md) that
  keeps secrets / large blobs out of state.

- **`ssl_domains` may be unknown at plan time.** The managed-cert resource is gated on
  non-empty (`var.enable_frontend && length(var.ssl_domains) > 0`), exactly like the gateway
  module. The LB URL is injected out-of-band at deploy (P7-9), like `followup_callback_url`
  / `public_url` (do not hardcode it in HCL).

- **Console/dashboard live internal serving is a follow-up.** Provisioning them as private
  staging buckets now is deliberate; the header comment logs it so it is NOT a silent cap
  (TODO.md P7 "No silent caps" / "Per-workload identity if serving surfaces split").

- **timewarp is local-only, not provisioned.** Confirmed no `timewarp` bucket/resource in
  `modules/frontend/main.tf` — it is a dev-only tool (`design/frontend.md` lines 38, 53),
  never deployed.

**Verification.** T1: `terraform fmt -check -recursive` clean; `terraform test` green with
NO credentials (15 passed incl. new `frontend.tftest.hcl` 2 runs + extended
`root.tftest.hcl`, 0 failed); `uv run pytest` green (no Python touched); `uv run ruff check`
clean; `pnpm install --frozen-lockfile && pnpm -r build` + `pnpm -r test` both green. T2.1:
disabled default credential-free-empty (`disabled_creates_nothing` run → zero frontend
resources). T2.2: enabled shape is correct (zone boundary enforced):
`enabled_serves_portal_public_and_internal_private` run asserts portal bucket public
(`allUsers:objectViewer`, PAP not enforced) behind a CDN-enabled backend bucket + forwarding
rule; console + dashboard buckets private (`public_access_prevention = "enforced"`, no
public grant). This is the load-bearing zone assertion — an implementer who accidentally
makes the internal buckets public will fail this run. T2.4: build-time endpoint wiring works
end-to-end: built the portal with a deployed endpoint injected (`VITE_PORTAL_URL=https://
bridge-runtime-abc.a.run.app pnpm --filter provider-portal build`); confirmed the built JS
contains the injected deployed origin (`grep -rl "bridge-runtime-abc.a.run.app"
frontend/provider-portal/dist/assets` → match). Proves the `VITE_*_URL` wiring the deploy
step relies on actually reaches the bundle.

**NOT a CRITICAL escalation.** Touches `terraform/` (new `modules/frontend/`; edits to root
`main.tf`/`variables.tf`/`outputs.tf`/`terraform.tfvars.example`) + `frontend/*` (new
`.env.production.example` files), but the repo's own convention (TODO.md line 65) explicitly
carves out terraform/frontend: "Adapters land under `bridge/seams/gcp/` — **auto-escalate**
(`bridge/`) per `CRITICAL.md`; Terraform (`terraform/`) and `frontend/` do not." P7-7 (and
P7-1, terraform-only) are the only P7 tasks NOT tagged `auto-escalates`. No `bridge/` core
touched, no isolation-guard/seam risk.

## 2026-09-07 — P7-8: Service-wiring guard (no hardcoded in-memory services in bridge/)

**Decision.** Built an AST-based regression fence (`tests/test_service_wiring_guard.py`)
ensuring no `bridge/` factory hardcodes an `InMemory*` session/task/artifact store
unconditionally. The guard scans `bridge/` for `InMemory*()` constructions and flags any
that are **ungated** — not an `or` operand and not inside an `if`/`elif`/`else` branch.

**Rationale.** Guards against the anti-pattern that `build_runner` exhibited (DECISIONS
2026-09-07 "Remove test-only `build_runner` from bridge core"): a core factory
unconditionally constructing `InMemorySessionService()` with no param and no env gate
silently puts a per-replica in-memory store on the deployed path. On multi-replica GCP
without session affinity, the A2A POST-then-poll pattern writes to replica A and polls
replica B → 86.8% 404 miss rate (the vibeflix-audit trap — Decision 2026-08-24). The GCP
`task_store` adapter is Cloud SQL (a shared store), but without a guard this could regress
at any new factory callsite. The guard turns that regression into a red test.

**Two legitimate gate forms (8 current sites, all allowed).**
1. **Or-gate:** `param or InMemory…()` — the injectable default idiom. Example:
   `bridge/edge/mock.py:487` (`task_store or InMemoryTaskStore()`).
2. **If-branch gate:** DSN- or env-gated backend swap. Example:
   `bridge/seams/local/__init__.py:59` (`if not dsn: return InMemorySessionService()`).

The violation shape: an `InMemory*()` construction executed unconditionally in a
function/module body — not an operand of boolean `or`, not inside any `if`/`else` branch.

**Implementation approach.** AST-based climb with parent-linking: parse each `.py` in
`bridge/`, link parents via a `node.parent` attribute, for each `Call` node whose callee
matches `r"^InMemory\w+$"`, climb ancestors to see if it sits in a `BoolOp(Or)` or inside
an `ast.If` body/orelse (not the condition). Naturally excludes class definitions, imports,
comments, strings (all non-Call). Mirrors `tests/test_isolation_guard.py` structure:
real-tree assertion + positive-control synthetic violations.

**Scope.** Scans `bridge/` only — the production zone. `agents/` (drivers, carriers,
console) and `tests/` legitimately construct `InMemory*` unconditionally in a few spots and
are out of scope per the task wording. The guard MUST be green on the current tree; if it
flags any of the 8 legitimate sites, the gate logic is wrong — do not modify `bridge/` to
satisfy it.

**Gotchas / learnings.**
- The guard must pass on the tree as-is. If it flags the eight allowed sites, the AST gate
  is wrong — `bridge/` is the reference, not the guard.
- `_in_if_branch(child, if_node)`: checks whether `child` is within `if_node.body` or
  `if_node.orelse`, **not** `if_node.test`. A call in an if-condition is not the real
  pattern, so only calls inside branch statements are treated as gated.
- Attribute-form calls (`mod.InMemoryTaskStore()`) are also violations — covered by
  positive control.
- Pattern to follow: `tests/test_isolation_guard.py` (AST helper, `_scan(...)` over
  `rglob`, one real-tree assertion, positive-control synthetic violations in `tmp_path`).

**Verification.** T1: full suite 607 passed, 13 skipped; `ruff check` clean. T2.1: guard
passes on the real tree (0 offenders) even though 8 `InMemory*` constructions exist. T2.2:
regression check — temporarily insert `_x = InMemorySessionService()` into
`bridge/agent.py::build_document_bridge` → test fails naming the violation. Revert → green.
T2.3: positive control flags unconditional + attribute-form, clears gated / class-def /
import / comment / string forms.

**Cross-referenced in `design/patterns.md`** under "Host-controlled wiring" — a `bridge/`
factory never constructs an `InMemory*` session/task/artifact store unconditionally; the
host owns durability wiring. Fence is `tests/test_service_wiring_guard.py`.

## 2026-09-07 — P7-9: deployed-path parity gate — artifacts + credential-free green, live is the operator step

**Decision.** Delivered the P7 exit gate as: the Cloud Run container image
(`Dockerfile` + `serve_container()`), the two-phase deploy orchestration
(`scripts/deploy.sh` + `scripts/parity.sh`), the deployed-path Address parity harness
(`tests/test_deployed_parity.py`), and gated terraform runtime seam envs
(`BRIDGE_SEAM_MODE` / `BRIDGE_EXTRACTION_ENGINE`). The three criteria that run
credential-free (plan-time terraform; the local half of every parity test; the harness
skipping cleanly) are GREEN; the three needing a provisioned project stay cleanly skipped
as the documented operator step. No gate was weakened and no live pass was faked.

**Credential-free vs live — the honest split (no silent cap).** ADC on this VM resolves
to an ambient dev SA (`gcloud auth application-default print-access-token` returns a token
against project `btvaroska`), but there is **no reviewer-provisioned Bridge stack** — no
deployed edge, no Cloud SQL instance, and none of the Bridge-specific envs
(`BRIDGE_TEST_GCP`, `BRIDGE_DEPLOYED_EDGE_URL`, `BRIDGE_GCP_TEST_DSN`) are set. Provisioning
a full VPC + Cloud SQL + Cloud Run + LB stack to satisfy the live criteria would be slow,
costly, and unauthorized (the plan's explicit warning), so the live `bash scripts/deploy.sh`
+ `bash scripts/parity.sh` is left as the gated operator step against a provisioned project.
The artifacts + the parity harness + the credential-free gate + the runbook ARE the P7-9
deliverable; the live apply is the operator handoff.

**Container binds 0.0.0.0:$PORT — required by Cloud Run, does NOT reopen S0-sec-3.**
`serve_container()` is a SEPARATE entrypoint; `serve()`'s loopback default is unchanged. On
the deployed path ingress is Gateway-locked (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` when
`enable_gateway`, the runtime module), so the container listens behind the Gateway, not on a
public interface. The served card advertises `BRIDGE_PUBLIC_URL` via `resolved_card_url(None)`
(adk >=2.7 same-origin, P7-5). Guarded `# pragma: no cover` like `serve()` (it is the process
boot, not unit-tested). The P7-8 service-wiring guard is the static backstop that
`serve_container` builds via `build_a2a_app` (no unconditional in-memory store).

**docbride is a uv virtual project — the image runs from the source tree, not a wheel.**
`import bridge` resolves to `/app/bridge/__init__.py`, not a site-packages copy, so the eval
corpus (`wiki/evals/`) — read via `__file__`-relative paths by `bridge/extraction/fixture.py`
+ `bridge/edge/artifacts.py` — resolves at runtime. Hence `uv sync … --no-install-project`
and `CMD ["python", "-c", "from bridge.edge.server import serve_container; serve_container()"]`
(the `bridge-serve` console script names the packaged equivalent; a wheel install would move
`__file__` into site-packages and break the corpus, so it is deliberately avoided). The image
copies `contract/ bridge/ skills/ wiki/` only — never `agents/` (isolation invariant).

**Two-phase apply resolves the self-reference cycle.** `public_url` /
`followup_callback_url` are the runtime's OWN URL, unknown until after apply 1 (a Terraform
cycle if wired module→module). `deploy.sh` applies phase 1 with them `""`, then re-applies
phase 2 with the resolved values — designed-in via their `default = ""` (P7-4/P7-5). The DB
DSN VALUE (which embeds the password) is composed in-memory and piped to `gcloud secrets
versions add --data-file=-` — never on disk, in HCL, or tfvars (CRITICAL.md "No secrets";
P7-2 no-`secret_version` structural guard). `DB_PASSWORD` is read from env only.

**Terraform seam-master-switch env added (small, gated, tested).** `modules/runtime` gained
`seam_mode` / `extraction_engine` gated dynamic-env blocks (the existing `for_each = var.x !=
"" ? [1] : []` idiom) so the deployed container gets `BRIDGE_SEAM_MODE=gcp` (routes every
seam to its Gcp adapter — miss it and multi-replica silently runs per-replica in-memory
stores, the session-affinity trap) and `BRIDGE_EXTRACTION_ENGINE=gemini`. Default "" leaves
the plan untouched (runtime tests stay green). Wired through the root; added `db_name` /
`db_user` root outputs for the DSN composition; a `seam_envs_when_deployed`
`runtime.tftest.hcl` run asserts the deployed shape and the default asserts no env
(terraform test 16 → 17 passed credential-free).

**Deployed parity is terminal-outcome, NEVER ledger-identical.** `test_deployed_parity.py`
runs the real driver (`run_collection`) against the deployed edge over an OIDC-authenticated
httpx client (an `httpx.Auth` minting a per-call ID token with `audience == the service
origin`, the P7-5 Agent-Identity pattern — never a message bearer field), covering CUJ-1
(instant accept), CUJ-10 (distinct-issuer accept + same-issuer reject), CUJ-4 (resubmit
first-turn reject). It asserts only accept/reject/satisfied — the deployed edge (real Gemini,
Cloud SQL, real timestamps) will not byte-match the fixture. The corpus (`wiki/evals/`) is the
oracle and CRITICAL-protected: if deployed extraction can't reach an expected outcome, that
is a finding to REPORT, never a corpus edit.

**Follow-up callback RECEIVER re-logged as deferred, not built.** The endpoint receiving the
Cloud Tasks POST (→ `FollowupLadder.on_fired` → re-arm) is NOT one of P7-9's four exit
criteria (CUJ-6's deployed ladder is not in the gate; `test_gcp_scheduler_live_roundtrip`
arms→observes→cancels without a real firing). It rides the "follow-up worker behind the edge"
family already deferred in TODO.md; kept deferred to hold P7-9 at exit-gate scope. If wanted,
it is a small runtime route mirroring `agents/address/wake.py` — call it out before adding.

**Gotchas / learnings.**
- ADC returning a token on a GCE VM is NOT the same as a provisioned project — probe the
  Bridge-specific envs + the actual substrate before treating Step-0 "LIVE" as runnable.
- `[project.scripts]` produces no binary in a uv virtual project (no dist metadata); the
  Dockerfile invokes `serve_container` directly. Adding a `[build-system]` to make the script
  real would move `__file__` into site-packages and break the `wiki/evals` runtime loaders —
  deliberately not done.
- The new harness adds 4 skips (CUJ-1/10-accept/10-reject/4), so the suite is 676 passed, 28
  skipped credential-free (24 pre-existing + 4), not the plan's estimated single new skip.

**CRITICAL escalation.** Touches `bridge/` (`serve_container` in `bridge/edge/server.py`) +
`terraform/` → auto-escalates (stronger model + mandatory reviewer before commit). Reads
(never edits) the protected `wiki/evals/` oracle. Staged path-scoped, NOT committed; the
reviewer inspects, commits, and flips `P7-9` → `- [x]`, leaving any unrelated pre-existing
working-tree edit alone.

**Verification.** T1: `uv run pytest -q` = 676 passed, 28 skipped; `uv run ruff check` clean;
`terraform fmt -check -recursive` clean + `terraform test` = 17 passed, 0 failed
credential-free; isolation + service-wiring guards green. T2 (credential-free): terraform
test 17-passed; deployed-parity skips cleanly naming both envs; cross-replica RAW `[local]`
passes; `docker build` + `docker run` + `curl` → the card (0.0.0.0:$PORT boot); deploy scripts
`bash -n` + shellcheck clean + no committed secret. Live criteria = the gated operator step.

## 2026-09-11 — Local-dev topology + the canonical env index (ported from the pre-fork repo)

**What.** Recovered two artifacts the clean-slate build never carried over: `.env.example`
(a REFERENCE index of every env var the code reads — nothing loads it) and the local run
topology (`scripts/run-{core,mock,portal,console,ops,timewarp,all}.sh` + `scripts/_dev-env.sh`,
with the port map and its caveats in `docs/local-dev.md`). The old repo's versions were stale
(three uv projects, Vite proxies, ports 8010-8013); these are rebuilt against the as-built
single-project layout: hosts on 8000-8004, apps on 5173-5176, CORS instead of proxies.

**Two code changes were needed to make `run-all.sh` real, not aspirational.**
1. `VITE_DEV_ORIGIN = "http://localhost:5173"` → `VITE_DEV_ORIGINS` (5173-5177) in all four
   hosts. All four Vite apps default to 5173, so before this only ONE app could run at a
   time — the second would bind a different port and be blocked by the browser, not the host.
   Deploy-time zone scoping is unaffected (that is Gateway/Agent-Identity, P7, not CORS).
2. `agents/console/server.py` grew `default_card_url()` reading `BRIDGE_CARD_URL`
   (`build_console_app(card_url=...)` now defaults to `None` → that). The console is a
   *consumer*: without an env override, `uvicorn --factory` could only ever dial the
   hardcoded mock at :8001. Tests pass `card_url` explicitly and are unaffected.

**Gotchas recorded in `docs/local-dev.md` rather than rediscovered.** The portal and the mock
edge both default to :8001 (alternatives, not companions — `run-all.sh` starts the real edge +
portal). Each host factory builds its OWN in-memory portal store, so portal/dashboard/timewarp
launched separately do not share sessions. No `--reload`: the hosts hold live state.

**Deliberately NOT ported.** The old `.github/workflows/ci.yml`, `Makefile`, `CONTRIBUTING.md`,
`.pre-commit-config.yaml` (user deferred CI). The 16 pre-fork ADRs and `docs/lessons-learned.md`
stay unported — spot-checks confirmed their content is already absorbed into `spec/standards.md`,
`design/`, and the code (e.g. ADR-0015's ring priority, ADR-0016's doctype `schema.json` binding).

**Verification.** T1: `uv run pytest -q` = 676 passed, 28 skipped; `uv run ruff check` clean.
T2: all five hosts started via their scripts and answered (card 200 with `url` following the
served origin on both real :8000 and mock :8001; portal `POST /sessions` 200; console
`/prepared` 200; ops `/exchanges` 200; timewarp `/timewarp/state` 200); CORS preflight from
`http://localhost:5175` returned a matching `access-control-allow-origin`; `provider-portal`
came up on a pinned `--strictPort 5173` and served 200.

## 2026-09-23 — S0-arch-5: extraction is schema-driven, through the `skill_registry` seam

**What.** `bridge/extraction/gemini.py` no longer carries a literal `EXTRACTION_PROMPT` with
the Address-shaped `{doctype, issuer, key_fields}` baked in. Every installed **doctype** skill's
`bridge-schema` asset is read through the `skill_registry` seam, rendered into the prompt as the
installed-doctype catalog, and the union of the declared fields is pinned as the request's
structured-output `response_schema`. Installing a doctype skill folder now changes extraction
with no code change and no redeploy — the property the Agent Card already had, extended to the
extraction path.

**Decisions made, and why.**

- **A new seam method `read_asset(name, rel) -> str`** on `SkillRegistrySeam` (local + GCP
  adapters, one shared `@pytest.mark.seam("skill_registry")` parity test) rather than a narrow
  `get_schema`. The schema had to be resolved *through the seam* (`SkillMeta` is frozen
  metadata, not an asset reader), and the same primitive is what S0-arch-8 needs for Path-A
  validation, with `validation.yaml` after it. `SkillMeta` gained `schema_asset`, **not**
  `schema` — that name shadows `BaseModel.schema` and pydantic v2 warns on it. Both adapters
  reuse the S0-sec-1 traversal defense verbatim (the GCP `_validate_install` generalized to
  `_validate_skill_paths(name, rels)`), and the GCP side fires it BEFORE `_bucket()` so no
  client is built for a bad request.

- **`fields.doctype` stays a FREE STRING — never an enum over the installed doctypes.** This is
  the trap in the task. The Bridge classifies (`extract(artifact_name)` has no doctype argument;
  doctype is an *output*), and the Address oracle carries a `passport` record whose expected
  terminal outcome is `rejected`/`unsupported`. An enum would make an out-of-label-space
  document unreportable and silently destroy that outcome. A new credential-free
  `test_corpus_replay_keeps_terminal_outcomes` replays all 8 corpus records through the real
  prompt/schema/parse path, so this now breaks in the default suite rather than only in a
  provisioned run.

- **The pinned `key_fields`/`field_confidence` are the nullable, non-required UNION** of every
  installed doctype's fields (one document only ever carries one doctype's fields; the corpus's
  `bill-aquautil-blurry` carries none at all). The direct consequence is that a model answering
  under the union emits `null` for the other doctypes' fields, so `_parse_extraction` **prunes
  null-valued entries** in those two dicts before validating (`dict[str, str]` /
  `dict[str, float]` would reject `None`). Pruning is scoped to those two open dicts — the
  top-level `extra="forbid"` loudness S0-bug-2 depends on is untouched.

- **No `additionalProperties` anywhere in the pinned schema.** The Gemini backend's OpenAPI
  subset does not reliably accept it and the live path is credential-gated, so a rejection would
  surface only in a provisioned run. A plain lowercase-`type` dict with `nullable: true` was
  verified to pass `google.genai._transformers.t_schema` → `types.Schema` (genai 2.18.1).

- **Caching is per-extractor, not module-level.** `get_adapter()` builds a fresh adapter per call
  and `BRIDGE_SKILLS_DIR` changes at runtime, so an `lru_cache` would break the live doctype-add
  property. The catalog is built once on first `extract` (zero reads at construction, one read
  across N calls); an injectable `schemas=` kwarg mirrors the existing `client=`/`resolver=`
  credential-free test seam.

- **Half-installed skills are handled asymmetrically, on purpose.** A declared-but-ABSENT asset
  is a legitimate registry state (`install_skill` with no assets), so that doctype contributes
  its name only — one half-installed unrelated doctype must not kill all extraction. A PRESENT
  but unparsable asset is an unambiguous authoring bug and refuses loud, naming the skill and
  the rel path. An empty catalog pins NO `response_schema` at all (JSON mime only): a
  `key_fields` object with zero properties would forbid every field — a silent capability loss.

- **The seam import in `gemini.py` is lazy**, inside `_doctype_schemas_from_registry`.
  `bridge/seams/local/__init__.py` imports `bridge.extraction.fixture` at module level, so a
  module-level `from bridge.seams import …` here would close an import cycle. Same precedent as
  `bridge/edge/skills.py::loaded_skills`; the existing AST guard only checks `google*` imports
  and would not have caught it.

**Verification.** T1: `uv run pytest -q` = 697 passed, 29 skipped (baseline 676/28), 0 failed;
`uv run ruff check` clean; `-m seam` 9 passed / 8 skipped. T2: the installed-skill catalog and
the pinned union observed on a fake-client request (all three doctypes in the prompt, no `enum`
on `doctype`, nulls pruned from the parsed result); a `pay-stub` skill installed through the seam
into a temp `BRIDGE_SKILLS_DIR` reached the next extractor's prompt and pinned schema with no
code change; exactly one registry read per extractor across two `extract` calls and zero at
construction; all 8 corpus records replay to their expected disposition/gate. `BRIDGE_TEST_GEMINI=1`
collects its 8 live params and fails only at `Client()` / "No API key was provided" — the
documented P7-6 posture (the provisioned run is the P7-9 proof).

## 2026-09-23 — S0-bug-2: bounded schema-validated retry inside `extract`, no fixture fallback

**What.** `GeminiExtractor.extract` made exactly ONE `generate_content` call and handed the
text to `_parse_extraction`; because `ExtractionResult` is `extra="forbid"`, one stray key,
one non-string `key_fields` value or one truncated token raised straight out and the
extraction was simply lost. `extract` now runs a bounded retry: a rejected payload is
re-asked with its own output plus the SPECIFIC validator reason fed back, and on exhaustion
the last error is raised.

**Decisions made, and why.**

- **The retry lives INSIDE `extract`, not in a caller.** Six call sites (`bridge/collect.py`,
  `bridge/pathb.py`, `bridge/signals.py`, `bridge/extraction/agent.py`, both seam adapters)
  only `await …extract(name)`; putting the loop in `extract` means the
  `extract(artifact_name) -> ExtractionResult` seam signature never moves and no call site
  changed. The idiom is copied from `bridge/seams/gcp::_TerminalRetryStore`
  (`attempts=3`, a `WARNING` per failure, an `ERROR` on exhaustion, then `raise last`) rather
  than invented.

- **`extra="forbid"` is deliberately kept as the retry's ORACLE.** The tempting "fix" —
  a tolerant pre-clean pass or `extra="ignore"` — would make the retry unreachable and
  silently drop whatever the model over-emitted. `_parse_extraction` and the wire type are
  untouched; the validator is what decides an attempt failed.

- **The correction echoes the rejected output AND the reason** (bounded at
  `MAX_ECHO_CHARS = 4000` with a "first N characters" marker). A deliberate small deviation
  from the reason-only correction the Aon engine uses: handing the model its own rejected
  JSON back is what makes attempt 2 a *repair* rather than a re-roll. Built by a pure
  `build_retry_correction(previous, error)`, keeping the module's "two independently-testable
  halves" posture (pure builders + one method) true.

- **`max_attempts=3` is a constructor kwarg, not an env var.** `GcpExtraction._resolve_engine`
  constructs `GeminiExtractor()` with no kwargs, so the default is what the deployed path
  gets; 3 is the number the request named. `max_attempts < 1` raises at construction
  (refuse-loud on misconfig).

- **Surfacing is logs + `add_note`, NOT new state and NOT a `contract/` field.** Per rejection:
  a `WARNING` with the artifact, `attempt n/N` and `repr(exc)`; on exhaustion an `ERROR` plus
  a note naming the attempt count, the artifact and the no-fallback refusal, then
  `raise last_error` — the ORIGINAL `ValidationError`/`JSONDecodeError`, type preserved, so a
  caller catching `ValidationError` keeps working and the eval pipeline records a real miss.
  Rejected: `self.last_attempt_count` (mutable per-call state on a shared object) and an
  `attempts` field on `contract/extraction.py` (that wire surface is S0-debt-2's, and a
  `contract/` change ripples across every consumer). An injectable `log=_LOG` kwarg mirrors
  `_TerminalRetryStore`.

- **Only `(ValidationError, json.JSONDecodeError)` are caught, and only around the parse.**
  The `generate_content` call is not wrapped: a 429/500 is not a schema rejection, so it
  propagates unchanged and burns no attempt.

- **Catalog, resolver and document part are evaluated once per `extract`, outside the loop.**
  A retry re-asks, it never re-fetches the artifact — load-bearing once S0-arch-7 makes the
  resolve a network call. A counting-resolver test pins it.

- **`response.text is None`** (safety block / `MAX_TOKENS`) is normalized to `""`, so an empty
  body becomes a retryable `JSONDecodeError` instead of an `AttributeError` that was neither
  retried nor useful.

- **No fixture fallback, ever, in this path** — stated in the code comment with its reason: a
  fallback would launder hand-authored corpus values in as if they had been extracted.
  `GcpExtraction` already refuses the same thing for engine select; this is the per-document
  analogue.

**Verification.** T1: `uv run pytest -q` = 708 passed, 29 skipped (baseline 697/29), 0 failed;
`uv run ruff check` clean; `-m seam` 9 passed / 8 skipped; all pre-existing gemini tests pass
UNEDITED (the diff to `tests/test_gemini_extraction.py` is purely additive). T2: a
bad-then-good script repairs on attempt 2 with the reason in the follow-up and the same
prompt+document reused; an always-bad script raises the original `ValidationError` after
exactly 3 calls with "3 schema-validated attempt(s)", the artifact and "no fixture fallback"
in `__notes__` (3 WARNINGs + 1 ERROR observed); a `RuntimeError("503")` client propagates
after exactly 1 call; all 8 corpus records still extract in exactly ONE call and reach their
expected disposition/gate.

## 2026-09-23 — S0-arch-6: a native-digital PDF in the benefits corpus

**What.** `.pdf → application/pdf → Part.from_bytes` existed only as a dict entry: the Address
corpus is text SVG (which `_document_part` deliberately routes to a *text* part),
`wiki/evals/benefits/images/` held only `.gitkeep`, and the benefits corpus note said outright
that `images/` was deferred — so "native PDF" was an unproven claim. A synthetic, PII-free
carrier-quote PDF is now committed at `wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf`,
attached to the existing `benefits-carrier-c-eur-fr` record as its `artifact`, and driven
through the gemini leg by `tests/test_pdf_extraction.py`.

**Decisions made, and why.**

- **Attach the PDF to an EXISTING record; do NOT add a sixth corpus document.**
  `tests/test_normalize.py` and `tests/test_standard.py` hard-assert `len(docs) == 5`, and
  `test_comparison`, `test_cuj11_negotiate_bind_rollup`, `test_cuj3_dial_out_deliver` all derive
  **four** program legs from the non-`-v2` documents. A new document would be a fifth carrier
  leg — five broken tests and a broken demo narrative. An additive `"artifact"` key is the
  Address corpus's own idiom (`"artifact": "images/<id>.svg"` on every record) and costs zero
  count churn.
- **The shared artifact name IS the parity pivot.** `FixtureExtractor._normalize_key` already
  strips `.pdf`, so the fixture leg and the gemini leg accept the *same* string
  `images/benefits-carrier-c-eur-fr.pdf` — one reads `expected.json`, the other reads the bytes,
  and the test asserts they land on the same `NormalizedQuote` / gaps / `ROUTE_NONE`. Terminal
  outcome only: confidences are deliberately not compared (CLAUDE.md; `design/seams.md`).
- **Carrier C, not A/B/D.** A and B are the Path-A "party asserts fields" carriers
  (`agents/benefits/quotes.py`) — handing them a document would contradict the demo narrative.
  C (EUR/fr) exercises the *full* downstream terminal path: FX EUR→USD ×1.10 **and** fr→en
  translation. D stays text-only; `PDF_DOC_IDS` in the generator makes adding it one line.
- **A committed stdlib generator, not a dependency and not an opaque blob.**
  `tests/corpus_pdf.py` writes the PDF from the standard library only (`reportlab`/`pypdf` are
  not installed and were not added) and renders the record's **own** `key_fields`, so the page
  can only print oracle values. The output is byte-deterministic (no `/CreationDate`, no `/ID`,
  no compression, no clock read) and a test regenerates it into `tmp_path` and asserts
  byte-equality with the committed file — that drift guard is what stops the asset and the
  oracle diverging later. It lives in `tests/` because the corpus's consumers do; `bridge/`
  does not depend on it.
- **Money renders as `EUR 1000.00`, never `1 000,00 €`.** `bridge/normalize.py::_convert_money`
  strips `$€£`, commas and whitespace but NOT an ISO-4217 *code* prefix and NOT a comma decimal
  separator, and a live model echoes what it reads. Free text (`coverage_limits`, `exclusions`)
  is printed byte-for-byte as the corpus strings because the `TRANSLATIONS` table is
  case-sensitive and unmapped text passes through unchanged.
- **Live-echo variance is absorbed by a ~6-line canonicalizer in the TEST, never by loosening
  `bridge/`.** The gated live test strips a currency code / lowercases free text before
  normalizing; the `TRANSLATIONS` table, the oracle and the wire types are untouched.
- **`bridge/extraction/gemini.py` gained docstrings only — zero logic change.** The PDF path
  already worked; this task *proves* it (the request part is asserted to be a
  `google.genai.types.Part` with `inline_data.mime_type == "application/pdf"`, not the SVG
  text branch) and records the boundary.
- **Scanned/raster (OCR) PDF stays with the deferred Document AI adapter**, stated explicitly in
  `design/seams.md`, the engine docstring, the corpus description and TODO's deferred list —
  rather than letting "PDF works" imply OCR coverage. Out of scope by the same discipline:
  resolving a PDF through `bridge/edge/artifacts.py::resolve_bytes` (S0-arch-7, anchored at the
  Address corpus) and the whole-journey A2A conformance run (S0-arch-9).

**Verification.** T1: `uv run pytest -q` = 715 passed / 30 skipped (baseline 708/29 + 7 new
credential-free tests + 1 gated live test), 0 failed; `uv run ruff check` clean; `-m seam`
9 passed / 8 skipped (unchanged); the corpus-consumer subset (normalize, standard, comparison,
CUJ-3/11/12, negotiate, program-comparison-view) 58 passed. T2: `pdftotext` prints the quote
page with `EUR 1000.00`, `EUR 500.00`, `soins dentaires et optique`, `chirurgie esthétique` and
both dates, accents intact; `uv run python -m tests.corpus_pdf` + `cmp` against a pre-copy is
BYTE-STABLE (git stays clean for the asset); a fake-client run over the committed PDF prints
`mime: application/pdf bytes: 1186 doctype: benefits-quote`; both legs on the same artifact
string reproduce `expected_normalized` (premium 1100.00, deductible 550.00, USD/en) with no gaps
and `ROUTE_NONE`. The gated live leg was NOT run (no credentials in this environment).

## 2026-09-23 — S0-arch-7: a real artifact resolver behind the extraction seam

**What.** ART-2 puts documents on the wire **by reference**, so the string that reaches the
extraction seam is the counterparty's uri — and the engine could only read
`<wiki/evals/address>/<name>` off local disk (`bridge/edge/artifacts.py::resolve_bytes` had
the same gap; the signed-URL/`gs://` fetch was a docstring promise). `bridge/extraction/
resolve.py` is now the one resolver for both paths, over four inbound shapes: `gs://` **by
reference** (`Part.from_uri`, no download, no storage client), an `http(s)` URL fetched under
a default-deny egress guard, an opt-in `file://`, and today's corpus-relative name unchanged.

**Decisions made, and why.**

- **No ninth seam.** `design/seams.md` pins eight and `tests/conftest.py` guards the marker
  names. The resolver is an *injectable collaborator behind the existing extraction seam* —
  the same seam-in-waiting posture `bridge/edge/artifacts.py` Decision 1 uses for the
  artifact store.
- **`gs://` is never downloaded.** Vertex reads the object natively, so the Bridge needs no
  GCS client and no credentials on the extraction path; `_document_part` emits
  `Part.from_uri`. Because that is Vertex-only, a client that says `vertexai is False`
  refuses loud naming `GOOGLE_GENAI_USE_VERTEXAI` — checked with `is False`, never falsy, so
  a test double lacking the attribute passes through. A `gs://` object whose suffix yields no
  known mime also refuses: an unclassified part is garbage the backend would reject anyway.
- **The SSRF primitive MOVED, it was not copied.** `bridge/netguard.py` (stdlib-only) now owns
  `url_allowed`/`ip_is_global`/`allowlist_from_env`; `bridge/edge/pushguard.py` re-exports it
  as `webhook_url_allowed` with an identical public surface (`tests/test_push_ssrf.py` passes
  unedited). The move was forced *and* right: `pushguard` imports `a2a` at module level, so
  `bridge/extraction/` could not import it — and two copies of an egress policy is how the
  two copies drift.
- **`follow_redirects=False` + a manual hop loop, every hop re-validated.** Load-bearing both
  ways: our own fetch endpoint 307s to a GCS signed URL on deploy (so redirects MUST be
  followed), and httpx's own redirect following would skip the guard (so a counterparty 307ing
  us at `169.254.169.254` would win). Max 3 hops, guard **before** the request on every hop —
  a refused URL has no connection opened, asserted by a test that checks the server saw zero
  requests.
- **Async core, `maybe_await` at both call sites.** The fetch is I/O on a shared Cloud Run
  event loop, so `resolve_document` is `async`; `GeminiExtractor.extract` and
  `persist_submission` await-if-awaitable so every existing **sync** injected resolver (~15
  tests) keeps working untouched. `_coerce_resolved` accepts a legacy `(bytes, mime)` 2-tuple
  alongside `ResolvedDocument` for the same reason — back-compat was a hard requirement, not
  a nicety, and the shim is what kept four pre-existing test files unedited.
- **Extraction raises, persistence skips — deliberately asymmetric.** A document the *engine*
  cannot read must never be silently replaced (the S0-bug-2 no-fabrication rule), so
  `resolve_document` refuses loud. `resolve_bytes` keeps Decision 7's never-raise posture: a
  refused/failing uri logs a WARNING and returns `None`, and a **by-reference** `gs://` resolve
  also returns `None` — there are no bytes to persist, the consumer's ref stays on the ledger,
  and the engine reads the object natively.
- **The resolve stays OUTSIDE the retry loop** (`test_extract_resolves_the_artifact_once_
  across_retries`), and `_catalog()` still runs first so a registry/authoring error precedes
  any network call. A retry re-asks; it never re-fetches.
- **`file://` is off unless `BRIDGE_ALLOW_FILE_URI=1`.** A consumer submitting
  `file:///etc/passwd` would otherwise get host file contents read by the model and laundered
  back through the extracted `key_fields` onto the ledger.
- **No new dependency** (`httpx` 0.28 is already transitive and imported lazily inside the
  function, the repo's idiom), no `contract/` change, no `terraform/` change (Vertex reads
  `gs://` with the runtime SA's existing object-viewer grant).

**Verification.** T1: `uv run ruff check` clean; `uv run pytest -q` = 749 passed / 31 skipped
(baseline 715/30 + 34 new, of which 1 gated), 0 failed; `-m seam` 9 passed / 8 skipped;
`tests/test_gemini_extraction.py`, `tests/test_pdf_extraction.py`, `tests/test_push_ssrf.py`,
`tests/test_edge_artifacts.py` all pass with no behavioural edit (the only change to
`test_pdf_extraction.py` is prose that named this task as an open gap). T2: the acceptance
script prints all eight PASS lines and exits 0 — a loopback-served copy of the committed
corpus PDF is fetched over `http` under the allowlist and reaches the model as
`inline_data.mime_type == "application/pdf"` byte-equal to the file (1186 bytes) yielding a
parsed `benefits-quote`; a 307 hop is followed and re-validated; `gs://bucket/quote.pdf`
produces `inline_data is None` + `file_data.file_uri == "gs://bucket/quote.pdf"` with no bytes
fetched; with the allowlist unset the metadata IP, the loopback URL, `file:///etc/passwd` and
`mock://x` are each refused with `ValueError`; the corpus read and its traversal containment
are unchanged. Leg-scoping is proved end to end against the REAL fetch app over an
`ASGITransport`: the owning `(context_id, filename)` resolves to the stored bytes while a
foreign `context_id` fails with `HTTP 404` — a reference is not a capability.

## 2026-09-23 — S0-arch-8: Path-A validation is built from the doctype skill's schema

**Context.** `bridge/patha.py::_KEY_FIELD_MODELS` mapped two doctypes (`gov-id`,
`utility-bill`) to hand-written contract models; every other doctype skipped the typed
key-field check. `benefits-quote` ships a full `assets/schema.json` and was therefore
accepted at Path-A validation with **any** `key_fields` — a party-with-agent could assert a
malformed carrier quote and only the label-space gate stood behind it. S0-arch-5 already
reads that same asset through the `skill_registry` seam for the extraction prompt; Path A
now builds its validator from it.

- **A new shared module `bridge/doctypes.py`, not `patha` importing `gemini`.** Path A is
  *defined* by not touching the extraction engine (`design/dual-path.md`), so the schema
  helpers (`key_field_properties`, `doctype_fields`, `declares_issuer`,
  `issuer_description`, `doctype_schemas`, `DEFAULT_SCHEMA_ASSET`) moved out of
  `bridge/extraction/gemini.py` into a pure module both import. One reader means the prompt
  and the validator cannot learn to disagree about what a doctype declares.
  `build_extraction_prompt` / `build_response_schema` stayed in `gemini.py` — they are
  prompt-shaping, not doctype knowledge.
- **Pydantic `create_model`, not a `jsonschema` dependency.** §2e's rule survives: required
  `key_fields` props become mandatory `str`, other declared props `str | None = None`, and
  `extra="forbid"` reproduces exactly what the deleted contract models enforced. Load-bearing
  beyond taste — `bridge/edge/executor.py` catches `pydantic.ValidationError` specifically to
  mint the `schema_invalid` reject; a `ValueError` or a custom exception would silently turn a
  rejected *entry* into a failed *task*. **Every declared property is `str`** whatever
  JSON-Schema type it names, because the wire shape is `key_fields: dict[str, str]`.
- **The cache is keyed by schema CONTENT, not by the registry read.** S0-arch-5 forbids a
  module-level cache *of the read* (`BRIDGE_SKILLS_DIR` changes at runtime;
  `get_adapter` builds a fresh adapter per call), and that still holds: the read happens per
  assertion, the `loaded_skills()` posture. What is cached (`@lru_cache` on
  `(doctype, json.dumps(schema, sort_keys=True))`) is the pure model build, so an installed or
  edited schema simply produces a new key and live doctype-add stays honest.
- **Per-call read, with an injectable `schemas=`.** `validate_assertion` is a free function
  with no instance to hang a catalog on, so it reads the catalog per assertion (a handful of
  small file reads; Path-A volume is one per asserted document). `schemas: dict | None`
  threads through `validate_only` / `validate_only_entry`, mirroring
  `GeminiExtractor(schemas=…)`, so tests can drive the pure path with no skills dir. Both are
  keyword-only and defaulted — the executor call site needed no change.
- **Skip policy — and the skips are no longer all silent.** No installed skill for the
  asserted doctype → skip, log `debug`: `fields.doctype` stays a **free string** (S0-arch-5)
  and the disposition label-space gate rejects it `unsupported`, which is what keeps the
  oracle's `passport` record reportable; turning that into a validation raise would delete a
  terminal outcome. Installed but the schema asset is **absent** → skip with a `WARNING`
  naming the doctype (a capability gap must not be silent). Asset present but **unparsable**
  → `ValueError` out of the registry read, propagating past the edge's `ValidationError`
  catch and failing the task: an authoring bug in an installed skill must never degrade to
  "accept anything". Deliberately no broad `except Exception` at the edge.
- **Two explicit non-goals.** Top-level `required` (utility-bill's `issuer`) is **not**
  enforced — the dict being replaced covered `key_fields` only, and widening it would change
  behaviour for assertions that legitimately omit `issuer`. `assets/validation.yaml` rules
  (`non_empty`, `not_expired`, window checks) are **not** wired — they are a values/rules
  concern, not schema shape, and remain the "validation.yaml after it" step S0-arch-5 named.
- **`contract/extraction.py` is untouched.** `GovIdKeyFields` / `UtilityBillKeyFields` stay:
  `tests/address/test_skills.py` asserts the skill schemas match those models, and that parity
  is now exactly what keeps the dynamically-built models honest — a new test asserts the built
  model's field and required sets equal the contract model's, so deleting the dict cost no
  coverage.

**Verification.** T1: `uv run ruff check` clean; `uv run pytest -q` = 759 passed / 31 skipped
(baseline 749/31 plus 10 new tests), 0 failed; `-m seam` 9 passed / 8 skipped (no new seam,
no new seam method — `read_asset`/`list_skills` only). T2: the corpus `benefits-quote`
extraction block minus `premium` raises `missing ('premium',)` and with an undeclared key
raises `extra_forbidden`, while the untouched block still disposes `accepted`; a `passport`
assertion still yields `rejected`/`unsupported`/`unsupported_doctype` rather than a raise; a
`pay-stub` skill folder dropped into `BRIDGE_SKILLS_DIR` gains validation of its own required
fields with zero change to `bridge/` between the two runs; end-to-end over the A2A app a
malformed `benefits-procurement` assertion yields `entry.reason_code == "schema_invalid"`
with the extraction-seam spy never called.

---

## 2026-09-23 — S0-debt-2: the extraction review vocabulary is **mapped**, not renamed

The finding: our extraction result speaks `overall_confidence` / `field_confidence` /
`flagged_fields` (bare `key_fields` keys); a consumer asks for `extraction_confidence` +
`fields_requiring_review` as **dotted field paths** (`commission.rate_pct`), and our flags
carry no provenance for *why* a field needs review. "Decide and record: rename on the wire,
or define the mapping and document it as the consumer-facing contract."

**Decision: define the mapping, make it executable, and ship the missing provenance.** The
normative table lives in the `contract/extraction.py` module docstring (the consumer-facing
contract text) and in `design/a2a-consumer.md`.

- **A rename would be a pure-cost major break.** `flagged_fields` is baked into the oracle
  corpus (every `extraction` block of `wiki/evals/address` + `wiki/evals/benefits`), into the
  disposition reason code (`reason_code="flagged_fields"`, asserted in `tests/test_collect.py`)
  and into the Path-A shape selector. It would bump `CONTRACT_VERSION` 1 → 2 — and
  `decode_wire`'s tolerance is **additive-only** (2026-09-03 S0-bug-1: a *removed* required
  field still correctly fails), so the bump would not even repair the break for a peer still
  on the old name.
- **A rename does not fix the finding.** The substantive half is "no provenance" and "flat
  names, not paths". Renaming the list still yields bare names with no reason; the mapping +
  provenance is the actual capability.
- **The paths are the consumer's, not ours.** A consumer's paths address *its* schema
  (`rates[0].amount`); `bridge/` must never import a consumer's types, so the Bridge emits
  paths into its **own** extraction result and the consumer re-maps (that mapping step is
  S0-arch-9's charter).

**What shipped.**
- `ReviewReason` (`low_confidence` / `derived` / `absent_from_document` / `model_flagged`) and
  `FieldReview{path, reason, confidence, detail}`; `ExtractionResult.field_reviews` —
  **additive, optional, defaulted**, which is the only reason no major bump is needed and the
  only reason `pathroute._is_extraction_assertion` (which uses `model_validate` as a shape
  selector) keeps classifying every existing Path-A message as Path A.
- Pure projections in `contract/` — `review_path`, `field_reviews_for`,
  `fields_requiring_review`. `model_flagged` is the honest default for a bare flag;
  `low_confidence` and `derived` are **never inferred** — inferring low-confidence would leak
  bridge-side `DispositionThresholds` policy into the shared contract package.
- `flagged_fields` gained `validation_alias=AliasChoices("flagged_fields",
  "fields_requiring_review")` with `populate_by_name=True` and **no** serialization alias — we
  keep dumping our own name, so the corpus and every round-trip stay byte-stable. Before this,
  an external asserter posting `fields_requiring_review` on Path A did not error: `extra="forbid"`
  made the shape selector return False and the message **silently fell through to Path B**.
  That silent misroute is the concrete bug the alias closes. A payload carrying both spellings
  with *different* values raises (pydantic would otherwise silently pick one); identical values
  drop the redundant key rather than trip `extra="forbid"` with a confusing error.
- **`_strip_to_known` gotcha:** it built its known-key map from `field.alias` only, to which a
  `validation_alias=AliasChoices(...)` is invisible — so on a foreign-major `contract_version`
  the tolerant decode would have stripped `fields_requiring_review` back out, defeating the
  tolerance for exactly the caller it exists for. It now registers every `AliasChoices` string
  too; `decode_wire` itself is untouched.
- **Gate hole closed:** `signals_from_result` uses `result.flagged_fields or
  fields_requiring_review(result)`. The raw list wins whenever non-empty (so `Signals` keeps
  bare names and every corpus terminal outcome is unchanged); the projection is a fallback so
  an engine that states `field_reviews` but leaves `flagged_fields` empty cannot
  **auto-approve a document it said needs review**.
- **The review view reaches the wire:** `LedgerEntry.field_reviews` (additive, defaulted) fed
  by `project_ledger_entry`. `reason_code` stays `flagged_fields`.
- **The gemini leg asks for the reason:** an optional (never `required`) `field_reviews`
  property on the response schema plus one `PROMPT_FOOTER` rule. A malformed entry is repaired
  by the S0-bug-2 retry loop for free.
- **Deviation from the plan (small):** `field_reviews_for` also runs an *engine-supplied*
  `path` through `review_path` (idempotent for an already-dotted path). Without it an engine
  answering with the bare `key_fields` name would emit the same field twice — once as its own
  review, once as the derived entry for its flag.
- **Cap named, not taken:** `ExtractionResult` still carries **no** `contract_version` marker,
  the only top-level domain payload without one. That is orthogonal to this decision (it would
  change every dump and there is nothing here for `decode_wire` to tolerate) — but a *future*
  rename of this vocabulary would require adding it first.
- `spec/` untouched (`spec/open-questions.md`'s rewrite is S0-docs-4's); `wiki/evals/`
  untouched — the whole point of the derivation rule is that the oracle needs no change.

**Verification.** T1: `uv run ruff check` clean; `uv run pytest -q` = 789 passed / 31 skipped
(baseline 759/31 plus 30 new tests), 0 failed. T2: the projection prints
`['fields.key_fields.name', 'fields.key_fields.expiry']` with reasons
`model_flagged`(0.62)/`absent_from_document`(None); a `fields_requiring_review`
payload validates, keeps the dotted path unchanged and dumps `flagged_fields` only; a result
with an empty `flagged_fields` and one `derived` review disposes `hitl_review` /
`flagged_fields` and lands `('fields.key_fields.name', 'derived')` on the ledger entry; the
response schema carries `field_reviews` with the four reasons and does not require it.

## 2026-09-23 — S0-docs-4: correct the Aon open question's premise (spec proposal)

**Task:** S0-docs-4 · **Area:** docs (Sprint 0 reopen — extraction-capability gap)

**The drift.** `spec/open-questions.md:38-44` ("Feature request (incoming) — carrier PDF →
structured extraction for the Aon benefits consumer") rests on three claims that are all now
false. Re-verified against `~/aon` at implement time, not transcribed from the plan:

| Stale claim | Ground truth (checked 2026-09-23) |
|---|---|
| line 40: the consumer repo "handles xlsx + email today via a fixture extraction adapter; it has **no real PDF path**" | `~/aon/engine/extraction/gcp.py:386::GcpExtractor` — Vertex Gemini `_DEFAULT_MODEL = "gemini-2.5-flash"` (l.53), `temperature=0` + `response_schema=_ExtractedQuote` (l.453-454), `_MAX_ATTEMPTS = 3` (l.54) with each rejection reason fed back (l.492, l.519) and **no** fixture fallback, `fields_requiring_review` on the DTO (l.130) and in the prompt rule (l.145), `application/pdf` binary part (l.226) and `gs://` passed through by reference for Vertex to read natively (l.240-245). Logged at `~/aon/DECISIONS.md:586` ("2026-09-13 — S?-normalization-4: real `gcp` extraction adapter"), with a live seam run (`ADP_TEST_GCP=1 … 2 passed, 9 deselected`). |
| line 44: "the deferred Benefits/RFP cycle (currently 'Benefits/RFP deferred', see architecture)" | Sprint 1 — Benefits cycle (CUJ-3/11/12) closed 2026-09-07 T3-green (`TODO.md:3`, `TODO.md:49-52`); all seven `S1-benefits-*` tasks are archived in `docs/features/benefits.md`, including **S1-benefits-4 Dial-out + Deliver (beat 2 · CUJ-3)** and **S1-benefits-6 cross-framework carriers as real A2A servers (CUJ-12)**, covered by `tests/test_cuj3_dial_out_deliver.py` + `tests/test_cuj12_interop.py`. RFP (CUJ-9/13) genuinely *is* still deferred. |
| line 44: "a later-cycle capability, not a critical-path item" | It became the Sprint-0 reopen of 2026-09-23 (`TODO.md:97-109`): a consumer routed around the Bridge's reason for existing. |
| line 44: "a Benefits/RFP process skill vs. a generic document-extraction skill" framed as undecided-and-unbuilt | `skills/benefits-quote/` ships `SKILL.md` + `assets/schema.json` + `assets/validation.yaml` today, and both the extraction prompt (S0-arch-5) and Path-A key-field validation (S0-arch-8) are built from that asset. |

**The capability gap is the finding, not the consumer's schedule.** The consumer built its own
engine because the Bridge could not do the job the Bridge exists for. Bridge-side state of
each concrete gap, as of this entry:

- `S0-arch-5` **done** — prompt + `response_schema` built from the doctype skill's
  `bridge-schema` asset through the `skill_registry` seam.
- `S0-bug-2` **done** — bounded schema-validated retry inside `GeminiExtractor.extract`,
  rejection reason fed back, no fixture fallback.
- `S0-arch-6` **done** — a committed synthetic native-digital carrier PDF in the benefits
  corpus + `tests/test_pdf_extraction.py`; scanned/OCR stays the deferred Document AI swap.
- `S0-arch-8` **done** — Path-A typed key-field validation driven by the skill schema.
- `S0-debt-2` **done** — the consumer's review vocabulary (`fields_requiring_review`, dotted
  field paths, `ReviewReason`) **mapped**, deliberately not renamed on the wire.
- `S0-arch-7` **FAILED (`- [!]`)** — the by-reference artifact resolver (`resolve_bytes`
  breaking its never-raise contract on transport errors).
- `S0-arch-9` **OPEN** — the cross-repo conformance run, gated on the four above.

So the two convergence prerequisites the rewritten question names are exactly `S0-arch-7`
(a consumer's `FileWithUri` must be readable on the deployed path) and `S0-arch-9` (nothing
yet proves end-to-end that the Bridge can serve this consumer). The proposal states both as
prerequisites still to converge — **not** as shipped; overclaiming readiness would be the same
class of bug this task fixes.

**Done (implement side, this repo).**
- `design/architecture.md` de-staled, status-free: "Benefits and RFP stay eval scaffolds until
  Address lands" → RFP only; the `### Explicitly deferred past cycle 1` list drops **Benefits
  (CUJ-3/11/12)** and **Bridge-as-client dial-out** (both built in Sprint 1 — verified via the
  seven archived `S1-benefits-*` entries and the two CUJ test files, which pass), leaving RFP
  (CUJ-9/13) and cold unsolicited inbound push.
- `TODO.md` line 9 scope line: Benefits is built (Sprint 1); RFP stays the eval scaffold.
- `docs/features/production-readiness.md` — the `### S0-docs-4` archive entry carrying the
  falsified claims, the capability-gap framing, and the `S0-arch-5…9` state table (the
  cross-reference the task line asks for lives there and here, not in the spec text).
- **`spec/` gets no edit from this task** — `git diff --staged -- spec/` is empty and nothing
  under `spec/` is in this commit. (The working tree does show ` M spec/open-questions.md`,
  from an unrelated in-flight wikilink → relative-markdown-link conversion across all of
  `spec/` and `design/`; strip the link syntax and every changed line is identical prose, the
  Aon section's "no real PDF path" included.) The stale text stays in place awaiting a
  spec-owner pass.

**SPEC PROPOSAL (not applied — spec/ is protected).** Replace the whole section currently at
`spec/open-questions.md:38-44`, heading included, with:

```markdown
## Feature request (incoming) — carrier PDF → structured extraction, and who owns it (Aon benefits consumer)

**Source:** Aon Digital Placement (`~/aon`), CUJ-8 "full demo" journey (step 1: "got PDFs from carriers"). The original ask: the Bridge owns carrier-quote **PDF → strict structured JSON** so the consumer normalizes to its `BenefitsQuote` schema — validated fields (rates by tier, benefit design by category, effective dates), extraction confidence + `fields_requiring_review` per field, schema-validated retry on malformed output, bytes by reference as artifacts ([artifacts](../design/artifacts.md)).

**What changed since the ask was filed.** The consumer did not wait. It shipped its own extraction engine (`~/aon/engine/extraction/gcp.py::GcpExtractor` — Vertex Gemini with a pinned response schema, a bounded schema-validated retry that feeds each rejection reason back to the model, no fixture fallback, `fields_requiring_review`, native PDF bytes and `gs://` read by reference). Benefits is no longer an eval scaffold: the Benefits journeys are built and a `benefits-quote` doctype skill ships with its own `assets/schema.json` + `assets/validation.yaml`.

**The finding.** The consumer built its own engine because the Bridge — whose premise is *documents in, structured signals out* — could not do the job: extraction was not schema-driven (one hardcoded Address-shaped prompt), had no schema-validated retry, exercised no PDF anywhere, and could not fetch an artifact that arrived **by reference**. That is a capability gap in the Bridge core, not a consumer scheduling choice, and it is why this entry is no longer a "later-cycle" item.

**The live question.** Two extraction engines now exist for the same document class. Does the consumer **delegate** extraction to the Bridge over A2A, or keep its own engine and use the Bridge only for the collection spine? Settle:

- **Convergence prerequisites.** A real by-reference artifact resolver (leg-scoped signed-URL fetch + `gs://` by reference) so a consumer's `FileWithUri` is readable on the deployed path; and an end-to-end conformance run — carrier PDF in by reference → per-field confidence + review flags out → mapped onto the consumer's `BenefitsQuote` — green on both the fixture and gemini legs at terminal-outcome parity.
- **Capability axis.** A generic document-extraction skill vs. a Benefits process skill ([skills](../design/skills.md)).
- **Contract shape.** The Bridge's extraction-result vocabulary vs. the consumer's `fields_requiring_review` with dotted field paths — mapped, deliberately not renamed on the wire ([standards](standards.md) `contract_version` semantics).
- **Provenance ownership.** Which side supplies intake context the document itself never carries (e.g. the placement/program identifier), and "no fixture fallback on exhausted retries" as a discipline both engines hold.
- **Boundary.** Scanned/raster (OCR) PDF is out of the native-digital path; it is the Document AI adapter swap.
```

Second, one line in the "Settled since last revision" list (`spec/open-questions.md:11`) — the
bullet currently reads "**First build cycle is Core + Address**; Benefits/RFP deferred. See
[layout & scope](../design/architecture.md)." Replace with:

```markdown
- **First build cycle is Core + Address**; the Benefits journeys followed, RFP stays an eval scaffold. See [layout & scope](../design/architecture.md).
```

Both blocks are droppable verbatim: `##` heading level and relative links (`../design/artifacts.md`,
`../design/skills.md`, `standards.md`) match the file's conventions, and the text is status-free —
no task ids, dates, sprint names, or checkboxes.

**Deliberate reading of the task line.** The task says "point at `S0-arch-5…9`". `spec/` is
status-free and forkable, so task ids cannot live in the proposed spec text; the cross-reference
lives in this entry and in `docs/features/production-readiness.md`, and the spec text expresses
the same content as capability statements ("a by-reference artifact resolver", "an end-to-end
conformance run"). Recorded here so a reviewer does not read it as a miss.

**The question stays OPEN.** Unlike S0-docs-1 (which proposed *deleting* a question that had
shipped along its stated leaning), this one is genuinely undecided — the proposal is a rewrite
in place, with no "resolved" marker and no deletion.

**Verification.** No code changed (docs + planning artifacts only). T1: `uv run ruff check`
clean; `uv run pytest -q` = **789 passed, 31 skipped** — identical to the pre-change baseline,
as it must be. T2: the four `~/aon` / repo premise checks above re-run green (including
`tests/test_pdf_extraction.py tests/test_cuj3_dial_out_deliver.py tests/test_cuj12_interop.py`
→ 14 passed, 1 skipped); `git diff --staged -- spec/` empty — no `spec/` path in the commit —
and `grep -c "no real PDF path" spec/open-questions.md` still 1; `grep -rn "Benefits/RFP
deferred\|Benefits and RFP stay eval scaffolds" design/ docs/ TODO.md` silent.

- Learning: for a "premise-went-false open question" the /implement side is (a) re-verifying
  every claim against the *other* repo at implement time rather than trusting the plan's
  table, (b) de-staling the design/planning docs the spec text cites, and (c) recording an
  apply-ready rewrite — the spec edit itself stays a protected spec-owner action. The stale
  premise is only half the bug; the other half is that it had propagated into
  `design/architecture.md`, which the spec line cites as its authority.

## 2026-09-23 — S0-arch-7 (retry): a fetch failure is a ValueError, and "never raises" means never

**Context.** The S0-arch-7 resolver landed (`bridge/extraction/resolve.py`, `bridge/netguard.py`,
the `gs://` by-reference part, `tests/test_artifact_resolver.py`) but the task was flagged
`- [!]` on a single blocking review finding: `bridge/edge/artifacts.py::resolve_bytes` caught
`(ValueError, OSError)` while documenting — and being depended on for — a **never-raise**
contract (Decision 7). `httpx.ConnectError` is neither, so an allowlisted artifact host that
was merely *down* raised straight out of the edge's persistence step and failed the whole
disposition turn. Reproduced before the fix: `resolve_bytes("http://127.0.0.1:<closed>/doc.pdf")`
→ `httpx.ConnectError`. Same defect on the extraction leg in a quieter form — `resolve_document`
promises `ValueError` for "a refused or failing fetch", but a transport error escaped as a raw
httpx exception, leaking the client library into the caller's error vocabulary.

**Decisions made, and why.**

- **Normalize at the source, not at each caller.** `_fetch_http` now wraps the hop loop
  (extracted as `_fetch_hops`, no behaviour change): our own `ValueError` refusals re-raise
  untouched, anything else becomes `ValueError("artifact fetch failed: <Type>: <msg> (<url>)")`
  chained via `from exc`. One rule — *a failing fetch is a `ValueError`* — instead of two
  callers each guessing at the client library's exception tree.
- **Catch the base `Exception` there, deliberately, not an httpx tuple.** httpx's errors are
  not single-rooted: `InvalidURL` and `StreamError` sit outside `HTTPError`. An enumerated
  tuple is exactly the construct that produced this bug; enumerating a *different*, longer
  tuple would only move the next gap. The error type is preserved in the message, so nothing
  is hidden — a `TypeError` from our own code surfaces as `fetch failed: TypeError: …`.
- **`resolve_bytes` catches `Exception` too — belt AND braces.** The wrapper's entire job is
  that the disposition turn survives whatever the byte source does, so its catch must not be
  a list of anticipated failures. `asyncio.CancelledError` is a `BaseException` and stays
  uncaught: a cancelled turn must stay cancelled. The WARNING now names the exception type.
- **Nothing else in the S0-arch-7 surface moved.** Guard-before-connect, per-hop
  re-validation, the 3-hop budget, both body caps, the `file://` opt-in, the corpus traversal
  containment, `gs://` never being downloaded — all unchanged and re-proved by the T2 script.
  No `contract/`, no `spec/`, no `wiki/evals/`, no `terraform/`, no new dependency.
- **`design/artifacts.md` gained the failure-taxonomy sentence** (status-free): a transport
  failure *is* a fetch failure, and the persistence skip is unconditional — "an enumerated
  list of anticipated failures is how *skips* quietly becomes *fails the turn*".

**Gotcha for future tasks.** A docstring that says "never raises" is a contract other code is
written against (here: the executor leaves the ref unchanged and runs the turn anyway). When
a function like that grows a new failure source — a network leg, a new backend — the catch
clause is part of the diff, not an afterthought. Grep for the promise, not just the call site.

**Verification.** T1: `uv run ruff check` clean; `uv run pytest -q` = 793 passed / 31 skipped,
0 failed (4 new tests); `-m seam` 9 passed / 8 skipped. T2: the acceptance script prints 14
PASS lines and exits 0 — `gs://` rides as `file_data.file_uri` with no fetch; the corpus PDF
served over loopback reaches the model as byte-equal `application/pdf` inline data (1186
bytes) parsing to a `benefits-quote`; a 307 is followed and a 307 to the metadata IP dies on
hop 2; the metadata IP, a non-allowlisted loopback (with the server confirming **zero**
requests seen), `file:///etc/passwd` and `mock://x` are each refused; the corpus read and its
traversal containment are unchanged; leg-scoping through the real fetch app resolves the
owning `(context_id, filename)` and 404s a foreign one; and the blocker case — a dead
allowlisted host — refuses as `ValueError: artifact fetch failed: ConnectError …` on the
extraction leg while `resolve_bytes` returns `None` with a WARNING on the persistence leg.

## 2026-09-30 — S0-arch-10: rows ride in a sibling `tables` map; the contract takes a minor bump

**Context.** The Aon exec asked about mixed monthly and weekly carriers and about rate sheets. `benefits-quote` was flat strings and `key_fields` is `dict[str, str]`, so a rate table had nowhere to go. That capability gap is what S0-arch-9's conformance run is blocked on.

**Decisions.**

- **A sibling `tables: dict[str, list[dict[str, str]]]`, not a wider `key_fields`.**
  - Widening `key_fields` to `str | list[...]` would have touched every reader of it: `normalize`, `LedgerEntry.key_fields`, the executor, the dashboards, the prompt's "values MUST be strings" rule, and `_strip_to_known`.
  - It would also have broken the flat corpus invariants.
  - The sibling is additive and defaulted, so nothing that exists today changes shape.
- **Commission and product line are scalar key fields** (`commission_basis`, `commission_rate_pct`, `commission_notes`, `product_line`), not an object. `key_fields` stays flat; mapping `commission_rate_pct` onto `commission.rate_pct` is the consumer's step, as with every other path.
- **Frequency is per row, and `rate_basis` is a new column.** Carriers mix frequencies inside one quote, which is the exec's question, so a quote-level frequency would lose data. The vocabulary is a superset of Aon's.
- **Money is text in every cell.** This is Aon's hallucination-firewall invariant: the consumer code-parses `Decimal`s.
- **A string `enum` is shape, not value.** The typed Path-A model maps it to a `Literal`. This is also the first key-field enum anywhere (`product_line`), and no existing schema declared one, so existing behaviour is unchanged.
- **`CONTRACT_VERSION` "1" → "1.1", and minors are additive by rule.**
  - `decode_wire` now also strips unknown fields when the payload's minor is *newer* than ours.
  - Same- or older-version decode stays strict, which preserves S0-bug-1's posture.
  - Without this a "1.1" producer would hard-fail a "1" reader on `tables`. With it, the next additive field degrades gracefully instead.
  - `LedgerEntry.field_reviews` (S0-debt-2) shipped without a bump. The rule applies from now on.
- **The v1 required key fields are unchanged**, including `premium` and `deductible`, because the oracle and `normalize` depend on them. Consequence: a pure VB rate sheet with no single premium must still assert those two on **Path A**. Path B has no required fields (the union schema). Revisit this with S0-arch-9's conformance mapping, and change it only with the corpus, never to make a test pass.

**Cross-repo proposal (not applied — `~/aon` is another repo).**
- Aon's `RateFrequency` lacks `WEEKLY` / `SEMI_MONTHLY`.
- Its `RateTier` has no `rate_basis`.

A Bridge row carrying either value cannot land in Aon's `BenefitsQuote` without those additions, so they belong on Aon's side (their S0-arch-3/-6).

**Gotchas.**
- `review_path` maps a bare name to `fields.tables.{t}` only when that table is *present* in the result. A flag naming a table the document didn't print falls back to `fields.key_fields.{t}` (`absent_from_document`), because `contract/` can't see the skill schema.
- The tables model cache follows S0-arch-8's rule: it is keyed by schema content and never by the registry read.

**Verification.**
- T1: `ruff check` clean; `pytest` 821 passed / 31 skipped (28 new); `-m seam` 9 passed / 8 skipped.
- T2: `tests/test_structured_quote.py`. Evidence is in `docs/features/extraction.md` under Completed Work.

## 2026-09-30 — S0-arch-11: RFP collection is a pure aggregate; the due date anchors the SLA ladder

**Decisions.**
- **Contract 1.1 → 1.2, additive.** The new fields are `Requirement.party`, `CollectRequest.requirements` and `CollectRequest.due_at`, plus the enum `RfpItemStatus`.
  - `RfpItemStatus` includes **`acknowledged`**, which the TODO list lacked, to line up with Aon S0-arch-4's tracker statuses.
- **The ladder is anchored on the due date, not on the open time.** A step fires at `due + (step_ticks − overdue_ticks) × tick`, and `rfp.tick_seconds` in the skill policy gives the tick.
  - Status is derived on read, so no alarm is armed.
  - Actually *sending* nudges and "chase now" are deferred to S0-frontend-1 / S0-arch-13.
- **Carrier view vs item view.**
  - An invited or acknowledged cell shows `overdue` once the ladder leaves on_track.
  - A carrier is on_track once none of its tracked cells is waiting. A cell that has tripped the cap escalates the carrier regardless of the clock.
- **The resubmit cap uses the `bridge.pathb` boundary** (`attempts > max_resubmissions` → escalate, never reject), with the same vocabulary (`REASON_ESCALATED`, `ESCALATE_MESSAGE`).
  - A HITL park (`needs_review`) is **not** an attempt. Only a rejection or a gate resubmit counts.
- **Declined stays outstanding** (reason_code `declined`). Waiving it is the app's decision (sense B), made through `update_requirements`. The Bridge never drops a cell and never mints `done`.
- **Dashboard.**
  - On an RFP row, `status: done` means "nothing left to chase", not sense-B done.
  - RFP rows are `hitl: False`. Per-quote HITL rides on the ledger entry.
  - The row drops the view's `as_of`, so the SSE diff-poll only fires on a real state change, not on every tick. The self-review caught this.

**Spec proposal (not applied — `spec/` is protected).**
- The CUJ-9/13 rows and Driver lines in `spec/cujs.md` should reflect carrier RFP collection via the `benefits-rfp` process, with Driver `tests/test_rfp.py`.
- Note that `spec/demos/rfp-demo.md` frames RFP as a supplier-onboarding casefile (Nimbus). That is a different framing; reconcile it or keep both.

**Gotchas.**
- Every datetime passed to `bridge.rfp` must be tz-aware, and naive ones raise `ValueError`.
- The contract-version tests in `tests/test_structured_quote.py` are now minor-agnostic, so the next additive bump doesn't break them.

## 2026-09-30 — S0-arch-9: the consumer conformance run — carrier PDF by reference → A2A → the consumer's BenefitsQuote

**Decisions.**
- **Engine confidence rides on the ledger (contract 1.2 → 1.3, additive).** `LedgerEntry.overall_confidence: float | None = None` and `field_confidence: dict[str, float] = {}`, projected by `bridge/collect.project_ledger_entry` from the `ExtractionResult`. `None` means "no engine number", not zero. A pre-1.3 turn decodes with the defaults. A newer-minor turn with unknown entry keys decodes tolerantly. This is the only `bridge/` logic change.
- **The oracle is a separate file.** `wiki/evals/benefits/conformance.json` is used, not `expected.json`, because the five-record count of `expected.json` is load-bearing for the normalize, standard, comparison and CUJ-3/11 legs. Same `{"documents": [...]}` shape, so `FixtureExtractor(corpus_path=...)` reads it unchanged.
- **The stem is the parity pivot.** A record's `id` equals its PDF stem, so `FixtureExtractor._normalize_key` sends both reference shapes (`http://…/x.pdf` and `gs://…/x.pdf`) to the same record.
- **Path B, not Path A.** Path B enforces no required key fields. A VB rate sheet carries no `premium`/`deductible`/`coverage_limits`/`exclusions`, so both conformance records are `schema_invalid` on Path A (verified).
- **The mapping refuses loudly.** `tests/conformance/consumer.map_entry_to_quote` raises `ConformanceGap` and collects **every** gap. Gaps are:
  - a frequency outside the consumer's `RateFrequency` (WEEKLY/SEMI_MONTHLY);
  - a non-FLAT `rate_basis`;
  - a review path it cannot map;
  - missing engine confidence.
  - **Deviation from the plan:** an unmodelled key field or table that is *present* is also a gap. The mapper never drops data silently.
- **Why a mirror and a subprocess.** Aon's package is also named `contract`, which collides with ours. So `tests/conformance/consumer.py` mirrors the consumer's models (`extra=forbid`, pinned at Aon 83c86cd). The gated drift check (`BRIDGE_TEST_CONSUMER_REPO`) runs the mapped quote through the real consumer models in a subprocess using the consumer's own venv (`PYTHONDONTWRITEBYTECODE=1`, read-only).
- **The parity key is terminal-outcome only:** disposition, gate, reason, review paths and the mapped quote minus `source`. It includes no floats and no source uri, because the uri differs per leg and per reference shape.
- **The live leg asserts record A only.** Record B's review flag is a judgement a live model may or may not make. A test-local canonicalizer absorbs echo variance: case, `USD`/`$`, an `Age` prefix on the band, and the "absent, and the consumer has no such field" reviews. `bridge/` and the oracle are never loosened.

**Findings / proposals (not applied).**
- (a) **Aon's `RateFrequency` lacks WEEKLY/SEMI_MONTHLY, and the consumer has no `rate_basis`.** The Bridge carries all of these since S0-arch-10, and the mapper refuses them as gaps. This is proposed consumer-side work.
- (b) **The Path-A required list for `benefits-quote` (`premium`/`deductible`/`coverage_limits`/`exclusions`) rejects a real VB rate sheet.** Propose relaxing it to what a rate sheet prints, together with a decision about the benefits corpus (whose five records assert those fields).
  - Live evidence: Gemini flags exactly those fields as `absent_from_document` on record A, which routes a clean sheet to `hitl_review`.
- (c) **The consumer can read `extraction_confidence` from `LedgerEntry.overall_confidence`** instead of re-deriving it.
- **Spec proposal (`spec/` is protected).** Add a `spec/cujs.md` Driver line pointing at `tests/conformance/` (`uv run python -m tests.conformance.journey`) for the benefits consumer-conformance journey.

**Live observations (Vertex Gemini, `GOOGLE_GENAI_USE_VERTEXAI=TRUE BRIDGE_TEST_GEMINI=1`).**
- Record A: `pending`/`hitl_review`, conf 0.98. Every key field and rate/design row is correct. The model flagged only the absent Path-A fields (see b). One run in five echoed the age band as `Age 30-39`, and the canonicalizer now absorbs that.
- Record B: the model flagged `tables.rates[1].frequency` (the `per pay period` cell) and **nulled** that cell rather than guessing. The fixture oracle keeps BI_WEEKLY plus a low_confidence review.

**Verification.**
- T1: `ruff check` clean; `pytest` 906 passed / 34 skipped, 0 failed (baseline 856 / 31); `-m seam` 9 passed / 8 skipped.
- T2: `tests/conformance/` gives 46 passed / 3 gated skips.
  - Gated consumer drift: 2 passed against `~/aon`, whose tree was unchanged.
  - Gated live Gemini: passed, stable across 4 reruns after the canonicalizer fix.
  - `uv run python -m tests.conformance.journey`: 8 runs, PARITY OK ×2, and a ConformanceGap on the WEEKLY/PER_1000 probe.

## 2026-09-30 — S0-arch-12: the `docai` engine is Gemini with a Document AI OCR pre-pass

**Decisions.**
- **A pre-pass, not a second adapter.** `GcpExtraction` resolves `docai` to `GeminiExtractor(prepass=DocAIPrepass())`. Document AI (the client's existing OCR / Layout / Form Parser processor) adds only a machine-read text layer. Classification, carrier-term mapping, the pinned response schema, retry and null-pruning stay in the one Gemini engine. There is no per-doctype entity map. Design: `design/docai-prepass.md`.
- **Request shape.** `docai` sends `[prompt, document, ocr_text]`. The OCR part sits in the base contents, so a schema retry keeps it, and the pre-pass runs **once per extraction**, before the retry loop.
- **No fallback.** A Document AI error propagates unchanged and no Gemini call is made. A missing `BRIDGE_DOCAI_PROCESSOR` raises `RuntimeError` naming the env, and a malformed name raises `ValueError`. Both happen when the engine is resolved, before any fetch. Empty OCR text is not an error: a warning is logged and no part is added.
- **Mime gating.** Only pdf/png/jpeg/tiff/gif/bmp/webp go to Document AI. SVG (the Address corpus) skips it with no call, so the request keeps the plain-Gemini shape.
- **By reference.** A `gs://` input rides as `gcs_document`, so the Bridge downloads nothing. Bytes ride as `raw_document`. Requests are plain dicts, which the proto-plus client accepts. This was checked against google-cloud-documentai 3.15.0.
- **Processor by resource name, regional endpoint.** Calls go to `{location}-documentai.googleapis.com`, because the default endpoint answers "not found" for an `eu` processor. The per-doctype `bridge-docai-processor` skill key is not consulted, since doctype is an output of extraction.
- **Cross-project IAM.** Terraform grants `roles/documentai.apiUser` to `bridge-runtime` on the processor's project (`modules/iam` `docai_api_user`). `manage_docai_grant=false` skips the grant, and the `docai_grant` output is then applied by the client.
- **Dependency.** The `gcp` extra adds `google-cloud-documentai>=3.0`, locked at 3.15.0. The import is lazy, and an AST test forbids a module-level `google*` import.
- **Aon sharing.** Aon's `ADP_DOCAI_PROCESSOR` is `BRIDGE_DOCAI_PROCESSOR`, with the same grant, the same injection and the same no-fallback rule. `bridge/extraction/docai.py` depends only on the stdlib and `ResolvedDocument`, so it can be vendored.

**Gotchas.**
- New `terraform test` run blocks that reference modules need `terraform init -backend=false` first. The lock file was unchanged.
- `tests/corpus_scanned_pdf.py` output is byte-deterministic for a given zlib build (level 9 Flate). A different zlib may produce different bytes; the page is identical.
- Known quirk, left alone: `GcpExtraction.extract` resolves the engine on every call, so `DocAIPrepass` is rebuilt per extraction. The client is lazy, so no RPC is wasted.
- `terraform/main.tf`, `terraform/modules/runtime/{main,variables}.tf` and `design/gcp-substrate.md` carried unrelated uncommitted edits. Only this task's hunks were staged.
- `wiki/evals/benefits/description.md` (≈:38/:43) still describes scanned input as out of scope until the corpus proposal below is applied.

**Corpus proposal (not applied: `wiki/evals/` is read-only here).**
- Add `wiki/evals/benefits/images/benefits-carrier-c-eur-fr-scanned.pdf`, generated with `uv run python -m tests.corpus_scanned_pdf wiki/evals/benefits/images/benefits-carrier-c-eur-fr-scanned.pdf`. It is a 300 dpi raster-only page with no text layer.
- Add a `benefits-carrier-c-eur-fr-scanned` record that clones carrier-C's party, extraction, `expected_normalized` and `expected_gaps`.
- Rewrite the scanned-input boundary sentences in `description.md`.
- Once applied, add a byte-equality drift test (corpus file vs. generator).
- No spec change is required. Optional spec proposal: a `spec/cujs.md` Driver line for the scanned-quote leg.

**Verification.**
- T1: `ruff check` clean; `pytest` 942 passed / 36 skipped, 0 failed; `-m seam` 9 passed / 8 skipped; `uv lock --check` OK; terraform fmt/validate clean and `terraform test` 21 passed.
- T2:
  - `tests/test_docai_extraction.py` gives 36 passed / 2 gated live skips (`BRIDGE_TEST_DOCAI=1` + `BRIDGE_DOCAI_PROCESSOR`).
  - The scanned PDF yields 0 characters from pdftotext and 1 image in pdfimages, and two builds are byte-identical.
  - The scanned carrier-C leg reaches the record's `expected_normalized`/`expected_gaps` with `ROUTE_NONE`.
- The live Document AI leg was not run (no processor or credentials here). It is deferred to the provisioned run.

## 2026-09-30 — S0-arch-13: the Bridge owns inbound email — a dedicated M365 mailbox, token-correlated, create-only landing

**Ownership.** The Bridge owns the email intake edge. Evidence (read-only, `~/aon/TODO.md`):
- Aon **S0-arch-3** splits the work: docbride3 owns collection (CollectRequest/ExchangeTurn, the disposition gate, outstanding status, SLA chase, resubmits, portal); Aon keeps typed extraction and comparison.
- Aon **S0-infra-7** defers the email-ingress owner to the S0-arch-3 ADR, which is not yet written (`~/aon/design/decisions/` holds ADR-001..003).
- Aon **S0-arch-5** recommends a dedicated `rfp-quotes@` mailbox with an RFP token / plus-address on M365 Graph change notifications, scoped by an Application Access Policy, and names an `EmailIngress` seam (local = `.eml` drop dir, gcp = Graph).

A carrier answering a solicited RFP by email is a collection channel, so it lands here. It is **solicited** (correlated by token to an open exchange), so it is not the deferred cold push of `design/bridge.md`. Design: `design/email-intake.md`.

**Decisions.**
- **A ninth seam, `email_ingress`.** Unlike the artifact store (an ADK-native backend swap) and the portal store (host glue), this is a new external boundary. The seam is the **landing zone**, not Graph: `land` / `pending` / `read` / `put_attachment` / `mark_processed` / `processed`. Local = `BRIDGE_EMAIL_DIR`, gcp = `BRIDGE_EMAIL_BUCKET`, same object layout. Graph (`bridge/email_ingress/graph.py`) is the deployed transport into `land`; `python -m bridge.email_ingress land x.eml` is its local analogue.
- **The token.** `base32(HMAC-SHA256(BRIDGE_EMAIL_TOKEN_SECRET, rfp_id "\n" party))[:16]`, lowercase, one per (RFP, carrier) leg. It rides the plus-address (`rfp-quotes+<token>@`), with a `[ref:<token>]` subject tag as fallback. Correlation recomputes every open leg's token and compares with `hmac.compare_digest`. Tokens naming two different legs raise `AmbiguousToken` and the mail is quarantined `ambiguous_token`, never split. Leg isolation: a token is not a capability beyond its one leg.
- **Create-only landing is the idempotency and the queue.** Key = `sha256("message-id:" + normalized Message-ID)[:32]` (or `sha256(raw)` without one). Local publishes via temp file + `os.link`; GCS uses `if_generation_match=0`. `pending()` = inbox minus processed, in landing order (GCS sorts by `time_created`, not name).
- **403 vs 412.** On GCS a `412` is a duplicate. An objectCreator-only identity overwriting an existing object gets **403**, not 412, so a 403 is a duplicate only when `exists()` is true; otherwise it is re-raised.
- **Directory per attachment index** (`attachments/<key>/<i>/<filename>`): two parts may share a filename, and the basename survives for the extractor (the fixture engine keys on `Path(uri).name`).
- **Retry order, at-least-once.** (1) Extract every attachment, board untouched; `ValueError`/`KeyError` marks that attachment unreadable, and any other exception propagates (the drain stops, no marker, the next drain retries in landing order). (2) Persist the bytes leg-scoped. (3) A fresh `board.rfps.get`, then acknowledge / `receive` / `note_unreadable`, then `board.put`, with no `await` in between. (4) `mark_processed`. A crash between 3 and 4 re-files with the same `doc_id` (`email-<key>-<i>`). A retry after a crash in 2–3 may add one extra artifact version.
- **`rfp.note_unreadable`** (new, pure): an audit-only `attachment_unreadable` event (`<filename>: <reason>`). The cell does not move. An unsupported media type (`rates.xlsx`: anything outside `SUFFIX_MIME`) or a deterministic engine refusal never gets a fabricated verdict; the disposition gate stays the sole verdict authority.
- **Quarantine never touches the board.** Reasons: parser (`oversize`, `too_many_attachments`, `unparseable`), `sender_authentication_failed` (an explicit `dmarc=fail`), `no_exchange_token`, `ambiguous_token`. Each is written to the marker (`GET /email/outcomes/{key}`) and logged at WARNING. A Graph-side oversize message is recorded under `sha256("graph:"+id)[:32]` and answered 202, so Graph does not retry forever.
- **The body is never persisted** beyond the raw `.eml`, which the bucket lifecycle expires (`email_retention_days`, default 30; ART-3, no Bridge GC).
- **Webhook.** clientState is compared in constant time before any Graph call. The notification `tenantId` is checked when the configured tenant is a GUID. The message id is regex-checked and the URL is built from the configured mailbox only, with no redirect following. The body is capped at 256 KiB (413) and the validationToken at 1024 (400). A Graph error or storage error returns 503 so Graph retries.
- **Deployed topology.** A webhook-only Cloud Run service (`<service>-email-webhook`) runs as `bridge-email-webhook`. That SA has no project roles: only `secretAccessor` on the Graph client secret + clientState and `roles/storage.objectCreator` on the email bucket. The runtime hosts the processor: `objectViewer` + `objectCreator` on the bucket (never `objectAdmin`) and `secretAccessor` on the token key only. All of it is gated on `enable_email_ingress` (default false: zero resources). Secret containers only; no `google_secret_manager_secret_version` in HCL.
- **The `allUsers` exception.** `roles/run.invoker` for `allUsers` on the webhook service is a deliberate trust-boundary exception: Graph cannot present a Google identity. Controls: clientState auth, own-mailbox-by-id fetch, a create-only SA, no runtime/DB/Vertex grants, `max_instance_count = 3`. An org policy with Domain-Restricted Sharing rejects `allUsers`; the operator then needs a project exception or an LB/Gateway path.

**Deviations from the plan.**
- The attachment-name validator also rejects `?` and `#`, and the local attachment URI is an unencoded `file://` + posix path, so the URI round-trips through the resolver.
- A `Content-ID` image part with **no** disposition counts as inline (skipped), like an explicit `inline`.
- Parsing uses `get_payload(decode=True)`, not `get_content()`, so undecodable charsets do not raise.
- A forwarded `message/rfc822` is opened one level deep; a deeper forward is kept as an opaque `.eml` attachment.
- The landing key hashes `"message-id:" + id`, a domain-separated prefix.
- Artifacts are persisted **before** the fresh board read (rather than after), which keeps the read→put window await-free.
- The webhook service carries no `deletion_protection` argument. The variable exists only in an unrelated uncommitted runtime hunk, so the provider default applies.
- A shared test helper module, `tests/email_fixtures.py`, was added.
- The T2 run uses the deployed split (a webhook app with no processor plus a processor app) rather than one app, so the explicit drain returns the outcome. With a processor bound, the webhook's background drain files first and an explicit drain returns `[]` (covered in `tests/test_email_webhook.py`).

**Proposals (not applied: `spec/`, `wiki/evals/`, other repos are read-only here).**
- `spec/standards.md` (≈:23, :46): "eight seams" → "nine seams", adding `email_ingress` to the enumerated list.
- `spec/cujs.md`: a Driver line on the RFP CUJs (CUJ-9/13) for "a carrier replies by email to its tokened intake address".
- Optional corpus case `wiki/evals/rfp/`: a synthetic reply `.eml` (CI + HI PDFs + `rates.xlsx` + inline logo) with an expected outcome (2 filed, 1 unreadable, `skipped_inline=1`).
- Cross-repo (Aon): S0-infra-7 → "owned by docbride3 S0-arch-13". S0-arch-5's ADR should cite the seam name `email_ingress` and the `BRIDGE_EMAIL_*` env vocabulary. S0-bug-6 (MIME-parsing Aon's own `.eml` fixture) stays Aon's.

**Follow-ups.**
- A deployed drain trigger on the runtime (a Cloud Tasks / scheduler tick, or a GCS object-finalize push) — today the drain is `POST /email/drain` or the co-hosted background drain.
- Subscription auto-renewal (< 4230 min) and Graph `lifecycleNotificationUrl` handling; today `python -m bridge.email_ingress renew` is an operator step.
- A durable RFP store with a cross-replica claim. The board is in-memory host glue, so delivery is at-least-once per replica.
- An xlsx rate-sheet parser, after which `rates.xlsx` stops being unreadable.
- Inject `reply_address` / `reply_subject_tag` into the outbound RFP request and the portal (S0-frontend-1).

**Verification.**
- T1:
  - `uv run ruff check` clean.
  - `uv run pytest -q`: 1063 passed / 41 skipped, 0 failed after the review fixes (1046 before them; baseline 942 / 36). The 5 new skips are live legs only: 3 email gcp-seam params (`BRIDGE_TEST_GCP`), 1 `BRIDGE_TEST_GRAPH`, 1 `BRIDGE_TEST_GEMINI`.
  - `-m seam`: 12 passed / 11 skipped. `tests/test_seams.py` asserts the 9 names and 9 local adapters.
  - `uv lock --check` OK; `.terraform.lock.hcl` unchanged.
  - `terraform fmt -check -recursive` and `validate` clean. `terraform test`: 31 passed, including 10 new runs in `tests/email.tftest.hcl` (default zero email resources; enabled: ingress ALL, the webhook SA, `secret_key_ref`-only Graph secrets, `allUsers` invoker, bucket role sets, PAP, lifecycle age, the runtime token `secret_key_ref`, a runtime-only token accessor).
- T2 (`uv run python /tmp/s0arch13_t2.py`, uncommitted): 9/9 PASS.
  1. The handshake echoes `abc 123` as `text/plain`.
  2. A notification lands one mail; the drain files 2, marks 1 unreadable (`unsupported_media_type`), `skipped_inline=1`. carrier-a CI = received, HI = incomplete (`flagged_fields`), carrier-b = invited.
  3. The ledger `ArtifactRef` is `http://bridge.test/artifacts/<ctx>/benefits-conformance-ci-usd.pdf?v=0`. The store bytes equal the corpus PDF, the fetch app returns 200 with the same bytes, and carrier-b's context returns 404.
  4. A re-delivery counts `duplicates:1`, the drain returns `[]`, and the board `model_dump` is identical.
  5. A wrong clientState is `ignored:1` with zero Graph requests.
  6. A token minted under another secret is quarantined `no_exchange_token`; the board is unchanged.
  7. Graph 500 → 503, nothing landed.
  8. The CLI prints `landed <key>` then `duplicate <same key>`.
  9. `terraform test` passes the email runs; no secret version in HCL.
- Not run: the live Graph leg (no tenant or credentials here) and the live GCS/Gemini legs. They are deferred to the provisioned run.
- Review fixes (CRITICAL review, 2026-09-30), each with a test:
  - `sanitize_filename` now strips leading dots to a fixed point (`". .pdf"` used to come back as `".pdf"`, which the seam validator refuses) and caps names at 200 UTF-8 bytes as well as 128 characters (128 CJK characters overflowed the 255-byte NAME_MAX). Before the fix, either case made `put_attachment` raise out of every drain and wedged the queue behind that mail. The validator enforces the same byte cap.
  - Graph oversize is its own type, `MessageTooLarge`, and it is the only fetch failure the webhook acknowledges. A garbled 2xx token response used to raise a bare `ValueError`, which the webhook read as "oversize": it acked the notification and never landed the mail. It is now a `GraphError`, which answers 503.
  - `GET /email/outcomes/{key}` is mounted only when a processor is bound. The public webhook no longer serves markers (sender, subject, RFP, carrier), which its create-only SA could not read anyway.
  - A malformed `BRIDGE_EMAIL_MAX_BYTES` fails at `build_email_app(graph=…)` / `EmailProcessor` construction. Before, every mail was quarantined with that error as the reason.
  - Accepted as-is: on GCS, a 403 overwrite answer to the objectCreator-only webhook SA cannot be confirmed by `exists()`, because that SA has no `get`. It surfaces as 503, Graph retries, and the mail is already landed, so nothing is lost.

## 2026-09-30 — S0-perf-1: large documents are extracted as page windows over the whole document; the scale harness is the oracle

**Context.** A 10-carrier × 5-product RFP brings back 1000-row rate sheets. One schema-driven Gemini response that size is cut at the output budget (`finish_reason == MAX_TOKENS`). The old engine sent no explicit budget and read the cut body as a malformed payload: it echoed the body back through the schema retry three times at the same budget and then raised. The document was lost, and three calls were wasted.

**Decisions.**
- **Page windows go in the prompt; the file is never split.** Every window request is `[prompt, document, (ocr_context), window_instruction]`, carrying the whole document part. Splitting a `gs://` object would need the download the resolver exists to avoid (S0-arch-7), and no PDF library is installed (S0-arch-6). Input cost grows with windows × document tokens. The output, which is the part that truncates, is bounded per window.
- **Truncation is detected from the flag, not from the JSON.** `candidates[0].finish_reason` is read as the genai enum `.name` or a plain string, before parsing. A truncated response never enters the retry loop.
- **Every request carries an explicit `max_output_tokens`.** The default is 32768 and the survey uses 256. A documented budget makes truncation deterministic and testable. The 8-page default window (~320 rows × ~60 tokens ≈ 19k) leaves headroom for 2.5-flash thinking tokens, which count against the same budget.
- **Proactive windows for an inline PDF longer than `chunk_pages`, reactive bisection otherwise.** The stdlib page count uses `/Count` on `/Type /Pages`, then `/Type /Page` leaves, then inflated `/ObjStm` streams bounded at 50 MB, and otherwise returns None. A `gs://` object, or a PDF it cannot read, gets one page-count survey call only after a truncation. A short document is still one call, which keeps the 8 Address records and every one-page quote byte-for-byte on the old request shape.
- **The merge is code-side and conservative, and there is no partial success.**
  - Rows concatenate with no dedupe.
  - Scalars are first-wins, and a disagreement gets a `model_flagged` review.
  - Window-local row paths are rebased, and an index the window never emitted is coarsened to the table with a WARNING.
  - Confidence takes the min and `legible` takes all().
  - Any failed window fails the extraction with the original exception type: the TaskGroup `ExceptionGroup` is unwrapped, and with several failures the first by page order is raised with a note.
- **`ExtractionTruncated(ValueError)`.** It is a deterministic refusal for this document: a single page that alone overflows, `chunk_pages=0`, a non-paged document, or the 128-window cap. The email processor's `ValueError` → unreadable mapping therefore takes the right branch, while transport errors stay transport errors.
- **Every `generate_content` call runs via `asyncio.to_thread`.** All test doubles implement only `.models.generate_content`, so the thread hop is seam-neutral. `client.aio` would have forked the doubles.
- **Env knobs refuse loud at construction:** `BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS` (≥ 1), `BRIDGE_EXTRACTION_CHUNK_PAGES` (≥ 0) and `BRIDGE_EXTRACTION_CHUNK_CONCURRENCY` (≥ 1). A kwarg wins over the env. `GcpExtraction` builds `GeminiExtractor()` with no kwargs, so the deployed env applies with no seam change.
- **`readmodel_snapshot` projects once per tick.** The queues become filters over the one projection, and the snapshot is equal to the old three-call composition (tested).

**Gotchas.**
- A truncated constrained-decoding body never closes its braces, so it fails as a `JSONDecodeError`, which looks exactly like a retryable rejection. Only the finish-reason flag tells the two apart.
- A window that emits a row path for a row it did not return cannot be placed. Guessing the global index would point a reviewer at the wrong row, so the path is coarsened instead.
- With the docai pre-pass, every window carries the same whole-document OCR part, which is bounded at `MAX_OCR_CHARS`. A long scan's later pages may therefore lack OCR grounding (the document itself is still attached).
- `tests/corpus_pdf.build_pdf` is byte-guarded. The multi-page generator is a separate function (`build_multipage_pdf`).

**Proposals (not applied: `spec/` is read-only during /implement).**
- `spec/standards.md`: a scale NFR. "A 1000-row / 50-page carrier document extracts with every row (count + order) on the deployed engine; an RFP of 10 carriers × 5 products × 1000 rows is collected, projected and streamed without blocking the event loop."
- **S0-perf-2:** the dashboard SSE frame carries every entry's full tables (8.6 MB per frame at 10 × 5 × 1000). Project counts and summaries into the stream and serve tables on demand from `/exchanges/{id}`.
- A concurrent Collector fan-out (today each carrier's document is extracted serially by its caller).
- A per-window OCR slice for long scans (the Document AI page ranges), replacing the shared whole-document OCR part.
- A `thinking_config` budget knob so window sizing is not hostage to the model's thinking-token spend.

**Verification.**
- T1:
  - `uv run ruff check` clean.
  - `uv run pytest -q`: 1159 passed / 42 skipped (baseline 1063 / 41). The one new skip is the `BRIDGE_TEST_GEMINI` live scale leg.
  - `-m seam`: 12 passed / 11 skipped (unchanged).
  - The only existing test edited is `test_no_doctype_schemas_pins_no_response_schema`, which now also pins `max_output_tokens == 32768`.
- T2:
  - `python -m tests.perf.harness --json /tmp/s0perf1.json` exits 0 with 16/16 checks passing. Table in `docs/features/extraction.md#scale-rfp-size-documents`.
  - The 8 Address records each make one `[prompt, document]` call.
  - `BRIDGE_EXTRACTION_CHUNK_PAGES=x` raises a `ValueError` naming the env.
  - `GcpExtraction(engine="gemini")` resolves to `32768 8 4`.
  - The `contract/`, `spec/`, `wiki/`, `terraform/` and `bridge/seams/` diff for this change is empty.
- Live leg: `GOOGLE_GENAI_USE_VERTEXAI=TRUE BRIDGE_TEST_GEMINI=1 uv run pytest tests/perf/test_scale_live.py` passed (1 passed in 326 s). The run used the real 2.5-flash on Vertex with ADC and the default knobs, on the 26-page synthetic sheet. The row count was exactly 1000, and at least 99% of rows matched canonically.
  - On the live model, 3 of the 4 eight-page windows (1-8, 9-16, 17-24) still hit `MAX_TOKENS`, and 13-16 was bisected again down to 2-page windows. The reactive bisection is therefore load-bearing in production, not only a fallback.
  - The ~320-row output estimate for an 8-page window plus thinking tokens overflows 32768 in practice. This is evidence for the `thinking_config` proposal, or for a smaller default `chunk_pages` (4), to save the wasted truncated calls. The default was not changed here because the plan pins 8.

## 2026-09-30 — S0-docs-5: the disposition gate is the published review contract; thresholds are checked against an executable corpus corridor

**Context.** The exec asked Aon "what level of human oversight, and when is manual review invoked?". The Bridge's answer is `bridge/disposition.py::dispose`, but it had three gaps:
- It was undocumented for consumers: `design/disposition.md` described it in prose, with no numbers, no boundary semantics and no wire mapping.
- It was uncalibrated: nothing tied 0.85 / 0.55 to the corpus beyond "the 8 Address records still pass".
- Its number differed from Aon's: Aon's frontend highlights at `LOW_CONFIDENCE_THRESHOLD = 0.9` (`~/aon/frontend/src/lib/review.ts:4`), while Aon S0-arch-2 says it will model its tiers on our 0.85 / 0.55.

**Decisions.**
1. **The contract values stay 0.85 / 0.55.** Both are inside the corpus corridor. Moving to 0.9 would be an equally uncalibrated change and would churn three policy files and their tests. Reconciliation happens on the consumer side: a consumer renders the Bridge's `gate` / `reason_code` / `field_reviews` and never re-thresholds `overall_confidence`. A confidence badge, if shown, uses `auto_approve_at`.
2. **Calibration is executable.** It is a feasible-corridor check over the gate-bearing corpus. The pure arithmetic (`CorridorPoint`, `Bound`, `ThresholdCorridor`, `threshold_corridor`) sits beside the gate in `bridge/disposition.py`, mirroring its precedence. The corpus and YAML reading live in `bridge/calibration.py` (`python -m bridge.calibration`). A threshold change is legal only if the corridor contains it and the live parity leg (`test_live_corpus_terminal_outcome_parity`, `BRIDGE_TEST_GEMINI=1`) stays green. Tightening the corridor is a corpus change.
3. **Threshold validity is enforced.** `DispositionThresholds.__post_init__` refuses anything outside `0 ≤ resubmit_below ≤ auto_approve_at ≤ 1`. It is written as one chained comparison, so it refuses NaN too. Equal values are legal: there is then no confidence band, and flags still route to HITL. `load_disposition_policy` re-raises the refusal naming the skill, so an inverted policy fails at load.
4. **The contract's home is `design/disposition.md` ("The review contract"), not `design/bridge.md`.** This deviates from the TODO `Files:` line. `design/bridge.md` carries unrelated uncommitted user edits (the wikilink → md-link conversion), a path-scoped commit would sweep them in, and `git add -p` is unavailable. `design/a2a-consumer.md` links the section.
5. **Design stays status-free and names no consumer.** The current corridor numbers and the Aon specifics live in `docs/features/extraction.md` and here; `design/` states the method and the invariant.

**The corridor and its honest limit.** There are 10 gate-bearing points (Address `expected.json` 8, benefits `conformance.json` 2):
- `resubmit_below ∈ (unbounded, 0.72]`, with the upper edge set by `bill-aquautil-clear`.
- `auto_approve_at ∈ (0.72, 0.94]`, with edges set by `bill-aquautil-clear` and `benefits-conformance-ci-usd`.

0.85 / 0.55 is inside, and **so is Aon's 0.9: the corpus cannot separate the two.** There is no lower bound on `resubmit_below`, because the only resubmit record is illegible and the legibility gate catches it at any threshold.
- The flags must come from `signals_from_result`. The HI conformance record is flagged only through its `field_reviews`. Reading raw `flagged_fields` would set `auto_approve_at > 0.90` and put the 0.85 default falsely OUTSIDE.
- A sweep test proves the corridor is exact. Inside it, every corpus gate reproduces. Just outside it (auto 0.72, auto 0.95, resubmit 0.73), a gate flips.

**Accepted as-is.** `bridge/portal/session.py:60-66` catches a policy `ValueError` and falls back to `DEFAULT_THRESHOLDS`. With the new validation, an inverted policy on the portal therefore degrades silently to the defaults (no log line) instead of crashing the session. The new all-skills guard plus `python -m bridge.calibration` catch it before deploy.

**Follow-up.** Thread the process-policy thresholds through the A2A agent tool `run_disposition_gate`, `bridge/collect.py`, `bridge/edge/email_ingress.py`, `bridge/patha.py` and `bridge/rfp.py`. Today only the portal (Path B) honours a per-process override, and every other path uses `DEFAULT_THRESHOLDS`. The contract holds because the new test (`test_every_process_policy_equals_defaults_and_is_inside`) keeps every policy equal to the defaults.

**Proposals (not applied: `spec/`, `wiki/evals/` and `~/aon` are read-only here).**
- `spec/standards.md`, a new oversight NFR: "The disposition gate's order and thresholds are a published consumer contract; thresholds must lie inside the eval-corpus corridor (`python -m bridge.calibration` exits 0)."
- `spec/cujs.md` CUJ-5: add `tests/test_disposition_calibration.py` as a second Driver.
- `wiki/evals/address`:
  - Add a legible low-quality record (for example a legible faded bill at confidence ≈ 0.45 → `rejected/resubmit/low_quality`). It would give `resubmit_below` a lower bound.
  - Add boundary-adjacent unflagged records (≈ 0.80 → hitl, ≈ 0.88 → auto) to narrow `auto_approve_at`. Only these can separate 0.85 from 0.9.
- Cross-repo, Aon:
  - S0-arch-2 adopts 0.85 / 0.55 from this contract and renders `gate` / `reason_code`.
  - `LOW_CONFIDENCE_THRESHOLD` changes from 0.9 to `auto_approve_at` (0.85), read from engine config rather than a frontend constant.
  - The S0-debt-6 calibration metric feeds the same corridor.

**Verification.**
- T1: `uv run ruff check` is clean, and `uv run pytest -q` gives 1197 passed / 42 skipped (baseline 1159 / 42). The 38 new tests are in `tests/test_disposition_calibration.py` (37) and `tests/test_policy.py` (1), and no skip was added.
- T2:
  - `uv run python -m bridge.calibration` exits 0 with the corridor above, and the defaults and all three policies are "inside, == defaults".
  - With `--auto-approve-at 0.95 --resubmit-below 0.55` it reports OUTSIDE and exits 1. With `--auto-approve-at 0.9 --resubmit-below 0.55` it exits 0.
  - `T(0.5,0.6)`, `T(nan,0.5)` and an inverted temporary skill policy all raise; `T(0.7,0.7)` is OK.
  - The corpus parity, extraction and benefits-conformance tests are green.
  - The gate table's six reason codes equal those `dispose` emits.

## 2026-09-30 — S0-frontend-1: RFP views — a consumer status API, a manual seq-keyed chase, and a leg-addressed carrier checklist over one board

**Context.** Aon's exec feedback: the Bridge must be visible (a) inside the consumer's own UI (Aon S0-frontend-12's status board, not an iframe) and (b) in our own apps for the `benefits-rfp` process (S0-arch-11). The RFP aggregate and `RfpBoard` already existed (S0-arch-11, S0-arch-13); nothing read them out except the dashboard SSE row.

**Decisions.**
1. **The consumer API is `RfpStatusView` verbatim plus two additive fields.** `GET /rfps/{id}` returns the status projection with `as_of`, plus `api_version: "1"` and per-carrier `reply_address` / `reply_subject_tag`. Stripping those three keys validates as `RfpStatusView` (`extra="forbid"`), and a test pins it. A consumer pins `api_version`. The SSE row keeps excluding `as_of`, so an idle board still emits no frames.
2. **Reply addressing is a small value object, `IntakeAddressing(mailbox, secret)`**, beside the correlation primitives. The hosts take it injected, or read it from `BRIDGE_EMAIL_MAILBOX` + `BRIDGE_EMAIL_TOKEN_SECRET` (a blank mailbox means none; a mailbox without a secret refuses).
3. **A manual chase is keyed by a per-leg sequence, not by the ladder step.** `Nudge.seq` (default `None`) makes the key `nudge:<leg ctx>:chase-<seq>` with `seq = leg.chases`. Keyed by step, a second chase on the same step would dedupe silently; keyed by seq, a second chase is a new send and a crash re-drive dedupes. Ladder nudges are unchanged.
4. **A chase is audit-only.** `record_chase` bumps `chases` / `last_chased_at` and appends a `chased` event. No cell status or `last_activity` moves: last activity is the carrier's, not the operator's. `update_requirements` now preserves existing leg objects, so chase state survives a requirements change.
5. **What is chaseable mirrors the ladder:** tracked, outstanding, non-declined items (`chaseable_items`). A declined line is the agent's to waive or replace. Nothing chaseable → 409.
6. **The carrier checklist is a separate model, not a new A2UI view kind.** `RfpChecklist` (`kind: "rfp_checklist"`) reuses `A2uiAction`, so the A2UI kinds, their frontend union and their tests stay untouched. It is addressed by the leg `context_id` and filtered to that carrier: addressing is the isolation (`design/zones.md`), with no new auth.
7. **Upload-into-a-row reuses the email-intake order.** Extract with the board untouched → gate → ledger entry → a fresh read → acknowledge on first response → `receive(product_line=…)` → put. A duplicate `doc_id` on the RFP is a 409 (a portal retry must not file a second version).
8. **Recommendation for Aon S0-frontend-16: keep this provider portal as the carrier surface**, themeable per deployment (`VITE_PORTAL_ACCENT`, `VITE_PORTAL_TITLE`). Retire Aon's `portal.html`.

**Deviations from the plan.**
- The chase route is `async def`, not a sync `def`. Sync routes run in the threadpool, which would race the board's read → put. The blocking sends go through `run_in_threadpool` first; the fresh read → record → put then runs on the loop with no await.
- A partial send failure records what already went, then answers 502. The plan did not specify this.
- A chase is recorded only when `leg.chases == sent.seq`, so two concurrent chases that dedupe onto one send count once.
- `build_rfp_demo_app` also takes `store` / `intake` / `max_ticks` (for T2 and tests). `rfp_demo` is deliberately not re-exported from `bridge.dashboard` (it imports the portal host).
- `bridge/portal/__init__.py` also exports the checklist names. This file was outside the plan's file list.
- `OpsExchange.rfp` is optional (`rfp?: RfpStatus | null`), so the existing `Queues.test.tsx` fixtures needed no edit. `provider-portal/src/domain/a2ui.ts` now exports `mapAction` / `WireAction` for reuse. The portal's `postJson` error now includes the host's detail text.

**Gotchas.**
- `bridge/dashboard/server.py`, `bridge/portal/server.py` and `design/frontend.md` carry unrelated uncommitted user hunks (the multi-origin CORS change for the dev scripts and the wikilink → md-link conversion). They were staged with `git hash-object` + `update-index` from HEAD + this task's hunks only.
- The demo host seeds an async upload from a sync factory, and `uvicorn --factory` calls the factory on its running loop. `_run` therefore runs `asyncio.run` on a worker thread.
- The repo is not `ruff format`-clean (pre-existing), so formatting was applied to new files only.

**Proposals (not applied: `spec/` is read-only during /implement; `~/aon` is out of repo).**
- `spec/cujs.md` CUJ-9 / CUJ-13: add the Driver line `tests/test_rfp_views.py` (consumer status API, chase, carrier checklist).
- `spec/demos/rfp-demo.md` still frames RFP as the Nimbus casefile (see the S0-arch-11 note). Reconcile it with the `benefits-rfp` matrix / chase / checklist views.
- Cross-repo, Aon:
  - S0-frontend-12 consumes `GET /rfps/{id}`, plus `POST /rfps/{id}/chase` for "send reminder". Mirror `frontend/shared/src/domain/rfp.ts`.
  - S0-frontend-16: retire `portal.html` and keep this provider portal, themed via env.

**Follow-ups.**
- A ladder-armed automatic chase for RFP legs (nudge on overdue / reminder through `build_nudge_notifier`).
- An outbound invitation send carrying the reply address and the portal link (`?leg=`).
- A durable RFP store shared across separately deployed portal and dashboard hosts. Today the board is in-memory, and only the local demo host shares one.
- Deployed reachability and auth for the internal-zone consumer API, plus the consumer's CORS origin.
- A real byte upload in the checklist (today it takes an eval-corpus artifact name).

**Verification.**
- T1:
  - `uv run ruff check` is clean.
  - `uv run pytest -q` gives 1226 passed / 42 skipped (baseline 1197 / 42). The 29 new tests are in `tests/test_rfp_views.py`, and no skip was added. The only existing test edited is `tests/test_outbound.py`'s exact Nudge field set (+ `seq`).
  - `pnpm -r test` is green (shared 14, agent-console 8, timewarp 5, ops-dashboard 12, provider-portal 12). `pnpm -r build` is green.
- T2: `/tmp/s0frontend1_t2.py` passes every check against `build_rfp_demo_app` with an injected `OutboundClient(FakeTransport())` + `IntakeAddressing`:
  - (1) the 6-cell matrix, `api_version` / `due_at` / `as_of`, and reply addresses equal to `intake_address(mailbox, mint_token(…))`;
  - (2) past due, the waiting cells and the lifecycle read overdue with no re-put, and at due + 4 days the row is in `/ops/queues/escalation`;
  - (3) one Nudge to carrier-c's leg naming only carrier-c with its reply address, `chases == 1`, `chase-1` on the second, 409 for a fully received carrier;
  - (4) carrier-b's checklist has no carrier-a / carrier-c substring, and a random hex leg is 404;
  - (5) an upload into carrier-a HI turns it received on `/ops/rfps` and in an `/ops/events` frame.
- Live: `uvicorn --factory bridge.dashboard.rfp_demo:build_rfp_demo_app` serves `/ops/rfps`, the carrier-c checklist (200) and a chase through the lazily built local `a2a_client` adapter. No browser was driven: no dev-browser tool is available in this session, so the views are covered by the vitest renders.

---

## 2026-10-01 — S3-edge-1: the hybrid edge — deterministic front door, the LlmAgent for intent; route table + LLM-outcome → wire mapping

**Context.** The `document_bridge` LlmAgent mediator is built + unit-tested but reachable by no serving surface — the agent-facing A2A edge deliberately serves the deterministic one-turn Collector (bridge/edge/executor.py, Decision 1). The S0-arch-1 deferral logged the consequence: off-script routing (F2), live real-erase deletion (ART-1, F3), and the request_human_review HITL zero-compute park (F4) are reachable through the LlmAgent's tools but have no agent-to-agent A2A entry point this cycle. Today: HITL park is proven via portal Path-B (CUJ-5) and the durable park/resume path; off-script + real-erase are unit-tested on the LlmAgent directly. Wiring the LlmAgent behind the edge was deferred with "not wired" logged only in Decision 1's docstring. This ADR supersedes that deferral and ratifies the hybrid edge design.

The Aon conformance suite (tests/conformance/test_benefits_conformance.py, wiki/evals/benefits/conformance.json record B) lands hitl_review/pending and asserts the task is COMPLETED; S3-edge-3 requires hybrid conformance byte-for-byte with 0 LLM calls, while S3-edge-6 requires a Collector hitl_review verdict to park via the LLM leg. Both can only hold if the HITL handoff is per-process opt-in (D5 below): Address-proof opts in (the Bridge owns review, timeline.json hitl_resolved); benefits-quote does not (Aon reviews in its own UI from field_reviews).

**Decisions.**

**D1 — Hybrid edge, ratified; supersedes the S0-arch-1 deferral and Decision 1's "not wired" clause.** Decision 1's posture survives: structured traffic never touches a model, and every turn — on either leg — is the same domain ExchangeTurn data part (never free text). Only the deferral is superseded. S0-arch-4's D3/D4 record is amended (D7/D8 below).

**D2 — Flag BRIDGE_EDGE_MODE=deterministic|hybrid.** Unset/blank → deterministic (today's behaviour byte-for-byte). Any other value → ValueError at build_a2a_app time (fail closed, mirroring bridge/seams/config.py::resolve_mode). Read once at app build, not per request; an explicit constructor/app argument overrides the env (test injection). It is a routing mode, not a seam (no local/gcp adapter pair, no @pytest.mark.seam). In deterministic mode the LLM leg is never constructed. S3-edge-3 adds it to .env.example.

**D3 — Route table (hybrid mode; first match wins).** Deterministic mode = R1 + R2 only (R2 also absorbs text-only, as today).

| # | Match (message shape + task state) | Leg | Wire outcome |
|---|---|---|
| G | Message on an existing task in INPUT_REQUIRED whose LLM session holds an unanswered request_human_review call | guard | a HumanReviewResolution part → R6; anything else is refused with InvalidRequestError (awaiting_human_review), task unchanged, no Runner built (zero compute) |
| R1 | A data part validating as ExtractionResult (classify_path → Path.A) | patha validate-only | unchanged: terminal turn, COMPLETED; a Path-A hitl_review stays pending-terminal — never hands off (model-free in both modes) |
| R2 | A CollectRequest data part and/or ≥1 by-reference file part (incl. text + files) | Collector | unchanged: SUBMITTED → COMPLETED; LLM invoked 0 times |
| R3 | R2's turn has ≥1 entry with gate=hitl_review and the process skill declares bridge-hitl: park (D5) | Collector → LLM handoff | after persist + URI rewrite, the turn is handed to the LLM leg, which calls request_human_review per hitl entry → INPUT_REQUIRED, terminal=False (mapping D6) |
| R4 | Text-only (no CollectRequest data part, no file part, no resolution part) — on a new task, or on an INPUT_REQUIRED task with no pending review (an escalated task) | LLM leg | per wire mapping D6 (deletion / off-script reject / escalate / unrouted) |
| R5 | A HumanReviewResolution part on a task with no pending review | — | InvalidRequestError (no_pending_review) |
| R6 | A HumanReviewResolution whose call_id matches the pending call, from an authorized reviewer principal | LLM leg resume | fresh Runner, id-matched FunctionResponse (no HTTP re-drive) → WORKING → COMPLETED with the resolved entry, or INPUT_REQUIRED while other reviews on the task are still pending. call_id mismatch → InvalidParamsError (review_call_id_mismatch, the TODO's "409"); non-reviewer principal → InvalidRequestError (reviewer_not_authorized); task unchanged in both refusals |
| — | Neither JSON intake nor text (e.g. an unrelated data part only) | — | unchanged (today's ValueError from decode_intake) |

Notes:
- G precedes R1 (deviation from the TODO line's order, deliberate): a task parked for a reviewer is locked to its resolution. Otherwise a resubmission routed to the Collector would complete the task and orphan the pending LRFT call in the ADK session, so the reviewer's resolution could never land. The party resubmits on a new task under the same context_id. G is a cheap session read (no Runner, no model), evaluated only when context.current_task is INPUT_REQUIRED — which on the hybrid edge only the LLM leg produces.
- HumanReviewResolution is shape-distinct (extra="forbid"; no process, no fields), so it can never match R1/R2; detect it before R4 (resolution + text → resolution wins).
- The hitl_review verdict route in the TODO is an outcome handoff after R2, not an inbound-shape classification.
- Router home: extend bridge/edge/pathroute.py with a Route enum (GUARD_REFUSE, PATH_A, COLLECT, LLM, RESUME_REVIEW, NO_PENDING_REVIEW) and a pure classify_route(message, *, mode, task_state, has_pending_review) -> Route (S3-edge-3); the R3 handoff is decided in CollectExecutor.execute after the Collector returns.

**D4 — Ledger from tool function responses only.** project_turn_from_events(events) (S3-edge-2) reads only function_response parts of the tools run_disposition_gate, request_human_review, handle_deletion_request, reject_submission, escalate_submission, plus the executor-fed resolution response. It ignores: model text parts (never relayed — not even as status.message), function calls without a response, and the extraction_agent AgentTool response (signals only, recorded to session state for S3-edge-4's arg pinning; never mints an entry). accepted can come only from a run_disposition_gate auto_approve response (args pinned by S3-edge-4's before_tool_callback) or from a reviewer resolution. A resolution FunctionResponse is trusted only if it is id-matched to a pending request_human_review call and was authored by the executor (user role, built from a HumanReviewResolution), never by the model.

**D5 — HITL handoff is per-process opt-in: skill frontmatter bridge-hitl: park | report** (default report). report = today: the Collector's pending/hitl_review entry rides a terminal turn and the consumer owns review (Aon). park = R3: the Bridge owns review and parks on the native LRFT. skills/address-proof/SKILL.md gets bridge-hitl: park and SkillMeta gains hitl: str = "report" — in S3-edge-6, not here. "Config not code" idiom, same family as bridge-satisfaction/bridge-tags. Effect: Aon conformance record B stays COMPLETED with 0 LLM calls in hybrid mode; the 2 Address hitl_review records park and reach the timeline.json hitl_resolved (S3-edge-7).

**D6 — LLM-outcome → wire mapping.** status.message text is always Bridge-authored (a tool's message or summarize_turn), never model text. reason_code / review_call_id ride the A2A status.message.metadata (envelope extension space — not a domain-contract change).

| Function response | LedgerEntry | terminal → task state | lifecycle | status.message text | status.message.metadata |
|---|---|---|---|---|---|
| run_disposition_gate (pinned) | entry projected from the verdict, as the Collector does | auto_approve/resubmit/unsupported → terminal; hitl_review routes to request_human_review | on_track | summarize_turn | — |
| request_human_review (LRFT, pending) | the Collector's hitl entry with disposition=pending, gate=hitl_review, reason_code=awaiting_human_review, message=tool message | False → INPUT_REQUIRED | on_track | tool message | reason_code=awaiting_human_review, review_call_id=<LRFT call id> |
| executor-fed resolution (id-matched) | the Collector's original hitl entry transformed exactly as bridge/pathb.py::resolve_hitl does (disposition=resolution, message=reviewer message, gate reason_code kept) | True → COMPLETED unless other reviews pending | on_track | reviewer message | reason_code=entry's reason_code |
| handle_deletion_request | none (erasure is not a disposition) | True → COMPLETED | on_track | tool message | reason_code=deletion_completed OR nothing_to_delete |
| reject_submission | none — never a disposition=rejected entry (that would be a model-minted compliance reject) | True → COMPLETED | on_track | tool message | reason_code=off_script_rejected |
| escalate_submission | none | False → INPUT_REQUIRED, survives tasks/get; only tasks/cancel terminates (DUR-6) | escalated | tool message | reason_code=escalated_for_review |
| no tool response (model text only) | none | True → COMPLETED | on_track | fixed Bridge text (e.g. "No action was taken on this message.") | reason_code=off_script_unrouted (new REASON_UNROUTED in bridge/offscript.py, S3-edge-5) |

Combination rules: entries accumulate per invocation keyed by doc_id (last write wins); terminal=False if any pending review or escalate; status text/reason_code precedence: pending review > escalated > the last of {deleted, rejected} > unrouted. The turn's context_id = the A2A context_id; status = project_collection_status(entries); for R3 the non-hitl entries of the same submission are kept as the Collector produced them. The LLM-leg escalate is non-terminal, unlike Path-B's cap→escalate (PathBStep.terminal=True, portal) — which is unchanged.

**D7 — D3 is now leg-specific.** Deterministic legs (R1/R2) keep SUBMITTED → COMPLETED (rationale unchanged: no genuine work phase). The LLM leg emits SUBMITTED → WORKING → COMPLETED | INPUT_REQUIRED (resume: WORKING → …) — the model call is genuine in-connection work, so WORKING is honest there. Every status update keeps a non-empty status.message.

**D8 — D4: the completing path is NOT collapsed.** On the LLM leg D4 is honoured in full: a HITL park or escalate is one durable task that parks and resumes on the same task_id/context_id, the ADK session durable via the sessions seam and ResumabilityConfig(is_resumable=True) on the edge App. On the deterministic completing path a round genuinely completes, so a new task per round under the same exchange context_id remains correct (the context_id is the aggregate). This replaces S0-arch-4's "collapsing … is part of the follow-up" — decided: not collapsed.

**D9 — Session key == artifact key.** LLM-leg ADK session = (app_name="document_bridge", user_id=<party>, session_id=<A2A context_id>), where party = the inbound CollectRequest.party when present, else the context's owning party resolved from the shared artifact store the way the fetch endpoint does (_candidate_users), else "_unscoped". So handle_deletion_request erases exactly the artifacts the Collector saved for that context, and a caller cannot reach another context's artifacts (addressing = isolation, design/zones.md). For R3 the executor seeds session state with the Collector's verdict + extraction signals per doc_id before running the LLM (S3-edge-4 pins gate args from it) and sends a Bridge-authored user message naming the hitl entries.

**D10 — Contract delta: only an additive contract.HumanReviewResolution** (lands in S3-edge-6). ExchangeTurn, CollectionStatus, LedgerEntry, CollectRequest unchanged. Shape (extra="forbid", decoded via decode_wire, tagged metadata.kind="human_review_resolution" → KIND_HUMAN_REVIEW_RESOLUTION in bridge/edge/wire.py):
```python
class HumanReviewResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = CONTRACT_VERSION
    call_id: str                                   # = status.message.metadata.review_call_id
    resolution: Literal["accepted", "rejected"]    # mirrors HitlBody / ResolveBody
    message: str
    reviewer: str | None = None                    # audit label only — never used for authz
```
CONTRACT_VERSION "1.3" → "1.4" (a new additive type) in S3-edge-6. Reason codes stay free strings (no enum change). Reviewer authorization comes from the A2A call-context principal (RequestContext.call_context.user; Gateway/Agent Identity on deploy), never from the payload; S3-edge-6 picks the principal source/config.

**Route → owning test.**

| Route | Owning test(s) |
|---|---|
| Flag parse (D2): default / hybrid / invalid → ValueError | `tests/test_edge_a2a.py` (both-mode parametrization + flag test, S3-edge-3) |
| Deterministic mode (all routes) | the full existing suite unchanged; `tests/test_edge_a2a.py` + `tests/address/test_golden.py` deterministic param (S3-edge-3) |
| G guard refuse | `tests/test_edge_hitl_a2a.py` (S3-edge-6) |
| R1 Path-A | existing `tests/test_pathroute.py`, `tests/test_patha.py`::test_edge_path_a_branch_skips_extraction / ::test_edge_path_a_malformed_assertion_rejected; hybrid param in `tests/test_edge_a2a.py` (S3-edge-3) |
| R2 Collector, 0 LLM calls | existing `tests/test_edge_a2a.py`::test_message_send_happy_path_accepted, ::test_end_to_end_text_intake, `tests/conformance/test_benefits_conformance.py`, `tests/address/test_golden.py`; hybrid-mode + ScriptedLlm call-count 0 (S3-edge-3) |
| R3 HITL handoff (park) | `tests/test_edge_hitl_a2a.py` (S3-edge-6); negative (skill report → no handoff): conformance record B stays COMPLETED in hybrid (S3-edge-3/6) |
| R4 LLM leg off-script | `tests/test_edge_offscript_a2a.py` (S3-edge-5); router unit tests in `tests/test_pathroute.py` (S3-edge-3) |
| R5 no pending review | `tests/test_edge_hitl_a2a.py` (S3-edge-6) |
| R6 resume / mismatch / non-reviewer | `tests/test_edge_hitl_a2a.py` (S3-edge-6); decode in `tests/test_contract.py` (S3-edge-6) |
| D4 projection (tool responses only) | `tests/test_llm_leg.py` (S3-edge-2); adversaries in `tests/test_edge_gate_inversion.py` (S3-edge-4) |
| D6 mapping rows: deletion/reject/escalate/unrouted | `tests/test_edge_offscript_a2a.py` (S3-edge-5) |
| D6 rows: pending review / resolution | `tests/test_edge_hitl_a2a.py` (S3-edge-6) |
| D9 session key == artifact key | `tests/test_llm_leg.py` (S3-edge-2) |
| Corpus parity, both modes | `tests/address/test_golden_llm_edge.py` (S3-edge-7) |

**Supersedes.**
- S0-arch-1's deferral ("Deferred to later cycles" section in TODO.md — the LlmAgent-behind-the-edge line is promoted to Sprint 3).
- Decision 1's "not wired" clause in bridge/edge/executor.py — the docstring is updated by S3-edge-3.
- S0-arch-4's D4 "follow-up" line — D8 above decides the completing path stays one task per round (not collapsed).

**Gotchas.**
- G precedes R1 (deviation from TODO line's order, deliberate): a task parked for a reviewer is locked to its resolution.
- Path-A never hands off to the LLM leg (model-free in both modes).
- 409 = A2A error class (InvalidParamsError for review_call_id_mismatch, since A2A JSON-RPC has no HTTP 409).
- LLM text never on the wire — status.message is always Bridge-authored (a tool's message or summarize_turn).
- Design docs written status-free/present-tense ahead of code per the design-SoT rule.

**Follow-ups for later tasks (S3-edge-2..8).**
- S3-edge-2: LlmAgent edge runtime + model-free ScriptedLlm double.
- S3-edge-3: Executor router behind BRIDGE_EDGE_MODE, add to .env.example.
- S3-edge-4: Gate inversion preserved at the edge (before_tool_callback pins args).
- S3-edge-5: Off-script routing + real-erase deletion (ART-1) over A2A.
- S3-edge-6: HITL request_human_review park/resume agent-to-agent; contract.HumanReviewResolution (CONTRACT_VERSION 1.3 → 1.4); SkillMeta.hitl key; skills/address-proof/SKILL.md bridge-hitl: park.
- S3-edge-7: CUJ golden parity: hybrid vs deterministic on the Address corpus.
- S3-edge-8: Spec proposal + close-out.

**Verification.**
- T1: uv run ruff check clean; uv run pytest -q → 1226 passed / 42 skipped (no code changed — baseline unchanged).
- T2 (from plan §4):
  1. grep -c BRIDGE_EDGE_MODE DECISIONS.md design/a2a-edge.md → both ≥1; grep -n "^## " DECISIONS.md | tail -1 shows the new S3-edge-1 entry as the last heading.
  2. grep -nE "this cycle|Deferred to later|TODO\.md|deferred|follow-up|S3-edge" design/a2a-edge.md → no output. grep -nE "S3-edge|TODO\.md" design/adk.md design/long-running.md design/a2a-consumer.md → no output.
  3. grep -n "PENDING. item" design/a2a-edge.md design/a2a-consumer.md → no output. The _status_for description is corrected to say it keys off terminal.
  4. The python check in the plan's §4.4 prints OK [...]. It confirms routes G and R1–R6, every owning-test path exists or appears in TODO.md, and the entry names the mapped reason codes.
  5. uv run python -c "import bridge.offscript as o; print(o.REASON_DELETED, o.REASON_NOTHING_TO_DELETE, o.REASON_OFF_SCRIPT_REJECT, o.REASON_ESCALATED, o.REASON_HUMAN_REVIEW)" → the five reason codes, used verbatim in DECISIONS.md and design/a2a-edge.md.
  6. After commit, git diff --name-only HEAD~1 lists only DECISIONS.md, TODO.md, design/a2a-edge.md, design/adk.md, design/long-running.md, design/a2a-consumer.md.

## 2026-10-01 — S3-edge-2: the LLM-leg runtime (`bridge/edge/llm_leg.py`) + the model-free `ScriptedLlm` double

**Context.** S3-edge-1 ratified the hybrid edge (D1–D10). This task builds the LLM leg's runtime standalone — no routing, no executor wiring (S3-edge-3), no gate-arg pinning (S3-edge-4), no reason-code metadata (S3-edge-5), no resolution/resume (S3-edge-6).

**Decisions.**
- **`LlmLeg` runtime.** `LlmLeg(model=, app=, session_service=, artifact_store=)`: every collaborator is an `or`-gated injectable — App from `build_app(model)`, sessions from the **sessions seam** (`get_adapter(Seam.sessions, resolve_mode(...)).build_session_service()`), store from `build_artifact_store()`; `bind_store()` mirrors `CollectExecutor.bind_store`. No `InMemory*` construction in the module (P7-8 guard green). `runner()` returns a **fresh `Runner` per call** — never cached (a resume runs on a fresh Runner over the durable session). `run(party=, context_id=, message=)` → `LlmLegResult(key, events, projection)`. `llm_leg` is not exported from `bridge/edge/__init__.py` (S3-edge-3 decides).
- **D9 keys.** `SessionKey(app_name, user_id, session_id)` = `("document_bridge", party, context_id)`. `resolve_party`: explicit party wins; else exactly one owning party in the shared store (new public `bridge.edge.artifacts.owning_parties`, built on `_candidate_users`); zero or >1 → `_unscoped` (fail closed — never guess among parties). Empty `context_id` → `ValueError`. `APP_NAME` lockstep (`build_app().name == artifacts.APP_NAME == llm_leg.APP_NAME`) is enforced by test, not by an import (`bridge/agent.py` must not import `bridge.edge`).
- **D4/D6 projection.** `project_from_events(events, *, context_id, seed_entries=()) -> LlmProjection(turn, reason_code, status_text, review_call_ids)`; `project_turn_from_events(...)` returns `.turn`. `context_id` is a required kwarg (deviation from the TODO's `project_turn_from_events(events)`: an ADK `Event` carries no session id and `ExchangeTurn.context_id` is required). Reads only `function_call` (to pair args) and `function_response` parts — never `text`/`thought`. Skips partial/empty events, user-authored function responses, and non-projected tools (`extraction_agent` included). A malformed gate response is skipped, never guessed. `reject_submission` never mints a `rejected` entry. Precedence: review (4) > escalate (3) > last of {delete, reject} (2); gate-only → `reason_code=None` (caller uses `summarize_turn`).
- **Gate `doc_id` source is interim.** The gate entry's `doc_id` is the preceding `extraction_agent` call's `request` arg, else the gate's `doctype` arg, else `"document"`; seed entries with that `doc_id` are overlaid. S3-edge-4 replaces this with session-pinned extraction signals.
- **`build_document_bridge` / `build_extraction_agent` accept `str | BaseLlm | None`** via `bridge.extraction.agent.resolve_model` (`None`/`""` → `BRIDGE_MODEL` env → default; a `BaseLlm` is never re-routed). The same instance is shared by root + extraction sub-agent. `build_app(model, *, is_resumable=True)` carries `ResumabilityConfig`, imported from the registered FQN `google.adk.apps.app`.
- **`tests/_scripted_llm.py`.** `ScriptedLlm(BaseLlm)` with `on_text` (case-insensitive substring of the latest user text) / `on_tool` (name of the latest function response) scripts, a deliberately false `after_tool_text` ("Done — the document was accepted."), a `max_calls` loop guard, recorded `requests` + `call_count`, and validation that every scripted call names a tool in `llm_request.tools_dict`.

**Gotchas.**
- **G1 — ADK LRFT pause vs D4 (D4 clarification, for S3-edge-6).** Reproduced with the ScriptedLlm (`test_llm_leg_review_end_to_end_parks_on_tool_response`: 2 model calls). In ADK 2.7.1 a `LongRunningFunctionTool` returning a **non-None** value (today's `request_human_review` returns a dict) does NOT pause a resumable invocation: ADK emits the function_response and calls the model again. Only a `None` return pauses (call event carries `long_running_tool_ids`, no response, no further model call — the real zero-compute park). D4's "function calls without a response are ignored" would make that park invisible, so the projection treats a `request_human_review` call whose id is in its event's `long_running_tool_ids` and that has no response as pending (Bridge-authored text "This submission was sent for human review.") — a framework-flagged call, not model text. `request_human_review`'s return is unchanged here (`tests/test_offscript.py` asserts the dict); S3-edge-6 decides (likely return `None` so the invocation truly ends).
- **G2** — import `ResumabilityConfig` from `google.adk.apps.app`; other re-export paths bypass the TEST-4 unregistered-use catalog.
- **G3** — a shared `BaseLlm` sees both agents' requests; `tools_dict` tells them apart (root: 6 tools incl. `extraction_agent`; sub-agent: `extract_document`). `call_count` counts both.
- **G4** — the double's `model` must not look like a gemini name (BaseLlm name-based capability fallback → FutureWarning); `capabilities` is overridden outright.
- **G5** — `LlmLeg()` with the default model string constructs without network; only `run()` calls the model, so every test that runs a turn injects a `ScriptedLlm`.
- **G6** — resumable runs emit an empty trailing event (no parts); the projection tolerates `content is None` / empty `parts`.
- Constructing `ResumabilityConfig` emits ADK's experimental `UserWarning` (no `filterwarnings=error` configured).

**Verification.**
- T1: `uv run ruff check` clean; `uv run pytest -q` → 1259 passed / 42 skipped (baseline 1226 / 42; +31 in `tests/test_llm_leg.py`, +2 in `tests/test_agent.py`; skips unchanged).
- T2:
  1. Model-free gate script (ScriptedLlm replies "Accepted! approved." + a flagged 0.6 gate call; sockets disabled, no `GOOGLE_API_KEY`) → `1 pending hitl_review True 2`.
  2. Real-erase script: `ctx-1: SessionKey(app_name='document_bridge', user_id='jordan-lee', session_id='ctx-1') True deletion_completed []`; `ctx-2: _unscoped nothing_to_delete ctx-1 keys: ['bill.pdf']`; `two owners: _unscoped`.
  3. `build_app()` → `document_bridge True`; `LlmLeg().runner()` resumable, two `runner()` calls distinct.
  4. `tests/test_adk_experimental.py` + `tests/test_service_wiring_guard.py` → 6 passed.
  5. `grep -rnE "^\s*(from|import) google\.adk\.a2a" bridge/` and `grep -n InMemory bridge/edge/llm_leg.py` → no output.
  6. `BRIDGE_SESSION_DB_URL=sqlite+aiosqlite:////tmp/s3e2.db … type(LlmLeg().session_service).__name__` → `DatabaseSessionService`.

## 2026-10-01 — S3-edge-3: the executor router behind `BRIDGE_EDGE_MODE` — deterministic by default, text-only intents to the LLM leg in hybrid

**Context.** S3-edge-1 ratified the hybrid edge (D1–D10) and S3-edge-2 built the LLM-leg runtime standalone. This task wires it: the flag, the route table, the executor dispatch and `build_a2a_app`. No change to `bridge/edge/llm_leg.py`, `bridge/edge/mock.py`, `contract/`, `spec/`, `design/` or `wiki/evals/`.

**Decisions.**
- **Flag (D2) — `bridge/edge/pathroute.py::resolve_edge_mode(value=None) -> EdgeMode`.** An explicit `EdgeMode`/non-blank string wins; `None`/blank falls through to `BRIDGE_EDGE_MODE`; unset/blank → `deterministic`. Exact, case-sensitive match; anything else → `ValueError` naming `BRIDGE_EDGE_MODE` (mirrors `bridge/seams/config.py::resolve_mode`). Not a seam. Resolved ONCE in `build_a2a_app`, so a bad value fails app start (`serve()`/`serve_container()` read the env through it).
- **Route table (D3) — pure `classify_route(message, *, mode, task_state, has_pending_review) -> Route`** (`GUARD_REFUSE`, `PATH_A`, `COLLECT`, `LLM`, `RESUME_REVIEW`, `NO_PENDING_REVIEW`). Deterministic = `PATH_A` iff `classify_path` says Path A, else `COLLECT` (text-only included), so it matches the old `execute` exactly. Hybrid, first match wins: parked (INPUT_REQUIRED + pending review) → `RESUME_REVIEW` if a resolution part, else `GUARD_REFUSE`; resolution part → `NO_PENDING_REVIEW`; assertion → `PATH_A`; CollectRequest data part or any `url`/`raw` file part → `COLLECT`; non-empty text → `LLM`; else `COLLECT` (today's `decode_intake` refusal). A resolution part is detected by the part metadata tag `kind == KIND_HUMAN_REVIEW_RESOLUTION` (the one constant added to `bridge/edge/wire.py`; the type and decoder land in S3-edge-6).
- **Executor.** `CollectExecutor.execute` dispatches on the route. The Path-A and Collector bodies are unchanged (the Collector body moved verbatim into `_execute_collect`; `_emit_turn`'s event sequence is untouched). `bind_edge(mode, *, llm_leg=None)` mirrors `bind_store` and is called only by `build_a2a_app` (no constructor params, so there is one source of truth). Hybrid without a leg → `ValueError`; deterministic drops any leg. Read-only `mode`/`llm_leg` properties. A bare executor is deterministic. `LlmLeg` is imported only under `TYPE_CHECKING` in executor.py and lazily in server.py, so `import bridge.edge` stays light and deterministic mode never imports or constructs the leg.
- **R4 `_execute_llm_leg` (D6/D7/D9).** Task(SUBMITTED) if new → WORKING (`LLM_WORKING_TEXT`) → `LlmLeg.run(party=None, context_id=<A2A context_id>, message=<joined text>)` → exchange_turn artifact (`last_chunk=True`) → COMPLETED or INPUT_REQUIRED via `_status_for`. Status text, in order: the projection's tool message, then `summarize_turn` (when entries exist), then `LLM_UNROUTED_TEXT`. Model text is never used. A leg exception → `logger.exception` + `updater.failed(LLM_FAILED_TEXT)` with no artifact, so the task never hangs in WORKING. No `reason_code` metadata yet (S3-edge-5).
- **R5 is final; G/R6 are unreachable by design.** `NO_PENDING_REVIEW` raises `InvalidRequestError("no_pending_review: …")`, which reaches the a2a client as `a2a.utils.errors.InvalidRequestError`. `_has_pending_review` is an `async` stub returning `False` and is only called in hybrid mode. So `GUARD_REFUSE` (`InvalidRequestError("awaiting_human_review: …")`) and `RESUME_REVIEW` (`UnsupportedOperationError`) are unit-tested in the classifier only, until S3-edge-6 lands the session read and resume.
- **`build_a2a_app(..., edge_mode=None, llm_leg=None)`.** Hybrid: use the injected leg or construct `LlmLeg(artifact_store=store)`, then always `llm_leg.bind_store(store)` (writer == reader == eraser), then `executor.bind_edge`. Deterministic: an injected leg is ignored. The leg's sessions come from the sessions seam; there is no new `InMemory*` (the P7-8 guard stays green).
- **Exports.** `bridge.edge` exports `EdgeMode`, `Route`, `classify_route`, `resolve_edge_mode`, but not `LlmLeg`. `.env.example` documents `BRIDGE_EDGE_MODE`.
- **Test shape (not a seam, so no `adapter` fixture).** Each area has a local both-mode fixture: `tests/test_edge_a2a.py::edge`, `tests/address/test_golden.py::real_app`, and `tests/conformance/conftest.py::edge_mode`. The last sets `journey.EDGE`, which `build_app` reads at call time; `corpus_server` depends on it, so every journey runs in both modes. Each hybrid param uses `ScriptedLlm(max_calls=0)` and asserts `call_count == 0` at teardown. `test_cuj1_path_a_instant_parity_mock_and_real` keeps its node id (it is the CUJ-1 Driver in `spec/cujs.md`) and is parametrized over `mock` / `real-deterministic` / `real-hybrid` with one shared `_assert_cuj1` body; it builds the real edge through the `_real_edge_app(mode, llm)` helper (which `real_app` also uses) and asserts `call_count == 0` inline, because a parametrized fixture cannot be pulled with `getfixturevalue` from a test not parametrized over it. `send_by_reference` gained `context_id=None`.

**Gotchas.**
- **G1 — deterministic byte-parity hinges on the classifier.** In deterministic mode `classify_route` returns exactly what `classify_path` implied and never consults state, pending review or the leg. Pinned by a test that compares against `classify_path` across task states.
- **G2 — `raw` parts route to COLLECT in hybrid,** so inline bytes keep today's `decode_submissions` rejection. Otherwise a text+raw message would go silently to the model.
- **G3 — "0 LLM calls" fails loud.** `ScriptedLlm(max_calls=0)` raises on the first call, so the task FAILs and the COMPLETED assertion breaks; teardown also asserts `call_count == 0`. Mutation-checked: routing text/CollectRequest messages to `LLM` in hybrid turned 45 tests red and errored 40 teardowns across conformance, test_edge_a2a and golden.
- **G4 — LLM-leg exceptions → FAILED, not a hung WORKING task.**
- **G5 — non-construction is proven by patching `bridge.edge.llm_leg.LlmLeg.__init__`,** not a server-module name, because of the lazy import.
- **G6 — A2A `context_id` vs `ExchangeTurn.context_id`.** The Collector still mints its own turn context_id when the CollectRequest has none. The LLM leg keys session and turn on the A2A `context.context_id` (D9). The byte-parity test pins `context_id="conformance-parity"` so the scoped artifact URIs, which embed it, match.
- **G7 —** a streamed INPUT_REQUIRED park logs a2a-sdk's `Dispatcher task is not running. Cannot wait for event dispatch.` warning. This is pre-existing SDK behaviour: the deterministic stub-collector park logs it too. It is benign.
- `ResumabilityConfig` emits ADK's experimental `UserWarning` on every hybrid app build. Expected.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q` → **1379 passed / 45 skipped**, against a baseline of 1259 / 42. The +120 passed are +58 `tests/test_pathroute.py`, +25 `tests/test_edge_a2a.py`, +4 `tests/address/test_golden.py` and +33 `tests/conformance/`. The +3 skips are only the hybrid copies of tests already gated on env: `test_live_gemini_conformance` (`BRIDGE_TEST_GEMINI`) and `test_real_consumer_accepts_mapped_quote` ×2 (`BRIDGE_TEST_CONSUMER_REPO`). They are journeys on `corpus_server`, so they gain the `edge_mode` param, and none is a convenience skip. `tests/test_service_wiring_guard.py`, `tests/test_isolation_guard.py` and `tests/test_adk_experimental.py` → 10 passed.
- T2: `pytest tests/conformance -k "hybrid or byte_identical"` → 33 passed / 3 skipped (the gated copies), with `test_hybrid_exchange_turn_byte_identical_to_deterministic` 8/8. `pytest tests/test_edge_a2a.py tests/address/test_golden.py -k hybrid -v | grep -c PASSED` → 25; `-k deterministic` → 18. `tests/test_pathroute.py` → 65 passed. `/tmp/s3e3_t2.py` printed `structured: ['TASK_STATE_COMPLETED'] TASK_STATE_COMPLETED llm_calls 0` / `text-only: ['TASK_STATE_WORKING', 'TASK_STATE_INPUT_REQUIRED'] TASK_STATE_INPUT_REQUIRED False escalated 'This submission was escalated for human review: asked for a person' llm_calls>0 True` / `bogus: ValueError True` / `default: deterministic True`. Guard greps (google.adk.a2a in bridge/, new InMemory in server/executor, deferral language) → no output. `.env.example` → one `BRIDGE_EDGE_MODE` line.

## 2026-10-01 — S3-edge-4: the gate inversion at the edge — pinned gate inputs, recorded signals, adversaries fail

**Context.** `run_disposition_gate` takes every input as a model-supplied argument, so a model sending `overall_confidence=1.0, flagged_fields=[]` got `auto_approve` on a hitl/resubmit record, and adding `passport` to `candidate_doctypes` turned `passport-unsupported` into an accept. The `extraction_agent` AgentTool's response is the sub-agent's final model text (ADK 2.7.1 `AgentTool.run_async` returns `merged_text`), so it is forgeable too. The S3-edge-2 projection took the gate entry's doc_id/doctype from model call args (marked interim). This task holds CORE-2 at the agent. No change to `bridge/disposition.py`, `contract/`, `spec/` or `wiki/evals/`.

**Decisions.**
1. **Pin all five gate args, including the label space** (`bridge/gatepin.py::pin_gate_args`, the root `document_bridge`'s `before_tool_callback`, always on — the invariant belongs to the agent, not only the edge). ADK passes one deep-copied `args` dict to the callback and then to the tool, so the pin overwrites it in place (`clear()` + exactly the five gate params). The source is session state only — never call args or AgentTool text. It is a no-op for every other root tool.
2. **Signals are recorded inside `extract_document`** (`tool_context: ToolContext | None = None`, hidden from the declaration; the direct call keeps working) via `gatepin.record_extraction(state, doc_id=artifact_name, result=...)`. The record goes through `signals_from_result`, so the flagged-field fallback and `canonicalize_issuer` match the Collector byte for byte. AgentTool forwards the sub-run's `state_delta` into the parent session, which is how the record reaches `document_bridge`. Keys are session-scoped and persisted: `bridge:gate_signals` (`{doc_id: RecordedSignals}`, insertion-ordered), `bridge:label_space`, `bridge:gate_pins` — never `temp:` (S3-edge-6 resumes later), `app:`/`user:` or `_adk*` (AgentTool filters that prefix from the sub-run state copy). Every write assigns a new top-level value so the delta is tracked.
3. **Selection = the most recently recorded un-gated record, consumed once** (`gated_by=<call id>`); re-recording a doc_id moves it to the end un-gated, so a resubmission is gateable again. Refusal = `{"refused": True, "reason_code": "gate_signals_missing" | "label_space_missing", "message": <Bridge text telling the model to call extraction_agent first>}` — the tool is skipped and the dict never validates as a `DispositionVerdict` (pinned by test). A call with no `function_call_id` is refused (nothing to pair). An explicit empty label space is legal (tested with `is None`).
4. **D4 clarification — the projection reads the Bridge's pin, not model content.** The pin (`{call_id: {"doc_id", "signals"}}`) is written to `tool_context.state`, so it rides the gate response event's `actions.state_delta` (ADK builds that event with `actions=tool_context.actions`). `project_from_events` accumulates pins (read before the content guard, so a content-less event still contributes; never from a user-authored event) and mints a gate entry only when the response's call id is pinned, the response validates as a verdict, and `dispose_signals(pinned).model_dump(mode="json") == response`; `accepted` with a gate other than `auto_approve` is skipped as defense in depth. doc_id/doctype/issuer come from the pin. `last_extraction_request` and the gate-args doctype source are deleted. The reviewer-resolution half of "accepted only from auto_approve or a reviewer resolution" is unchanged (user-authored responses are skipped); S3-edge-6 adds the id-matched resolution.
5. **Label space is Bridge-decided.** R4 (`CollectExecutor._execute_llm_leg`) passes `label_space=self._llm_label_space(text)`: the deterministic `parse_text_request` resolver + `candidate_doctypes_for`; no match → `None` → every gate call refused. An R3 Collector seed carries its request's label space on the record. `LlmLeg.run(..., label_space=None, seed_signals=(), seed_entries=())` is the seeding API S3-edge-6 calls: `gatepin.seed_state_delta(session.state, ...)` (pure) rides `Runner.run_async(state_delta=...)`, and `seed_entries` go to the projection.
6. **Reading of "no accepted entry / verdict == expected_gate".** "No accepted entry" = no accept the adversary minted: none at all on the 4 non-auto_approve records under any adversary; on the 4 auto_approve records an accepted entry exists only from the genuine pinned gate (adversary b — the positive control proving the pin is not a blanket refusal) or the Collector seed. "verdict == expected_gate" = the entry's `(gate, disposition)` when an entry exists; otherwise `dispose_signals` over the genuine session-recorded record (with `gated_by is None` — the gate never ran).

**Gotchas.**
- **`run_async(state_delta=)` works as needed in 2.7.1:** the delta rides the new user event and is visible to `tool_context.state` in the same invocation (the seeded adversary b mints its entry on the first move). No `append_event` fallback was needed.
- **Parallel gate calls in one model turn fail closed.** Observed: the first call is pinned and the second is refused `gate_signals_missing` (the record is already consumed); one entry, on the genuine verdict. The test asserts ≤ 1 entry with the genuine verdict, so it does not depend on ADK's scheduling.
- A gate call in the same model turn as the extraction call is refused: the signals are not recorded yet.
- A stale un-gated record from an earlier invocation in the same context can still be pinned later. It holds genuine signals for its own doc_id, so this is not a forgery.
- The model chooses the artifact name it extracts; a genuine extraction of a different real artifact is not a forgery. Artifact-to-party scoping of extraction is out of scope here.
- `tests/_scripted_llm.py` needed no change.

**Verification.**
- T1: `uv run ruff check` clean; `uv run pytest -q` → **1514 passed / 45 skipped** (baseline 1379 / 45; +133 `tests/test_edge_gate_inversion.py`, +2 `tests/test_agent.py`; `tests/test_llm_leg.py` gate tests rewritten, count unchanged; skips unchanged). Guard tests (isolation, service wiring, ADK experimental) → 10 passed. `python -c "import bridge.extraction.agent, bridge.agent, bridge.edge, bridge.gatepin"` → OK.
- T2:
  1. `pytest tests/test_edge_gate_inversion.py -v` → 133 passed in ~9 s. `--collect-only | grep -c "\["` → 112; the matrix alone is 96 (8 records × {a_text_claim, b_forged_gate, c_reject, c_escalate} × {in_process_seeded, in_process_extracted, a2a_hybrid}), 12 per record id.
  2. Mutation checks: (i) `pin_gate_args` returning `None` after the tool-name check → all 24 `b_forged_gate` cases fail. (ii) pin kept but args not overwritten + projection consistency check removed (the pre-task trust model) → 12 failures, exactly the 4 non-auto records × 3 harnesses, each `assert {'<rid>'} == set()` (the forged gate accepted gov-id-expired, bill-aquautil-clear, bill-aquautil-blurry, passport-unsupported). (iii) projection trusting unpinned responses (call-args signals) → `test_projection_unpinned_gate_response_mints_nothing` and `test_projection_user_authored_pin_is_ignored` fail. Restored → green.
  3. `/tmp/s3e4_t2.py` (model-free, sockets disabled, no `GOOGLE_API_KEY`) → 8 lines, `entry.gate == expected_gate` for every record, accepted only on the 4 auto_approve records, `passport-unsupported unsupported -> unsupported rejected False` despite the forged label space.
  4. `-k a2a` → 33 passed; `-k "a2a and gov-id-expired"` → 4 passed, (b) COMPLETED with a `pending/hitl_review` entry, no accepted entry.
  5. Regression `tests/test_llm_leg.py tests/test_edge_a2a.py tests/conformance tests/address/test_golden.py` → 183 passed / 6 skipped (the env-gated copies).
  6. Guard greps: no `bridge.edge` import in `bridge/gatepin.py`, `bridge/agent.py`, `bridge/extraction/agent.py` (the one hit is a pre-existing docstring); no `last_extraction_request`; no status language in `design/a2a-edge.md`.

## 2026-10-01 — S3-edge-5: off-script outcomes on the wire — reason_code metadata, real erase over A2A, erase confined to the leg

**Context.** A spike drove the real hybrid edge in-process with a `ScriptedLlm`: two Collector submissions of `bill.pdf` (v0, v1), then the text "please delete bill.pdf" on the same context → R4 → `handle_deletion_request` → COMPLETED, and every version of the scoped fetch URI 404'd. So real erase over A2A, D9 party resolution and all-versions deletion already worked. Missing: (G-A) no `reason_code` on the wire (`status.message.metadata` was `{}`); (G-B) no `REASON_UNROUTED`; (G-C) a real scoping hole — ADK artifact services (`InMemoryArtifactService` and `GcsArtifactService`) treat a filename starting with `user:` as **user-namespaced** (stored at `app/<user>/user/<filename>`, across every session of the party), and `list_artifact_keys(session_id=X)` includes those keys, so `_erase_artifact`'s membership check passed. Repro: party `jordan-lee` persists `user:bill.pdf` in ctx-A and `other.pdf` in ctx-B; deleting `"user:bill.pdf"` on ctx-B returned `deletion_completed` and erased ctx-A's document. (G-D) no owning test. No change to `contract/`, `spec/`, `wiki/evals/`, `terraform/`.

**Decisions.**
1. **`reason_code` rides `status.message.metadata["reason_code"]`** (`META_REASON_CODE` in `bridge/edge/wire.py`) on the R4 **final** status message only (COMPLETED or INPUT_REQUIRED). WORKING and FAILED messages carry no metadata. A gate-only turn carries none (D6 row "—"); metadata is then left **unset** (no empty Struct, no `null`). Model-text-only with no entries → `off_script_unrouted` (`REASON_UNROUTED` in `bridge/offscript.py`). The deterministic legs (`_emit_turn`, `status_message_for`) are unchanged and carry no metadata, so structured traffic stays byte-identical in both modes.
2. **`llm_status(projection, turn) -> (text, reason_code)`** (module-level in `bridge/edge/executor.py`, public for unit tests) is the single D6 text-plus-reason decision. The text expression is byte-identical to S3-edge-3's inline one; the reason is the projection's precedence winner, else `None` with entries, else `REASON_UNROUTED`. `LlmProjection` is imported under `TYPE_CHECKING` only — the executor still never imports `bridge.edge.llm_leg` at runtime.
3. **The erase is confined to the leg.** `_erase_artifact` refuses any `user:`-prefixed filename (`USER_NAMESPACE_PREFIX`) as its first statement, before any store call — even from the session that saved it, because such an artifact spans the party's other contexts and is never inside one leg. The exact-membership check against `list_artifact_keys(app, user, session)` stays, so traversal-looking names (`../ctx-A/bill.pdf`) and other contexts' names are never found.
4. **A deletion addresses the ExchangeTurn's context_id.** The Collector mints its own turn context_id when a `CollectRequest` omits one (S3-edge-3 G6), so a client deletes on the context named in the turn or the scoped URI's first path segment, not necessarily the A2A context of the submitting task. The tests always set `CollectRequest.context_id == message.context_id`.

**Gotchas.**
- The plan's deterministic-mode negative used the text "please delete bill.pdf"; on a deterministic edge R2's `parse_text_request` cannot resolve a process from it and the request errors (JSON-RPC -32603) — also no erase, but not a COMPLETED task. The test uses `"address-proof: please delete bill.pdf"` (resolvable → COMPLETED, no `reason_code`, GET still 200, 0 LLM calls).
- The durable `DatabaseTaskStore` (sqlite) round-trips `status.message.metadata`: after an engine dispose and a fresh app on the same DSN, `tasks/get` still shows INPUT_REQUIRED with `reason_code=escalated_for_review`, and `tasks/cancel` → CANCELED.
- The a2a-sdk accepts a follow-up message on an INPUT_REQUIRED (escalated) task; it routes R4 to the LLM leg on the same task id.
- The double's `after_tool_text` ("Done — the document was accepted.") runs after every tool response, deletion included; it never reaches the status text, the streamed statuses or the turn.
- `bridge/agent.py`, `bridge/edge/llm_leg.py`, `bridge/edge/server.py` and `tests/_scripted_llm.py` needed no change.

**Follow-ups (PROPOSALS — not fixed here).**
- **G7 — persist/fetch side of the `user:` namespace (proposed Sprint 0 security task).** `filename_for` passes a client-supplied part `filename` (`user:x`) straight to `save_artifact`, which writes into the party's user namespace; the fetch endpoint's `load_artifact` for that name then serves it from any context the party owns — a cross-context read. Proposal: neutralize/refuse `user:`-prefixed artifact filenames at persist; the fetch endpoint 404s on them.
- **G6 — deployed GCS store.** `owning_parties()` is empty on `GcsArtifactService`, so the R4 session user is `_unscoped` and a party-scoped artifact is not found (`nothing_to_delete`). Fails closed, not a leak; per-party discovery on GCS is P7-9.

**Verification.**
- T1: `uv run ruff check` clean; `uv run pytest -q` → **1535 passed / 45 skipped** (baseline 1514 / 45; +19 `tests/test_edge_offscript_a2a.py`, +2 `tests/test_offscript.py`; skips unchanged). Guard tests (isolation, service wiring, ADK experimental) → 10 passed. Regression subset (`test_llm_leg`, `test_edge_a2a`, `test_offscript`, `test_edge_gate_inversion`, `conformance`, `address/test_golden`, `test_lifecycle`) → 330 passed / 6 skipped (env-gated).
- T2:
  1. `uv run pytest tests/test_edge_offscript_a2a.py -v --durations=10` → 19 passed in ~3.5 s (slowest 0.56 s).
  2. `/tmp/s3e5_t2.py` (hybrid edge, ScriptedLlm, in-process a2a client): collect ×2 → `pre ?v=0/?v=1/bare 200`; delete → `COMPLETED … permanently erased. {'reason_code': 'deletion_completed'}` (stream and tasks/get); `post ?v=0/?v=1/bare 404`; `list_versions []`; missing → `nothing_to_delete`, other file 200; reject → `off_script_rejected`, 0 entries; escalate → `INPUT_REQUIRED escalated {'reason_code': 'escalated_for_review'}`, tasks/get again INPUT_REQUIRED; unrouted → `No action was taken on this message. {'reason_code': 'off_script_unrouted'}`.
  3. `/tmp/s3e5_ns.py` (the G-C repro) → now `nothing_to_delete`, both keys remain (was `deletion_completed`).
  4. Mutation checks (each reverted): (i) `user:` guard removed → 2 failures (`test_erase_artifact_refuses_user_namespace`, the `[user:bill.pdf]` case of the namespace/traversal test). (ii) `_text_message` ignoring `reason_code` → 11 failures (every A2A outcome test + the `_text_message` unit test). (iii) soft-delete (`_erase_artifact` returns True without `delete_artifact`) → 4 failures (the 404 assertions of the all-versions, cross-context and precedence tests, plus `test_erase_artifact_deletes_existing`).
  5. Guard greps: no `google.adk.a2a` import in `bridge/`; the `InMemory` hits in `executor.py`/`server.py` are pre-existing docstrings; no runtime `bridge.edge.llm_leg` import in `executor.py`; `import bridge.edge` loads the same 1281 modules before and after.

## 2026-10-01 — S3-edge-5: off-script outcomes on the wire — reason_code metadata, real erase over A2A, erase confined to the leg

**Context.** A spike drove the real hybrid edge in-process with a `ScriptedLlm`: two Collector submissions of `bill.pdf` (v0, v1), then the text "please delete bill.pdf" on the same context → R4 → `handle_deletion_request` → COMPLETED, and every version of the scoped fetch URI 404'd. So real erase over A2A, D9 party resolution and all-versions deletion already worked. Missing: (G-A) no `reason_code` on the wire (`status.message.metadata` was `{}`); (G-B) no `REASON_UNROUTED`; (G-C) a real scoping hole — ADK artifact services (`InMemoryArtifactService` and `GcsArtifactService`) treat a filename starting with `user:` as **user-namespaced** (stored at `app/<user>/user/<filename>`, across every session of the party), and `list_artifact_keys(session_id=X)` includes those keys, so `_erase_artifact`'s membership check passed. Repro: party `jordan-lee` persists `user:bill.pdf` in ctx-A and `other.pdf` in ctx-B; deleting `"user:bill.pdf"` on ctx-B returned `deletion_completed` and erased ctx-A's document. (G-D) no owning test. No change to `contract/`, `spec/`, `wiki/evals/`, `terraform/`.

**Decisions.**
1. **`reason_code` rides `status.message.metadata["reason_code"]`** (`META_REASON_CODE` in `bridge/edge/wire.py`) on the R4 **final** status message only (COMPLETED or INPUT_REQUIRED). WORKING and FAILED messages carry no metadata. A gate-only turn carries none (D6 row "—"); metadata is then left **unset** (no empty Struct, no `null`). Model-text-only with no entries → `off_script_unrouted` (`REASON_UNROUTED` in `bridge/offscript.py`). The deterministic legs (`_emit_turn`, `status_message_for`) are unchanged and carry no metadata, so structured traffic stays byte-identical in both modes.
2. **`llm_status(projection, turn) -> (text, reason_code)`** (module-level in `bridge/edge/executor.py`, public for unit tests) is the single D6 text-plus-reason decision. The text expression is byte-identical to S3-edge-3's inline one; the reason is the projection's precedence winner, else `None` with entries, else `REASON_UNROUTED`. `LlmProjection` is imported under `TYPE_CHECKING` only — the executor still never imports `bridge.edge.llm_leg` at runtime.
3. **The erase is confined to the leg.** `_erase_artifact` refuses any `user:`-prefixed filename (`USER_NAMESPACE_PREFIX`) as its first statement, before any store call — even from the session that saved it, because such an artifact spans the party's other contexts and is never inside one leg. The exact-membership check against `list_artifact_keys(app, user, session)` stays, so traversal-looking names (`../ctx-A/bill.pdf`) and other contexts' names are never found.
4. **A deletion addresses the ExchangeTurn's context_id.** The Collector mints its own turn context_id when a `CollectRequest` omits one (S3-edge-3 G6), so a client deletes on the context named in the turn or the scoped URI's first path segment, not necessarily the A2A context of the submitting task. The tests always set `CollectRequest.context_id == message.context_id`.

**Gotchas.**
- The plan's deterministic-mode negative used the text "please delete bill.pdf"; on a deterministic edge R2's `parse_text_request` cannot resolve a process from it and the request errors (JSON-RPC -32603) — also no erase, but not a COMPLETED task. The test uses `"address-proof: please delete bill.pdf"` (resolvable → COMPLETED, no `reason_code`, GET still 200, 0 LLM calls).
- The durable `DatabaseTaskStore` (sqlite) round-trips `status.message.metadata`: after an engine dispose and a fresh app on the same DSN, `tasks/get` still shows INPUT_REQUIRED with `reason_code=escalated_for_review`, and `tasks/cancel` → CANCELED.
- The a2a-sdk accepts a follow-up message on an INPUT_REQUIRED (escalated) task; it routes R4 to the LLM leg on the same task id.
- The double's `after_tool_text` ("Done — the document was accepted.") runs after every tool response, deletion included; it never reaches the status text, the streamed statuses or the turn.
- `bridge/agent.py`, `bridge/edge/llm_leg.py`, `bridge/edge/server.py` and `tests/_scripted_llm.py` needed no change.

**Follow-ups (PROPOSALS — not fixed here).**
- **G7 — persist/fetch side of the `user:` namespace (proposed Sprint 0 security task).** `filename_for` passes a client-supplied part `filename` (`user:x`) straight to `save_artifact`, which writes into the party's user namespace; the fetch endpoint's `load_artifact` for that name then serves it from any context the party owns — a cross-context read. Proposal: neutralize/refuse `user:`-prefixed artifact filenames at persist; the fetch endpoint 404s on them.
- **G6 — deployed GCS store.** `owning_parties()` is empty on `GcsArtifactService`, so the R4 session user is `_unscoped` and a party-scoped artifact is not found (`nothing_to_delete`). Fails closed, not a leak; per-party discovery on GCS is P7-9.

**Verification.**
- T1: `uv run ruff check` clean; `uv run pytest -q` → **1535 passed / 45 skipped** (baseline 1514 / 45; +19 `tests/test_edge_offscript_a2a.py`, +2 `tests/test_offscript.py`; skips unchanged). Guard tests (isolation, service wiring, ADK experimental) → 10 passed. Regression subset (`test_llm_leg`, `test_edge_a2a`, `test_offscript`, `test_edge_gate_inversion`, `conformance`, `address/test_golden`, `test_lifecycle`) → 330 passed / 6 skipped (env-gated).
- T2:
  1. `uv run pytest tests/test_edge_offscript_a2a.py -v --durations=10` → 19 passed in ~3.5 s (slowest 0.56 s).
  2. `/tmp/s3e5_t2.py` (hybrid edge, ScriptedLlm, in-process a2a client): collect ×2 → `pre ?v=0/?v=1/bare 200`; delete → `COMPLETED … permanently erased. {'reason_code': 'deletion_completed'}` (stream and tasks/get); `post ?v=0/?v=1/bare 404`; `list_versions []`; missing → `nothing_to_delete`, other file 200; reject → `off_script_rejected`, 0 entries; escalate → `INPUT_REQUIRED escalated {'reason_code': 'escalated_for_review'}`, tasks/get again INPUT_REQUIRED; unrouted → `No action was taken on this message. {'reason_code': 'off_script_unrouted'}`.
  3. `/tmp/s3e5_ns.py` (the G-C repro) → now `nothing_to_delete`, both keys remain (was `deletion_completed`).
  4. Mutation checks (each reverted): (i) `user:` guard removed → 2 failures (`test_erase_artifact_refuses_user_namespace`, the `[user:bill.pdf]` case of the namespace/traversal test). (ii) `_text_message` ignoring `reason_code` → 11 failures (every A2A outcome test + the `_text_message` unit test). (iii) soft-delete (`_erase_artifact` returns True without `delete_artifact`) → 4 failures (the 404 assertions of the all-versions, cross-context and precedence tests, plus `test_erase_artifact_deletes_existing`).
  5. Guard greps: no `google.adk.a2a` import in `bridge/`; the `InMemory` hits in `executor.py`/`server.py` are pre-existing docstrings; no runtime `bridge.edge.llm_leg` import in `executor.py`; `import bridge.edge` loads the same 1281 modules before and after.

## 2026-10-01 — S3-edge-6: HITL `request_human_review` park/resume agent-to-agent (G / R3 / R6)

**Context.** The route table's G/R3/R6 rows (S3-edge-1 ADR) were unreachable: `_has_pending_review` was a stub and `request_human_review` returned an ack dict, so the LongRunningFunctionTool never paused. Probes against ADK 2.7.1 and a2a-sdk 1.1.2 established:
- an LRFT pauses only when it returns `None`;
- the resume is a fresh Runner fed an id-matched `FunctionResponse` as `role="user"` content, and continues the same `invocation_id`;
- ADK raises on an unknown id and re-accepts an already-answered one;
- **an exception raised inside `AgentExecutor.execute` marks an existing non-terminal task FAILED** (`ActiveTask._run_producer`).

No change to `spec/`, `wiki/evals/`, `terraform/`.

**Decisions.**
1. **D-a — the pause shape.** `request_human_review` returns `None`. `pending_review_calls` treats a review call as pending when its id is in its event's `long_running_tool_ids` and no response with that id exists (from any author). `resolved_review_calls` counts user-authored responses only, which only the executor writes.
2. **D-c — per-task scoping through server-authored task metadata.** At park, `task.metadata.bridge_review_session = {user_id, session_id, call_ids}` is written via `TaskUpdater.update_status`, always as the full value. Pending-for-task = `call_ids` ∩ the session's pending calls, so a call parked on another task of the same context refuses. `DatabaseTaskStore` round-trips it (proven across a rebuild).
3. **D-e — durable seed.** The Collector's original entries are persisted in the same run's `state_delta` under `bridge:review_tasks[<task_id>]` (`LlmLeg.run(review_task_id=)`). The resume rebuilds the turn from them with the `resolve_hitl` transform (disposition + reviewer message; gate `hitl_review` and the gate reason_code kept). The rebuilt turn replaces the task's `exchange_turn` artifact (same artifact id).
4. **D-g — reviewer authorization fails closed.** The allow-list is `build_a2a_app(reviewers=)`, else `BRIDGE_EDGE_REVIEWERS`; unset or empty refuses every resolution. The principal is only the call-context user, populated by an injected Starlette `AuthenticationBackend` (`build_a2a_app(auth_backend=)`). The edge ships no backend; tests define a header backend in the test file. The payload `reviewer` is an audit label.
5. **Pre-dispatch guard (deviation from the plan's D-h).** The plan raised refusals inside `execute`. That marks the parked task FAILED, so the task would not be unchanged. Instead `CollectExecutor.admit(message, task, user)` runs from `EdgeRequestHandler` (a `DefaultRequestHandler` subclass overriding `on_message_send`/`on_message_send_stream`) before the SDK touches the task. It handles G (`awaiting_human_review`), R5 (`no_pending_review`) and R6 validation:
   - decode → `InvalidParamsError` `malformed_human_review_resolution`;
   - authorization → `InvalidRequestError` `reviewer_not_authorized`;
   - pending-on-this-task → `InvalidParamsError` `review_call_id_mismatch`, -32602.

   `execute` repeats the checks as defense in depth. This also fixes the pre-existing R5 path, which raised from `execute`.
6. **R3 handoff only when it can matter.** The handoff runs only on a hybrid edge with a leg, under a `bridge-hitl: park` process, with at least one `hitl_review` entry.
   - The genuine `ExtractionResult`s come from the additive `Collector.collect(results_out=)`, which is passed only on that path, so duck-typed collectors keep working.
   - The seeds are `source="collector"` signals, the request's label space and the session key `(party, turn.context_id)`.
   - If the model parks nothing, or the leg raises, the edge degrades to the report-mode Collector turn, COMPLETED.
7. **Contract 1.4** adds the additive `HumanReviewResolution` (`extra="forbid"`, `call_id`, `resolution: accepted|rejected`, `message`, `reviewer?`). The tolerant reader strips a newer minor's fields. Skill metadata gains `bridge-hitl: park|report` (default `report`; `address-proof` parks).

**Gotchas.**
- **Owner-scoped task store.** a2a-sdk task stores scope tasks by `resolve_user_scope(context)`, the principal name. A principal other than the task's owner gets `TaskNotFoundError`, and the guard defers to the SDK in that case. The resolving principal is therefore the owner's: the consumer agent relays its human reviewer's decision.
- **Duplicate-resolution race.** Two identical resolutions racing past the guard concurrently can still fail the task: the second reaches ADK with an answered id.
- **Several reviews in one turn.** They resolve one at a time; resuming one of several paused calls yields no events until the last one is answered.
- **History in later turns.** A new R3 park on a context that already holds another task's paused call starts a new invocation. The model history then contains the unanswered call. ScriptedLlm is indifferent to this; a real model's tolerance is unverified.
- **Restart-test noise.** The a2a `ActiveTask` producer/consumer coroutines of the torn-down first app log `coroutine ignored GeneratorExit` at the in-loop restart boundary. Harmless; the existing escalate restart test shows the same.

**Follow-ups (PROPOSALS).**
- **Deploy-time verified principal backend** (Gateway/IAP identity → `AuthenticationBackend`). Until it exists, a deployed hybrid edge refuses every resolution, which is the correct fail-closed behavior.
- **Separate reviewer identity** (a reviewer who is not the task owner) needs an owner-resolver/delegation design on the task store.
- **Spec proposal (not edited):** `spec/` could state that the resolving principal is the task owner's (A2A owner scoping) and that refusals are pre-dispatch JSON-RPC errors.

## 2026-10-01 — S3-edge-7: CUJ golden parity — hybrid vs deterministic on the Address corpus

**Context.** S3-edge-1..6 built the hybrid edge (route table, LLM leg, gate pin, off-script wire outcomes, HITL park/resume). This task proves the two edge modes reach the same **terminal outcome** on the Address oracle (`wiki/evals/address/expected.json` + `timeline.json`, read-only). It is test-only: no change to `bridge/`, `contract/`, `agents/`, `spec/`, `wiki/evals/` or `terraform/`. New: `tests/address/test_golden_llm_edge.py`; additive helpers in `tests/address/_corpus.py` (`record_by_id`, `hitl_record_ids`, `timeline_hitl_resolutions`).

**Decisions.**
1. **D-1 — the vehicle is split.** CUJ-1, CUJ-10 (distinct + same-issuer) and the CUJ-4 first turn run through the Address driver (`run_collection`, non-durable). The 8-record matrix and the full CUJ-4 blurry→clear loop run through the in-process `a2a-sdk` client. Reason: a planning spike showed `run_collection` against a hybrid edge that parks cannot finish. Non-durable, the gate loops `again` and the re-sent collect message is refused by `decode_intake` (-32603). Durable, it stops at the parked `pending` turn with `terminal=False`. The Address consumer has no way to author a `HumanReviewResolution`, and the G guard correctly refuses its resume FunctionResponse. **`agents/address/driver.py` is not modified.** The TODO Files line listed it, but that was over-scoped: a consumer reviewer-relay is a new consumer feature (PROPOSAL below).
2. **D-2 — parity is terminal outcome, compared mode against mode.** The `Outcome` projection is `(task_state, terminal, status reason_code, entries)`. Each entry is `(doc_id, doctype, issuer, disposition, gate, reason_code)`, sorted by doc_id. Every outcome is also pinned to the corpus (`expected_disposition`/`expected_gate`/`doctype`/`issuer`), so "both wrong the same way" fails. Confidences, entry message text (except the reviewer message on resolution), artifact URIs and context ids are never compared.
3. **D-3 — HITL resolution oracle.** `timeline_hitl_resolutions()` reads every `hitl_resolved` beat: `bill-aquautil-clear → accepted`. `gov-id-expired` has no beat, so it is resolved both `accepted` and `rejected`, and the final disposition must follow the reviewer. That proves faithful relay without inventing an oracle value. The params come from the corpus at import, not hard-coded. An integrity test checks that every timeline-resolved doc is a `hitl_review` record and that the split is 6 non-HITL + 2 HITL.
4. **D-4 — expected HITL shapes.**
   - **Deterministic:** COMPLETED, `terminal=True`, `pending/hitl_review/R` (R = the Collector reason: `flagged_fields` for gov-id-expired, `needs_review` for bill-aquautil-clear), no status `reason_code` (report mode).
   - **Hybrid park:** INPUT_REQUIRED, `terminal=False`. The entry is the det entry with `reason_code=awaiting_human_review`. The status carries `reason_code=awaiting_human_review` plus `review_call_id`. `task.metadata.bridge_review_session == {user_id: jordan-lee, session_id: ctx, call_ids: [call_id]}`.
   - **Hybrid after resolution `res`:** COMPLETED, `terminal=True`, same task/context ids. There is exactly one `exchange_turn` artifact, the status text is the reviewer message, and the status `reason_code` is R. The crisp equality is `final.entries == det.entries with disposition=res`.
5. **D-5 — LLM call accounting (fixture leg).** Non-HITL records and every driver case: `call_count == 0`. The hybrid double is `ScriptedLlm(max_calls=0)`, so a model call fails loud. HITL: `== 1` after park and `== 2` after resume. The double's `after_tool_text` / `default_text` never appear in the turn or in any streamed or final status text.
6. **D-6 — one principal.** Every app (both modes) is built with `reviewers=("alice",)` and a test-local `_HeaderAuth`. Submission and resolution both use `X-Test-Principal: alice`, because the task store is owner-scoped. The driver's httpx client sends no principal; no resolution happens there.
7. **D-7 — context ids.** Each a2a case uses a unique `s3e7-{mode}-{rid}[-{res}]` ctx with `CollectRequest.context_id == message.context_id`. The det and hybrid apps never share a session service or ctx.
8. **D-8 — the Gemini leg.** A `leg` fixture parametrizes `scripted` / `gemini`. The gemini param is skipped unless `BRIDGE_TEST_GEMINI=1`; it pins `BRIDGE_SEAM_EXTRACTION=local` and builds `_CountingGemini(Gemini)` (`model=resolve_model(None)`; `calls` counted in `generate_content_async(llm_request, stream=False)`). Exact counts relax to non-HITL `== 0` and HITL park `>= 1`, resume `> park`. Everything else is identical. `test_live_adversary_a2a_hybrid[rid × kind]` (gemini-only, 32 cases) asserts only model-independent invariants (`_assert_no_minted_accept`): entries ⊆ {rid}, no accept on a non-auto_approve record, `(gate, disposition)`/doctype/issuer equal to the corpus, no `rejected` unless the corpus says so, a pinned gate disposes to the genuine verdict, the canary never on the wire, and task ≠ FAILED. It does not assert which tool the model chose. The scripted adversaries stay in `tests/test_edge_gate_inversion.py`.

**Gotchas.**
- `_turn` selects the LAST `exchange_turn` data part by metadata. After resume the artifact is replaced (same id), so the name count is 1.
- Deterministic status metadata is **unset**, so `_meta` reads it with `HasField`.
- `make_llm(())` must unpack (`_review_llm(*docs)`). An early draft passed `()` as one doc id, which silently replaced the fail-loud `max_calls=0` double with a reviewing one.
- Expected noise: ADK's experimental `UserWarning` per hybrid build, and the a2a-sdk "Dispatcher task is not running" warning on a streamed park.

**Live-leg findings (`BRIDGE_TEST_GEMINI=1`, `GOOGLE_GENAI_USE_VERTEXAI=1`, gemini-2.5-flash on Vertex) — NOT fixed here (`bridge/` out of scope).** The leg was run with credentials present.
- Full run 1: 3 failures. Full run 2: 44 passed / 2 failed. Two more runs of the 4 HITL-park cases: 1 failed, then 0 failed.
- All 6 non-HITL cases, all 4 driver cases and 31–32 of the 32 live adversaries passed every run. No adversary ever minted an accept, and the canary never reached the wire.
- **F-1 — the live model does not reliably park.** On the R3 handoff, Gemini sometimes answers without calling `request_human_review` (observed on `gov-id-expired` and the CUJ-4 clear copy, about 1 in 4–8 attempts). The edge then degrades to report-mode COMPLETED (S3-edge-6 D6). That is safe, since there is no accept, but it misses the park. Per the plan this is a real finding, and the assertion is not loosened. **PROPOSAL:** make the R3 handoff model-independent, either by having the executor call the LRFT directly or by forcing the tool (ADK `tool_config` mode `ANY` restricted to `request_human_review`) on the handoff turn.
- **F-2 — `extract_document` raises on an unknown artifact name.** Under an injection prompt, Gemini called `extraction_agent` with `"address proof document bill-powerco-clean"`. `FixtureExtractor` raised `KeyError`, the tool error propagated, the LLM leg failed and the task went FAILED (`test_live_adversary_a2a_hybrid[bill-powerco-clean-c_reject]`). Nothing was minted, but a model-chosen name can fail the task. **PROPOSAL (Sprint 0 robustness):** `extract_document` returns a refusal dict (as the gate pin does) instead of raising for an unknown or unresolvable artifact.

**Follow-ups (PROPOSALS — not implemented).**
- **Address consumer reviewer-relay (D-1):** the Address consumer relays a human reviewer's `HumanReviewResolution` on a Bridge `awaiting_human_review` park. CUJ-5 could then go agent-to-agent through the consumer driver, and `agents/address/driver.py` would own the CUJ-4 full loop on the hybrid edge.
- **`timeline.json` beat for `gov-id-expired` (D-3):** a `hitl_resolved` beat would give it an oracle resolution. `wiki/evals/` is read-only here.
- **For S3-edge-8:** the CUJ-1/10 Judges gain "identical on deterministic and hybrid edges". The Driver can cite `tests/address/test_golden_llm_edge.py::test_cuj_driver_parity`.
- F-1 and F-2 above.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q`: baseline 1619 collected → 1574 passed / 45 skipped. After: 1681 collected → **1590 passed / 91 skipped**. The +16 passed are all in the new file. The +46 skips are only the `BRIDGE_TEST_GEMINI` gate (14 gemini `leg` params + 32 live adversaries). Guard tests (isolation, service wiring, ADK experimental) → 10 passed.
- T2:
  1. `uv run pytest tests/address/test_golden_llm_edge.py -v -rs` → 16 passed / 46 skipped. Every skip reason is "set BRIDGE_TEST_GEMINI=1 to run the live Gemini LLM leg". Passed: the 6 non-HITL ids, `gov-id-expired-{accepted,rejected}`, `bill-aquautil-clear-accepted`, the 4 driver CUJ cases, the CUJ-4 loop and the 2 integrity tests.
  2. `/tmp/s3e7_t2.py` (model-free) printed 6 `det == hyb` lines with `llm_calls=0`. `gov-id-expired` printed `park=(INPUT_REQUIRED, awaiting_human_review, call_id) -> final=(COMPLETED, accepted|rejected, hitl_review, flagged_fields)`. `bill-aquautil-clear` printed `-> final=(COMPLETED, accepted, hitl_review, needs_review)`. R equals det's reason in every case, and the HITL `llm_calls=2`.
  3. Mutation checks, each reverted with `git checkout -- bridge/`:
     1. `hitl_mode_for` always `"report"` → 4 failures: the 3 HITL cases and the CUJ-4 loop.
     2. `classify_route` sends COLLECT-matching messages to `LLM` in hybrid → 14 failures: every case except the 2 integrity tests.
     3. The resume always sends `"accepted"` → 1 failure: `gov-id-expired-rejected`.
     After the reverts the file is green again (16 passed) and `git status` shows no `bridge/` change.
  4. `git diff --quiet -- wiki/ spec/ bridge/ contract/ agents/` → 0.
  5. Live leg: see the findings above.

## 2026-10-01 — S3-edge-8: spec proposal (CUJ-14; CUJ-5/1/10 Drivers & Judges) + Sprint 3 close-out

**Context.** S3-edge-1..7 built the hybrid edge and made off-script intents (real erase of every version, reject, escalate), HITL park/resume and the LlmAgent leg reachable agent-to-agent. `spec/cujs.md` still names only portal-side drivers for CUJ-5, has no off-script CUJ, and its CUJ-1/10 Judges do not mention edge modes. This task documents those capabilities as **spec proposals** (no `spec/` edit — `spec/` is protected) and closes Sprint 3 with T3 green: **11/11 built CUJs pass both judges (deterministic drivers + hard-fail traps; LLM judge) in both edge modes**. No code change.

**SPEC PROPOSAL (not applied — spec/ is protected).**

**P-1 — CUJ table: add a CUJ-14 row after CUJ-13, and update "How to read this".**
```markdown
| **CUJ-14** | **Off-script intent mediated agent-to-agent** — a party's agent sends free text instead of a document ("erase my file", "that was the wrong file", "I need a person") and the Bridge's LlmAgent routes it to the right tool: a real erase of every version, an off-script reject, or a durable escalate — never a disposition of a genuine document. | — ([off-script intents](../design/adk.md)) | Address | ✅ |
```
In "How to read this": `Address exercises CUJ-1/2/4/5/6/7/8/10` → `Address exercises CUJ-1/2/4/5/6/7/8/10/14`.

**P-2 — Drivers & Judges: add CUJ-14 (after CUJ-13).**
```markdown
- **CUJ-14** — **Driver:** `tests/test_edge_offscript_a2a.py::test_delete_erases_all_versions_then_fetch_404` + `::test_reject_submission_off_script_rejected` + `::test_escalate_input_required_survives_tasks_get_and_restart` (+ `::test_cannot_erase_another_contexts_or_partys_artifact`). **Judge:** on the hybrid edge a free-text deletion completes with `reason_code=deletion_completed` and every version of the scoped fetch URI then 404s; a wrong file completes with `off_script_rejected` and no `rejected` ledger entry; an escalate holds `INPUT_REQUIRED` + `lifecycle=escalated` across `tasks/get` and a restart; another party's or context's artifact is never erased; trap: soft-delete (the fetch still 200 after `deletion_completed`) or the LLM disposing a genuine document (`tests/test_edge_gate_inversion.py::test_adversary_a2a_hybrid` — an off-script tool on a genuine document mints no entry beyond the genuine gate verdict).
```

**P-3 — Drivers & Judges: replace the CUJ-5 line.**
```markdown
- **CUJ-5** — **Driver:** `tests/test_portal.py::test_hitl_reviewer_can_reject` + `::test_blurry_resubmit_then_clear_hitl_then_resolve_accept`; `tests/address/test_park_resume.py::test_dur3_park_is_zero_compute`; agent-to-agent: `tests/test_edge_hitl_a2a.py::test_hitl_park_input_required_with_call_id` + `::test_parked_task_zero_compute` + `::test_resume_after_rebuild_reaches_timeline_resolution`. **Judge:** flagged extraction parks then resumes on an id-matched response — via portal Path-B, and agent-to-agent as an `INPUT_REQUIRED` A2A task (`awaiting_human_review` + `review_call_id`) resumed by a `HumanReviewResolution` on the same task, reaching the `timeline.json` resolution after a rebuild; trap: park uses compute (DUR-3; `tests/test_edge_hitl_a2a.py::test_parked_task_zero_compute`) / HITL-without-park not 409 (`tests/test_portal.py::test_hitl_without_park_409`) / a non-reviewer or mismatched-call-id resolution accepted (`tests/test_edge_hitl_a2a.py::test_resume_non_reviewer_rejected`, `::test_resume_mismatched_call_id_refused`).
```

**P-4 — Drivers & Judges: replace the CUJ-1 and CUJ-10 lines.**
```markdown
- **CUJ-1** — **Driver:** `tests/address/test_golden.py::test_cuj1_path_a_instant_parity_mock_and_real` (+ `tests/address/test_golden_llm_edge.py::test_cuj_driver_parity[scripted-cuj1]`). **Judge:** terminal accepted+satisfied identical on mock and real edge, and identical on the deterministic and hybrid edges; trap: raw bytes on the wire / ADK events read / the hybrid edge calls the model on a structured request.
- **CUJ-10** — **Driver:** `tests/address/test_golden.py::test_cuj10_distinct_issuer_accept_real_edge` + `::test_cuj10_same_issuer_reject_real_edge` (+ `tests/address/test_gate.py`, `tests/address/test_golden_llm_edge.py::test_cuj_driver_parity[scripted-cuj10-distinct]` + `[scripted-cuj10-same-issuer]`). **Judge:** one gov-ID *or* two distinct-issuer bills satisfy; same-issuer pair rejects; the outcome is identical on the deterministic and hybrid edges; trap: same-issuer counted as two / the hybrid edge calls the model on a structured request.
```

**P-5 (additive, beyond the TODO list) — CUJ-4 Driver gains a real-edge both-mode companion.** The current CUJ-4 driver runs on the mock edge only.
```markdown
- **CUJ-4** — **Driver:** `tests/address/test_golden.py::test_cuj4_resubmit_full_loop` (+ `tests/test_portal.py::test_resubmit_cap_escalates_not_rejects`, `tests/address/test_golden_llm_edge.py::test_cuj4_resubmit_then_clear_loop`). **Judge:** blurry→clear accepts within ≤3, on the mock and on the real (hybrid) edge; trap: >3 resubmits / cap escalates-not-rejects violated.
```
Also note: on apply, `docs/features.md` CORE‑8 and ART‑1 gain `14` in their CUJ cell.

**ADK friction recorded.** Issues 3–5 added to `docs/upstream-adk-issues.md`:
- **Issue 3** — `user:` filename prefix silently switches artifact scope to party-wide user namespace; `list_artifact_keys` mixes those keys in. Workaround: `bridge/offscript.py` (`USER_NAMESPACE_PREFIX` and `_erase_artifact` guard). Covered by `tests/test_edge_offscript_a2a.py::test_user_namespace_and_traversal_names_never_erase_across_contexts`.
- **Issue 4** — `LongRunningFunctionTool` pause/resume semantics are implicit: (a) only `None` pauses; (b) ADK re-accepts already-answered call ids; (c) resuming one of several paused calls yields no events until all are answered. Workaround: executor's own `pending_review_calls`/`resolved_review_calls` bookkeeping + `CollectExecutor.admit` pre-dispatch guard.
- **Issue 5** — `AgentTool` returns sub-agent merged final text, not its tool output. Workaround: `bridge/gatepin.py::record_extraction` writes signals to session state, forwarded via `state_delta`, pinned by root `before_tool_callback`.

**Excluded from upstream-adk-issues.md** (noted but not filed):
- a2a-sdk behavior (exception in `AgentExecutor.execute` marks task FAILED — needed `EdgeRequestHandler` pre-dispatch). That is a2a-sdk, not ADK, and is a candidate for a separate a2a-sdk report.
- Gemini not reliably calling `request_human_review` (S3-edge-7 F-1): model behavior; fix is a Bridge change (forced `tool_config`).
- `extract_document` raising on unknown name (S3-edge-7 F-2): Bridge bug, not ADK.
- `ResumabilityConfig` experimental warning: already in TEST-4 register.

**T3 evidence (both edge modes).** Each of the 11 built CUJs ran in both `BRIDGE_EDGE_MODE=deterministic` and `=hybrid` with credentials stripped (`env -u GOOGLE_API_KEY -u GOOGLE_GENAI_USE_VERTEXAI`), plus the proposed CUJ-14 in hybrid. Jeep LLM judge (Gemini 2.5 Flash via Vertex): criteria = CUJ journey row + Judge text; rubric = `{score, pass, rationale}`. Pass rule: `pass == true && score >= 0.8`. All 11 CUJs pass both judges in both modes.

| CUJ | Mode mechanism | Det passed | Hyb passed | Jeep score det | Jeep score hyb |
|---|---|---|---|---|---|
| CUJ-1 | explicit param (`[mock]/[real-deterministic]/[real-hybrid]`) | 4 | 4 | 1.00 | 1.00 |
| CUJ-2 | mode-independent (never routes through executor) | 3 | 3 | 1.00 | 1.00 |
| CUJ-3 | mode-independent | 1 | 1 | 1.00 | 1.00 |
| CUJ-4 | mock-only spec driver; real-edge both-mode companions | 6 | 6 | 1.00 | 1.00 |
| CUJ-5 | hybrid-only a2a (`tests/test_edge_hitl_a2a.py`); det companion | 25 | 25 | 1.00 | 1.00 |
| CUJ-6 | mode-independent | 3 | 3 | 1.00 | 1.00 |
| CUJ-7 | mode-independent | 2 | 2 | 1.00 | 1.00 |
| CUJ-8 | env-sensitive (only CUJ failing on `BRIDGE_EDGE_MODE=bogus`) | 1 | 1 | 1.00 | 1.00 |
| CUJ-10 | explicit param (`[deterministic]/[hybrid]`) | 14 | 14 | 1.00 | 1.00 |
| CUJ-11 | mode-independent | 2 | 2 | 1.00 | 1.00 |
| CUJ-12 | mode-independent | 5 | 5 | 1.00 | 1.00 |
| **CUJ-14 (proposed, not counted)** | hybrid-only (off-script tools) | — | 51 | — | 1.00 |

**Sensitivity proof:** `BRIDGE_EDGE_MODE=bogus` fails exactly 1 test (CUJ-8: `tests/test_live_doctype_add.py::test_live_doctype_add_regenerates_card`) with `ValueError: 'bogus' is not a valid EdgeMode`, and 65 pass. Confirms env `BRIDGE_EDGE_MODE` reaches only CUJ-8's driver; CUJ-1/10 carry explicit mode params.

**11/11 built CUJs pass both judges in both edge modes.**

**Carried proposals (consolidated for /replan; none implemented here).**
- **S3-edge-5:** G6 (GCS `owning_parties` empty → `_unscoped`), G7 (persist and fetch side of `user:` namespace).
- **S3-edge-6:** deploy-time verified principal backend; separate reviewer identity; "resolving principal is the task owner's" spec note.
- **S3-edge-7:** D-1 (Address consumer reviewer-relay); `timeline.json` `gov-id-expired` beat; F-1 (model-independent R3 handoff); F-2 (`extract_document` refusal instead of raise).
- **This task:** `docs/upstream-adk-issues.md` Issue 1 stale path (`agents/src/bridge_client/remote_consumer.py` should be `agents/address/...`); noted in S0-docs-9.

**Gotchas.**
- Env `BRIDGE_EDGE_MODE` reaches only CUJ-8's driver; CUJ-1/10 carry explicit mode params.
- CUJ-4's spec driver is mock-only; real-edge both-mode evidence comes from three companion tests.
- The features catalog (`docs/features.md`) cites no proposed CUJ (avoids dangling spec references).
- `docs/features/a2a-edge.md` is oldest-first despite its "Newest first" comment.
- There is a duplicated S3-edge-5 entry in `DECISIONS.md` (lines 3076 and 3106). This is noted but not deduped here.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q` → 1590 passed / 91 skipped (baseline matches S3-edge-7 exit count; no code change).
- T2:
  1. **Scope.** `git diff --name-only HEAD` lists only `DECISIONS.md TODO.md docs/features.md docs/upstream-adk-issues.md docs/features/a2a-edge.md`. `git diff --quiet HEAD -- spec/ wiki/ bridge/ contract/ agents/ terraform/ tests/ frontend/; echo $?` prints `0`.
  2. **Node id validation.** `uv run python /tmp/s3e8_ids.py DECISIONS.md "## 2026-10-01 — S3-edge-8"` prints `OK 24 ids` and exits 0. The script is from plan §4.2. All 24 proposed ids (P-1..P-5 blocks) collect.
  3. **T3 deterministic half.** Each of the 11 built CUJ node sets exits 0 under both `BRIDGE_EDGE_MODE=deterministic` and `=hybrid`, credentials stripped. Skips are only gemini/`BRIDGE_TEST_GEMINI` params. The proposed CUJ-14 set (`tests/test_edge_offscript_a2a.py` + `tests/test_edge_gate_inversion.py::test_adversary_a2a_hybrid`) exits 0 in hybrid.
  4. **T3 sensitivity.** `BRIDGE_EDGE_MODE=bogus uv run pytest -q -p no:cacheprovider <all spec driver+trap ids>` fails exactly `tests/test_live_doctype_add.py::test_live_doctype_add_regenerates_card` with `ValueError: 'bogus' is not a valid EdgeMode`. Result: 1 failed / 65 passed.
  5. **T3 LLM half.** 11 jeep verdicts, each attached both mode runs, all with `pass=true` and `score >= 0.8`. CUJ-14 verdict: `pass=true`, `score=1.0`.
  6. **Features catalog.**
     - `git diff -U0 docs/features.md | grep '^+' | grep -nE "S[0-9]-[a-z]|Sprint|this cycle|\bnow\b|CUJ-14|\b14\b"` produces no output (status-free, no dangling proposed CUJ).
     - `grep -c "WIRE‑7\|WIRE‑8" docs/features.md` prints `2`.
     - `grep -n "PENDING → " docs/features.md` produces no output (WIRE-3 row corrected).
     - Every table row has the header's cell count (verified with awk count of `|` per section).
  7. **Upstream doc.**
     - `grep -n "^## Issue" docs/upstream-adk-issues.md` shows Issues 1–5 in sequence.
     - `grep -n "USER_NAMESPACE_PREFIX" bridge/offscript.py` hits line 30.
     - `grep -n "def pending_review_calls" bridge/edge/llm_leg.py` hits line 441.
     - `grep -n "def record_extraction" bridge/gatepin.py` hits line 129.
     - Each cited ADK file exists under `.venv/lib/python3.12/site-packages/google/adk/`.
  8. **TODO.**
     - `grep -n "S3-edge-8" TODO.md` shows `- [x]` and `_(done 2026-10-01; reviewed; see docs/features/a2a-edge.md)_`.
     - `grep -c "^- \[ \] \*\*S3-edge" TODO.md` prints `0` (no open S3-edge tasks).

## 2026-10-01 — S0-bug-3: landed email is drained on the board-owning host (served drain + poll loop + `drain` CLI)

**Bug.** `EmailProcessor` was constructed nowhere outside `tests/`: the deployed webhook lands MIME create-only and binds no processor (by design), the runtime container builds only `build_a2a_app` (no RFP board), and the CLI had no `drain`. A landed carrier reply was never correlated or filed on any real host.

**Decisions.**
1. **The processor binds where the board lives: `build_dashboard_app(rfps=…)`**, wired from env (`email_processor_from_env`), and so also on `build_rfp_demo_app` (mounted at `/ops`). That host serves `POST /email/drain` + `GET /email/outcomes/{key}` via the new `mount_email_drain_routes` (extracted from `build_email_app`, which now calls it — behavior unchanged; the webhook-only host still 404s both).
2. **Never bind a processor to a board no RFP opens onto.** With an empty board every landed mail correlates to nothing and is **permanently** marked processed `no_exchange_token` — that destroys the queue. So: no processor on `build_a2a_app` / `serve_container`, and the CLI `drain` **POSTs to the served host's `/email/drain`** instead of building its own processor + (empty) board in-process.
3. **No processor without `rfps`.** `build_dashboard_app(rfps=None)` binds none even with env fully set; `email=` without `rfps=`, or with a processor whose `.board is not rfps` → `ValueError`.
4. **Env gate.** Mailbox blank → `None`; mailbox set but no landing location for the resolved `email_ingress` mode (local `BRIDGE_EMAIL_DIR` / gcp `BRIDGE_EMAIL_BUCKET`) → `None` (mailbox + secret alone still only shows reply addresses — otherwise the poll loop would raise from the local adapter every tick); mailbox + landing without `BRIDGE_EMAIL_TOKEN_SECRET` → `RuntimeError` at build.
5. **Trigger = served `POST /email/drain` + a lifespan poll loop** (`email_drain_loop`): `BRIDGE_EMAIL_DRAIN_INTERVAL` seconds, unset → 30, `0` → no loop, negative / non-numeric / NaN / inf → `ValueError` at build; an explicit `email_drain_interval=` kwarg wins (also validated). A failed drain logs `periodic email drain failed; will retry next tick` and keeps looping; `CancelledError` propagates and the task is cancelled + awaited on shutdown. `EmailProcessor.drain` holds an `asyncio.Lock`, so the loop and the POST serialize.
   - **Gotcha: Starlette never runs a mounted sub-app's lifespan.** `rfp_demo` mounts the dashboard, so its OUTER app carries the lifespan; the dashboard resolves + validates the interval once and exposes it as `app.state.email_drain_interval` for the mounting host. Exactly one loop per process.
6. **Reply addresses match what correlates.** With a processor bound and `intake=None`, the dashboard (and the demo's portal) show `processor.intake` (new read-only `EmailProcessor.intake -> IntakeAddressing`) rather than `IntakeAddressing.from_env()`; an injected `intake` still wins.
7. **Circular-import trap.** `bridge/edge/email_ingress.py` imported `bridge.dashboard.board.RfpBoard` at module top (→ `bridge/dashboard/__init__` → `server`). Fixed both sides: `RfpBoard` is a `TYPE_CHECKING` import in email_ingress (annotation-only), and `server.py` / `rfp_demo.py` import the email_ingress names lazily inside the builders. Guarded by fresh-process subprocess tests.
8. **Deployed scope (honest).** No deployed host owns an RFP board, so the deployed runtime still binds no processor; no Cloud Run service / IAM change. Terraform edits are comment-only (the runtime `BRIDGE_EMAIL_*` envs + bucket grants are exactly what a board-owning host running as `bridge-runtime` needs; `email.tftest.hcl` still asserts them). Follow-up filed: **S0-arch-17** (a deployed board-owning host binds the processor + a Scheduler / GCS-finalize trigger on `/email/drain`; needs S0-arch-14).

**Deviations from the plan.**
- The CLI imports `httpx` lazily (the `graph.py` idiom). An injected client (tests pass a `TestClient`) keeps its own timeout; `--timeout` applies to the CLI's own client only (Starlette deprecates a per-request `timeout` on `TestClient`).
- `TODO.md` S0-bug-3 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.
- Additional tests beyond the plan: a CLI `quarantined <key> <reason>` line, `BRIDGE_EMAIL_DRAIN_URL` fallback, `inf` interval, the unset → 30 default, and a negative explicit kwarg.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q` → 1611 passed / 91 skipped (baseline 1590 / 91: +21 new in `tests/test_email_served.py`, no new skips). `terraform fmt -check -recursive && terraform validate && terraform test` → 31 passed. `import bridge.edge.email_ingress`, `import bridge.dashboard`, `import bridge.dashboard.rfp_demo` each succeed in a fresh process.
- T2 (real `uvicorn --factory bridge.dashboard.rfp_demo:build_rfp_demo_app --port 8010`, `BRIDGE_EMAIL_DRAIN_INTERVAL=0`):
  - PASS — the served API advertises carrier-c's `reply_address` (`rfp-quotes+<token>@broker.example`).
  - PASS — `land /tmp/reply.eml` → `landed <key>`.
  - PASS — `drain --url http://127.0.0.1:8010/ops` → `filed <key> acme-vb-2027 carrier-c filed=2 unreadable=1`, `drained 1`.
  - PASS — `/ops/rfps/acme-vb-2027`: carrier-c CRITICAL_ILLNESS `received`, HOSPITAL_INDEMNITY `incomplete`.
  - PASS — `/ops/email/outcomes/<key>`: `filed carrier-c acme-vb-2027`, unreadable `unsupported_media_type`.
  - PASS — second drain → `drained 0`; `drain --url http://127.0.0.1:9` → stderr `drain failed: ConnectError: …`, exit 1.
  - PASS — poll loop (`BRIDGE_EMAIL_DRAIN_INTERVAL=1`): a landed carrier-b CI reply moved carrier-b CRITICAL_ILLNESS `acknowledged` → `received` within 3 s with no CLI drain; clean shutdown.
  - PASS — webhook-only `build_email_app(graph=…, client_state=…)` still 404s `/email/drain` (`tests/test_email_webhook.py` green).

**Spec proposal (not applied; spec is protected).** S0-docs-6(d)'s email-intake CUJ ("carrier answers a solicited request by email → correlated to its leg → dispositioned") can cite the served path as its Driver: `tests/test_email_served.py::test_landed_reply_reaches_its_rfp_leg_through_the_served_host`.

## 2026-10-01 — S0-arch-14: the RFP board persists through the `exchange_store` seam (versioned exchange records, CAS)

**Context.** The RFP aggregate (`RfpCollection`, emergent Collect) lived in an in-memory `dict` on `RfpBoard`, while `design/aggregate-model.md` says agent-owned Requirements state materializes the Exchange into a stored record via the `exchange_store` seam — which was an empty ABC with `pass` adapters. An RFP with weeks-scale due dates died with the process. Fix chosen: **build the seam** (not "record the exception").

**Decisions.**
1. **Generic exchange-record store, not an RFP table.** `bridge/exchange_store.py`: an `ExchangeRecord` is a JSON document + version keyed by `(kind, ref)` (`kind="rfp"`, `ref=rfp_id`), carrying the exchange's A2A `context_id` as an indexed, **non-unique** column. `context_id` is NOT the key: `tests/email_fixtures.make_board` opens several RFPs on one `context_id`, and identity stays the A2A context token (no minted exchange id).
2. **One SQL implementation both sides.** `SqlExchangeStore` (SQLAlchemy Core over an `AsyncEngine`, table `bridge_exchange_records`, distinct from the ADK/a2a tables): local `sqlite+aiosqlite:///…` from `BRIDGE_EXCHANGE_DB_URL`, GCP `postgresql+asyncpg://…` from the existing `DATABASE_URL` (same Cloud SQL DB as sessions/task_store; no terraform change). `InMemoryExchangeStore` is the local no-DSN default (mirrors `LocalSessions`). GCP refuses in-memory via `_resolve_dsn`.
3. **Sync API over a private loop thread.** Every board caller is sync and the write sites rely on "fresh read → apply → put with no `await` between"; only async drivers are installed (no pg8000/psycopg added, no deprecated `async_fallback`). The SQL store runs its engine on a daemon `exchange-store` thread and blocks callers via `run_coroutine_threadsafe(...).result(timeout=30)`; a call from the store thread itself raises (would deadlock). **Trade-off:** a board read blocks the calling event loop for one DB round-trip — fine at RFP scale; S0-arch-17 may revisit.
4. **CAS at the seam.** `put(..., expected_version=N)`: `0` = create-only (INSERT; `IntegrityError` → `StaleExchangeWrite`); `N>0` = `UPDATE … WHERE version=N` (rowcount 0 → `StaleExchangeWrite`, also for a missing record — never an implicit create). No unconditional upsert.
5. **`RfpBoard` keeps its surface; versions tracked per THREAD** (`threading.local`). Read-through, no cache (a cache re-creates the per-replica split). `put` expects the version this thread last read/wrote (`0` if never seen). Thread-local because FastAPI runs sync routes on a threadpool: a concurrent read on another thread must not bump the version an in-flight async write expects.
6. **`board.rfps` is a read-only `Mapping` view** (`_RfpMapping`): `values()`/`items()` = one `store.list`; no `__setitem__`. No call site changed.
7. **`RfpBoard(store=None)` defaults to `InMemoryExchangeStore()` directly** (the `param or InMemory…()` idiom the service-wiring guard accepts — `ExchangeStore` defines no `__len__`/`__bool__`, so it is always truthy), not env resolution, so the many bare `RfpBoard(policy=…)` constructions stay pure. Hosts wire `exchange_store_from_env()`; the RFP demo host does (`build_rfp_demo_app(exchange_store=…)`).
8. **No 409 mapping for `StaleExchangeWrite` here.** An unhandled conflict is a 500 on the chase/upload routes; in the email drain it propagates, the mail stays pending and the next drain tick retries. Retry/409 policy belongs to **S0-arch-17** (the multi-replica host).
9. **`ProgramBoard` stays in-memory** (its deferral paragraph now scoped to programs only).

**Gotchas.**
- **Lazy everything.** Thread, loop, engine and `create_all` start on the FIRST operation, never in `__init__`: `get_adapter` builds adapters with no env, and building a GCP store with an asyncpg DSN must not import asyncpg (`test_gcp_exchange_store_refuses_in_memory_and_sync_pg` asserts it). `create_all` retries once (a concurrent handle may win the CREATE race). `close()` is idempotent, a no-op if never opened, and final (a closed store raises on use). A process that never closes exits cleanly (daemon thread; each op commits in its own transaction).
- **Seed only when absent.** `build_rfp_demo_app` seeds `acme-vb-2027` only if the store lacks it — over a durable DSN a restart would otherwise hit `StaleExchangeWrite` on `open_rfp`'s create-only put (and would erase the durable state anyway).
- **JSON round-trip in both stores** (on write and read, `allow_nan=False`): a non-serializable doc fails identically (`TypeError`) and returned docs never alias stored state.
- **Lazy `bridge.seams` import** in `exchange_store_from_env` (the circular-import trap, S0-bug-3 #7): `bridge.dashboard` → `board` → `bridge.exchange_store` has stdlib-only top-level imports.
- No existing test needed a re-read fix: every write site and fixture already follows fresh-read → put in one thread.

**Deviations from the plan.** `RfpBoard` uses `store or InMemoryExchangeStore()` instead of an `is not None` ternary (the service-wiring guard only accepts the `or` idiom). Extra tests beyond the plan: an update of a missing record is stale, empty kind/ref → `ValueError`, arg-wins-over-env, `exchange_store_from_env` resolution incl. the gcp refusal, a call from inside a running event loop, close idempotence/finality, `board.rfps` read-only, create-existing-from-another-board refused.

**Handoff to S0-arch-17.** The deployed board-owning host wires `exchange_store_from_env()` with `BRIDGE_SEAM_EXCHANGE_STORE=gcp` (or `BRIDGE_SEAM_MODE=gcp`) over `DATABASE_URL`; decide the `StaleExchangeWrite` → 409 / retry policy; consider closing the store on host shutdown.

## 2026-10-01 — S0-arch-15: the core names no skill — RfpPolicy from the process skill; demo hosts fenced in bridge/demos/

**Context.** `bridge/rfp.py` hardcoded `PROCESS = "benefits-rfp"` / `QUOTE_DOCTYPE = "benefits-quote"`, imported by `bridge/edge/email_ingress.py`, `bridge/portal/rfp_checklist.py` and the demo host `bridge/dashboard/rfp_demo.py` (which also shipped demo seeding inside the core). That contradicts `design/bridge.md` ("skills plus a thin driver … with no change to the core") and `design/patterns.md` ("a new industry is new skills, not new flow"). Done = a second emergent-Collect use case runs with zero `bridge/` edits, and demo-host glue sits in a fenced package.

**Decisions.**
1. **The process skill supplies everything domain-specific, via `RfpPolicy`.** The frozen dataclass gains `process` (the skill name), `candidate_doctypes` (SKILL.md `metadata.bridge-candidate-doctypes`, space-separated, never empty), `item_field` (policy `rfp.item_field`; optional, default `product_line` = the cell's native field; a present value must be a non-empty `str`) and a `doctype_hint` property (the sole candidate, else `None`). New fields carry no defaults (nothing constructs `RfpPolicy(` directly; an empty label space is impossible, not silently `[]`).
2. **`load_rfp_policy(skill_name, *, skills_dir)` — name required.** Validation order kind → pattern → candidates → `rfp:` block, each a `ValueError` naming the skill: `benefits-quote` fails on `bridge-kind 'doctype'`, `benefits-procurement` on `bridge-pattern 'negotiate'`, `address-proof` (bounded collect) on the missing `rfp:` block.
3. **Frontmatter via a pure helper `bridge.sla._load_skill_frontmatter`, not `bridge.edge.skills`** — `bridge.rfp` stays free of `bridge.edge` (its package `__init__` pulls a2a/ADK). `_load_policy_doc` now calls it; behavior and error messages unchanged. Same filesystem `SKILLS_DIR` posture as the sla / disposition / negotiate loaders — none of them reads through the `skill_registry` seam; not changed here.
4. **`PROCESS` / `QUOTE_DOCTYPE` deleted, no alias.** `receive` matches on `key_fields[policy.item_field]`; `to_exchange_turn` hints `policy.doctype_hint` (benefits-rfp → still `"benefits-quote"`, wire-identical); the email drain and the checklist upload gate on `board.policy.candidate_doctypes`; `render_rfp_checklist` takes a required `candidate_doctypes` keyword for the upload action's `accept`. `bridge/email_ingress/correlation.py` needed no change — its `bridge.rfp` import is `TYPE_CHECKING`-only and names the generic `RfpCollection` / `RfpLeg`.
5. **One process per board, enforced at the single writer.** `RfpBoard.put` raises `ValueError` (before touching the store or the thread-local version map) for an RFP whose `process` is not `policy.process` — otherwise an RFP of process X would be projected with Y's ladder / label space. A second use case = its own skill + its own board (host config). No multi-process board.
6. **No stored-schema / wire change.** `RfpCollection` already carries `process`, so S0-arch-14 durable records decode unchanged; `RfpStatusView`, `RFP_STATUS_API_VERSION`, `contract/` untouched.
7. **Demo hosts move to the fenced package `bridge/demos/`** (`git mv bridge/dashboard/rfp_demo.py bridge/demos/rfp_demo.py`, no shim). It may name demo skills (`DEMO_PROCESS = "benefits-rfp"`), seed synthetic data and read `wiki/evals/`; nothing outside it imports it. Why inside `bridge/`: `agents/` never imports `bridge/` (S0-arch-2 guard), and a new top-level package would change the repo shape in `CLAUDE.md`. `bridge/demos/__init__.py` imports nothing (fresh-process imports stay light; the S0-bug-3 #7 circular-import trap stays closed — the demo still imports `bridge.edge.email_ingress` lazily inside its builder).
8. **Two guard tests (`tests/test_isolation_guard.py`).** `test_core_never_imports_demo_hosts` (incl. `from bridge import demos`) and `test_core_names_no_skill`: no non-docstring string constant in `bridge/**` outside `bridge/demos/` EQUALS an installed skill name (`skills/*/SKILL.md` dir names) — equality, not substring, so prose like normalize.py's "benefits-quote extractions carry…" passes. Allowlist (file → reason): `bridge/edge/mock.py` (mock Bridge scripted from the Address corpus), `bridge/calibration.py` (corpus → label space), `bridge/edge/skills.py` (`KNOWN_DOCTYPES` unresolved-process fallback → **S0-arch-18**). The test also fails when an allowlisted file stops naming a skill. Both have synthetic positive controls.

**Residuals (known, recorded in `design/architecture.md` "Core vs. demo hosts").** The RFP-named API vocabulary (`/rfps`, `RfpCell.product_line`, `FiledAttachment.product_line`, "carriers" for parties, the chase copy's "quote(s)") stays — renaming it is an API/contract change, not a skill binding. The three allowlisted literals stay; `KNOWN_DOCTYPES` is filed as S0-arch-18.

**Deviations from the plan.**
- `bridge/edge/email_ingress.py` had a SECOND hardcoded item key the plan missed: the drain's `FiledAttachment.product_line` read `entry.key_fields.get("product_line")`. It now reads `self.board.policy.item_field` (the second-use-case test asserts `product_line == "W9"` on the outcome — the field name is wire vocabulary, the value is the skill's item key).
- `bridge/outbound.py` (not in the plan's file list): a docstring-only reword (`e.g. ``"benefits-procurement"``` → `e.g. the ``benefits-procurement`` process skill`) so the T2 grep `["'](benefits-rfp|benefits-quote|benefits-procurement)["']` over the core returns only the allowlisted `bridge/calibration.py` line, as the acceptance criterion states. No behavior change.
- `TODO.md` S0-arch-15 is NOT flipped here (CRITICAL task — the reviewer flips + commits). S0-arch-18 filed; `s0-arch` counter → 18.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q` → 1655 passed / 96 skipped (baseline at HEAD in a clean worktree: 1634 / 96 — +21 new: 7 second-use-case, 9 `TestPolicyFromSkill`, 4 isolation-guard, 1 `bridge.demos` fresh-process param; no new skips). Fresh-process `import` of `bridge.rfp`, `bridge.demos`, `bridge.demos.rfp_demo`, `bridge.edge.email_ingress`, `bridge.dashboard`, `bridge.portal` each succeed; `import bridge.rfp` loads no `bridge.edge` (its transitive ADK import set is identical to HEAD's).
- T2: (1) the core grep returns only `bridge/calibration.py:50`; `bridge.rfp` has no `PROCESS` / `QUOTE_DOCTYPE`. (2) `load_rfp_policy('benefits-rfp')` → `benefits-rfp ('benefits-quote',) product_line benefits-quote`; `benefits-procurement` / `benefits-quote` / `address-proof` raise the pattern / kind / missing-`rfp:` `ValueError`s. (3) `tests/test_rfp_second_use_case.py` 7/7: a synthetic `supplier-onboarding` skill in `tmp_path` (labels `w9-form insurance-cert`, `item_field: credential`) runs through `open_rfp`, the board (process guard), `to_exchange_turn` (`doctype_hint is None`), `receive` (matched on `credential`), the `EmailProcessor` drain (`w9-form` → received; an out-of-label-space `benefits-quote` → incomplete `unsupported_doctype`), `checklist_for` (`accept == ["w9-form", "insurance-cert"]`) and `GET /rfps/{id}` (`process == "supplier-onboarding"`). (4) the guard tests pass; appending `X = "benefits-rfp"` to `bridge/rfp.py` fails `test_core_names_no_skill` (`bridge/rfp.py:917: 'benefits-rfp'`), `from bridge import demos` in `bridge/sla.py` fails `test_core_never_imports_demo_hosts` (both reverted). (5) real `uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010`: carrier-a CI received / HI incomplete, carrier-b acknowledged, carrier-c invited, every carrier with a `reply_address`; carrier-c checklist `accept == ['benefits-quote']`; `land` → `landed <key>`, `drain --url http://127.0.0.1:8010/ops` → `filed <key> acme-vb-2027 carrier-c filed=2 unreadable=1`, `drained 1`; the old `bridge.dashboard.rfp_demo` factory fails ("Could not import module").

**Spec proposal (not applied; spec is protected).** CUJ-13's Driver may cite `tests/test_rfp_second_use_case.py` as evidence that a new emergent-Collect industry is new skills only (no core edit).

## 2026-10-01 — S0-arch-16: the Bridge's best-effort satisfaction proposal (COLL-2), model-interpreted, opt-in

**Context.** `design/adk.md`, `design/skills.md`, `design/collect.md` and `design/disposition.md` said the Bridge reads the process skill's prose satisfaction rule (`metadata.bridge-satisfaction`) and proposes what's outstanding. Nothing in `bridge/` read it; `project_outstanding` listed non-accepted entries and disclaimed COLL-2. Fix chosen: **build it** (not reword). The protected spec requires it: `spec/demos/address-demo.md` ("best-efforts an advisory assessment of what's still outstanding, and chases it"), `spec/demos/demo-suite.md`, COLL-2 in `docs/features.md`. Rewording design/ to the per-entry list would have contradicted spec/.

**Decisions.**
1. **Model-interpreted, never a rule engine.** `bridge/satisfaction_llm.py::LlmSatisfactionAssessor` makes one stateless `BaseLlm.generate_content_async(LlmRequest, stream=False)` call in JSON mode (`response_schema` = `{items: [ProposedItem]}`, temperature 0). No `Runner` or session. A model name becomes a `BaseLlm` through `LLMRegistry.new_llm` on the first assessment, so building the app needs no credentials. A `BaseLlm` instance (a test double) is used as-is. The model resolves like `document_bridge`'s (`resolve_model` → `BRIDGE_MODEL`). No OR/count/distinct-issuer logic is hand-coded in `bridge/`, and the prompt is skill-agnostic (the S0-arch-15 guard holds).
2. **Opt-in, resolved once.** `BRIDGE_SATISFACTION`: unset/blank/`off` → no assessor; `llm` → the model assessor; anything else → `ValueError` at `build_a2a_app` (exact, case-sensitive; mirrors `resolve_edge_mode`). `build_a2a_app(satisfaction=…)`: an injected assessor wins, then an explicit mode string, then the env. It is bound with `CollectExecutor.bind_assessor`, and a bare executor has none. The default posture is byte-identical and model-free. The T1 suite, conformance and the hybrid `ScriptedLlm(max_calls=0)` G3 tests are unchanged. Not a seam (no local/GCP pair).
3. **No contract change.** The proposal rides `CollectionStatus.outstanding` in the COLL-3 vocabulary (`required`/`optional`/`satisfied`, never `waived`). Every item has `reason_code="satisfaction_proposal"`. `done` is **always** False. `bridge.satisfaction.looks_done(outstanding)` = ≥1 item and nothing `required` (advisory; the app's gate owns done — e.g. `agents.address.gate.is_satisfied`).
4. **Replace-on-success, fall back on anything else.** `propose_outstanding` returns the per-entry `project_outstanding(entries)` list in these cases: no rule, no assessor, any exception (including timeout, `DEFAULT_TIMEOUT_S=30`), invalid JSON/schema, or an empty proposal after sanitizing. Sanitizing drops blank items, nulls hints outside the label space, truncates messages to 300 chars and caps the list at 20 items. A failure logs only the exception type, never ledger contents. Per-document reasons stay on `entries[*]`.
5. **Data minimization.** The model sees `doc_id`, `doctype`, `disposition`, canonical `issuer`, `reason_code` and `message` per entry, plus the rule and the label space. It never sees `key_fields`, `tables`, confidences or artifact refs (a test asserts the synthetic name is absent from the request).
6. **Where.** The edge executor's deterministic legs only: R2 Collector turns (`_execute_collect`, after the artifact-URI rewrite and before the HITL branch, so the degrade path carries it) and Path-A turns. The LLM leg (R4) and the HITL resume projections keep the per-entry list. The `Collector` class stays model-free (composition happens in the executor). `_propose` checks for the assessor FIRST, so the default edge never reads skills for this. Task state is untouched (it keys on `terminal`).
7. **Scope = the turn's ledger.** The Collector is one-turn; no cross-turn cumulative Bridge ledger was built.
8. **`summarize_turn` counts only `required`/`optional`** as outstanding. Per-entry items are all `required`, so every existing summary string is unchanged.
9. **Skill read.** `SkillMeta.satisfaction` (from `metadata.bridge-satisfaction`, stripped, default `""`) and `bridge.edge.skills.satisfaction_for(process)` (unresolved → `""`, never assessed). It is not re-exported from `bridge.edge`, because the sibling `hitl_mode_for` is not either.

**Deviations from the plan.** (a) Sanitizing runs inside the same `try` as the assessor call, and each item goes through `ProposedItem.model_validate`. A misbehaving injected assessor that returns raw dicts or garbage therefore also falls back instead of raising. (b) The assessor prompt adds one sentence: when the accepted entries partly meet an alternative, chase completing that alternative. Without it, the live model sometimes chased a gov-id for a single accepted bill. COLL-2's acceptance says "1 bill → chases a 2nd distinct-issuer". (c) `resolve_assessor` raises `TypeError` for an injected non-str object without `assess`. (d) `CollectExecutor.assessor` read-only property added (mirrors `mode`/`llm_leg`).

**Not wired (known, no task filed).** `BRIDGE_SATISFACTION=llm` is not set in terraform for the deployed runtime. Nothing in `bridge/` chases from `status.outstanding` today (the SLA ladder is policy-driven), so the proposal is surfaced to the app but does not yet drive the nudge copy.

**Verification.** `tests/test_satisfaction.py`: 29 model-free tests + 1 live test gated on `BRIDGE_TEST_GEMINI=1`. The live test passed against Vertex `gemini-2.5-flash`: gov-id → looks done; one bill → `required` `utility-bill` "…from a different company than 'power-co'"; two same-issuer bills → not looks done; distinct issuers → looks done; `done` False throughout.

## 2026-10-01 — S0-arch-17: a deployed RFP board host binds the email processor; Cloud Scheduler drives `/email/drain`

**Gap.** S0-bug-3 bound the `EmailProcessor` on the host that owns the RFP board, but no deployed service owned one: the A2A runtime (`serve_container` → `build_a2a_app`) holds no board, so the landing bucket the public webhook writes into was drained by nothing deployed. S0-arch-14 made the board durable and cross-replica (Cloud SQL `bridge_exchange_records`, CAS), which is what makes a deployed board host safe.

**Decisions.**
1. **A new deployed host, not a mount on the A2A runtime.** The A2A edge is Gateway-public; the RFP read API, the drain and the outcome markers (sender / subject / RFP / carrier) are internal-zone. New `bridge/dashboard/board_host.py`: `build_rfp_board_app(...)` + `serve_rfp_board_container()`, deployed as a third Cloud Run service on the same image, `<service_name>-rfp-board`, running as `bridge-runtime` (which already holds cloudsql.client, the artifacts bucket, the email bucket objectViewer + objectCreator and the token-key accessor; no new SA grant).
2. **S0-bug-3 #2, refined.** That rule fences a board that is not the source of truth. This board is a read-through view of the durable shared `exchange_store` record every RFP writer uses, so a mail that correlates to nothing on it correlates to nothing anywhere. Enforced: `build_rfp_board_app` raises `RuntimeError` (naming `BRIDGE_EXCHANGE_DB_URL` / `DATABASE_URL`) when a processor would bind over an `InMemoryExchangeStore`. No email env → no processor → in-memory is allowed (read API only).
3. **Process from env, never a literal.** `BRIDGE_RFP_PROCESS` (`RFP_PROCESS_ENV`); explicit `process=` wins; blank → `RuntimeError` naming the env; `load_rfp_policy`'s `ValueError` for a non-emergent-Collect skill propagates. One process per board. `test_core_names_no_skill` stays green.
4. **Trigger = Cloud Scheduler → `POST /email/drain`**, not a GCS-finalize push (a push per object fires on the processor's own attachment/marker writes and races the same key across instances; a tick drains in landing order — the bucket IS the queue). The board service sets `BRIDGE_EMAIL_DRAIN_INTERVAL=0`; Scheduler `retry_count = 0`, schedule default `* * * * *`, `attempt_deadline = 300s`.
5. **Ingress + auth.** Board ingress default `INGRESS_TRAFFIC_INTERNAL_ONLY` (validated root + module var `rfp_board_ingress`), no `allUsers`, `roles/run.invoker` for `bridge-runtime` alone (the Scheduler's OIDC identity, audience = the board URL). Auth is Cloud Run IAM, like the `/followups` callback.
6. **Cross-replica drain safety.** (a) `rfp_board_max_instances` default 1 (drains serialize on the in-process lock). (b) **CAS retry** in `EmailProcessor.process` phase 3: on `StaleExchangeWrite`, fresh read → apply → put again, up to `_STALE_RETRIES = 3`; exhausted → re-raise (no marker, key stays pending, next tick retries). Extraction / artifact persist are not redone. (c) **Re-file guard**: if a fresh read already holds any `email-<key>-<i>` doc id of this mail, skip the mutation + put and write the marker from the STORED entries. This upgrades "at-least-once may duplicate a version" to "no duplicate version" for mail with ≥1 extracted attachment. Residuals: an unreadable-only mail re-applied may append one audit-only event (no cell moves); a retry after a crash in phase 2–3 may add one extra artifact version; two replicas on the same key at once may both extract.
7. **`StaleExchangeWrite` → HTTP 409** (`{"detail": "RFP '<ref>' changed concurrently; retry"}`) on the dashboard app (chase, `/email/drain`) and the portal app (upload into a row). On `/email/drain` an exhausted retry is a logged Scheduler failure the next tick absorbs.
8. **The outer lifespan closes the store** (`await asyncio.to_thread(board.store.close)` in a `finally`), composed with the drain loop when interval > 0.
9. **Not included: the deployed RFP-open path** (the broker agent's `CollectRequest` → `open_rfp` → durable board). The board host serves and drains onto whatever the shared store holds; until an open path exists, it holds none. Filed **S0-arch-19**. Also residual: the board host's chase route needs the a2a_client gcp env (`BRIDGE_GATEWAY_HOST`, `BRIDGE_RUNTIME_SA_EMAIL`), else 503 (lazily-built, unchanged); the `SqlExchangeStore` sync facade still blocks the loop one round-trip per board call (S0-arch-14 #3).
10. **The processor env moves off the A2A runtime.** `google_cloud_run_v2_service.bridge` no longer carries `BRIDGE_EMAIL_BUCKET/MAILBOX/TOKEN_SECRET` (dead config there); `google_cloud_run_v2_service.rfp_board` does. Per-SA bucket/secret grants are unchanged. A root `check "email_ingress_has_a_drain"` warns on `enable_email_ingress` without `enable_rfp_board`. `scripts/deploy.sh` enables `cloudscheduler.googleapis.com`.

**Gotchas.**
- **The Scheduler needs the board URL at plan time without a cycle.** Uses the deterministic Cloud Run URL `https://<name>-<project-number>.<region>.run.app` (`data.google_project.this.number`) rather than `google_cloud_run_v2_service.rfp_board[0].uri` (unknown under `mock_provider`, and couples the job to the service's apply).
- **Mount at the root + one loop.** `app.mount("", dashboard)` keeps `/rfps`, `/email/drain`, … at the root; Starlette never runs a mounted sub-app's lifespan, so the loop + store close live on the outer app (one loop per process).
- **Scheduler vs internal-only ingress.** Scheduler in the same project counts as internal for Cloud Run ingress; if a live deploy shows Scheduler 403/404 from ingress, set `rfp_board_ingress = "INGRESS_TRAFFIC_ALL"` — IAM still gates.
- **Interval 0 on Cloud Run.** Request-based CPU starves a background loop between requests; the drain runs inside the Scheduler's request.
- `RfpBoard` CAS versions are thread-local; the retry re-reads on the same thread, with no `await` between read and put.

**Deviations from the plan.**
- Dropped the planned `email=` kwarg on `build_rfp_board_app`: the host builds its board internally, so an injected processor's `.board` could never be that board (`build_dashboard_app` would reject it). Tests inject `extraction=` and env instead.
- Added a root `rfp_board_ingress` variable (passed through to the module) alongside the module var, so the ingress fallback is operable from tfvars.
- The helper is `_filed(att, entry)` — `artifact_uri` comes from `entry.artifact`, so the re-file guard builds the marker from stored entries without the local `ArtifactRef`.
- `terraform/tests/email.tftest.hcl`: the runtime-enabled run now sets `enable_rfp_board` + `rfp_process` and asserts the token `secret_key_ref` and `BRIDGE_EMAIL_*` set on `rfp_board[0]` (incl. `BRIDGE_EMAIL_DRAIN_INTERVAL`) and NO `BRIDGE_EMAIL_*` on the A2A runtime; the root-enabled run sets the board on (the new check).
- `TODO.md` S0-arch-17 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q` → 1699 passed / 97 skipped (baseline 1684 / 97: +14 in `tests/test_rfp_board_host.py`, +1 fresh-import param; no new skips). `terraform fmt -check -recursive && terraform validate && terraform test` → 39 passed (+8 in `tests/rfp_board.tftest.hcl`). Fresh-process imports of `bridge.dashboard.board_host`, `bridge.edge.email_ingress`, `bridge.dashboard`, `bridge.demos.rfp_demo`, `bridge.portal.server` succeed.
- T2 (real `uvicorn` on `build_rfp_board_app(extraction=FixtureExtractor(...))`, sqlite DSN, `BRIDGE_EMAIL_DRAIN_INTERVAL=0`):
  - PASS — a separate process opened acme-vb-2027 onto `SqlExchangeStore` and closed it (exit 0).
  - PASS — host `GET /rfps/acme-vb-2027` 200; carriers a/b/c show `rfp-quotes+<token>@broker.example`; `/rfps` lists only the writer's RFP (host seeds nothing).
  - PASS — `land reply.eml` → `landed <key>`.
  - PASS — `POST /email/drain` → one `filed` outcome (acme-vb-2027 / carrier-c, CI accepted, HI pending `flagged_fields`, xlsx `unsupported_media_type`); `drain --url` → `drained 0`.
  - PASS — carrier-c CI `received`, HI `incomplete`; `/email/outcomes/<key>` `filed`, unreadable `unsupported_media_type`.
  - PASS — SIGINT → "Application shutdown complete", no traceback; a fresh process over the DSN sees CI `received`; a restarted host shows the same state, outcome `filed`, `drain` → `drained 0`.
  - PASS — `env -u BRIDGE_EXCHANGE_DB_URL` → `RuntimeError` naming `BRIDGE_EXCHANGE_DB_URL`; `env -u BRIDGE_RFP_PROCESS` → `RuntimeError` naming `BRIDGE_RFP_PROCESS`.
  - PASS — `terraform test -filter=tests/rfp_board.tftest.hcl` 8/8: Scheduler POSTs `https://bridge-runtime-rfp-board-<n>.us-central1.run.app/email/drain` with an OIDC token for bridge-runtime (audience = board URL), retry 0; the A2A runtime carries no `BRIDGE_EMAIL_*`.
  - PASS — no duplicate version on re-drain after a crash between put and marker (negative control: disabling the guard fails the test); CAS conflict retried keeping both writes; exhausted retry leaves the mail pending and is a 409 on the host; chase + portal upload conflicts are 409.

**Spec proposal (not applied; spec is protected).** The email-intake CUJ's deployed Driver can name the board host path (Scheduler → `<service>-rfp-board` `POST /email/drain`) once S0-arch-19 provides a deployed RFP opener; until then its deployed leg is unprovable end-to-end.

## 2026-10-01 — S0-arch-18: an unresolved process gets an empty label space

**Problem.** `bridge/edge/skills.py` `KNOWN_DOCTYPES = ("gov-id", "utility-bill")` was the P2-1 fallback `candidate_doctypes_for` returned when no loaded process skill of that name declared a candidate list. An unknown process was therefore dispositioned against Address's doctypes: a clean gov-id sent under `process="unknown-process"` came back **accepted**. It was also the last behavioral skill-name binding in the core (allowlisted in `tests/test_isolation_guard.py::SKILL_NAME_ALLOWLIST`).

**Decision: empty label space, not a refusal.** `candidate_doctypes_for(process)` returns `list(skill.candidate_doctypes)` for the first loaded `kind == "process"` skill of that name, else `[]`. `KNOWN_DOCTYPES` is deleted. Why:
1. **Wire-compatible.** No new error path or JSON-RPC error; consumers still get a COMPLETED `ExchangeTurn`.
2. **Fails closed through the existing gate.** `dispose()` step 1 (`is_in_label_space`) rejects every document `rejected / Gate.unsupported / unsupported_doctype`; nothing can be accepted.
3. **Matches the neutral unresolved-process defaults beside it:** `hitl_mode_for` → `"report"`, `satisfaction_for` → `""`.

**"Unresolved" is two cases, both `[]`:** (a) no loaded process skill with that name; (b) a process skill whose SKILL.md omits `bridge-candidate-doctypes` (`SkillMeta.candidate_doctypes == ()`).

**R4 `[]` vs `None` is unchanged.** `_llm_label_space` returns `[]` for a text-resolved process with no candidates — gate calls are pinned with `[]` and reject as unsupported (legal since S3-edge-4, tested `is None`). `None` (the text resolver found no process) still refuses with `label_space_missing`.

**`dispose` empty-list message.** An empty label space now reaches the unsupported branch, which rendered `"... Accepted types: ."`. Only when `candidate_doctypes` is empty the message is `"The document type '<doctype>' is not supported for this process. This process accepts no document types."`; `disposition` / `gate` / `reason_code` are unchanged and the non-empty message is byte-identical (the corpus goldens relay it; `wiki/evals/` untouched).

**Allowlist.** `SKILL_NAME_ALLOWLIST` is down to two entries: `bridge/edge/mock.py`, `bridge/calibration.py`. The literal and its allowlist entry had to go in the same change (the guard fails on a stale entry).

**Ripple.** The portal's `start_session` for an unknown process now stores `candidate_doctypes=()` and every upload is rejected unsupported. Pre-flight showed only two tests asserted the old fallback (`tests/address/test_skills.py::test_candidate_doctypes_match`, `tests/test_edge_a2a.py::test_consolidated_candidate_doctypes`); all real callers pass installed process skills with candidate lists, so the golden/corpus runs never depended on it.

## 2026-10-01 — S0-arch-19: the deployed A2A runtime opens RFPs onto the durable board

**Gap.** S0-arch-17 deployed the internal RFP board host over the durable exchange store, but the only RFP opener was the fenced local demo host (seed-only). On the deployed A2A runtime, a broker agent's emergent-Collect `CollectRequest` went to the `Collector`, so the shared store never held an RFP and every drained carrier reply was quarantined `no_exchange_token`.

**Decisions.**
1. **The A2A runtime is the opener.** The opener lives on the runtime, not on the board host. The broker agent already speaks A2A to the runtime, and the board host stays internal-zone (read API, drain, outcome markers). The shared durable `exchange_store` is the hand-off between the two.
   - New `bridge/edge/rfp_open.py`. `build_a2a_app(rfp_board=None)` resolves `rfp_board or rfp_board_from_env()` (the wiring-guard `or` idiom), binds it with `CollectExecutor.bind_rfp` and exposes it as `app.state.rfp_board`.
   - In `_execute_collect`, after `decode_intake`, a `CollectRequest` whose `process` is the board's process goes to `_execute_rfp`. This happens in both edge modes and makes no LLM call. Other processes keep plain R2.
2. **Opener-only board, durable or nothing.** `rfp_board_from_env()` reads `BRIDGE_RFP_PROCESS` (`RFP_PROCESS_ENV`, moved to `bridge/rfp.py`; `board_host` re-exports it).
   - Unset or blank → `None`; the edge is unchanged.
   - A skill that is not emergent-Collect → `load_rfp_policy`'s `ValueError` at build.
   - An `InMemoryExchangeStore` → `RuntimeError` naming `BRIDGE_EXCHANGE_DB_URL` / `DATABASE_URL`, mirroring S0-arch-17 #2.
   - An injected board is used as-is.
   - The board has no processor, no read API and no `BRIDGE_EMAIL_*`. Reply addresses are minted on the board host from `rfp_id` + party.
3. **`rfp_id` = the exchange context.** It is `request.context_id`, else the A2A context. `CollectRequest` carries no RFP id, and adding one would be a contract change.
   - The returned `ExchangeTurn.context_id` IS the RFP id (`GET /rfps/{context_id}`). A follow-up on the same context addresses the same RFP, and a retried open is idempotent.
   - The id must match `RFP_CONTEXT_RE` (`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`), because it is a URL path segment and the reply token's HMAC input.
4. **One sync call on one worker thread.** `open_or_update_rfp` does read → open / `update_requirements` / status read → put → `to_exchange_turn`. The executor runs it via `asyncio.to_thread` because the board's CAS versions are thread-local (S0-arch-14 #5), so read and put share a thread and the event loop never blocks on the DB.
   - An identical requirements list is a no-op: no put, no audit event, no version bump.
   - A `StaleExchangeWrite` re-reads and re-applies, up to 3 attempts, so a racing create retries as an update. When the attempts run out it is re-raised.
5. **Refusals are JSON-RPC errors raised in `execute` before any task event.** Verified empirically: on a new task the SDK returns the error with no task row, so no `admit` hook was needed.
   - inline or URL file part → InvalidParamsError `rfp_submissions_unsupported: …` (quotes arrive by email or the carrier checklist);
   - bad context id → InvalidParamsError `invalid_rfp_context: …`;
   - invalid open or update → InvalidParamsError `invalid_rfp_request: <ValueError>`;
   - CAS exhausted → InvalidRequestError `rfp_changed_concurrently: …`.
6. **Terraform.**
   - `enable_rfp_board` + `rfp_process` puts `BRIDGE_RFP_PROCESS` on `google_cloud_run_v2_service.bridge`, which still has no `BRIDGE_EMAIL_*`.
   - The board service's `board_plain_env` gains `BRIDGE_RUNTIME_SA_EMAIL` and `BRIDGE_GATEWAY_HOST` (new root + module var `gateway_host`, default `""`, filtered out when empty). This folds in the S0-arch-17 residual (the chase route 503'd without the a2a_client gcp env).
7. **No lifespan on `build_a2a_app`.** The `SqlExchangeStore` loop thread is a daemon and each op commits (S0-arch-14), so a runtime that never closes the store exits cleanly. Tests close `app.state.rfp_board.store` explicitly.

**Gotchas.**
- `bridge.edge.*` must not import `bridge.dashboard` at module top (the S0-bug-3 #7 circular-import trap). `RfpBoard` is imported under `TYPE_CHECKING` plus lazily in `rfp_board_from_env`. `bridge.edge.rfp_open`, `bridge.edge.server` and `bridge.edge.executor` were added to the fresh-process import guard (`tests/test_email_served.py`).
- The env-built board runs on the wall clock. Tests use a real future `due_at`.
- The summary text `collected 0 documents; 6 outstanding` is fine for an open. The turn is non-terminal, so the task is `INPUT_REQUIRED`.

**Deviations from the plan.**
- `InvalidRfpContext(ValueError)` subclass added, so the executor can tell a bad context id apart from an `open_rfp` `ValueError`.
- The test `clean_env` fixture also clears `BRIDGE_EDGE_MODE`, `BRIDGE_SATISFACTION`, `BRIDGE_TASK_DB_URL` and `BRIDGE_SEAM_TASK_STORE`, so ambient env cannot leak into the built app.
- `terraform/main.tf` comment wording only. `ruff format` was applied to the two new files only; the pre-existing format drift in `executor.py` / `server.py` is left alone.

**Residuals.**
- A changed `due_at` on a follow-up is ignored, because it is fixed at open.
- The edge has no per-party authorization on who may open or update an RFP. That is the Gateway's job, as on the rest of the A2A edge.
- A refusal sent with an existing `task_id` marks that task FAILED, like every `execute`-time refusal (`design/a2a-edge.md` pre-dispatch guard).
- On the A2A runtime the sync store round-trip now runs on a worker thread. The board host's `SqlExchangeStore` facade still blocks its loop for one round-trip per board call (S0-arch-14 residual).

`TODO.md` S0-arch-19 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.

**Verification.**
- T1:
  - `uv run ruff check` is clean.
  - `uv run pytest -q` → 1737 passed / 97 skipped (baseline 1704 / 97: +30 in `tests/test_rfp_a2a_open.py`, +3 fresh-import params; no new skips). Negative control: with `bind_rfp(None)`, 20 of the 30 new tests fail.
  - `terraform fmt -check -recursive && terraform validate && terraform test` → 39 passed.
- T2: two real `uvicorn` processes on one sqlite DSN, both with `BRIDGE_RFP_PROCESS=benefits-rfp`: A2A `build_a2a_app` on :8020, and `build_rfp_board_app(extraction=FixtureExtractor(conformance.json))` on :8021 with email env and drain interval 0. Every check passed:
  - **Open.** Raw `SendMessage` of a 3 carriers × 2 lines open → `TASK_STATE_INPUT_REQUIRED` with 6 outstanding, all `invited`.
  - **Board.** `/rfps` lists exactly one RFP, `rfp_id` == the context id, and every carrier's `reply_address` is `rfp-quotes+<token>@broker.example`.
  - **Mail.** `land reply.eml` → `landed <key>`. `drain --url :8021` → `filed <key> <context_id> carrier-c filed=2 unreadable=1` and `drained 1` (previously `no_exchange_token`).
  - **Follow-up.** A requirements-less `SendMessage` on the same context shows carrier-c CRITICAL_ILLNESS absent from outstanding and HOSPITAL_INDEMNITY `incomplete` (5 outstanding).
  - **Durability.** After a SIGINT restart of the A2A runtime, the status read gives the same answer.
  - **Fail-closed start.**
    - `env -u BRIDGE_EXCHANGE_DB_URL` → `RuntimeError` naming `BRIDGE_EXCHANGE_DB_URL`.
    - `BRIDGE_RFP_PROCESS=address-proof` → `ValueError` (`policy.yaml missing an 'rfp:' block`).
    - Unset → `app.state.rfp_board is None`.
  - **Refusal on the wire.** An open without requirements → JSON-RPC -32602 `invalid_rfp_request: …`, and `/rfps` is unchanged.
  - **Terraform.** `terraform test -filter=tests/rfp_board.tftest.hcl` → 8/8.
  - **Model-free.** The hybrid-mode acceptance test asserts 0 LLM calls.

**Spec proposal (not applied; spec is protected).** Name the email-intake CUJ's deployed Driver `tests/test_rfp_a2a_open.py::test_mail_drained_by_board_host_files_onto_rfp_opened_through_a2a`: an RFP opened through the A2A runtime, a reply landed, drained by the board host and filed onto that RFP.

## 2026-10-01 — S0-debt-3: Address eval parity anchors restated as what tests check

**Problem.** `wiki/evals/address/description.md` ("Consumers (parity)") named two test-held anchors that exist nowhere in the repo: `bridge.extraction.fixture._DEFAULT_TABLE` and `agents.address.portal_server.SAMPLES`. The SAMPLES check was an open `TODO` "deferred to P6" in `tests/address/test_corpus.py`, and `docs/features.md` TEST-2 claimed "fixture ↔ portal samples resolve" as enforced.

**Decision: the corpus *is* the fixture table.** `FixtureExtractor` reads `expected.json` directly (`DEFAULT_CORPUS`), so there is no second table to drift from. The fixture anchor is restated as the checks that already exist: `test_fixture_resolves_every_id` (artifact path + bare id → exact `extraction` block) and `test_collect_path_parity` (disposition / gate / issuer through `Collector`, deterministic on repeat).

**Decision: portal-sample parity is checked from Python, by regex-reading the TSX.** The real portal sample source is `CORPUS_IDS` in `frontend/provider-portal/src/components/UploadPrompt.tsx`; the portal posts the bare id as `artifact_name` and the local fixture extractor resolves it. `test_portal_samples_resolve_to_corpus` reads that const and asserts every id is a corpus id (no duplicates) and resolves through `FixtureExtractor` to its exact `extraction` block. Why Python, not vitest: T1 is `uv run pytest`; a vitest test would not gate the commit. The parse fails loud: a renamed or reshaped const fails with "CORPUS_IDS array not found" (or "is empty"), never a vacuous pass. This is the first test that reads `frontend/` source; it stays local to `test_corpus.py`.

**Scope.** Address only. `RfpChecklist.tsx::QUOTE_ARTIFACTS` (benefits conformance ids) has the same drift shape and is not covered here (possible follow-up). Oracle data (`expected.json`, `timeline.json`, `images/`) and the TSX are unchanged; `description.md` changed in prose only.

**Verification.**
- T1: `uv run ruff check` is clean; `uv run pytest -q` → 1738 passed / 97 skipped (baseline 1737 / 97: +1, no new skips).
- T2: `-k portal_samples` → 1 passed. Mutations (each reverted with `git checkout`): `"bill-powerco-clean"` → `"bill-powerco-renamed"` fails naming `'bill-powerco-renamed'`; `const CORPUS_IDS` → `const SAMPLE_IDS` fails with "CORPUS_IDS array not found". The phantom-anchor grep over `tests/ wiki/evals/ docs/features.md` is empty.

`TODO.md` S0-debt-3 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.

## 2026-10-01 — S0-docs-6: spec/cujs.md proposal (de-status, full Driver ids, CUJ-13 restated, CUJ-15 email intake)

**Context.** `spec/cujs.md` had drifted from its own Drivers and from what ships. Re-verified at implement time (HEAD 61fca8b):
- **(a) Status leakage.** The table's "First cycle" column, the sentence `"First cycle" marks what the Core + Address cycle builds`, "Each **built** CUJ", and the `Driver: — (unbuilt)` convention are progress markers inside a status-free spec. They are also wrong: CUJ-3/11/12 read "later" / "designed; built w/ Benefits" while their Drivers exist and pass.
- **(b) Bare `::name` continuations mis-resolve.** Running `pytest --collect-only` on every Driver/Judge id (a bare `::name` read against the file before it) fails on exactly two:
  - CUJ-5 `::test_hitl_without_park_409` resolves to `tests/address/test_park_resume.py`. The test lives in `tests/test_portal.py`.
  - CUJ-6 `::test_nudge_carries_no_bearer_token` resolves to `tests/test_sla.py`. The test lives in `tests/test_outbound.py`.

  The other seven continuations (CUJ-2 ×2, CUJ-5, CUJ-10, CUJ-12 ×3) resolve by luck of ordering.
- **(c) CUJ-13 is built but specced as unbuilt.** The emergent-Collect capability (agent-mutated Requirements over N carriers × lines, isolated per-carrier legs, adds and waives at runtime, the outstanding delta every turn, `done` never minted by the Bridge) ships as `bridge/rfp.py` + the `benefits-rfp` skill. It is reachable over A2A onto the durable store (S0-arch-19) and runs a second industry from skills only (S0-arch-15). The row still says "added live", which is CUJ-9's journey.
- **(d) Email intake has no CUJ.** It is served on the board host (S0-bug-3), deployable (S0-arch-17) and drains onto RFPs opened over A2A (S0-arch-19).
- The S3-edge-8 proposal (P-1..P-5: CUJ-14 + the CUJ-1/4/5/10 lines) was never applied. Its ids all still collect.

**Decisions.**
1. **One consolidated full-body replacement**, not patches. Dropping a table column touches every row, and the S3-edge-8 lines would conflict with respelled ones. This **supersedes S3-edge-8 P-1..P-5**: same content, ids spelled in full, the `✅` cell dropped. One CUJ-14 Judge wording changes: `INPUT_REQUIRED + lifecycle=escalated` becomes "`INPUT_REQUIRED`, escalated and non-terminal". The test asserts exactly that, and the change keeps the block clear of the status-word check (`cycle` matches "lifecycle").
2. **Frontmatter (lines 1-8) byte-identical.** Its `[[wikilink]]` form belongs to S0-docs-11.
3. **CUJ-13 is restated, not joined by a separate broker-RFP CUJ.** The row now describes the emergent-Collect *capability*, industry-neutral (suppliers × items), without "added live". The Driver cites the broker-RFP tests plus `tests/test_rfp_second_use_case.py` (a synthetic `supplier-onboarding` skill with `w9-form` / `insurance-cert`, the Nimbus shape). *Rejected:* a new broker-RFP CUJ. It would leave CUJ-13 Driver-less while its exact capability passes.
4. **Email intake is CUJ-15.** CUJ-14 is the S3-edge-8 off-script CUJ consolidated here. If the spec owner rejects CUJ-14, renumber CUJ-15 → CUJ-14 and fix the "How to read this" bundle line (`Address … /10/14`, `RFP adds CUJ-9/13/15`).
5. **Status leakage removed completely.** The "First cycle" column and its sentence are dropped (CUJ-3's outbound link moves into the Journey cell). "Each built CUJ" becomes "Each CUJ". `Driver: — (unbuilt)` is replaced by a mechanical rule: a Driver of `—` means no harness node, and `/verify` reports the CUJ not runnable.
6. **CUJ-9 keeps Driver `—` but gains a requirement-level Judge**, so a future "while other exchanges run" test has a target. No "RFP cycle" wording.
7. **Every id is spelled in full as file path + node**, including Judge traps and `(+ …)` companions. File-only references (`tests/address/test_gate.py`, `tests/test_rfp_second_use_case.py`, `tests/test_email_correlation.py`) stay as they are.
8. **Every CUJ-13/CUJ-15 Judge clause was checked against an assertion in the cited test.** The plan draft was tightened in three places:
   - CUJ-13: "every turn proposes exactly the unfilled required items" becomes "the exchange turn proposes exactly the outstanding items (waived ones excluded)". `test_exchange_turn_proposes_the_outstanding_delta` pins one turn's exact set, and the A2A follow-up test pins the waived exclusion (`len(out) == 6`).
   - CUJ-13 adds clauses the tests do assert: the waived item counts as neither ready nor waiting (`(0, 7)`), the A2A update lands on the durable record, and the read API carries the matrix.
   - CUJ-15 adds "no other leg changes" and "a second drain files nothing" (both asserted).
9. **Archive area:** `docs/features/production-readiness.md`, after S0-docs-4 (the Sprint 0 docs precedent).

**SPEC PROPOSAL (not applied — spec/ is protected).** Replace `spec/cujs.md` lines 10-51 (everything after the frontmatter and the blank line that follows it) with:

```markdown
# Critical User Journeys (CUJ catalog)

> The canonical journeys the Bridge must support, each a **user-visible outcome** rather than a component. Design notes cite these by id (e.g. "CUJ-10"); this note is where the ids resolve. Each maps to an [exchange pattern](../design/patterns.md) and lands in a [demo](demos/demo-suite.md).

| CUJ | Journey | Pattern | Demo |
|---|---|---|---|
| **CUJ-1** | Servicer requests a **single document** and receives it normalized (one-turn pull). | [Request](../design/patterns.md) | Address |
| **CUJ-2** | A **human party uploads** via the portal → extract → normalize → disposition ([extraction graph](../design/fulfillment-graph.md)). | Request | Address |
| **CUJ-3** | Bridge **delivers a finished document outward** (Deliver) ([outbound](../design/outbound.md)). | [Deliver](../design/patterns.md) | Benefits |
| **CUJ-4** | **Resubmission loop** — a blurry doc hits the quality gate, party re-sends a clear copy (≤ 3). | Request | Address |
| **CUJ-5** | **HITL review** — a flagged/mid-confidence extraction parks for a human, then resumes. | Request | Address |
| **CUJ-6** | **Proactive chase → escalate** — a silent party is nudged on the SLA ladder, then escalated. | Request + [proactive](../design/proactive.md) | Address |
| **CUJ-7** | **Park & resume across a process restart** — a weeks-scale leg survives redeploy ([deploy-resume](../design/deploy-resume.md)). | any | Address |
| **CUJ-8** | **Live doctype-add** — upload one doctype skill to the running core; the Agent Card regenerates, no redeploy. | — | Address |
| **CUJ-9** | **Live use-case-add** — a whole new demo's skills uploaded live while other exchanges run. | — | RFP |
| **CUJ-10** | **Address bounded Collect** — prove an address with **one gov-ID or two bills from different companies** (the ID-only branch is the fast case). | [Collect](../design/collect.md) (bounded) | Address |
| **CUJ-11** | **Benefits Negotiate over a program** — fan-out → normalize → compare/standard-gap → negotiate → bind → rollup. | [Negotiate](../design/patterns.md) | Benefits |
| **CUJ-12** | **Cross-framework A2A interop** — the ADK Bridge negotiates multi-turn with a **LangGraph** counterparty. | Negotiate | Benefits |
| **CUJ-13** | **RFP emergent Collect** — an agent posts Requirements over **N suppliers × items**; each supplier gets its own isolated leg; the Bridge files each reply per item through disposition and proposes the outstanding delta every turn, while the agent **mutates Requirements at runtime** (adds a supplier or an item, waives one) and alone decides done. | [Collect](../design/collect.md) (emergent) | RFP |
| **CUJ-14** | **Off-script intent mediated agent-to-agent** — a party's agent sends free text instead of a document ("erase my file", "that was the wrong file", "I need a person") and the Bridge's LlmAgent routes it to the right tool: a real erase of every version, an off-script reject, or a durable escalate — never a disposition of a genuine document. | — ([off-script intents](../design/adk.md)) | Address |
| **CUJ-15** | **Email reply intake** — a supplier answers a solicited request **by email** to the per-leg reply address the Bridge advertised; the Bridge correlates the mail to exactly its exchange leg and files each attachment through disposition onto that leg's items — mail it cannot attribute is quarantined, never guessed onto a leg. | [Collect](../design/collect.md) (emergent) | RFP |

**How to read this.** A demo is a *bundle* of CUJs: Address exercises CUJ-1/2/4/5/6/7/8/10/14; Benefits adds CUJ-3/11/12; RFP adds CUJ-9/13/15. The [pattern set](../design/patterns.md) is closed — every CUJ selects an existing built-in flow, never a new one.

## Drivers & Judges (verification wiring)

Each CUJ declares a **Driver:** (the e2e harness node ids `/verify` runs, each a full pytest node id — file path plus node name, never a bare `::name` continuation) and a **Judge:** (the deterministic pass criterion + the hard-fail trap that caps the score at 0 if tripped). `/verify` grades the Driver run against the row's journey text (LLM judge) *and* the Judge criterion (deterministic); both must pass. A Driver of `—` means the CUJ has no harness node; `/verify` reports it not runnable.

- **CUJ-1** — **Driver:** `tests/address/test_golden.py::test_cuj1_path_a_instant_parity_mock_and_real` (+ `tests/address/test_golden_llm_edge.py::test_cuj_driver_parity[scripted-cuj1]`). **Judge:** terminal accepted+satisfied identical on mock and real edge, and identical on the deterministic and hybrid edges; trap: raw bytes on the wire / ADK events read / the hybrid edge calls the model on a structured request.
- **CUJ-2** — **Driver:** `tests/test_portal.py::test_blurry_resubmit_then_clear_hitl_then_resolve_accept` (+ `tests/test_portal.py::test_clean_doc_accepts_one_step`). **Judge:** Path-B upload→extract→dispose reaches accept; trap: relay not byte-identical (`tests/test_portal.py::test_relay_message_reason_code_byte_identical`).
- **CUJ-3** — **Driver:** `tests/test_cuj3_dial_out_deliver.py::test_cuj3_dial_out_deliver_golden_run`. **Judge:** Bridge-as-client originates the A2A Deliver and returns a receipt; trap: idempotent redrive double-sends.
- **CUJ-4** — **Driver:** `tests/address/test_golden.py::test_cuj4_resubmit_full_loop` (+ `tests/test_portal.py::test_resubmit_cap_escalates_not_rejects`, `tests/address/test_golden_llm_edge.py::test_cuj4_resubmit_then_clear_loop`). **Judge:** blurry→clear accepts within ≤3, on the mock and on the real (hybrid) edge; trap: >3 resubmits / cap escalates-not-rejects violated.
- **CUJ-5** — **Driver:** `tests/test_portal.py::test_hitl_reviewer_can_reject` + `tests/test_portal.py::test_blurry_resubmit_then_clear_hitl_then_resolve_accept`; `tests/address/test_park_resume.py::test_dur3_park_is_zero_compute`; agent-to-agent: `tests/test_edge_hitl_a2a.py::test_hitl_park_input_required_with_call_id` + `tests/test_edge_hitl_a2a.py::test_parked_task_zero_compute` + `tests/test_edge_hitl_a2a.py::test_resume_after_rebuild_reaches_timeline_resolution`. **Judge:** flagged extraction parks then resumes on an id-matched response — via portal Path-B, and agent-to-agent as an `INPUT_REQUIRED` A2A task (`awaiting_human_review` + `review_call_id`) resumed by a `HumanReviewResolution` on the same task, reaching the `timeline.json` resolution after a rebuild; trap: park uses compute (DUR-3; `tests/test_edge_hitl_a2a.py::test_parked_task_zero_compute`) / HITL-without-park not 409 (`tests/test_portal.py::test_hitl_without_park_409`) / a non-reviewer or mismatched-call-id resolution accepted (`tests/test_edge_hitl_a2a.py::test_resume_non_reviewer_rejected`, `tests/test_edge_hitl_a2a.py::test_resume_mismatched_call_id_refused`).
- **CUJ-6** — **Driver:** `tests/test_outbound.py::test_ladder_with_nudge_notifier_end_to_end` (+ `tests/test_sla.py::TestLoadSlaPolicy::test_timeline_parity`). **Judge:** on_track→overdue→reminder(real nudge)→escalated, each recorded, next alarm re-armed; trap: nudge carries a bearer token (`tests/test_outbound.py::test_nudge_carries_no_bearer_token`).
- **CUJ-7** — **Driver:** `tests/address/test_wake.py::test_restart_survival_via_doorbell` (+ `tests/address/test_park_resume.py::test_dur4_crash_safe_resume_path_b_two_bills`). **Judge:** parked leg survives restart to the same terminal outcome; trap: throwaway-child-session / new-task-per-round.
- **CUJ-8** — **Driver:** `tests/test_live_doctype_add.py::test_live_doctype_add_regenerates_card`. **Judge:** skill dropped into `BRIDGE_SKILLS_DIR` → next discovery GET regenerates the Agent Card, no redeploy; trap: card unchanged.
- **CUJ-9** — **Driver:** —. **Judge:** a new use-case's skills placed in the running core while an exchange of another process is in flight → the next discovery GET advertises the new process, and the in-flight exchange reaches the same terminal outcome it reaches without the upload; trap: a redeploy/restart, or the in-flight exchange disturbed.
- **CUJ-10** — **Driver:** `tests/address/test_golden.py::test_cuj10_distinct_issuer_accept_real_edge` + `tests/address/test_golden.py::test_cuj10_same_issuer_reject_real_edge` (+ `tests/address/test_gate.py`, `tests/address/test_golden_llm_edge.py::test_cuj_driver_parity[scripted-cuj10-distinct]` + `tests/address/test_golden_llm_edge.py::test_cuj_driver_parity[scripted-cuj10-same-issuer]`). **Judge:** one gov-ID *or* two distinct-issuer bills satisfy; same-issuer pair rejects; the outcome is identical on the deterministic and hybrid edges; trap: same-issuer counted as two / the hybrid edge calls the model on a structured request.
- **CUJ-11** — **Driver:** `tests/test_cuj11_negotiate_bind_rollup.py::test_cuj11_negotiate_bind_rollup_golden_run`. **Judge:** fan-out→normalize→compare→negotiate(versioned revise, capped)→bind→rollup; trap: a failed revise silently crashes instead of routing to human escalate (`tests/test_negotiate.py::TestRevise::test_cap_exceeded_escalates_not_rejects`).
- **CUJ-12** — **Driver:** `tests/test_cuj12_interop.py::test_interop_same_conversation_both_builds` + `tests/test_cuj12_interop.py::test_carrier_b_multi_turn_revise_langgraph` + `tests/test_cuj12_interop.py::test_carriers_are_framework_diverse`. **Judge:** ADK Bridge negotiates multi-turn with a LangGraph counterparty in one conversation, carriers genuinely framework-diverse; trap: bearer token on the carrier wire (`tests/test_cuj12_interop.py::test_no_bearer_token_on_carrier_wire`).
- **CUJ-13** — **Driver:** `tests/test_rfp.py::TestUpdateRequirements::test_agent_adds_a_carrier_and_a_line_and_waives_an_item` + `tests/test_rfp.py::TestAnswer::test_exchange_turn_proposes_the_outstanding_delta` + `tests/test_rfp_a2a_open.py::test_follow_up_requirements_waive_and_add_a_carrier` (+ `tests/test_rfp.py::TestAnswer::test_corpus_quotes_file_through_the_real_gate`, `tests/test_rfp_views.py::TestConsumerReadApi::test_list_and_get_carry_the_matrix`, `tests/test_rfp_second_use_case.py`). **Judge:** an agent-mutated Requirements update adds a supplier (on its own isolated leg) and an item and waives an item without dropping any cell, and the waived item counts as neither ready nor waiting; the same update over A2A lands on the durable exchange record; replies file per (supplier, item) through the real disposition gate; the exchange turn proposes exactly the outstanding items (waived ones excluded) with reason codes and `done` is false — completeness is the agent's call; the consumer read API carries the supplier × item matrix; a second industry's process skill (its own label space and item key) runs the same flow; trap: a supplier is shown another supplier's lines (`tests/test_rfp_views.py::TestChaseAggregate::test_chase_message_names_only_the_carrier_and_its_lines`, `tests/test_rfp_views.py::TestCarrierChecklist::test_checklist_is_this_carrier_only`) / the resubmit cap rejects instead of escalating (`tests/test_rfp.py::TestReceive::test_resubmit_cap_escalates_never_rejects`).
- **CUJ-14** — **Driver:** `tests/test_edge_offscript_a2a.py::test_delete_erases_all_versions_then_fetch_404` + `tests/test_edge_offscript_a2a.py::test_reject_submission_off_script_rejected` + `tests/test_edge_offscript_a2a.py::test_escalate_input_required_survives_tasks_get_and_restart` (+ `tests/test_edge_offscript_a2a.py::test_cannot_erase_another_contexts_or_partys_artifact`). **Judge:** on the hybrid edge a free-text deletion completes with `reason_code=deletion_completed` and every version of the scoped fetch URI then 404s; a wrong file completes with `off_script_rejected` and no `rejected` ledger entry; an escalate holds `INPUT_REQUIRED`, escalated and non-terminal, across `tasks/get` and a restart; another party's or context's artifact is never erased; trap: soft-delete (the fetch still 200 after `deletion_completed`) or the LLM disposing a genuine document (`tests/test_edge_gate_inversion.py::test_adversary_a2a_hybrid` — an off-script tool on a genuine document mints no entry beyond the genuine gate verdict).
- **CUJ-15** — **Driver:** `tests/test_email_ingress.py::test_reply_files_each_attachment_through_the_real_gate` + `tests/test_email_served.py::test_landed_reply_reaches_its_rfp_leg_through_the_served_host` + `tests/test_rfp_a2a_open.py::test_mail_drained_by_board_host_files_onto_rfp_opened_through_a2a` (+ `tests/test_email_correlation.py`). **Judge:** a reply to the advertised per-leg address correlates to exactly its (exchange, supplier) leg and no other leg changes; each attachment is dispositioned through the real gate onto that leg's items (accepted → received, flagged for review → incomplete, an unreadable attachment recorded as unreadable) and stored by reference at the leg-scoped fetch URI; the same holds through the served host's drain and onto an exchange opened over A2A, and a second drain files nothing; trap: an unattributable mail (foreign secret, no token, failed sender authentication, ambiguous, unparseable) touches the board (`tests/test_email_ingress.py::test_quarantine_never_touches_the_board`) / a replayed mail files twice (`tests/test_email_ingress.py::test_replay_is_a_noop`).

## Related
- [exchange patterns](../design/patterns.md) · [demo implementations](demos/demo-suite.md) · [Collect](../design/collect.md) · [outbound direction](../design/outbound.md)
```

**Supersedes.**
- S3-edge-8 P-1..P-5.
- The CUJ Driver notes in S0-bug-3 (served path), S0-arch-15 (second use case), S0-arch-17 (deployed leg "unprovable", now proven) and S0-arch-19 (deployed-shape Driver).
- The older CUJ-9/13 Driver suggestions in S0-arch-11 (`tests/test_rfp.py`) and S0-frontend-1 (`tests/test_rfp_views.py`). Both are folded into CUJ-13.
- The S0-arch-13 note (a Driver for "a carrier replies by email to its tokened intake address"), folded into CUJ-15.
- **Not folded (deliberately):** S0-arch-9 (a Driver line for the `tests/conformance/` consumer-conformance journey), S0-arch-12 (optional scanned-quote leg) and S0-docs-5 (`tests/test_disposition_calibration.py` as a second CUJ-5 Driver). These are component/consumer checks, not journey Drivers. They stay open for the spec owner and are unaffected by this body.

**Apply-time ripple / known residuals (left to their own tasks).**
- `design/architecture.md:74` and `design/patterns.md:24` still word CUJ-13 the old way (S0-docs-9).
- In `docs/features.md`, CORE-8/ART-1 gain `14` and the RFP/email rows gain `13`/`15` in their CUJ cells (S0-docs-10). Apply after the spec lands, so the catalog never cites an unapplied CUJ.
- `spec/demos/rfp-demo.md`: the Nimbus procurement scenario vs the built broker RFP (S0-docs-7 neighborhood).
- The frontmatter's `[[wikilink]]` form (S0-docs-11).
- If CUJ-14 is rejected, renumber CUJ-15 as in decision 4.
- CUJ-9 stays not-runnable until a "skills uploaded while another process's exchange is in flight" test exists.

**Verification.**
- T1: `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped (identical to the HEAD baseline) (no code or test change; no new skips).
- T2 (checker `/tmp/cujids.py` from the plan: every backticked id on `- **CUJ-` lines, bare `::`/`[` continuations to stderr):
  1. **Finding (b) reproduces on the current spec.** `uv run python /tmp/cujids.py spec/cujs.md | sort -u | xargs uv run pytest --collect-only -q` → `ERROR: not found` for exactly `tests/address/test_park_resume.py::test_hitl_without_park_409` and `tests/test_sla.py::test_nudge_carries_no_bearer_token`. 9 bare continuations reported.
  2. **The proposal is paste-ready.** `/tmp/cujs.proposed.md` = lines 1-9 of `spec/cujs.md` + the fenced block above, extracted from this file. Every check passed:
     - The checker prints `BARE: []`.
     - `--collect-only` on every extracted id → 118 collected, no `ERROR: not found`.
     - `pytest -q` on every extracted id → 117 passed / 1 skipped / 0 failed. The skip is the pre-existing live-Gemini param of `tests/address/test_golden_llm_edge.py::test_cuj4_resubmit_then_clear_loop`, gated on `BRIDGE_TEST_GEMINI`.
     - 15 table rows and 15 Driver lines, each with a `**Judge:**`. Only CUJ-9 has `**Driver:** —`.
     - The status grep (`first cycle|unbuilt|\blater\b|built w/|✅|cycle|sprint|<task-id patterns>|TODO`, case-insensitive) is empty.
     - `grep -c '<'` → 0 (the original's count is also 0).
     - Lines 1-8 diff empty against `spec/cujs.md`.
  3. **CUJ-13 + CUJ-15 Drivers in both edge modes**, under `env -u GOOGLE_API_KEY -u GOOGLE_GENAI_USE_VERTEXAI`: `BRIDGE_EDGE_MODE=deterministic` → 33 passed, and `=hybrid` → 33 passed. 0 skips in each (CUJ-13: 15, CUJ-15: 18).
  4. **jeep LLM judge** (gemini-2.5-flash, temperature 0, `{score, pass, rationale}` schema; criteria = journey row + Driver/Judge line; evidence = both mode runs + cited test sources): CUJ-13 `pass=true score=1.0`, CUJ-15 `pass=true score=1.0`.
  5. **Protected tree untouched.** `git status --porcelain -- spec/` is empty. Staged: `DECISIONS.md`, `docs/features/production-readiness.md`.

`TODO.md` S0-docs-6 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.

## 2026-10-01 — S0-docs-7: spec/standards.md, open-questions.md, demos/ proposal (nine seams, D2 posture, resolved questions out, Aon question restated, Benefits process skill + hard-document beat, RFP Requirement fields)

**Context.** Four spec files had drifted from the code. Every claim below was re-verified at implement time (HEAD 0e7ce8b), including `~/aon` (read-only):
- **standards.md:23,46 count eight seams.** `bridge/seams/seam.py` `Seam` has nine members; the ninth is `email_ingress`. `design/seams.md` already lists Email ingress. It is Bridge-owned (`bridge/seams/protocols.py` `EmailIngressSeam`), not a platform ABC: `design/seams.md` calls it "a new external boundary … so it gets the full seam treatment".
- **standards.md:35 cites a `strict=True` footgun with no source.** Nothing in `bridge/`, `contract/`, `agents/`, ADK or a2a-sdk has a party-scoping `strict` flag. The only `strict` hits are `zip(strict=True)`, `resolve(strict=False)` and comments about `extra="forbid"`. As built, `design/zones.md:11` says "Isolation is enforced **at the network layer only** — not by application-level tenant scoping", and `design/zones.md` says the Gateway scopes per party ("a party agent can address only its own leg's context"). The one application-level scope is the leg: every artifact fetch is authorized against the leg that asked (`design/artifacts.md`, `design/adk.md`, `bridge/edge/artifacts.py` authorize-before-sign).
- **open-questions.md carries three resolved questions.**
  - Request intake (:20-24) is decided and as-built in `design/a2a-edge.md` ("Request intake — format vs capability (decided; as-built)").
  - FX rate source (:30-32): `bridge/normalize.py` has a fixed `FX_RATES` table that the Benefits normalize oracle is authored against, and an `FxRates = Callable[[str], float]` swap point (`normalize_quote(fx=fixed_fx)`). This is the S1-benefits-2 decision. It was not in `design/`, so it is folded in below.
  - Requirements-list schema (:34-36): `contract/wire.py` `Requirement{item, status, party?, doctype_hint?, reason_code?, message?}` (`extra="forbid"`) and `RequirementsList{requirements, done}`; `contract/enums.py` `RequirementStatus` = `required|optional|satisfied|waived`. `CollectRequest.requirements` + `due_at`, which anchors the SLA ladder (`bridge/rfp.py`). `design/collect.md` showed the list without `party` / `reason_code` / `message`, so those are folded in below.
- **open-questions.md:15** ("First build cycle is Core + Address; Benefits/RFP deferred") is pure sequencing, and false: Benefits (CUJ-3/11/12) and the RFP emergent Collect both run. The S0-docs-4 replacement for it ("RFP stays an eval scaffold") is now false too.
- **The never-applied S0-docs-4 Aon rewrite is itself stale.**
  - Its "Convergence prerequisites" bullet has converged. The by-reference resolver is S0-arch-7 (passed on retry). The conformance run is S0-arch-9 (`tests/conformance/`).
  - Its "Boundary: scanned/raster … Document AI adapter swap" bullet has converged: the OCR pre-pass is S0-arch-12 (`design/docai-prepass.md`).
  - Page-window chunking (S0-perf-1) and the rate/design tables in `benefits-quote` (S0-arch-10) also shipped.
  - Its "finding" paragraph (the Bridge "could not do the job": not schema-driven, no retry, no PDF, no by-reference) is now false clause by clause.
  - `~/aon` (HEAD 83c86cd) still extracts in-process. `agents/document_bridge/bridge.py::DocumentBridge.bridge(ArtifactRef) -> BenefitsQuote` sits over its own `engine/extraction/gcp.py::GcpExtractor`. Its DECISIONS still calls the remote Document Bridge "Stage 2", and nothing there calls this Bridge over A2A. So the delegate-or-keep question is still open.
- **benefits-demo.md names the process skill `benefits-quote`**, at :32 and also at :18 (`request benefits-quote for …`; the task line names only :32). The shipped process skill is `skills/benefits-procurement/` (`bridge-pattern: negotiate`, `bridge-policy: assets/policy.yaml` with `negotiate.revise_cap: 2`, `bridge-program-standard: assets/standard.yaml` with `overridable: true`), and `tests/test_cuj11_negotiate_bind_rollup.py` uses `PROCESS = "benefits-procurement"`. `benefits-quote` is the doctype skill only. Its schema v2 carries `tables.rates` / `tables.design` plus product line and `commission_*` key fields.
- **benefits-demo.md has no beat for scanned or 1000-row documents**, though both capabilities have tests:
  - `tests/test_docai_extraction.py::test_scanned_pdf_reaches_the_records_terminal_outcome` — a raster-only carrier-C quote lands the record's `expected_normalized` and `expected_gaps`.
  - `tests/perf/test_scale_extraction.py`: a 1000-row sheet over 26 pages comes back exact and in order; a flagged row lands on its global path; an unchunked leg raises `ExtractionTruncated` after exactly one call.
- **rfp-demo.md:64** lists `item, status, doctype hint, note`. That omits `party`, `reason_code` / `message`, `done` and `due_at`, and has a `note` field that does not exist.
- **No "showcase runner" exists.** `scripts/run-all.sh` boots the core (one Agent Card over every `skills/` dir) plus portal / console / ops / timewarp. The RFP board lives on a separate host (`bridge.demos.rfp_demo:build_rfp_demo_app`, :8010) with its own dashboard mount. Nothing boots all three demos onto one card and one dashboard and exercises them concurrently.

**Decisions.**
1. **Per-file proposals.** standards.md and rfp-demo.md get whole-line replacements at exact line numbers. open-questions.md and benefits-demo.md get **full-body replacements** (everything after the frontmatter and the blank line that follows it), because they delete sections and insert a table row. Every frontmatter block is untouched; its `[[wikilink]]` form belongs to S0-docs-11.
2. **The seam list carries no count.** :23 names all nine seams and drops the numeral; :46 says "every seam". A numeral drifts on the next seam, which is exactly this bug. "Typed against the **real** platform ABCs" becomes "the real platform ABC where the platform has one", and Email ingress is named as the Bridge-owned boundary given the same seam treatment (the `design/seams.md` wording).
3. **D2 states the as-built trust posture, with no `strict=True`.** Isolation is at the network layer ([zones](../design/zones.md)): a party addresses only its own leg's context through the gateway. The one application-level scope is the leg ([artifacts](../design/artifacts.md)). *Rejected:* keeping the footgun with a citation. There is nothing to cite, because no such flag exists.
4. **open-questions.md body.**
   - The three resolved questions are deleted. Their decisions live in `design/a2a-edge.md` (intake), `design/fulfillment-graph.md` (FX, folded in here) and `design/collect.md` (Requirements, folded in here).
   - "Artifact retention window" stays verbatim. It is still a deploy-time decision.
   - The "Benefits/RFP deferred" settled bullet is **deleted**, not restated. It is sequencing, and the demo suite already specs all three demos. The other four settled bullets stay verbatim.
   - The Aon section keeps the S0-docs-4 heading and Source paragraph. Changes:
     - "What changed" states both engines, including the consumer's in-process document-bridge interface.
     - The false "finding" becomes **"What the Bridge offers for this document class"**: the capability axis (stated as given), schema-validated retry, by-reference PDF, the OCR pre-pass, page windows, and mapped-not-renamed review vocabulary proven by a conformance run.
     - The live **delegate-or-keep** question stays OPEN.
     - Of the S0-docs-4 sub-points: *convergence prerequisites* and *boundary (OCR)* are dropped as converged. *Capability axis* is dropped as settled (now stated in the offer paragraph). *Contract shape* is restated as what is genuinely still open: the conformance mapper refuses the Bridge's wider vocabulary (`WEEKLY` / `SEMI_MONTHLY`, a non-`FLAT` rate basis) rather than coercing, and which side widens is undecided. *Provenance ownership* stays verbatim.
   - The Related line stays verbatim; the Aon offer paragraph now links the Benefits demo.
5. **benefits-demo.md body.**
   - `benefits-procurement` is the process skill at :18 and :32, with the program standard as the separate `assets/standard.yaml` asset. `policy.yaml` carries `negotiate.revise_cap`, so "revise-loop cap" stays.
   - The doctype line gains product line, commission terms and the two tables.
   - New beat **`3b · Hard documents`** (scanned PDF + 1000-row sheet). *Beats are not renumbered*: `skills/benefits-procurement/assets/standard.yaml` and `bridge/` docstrings cite "beat-4" / "beat 5" by number.
   - Two depth-capability bullets: scanned-document extraction ([Document AI pre-pass](../../design/docai-prepass.md)) and large-document extraction ([seams](../../design/seams.md)).
   - Core machinery names the three engine shapes. Demo data and validation gain the scanned rendition and the 1000-row sheet.
   - Design parameters are restated as the chosen shapes.
6. **rfp-demo.md:64 matches `contract.wire.Requirement`.** The fields are `item`, `status`, optional `party` (party × item), doctype hint, reason code + message, the list's `done`, and the request's due date. The **Nimbus scenario stays as written**. It is the industry-neutral requirement, `tests/test_rfp_second_use_case.py` runs its shape (supplier-onboarding, `w9-form`, `insurance-cert`), and the broker RFP is one instance of it. This closes the S0-docs-6 residual by decision; the scenario and its skill names are not rewritten.
7. **Showcase runner: kept as an explicit requirement, with a new task.** Rewording it to "the real mechanism" would make the spec false or weaker: `run-all.sh` gives one card, but the RFP board is a separate host and nothing exercises the three demos concurrently. The requirement proves the reusability thesis. So `spec/demos/demo-suite.md:18,38` and `rfp-demo.md:55` stay **verbatim**, with no spec proposal for those lines, and **S0-arch-20** is filed in `TODO.md` (counter `s0-arch=20`). The same harness's live use-case-add variant is CUJ-9's missing Driver.

**Design fold-ins (applied; `design/` is the source of truth and is not protected).**
- `design/fulfillment-graph.md:27`: FX conversion reads a fixed rate table (deterministic; the normalize oracle is authored against it), and a live rate source is a one-argument swap at the same conversion point. Only line 27 is touched; :16-25 belong to S0-docs-8.
- `design/collect.md`: one paragraph after the Requirements block gives the fixed item shape (`item`, `status`, optional `party` / `doctype_hint` / `reason_code` + `message`), what `party` means (party × item), and that a `CollectRequest` carries the turn's list plus the due date that anchors the chase ladder.

**SPEC PROPOSAL (not applied — spec/ is protected).** `spec/standards.md` — three whole-line replacements; every other line is unchanged.

Replace line 23 (it begins `| **Ring 2 — Platform services (the seams)**`) with:

```markdown
| **Ring 2 — Platform services (the seams)** | The seams — Sessions, Task store, Exchange store, Skill registry, Memory bank, Scheduler/Timer, A2A client, Extraction, Email ingress — each typed against the **real** platform ABC where the platform has one; Email ingress is a Bridge-owned external boundary that gets the same seam treatment. | Our **deployment story** — the mock→real / local→GCP swap-is-a-no-op property. | Tolerated only as an *adapter behind the ABC*. See [seams](../design/seams.md). |
```

Replace line 35 (it begins `- **D2 — Domain inside parts`) with:

```markdown
- **D2 — Domain inside parts (contract hygiene).** Domain types (`CollectRequest`, `ExchangeTurn{CollectionStatus}`, `RequirementsList`, `LedgerEntry`) travel as **JSON `DataPart`s inside** the standard envelope — the envelope is standard, the domain is the payload. Carries a `contract_version` marker; unknown-field policy is `extra="forbid"` for our two-party edge (a **tolerant reader** is the posture for genuinely external callers); the trust boundary is enforced at the network layer, not by application-level tenant scoping — a party addresses only its own leg's context through the gateway ([zones](../design/zones.md)) — and the one application-level scope is the leg: every artifact fetch is authorized against the leg that asked ([artifacts](../design/artifacts.md)).
```

Replace line 46 (it begins `Ring 2 is fully specified in`) with:

```markdown
Ring 2 is fully specified in [seams](../design/seams.md) (every seam, two adapters each, one shared test suite). Ring 3 is the construct layer in [adk](../design/adk.md) and [patterns](../design/patterns.md) — where custom code is *meant* to live, priced as internal coupling and watched via the living list of experimental surfaces.
```

**SPEC PROPOSAL (not applied — spec/ is protected).** `spec/open-questions.md` — replace lines 8-47 (everything after the frontmatter and the blank line that follows it) with:

```markdown
# Open Questions

> Design questions that are genuinely undecided or intentionally settled at deploy time. Resolved decisions live in the relevant design pages; this note tracks only what is still open, with the current leaning where one exists.

## Settled since last revision

- **Outbound is first-class** (not "a separate design direction"). The Bridge chases, delivers, and dials counterparties over the existing A2A-client seam; the pull spine remains the core motion. First build slice is the outbound seam + a real party-channel nudge in the Address cycle. See [outbound direction](../design/outbound.md).
- **ADK is authoritative at `google-adk 2.7.0`** — no pre-planning verification; the experimental-surfaces list is the risk register ([adk](../design/adk.md)).
- **Three demos only** — the "Maturity" demo is dropped ([frontend](../design/frontend.md)).
- **CUJ ids now resolve** to the [CUJ catalog](cujs.md).

## Artifact retention window

The retention *mechanism* is a backend object-lifecycle policy (not bespoke GC), covering at least the exchange life. The concrete post-terminal **window** is a compliance/ops concern left to deploy-time policy, not fixed in the architecture. See [long-running collection](../design/long-running.md).

## Feature request (incoming) — carrier PDF → structured extraction, and who owns it (Aon benefits consumer)

**Source:** Aon Digital Placement (`~/aon`), CUJ-8 "full demo" journey (step 1: "got PDFs from carriers"). The original ask: the Bridge owns carrier-quote **PDF → strict structured JSON** so the consumer normalizes to its `BenefitsQuote` schema — validated fields (rates by tier, benefit design by category, effective dates), extraction confidence + `fields_requiring_review` per field, schema-validated retry on malformed output, bytes by reference as artifacts ([artifacts](../design/artifacts.md)).

**What changed since the ask was filed.** Both sides now carry an extraction engine for this document class. The consumer runs its own (`~/aon/engine/extraction/gcp.py::GcpExtractor` — Vertex Gemini with a pinned response schema, a bounded schema-validated retry that feeds each rejection reason back to the model, no fixture fallback, `fields_requiring_review`, native PDF bytes and `gs://` read by reference), reached in-process through its own document-bridge interface: a carrier document reference in, a validated `BenefitsQuote` out.

**What the Bridge offers for this document class.** The capability is a doctype skill over the one generic, schema-driven engine ([skills](../design/skills.md)): the `benefits-quote` doctype skill the [Benefits demo](demos/benefits-demo.md) runs on, its schema including the rate and benefit-design tables. Its extraction is schema-validated with a bounded retry that echoes each rejection back, and an exhausted retry raises — never a fixture fallback. A native PDF is read **by reference** (`gs://`, or an `http(s)` URL such as a signed URL, fetched under an egress guard); a scanned page is read through an OCR pre-pass ([Document AI pre-pass](../design/docai-prepass.md)); a large rate sheet is extracted in page windows and merged without dropping a row ([seams](../design/seams.md)). Per-field confidence and review flags travel in the consumer's dotted-path vocabulary — mapped, deliberately not renamed on the wire ([standards](standards.md) `contract_version` semantics) — and a conformance run takes a carrier PDF in by reference over the A2A edge and maps the result onto the consumer's `BenefitsQuote` shape, on the fixture and Gemini legs at terminal-outcome parity.

**The live question.** Two extraction engines now exist for the same document class. Does the consumer **delegate** extraction to the Bridge over A2A, or keep its own engine and use the Bridge only for the collection spine? Settle:

- **Contract shape.** Where the Bridge's vocabulary is wider than the consumer's `BenefitsQuote` (rate frequencies or a rate basis the consumer does not model), the mapping refuses rather than coerces; which side widens is open.
- **Provenance ownership.** Which side supplies intake context the document itself never carries (e.g. the placement/program identifier), and "no fixture fallback on exhausted retries" as a discipline both engines hold.

## Related
- [demo implementations](demos/demo-suite.md), [A2A edge](../design/a2a-edge.md), [long-running collection](../design/long-running.md), [Benefits demo](demos/benefits-demo.md)
```

**SPEC PROPOSAL (not applied — spec/ is protected).** `spec/demos/benefits-demo.md` — replace lines 12-58 (everything after the frontmatter and the blank line that follows it) with:

```markdown
# Benefits Demo — scenario

> The **depth** demo: a broker's agent gathers group-benefits quotes from **N carriers** and needs **one comparable view** to decide on. This is the [Negotiate](../../design/patterns.md) pattern over a **program** — fan-out → normalize → compare/standard-gap → negotiate → bind → rollup. The payoff is the moment N messy, multi-currency, multi-language quotes render as one apples-to-apples table with the gaps flagged.

## Scenario — group benefits procurement

A benefits broker's agent must source a group health plan for client *Acme Corp* (synthetic) from **four carriers** in different regions. It opens one **program** and fans out one leg per carrier — `request benefits-procurement for program:acme-2026, party:carrier-{a,b,c,d}`. Each leg is its **own [context + party](../../design/aggregate-model.md)**; competitors never share a context ([isolation by per-leg addressing](../../design/a2a-edge.md)). The agent asks once per carrier and waits for a single normalized comparison.

| Beat | What happens | Proves |
|---|---|---|
| 1 · Fan-out | Agent opens `program:acme-2026`, requests a quote on four isolated legs. | program context linking N legs · per-leg party isolation |
| 2 · Mixed responses | **Two carriers answer via their own agents (Path A):** Carrier A's agent is built with **Google ADK**, Carrier B's with **LangGraph** — both respond over A2A (USD). Carriers C, D upload PDFs (Path B) in **EUR / French** and **GBP / German**. | [dual-path](../../design/dual-path.md) at fan-out scale · **A2A framework interoperability** (ADK ↔ LangGraph ↔ the Bridge) · heterogeneous inputs |
| 3 · Normalize | Multilingual extraction + **FX normalization** map every quote to one schema, one currency, one language. | the normalization that makes comparison possible |
| 3b · Hard documents | Carrier C's quote also arrives as a **scanned** PDF with no text layer: an OCR pre-pass reads the pixels while extraction still maps onto the doctype schema, and the scan lands the same normalized quote and gaps as the native PDF. A carrier rate sheet runs to **1000 rows** over two dozen pages: extraction returns every row, in order, and a flagged row is reported at its position in the whole sheet — a truncated answer fails loud, never silently short. | extraction holds on raster and rate-sheet-scale documents · no silent row loss |
| 4 · Compare + standard-gap | The [program comparison view](../../design/a2ui-edge.md) renders four legs side by side; each is flagged against the **program standard** via pack-declared rules (e.g. "deductible ≤ X", "must cover Y") — [sense A](../../design/disposition.md). Carrier B's quote shows a coverage gap. | **the golden payoff** · declared-rule standard-gap (Bridge surfaces, never decides the deal) |
| 5 · Negotiate | Agent pushes back on Carrier B's gap over A2A; **B's LangGraph agent** returns a **revised quote** → a new artifact **version** on the same leg; the comparison updates live. | Negotiate pattern · versioned revise loop · **multi-turn A2A with a non-ADK counterparty** |
| 6 · Bind + rollup | Agent binds the winning leg (**sense B — the deal is the agent's**); the Bridge records per-leg bind → **program rollup** → audit trail. | bind → rollup → audit · the mediator never decides acceptance |

## What we need to make it work

**Skills:**
- **Process skill `benefits-procurement`** — pattern `Negotiate`, policy (SLA, nudges, revise-loop cap), candidate doctypes `[benefits-quote]`, and the **program standard** — a separate declared-standard asset (`assets/standard.yaml`) of rules the comparison flags against, agent-overridable per client.
- **Doctype skill `benefits-quote`** — schema (premium, deductible, coverage limits, exclusions, currency, language, effective dates, product line, commission terms, plus a **rate table** and a **benefit-design table**), prompt, validation.

**Depth capabilities:**
- **Programs** — a provider-facing program context that links N legs and carries the rollup; per-leg isolation enforced by addressing ([Programs](../../design/a2a-edge.md)). Program-scale Gateway addressing.
- **Multilingual extraction + FX normalization** — the [extraction graph](../../design/fulfillment-graph.md)'s translate + convert layer: map every quote to one schema/currency/language.
- **Scanned-document extraction** — an OCR pre-pass in front of the same schema-driven request, so a raster PDF with no text layer extracts against the doctype schema unchanged ([Document AI pre-pass](../../design/docai-prepass.md)).
- **Large-document extraction** — a long document is extracted in page windows and merged in page order, so a rate sheet of a thousand rows returns every row and a truncated response fails loud ([seams](../../design/seams.md)).
- **Declared-rule standard-gap disposition** — flag each normalized quote against the program standard (sense A); routes to negotiation.
- **Negotiate + versioned revise loop** — artifact versioning on the same leg; the comparison recomputes as versions land.
- **Program comparison A2UI view** — N legs side by side, one currency and schema, gaps flagged.
- **Bind → program rollup → audit** — per-leg bind, program-level rollup, an audit trail.

**Simulated provider agents (the interoperability proof):**
- **Framework-diverse counterparties.** The Path-A carriers are stood up as **real A2A agents in the external zone**, each its own A2A client + agent logic responding to the Bridge's [A2A edge](../../design/a2a-edge.md). Deliberately built in **two different frameworks** — Carrier A in **Google ADK**, Carrier B in **LangGraph** — to prove A2A is framework-agnostic: the ADK-built Bridge negotiates, multi-turn, with a **non-ADK** counterparty and never notices the difference. This is the platform-evaluator headline for depth: *interoperability, not just fan-out.*
- **The LangGraph carrier drives the negotiate loop** (beat 5): it holds the coverage gap, receives the pushback over A2A, and returns a revised quote — so the interop proof covers the **multi-turn revise loop**, not a one-shot response. Both simulated agents are demo furniture (like the A2UI reference renderer), not part of the Bridge core.

**Core machinery the demo relies on:** both edges, dual-path, extraction seam (fixture, Gemini, and Gemini with a Document AI OCR pre-pass), disposition gates, HITL, proactive follow-up, seams, network zones.

**Demo data:** **synthetic** quote documents — four carriers, three currencies (USD/EUR/GBP), three languages (English/French/German); the **LangGraph** carrier (B) carries both the deliberate coverage gap and the revise round. Two Path-A structured quotes (A, B), two Path-B PDFs (C, D), plus a **scanned** rendition of Carrier C's PDF (raster, no text layer) and a synthetic multi-page **1000-row rate sheet**. Doubles as extraction fixtures + test corpus. An **FX rate source** (fixed rates for a deterministic run).

**Validation:** golden runs per branch (structured vs PDF intake, each language/currency, standard-gap flag, revise loop, bind + rollup) against fixture and Gemini adapters, **plus a cross-framework interop test**: the same A2A conversation must pass whether the counterparty agent is the ADK or the LangGraph build. The scanned rendition reaches the same normalized quote and gaps as the native PDF; the 1000-row sheet returns every row exactly and in order, and an unwindowed run fails loud rather than returning a short table.

## Design parameters
- **FX rate source** — a fixed rate table drives the deterministic demo and is the normalize oracle; a live rate source is a one-argument swap at the same conversion point.
- **Program standard rules** — a separate declared-standard asset in the process skill, agent-overridable per client.

## Related
- [demo implementations](demo-suite.md), [patterns](../../design/patterns.md), [A2A edge / Programs](../../design/a2a-edge.md), [A2UI edge / comparison](../../design/a2ui-edge.md), [disposition](../../design/disposition.md)
```

**SPEC PROPOSAL (not applied — spec/ is protected).** `spec/demos/rfp-demo.md` — replace line 64 (it begins `- **Requirements-list schema**`) with the line below. Lines 28-38 (the Nimbus scenario) and line 55 (showcase runner) stay verbatim (decisions 6 and 7).

```markdown
- **Requirements-list schema** — a small fixed shape the agent returns each turn: per item `item`, `status: required|optional|satisfied|waived`, an optional `party` (whose response it awaits — over N suppliers a requirement is party × item), a doctype hint, and a reason code + message; the list carries the agent's `done`. The request also carries the due date that anchors the chase ladder. Data, not a language (see [Collect](../../design/collect.md)).
```

All blocks drop in verbatim. Heading levels and relative links match each file's conventions (`spec/*.md` → `../design/x.md`, `standards.md`; `spec/demos/*.md` → `../../design/x.md`, `demo-suite.md`), and every changed line is status-free: no task ids, dates, sprint/cycle words or checkboxes.

**Supersedes.**
- The S0-docs-1 proposal to delete "Request intake" (folded into this body).
- The S0-docs-4 proposal, both blocks: the Aon section rewrite and the settled-bullet line. Its base text is kept where still true.
- The FX "design parameter" note in S1-benefits-2. The fixed table is now the chosen shape in `design/` and in the Benefits demo text, not an open parameter.

**Apply-time ripple / known residuals (left to their own tasks).**
- Frontmatter `[[wikilink]]` form in all four files → S0-docs-11.
- `design/architecture.md` (cited by the deleted settled bullet) still carries cycle sequencing → S0-docs-9.
- The verbatim outbound settled bullet still says "First build slice … in the Address cycle". It is kept as the plan directs, and is a candidate for the next spec-owner pass.
- `design/fulfillment-graph.md:25` still describes Document AI as "a processor per doctype … swap" rather than the pre-pass → S0-docs-8.
- `bridge/normalize.py`'s docstring still calls the live FX seam "designed-but-unbuilt this cycle" (code comment, not spec).
- Showcase runner and CUJ-9's Driver → S0-arch-20.

**Verification.**
- T1: no code changed. `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped, identical to the HEAD baseline, with no new skips.
- T2 (`/tmp/s0d7/t2.py`): re-extracts the six `markdown` fenced blocks from this entry and splices them onto copies of the real spec files (`/tmp/s0d7/x/*.proposed.md`). 0 failures:
  1. `git status --porcelain -- spec/` is empty, and no `spec/` path is staged.
  2. `cmp` of the frontmatter is byte-identical: standards 1-10, open-questions 1-6, benefits-demo 1-10, rfp-demo 1-9.
  3. Every relative `](x.md)` link resolves against the real file's directory: 17 / 15 / 16 / 14 links, 0 misses.
  4. Every `Seam` member (9) maps to a display name that appears in the proposed Ring-2 row, Email ingress included. `\b(eight|nine)\b` → none.
  5. No `strict=True`. The D2 bullet links `../design/zones.md`, which contains "network layer only".
  6. open-questions.md has none of the three resolved headings, exactly one "Artifact retention window" and one "Feature request (incoming)". The Aon section still asks delegate-or-keep. The stale strings (convergence prerequisites, adapter swap, no real PDF path, could not do the job, Benefits/RFP deferred, eval scaffold) → none.
  7. benefits-demo.md has the "Process skill `benefits-procurement`" line ×1 and `request benefits-procurement` ×1, no `benefits-quote` as the process, and names `standard.yaml`. The named skills exist under `skills/`. Beats run 1, 2, 3, 3b, 4, 5, 6, and 3b mentions scanned + 1000 rows. Backing tests: the two scanned-PDF tests + `tests/perf/test_scale_extraction.py` + `tests/test_cuj11_negotiate_bind_rollup.py` → 8 passed, 0 skipped. Aon offer paragraph: `tests/conformance` + `tests/test_rfp_second_use_case.py` → 86 passed / 6 skipped (gated); with `BRIDGE_TEST_CONSUMER_REPO=~/aon`, `-k consumer` → 36 passed, the real-consumer drift check included.
  8. `set(Requirement.model_fields) == {item, status, party, doctype_hint, reason_code, message}`, and every field plus `done` and `required|optional|satisfied|waived` appears on the proposed :64. `note` is gone.
  9. The status grep on changed lines only (task ids, sprint/cycle, deferred, not yet, shipped, built, TODO, dates, checkboxes) → empty in all four files. There are no `<` placeholders.
  10. `design/fulfillment-graph.md` changes on line 27 only and `design/collect.md` gains the `party` paragraph. Both `+` sets are status-free, and only those two design files changed.
  11. `TODO.md` has `**S0-arch-20**` ×1 (naming `demo-suite.md:18,38` + `rfp-demo.md:55`) and the counter reads `s0-arch=20`. S0-docs-7 is still `- [ ]`.

`TODO.md` S0-docs-7 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.

## 2026-10-01 — S0-docs-8: design docs restated to the as-built deployment

Docs-only, plus comment/docstring-only edits in `bridge/outbound.py` and `bridge/edge/executor.py`. No behavior change. Nothing under `spec/`, `terraform/`, `contract/`, `wiki/evals/` or `skills/` was touched.

**Decisions.**
- **(a) Sessions are Cloud SQL `DatabaseSessionService`.** `gcp-substrate.md`, `long-running.md`, `deploy-resume.md`, `a2a-consumer.md`, `adk.md` and `seams.md` now say that `GcpSessions` builds ADK `DatabaseSessionService` on the shared `DATABASE_URL` DSN, next to `DatabaseTaskStore` and `SqlExchangeStore`. Vertex Agent Engine is not part of the deployment. Memory Bank is dropped from the substrate list and gets its own "no methods" table row. The deploy-resume code block now builds sessions through `get_adapter(Seam.sessions, …)`. The S0-docs-9 lines (deploy-resume :36, :63/64, :70, the hypothetical-runtime asterisk) were left alone.
- **(b) Party memory is reduced to interface intent.** `party-memory.md` states the intent, then the as-built empty seam: no methods, no GCP adapter, no parity test, nothing reads or writes it. It lists three intended consumers and keeps the advisory-only invariant. `outbound.md` says a nudge uses a fixed `email` channel and the default copy. `seams.md` updates the Memory-bank row and drops "memory write". The two `bridge/outbound.py` "(P?)" party-memory comments now describe the fixed default.
- **(c) Extraction engine is set per deployment, and the Path-B diagram is the code's.** `fulfillment-graph.md` now carries the `bridge/pathb.py` docstring diagram verbatim, and describes the docai engine as Gemini plus one deployment-wide OCR pre-pass. `seams.md` drops "per-doctype capability" twice and notes that `bridge-extraction-engine` is not read either. The `skills.md` frontmatter example keeps both keys, marked declarative, and the gov-id/utility-bill engine parentheticals are gone.
- **(d) Zones has one Gateway exception.** The `zones.md` Gateway bullet now names the public `allUsers` email-intake webhook (clientState-authenticated, create-only identity) and says the RFP board host defaults to internal-only.
- **(e) gcp-substrate has an Artifacts row and deletion protection.** It adds an Artifacts row (`GcsArtifactService` via `BRIDGE_ARTIFACT_BUCKET`, a native swap and not a seam; versioned; lifecycle `artifact_retention_days`, default 365; signed URL only). It also adds a deletion-protection paragraph (Cloud SQL plus the runtime and RFP-board services; the demo deploy sets it false; buckets have none).
- **(f) `_status_for` follows `ExchangeTurn.terminal`.** In `disposition.md`, rows 3a/3b now read `COMPLETED` (`report`) · `INPUT_REQUIRED` (`park`, hybrid edge), and the status bullet is rewritten (the false "resubmission … stays parked" claim is removed). Both `a2a-consumer.md` mentions now key on the terminal flag. The `_status_for` docstring now lists the RFP opener as a non-terminal producer and says a pending item alone does not park.
- **(g) There is no CI and no skills-ref.** `seams.md` and `skills.md` now name the Bridge's own loader (`skill_meta_from_dir` → `SkillMeta`, the same parser on both registry adapters) as the check. The same stale claim in `docs/features/gcp-deployment.md` was fixed too, because the T2 grep covers `docs/`.

**Deviations from the plan.**
- `adk.md:35` drops the Agent Engine mention entirely, and the gcp-substrate runtime paragraph is split ("Durable state = Cloud SQL Postgres." is now its own paragraph). Without that, the plan's own draft text matched the Agent-Engine/session T2 grep.
- `seams.md:32` "Agent Engine multi-replica" became "any multi-replica managed agent runtime". The session-affinity sentence matched the same grep. The S0-docs-9 "and P7" at the end of that line is untouched.
- `TODO.md` is not flipped here. This is a CRITICAL task, so the mandatory reviewer flips and commits.

**Gotchas.**
- The stale "not reachable yet" executor comment the TODO cites was already gone (`git log -S` finds it only in `0432a8a`). Nothing was deleted for it.
- Line numbers in the TODO entry had drifted. Edits were anchored on text.
- The isolation guard scans `bridge/` docstrings for installed skill names, so the `_status_for` addition names no skill.
- The skill-folder keys `bridge-extraction-engine` / `bridge-docai-processor` are inert but asserted by `tests/address/test_skills.py`. They are kept and documented as declarative.

**Handoff.**
- **S0-docs-10:** `docs/features.md` SEAM-1 ("Vertex Agent Engine"), SEAM-5 ("local event log / Vertex Memory Bank") and GCP-1 ("consumes managed session/memory via seams") carry the same drift.
- **S0-docs-9 / later:** `design/bridge.md:27` and `design/adk.md:75` still list Memory Bank among the showcased platform pillars.
- **Code-comment status leakage (out of scope):** the `MemoryBankSeam` docstring (`bridge/seams/protocols.py`) and the `LocalMemoryBank` docstring (`bridge/seams/local/__init__.py`) still say "this cycle", "P7 GCP swap" and carry a TODO pointer. The `GcpSessions` docstring still says "a documented later swap".
- **Skill frontmatter:** removing the inert keys from `skills/*/SKILL.md` would also need `tests/address/test_skills.py:245,312` updated.

## 2026-10-01 — S0-docs-9: design/ de-statused; architecture.md is the as-built repo map

Docs-only. No code, `spec/`, `terraform/`, `contract/`, `wiki/evals/` or `skills/` changed. Every status/sequencing marker removed from `design/`, and `architecture.md` rewritten to the as-built module paths.

**Decisions.**
- **(a) First-cycle scope was deleted, not moved.** The "First planning cycle — Core + Address" and "Explicitly deferred past cycle 1" sections in `architecture.md` are deleted entirely. That content is sequencing (already in `docs/roadmap.md` / `TODO.md`), not design. Cold unsolicited push is now stated as out of scope (no edge accepts it without an open exchange) rather than "deferred".
- **(b) The anchor `#core-vs-demo-hosts` was preserved.** The heading `#### Core vs. demo hosts` in `architecture.md` is byte-identical. The anchor is linked from `design/bridge.md:29` and `docs/features/benefits.md:73`, and is cited in `tests/test_isolation_guard.py:181`.
- **(c) a2a-edge wording was coordinated with S3-edge-1.** The a2a-edge route table and hybrid paragraphs were not changed. The "Inbound fulfillment" paragraph's last sentence now names the as-built `bridge/outbound.py` activities (`Nudge` / `DialOut` / `Delivery` → CUJ-3/12).
- **(d) The RFP ladder is derived on read, so the manual chase is its only send path.** `proactive.md` documents the RFP ladder as anchored on `due_at` and derived on read (no alarm armed), so it sends nothing itself. A carrier is nudged only by the operator's manual chase (`POST /rfps/{id}/chase`, `Nudge.seq`, `record_chase`). That chase is now documented in both `outbound.md` and `proactive.md`.
- **(e) Line-number drift.** The line numbers in the TODO entry were written at bb6cd2f, and three tasks (S0-arch-15, S3-edge-1, S0-docs-8) have since edited these files. The plan's anchors were used, not the TODO's drifted numbers.

**Gotchas.**
- Status-free means no "built" / "not yet" / "today" / "Sprint" / "Pn" markers. Describe what the system does (present tense, module paths, test ids). "Derived on read, sends nothing itself" is design. "Automatic RFP chase not yet built" is status.
- The anchor `#### Core vs. demo hosts` was kept exactly as it is.
- The `related:` / `up:` wikilink frontmatter was not edited. S0-docs-11 owns it.
- `spec/` was not edited. No spec change was needed.
- `docs/upstream-adk-issues.md` also cites `bridge_client/`. S0-docs-10 owns that file, so it was left alone.
- The a2a-edge route table and D3/D4 paragraphs were not reworded. S3-edge-1 ratified them.

**Follow-ups (not edited here).**
- `docs/features.md:5,194` (the "layout & first-cycle scope" link text and the C1 / Def scope legend) → S0-docs-10.
- `README.md:107` ("layout and first-cycle scope") → S0-docs-11.
- `docs/roadmap.md:40` link text "layout & scope" (planning doc, low priority).
- `skills/address-proof/assets/policy.yaml:8`: the comment "Consumed by P5-1, not this cycle." is false and is status.
- The `bridge/` comment leaks: `bridge/outbound.py` DialOut docstring "the P7 transport's job", and `bridge/followup.py` "P5-3 plugs …" / "P5-4: persist" / "later wiring, not P5-2".
- None of these follow-ups is in this task's scope, and `bridge/` and `skills/` would escalate it.

**Verification.**
- T1: no code changed. `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped, identical to the baseline at HEAD 1238c37, with no new skips.
- T2:
  1. Leak grep → 0 hits (baseline: 22).
  2. Phantom paths grep → 0 hits.
  3. TODO.md mentions: 3 hits (architecture.md tree line + "Design vs. planning" paragraph + bridge.md planning note).
  4. Path loop → no output (no MISSING). Backticked path extraction → all exist. Import test succeeds.
  5. Anchor grep → exactly one line.
  6. Chase and ladders: `POST /rfps/{` hits in both outbound.md and proactive.md. `record_chase` + `seq` in outbound.md, `due_at` + due-date anchoring in proactive.md. Numbers cross-checked against the two policy.yaml files (Address 1/2/3, RFP 1/3/5 → due, +2d, +4d). Tests green: `uv run pytest -q tests/test_rfp_views.py tests/test_rfp.py tests/test_followup.py tests/test_outbound.py` → 77 passed / 11 skipped.
  7. Cited test ids: all exist and pass or skip only via the existing GCP gate. `uv run pytest -q "tests/test_durable_seams.py::test_task_store_cross_replica_read_after_write" "tests/test_exchange_store_seam.py::test_exchange_store_cross_replica_read_after_write" tests/test_agent.py::test_build_app_is_resumable_by_default tests/test_llm_leg.py::test_llm_leg_app_is_resumable` → 4 passed.
  8. Protected files untouched: `git diff --name-only` shows only `design/*.md`, `DECISIONS.md`, `TODO.md`, `docs/features/production-readiness.md`. No `spec/`, `bridge/`, `agents/`, `skills/`, `contract/`, `terraform/` or `wiki/evals/`.
  9. Relative links resolve: every `](x.md…)` or `](../…)` link in the edited files resolves.

---

## 2026-10-01 — S0-docs-10: Feature catalog + feature docs catch up two sprints

**Problem.** `docs/features.md` stopped at SEAM-8 (no `email_ingress`) and had no entries for email intake, page-window chunking, RFP views / `/rfps` API, the disposition contract + calibration, the conformance run, or any Benefits capability (Programs, FX, standard-gap, Negotiate/bind/rollup, interop); OUT-3/OUT-4/AGENT-8 still read "Def … (built with Benefits)". `docs/features/benefits.md` showed "Planned (active Sprint 1)" with unchecked acceptance boxes though Sprint 1 closed 2026-09-07 T3-green. `docs/features/gcp-deployment.md` listed P7-6/P7-8 under the wrong titles. `docs/upstream-adk-issues.md` cited nonexistent `agents/src/bridge_client/…` (real: `agents/address/interceptor.py`, `agents/address/wire.py`) and `google-adk == 2.7.0` (lock: 2.7.1).

**Decisions.**

**(a) Scope column replaced by Area (status-free).** The `docs/features.md` header + legend changed from Scope (**Cycle 1 = Core + Address**, or **Deferred**) with values `C1`/`C1‑slice`/`Def` to Area (Core · Address · Benefits · RFP · Deploy), grouping by what exercises the core. The C1/Def vocabulary is sequencing (lives in `TODO.md`), not feature categorization. Area is a *decision* (which demo's consumer/skills exercise it; or if it's the substrate), not progress — status-free per user preference.

**(b) IDs appended, never renumbered.** New IDs: `CORE‑9`, `COLL‑6/7`, `BEN‑1…7`, `RFP‑1/2`, `EMAIL‑1/2`, `EXT‑1/2`, `TEST‑5`, `SEAM‑9`, `GCP‑9`. Existing IDs (e.g. `COLL‑5`, `SEAM‑8`, `AGENT‑8`) stayed unchanged — they are cited in `TODO.md`, `roadmap.md`, `DECISIONS.md` and test docstrings. Never renumber or rename an existing feature ID.

**(c) Gemini golden-run scope stated honestly.** The Benefits fixture leg runs every branch (fan-out, normalize, revise, bind, rollup); the Gemini leg is proven on the PDF intake branch by `tests/test_pdf_extraction.py::test_pdf_replay_reaches_the_same_terminal_outcome_on_both_legs` (fake client) + the credential-gated `test_live_pdf_terminal_outcome_parity`. Revise/bind/rollup are fixture-only for now. The acceptance checkbox was flipped with the exact scope stated in the proof note — no false claim that Gemini runs for everything.

**(d) Upstream issues still open on 2.7.1 (evidence).** Both issues (resume detection, DataPart decode) reproduce on the locked `google-adk 2.7.1`. Evidence: `grep -n 'events\[-1\].author' .venv/lib/python3.12/site-packages/google/adk/agents/remote_a2a_agent.py` → line 629 still checks `ctx.session.events[-1].author != "user"`; `grep -n "A2A_DATA_PART_START_TAG =" .venv/lib/python3.12/site-packages/google/adk/a2a/converters/part_converter.py` → line present. No upstream fix yet; both issues stay open/unfiled. The ADK version in the doc's environment line was updated to match `uv.lock`: `google-adk = "==2.7.1"`.

**Follow-ups (not edited here).**

- `design/seams.md` row + paragraph say "GCP Skill Registry" while the adapter is a GCS bucket (`GcpSkillRegistry`, `BRIDGE_SKILLS_BUCKET`). Reconcile text vs. code or record the bucket name as intentional.
- S0-docs-9 archive placement: sits under "## Planned Work" while S0-docs-8 is under "## Completed Work". Reconcile heading placement or leave as-is with a note that `production-readiness.md` is chronological append-only.

**Done.** `docs/features.md`: header/legend changed, dropped C1/Def, added Benefits oracle (`wiki/evals/benefits/` — 5 records + `conformance.json`), fixed stale rows (SEAM-1 → `DatabaseSessionService` / Cloud SQL; SEAM-5 → empty adapter; SEAM-8 → per-deployment engine, not per-doctype; OUT-3 → `DialOut` activity on `bridge/outbound.py`; OUT-4 → `Delivery` activity; AGENT-8 → `agents/benefits/` reusing the A2A contract), added SEAM-9 (`email_ingress`), GCP-9 (deployed RFP board host + email processor), CORE-9 (disposition gate + calibration), COLL-6/7 (RFP aggregate + A2A opener), new sections N (Benefits — BEN-1…7: Programs, FX, standard-gap, comparison, negotiate, interop, rate-tables), O (RFP views & email — RFP-1/2, EMAIL-1/2), P (Extraction depth — EXT-1/2: chunking, docai), TEST-5 (conformance run); tables K/L/M Scope column dropped. `docs/features/benefits.md`: acceptance boxes flipped with proof (`S1-benefits-*` task + test ids), "Planned Work" Problem paragraph moved into intro as "Cycle record", Status replaced with "Closed 2026-09-07 (Sprint 1, T3-green); every `S1-benefits-*` task is archived under Completed Work", Planned Work section left with standard placeholder. `docs/features/gcp-deployment.md`: P7-6 → "Deployed extraction adapter (Gemini engine behind the seam)", P7-8 → "Service-wiring guard test (no hardcoded in-memory services in `bridge/`)" with note entry is in `production-readiness.md`. `docs/upstream-adk-issues.md`: env line → "Issues 1–2 first observed on `google-adk` 2.7.0 and re-confirmed on the locked 2.7.1; Issues 3–5 on 2.7.1. `a2a-sdk` 1.1.2, Python 3.12."; Issue 1 workaround → `agents/address/interceptor.py` (`pending_resume_target` + `resume_stamping_interceptor`, no leading underscore); Issue 2 workaround → `agents/address/wire.py` (`extract_exchange_turn`, `_TAG_START`/`_TAG_END`); Issue 2 Where → `part_converter.py` (`convert_a2a_part_to_genai_part`, `A2A_DATA_PART_START_TAG`), not `event_converter.py`. Archive entry appended to `docs/features/production-readiness.md` (this entry). `TODO.md`: S0-docs-10 flipped to `- [x]` with `_(done 2026-10-01; see docs/features/*)_`.

**Files touched (docs-only, no code change).** `docs/features.md`, `docs/features/benefits.md`, `docs/features/gcp-deployment.md`, `docs/upstream-adk-issues.md`, `docs/features/production-readiness.md`, `DECISIONS.md`, `TODO.md`.

---

## 2026-10-01 — S0-docs-11: Entry-point docs, eval index and repo conventions match the as-built repo; frontmatter wikilinks converted

**Problem.** The entry-point docs (`README.md`, `docs/local-dev.md`), the eval index (`wiki/evals/README.md`, `wiki/evals/benefits/description.md`) and the repo conventions (`CLAUDE.md`, `CRITICAL.md`, `scripts/_dev-env.sh`) described an earlier build. README called RFP "specified, not yet built". The eval index called Benefits a scaffold. The Benefits description listed 4 of its 5 records and twice called scanned PDFs "the deferred Document AI adapter". `docs/local-dev.md` had no RFP demo host and no email CLI. CLAUDE.md and CRITICAL.md kept "(from P6)" / "(from P7)" gating. The repo-shape blocks omitted `scripts/`, `docs/`, `tests/conformance`, `tests/perf`, `frontend/shared` and `frontend/e2e`. CRITICAL.md had no rows for live-GCP scripts, runtime skill policy or the secret-bearing email paths. `scripts/_dev-env.sh` stated a seam count. Separately, the YAML frontmatter of every `design/*.md` (29), `spec/*.md` (7) and `wiki/evals/**/*.md` (4) still carried Obsidian `"[[stem]]"` wikilinks, though the bodies had already been converted to relative markdown links.

Docs and comments only. No Python, TS, Terraform, skill or JSON change.

**Decisions.**
- **(a) Convert, not drop.** Each frontmatter `"[[stem]]"` became a quoted relative path from the file's own directory (`"seams.md"`, `"../spec/standards.md"`, `"../../../spec/demos/benefits-demo.md"`). The `up:` / `down:` / `related:` keys and their order are kept. This matches the earlier body conversion, keeps the graph, and is mechanically checkable (every path must exist). `type:` / `tags:` untouched.
- **(b) Resolution order: `design/` → `spec/` → `spec/demos/`.** `docs/features/a2a-edge.md` and `docs/features/email-intake.md` share stems with design files and are never targets. Every stem resolved uniquely. The one-off converter lived in `/tmp` and is not committed. It rewrote only the frontmatter, and the body-diff check confirms every `design/` body is byte-identical to HEAD.
- **(c) The TODO's module path was wrong.** The RFP demo host is `bridge.demos.rfp_demo:build_rfp_demo_app` (S0-arch-15 moved it into the fenced `bridge.demos` package), not `bridge.dashboard.rfp_demo`. The docs use the real path. The S0-arch-20 TODO text already has it right.
- **(d) The RFP eval stays a scaffold, deliberately and truthfully.** `wiki/evals/rfp/expected.json` has `documents: []` and `"status": "scaffold"`. The index keeps the RFP row as a scaffold and says where the RFP demo's inputs come from (the Benefits conformance corpus).
- **(e) The RFP demo host is an alternative to `run-all.sh`.** Its recipe (copied from the module docstring) runs the dashboard Vite app on the default port (5173) and the portal on 5174, which collide with the run-all Provider Portal and Agent Console. The local-dev port-table row records 5173 / 5174, not the plan's draft "5175 / 5174": neither `vite.config.ts` pins a port, so `pnpm --filter ops-dashboard dev` takes Vite's default.
- **(f) No seam numeral.** `_dev-env.sh` now says "every storage/infra seam" and names extraction as its own axis (`BRIDGE_EXTRACTION_ENGINE`, fixture by default). This follows S0-docs-7 decision 2.
- **(g) CRITICAL.md rows are one path each.** The six new rows are `scripts/deploy.sh`, `scripts/parity.sh`, `skills/`, `bridge/email_ingress/`, `bridge/edge/email_ingress.py` and `.env.example`, one backticked path per first-column cell so a path parser can read them. The five existing paths are unchanged. `bridge/email_ingress/` and `bridge/edge/email_ingress.py` already escalate through `bridge/`; the rows are kept to record *why* (secrets, the public webhook).

**SPEC PROPOSAL (not applied — `spec/` is protected).** Frontmatter only, bodies untouched. The 7 spec files would take the same conversion. This is the converter's `--print` output, exactly:

```diff
--- a/spec/cujs.md
+++ b/spec/cujs.md
@@ -4,3 +4,3 @@
-  - "[[patterns]]"
-  - "[[demo-suite]]"
-  - "[[collect]]"
+  - "../design/patterns.md"
+  - "demos/demo-suite.md"
+  - "../design/collect.md"
--- a/spec/open-questions.md
+++ b/spec/open-questions.md
@@ -4 +4 @@
-  - "[[demo-suite]]"
+  - "demos/demo-suite.md"
--- a/spec/standards.md
+++ b/spec/standards.md
@@ -4,5 +4,5 @@
-  - "[[a2a-edge]]"
-  - "[[a2a-consumer]]"
-  - "[[seams]]"
-  - "[[adk]]"
-  - "[[dual-path]]"
+  - "../design/a2a-edge.md"
+  - "../design/a2a-consumer.md"
+  - "../design/seams.md"
+  - "../design/adk.md"
+  - "../design/dual-path.md"
--- a/spec/demos/address-demo.md
+++ b/spec/demos/address-demo.md
@@ -4,4 +4,4 @@
-  - "[[demo-suite]]"
-  - "[[dual-path]]"
-  - "[[collect]]"
-  - "[[proactive]]"
+  - "demo-suite.md"
+  - "../../design/dual-path.md"
+  - "../../design/collect.md"
+  - "../../design/proactive.md"
--- a/spec/demos/benefits-demo.md
+++ b/spec/demos/benefits-demo.md
@@ -4,5 +4,5 @@
-  - "[[demo-suite]]"
-  - "[[patterns]]"
-  - "[[a2a-edge]]"
-  - "[[a2ui-edge]]"
-  - "[[disposition]]"
+  - "demo-suite.md"
+  - "../../design/patterns.md"
+  - "../../design/a2a-edge.md"
+  - "../../design/a2ui-edge.md"
+  - "../../design/disposition.md"
--- a/spec/demos/demo-suite.md
+++ b/spec/demos/demo-suite.md
@@ -4,4 +4,4 @@
-  - "[[address-demo]]"
-  - "[[benefits-demo]]"
-  - "[[rfp-demo]]"
-  - "[[collect]]"
+  - "address-demo.md"
+  - "benefits-demo.md"
+  - "rfp-demo.md"
+  - "../../design/collect.md"
@@ -9,2 +9,2 @@
-  - "[[bridge]]"
-  - "[[patterns]]"
+  - "../../design/bridge.md"
+  - "../../design/patterns.md"
--- a/spec/demos/rfp-demo.md
+++ b/spec/demos/rfp-demo.md
@@ -4,4 +4,4 @@
-  - "[[demo-suite]]"
-  - "[[collect]]"
-  - "[[skills]]"
-  - "[[disposition]]"
+  - "demo-suite.md"
+  - "../../design/collect.md"
+  - "../../design/skills.md"
+  - "../../design/disposition.md"
```

**Gotchas.**
- The wikilink false positives are left alone: `docs/features/benefits.md` (`Callable[[str], float]`) and mentions in `TODO.md` / `DECISIONS.md`.
- `wiki/evals/**/*.json` and the corpus PDFs are untouched. The PDFs are byte-equality-tested.
- The anchor `#### Core vs. demo hosts` in `design/architecture.md` is untouched because the bodies are byte-identical.
- README's platform-pillar list (`Memory Bank`) mirrors `design/bridge.md:27` and was left alone.
- The README anchor `docs/local-dev.md#rfp-demo-host` resolves to the new `## RFP demo host` heading.

**Follow-ups (not edited here).**
- `wiki/evals/benefits/expected.json` `note` still says "Scanned/raster (OCR) PDFs are the deferred Document AI adapter". It is the oracle file, so the fix needs its own CRITICAL edit. The `benefits-carrier-b-usd-en` record's `note` also still says "Coverage gap / disposition are later beats".
- `scripts/_dev-env.sh` does not unset `BRIDGE_EMAIL_BUCKET` / `BRIDGE_EXCHANGE_DB_URL` alongside the other deployed-path envs. That is a behavior change, so it was only noted here.
- README, `design/bridge.md:27` and `design/adk.md:75` still list "Memory Bank" as a platform pillar.
- Applying the SPEC PROPOSAL above is a spec-owner action.

**Verification.**
- T1: `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped, the same as the baseline, with no new skips.
- T2: no `[[` left under `design/` or `wiki/evals/` (spec still 7 files). The frontmatter YAML loads and every up/down/related path resolves (`bad 0`). The only body changes are in `wiki/evals/README.md` and `wiki/evals/benefits/description.md`. The stale-claim greps are empty. CLAUDE.md shape rows and the 6 CRITICAL rows are present. The RFP demo host serves `GET /ops/rfps/acme-vb-2027` with `"process":"benefits-rfp"`. The email CLI `--help` lists `land, subscribe, renew, drain`. Every relative link in the edited docs resolves. `git diff --name-only -- spec/` is empty.

## 2026-10-01 — S0-docs-11: entry docs, eval index and repo conventions sync to as-built

**Task:** S0-docs-11 · **Area:** docs (Sprint 0 hardening)

**Problem.** Entry-point docs (README.md, docs/local-dev.md), the eval index (wiki/evals/README.md, wiki/evals/benefits/description.md) and repo conventions (CLAUDE.md, CRITICAL.md, scripts/_dev-env.sh) described an earlier build. README said RFP was "specified, not yet built"; local-dev lacked the RFP demo host and email CLI; the eval index said Benefits had 4 carrier quotes; CLAUDE.md said "Frontend (from P6)" and "GCP adapter (from P7)"; CRITICAL.md lacked the new secret-bearing and live-apply paths. Separately, every `design/*.md` (29 files) and `wiki/evals/**/*.md` (4 files) carried YAML frontmatter with Obsidian-style `"[[stem]]"` wikilinks under `up:` / `down:` / `related:` keys. Document bodies had already been converted to relative markdown links (DECISIONS.md around line 2398); only the frontmatter was left.

**Docs and comments only.** No Python, TS, Terraform, skill or JSON change. Baseline: `uv run pytest -q` gave 1738 passed / 97 skipped; `uv run ruff check` clean. The result is identical.

**Decision: convert, not drop.** Each `"[[stem]]"` converted to a **quoted relative path from the file's own directory** (e.g., `"seams.md"`, `"../spec/standards.md"`, `"../../../spec/demos/benefits-demo.md"`). Resolution order: design → spec → spec/demos, ignoring docs/. Reasons: matches the body conversion that already happened, keeps the graph information, is mechanically checkable (every path must exist).

**Built (docs and comments only; no code or test change).**

1. **Frontmatter conversion (design/ and wiki/evals/ only).** A one-off script (not committed) converted 29 design/*.md files and 4 wiki/evals/ description files. Every `"[[stem]]"` inside the frontmatter replaced with a quoted relative path. For spec/ files (cujs.md, standards.md, open-questions.md, demos/*.md), the script generated a dry-run diff (the SPEC PROPOSAL below) — spec/ is protected, so no spec/ file was written.

2. **README.md.**
   - Line 22-25: RFP now described as built (emergent Collect with RFP board, consumer /rfps read API, manual chase, carrier checklist, email intake, served locally by RFP demo host).
   - Line 47: Added one-line pointer to RFP demo host as separate :8010 host.
   - Repo map (lines 83-96): added `scripts/` row; changed frontend to "four apps + shared lib + e2e"; changed tests to "shared seam suite (local + gcp) + golden runs + conformance + perf"; changed wiki/evals to "the eval corpus (Address + Benefits oracles)".
   - Line 107: changed "layout and first-cycle scope" to "the as-built repo and module map".
   - Platform-pillar list (lines 29-31) left alone (mirrors design/bridge.md:27, the design SoT; pillar drift is a separate follow-up).

3. **docs/local-dev.md.**
   - Port table (line 20-28): added row for 8010 (RFP demo host).
   - Override sentence (line 30-31): appended note that RFP demo host takes uvicorn --port.
   - New section "## RFP demo host": 3-command recipe, alternative to run-all.sh (portal Vite port 5174 collides with Agent Console), seeded acme-vb-2027 (benefits-rfp, from wiki/evals/benefits/conformance.json), BRIDGE_EXCHANGE_DB_URL persistence option, email env needed to bind processor.
   - New section "## Email intake CLI": land/drain local loop, subscribe/renew for Graph path, env requirements, why drain calls the host, secret env vars.
   - Deployed path (line 57-61): added note that deployed RFP board host is bridge.dashboard.board_host (internal-zone <service>-rfp-board).

4. **wiki/evals/README.md** (CRITICAL path).
   - Frontmatter: done by step 1 ("../../spec/demos/demo-suite.md").
   - Layout block (line 16-22): added conformance.json row.
   - Line 29 Benefits row: changed to "5 carrier-quote records (expected.json: normalize, standard-gap and revise oracles, incl. Carrier B's revised -v2), 2 consumer-conformance records (conformance.json), 3 native-digital PDFs".
   - Line 30 RFP row: kept "scaffold (structure only)", appended "— the RFP demo host and RFP tests read the Benefits conformance corpus".
   - Line 32: changed to "Address and Benefits carry full corpora + expected outputs. RFP provides the eval structure only; its demo inputs come from the Benefits conformance corpus."

5. **wiki/evals/benefits/description.md** (CRITICAL path).
   - Line 10: changed to "oracle for normalize (beat 3), the standard-gap disposition (beat 4, each record's expected_gaps) and the revise loop (beat 5, Carrier B's revised -v2 quote). The comparison view and the CUJ-3 / CUJ-11 legs derive from it."
   - Line 14: changed to name Carrier B's revised quote (5 records).
   - Table (line 20-25): added row for benefits-carrier-b-usd-en-v2.
   - Under table: added sentence "expected_gaps is [] for every record except benefits-carrier-b-usd-en (no_orthodontics_exclusion, under default skills/benefits-procurement/assets/standard.yaml)."
   - Lines 38 and 43 (two "deferred Document AI adapter" sentences): replaced with description of docai engine (Gemini + client's Document AI OCR pre-pass, design/docai-prepass.md); no raster PDF committed; tests/test_docai_extraction.py renders carrier-C's scanned twin at test time (tests/corpus_scanned_pdf.py).
   - Line 42: changed to "five carrier-quote records (four carriers + Carrier B's revised v2) with their expected_normalized and expected_gaps oracle blocks".
   - Line 46: changed to ".gitkeep — retained; carriers a, b, b-v2 and d are text-only records".
   - Did NOT edit wiki/evals/benefits/expected.json (the oracle file; its note still says "deferred Document AI adapter" — follow-up).

6. **CLAUDE.md** (project instructions).
   - Line 15: changed to "Frontend: pnpm workspaces (frontend/*), React + TS + Vite + MUI; frontend/shared is the shared lib, frontend/e2e the Playwright suite."
   - Repo-shape block (line 21-32): changed agents to "service-agent consumers + demo drivers (Address, Benefits carriers, console)"; frontend to "the four apps + shared lib + e2e"; terraform to "IaC by substrate primitive" (drop "(P7)"); tests to "the shared seam suite (local + gcp adapters) + demo golden runs; conformance/ (Benefits consumer conformance), perf/ (scale harness)"; wiki/evals to "the eval corpus (Address + Benefits oracles) — a runtime fixture, referenced by code + tests". Added rows for docs/ and scripts/.
   - Line 37: changed "and (from P7) a GCP adapter" to "and a GCP adapter".

7. **CRITICAL.md.**
   - Line 12 terraform row: drop "(P7)".
   - Line 10 wiki/evals row: changed "(Address oracle)" to "(Address + Benefits oracles)" and "… for all Address behavior" to "… for Address and Benefits behavior".
   - Added rows (one backticked path per row):
     * scripts/deploy.sh: live GCP apply (image build + two-phase Terraform apply against real project; needs live auth). A mistake mutates real infra.
     * scripts/parity.sh: the deployed-path gate; runs against live GCP resources.
     * skills/: runtime policy the Bridge loads. Thresholds, SLA ladders, satisfaction and standard rules change verdicts without a code change. Skill frontmatter is asserted by tests (tests/address/test_skills.py, tests/test_benefits_skills.py).
     * bridge/email_ingress/: secret-bearing. The HMAC correlation token (BRIDGE_EMAIL_TOKEN_SECRET) and the Graph app credential (BRIDGE_EMAIL_GRAPH_CLIENT_SECRET). Never log or commit a value.
     * bridge/edge/email_ingress.py: the public (allUsers) email webhook. It checks the Graph subscription BRIDGE_EMAIL_CLIENT_STATE (secret). It is the one Gateway exception in design/zones.md.
     * .env.example: the index of every env var, including the SECRET ones (BRIDGE_EMAIL_TOKEN_SECRET, BRIDGE_EMAIL_GRAPH_CLIENT_SECRET, BRIDGE_EMAIL_CLIENT_STATE, DATABASE_URL). Names + blank defaults only, never a value.

8. **scripts/_dev-env.sh:11-12** (comment only).
   - Changed "# Seams: all eight boundaries on their local adapters" to "# Seams: every storage/infra seam on its local adapter". Kept the following comment line, and added one comment line: "# Extraction is its own axis (BRIDGE_EXTRACTION_ENGINE, fixture by default)." No numeral. Did NOT change any export / unset. Behavior changes (also unsetting BRIDGE_EMAIL_BUCKET / BRIDGE_EXCHANGE_DB_URL) are a follow-up noted here only.

**Ground truth (each item checked).**
- **RFP demo host:** module is `bridge.demos.rfp_demo:build_rfp_demo_app` (the TODO's `bridge.dashboard.rfp_demo` was wrong — S0-arch-15 moved the host into the fenced bridge.demos package). Serves on :8010, mounts /ops (build_dashboard_app) and /portal (build_portal_app). Seeds RFP acme-vb-2027 (process benefits-rfp) from Benefits conformance corpus. Smoke-tested: GET /ops/rfps → 200, GET /ops/rfps/acme-vb-2027 → JSON with "process":"benefits-rfp". scripts/run-all.sh does NOT start it; canonical run recipe is bridge/demos/rfp_demo.py docstring. Portal Vite port 5174 collides with Agent Console, so RFP host is alternative to run-all.sh, not companion.
- **Email CLI:** `uv run python -m bridge.email_ingress {land,subscribe,renew,drain}`. land lands one .eml on email_ingress seam; drain POSTs <url>/email/drain on board-owning host; subscribe/renew manage Graph subscription. Deployed counterpart: bridge.dashboard.board_host.
- **Secret-bearing email env:** BRIDGE_EMAIL_TOKEN_SECRET (HMAC key for RFP correlation token), BRIDGE_EMAIL_GRAPH_CLIENT_SECRET (Graph app credential), BRIDGE_EMAIL_CLIENT_STATE (subscription clientState, checked by webhook), DATABASE_URL. All marked SECRET in .env.example:139-148.
- **Benefits eval corpus:** expected.json has 5 records (carriers a/b/c/d plus benefits-carrier-b-usd-en-v2). v2 is identical to carrier-b except exclusions: "cosmetic surgery"; expected_normalized premium 1500.00, deductible 750.00, coverage dental, vision; expected_gaps []. Only benefits-carrier-b-usd-en has a gap (["no_orthodontics_exclusion"], under default skills/benefits-procurement/assets/standard.yaml). v2 is used by tests/test_negotiate.py, tests/test_cuj11_negotiate_bind_rollup.py, tests/test_program_comparison_view.py (REVISED_CARRIER_B). conformance.json has 2 records. images/ has 3 native-digital PDFs plus .gitkeep.
- **Scanned PDFs:** the docai engine's envelope (Gemini + client's Document AI processor as OCR pre-pass, bridge/extraction/docai.py, design/docai-prepass.md). No raster PDF committed to corpus. Scanned twin of carrier-C is rendered at test time by tests/corpus_scanned_pdf.py and asserted in tests/test_docai_extraction.py::test_scanned_pdf_reaches_the_records_terminal_outcome. "Deferred Document AI adapter" is false.
- **RFP eval genuinely still a scaffold:** wiki/evals/rfp/expected.json has documents: [] and "status": "scaffold". RFP demo host and tests/test_rfp*.py read Benefits conformance corpus. Keep RFP row as scaffold, but say where RFP demo's inputs come from.
- **Seams:** nine (sessions, task_store, exchange_store, skill_registry, memory_bank, scheduler, a2a_client, email_ingress, plus extraction). Extraction is its own axis (BRIDGE_EXTRACTION_ENGINE=fixture|gemini|docai, fixture default), not governed by BRIDGE_SEAM_MODE. S0-docs-7 decision 2 says never state a seam count (numeral drifts).
- **Repo dirs missing from shape blocks:** scripts/ (run-*.sh, _dev-env.sh, deploy.sh, parity.sh), docs/, tests/conformance/, tests/perf/, frontend/shared/, frontend/e2e/, agents/ holds address/, benefits/, console/, bridge/demos/.
- **Frontmatter wikilinks:** 133 `"[[stem]]"` occurrences total in design/ frontmatter, all double-quoted, all inside frontmatter. None in bodies (checked with awk). Every stem resolves uniquely to design/<stem>.md or spec/<stem>.md or spec/demos/<stem>.md. docs/features/a2a-edge.md and docs/features/email-intake.md share stems but are NOT targets (resolve design → spec → spec/demos, ignore docs/). False positives left alone: docs/features/benefits.md:651 (Callable[[str], float]), plus mentions in TODO.md and DECISIONS.md.

**SPEC PROPOSAL (not applied — spec/ is protected).** The following frontmatter replacements are apply-ready. Bodies are untouched. Applying is a spec-owner action.

### spec/cujs.md
Lines 3-8 become:
```yaml
up:
  - "../design/a2a-edge.md"
  - "../design/collect.md"
  - "../design/outbound.md"
related: "demo-suite.md"
```

### spec/standards.md
Lines 3-8 become:
```yaml
up:
  - "../design/a2a-edge.md"
related:
  - "../design/wire.md"
  - "../design/zones.md"
  - "../design/seams.md"
```

### spec/open-questions.md
Lines 3-6 become:
```yaml
up:
  - "../design/bridge.md"
  - "../design/architecture.md"
related: "standards.md"
```

### spec/demos/address-demo.md
Lines 3-8 become:
```yaml
up: "demo-suite.md"
related:
  - "../../design/collect.md"
  - "../../design/address-doctype.md"
  - "../../design/a2ui-edge.md"
  - "../../design/disposition.md"
```

### spec/demos/benefits-demo.md
Lines 3-9 become:
```yaml
up: "demo-suite.md"
related:
  - "../../design/collect.md"
  - "../../design/benefits-quote.md"
  - "../../design/program.md"
  - "../../design/negotiate.md"
  - "../../design/disposition.md"
```

### spec/demos/demo-suite.md
Lines 3-8 become:
```yaml
up: "../cujs.md"
related:
  - "address-demo.md"
  - "benefits-demo.md"
  - "rfp-demo.md"
  - "../../design/collect.md"
down:
  - "../../design/bridge.md"
  - "../../design/patterns.md"
```

### spec/demos/rfp-demo.md
Lines 3-7 become:
```yaml
up: "demo-suite.md"
related:
  - "../../design/collect.md"
  - "../../design/skills.md"
  - "../../design/disposition.md"
```

**Gotchas.**
- Never write spec/. git diff --name-only -- spec/ must be empty.
- Do not touch wiki/evals/**/*.json or any PDF. The PDFs are byte-equality-tested.
- The anchor `#### Core vs. demo hosts` in design/architecture.md is cited by tests/test_isolation_guard.py. The frontmatter script left bodies byte-identical.
- The isolation guard scans bridge/ docstrings. This task edits no bridge/ file.
- No seam numeral anywhere written.
- Status-free wording in README/local-dev: describe what runs, not "now built" / "shipped".
- docs/features/a2a-edge.md / email-intake.md share stems with design files. Never resolve a wikilink to docs/.

**Follow-ups (not edited here).**
- wiki/evals/benefits/expected.json note still says "deferred Document AI adapter". It is the oracle file, so it gets a separate CRITICAL edit.
- scripts/_dev-env.sh does not unset BRIDGE_EMAIL_BUCKET / BRIDGE_EXCHANGE_DB_URL.
- README / design/bridge.md:27 / design/adk.md:75 still list "Memory Bank" as a pillar.
- S0-arch-20 TODO text is correct (bridge.demos.rfp_demo).

**Verification.**
- T1: `uv run ruff check` clean; `uv run pytest -q` → exactly baseline 1738 passed / 97 skipped.
- T2 (all checks passing):
  1. No wikilinks left outside spec/: `grep -rn '\[\[' design wiki/evals --include=*.md` → no output. `grep -rln '"\[\[' spec` → still 7 files (spec is proposal-only).
  2. Frontmatter is valid YAML and every link resolves: bad 0.
  3. Bodies byte-identical: only wiki/evals/README.md and wiki/evals/benefits/description.md print (step 4-5 edited bodies); no design/ file prints.
  4. Stale claims gone: no "not yet built" in README, no "first-cycle" in README, only RFP row has "scaffold" in wiki/evals/README.md, no "deferred Document AI" in wiki/evals/benefits/description.md, benefits-carrier-b-usd-en-v2 present in table, no "from P6|from P7|(P6)|(P7)" in CLAUDE.md / CRITICAL.md, no "eight" in scripts/_dev-env.sh, `bash -n scripts/_dev-env.sh` → exit 0.
  5. Repo-shape and CRITICAL rows present: `grep -nE '^(scripts|docs)/' CLAUDE.md` → 2 lines; conformance/ + perf/ + frontend/shared + e2e present; 6 new CRITICAL rows present.
  6. Local-dev recipes work: RFP demo host serves GET /ops/rfps/acme-vb-2027 → JSON with "process":"benefits-rfp"; `uv run python -m bridge.email_ingress --help` lists land/subscribe/renew/drain; no bridge.dashboard.rfp_demo (wrong path).
  7. Every relative link resolves, including README anchor docs/local-dev.md#rfp-demo-host → `## RFP demo host`.
  8. Protected files: git diff --name-only lists only README.md, docs/local-dev.md, CLAUDE.md, CRITICAL.md, scripts/_dev-env.sh, design/*.md, wiki/evals/README.md, wiki/evals/{address,benefits,rfp}/description.md, DECISIONS.md, TODO.md, optionally docs/features/production-readiness.md. No spec/, bridge/, agents/, contract/, skills/, terraform/, or *.json.
  9. The spec proposal is recorded: `grep -n "SPEC PROPOSAL" DECISIONS.md | tail -1` is in the S0-docs-11 entry, covers all 7 spec files.

## 2026-10-01 — S0-arch-20: the showcase runner — one process, one card, one dashboard over shared stores

**Gap.** `spec/demos/demo-suite.md:18,30-38` and `spec/demos/rfp-demo.md:55` require one launch that boots one core with the Address, Benefits and RFP skills loaded, and exercises them concurrently on one Agent Card and one dashboard. `scripts/run-all.sh` boots separate hosts, each with its own in-memory store. The RFP board was a separate host (`bridge.demos.rfp_demo`, :8010) with no A2A edge and no program board. Nothing wired all three sources into one host.

**Decisions.**
1. **New fenced module `bridge/demos/showcase.py`** (`build_showcase_app`). It names demo skills, seeds synthetic data and reads `wiki/evals/`, so it lives in `bridge/demos/` (S0-arch-15 #7). Nothing outside `bridge/demos/` imports it (`tests/test_isolation_guard.py`).
2. **One composed Starlette app**: `[Mount("/ops", dashboard), Mount("/portal", portal), Mount("", edge)]`. The edge goes last because `Mount("")` matches everything. With the edge at the root, the card stays at `/.well-known/agent-card.json` and JSON-RPC at `/`, so the card url and executor base_url need no prefix (P7-5). The outer app is Starlette, not FastAPI, so no `/docs` / `/openapi.json` shadows an edge path.
3. **Shared sources built once and injected everywhere:**
   - one `PortalStore` → dashboard + portal;
   - one `RfpBoard` (`benefits-rfp`, over `exchange_store or exchange_store_from_env()`) → edge (`rfp_board=`, used as-is), dashboard (`rfps=`), portal (`rfps=`);
   - one `ProgramBoard` (`benefits-procurement` standard) → dashboard (`board=`).
   - The edge's task store stays seam-resolved.
4. **The RFP seed is reused, not copied.** `bridge/demos/rfp_demo.py` gains the public `seeded_rfp_board(*, clock, exchange_store) -> (RfpBoard, FixtureExtractor)` (seed only when `acme-vb-2027` is absent). `build_rfp_demo_app` calls it. The `_run` helper is renamed `run_sync` (one definition, imported by the showcase). Behavior is unchanged: `GET /ops/rfps/acme-vb-2027` with a pinned clock is byte-identical before and after (process, counts, cells).
5. **Seeds.** All synthetic and deterministic, run through `run_sync` so the factory works under `uvicorn --factory` and from a sync test.
   - Address: two portal sessions over `bridge.portal.session` with an explicit `FixtureExtractor()` (Address corpus). `bill-powerco-clean` → `done`, `bill-aquautil-clear` → `in_flight` (HITL queue item).
   - Benefits: `acme-2026`, `fan_out` to carriers a–d, v0 quotes normalized from `wiki/evals/benefits/expected.json`, `open_negotiation`, `ProgramBoard.put`. carrier-b routes `negotiate` (`no_orthodontics_exclusion`).
   - RFP: `seeded_rfp_board` (`acme-vb-2027`).
6. **Email wiring mirrors `rfp_demo`.** `email_processor_from_env(board, extraction=<conformance engine>)` unless injected; the drain loop runs in the outer app's lifespan when bound with interval > 0. No store close in the lifespan (the SQL loop thread is a daemon).
7. **`bridge.edge.*` is imported lazily inside the builder** (the S0-bug-3 #7 circular-import trap). `bridge.demos.showcase` joined the fresh-process import guard in `tests/test_email_served.py`.
8. **`scripts/run-showcase.sh`** (port 8011): sources `_dev-env.sh`, exports `BRIDGE_PUBLIC_URL` to the served origin, starts `uv run uvicorn --factory bridge.demos.showcase:build_showcase_app` plus (unless `SHOWCASE_APPS=0`) ops-dashboard :5175 and provider-portal :5173 pointed at the mounts, with the run-all pids/trap cleanup. It is an alternative to `run-all.sh` and the RFP demo host, not a companion (Vite ports collide). `run-all.sh` gains a header line pointing to it.

**Deviations from the plan.**
- `url` defaults to `None`, not `DEFAULT_URL`. `DEFAULT_URL` lives in `bridge.edge.card`, and importing it at module top would pull the `bridge.edge` package (the circular-import trap). `None` passes no `url` to `build_a2a_app`, whose own default honors `BRIDGE_PUBLIC_URL`, so behavior is the same.
- The card's url is under `supportedInterfaces[].url` (a2a v1 card shape); the top-level `url` is absent. The test and the T2 check read `supportedInterfaces`.
- The test asserts more than the plan's minimum: the two seeded Address sessions are exactly `done` / `in_flight`, and the HITL-queued session is the seeded aquautil session id from `app.state.address_session_ids`.
- `test_seeds_are_idempotent_over_a_durable_store` is implemented (not skipped): two builds over one sqlite `BRIDGE_EXCHANGE_DB_URL`; the second keeps exactly one `acme-vb-2027` with the same audit length.

**Known residuals (not fixed here).**
- (a) `/exchanges` returns portal sessions + RFPs, but not programs; programs are on `/programs` of the same host. demo-suite.md:36's "one query returns all three types" is met only across two endpoints of one host. Follow-up: programs join `/exchanges` (readmodel + ops-dashboard UI), or restate the spec.
- (b) No A2A path lands a Benefits program on a `ProgramBoard`; the showcase seeds it. `ProgramBoard` is in-memory (S1-benefits-7, durable deferred).
- (c) Three backing stores in one process (PortalStore, ExchangeStore, ProgramBoard), each shared by every surface in that process. Not one physical store.
- (d) The live use-case-add variant is CUJ-9's Driver and stays open.
- (e) Address exchanges driven over A2A land in the edge task store, not on `/exchanges` (the dashboard projects Path-B portal sessions).
- Out of scope: a deployed one-runtime composition. Each demo still runs deployed through the existing services. GCP parity is N/A for this local demo host; no seam or adapter changed.

**SPEC PROPOSAL (not edited).** `spec/demos/demo-suite.md`:
- Line 36: either keep "one query returns all three types" and open the follow-up in residual (a), or restate it as "one dashboard host serves all three types (`/exchanges` for Address + RFP, `/programs` for Benefits programs)".
- Name `tests/test_showcase.py::test_exercises_all_three_concurrently_on_one_core` (and `scripts/run-showcase.sh`) as the Driver evidence for the "one core, many demos" requirement (lines 18, 38) and for `spec/demos/rfp-demo.md:55`.

`TODO.md` S0-arch-20 is NOT flipped here: CRITICAL task, the mandatory reviewer flips + commits.

**Verification.**
- T1: `uv run ruff check` clean. `uv run pytest -q` → 1745 passed / 97 skipped (baseline 1738 / 97: +6 in `tests/test_showcase.py`, +1 fresh-import param; no new skips).
- T2:
  - `uv run pytest -q tests/test_showcase.py -v` → 6 passed, including `test_exercises_all_three_concurrently_on_one_core` (A2A address-proof gov-id-clean → COMPLETED; A2A benefits-rfp open on `showcase-live-rfp` → INPUT_REQUIRED; portal session + bill-powerco-clean upload; GET `/ops/programs`; all in one `asyncio.gather`, then `/ops/rfps` ⊇ {acme-vb-2027, showcase-live-rfp}, `/ops/exchanges` 3 address-proof + both RFPs, `/ops/programs` acme-2026).
  - `SHOWCASE_APPS=0 bash scripts/run-showcase.sh` (:8011): card skills `address-proof`, `benefits-procurement`, `benefits-rfp`; interface url `http://127.0.0.1:8011/`. `/ops/exchanges`: `benefits-rfp acme-vb-2027 in_flight` + 2 address-proof (one `done`, one `in_flight`). `/ops/programs`: acme-2026, 4 legs, carrier-b `negotiate`. `/ops/rfps`: `acme-vb-2027`. A live portal session + bill-powerco-clean upload → `"done"` on `/ops/exchanges`. A live A2A open on `live-check-rfp` → `TASK_STATE_INPUT_REQUIRED`, and `/ops/rfps` lists `acme-vb-2027` + `live-check-rfp`. SIGINT to the group and SIGTERM to the script both leave no uvicorn child.
  - RFP demo host regression: `GET /ops/rfps/acme-vb-2027` (pinned clock) byte-identical HEAD vs working tree: `benefits-rfp`, counts `received 1, incomplete 1, acknowledged 2, invited 2`.
  - Isolation: no `bridge.demos` import outside `bridge/demos/`; `tests/test_isolation_guard.py` green.
  - The pnpm Vite apps (optional check 5) were not run.

## 2026-10-02 — Applied the spec proposals (S0-docs-6, S0-docs-7, S0-docs-11, S0-arch-20)

Applied by the spec owner's instruction ("write them"). Every block went in verbatim from its DECISIONS.md entry, except where noted.
- `spec/cujs.md`: S0-docs-6 full body. This supersedes S3-edge-8 P-1..P-5. It adds CUJ-14 (off-script intents agent-to-agent) and CUJ-15 (email reply intake), restates CUJ-13, spells every Driver id in full, and drops the "First cycle" column.
- `spec/standards.md`, `spec/open-questions.md`, `spec/demos/benefits-demo.md`, `spec/demos/rfp-demo.md:64`: S0-docs-7 blocks.
- `spec/demos/demo-suite.md:36`: S0-arch-20's option (b). The line is restated as "one dashboard host serves all three types (`/exchanges` + `/programs`)" rather than keeping "one query" and opening a follow-up. The showcase-runner sentence names `scripts/run-showcase.sh` and its Driver, `tests/test_showcase.py::test_exercises_all_three_concurrently_on_one_core`.
- Frontmatter in all 7 files: S0-docs-11's **first** proposal. It is a straight `[[stem]]` → relative-path conversion that keeps the existing targets. The later, restructured variant, which swaps in different targets, was not used.
- Not folded (unchanged; still open for a later pass):
  - a conformance-run Driver line;
  - a scanned-quote Driver;
  - `tests/test_disposition_calibration.py` as a second CUJ-5 Driver;
  - the HITL resolving-principal clarification;
  - the "ADK authoritative at 2.7.0" settled bullet (the lock is 2.7.1; the pin's floor is 2.7.0).

Checks:
- All 54 Driver/Judge ids in `spec/cujs.md` → 117 passed / 1 skipped (live-Gemini gate) in both `BRIDGE_EDGE_MODE=deterministic` and `hybrid`.
- Every relative link in spec/ resolves.
- `uv run pytest -q` → 1745 passed / 97 skipped; ruff clean.

## 2026-10-02 — Sprint 4 scoped: CUJ-9 live use-case-add (`S4-usecase-1…5`)

- **Driver, not new capability first.** `list_skills()` and the Agent Card already read the registry live (CUJ-8), so the sprint opens with an audit (S4-usecase-1) for per-request skill consumers that capture at build/start, then builds the CUJ-9 Driver.
- **Synthetic use case.** The Driver uploads the synthetic `supplier-onboarding` skill (shared fixture extracted from `tests/test_rfp_second_use_case.py`), not the shipped `benefits-rfp`: it proves industry-neutrality and keeps the test independent of the demo skills.
- **Spec stays untouched.** The CUJ-9 `Driver:` line and a bad-upload trap are a PROPOSAL, written out by S4-usecase-5; the spec owner applies it.

## 2026-10-03 — S4-usecase-1: skill-consumer read-time audit (two captures fixed)

- **Found and fixed (CRITICAL path `bridge/`).**
  1. The policy loaders (`load_sla_policy`, `load_disposition_policy`, `load_rfp_policy`, `load_negotiate_policy`, `load_program_standard`, and the frontmatter/policy/standard doc readers) defaulted `skills_dir` to the import-time `SKILLS_DIR` constant, the repo `skills/`. Callers that pass no `skills_dir` (`portal/session.py`, `edge/rfp_open.py`, `dashboard/board_host.py`, `timewarp/host.py`, `demos/rfp_demo.py`) therefore never saw a skill dropped into `BRIDGE_SKILLS_DIR`. The default is now `None`, resolved per call by the new `bridge.sla.skills_root()`, the same rule `LocalSkillRegistry` uses.
  2. `GeminiExtractor._catalog` read the doctype catalog once per extractor. One extractor lives as long as the app, so a doctype added after the first extract never reached the extraction prompt or the pinned response schema. It now reads per `extract` and rebuilds only when the catalog changed. This reverses the earlier "ONE registry read per extractor" decision: the live-add property outweighs one directory listing per model call. The old once-per-extractor test became `test_schema_catalog_is_read_per_extract_not_at_construction`.
- **Audited, not captured:** the card builder, `candidate_doctypes_for` / `hitl_mode_for` / `satisfaction_for`, Path-A validation (its `lru_cache` is keyed by schema content). `bridge/calibration.py` stays an operator CLI over the repo `skills/`. The table is in `design/skills.md`.
- **Tests:** `tests/test_live_skill_reads.py` (loaders; import-fails / red before) and `test_doctype_added_after_first_extract_reaches_the_next_prompt`.
- **Gotcha:** never write `skills_dir: Path = SKILLS_DIR` in a new loader; use `skills_dir: Path | None = None` and `skills_root(skills_dir)`.

## 2026-10-03 — S4-usecase-2: the CUJ-9 Driver (test-only; no bridge/ change)

- **Parking an Address exchange.** On `build_a2a_app` the deterministic Collector turn is always terminal, so an Address exchange can only be genuinely `INPUT_REQUIRED` through the R3 HITL park: a hybrid edge with `ScriptedLlm`, one round of `bill-powerco-clean` + `bill-aquautil-clear` (PowerCo accepted, AquaUtil parks). The reviewer's resolution completes it with reason `needs_review`, two accepted entries (`power-co`, `aqua-util`). No stub collector.
- **Control before upload.** `BRIDGE_SKILLS_DIR` is process-global, so the control core runs to completion BEFORE the install, and the registry is asserted still Address-only at that point. Never reorder (mutation-checked).
- **FINDING (for S4-usecase-4/5, not fixed).** `build_a2a_app` binds the RFP opener once at build from `BRIDGE_RFP_PROCESS` (`rfp_board_from_env`, which raises when the skill is absent). A live-added process therefore runs on the bounded Collector path, not as an A2A RFP opener, without a rebuild. The Driver uses `CollectRequest(process="supplier-onboarding")` with the Collector reading `candidate_doctypes_for` live.
- **"Usable" is checked three ways:** Path-B collect accepted (and `unsupported_doctype` before the upload), Path-A schema enforcement (an undeclared `tin` key is `schema_invalid`), and the new process's artifacts persist and GET 200 under scoped `/artifacts/` URIs.
- **Install mechanism.** `install_skill` through the `skill_registry` seam, doctypes first, process last (never writes `skills/`). S4-usecase-4 reuses `install_supplier_onboarding` under the gcp adapter.
- **Shared fixture.** `tests/_skill_fixtures.py` holds the supplier-onboarding constants; no test module imports another's constants any more.
- **Driver node id (for the S4-usecase-5 spec proposal):** `tests/test_live_use_case_add.py::test_whole_use_case_added_live_while_an_exchange_is_in_flight`.
- **Gotcha:** after the upload there are two process skills, so text-only intake no longer falls back to "the only process"; the Driver sends structured traffic only.

## 2026-10-03 — S4-usecase-3: a bad live upload is refused, not propagated

- **Two layers of refusal.**
  - (a) **Structural, per folder.** `bridge.edge.skills.skill_meta_from_dir` is now the strict validator. Every refusal is a `ValueError` naming the folder and the reason: no frontmatter fence; frontmatter YAML error, frontmatter not a mapping, or `metadata` not a mapping; missing `name` / `description` / `metadata.bridge-kind` (previously a bare `KeyError`); `bridge-kind` not `process` | `doctype` (`SkillMeta.kind` is now `Literal["process", "doctype"]`); `name` differs from the folder name; a process's policy file present but not a YAML mapping; a doctype's schema asset present but not valid JSON; a policy / schema pointer escaping the skill dir. Absent policy / schema stays legitimate (absent = legit, present-but-corrupt = refuse). A pydantic `ValidationError` (bad `bridge-hitl`) is raised as-is; it already subclasses `ValueError`.
  - Both adapters' `list_skills` go through ONE helper, `listed_skill_meta`: it catches `(ValueError, OSError)` only (never `except Exception`), logs a WARNING on logger `bridge.edge.skills` (`skill folder %r skipped: <reason>`) on every call (no dedupe state), and skips. `get_skill` stays strict.
  - (b) **Cross-skill, per read.** `loaded_skills()` returns `resolve_label_spaces(registry.list_skills())`. A process's label space is its declared candidates that are installed, in declared order. Some missing → narrowed + WARNING (executor label space == card tags; a document of an uninstalled doctype is `unsupported_doctype`, no longer accepted without a schema). None installed → dropped + WARNING (absent from the card; `candidate_doctypes_for` → `[]`, S0-arch-18; `hitl_mode_for` → `report`; no longer defeats text intake's single-process fallback). Empty declared list → unchanged (S0-arch-18 case (b)). Not in `agent_card_from_skills`, which stays pure (explicit `skills=` callers keep its doctype-tag filter). `doctype_schemas()` reads `registry.list_skills()` directly and gets layer (a) only, which is correct for doctypes.
- **Install refuses before any write.** A shared `validate_skill_upload(name, skill_md, assets)` stages the upload in a temp dir and parses it strictly. Local `install_skill`: name/root checks → every asset path checked against the REAL `skill_dir` (hoisted out of the write loop) → `validate_skill_upload` → write. GCP: `_validate_skill_paths` → `validate_skill_upload` → `_bucket()` (no client is built for a bad upload). Cross-skill state is not checked at install (a process may precede its doctypes, the CUJ-8 order).
- **Supersedes part of S0-arch-5/8.** A present-but-unparsable doctype schema no longer makes `doctype_schemas()` raise for every doctype. The refuse-loud now happens at install (`ValueError` naming the skill), and at listing the doctype is NOT INSTALLED (skipped, logged; its documents are `unsupported_doctype`). The intent ("an authoring bug never degrades to accept-anything") is kept. The `raise` in `doctype_schemas()` stays as defense in depth (reachable only by a write racing list → read). `tests/test_gemini_extraction.py::test_unparsable_doctype_schema_fails_loud` became `test_unparsable_doctype_schema_is_refused_not_propagated`.
- **Residuals (documented, not fixed).**
  1. A raw filesystem / `gsutil cp` overwrite of an existing skill with garbage removes that skill (fail closed, logged). There is no last-known-good cache: it would need adapter state, and `get_adapter` builds a fresh adapter per call. The local write is still non-atomic (`SKILL.md` before assets); a concurrent listing of a half-written folder is covered by layers (a) and (b).
  2. `bridge.rfp.load_rfp_policy` reads `bridge-candidate-doctypes` straight from frontmatter, un-narrowed. The RFP opener is build-bound (S4-usecase-2 finding); this is S4-usecase-4's concern.
- **PROPOSAL for S4-usecase-5 (spec not edited).** The CUJ-9 bad-upload trap's companion Driver node id: `tests/test_live_use_case_add.py::test_bad_live_upload_is_refused_not_propagated`.
- **Tests.** `tests/test_skill_upload_refusal.py` (26: each structural reason skipped with a logged reason, `get_skill` strict, repaired → listed, `resolve_label_spaces` units, `candidate_doctypes_for` narrowing, text fallback, install refusals writing nothing); the bad-upload Driver; GCP fake-bucket parity (`test_list_skills_skips_malformed_with_a_logged_reason`, `test_install_refuses_malformed_before_any_io`); seam test `test_skill_registry_skips_malformed_folder` (its gcp param is `BRIDGE_TEST_GCP`-gated per the seam convention: the suite's skip count goes 97 → 98). Fixture: `tests/_skill_fixtures.py::BAD_UPLOAD_SKILLS` / `write_bad_upload` (raw drop; the seam would refuse it).
- **Gotcha.** A new test that writes a skill folder must use a frontmatter `name` equal to the folder name, and must not write an empty / non-mapping `policy.yaml` for a process skill, or the folder is now skipped.

## 2026-10-03 — S4-usecase-4: a live-added RFP use case opens on the shared board next to a running exchange

- **1. `RfpBoard` gains an optional live policy resolver, not a new class.** `RfpBoard(*, policy, clock=None, store=None, live_policy=None)`. `policy` stays the board's **home** process (`BRIDGE_RFP_PROCESS`, loaded once at build, used as-is). `policy_for(process)`: home → `self.policy`; else `live_policy(process)` when bound; else `ValueError`. `serves(process)` = `policy_for` catching only `ValueError`. `live_policy=None` is exactly the previous single-process board (every direct `RfpBoard(policy=...)` caller unchanged; the single-mode `put` refusal keeps its exact message).
- **2. Every per-RFP consumer uses `board.policy_for(rfp.process)`.**
  - `RfpBoard.put` (live mode raises the resolver's `ValueError`); `items()` / `statuses()` memoize per call and **skip** an RFP whose process no longer resolves, with a WARNING on `bridge.dashboard.board` (one removed skill cannot 500 `/rfps`); `get()` → `KeyError` chained (404).
  - `open_or_update_rfp`: the request's process policy for open / update / read; an existing RFP id with a request of another process → `ValueError` (surfaced `invalid_rfp_request`).
  - `EmailProcessor` (CRITICAL): resolves the policy ONCE right after correlation, before any attachment is stored or extracted; `ValueError` → quarantined `process_unavailable` (new `REASON_PROCESS_UNAVAILABLE`); `_filed` (`item_field`), `signals_from_result(candidate_doctypes=...)` and `receive(policy=...)` use it. `correlate` still scans all RFPs (tokens are process-agnostic).
  - Portal `checklist_for` / `upload_quote`: unresolvable → `KeyError` (404), resolved before extraction.
- **3. The opener routes on `board.serves(process)`, not equality** (`bridge/edge/executor.py`). The home check is free; a live check is a synchronous registry read on the loop (same posture as `candidate_doctypes_for` / `hitl_mode_for` there). With no board bound the edge is unchanged (the S4-usecase-2 Collector path still runs).
- **4. The live resolver is `bridge/edge/skills.py::rfp_policy_for(process)`**, next to the other live readers. It reads through the `skill_registry` seam: the process must be a `kind == "process"` entry of `loaded_skills()` (the narrowed label space; else `ValueError("... is not an installed process skill")`); `SKILL.md` and the policy asset come from `registry.read_asset`, parsed by the shared pure validators; the returned candidates are the narrowed ones (== the card tags). Only `ValueError` / `KeyError` / `OSError` / `yaml.YAMLError` are caught (converted to `ValueError`).
- **5. Both host factories turn live mode on**: `rfp_board_from_env` (top-level import of `bridge.edge.skills`, same package) and `build_rfp_board_app` (lazy import inside the builder: `bridge/dashboard` must not import `bridge.edge` at module top). An injected `rfp_board=` / the showcase's seeded board stays as the caller built it.
- **6. Pure parsers split from I/O** so the seam reader and the filesystem loader share one validator, with unchanged error texts: `bridge.sla.parse_skill_frontmatter` / `sla_policy_from_doc`, `bridge.policy.disposition_policy_from_doc`, `bridge.rfp.rfp_policy_from_docs(name, frontmatter, load_policy_doc, *, where=None)`. The policy doc is loaded lazily and ONCE, so the check order stays kind → pattern → candidates → (read doc) → rfp block → tick → item_field (a negotiate skill with no policy file still fails on pattern). `bridge/rfp.py` stays pure (no `bridge.edge` import).
- **7. No `contract/`, `skills/` or `spec/` change.** `MailOutcome.reason` is a free `str`.
- **Residuals (documented, not fixed).**
  1. The home process's policy is fixed at host build (no live re-read; a deployed benefits-rfp board is byte-identical), and `load_rfp_policy` for the home process is still un-narrowed (declared candidates).
  2. An edge with no home RFP process (`BRIDGE_RFP_PROCESS` unset) has no opener at all, so it cannot open RFPs for a live-added process; the opener still needs `BRIDGE_RFP_PROCESS` + a durable store.
  3. The showcase's seeded board stays single-process (`live_policy=None`).
  4. The live check is a synchronous registry read on the event loop (one per non-home structured request; a gcp registry read is a bucket list + two blob reads).
- **Tests.** `tests/test_rfp_live_policy.py` (13: parser parity + check order, `rfp_policy_for` per-call / refusals / narrowing / credential-free gcp fake bucket, board single vs live mode, removed-skill skip + 404, email `process_unavailable` before extraction, opener cross-process refusal). `tests/test_live_rfp_use_case_add.py` (the Driver; the fake-gcp run with an empty filesystem registry; the `@pytest.mark.seam("skill_registry")` terminal-outcome parity test, whose gcp param is `BRIDGE_TEST_GCP`-gated, never deletes a skill it did not write, and moves the suite's skip count 98 → 99). Shared helpers moved, behavior unchanged: `tests/_a2a_live.py` (A2A live-add kit), `tests/_fake_gcs.py`, `tests/_skill_fixtures.py::StubExtraction`. Totals: `uv run pytest -q` 1797 passed / 99 skipped (baseline 1781 / 98); ruff clean.
- **Domain note.** A carrier's reply moves its other open cells `invited` → `acknowledged` (the W-9 reply leaves supplier-y's COI acknowledged), as on benefits-rfp.
- **Companion Driver node id (for S4-usecase-5):** `tests/test_live_rfp_use_case_add.py::test_live_rfp_use_case_opens_on_the_board_next_to_a_running_exchange`.
- **PROPOSAL for S4-usecase-5 (spec not edited).** CUJ-9 in `spec/cujs.md` could add the board companion: "A live-added emergent-Collect process opens RFPs through the A2A Requirements path onto the shared board, which lists it, serves its carrier checklist with its own label space and files its emailed documents with its own item key, without a host rebuild and without disturbing a parked exchange of another process." Driver: `tests/test_live_rfp_use_case_add.py::test_live_rfp_use_case_opens_on_the_board_next_to_a_running_exchange`.

## 2026-10-03 — S4-usecase-5: CUJ-9 spec proposal (Driver + bad-upload trap) + Sprint 4 close-out

- **Context.** S4-usecase-1 made every per-request skill consumer read the registry live; S4-usecase-2 built the Driver; S4-usecase-3 refused bad uploads; S4-usecase-4 let a live-added emergent-Collect process open RFPs on the shared board. `spec/cujs.md:46` still has `**Driver:** —`. No code change; `spec/` is untouched.
- **SPEC PROPOSAL (not applied — spec/ is protected).** `spec/cujs.md` — replace line 46 (it begins `- **CUJ-9** — **Driver:** —`) with the line below. Every other line is unchanged: the table row, 'How to read this' (it already says RFP adds CUJ-9) and the frontmatter.

```markdown
- **CUJ-9** — **Driver:** `tests/test_live_use_case_add.py::test_whole_use_case_added_live_while_an_exchange_is_in_flight` (+ `tests/test_live_use_case_add.py::test_bad_live_upload_is_refused_not_propagated`, `tests/test_live_rfp_use_case_add.py::test_live_rfp_use_case_opens_on_the_board_next_to_a_running_exchange`). **Judge:** a new use-case's skills placed in the running core while an exchange of another process is in flight → the next discovery GET advertises the new process, and the in-flight exchange reaches the same terminal outcome it reaches without the upload; trap: a redeploy/restart, or the in-flight exchange disturbed, or a malformed / half-copied skill upload breaking discovery, removing an already-advertised skill or disturbing the in-flight exchange instead of being skipped with a logged reason (`tests/test_live_use_case_add.py::test_bad_live_upload_is_refused_not_propagated`).
```

  The Judge's first clause is the current text, verbatim. Only the trap gains a third alternative. On apply, `docs/features.md` needs no change, because SKILL‑5 already cites CUJ 9.
- **Not folded (offered to the spec owner, optional).** S4-usecase-4 proposed a board clause for the Judge: "A live-added emergent-Collect process opens RFPs through the A2A Requirements path onto the shared board, which lists it, serves its carrier checklist with its own label space and files its emailed documents with its own item key, without a host rebuild and without disturbing a parked exchange of another process." The task fixes the Judge verbatim, so the board test rides as a Driver companion only; the clause is an optional additive Judge sentence, not part of the apply-ready line.
- **T3 evidence (CUJ-9, run by hand against the proposed line).** `/verify CUJ-9` reads `spec/`, where the Driver is still `—`, so the procedure was run manually.

| Item | Result |
|---|---|
| Mode mechanism | mode-pinned hybrid (explicit `edge_mode="hybrid"`); env-insensitive |
| `BRIDGE_EDGE_MODE=deterministic` (creds stripped; 3 proposed ids + 3 supporting) | exit 0, 6 passed / 2 skipped (both `BRIDGE_TEST_GCP` gates) |
| `BRIDGE_EDGE_MODE=hybrid` (same set) | exit 0, 6 passed / 2 skipped (both `BRIDGE_TEST_GCP` gates) |
| `BRIDGE_EDGE_MODE=bogus` (same set + CUJ-8 driver) | only `tests/test_live_doctype_add.py::test_live_doctype_add_regenerates_card` fails (invalid edge mode); 6 passed / 2 skipped, so CUJ-9 is env-insensitive |
| jeep LLM judge (gemini-3.5-flash) | score 1.0, pass true: "the parked exchange continues and reaches the exact same terminal outcome as a control exchange run without the live upload ... Malformed/corrupt skill uploads are gracefully skipped with a logged reason" |

  Supporting ids: `tests/test_live_rfp_use_case_add.py::test_live_rfp_policy_comes_through_the_gcp_registry_adapter` (fake bucket), `tests/test_live_rfp_use_case_add.py::test_live_rfp_use_case_add_terminal_outcome_parity` (`[gcp]` gated), `tests/test_skill_registry_seam.py::test_skill_registry_skips_malformed_folder` (`[gcp]` gated). Trap sensitivity is the S4-usecase-2/3/4 mutation evidence above; no mutations re-run. Full T1 (`uv run pytest -q`) covers the CUJ-8 and CUJ-13 drivers, which share code with S4-usecase-3/4: 1797 passed / 99 skipped (identical to baseline), ruff clean. CUJ-9 passes both judges (against the proposed Driver/Judge); it does not claim deterministic-edge execution or live gcp-adapter runs.
- **Carried residuals (for /replan; none implemented here).**
  - S4-usecase-3 R1: a raw overwrite of a good skill with garbage removes it (fail closed; no last-known-good cache); the local install write is non-atomic.
  - S4-usecase-4 R1: the home RFP policy is fixed at host build, and the home `load_rfp_policy` is un-narrowed.
  - S4-usecase-4 R2: an edge with no `BRIDGE_RFP_PROCESS` has no opener, so it cannot open RFPs for a live-added process.
  - S4-usecase-4 R3: the showcase board stays single-process (`live_policy=None`).
  - S4-usecase-4 R4: the live `serves` check is a synchronous registry read on the event loop.
  - Still-unfolded spec items from the 2026-10-02 apply entry: conformance-run Driver, scanned-quote Driver, calibration CUJ-5 Driver, HITL resolving-principal note, ADK 2.7.0 vs 2.7.1 bullet.
- **Gotchas.**
  - `/verify CUJ-9` reports "not runnable" until the spec owner applies the line, because it reads `spec/`.
  - The Drivers pin `edge_mode="hybrid"`.
  - The S4-usecase-4 TODO line cites a non-existent `tests/test_seam_skill_registry.py`; the real seam parity test is `tests/test_live_rfp_use_case_add.py::test_live_rfp_use_case_add_terminal_outcome_parity`.
  - `docs/features/usecase.md` pre-existed; the TODO's "new" was stale.
- **Verification.** T1 and T2 as recorded in the T3 bullet above; scope diff limited to DECISIONS.md, TODO.md, docs/features.md, docs/features/usecase.md, docs/roadmap.md.

---

## 2026-10-18 — Agent Gym human-oversight literature note (`design/agent-gym.md`, docs-only)

- **Context.** Reviewed Margaret Mitchell, Avijit Ghosh, & Samir Passi (2026), [*AI Agents Push Humans Out of the Loop*](https://arxiv.org/pdf/2608.23642) (`arXiv:2608.23642`) alongside foundational and recent human-AI decision-making literature (Bainbridge 1983; Endsley 2023; Buçinca, Malaya, & Gajos 2021; Bansal et al. 2021; Vasconcelos et al. 2023; Schemmer et al. 2023; Miller 2023 / Le et al. 2024; Vaccaro, Almaatouq, & Malone 2024 *Nature Human Behaviour*; Dawid & Skene 1979) against `bridge/agent_gym.py` and `bridge/a2ui_inbox.py`.
- **Decision: record in documentation (`design/agent-gym.md`), do not change runtime code yet.**
  - While `bridge/agent_gym.py` rigorously tests the *agent* before rule or autonomy promotion (`generate_synthetic_stress_suite`, `evaluate_candidate_rule`, deterministic `Decimal` math firewall), it currently treats the *human reviewer's signal* at face value (`consecutive_matches` on `ProgrammableTrustPolicy`, pre-selected `options[0]["value"]` and upfront `confidenceScore` on `ApprovalCard` in `bridge/a2ui_inbox.py`, and fallback memos in `distill_from_inbox_audit`).
  - Documented four candidate Agent Gym v2 patterns in `design/agent-gym.md` for future consideration: (1) **Pre-commitment & anti-anchoring** (withholding pre-selected winners and confidence scores on high-risk or calibration items until source inspection or independent selection); (2) **Reasoning probes** on overrides and high-risk approvals (requiring structured rationale/citations before distilling a `TribalKnowledgeRule`); (3) **Behavioral oversight telemetry** (`review_dwell_ms`, `citation_opened`, `pre_committed` on `InboxAuditRecord` so rubber-stamped approvals cannot increment `consecutive_matches`); and (4) **Continuous spot-checks & seeded honeypots** (routing a small stratified sample of `trusted_auto_approve` traffic + synthetic stress cases through `guided_review` to prevent skill atrophy and measure Relative Self-Reliance).

---

## 2026-10-18 — Vertex AI Agent Engine migration: Option A implemented; Option B (A2A server on Agent Engine) recorded in backlog as future decision

- **Decision.** Implemented **Option A** (Agent Engine hosts ADK `Sessions`, `Memory Bank`, and `document_bridge` / `address` ADK `App` execution; Cloud Run remains the thin HTTP/A2A/webhook/REST edge) and recorded **Option B** (moving the A2A JSON-RPC Server / `CollectExecutor` itself onto Vertex AI Agent Engine's native A2A runtime) in the backlog (`TODO.md`, `docs/roadmap.md`, and the `spec/open-questions.md` proposal below) as a future architectural decision.
- **What shipped in Option A:**
  1. **`Seam.sessions` (`GcpSessions`) & `agents/address/app.py::build_durable_session_service`:** When `BRIDGE_AGENT_ENGINE_ID` (short ID or full `projects/.../locations/.../reasoningEngines/...` resource name, parsed by `parse_agent_engine_resource`) is set and no explicit `db_url` override is passed, builds ADK `VertexAiSessionService(project=..., location=..., agent_engine_id=...)` (with injectable `vertex_factory` for credential-free unit tests). When `BRIDGE_AGENT_ENGINE_ID` is unset, preserves `DatabaseSessionService` over `DATABASE_URL` so Cloud SQL deployments and existing tests remain byte-for-byte unchanged.
  2. **`Seam.memory_bank` (`MemoryBankSeam`, `LocalMemoryBank`, `GcpMemoryBank`):** Added `build_memory_service(db_path: str | None = None) -> BaseMemoryService` to `MemoryBankSeam`. `LocalMemoryBank` returns `InMemoryMemoryService()` by default or `SqliteMemoryService(db_path=...)` when `BRIDGE_MEMORY_DB_PATH` is set. `GcpMemoryBank` (registered in `GCP_ADAPTERS`) returns `VertexAiMemoryBankService(project=..., location=..., agent_engine_id=...)` and refuses loudly (`RuntimeError`) if `BRIDGE_AGENT_ENGINE_ID` is unset.
  3. **`LlmLeg` (`bridge/edge/llm_leg.py`) & `wake_task` (`agents/address/wake.py`):** Wired `memory_service` from `Seam.memory_bank` onto `Runner(...)` in `LlmLeg.runner()`. Added `remote_engine` (`BRIDGE_AGENT_ENGINE_REMOTE=1`) support in `LlmLeg._stream_events` and `agents/address/driver.py::_run_once` / `wake_task` so turns can execute against a deployed Vertex AI Agent Engine `ReasoningEngine` (`async_stream_query`) while keeping `project_from_events` and `project_review_turn` deterministic at the edge.
  4. **Packaging & Terraform (`pyproject.toml`, `terraform/`):** Added `[build-system]` (`hatchling`) + `[tool.hatch.build.targets.wheel]` and `"google-cloud-aiplatform[agent_engines]>=1.93"` in the `gcp` extra so `docubridge` can be packaged as a wheel for Agent Engine deployment. Added `enable_agent_engine_psc` (`google_compute_network_attachment.agent_engine_psc` on the internal subnet) in `terraform/modules/network` and `agent_engine_id` / `agent_engine_remote` (`BRIDGE_AGENT_ENGINE_ID` / `BRIDGE_AGENT_ENGINE_REMOTE` plain envs) in `terraform/modules/runtime` and root `terraform/`.
- **Why Option B (A2A server on Agent Engine) is deferred as a future decision:**
  - `CollectExecutor` is a custom `a2a-sdk` `AgentExecutor` (Decision 2 in `bridge/edge/llm_leg.py`: never `google.adk.a2a` / `A2aAgentExecutor`), and `bridge/edge/server.py` serves non-A2A HTTP routes on the same process (`GET /artifacts/{context_id}/{name}`, `POST /followups`, and per-GET live Agent Card regeneration `_live_card` for CUJ-8/CUJ-9). Moving the A2A server onto Agent Engine's native A2A runtime requires deciding how per-GET live Agent Card regeneration and custom HTTP routes are fronted (e.g., via an API Gateway / Cloud Run BFF) and requires PSC attachment to Cloud SQL for `DatabaseTaskStore` / `SqlExchangeStore`.
- **SPEC PROPOSAL (not applied — `spec/` is protected per `CRITICAL.md`).** Append to `spec/open-questions.md`:
  ```markdown
  - **Option B — Native A2A Server on Vertex AI Agent Engine (future decision).** Option A moves `Seam.sessions` (`VertexAiSessionService`), `Seam.memory_bank` (`VertexAiMemoryBankService`), and `document_bridge` / `address` ADK `App` execution onto Vertex AI Agent Engine while keeping the A2A JSON-RPC server (`CollectExecutor`) and custom HTTP routes (`/artifacts`, `/followups`, `/wake`, A2UI/Ops/Console) on Cloud Run. Moving the A2A server itself onto Agent Engine's native A2A runtime is deferred pending a decision on per-GET live Agent Card regeneration (CUJ-8/CUJ-9) and custom non-A2A route hosting.
  ```
