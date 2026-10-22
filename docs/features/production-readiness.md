# Production readiness — Sprint 0 hardening

Security, correctness, architecture, and debt findings from the whole-project audit
(`/review`), tracked in `TODO.md` "Sprint 0" and archived here.

Completed-work archive for this feature area (`docs/features/`). This holds the
plan **substance**, not links — `.claude/plans/*` are local and gitignored, so
their reasoning must be captured HERE (and decisions logged in `DECISIONS.md`).

**Supports CUJs:** cross-cutting (wire contract underpins all A2A CUJs — 1,2,4,5,6,7,8,10)

---

## Completed Work

<!-- Newest first. One entry per completed task; capture what a future reader
     needs WITHOUT the local plan file. -->

### S0-arch-20 — Showcase runner: one process, one card, one dashboard, three demos (2026-10-01)

**What:** `spec/demos/demo-suite.md` and `spec/demos/rfp-demo.md` require one launch that boots one core with Address, Benefits and RFP skills loaded, exercising them concurrently on one Agent Card and one dashboard over shared stores. `scripts/run-all.sh` boots separate hosts with isolated in-memory stores; the RFP demo (`bridge.demos.rfp_demo`, :8010) had no A2A edge or program board. Nothing wired all three sources into one host.

**Built:**
- **`bridge/demos/showcase.py`** — `build_showcase_app` factory composing one Starlette app: `[Mount("/ops", dashboard), Mount("/portal", portal), Mount("", edge)]`. Edge at root so Agent Card stays at `/.well-known/agent-card.json` with no path prefix. Shared sources built once and injected: one `PortalStore` to dashboard + portal, one `RfpBoard` (`benefits-rfp`) to edge + dashboard + portal, one `ProgramBoard` (`benefits-procurement`) to dashboard. Seeds: Address (2 portal sessions, `bill-powerco-clean` → done, `bill-aquautil-clear` → in_flight), Benefits (`acme-2026`, carriers a–d, carrier-b negotiates), RFP (`acme-vb-2027` via `seeded_rfp_board`). All synthetic, deterministic, run through `run_sync` so factory works under uvicorn and from sync tests.
- **`bridge/demos/rfp_demo.py` refactor** — extracted public `seeded_rfp_board(*, clock, exchange_store)` helper (seeds only when `acme-vb-2027` absent); `_run` renamed `run_sync`. Behavior unchanged: `GET /ops/rfps/acme-vb-2027` byte-identical before/after.
- **`tests/test_showcase.py`** — 6 tests proving one card advertises `address-proof`, `benefits-procurement`, `benefits-rfp`; one dashboard serves Address exchanges, Benefits program `acme-2026`, and RFP `acme-vb-2027`; all three exercised concurrently (A2A address-proof, A2A RFP open, portal session upload, GET /ops/programs in one `asyncio.gather`, then assertions on shared dashboard); seeds idempotent over durable store.
- **`scripts/run-showcase.sh`** — port 8011, sources `_dev-env.sh`, exports `BRIDGE_PUBLIC_URL`, starts `uvicorn --factory bridge.demos.showcase:build_showcase_app` plus (unless `SHOWCASE_APPS=0`) ops-dashboard :5175 and provider-portal :5173 pointed at the mounts. Alternative to `run-all.sh`, not companion (Vite ports collide).
- **Docs** — `docs/local-dev.md` port table + showcase section; `design/architecture.md` core-vs-demo-hosts example list extended; `README.md` pointer.

**Decisions:**
1. **Fenced module.** `bridge/demos/showcase.py` names demo skills, seeds synthetic data, reads `wiki/evals/` — lives in `bridge/demos/` per S0-arch-15. Nothing outside `bridge/demos/` imports it (isolation guard).
2. **Starlette, not FastAPI** for outer app — no `/docs` / `/openapi.json` shadowing edge paths.
3. **RFP seed reused, not copied.** `rfp_demo.py` gains public `seeded_rfp_board`; showcase imports it.
4. **Email wiring mirrors `rfp_demo`.** `email_processor_from_env` unless injected; drain loop runs in outer lifespan when bound with interval > 0.
5. **Lazy bridge.edge import** inside builder (circular-import trap from S0-bug-3). Module joined fresh-process import guard.

**Known residuals:**
- `/exchanges` returns portal sessions + RFPs, not programs (programs on `/programs` of same host). demo-suite.md:36 "one query" met across two endpoints.
- No A2A path lands a Benefits program on `ProgramBoard`; showcase seeds it. `ProgramBoard` in-memory (S1-benefits-7 deferred durable).
- Three backing stores in one process (PortalStore, ExchangeStore, ProgramBoard), not one physical store.
- Deployed one-runtime composition out of scope; GCP parity N/A for local demo host.

**Spec proposal (not applied):** `DECISIONS.md` "2026-10-01 — S0-arch-20" proposes naming `tests/test_showcase.py::test_exercises_all_three_concurrently_on_one_core` and `scripts/run-showcase.sh` as Driver evidence for spec/demos/demo-suite.md:18,38 and rfp-demo.md:55 "one core, many demos" requirement. spec/ is protected.

**Verification:**
- T1: `uv run ruff check` clean; `uv run pytest -q` → 1745 passed / 97 skipped (baseline 1738 / 97: +6 in test_showcase.py, +1 fresh-import param; no new skips).
- T2: `test_exercises_all_three_concurrently_on_one_core` passes (A2A address-proof + RFP open + portal upload + programs GET concurrent, then shared dashboard shows both RFPs, 3 address-proof sessions, acme-2026 program). Live `SHOWCASE_APPS=0 bash scripts/run-showcase.sh` :8011: card lists 3 skills, interface url `http://127.0.0.1:8011/`, `/ops/exchanges` 2 address-proof + benefits-rfp acme-vb-2027, `/ops/programs` acme-2026 with carrier-b negotiate, `/ops/rfps` acme-vb-2027. Live portal upload → dashboard sees it done. Live A2A RFP open → dashboard lists both `acme-vb-2027` and `live-check-rfp`. RFP demo regression: `GET /ops/rfps/acme-vb-2027` byte-identical. Isolation guard green. Cleanup trap works.

### S0-docs-11 — Entry docs, eval index and repo conventions match as-built (2026-10-01)

**What:** Entry-point docs (README.md, docs/local-dev.md), the eval index (wiki/evals/README.md, wiki/evals/benefits/description.md) and repo conventions (CLAUDE.md, CRITICAL.md, scripts/_dev-env.sh) described an earlier build. Separately, every frontmatter block in design/* and wiki/evals/* carried Obsidian-style `"[[stem]]"` wikilinks; the document bodies had already been converted to relative markdown links.

**Built (docs and comments only):**
- **Frontmatter wikilinks converted to relative paths.** Every `"[[stem]]"` in design/*.md (29 files) and wiki/evals/ description files (4 files) converted to quoted relative paths (e.g., `"seams.md"`, `"../spec/standards.md"`, `"../../../spec/demos/benefits-demo.md"`). Resolution order: design → spec → spec/demos, ignoring docs/. Keeps the graph information and is mechanically checkable. A one-off script generated the conversions; spec/ files get only a proposal (spec/ is protected).
- **README.md**: RFP demo now described as built (emergent Collect with RFP board, carrier checklist, email intake); repo map updated (scripts/, frontend four apps + shared lib + e2e, tests with conformance + perf, wiki/evals as Address + Benefits oracles).
- **docs/local-dev.md**: RFP demo host added to port table (:8010, `bridge.demos.rfp_demo:build_rfp_demo_app`); new sections for RFP demo host (3-command recipe, seeded RFP from Benefits conformance corpus, persistence option, email env) and email CLI (land/drain local loop, subscribe/renew for Graph, env requirements).
- **wiki/evals/README.md**: Benefits row updated (5 carrier-quote records including Carrier B's revised -v2, 2 conformance records, 3 native-digital PDFs); RFP row kept as scaffold, noting demo inputs come from Benefits conformance corpus.
- **wiki/evals/benefits/description.md**: Updated to cover normalize, standard-gap disposition, and revise loop oracles; added benefits-carrier-b-usd-en-v2 to table; replaced "deferred Document AI adapter" with description of docai engine (Gemini + Document AI OCR pre-pass); scanned PDF rendered at test time.
- **CLAUDE.md**: Frontend and repo-shape blocks updated (frontend: shared lib + e2e; tests: conformance/ + perf/; agents: Address, Benefits carriers, console; scripts/ and docs/ rows added); seams description updated (drop "from P7", no numeral).
- **CRITICAL.md**: Added six paths (scripts/deploy.sh, scripts/parity.sh, skills/, bridge/email_ingress/, bridge/edge/email_ingress.py, .env.example) with rationales (live GCP apply, runtime policy, secret-bearing code, public webhook, env var index).
- **scripts/_dev-env.sh**: Comment updated to describe seams without a count; extraction axis noted.

**Decision: convert, not drop.** Frontmatter wikilinks converted to relative paths (matching the body conversion that already happened) to preserve graph information and enable mechanical checking. Resolution order design → spec → spec/demos ensures unique resolution.

**Key findings:**
- The RFP demo host module is `bridge.demos.rfp_demo:build_rfp_demo_app`, not `bridge.dashboard.rfp_demo` (corrected in docs).
- Benefits eval corpus has 5 carrier-quote records (4 carriers + Carrier B's revised -v2), 2 conformance records, 3 native-digital PDFs. Only benefits-carrier-b-usd-en has a gap under default standard.yaml.
- Scanned PDFs are the docai engine's envelope (Gemini + Document AI OCR pre-pass); no raster PDF is committed to corpus; scanned twin rendered at test time.
- The RFP eval genuinely stays a scaffold; RFP demo host and tests read Benefits conformance corpus.

**Spec proposal (not applied):** `DECISIONS.md` "2026-10-01 — S0-docs-11" carries apply-ready frontmatter replacements for 7 spec files (cujs.md, standards.md, open-questions.md, 4 demos/*.md). spec/ is protected; applying the proposal is a spec-owner action.

**Verification:**
- T1: `uv run ruff check` clean; `uv run pytest -q` → 1738 passed / 97 skipped (identical to baseline).
- T2: No `[[` wikilinks left under design/ or wiki/evals/ (spec/ still has 7 files with proposals). Frontmatter YAML loads and every path resolves (bad 0). Bodies byte-identical except wiki/evals/README.md and wiki/evals/benefits/description.md. Stale claims gone (no "not yet built", "first-cycle", "eight seams", "from P6/P7"). CLAUDE.md has scripts/ and docs/ rows; CRITICAL.md has 6 new rows. RFP demo host serves acme-vb-2027 with "process":"benefits-rfp". Email CLI --help lists land/subscribe/renew/drain. Every relative link in edited docs resolves. No spec/ files in commit.

**Follow-ups:**
- `wiki/evals/benefits/expected.json` note still says "deferred Document AI adapter" (oracle file, needs separate CRITICAL edit).
- `scripts/_dev-env.sh` does not unset BRIDGE_EMAIL_BUCKET / BRIDGE_EXCHANGE_DB_URL (behavior change, noted only).
- README / design/bridge.md / design/adk.md still list Memory Bank as platform pillar.
- Applying the spec/ frontmatter proposal is a spec-owner action.

### 2026-09-03 — S0-sec-2 / S0-sec-3 Push-webhook SSRF guard + loopback serve defaults

**What shipped:**
- **S0-sec-2 (SSRF):** New `bridge/edge/pushguard.py`. The A2A push surface POSTs task
  events to a client-supplied `pushNotificationConfig` webhook; previously any URL was
  accepted (`http://169.254.169.254/…` metadata, internal hosts). `webhook_url_allowed`
  now requires an http(s) scheme and rejects any URL whose host is/resolves-to a
  non-global IP (loopback/link-local/private/reserved), with a `BRIDGE_PUSH_ALLOWLIST`
  (comma-separated host / host:port) escape hatch. `SsrfGuardPushConfigStore` wraps any
  config store and enforces this in `set_info` (raising `InvalidParamsError` → clean
  JSON-RPC error at registration), delegating all other ops — including
  `get_info_for_dispatch`, preserving the inner multi-owner override. Wired into both
  `build_a2a_app` (real edge) and `build_mock_bridge_app` (mock double).
- **S0-sec-3:** All manual-run `serve()`/`serve_mock()` helpers now default to
  `host="127.0.0.1"` instead of `"0.0.0.0"` (edge, mock, portal, dashboard, timewarp,
  console).

**Key approach:** guard at *registration* (last cheap point before storage), default-deny
via `ipaddress.is_global`. Unresolvable hosts are allowed — they can't reach an internal
target, so the POST just fails; only names/literals resolving to a private range are the
risk this blocks. **Scope:** DNS-rebinding (public at `set_info`, private at send) needs a
check-at-send re-validation, and per-party auth + Gateway egress policy, both P7
(`design/zones.md`). This is the proportionate pre-deploy egress guard.

**Verification:**
- T1: `uv run pytest` → 513 passed, 13 skipped (baseline 497 + 16 new). Ruff not a
  project dep (lint not configured); `uv run pytest` is the T1 gate per CLAUDE.md.
- T2: `tests/test_push_ssrf.py` (16 cases): `webhook_url_allowed` rejects metadata/
  loopback/private/IPv6-loopback/bad-scheme/no-host and allows public-IP/unresolvable/
  allowlisted hosts; `allowlist_from_env` parsing; `SsrfGuardPushConfigStore` raises on a
  metadata URL (never reaching the inner store), delegates an allowed URL + dispatch, and
  wraps idempotently; every `serve` default asserted `== "127.0.0.1"` via signature
  inspection. Existing push tests (`test_push_config`, `test_progress_surfaces`,
  `address/test_wake`) stay green (their `http://receiver/hook` is unresolvable → allowed).

**Decisions & gotchas:** See `DECISIONS.md` 2026-09-03. Note S0-sec-2's own text defers
*full* allowlisting to P7; this ships the reachable default-deny guard now and leaves the
auth/Gateway/rebinding hardening to P7. `bridge/` touched → CRITICAL (Opus + self-review).

### 2026-09-03 — S0-bug-1 Version-gated tolerant reader on the wire

**What shipped:** Inbound A2A wire decodes now honour `contract_version` (previously
carried but never inspected). A newer-major producer that adds a field no longer
hard-fails the consumer's `model_validate`; the added field is ignored and the decode
degrades gracefully. Same-major decode is unchanged — still strict `extra="forbid"`.

**Approach:** Kept every `contract/wire.py` model `extra="forbid"` (spec D2's
"extra=forbid for our two-party edge" — strict within our major, catches first-party
typos). Added `contract.wire.decode_wire(model_cls, data)`: reads the payload's
`contract_version`, and only when its **major** differs from ours strips fields the
model tree doesn't declare (`_strip_to_known`, a recursive walk over nested
`BaseModel`, `list[...]`, `X | None`, `dict`, enums, scalars) before validating. All
inbound wire-decode sites route through it: `bridge/edge/wire.py`
(`decode_collect_request`, `decode_exchange_turn`), `agents/address/wire.py`
(`_turn_from_part`), `agents/address/turn_reader.py` (state fallback), and
`agents/address/driver.py` (final-output decodes). `decode_wire` lives in `contract/`
so both `bridge/` and `agents/` import it without breaking the isolation invariant.

**Verification:**
- T1: `uv run pytest` → 497 passed, 13 skipped (baseline 490 + 7 new). Ruff not
  installed in this env (lint not configured).
- T2: added `TestVersionGatedTolerantReader` in `tests/test_contract.py` (7 cases):
  same-major/no-version unknown field still raises; newer-major (`contract_version="2"`)
  tolerates an unknown **top-level** field and an unknown **nested** `LedgerEntry`
  field; newer-major still requires required fields; a CollectRequest blob (even v2)
  never mis-decodes as an ExchangeTurn (select-by-shape preserved); non-dict passthrough.

**Decisions & gotchas:** See `DECISIONS.md` 2026-09-03. No spec change needed — a
version-skewed producer *is* the "genuinely external caller" spec D2 already reserves
tolerant-reader for. Gotcha: tolerance adds fields only; a *removed* required field
still fails (can't fabricate missing data). `from __future__ import annotations` is
safe here because pydantic's `model_fields[...].annotation` returns the resolved type,
not a string.

### S0-arch-2 — Reverse isolation guard (agents/ must not import bridge/) — 2026-09-03

**What:** `tests/test_isolation_guard.py` guarded only `bridge/→agents/`. The reverse
invariant — `agents/` must not import `bridge/` — was documented
(`bridge/seams/local/__init__.py` §1.4) and load-bearing for the mock→real Bridge swap
property, but unguarded. No violation existed; this closes the latent gap.

**Approach:** generalized the AST detector `_imports_agents(path)` →
`_imports_package(path, package)` and added a `_scan(source_pkg, forbidden_pkg)` helper,
so both directions share one code path. Added `test_agents_never_imports_bridge` (scans
the real `agents/` tree) and `test_guard_detects_reverse_violation` (positive control).
All original bridge→agents cases retained. One-line docstring update in
`bridge/seams/local/__init__.py` naming the enforcing test.

**Verification:**
- T1: `uv run pytest` → 515 passed, 13 skipped. Lint not configured in this env.
- T2: `test_agents_never_imports_bridge` passes against the live tree (agents/ clean —
  `grep -rn "import bridge" agents/` = none). Positive control `test_guard_detects_reverse_violation`
  catches synthetic `import bridge` / `from bridge.edge import server` / `import bridge.agent`
  (3 violations), while ignoring `contract/` imports and the near-miss `bridgehead`.

**Gotcha:** consumers reach the Bridge only over the A2A wire + shared `contract/`;
`contract/` imports must never be flagged. The near-miss guard (`package` + `package.`
prefix only) keeps `bridgehead`/`agentspew` from tripping either direction.

### S0-arch-3 — Seam-harness hygiene (2026-09-06)

Three related P2 clean-ups that restore the "one shared parametrized test per seam"
invariant and make the seam ABCs' intent explicit.

**Built (three parts):**
- **(a) Deferred empty seams labelled.** `ExchangeStoreSeam` + `MemoryBankSeam` (and
  their `LocalExchangeStore` / `LocalMemoryBank` adapters) now carry a **"deferred,
  empty by design"** docstring — no domain methods this cycle, no `@pytest.mark.seam`
  parity test, kept as named seams for the P7 GCP swap. (Replaced the stale
  "filled in at P4" placeholder text — P4 shipped and these stayed empty by design.)
- **(b) Consistent `@abc.abstractmethod`.** Added `@abc.abstractmethod` to every
  non-empty seam ABC method (`SessionsSeam.build_session_service`,
  `TaskStoreSeam.build_task_store`/`build_push_config_store`,
  `SkillRegistrySeam.list_skills`/`get_skill`/`install_skill`,
  `SchedulerSeam.build_scheduler`, `A2AClientSeam.build_client`) so the base ABCs are
  no longer directly instantiable — matching the lone pre-existing `ExtractionSeam.extract`.
- **(c) De-overloaded the `seam` marker.** Removed `@pytest.mark.seam(...)` from 37
  non-parity integration tests (test_progress_surfaces 2, test_edge_artifacts 8,
  test_collect 7, test_edge_a2a 12, test_mock_bridge 1, test_signals 3,
  test_push_config 2, address/test_corpus 2). The marker now sits only on the six true
  parity tests (sessions, task_store, skill_registry, scheduler, a2a_client, extraction),
  matching its pyproject definition. No `-m seam` selection exists in CI, so which tests
  run is unchanged.

**Verification:**
- T1: `uv run pytest` → 515 passed, 13 skipped. Lint not configured in this env.
- T2 (a): docstrings assert the deferred-by-design intent (grep-confirmed).
- T2 (b): direct instantiation of each non-empty ABC now raises `TypeError`
  (abstract); the two empty seams stay instantiable (`__abstractmethods__` empty);
  all 8 local adapters still build through `get_adapter(..., local)`.
- T2 (c): marker present only on the six parity tests, each of which takes the
  `adapter` fixture (or, for extraction, resolves the local adapter and compares to the
  fixture); full suite green after removal.

### S0-arch-4 — Reconcile D3/D4 edge lifecycle divergence (2026-09-06)

The live A2A edge diverges from two `spec/standards.md` Ring-1 disciplines: D3 skips the
intermediate `WORKING` state (`SUBMITTED → COMPLETED|INPUT_REQUIRED` only), and D4's
single-durable-task discipline is met on the park/resume path + Path-B but not on the
deterministic completing path (the consumer opens a new task per round). The task offered
two fixes — emit `WORKING`+park at the edge, or record the as-built divergence.

**Chose: record the divergence (option 2).** Both divergences are direct consequences of
the already-committed Decision 1 (deterministic one-turn `Collector`, not the LlmAgent).
The Sense-A collect completes synchronously in one call, so a synthetic `WORKING` event
would manufacture progress that never happened and leak an outward Ring-1 wire semantic —
the most expensive class of deviation. D4 is honored where the multi-turn loop actually
runs. Same posture as S0-arch-1 (make the scope-out visible, don't manufacture behavior).

**Built:**
- Added an "As-built lifecycle divergence (D3/D4)" subsection to `design/a2a-edge.md`
  documenting both divergences, their rationale, and pointers to `bridge/edge/executor.py`
  (`_emit_turn`/`_status_for`) and `agents/address/interceptor.py`.
- Added a cross-reference docstring in `CollectExecutor._emit_turn` so the divergence is
  not logged only in the design doc (mirrors the S0-arch-1 executor cross-ref).

**Verification:**
- T1: `uv run pytest` → 515 passed, 13 skipped (docstring + docs only; unchanged count).
- T2: design doc records BOTH divergences with rationale (grep-confirmed); code
  cross-references it; no behavior regression — `_status_for` lifecycle tests
  (`tests/test_edge_a2a.py`) stay green and no `TASK_STATE_WORKING` is ever emitted at
  the edge (grep-confirmed: only referenced in `cancel` comments).

### S0-debt-1 — A2A cap→escalate golden run (CUJ-4) (2026-09-06)

The resubmission `attempts <= cap` ceiling is unit-tested in `bridge/pathb.py` §2b, but
the **cap→escalate transition was never exercised through the agent-to-agent journey** —
the Address consumer uses a separate `DEFAULT_MAX_ROUNDS=6` guard and the mock `resubmit`
scenario had only two steps (blurry→clean), so nothing crossed the cap over the A2A wire.

**Built:**
- New `resubmit-escalate` scenario in `bridge/edge/mock.py` mirroring `pathb` §2b with
  `max_resubmissions=3`: three blurry-bill resubmit parks (attempts 1,2,3 at the cap,
  `terminal=False` INPUT_REQUIRED) then a terminal **escalate** turn (attempts 4 exceeds
  the cap). The escalate is `PENDING` (route-to-human, **not** rejected §2a),
  `lifecycle=escalated`, `reason_code=REASON_ESCALATED` — reason code sourced from
  `bridge.offscript` and the message from `bridge.pathb.ESCALATE_MESSAGE` (single source,
  so A2A wording matches the Path-B engine).
- New golden run `tests/address/test_golden.py::test_cuj4_resubmit_to_escalate_boundary_mock`
  driving `run_collection(durable=True)`'s park→resume loop across the four turns.

**Key approach / gotcha:** the durable resume loop *replays* the workflow graph, so the
gate's `STATE_GATE_ROUNDS` counter always lands on `max_rounds` regardless of ladder
length — it is **not** a reliable "turns taken" measure (an early ceiling-based assertion
was wrong and dropped). The durable proof that the cap was crossed is the escalate
**content** itself: `ScriptedBridgeExecutor` advances one step per send and only emits
`steps[3]` (escalate) after traversing `steps[0..2]` (the three parks), so reaching the
escalate turn is structurally impossible without the four A2A sends.

**Verification:**
- T1: `uv run pytest` → 516 passed, 13 skipped. Ruff is not a configured linter here
  (no config; it flags untouched files like `pathb.py`/`gate.py` identically), so no
  configured lint gate regressed.
- T2: the golden run reaches escalated-terminal over the A2A wire (mock double, same wire
  as the real edge) — terminal `escalated` lifecycle, exactly one `pending` entry with
  `REASON_ESCALATED`, no `rejected` disposition (escalate ≠ reject), `is_satisfied` False.

### S0-docs-3 — Strip status leakage from design/outbound.md (2026-09-06)

**What:** `design/outbound.md` "Sequencing — what lands when" carried live-status
phrasing ("Dial-out and Deliver are designed, **not yet built**") plus a per-cycle
"Outbound built | Cycle" table — both roadmap/status material that goes stale and
violates the design-docs-are-status-free invariant.

**Key approach:** Replaced the section with a status-free architectural statement
("One architecture, demand-driven build") — all three outbound activities ride the
single A2A-client seam through the one trust boundary over shared contracts, so the
architecture is whole from day one while the build tracks demand. Which cycle wires
up which activity is now pointed at `TODO.md` rather than restated in the design doc.
No sequencing was lost: it already lives in `TODO.md` (P5 note L162 for the Address-cycle
dial-out/Deliver cap; "Deferred to later cycles" register) and `docs/roadmap.md:34`
(Benefits-cycle dial-out + Deliver) — so the "move to TODO.md" was already satisfied.

**Verification:**
- T1: `uv run pytest` → 516 passed, 13 skipped; `uv run ruff check` clean.
- T2: `grep -nE 'not yet built|Outbound built|Cycle \|' design/outbound.md` returns
  nothing (status phrasing + cycle table gone); no progress markers remain; sequencing
  still discoverable in `TODO.md`.

### S0-docs-2 — Machine-drivable CUJs: Driver:/Judge: fields (2026-09-07)

**What:** `spec/cujs.md` was a prose table, so `/verify` could not drive any CUJ without
reading the driver mapping out of `DECISIONS.md`.

**Key approach:** Proposed during `/implement` (spec is protected), then applied under
direct spec-owner authorization: a "Drivers & Judges (verification wiring)" section gives
every built CUJ a `Driver:` (e2e harness node id) and a `Judge:` (deterministic criterion
+ hard-fail trap). Unbuilt CUJs (9, 13) carry `Driver: — (unbuilt)`. The fields are
verification metadata, not status. Node ids drift as tests move — 2026-10-01 corrected
CUJ-6 (`test_timeline_parity` now lives on `TestLoadSlaPolicy`) and named CUJ-11's trap
guard (`tests/test_negotiate.py::TestRevise::test_cap_exceeded_escalates_not_rejects`).

**Verification:** `/verify` extracts and runs every built CUJ's driver; 11/11 pass both
judges.

### P7-8 — Service-wiring guard test (2026-09-07)

**What:** AST-based regression fence ensuring no `bridge/` factory hardcodes an in-memory
service (`InMemorySessionService`, `InMemoryTaskStore`, `InMemoryArtifactService`, etc.)
unconditionally. Prevents the session-affinity trap that caused an 86.8% poll-404 rate on
multi-replica GCP deployments in the Google vibeflix-audit reference (Decision 2026-08-24)
— when a per-replica `InMemoryTaskStore` is hardcoded in core, A2A's POST-then-poll
pattern hits different replicas and the poll misses.

**Built:**
- **`tests/test_service_wiring_guard.py`** — scans `bridge/` via AST, detects `InMemory*`
  constructions, and flags any that are **ungated** (not an `or` operand and not inside an
  `if`/`elif`/`else` branch). Accepts two legitimate forms: (1) `param or InMemory…()` (the
  injectable default idiom), (2) DSN- or env-gated backend swap (`if not dsn: return
  InMemory…()`). Positive control covers violation detection (unconditional + attribute-form
  constructions), gated forms (or-gate, if-branch), and natural exclusions (class definitions,
  imports, comments, strings).
- **`design/patterns.md`** — added "Host-controlled wiring" subsection documenting the rule:
  `bridge/` factories never hardcode in-memory services unconditionally; the host owns
  durability wiring. Cross-references `design/seams.md` (session-affinity trap) and the guard.

**Why AST gates (not just grep).** Grep would flag all 8 current legitimate `InMemory*`
constructions in `bridge/` (edge/mock, portal/dashboard/timewarp servers, local seam
adapters, artifact service fallback). The AST climb identifies an `or`-operand or an
`if`-branch ancestor, distinguishing `param or InMemory…()` (allowed) from an unconditional
`x = InMemory…()` in a function body (violation).

**Example violation (what the old `build_runner` was):** A factory function unconditionally
constructing `InMemorySessionService()` with no param and no environment gate, silently
putting a per-replica store on the deployed path. The guard makes this a red test.

**Verification:**
- T1: `uv run pytest tests/test_service_wiring_guard.py -q` → all pass; full suite 607
  passed, 13 skipped; `uv run ruff check` clean.
- T2.1: guard passes on the real tree (0 offenders) even though 8 `InMemory*` constructions
  exist — proving the gate forms are recognized.
- T2.2: regression check — temporarily inserted `_x = InMemorySessionService()` into
  `bridge/agent.py::build_document_bridge` → test fails, names the violation
  `bridge/agent.py:<line>: InMemorySessionService`. Reverted → green.
- T2.3: positive control covers all gate forms, class-def/import/comment exclusions, and
  attribute-form calls.

**Gotcha:** the guard scans `bridge/` only (the production zone). `agents/` and `tests/`
legitimately construct `InMemory*` unconditionally and are out of scope per the task. The
guard never modifies `bridge/` to satisfy itself — gate logic is adjusted, not source.

### S0-arch-5 — Schema-driven extraction through the skill_registry seam (2026-09-23)

**What:** `bridge/extraction/gemini.py` pinned one literal `EXTRACTION_PROMPT` in the
Address shape and passed only `response_mime_type="application/json"`, so every doctype
skill's `assets/schema.json` was decorative metadata. The engine now builds both the
prompt and the request's structured-output schema from the installed skills.

**Built:**
- **`SkillRegistrySeam.read_asset(name, rel) -> str`** (new abstract method) with local +
  GCP adapters, both reusing the S0-sec-1 traversal defense (GCP's `_validate_install`
  generalized to `_validate_skill_paths(name, rels)` and fired **before** `_bucket()`), and
  one shared `@pytest.mark.seam("skill_registry")` parity test.
- **`SkillMeta.schema_asset`** parsed from `metadata.bridge-schema` (not named `schema` —
  that shadows `BaseModel.schema`).
- **`build_extraction_prompt` / `build_response_schema`** (pure) plus
  `_doctype_schemas_from_registry` (lazy seam import — `bridge.seams.local` imports
  `bridge.extraction.fixture`, so a module-level import would close a cycle). The catalog
  is built **once per extractor** on first `extract` — never per call, never a module-level
  cache (`BRIDGE_SKILLS_DIR` changes at runtime: the live doctype-add property).
- **Null-pruning in `_parse_extraction`** — the pinned `key_fields`/`field_confidence` are
  the nullable, non-required **union** of every installed doctype's fields, so a model
  nulls the other doctypes' fields; only those two open dicts are pruned, the top-level
  `extra="forbid"` loudness is untouched.

**Key trap:** `fields.doctype` stays a **free string with no enum**. The Bridge classifies
(`extract` takes no doctype), and the Address oracle's `passport` record must stay
reportable or the `rejected`/`unsupported` terminal outcome silently disappears. An empty
catalog pins **no** `response_schema` at all (an empty-properties object would forbid every
field). A declared-but-absent asset contributes the doctype name only; a present-but-corrupt
asset refuses loud.

**Verification:**
- T1: `uv run pytest` → 697 passed, 29 skipped (baseline 676/28); `uv run ruff check` clean;
  `-m seam` 9 passed / 8 skipped.
- T2: fake-client request carries all three installed doctypes + their fields and the union
  schema with no `enum` on `doctype`; a `pay-stub` skill installed through the seam into a
  temp `BRIDGE_SKILLS_DIR` reaches the next extractor's prompt and pinned schema with no code
  change; one registry read per extractor (zero at construction); all 8 corpus records replay
  to their expected disposition/gate (new credential-free `test_corpus_replay_keeps_terminal_outcomes`);
  the pinned dict round-trips `google.genai._transformers.t_schema` → `types.Schema`.

### S0-bug-2 — Schema-validated retry in GeminiExtractor.extract (2026-09-23)

**What:** `GeminiExtractor.extract` made exactly one `generate_content` call; a stray
field, non-string `key_fields` value, or truncated JSON token from the model raised
straight out to the caller and the extraction was lost. The engine now implements bounded
retry with the specific rejection reason fed back to the model.

**Built:**
- **Bounded retry loop inside `extract`** — up to `max_attempts` (default 3, constructor
  kwarg) with each follow-up turn carrying both the model's own rejected output and the
  specific validator reason. The catalog read, artifact resolution, and document-part
  build happen once per `extract` and are reused across all attempts.
- **Correction turn builder** — `build_retry_correction(previous, error)` is a pure,
  testable function that echoes the rejected payload (bounded at 4000 chars to prevent
  runaway responses from ballooning follow-up requests) plus the specific validation error.
- **Surfacing via logs and exception notes** — per rejected attempt logs a `WARNING` with
  the artifact name and attempt count; on exhaustion logs an `ERROR` then calls
  `last_error.add_note(...)` with the attempt count and the deliberate no-fallback
  rationale, and raises the original `ValidationError` or `JSONDecodeError` with type
  preserved.
- **No fixture fallback** — explicitly refused in code comments and the raised exception
  note. A fallback would launder hand-authored corpus values in as if they had been
  extracted; a missing extraction is a measurable miss, a fabricated one is a lie.
- **Transport errors not retried** — only `ValidationError` and `JSONDecodeError` around
  the parse are caught. A 429/500 from `generate_content` propagates unchanged and burns
  no attempt.

**Key approach:** The retry mirrors the existing `bridge/seams/gcp/_TerminalRetryStore`
idiom (attempts=3, warn per failure, error on exhaustion, raise the last error). The
correction echoes the rejected output (making the next attempt a repair rather than a
re-roll) — a deliberate small deviation from the consumer's reason-only correction. The happy path
is unchanged: a valid first response completes in exactly one model call with no extra
latency or cost.

**Verification:**
- T1: `uv run pytest` → 708 passed, 29 skipped (baseline 697 + 11 new tests);
  `uv run ruff check` clean; seam tests unchanged.
- T2: scripted-client tests prove a rejected payload is repaired in 2 attempts (both
  schema violations and truncated JSON); exhaustion after 3 attempts raises the last
  `ValidationError` with the attempt count in `__notes__`; transport errors propagate
  on the first call without retry; all 8 corpus records still extract in exactly one
  call with terminal outcomes unchanged.

**Gotchas:** `response.text` can be `None` on safety block or token-limit truncation;
normalized to empty string so it becomes a retryable `JSONDecodeError` rather than an
`AttributeError`. The lazy-import AST guard stays green (no module-level `google*`
import). `_parse_extraction` and the top-level `extra="forbid"` on `ExtractionResult`
are deliberately unchanged — the validator is the retry oracle.

### S0-arch-7 — A real artifact resolver behind the extraction seam (2026-09-23)

**What:** ART-2 puts documents on the wire **by reference** (`FileWithUri`), and
`bridge/edge/wire.py::decode_submissions` sets `DocumentSubmission.artifact_name =
part.url` — so the string reaching the extraction seam is *the counterparty's uri*. The
engine could only read `<wiki/evals/address>/<name>` off local disk (`Path(corpus) /
"https://…"` → `FileNotFoundError`), and `bridge/edge/artifacts.py::resolve_bytes` carried
the same gap; the signed-URL / `gs://` fetch was a docstring promise. A by-reference
document from a third-party consumer is now extractable.

**Built:**
- **`bridge/extraction/resolve.py`** — one resolver for both the extraction path and the
  edge's persistence, dispatching **scheme-first, corpus-relative last** over four inbound
  shapes:
  * `gs://bucket/obj.pdf` → **by reference**. `ResolvedDocument(uri=…)`; no download, no
    GCS client, no credentials. An object whose suffix yields no known mime refuses loud
    (an unclassified part is garbage the backend would reject anyway).
  * `http(s)://…` → **leg-scoped fetch** (`_fetch_http`): guard before connect, 20s
    timeout, 32 MiB cap checked against `Content-Length` *and* enforced on the streamed
    read, mime from `Content-Type` (parameters stripped) with the uri suffix as fallback.
  * `file:///…` → refused unless `BRIDGE_ALLOW_FILE_URI=1`. Without that, a consumer
    submitting `file:///etc/passwd` would get host file contents read by the model and
    laundered back through the extracted `key_fields` onto the ledger.
  * bare relative name → today's corpus read, `resolve()` + `relative_to` traversal
    containment unchanged.
  Anything else (`mock://`, `bridge://`, `ftp://`) raises.
- **`bridge/netguard.py`** — the SSRF primitive **moved** (not copied) out of
  `bridge/edge/pushguard.py`: stdlib-only `url_allowed` / `ip_is_global` /
  `allowlist_from_env(var, env=None)`. Forced *and* right: `pushguard` imports `a2a` at
  module level so `bridge/extraction/` could not import it, and two copies of an egress
  policy is how two copies drift. `pushguard` re-exports `url_allowed as
  webhook_url_allowed` and keeps an identical public surface.
- **Redirect posture** — `follow_redirects=False` plus a **manual hop loop**, max 3 hops,
  `url_allowed` re-run **before** every hop. Load-bearing both ways: our own
  `/artifacts/{context_id}/{filename}` endpoint 307s to a GCS signed URL on deploy (so
  redirects must be followed), and httpx's own redirect following would skip the guard (so
  a counterparty 307ing us at `169.254.169.254` would win).
- **Async core + back-compat shims** — the fetch is I/O on a shared Cloud Run event loop,
  so `resolve_document` and `resolve_bytes` are `async`. `maybe_await` (await-if-awaitable)
  at both call sites keeps every **sync** injected resolver working, and
  `_coerce_resolved` accepts a legacy `(bytes, mime)` 2-tuple alongside `ResolvedDocument`.
  Those two shims are why `tests/test_gemini_extraction.py`, `tests/test_push_ssrf.py` and
  `tests/test_edge_artifacts.py` pass **unedited**. `_corpus_resolver` survives as the
  sync corpus-only legacy helper; `artifact_resolver(corpus_root)` is the new default.
- **`_document_part(resolved, *, client)`** — three branches: by-reference →
  `Part.from_uri`; `image/svg+xml` → text part (unchanged); everything else → inline
  `Part.from_bytes` (unchanged, keeps the S0-arch-6 PDF assertions green). `gs://` is
  Vertex-only, so `getattr(client, "vertexai", None) is False` refuses loud naming
  `GOOGLE_GENAI_USE_VERTEXAI` — `is False`, never falsy, because a test double has no such
  attribute and must pass through.

**Key approach:** no ninth seam. `design/seams.md` pins eight and `tests/conftest.py`
guards the marker names; the resolver is an *injectable collaborator behind the existing
extraction seam*, the same seam-in-waiting posture `bridge/edge/artifacts.py` Decision 1
uses for the artifact store. No new dependency — `httpx` is already transitive and is
imported lazily **inside** the function, the repo's idiom, which also keeps
`bridge/extraction/` free of module-level `google.*` / `bridge.edge` / `bridge.seams`
imports (the AST guard and the import cycle both bite). The resolve stays **outside** the
retry loop and `_catalog()` still runs first, so a registry error precedes any network
call and a retry re-asks rather than re-fetching.

**The asymmetry, deliberately:** extraction **raises**, persistence **skips**. A document
the *engine* could not read must never be silently replaced by a fixture (the S0-bug-2
no-fabrication rule), so `resolve_document` refuses loud. `resolve_bytes` keeps Decision
7's never-raise posture — a refused/failing uri logs a WARNING and returns `None`, and a
**by-reference** `gs://` resolve also returns `None`: there are no bytes to persist, the
consumer's ref stays on the ledger unrewritten, and the engine reads the object natively.

**Leg-scoping is now two-sided** (`design/artifacts.md` — a reference is not a
capability). *Outbound:* our endpoint 404s a foreign `(context_id, filename)` **before**
minting a signed URL. *Inbound:* the Bridge never becomes an open fetch proxy — an
`http(s)` host resolving to a non-global address is refused **with no request issued**,
unless its host / `host:port` is in `BRIDGE_FETCH_ALLOWLIST`.

**Verification:**
- T1: `uv run ruff check` clean; `uv run pytest -q` → 749 passed, 31 skipped (baseline
  715/30, +34 from `tests/test_artifact_resolver.py`); `uv run pytest -q -m seam` → 9
  passed, 8 skipped (unchanged — no ninth seam).
- T2: a loopback-served copy of the committed corpus PDF is fetched over `http` under the
  allowlist and reaches the model as `inline_data.mime_type == "application/pdf"`
  byte-equal to the file (1186 bytes), yielding a parsed `benefits-quote`; a 307 hop is
  followed and re-validated; `gs://bucket/quote.pdf` produces `inline_data is None` +
  `file_data.file_uri == "gs://bucket/quote.pdf"` with no bytes fetched; with the allowlist
  unset the metadata IP, the loopback URL, `file:///etc/passwd` and `mock://x` are each
  refused with `ValueError`; the corpus read and its traversal containment are unchanged.
  Leg-scoping is proved against the **real** fetch app over an `httpx.ASGITransport`: the
  owning `(context_id, filename)` resolves to the stored bytes while a foreign
  `context_id` fails with `HTTP 404`.

**Gotchas:** the egress guard rejects `127.0.0.1` as non-global, so every loopback fetch
test must allowlist `127.0.0.1:{port}` — that is the escape hatch under test, not a
workaround (same shape as `tests/test_push_ssrf.py`). `BRIDGE_FETCH_ALLOWLIST` /
`BRIDGE_ALLOW_FILE_URI` are read at **call** time, never at import. A live `gs://` read
needs a provisioned bucket plus Vertex credentials and is gated
(`BRIDGE_TEST_GEMINI=1` + `BRIDGE_TEST_GS_URI`), skipped by default; the whole-journey A2A
conformance run is S0-arch-9. No `terraform/` change is needed — Vertex reads `gs://` with
the runtime SA's existing object-viewer grant; a *foreign* bucket would be a deployment
note in `docs/features/gcp-deployment.md`.

**Retry (2026-09-23) — the blocker that flagged this task `- [!]`, closed.** Review found
one blocking defect: `bridge/edge/artifacts.py::resolve_bytes` caught `(ValueError, OSError)`
while documenting a **never-raise** contract (Decision 7). `httpx.ConnectError` is neither,
so an allowlisted artifact host that was merely *down* raised out of the edge's persistence
step and failed the whole disposition turn — the exact outcome the skip-don't-raise
asymmetry above exists to prevent. Reproduced against a closed loopback port before the fix.

- **`bridge/extraction/resolve.py::_fetch_http`** normalizes at the source: the hop loop moved
  verbatim into `_fetch_hops`, and the wrapper re-raises our own `ValueError` refusals while
  turning anything else into `ValueError("artifact fetch failed: <Type>: <msg> (<url>)")`
  chained `from exc`. One rule — *a failing fetch is a `ValueError`* — so a transport error no
  longer leaks httpx into the caller's error vocabulary on the extraction leg either (that
  contradicted `resolve_document`'s own docstring).
- **The catch is the base `Exception` on purpose.** httpx's error tree is not single-rooted
  (`InvalidURL`, `StreamError` sit outside `HTTPError`); an enumerated tuple is the construct
  that produced the bug, and a longer tuple would only move the next gap. The type is kept in
  the message, so nothing is hidden. `resolve_bytes` likewise catches `Exception` (the
  contract is unconditional) and logs the type; `asyncio.CancelledError` is a `BaseException`
  and stays uncaught — a cancelled turn must stay cancelled.
- **Tests (+4):** a dead allowlisted host → `ValueError` out of `resolve_document`; an
  injected client raising `httpx.ReadTimeout` → `ValueError`; **the regression test** — the
  same dead host through `resolve_bytes` → `None` plus a WARNING naming `ConnectError`; and
  `resolve_document` monkeypatched to raise `RuntimeError` → still `None`.
- **`design/artifacts.md`** gained the failure-taxonomy sentence: a transport failure *is* a
  fetch failure and the persistence skip is unconditional — an enumerated list of anticipated
  failures is how "skips" quietly becomes "fails the turn".
- **Re-verified, not just the delta:** T1 `ruff` clean, `pytest -q` 793 passed / 31 skipped,
  `-m seam` 9 passed / 8 skipped. T2 re-ran the **whole** acceptance surface as a script —
  14 PASS lines, exit 0 — every criterion above plus the blocker case on both legs.

### S0-arch-8 — Path-A validation driven by the doctype skill's schema (2026-09-23)

**What:** `bridge/patha.py::_KEY_FIELD_MODELS` was a hand-maintained two-entry dict
(`gov-id`, `utility-bill`); every other doctype "skipped the typed-key check". A
`benefits-quote` assertion — a doctype that ships a full `assets/schema.json` — validated
clean with **any** `key_fields` at all, so a party-with-agent asserting a malformed carrier
quote got past validation with only the label-space gate behind it. Path-A validation is now
built from the asserted doctype's installed skill schema, the same asset S0-arch-5 already
reads for the extraction prompt.

**Built:**
- **`bridge/doctypes.py`** (new, pure) — the one place a doctype `schema.json` is read and
  interpreted. `key_field_properties` / `doctype_fields` / `declares_issuer` /
  `issuer_description` / `doctype_schemas` moved out of `bridge/extraction/gemini.py`
  verbatim (both paths now share one reader, so the prompt and the validator cannot learn
  to disagree), plus `required_key_fields` and `key_field_model`.
- **`key_field_model(doctype, schema)`** — `pydantic.create_model` over
  `properties.key_fields.{properties,required}`: required props mandatory, other declared
  props `str | None = None`, `extra="forbid"`. Pydantic, **not** a new `jsonschema`
  dependency, so the raised type stays `pydantic.ValidationError` — exactly what
  `bridge/edge/executor.py` catches to mint the `schema_invalid` reject. Every declared
  property is typed `str`: the wire shape is `key_fields: dict[str, str]`.
- **`validate_assertion` / `validate_only` / `validate_only_entry`** take an injectable
  `schemas=` catalog (mirrors `GeminiExtractor(schemas=…)`); by default the registry is read
  **per assertion** — the `loaded_skills()` posture, so a live-installed doctype validates
  immediately. The built model is `lru_cache`d by `(doctype, schema content)`, never by the
  registry read.

**Key trap:** a doctype with **no installed skill** must keep skipping the typed check —
`fields.doctype` stays a free string (S0-arch-5) and the disposition label-space gate owns
rejecting it `unsupported`, so the Address oracle's `passport` record stays reportable. The
other two skips are not silent: a declared-but-absent asset logs a WARNING naming the
doctype, and a present-but-corrupt asset refuses loud (`ValueError`) exactly as on the
extraction path. **Not** in scope: top-level `required` (`issuer` on utility-bill) and the
`assets/validation.yaml` value rules — both are separate follow-ups.

**Verification:**
- T1: `uv run pytest -q` → 759 passed, 31 skipped (baseline 749/31 + 10 new); `uv run ruff
  check` clean; `-m seam` 9 passed / 8 skipped.
- T2: the corpus `benefits-quote` block minus `premium` now raises `missing ('premium',)`
  and with a `bogus` key raises `extra_forbidden`, while the untouched block still disposes
  `accepted`; a `passport` assertion still disposes `rejected`/`unsupported`/
  `unsupported_doctype` rather than raising; a `pay-stub` skill folder dropped into
  `BRIDGE_SKILLS_DIR` validates its own required fields with **zero** change to `bridge/`
  between runs; end-to-end over the A2A app a malformed `benefits-procurement` assertion
  yields `reason_code == "schema_invalid"` with the extraction-seam spy never called; the
  models built for `gov-id`/`utility-bill` have field and required sets identical to
  `GovIdKeyFields`/`UtilityBillKeyFields` (no coverage regression from deleting the dict).

### S0-docs-4 — Correct the false premise in the Benefits Consumer open question (2026-09-23)

**What:** `spec/open-questions.md:38-44` — the incoming Benefits Consumer feature request — rested on three
claims that had all gone false, and it cited `design/architecture.md` as their authority, so
the stale premise had propagated out of `spec/` into the design SoT. The entry therefore filed
a live capability gap as "a later-cycle capability, not a critical-path item".

**The three falsified claims (re-verified against `~/benefits-consumer` at implement time):**
- *"it has **no real PDF path**"* (line 40) — `~/benefits-consumer/engine/extraction/gcp.py:386::GcpExtractor`
  is a Vertex Gemini engine: `gemini-2.5-flash`, `temperature=0`,
  `response_schema=_ExtractedQuote`, `_MAX_ATTEMPTS = 3` with each rejection reason fed back,
  **no** fixture fallback, `fields_requiring_review`, an `application/pdf` binary part, and
  `gs://` passed through for Vertex to read by reference. Logged at `~/benefits-consumer/DECISIONS.md:586`
  (2026-09-13, with a live `ADP_TEST_GCP=1` seam run).
- *"the deferred Benefits/RFP cycle"* (line 44) — Sprint 1 (Benefits, CUJ-3/11/12) closed
  2026-09-07 T3-green; all seven `S1-benefits-*` tasks are archived in
  `docs/features/benefits.md`, including **S1-benefits-4 Dial-out + Deliver** and
  **S1-benefits-6 cross-framework carriers as real A2A servers**, covered by
  `tests/test_cuj3_dial_out_deliver.py` and `tests/test_cuj12_interop.py`. RFP (CUJ-9/13) is
  still genuinely deferred.
- *"a Benefits/RFP process skill vs. a generic document-extraction skill"* framed as
  undecided-and-unbuilt — `skills/benefits-quote/` ships `SKILL.md` + `assets/schema.json` +
  `assets/validation.yaml`, and both the extraction prompt (S0-arch-5) and Path-A key-field
  validation (S0-arch-8) are now built from that asset.

**The reframe:** the consumer did not route around the Bridge on a scheduling whim — it built
its own engine because the Bridge, whose premise is *documents in, structured signals out*,
could not do the job. That is a **capability gap in the Bridge core**, and it is the reason
this is a Sprint-0 item rather than a later-cycle nice-to-have. Bridge-side state of each gap
at the time of writing: `S0-arch-5` (schema-driven extraction through the `skill_registry`
seam) **done**; `S0-bug-2` (bounded schema-validated retry, no fixture fallback) **done**;
`S0-arch-6` (a native-digital carrier PDF in the benefits corpus + `tests/test_pdf_extraction.py`;
scanned/OCR stays the deferred Document AI swap) **done**; `S0-arch-8` (Path-A validation from
the skill schema) **done**; `S0-debt-2` (the consumer's `fields_requiring_review` / dotted-path
review vocabulary **mapped**, not renamed) **done**; **`S0-arch-7` FAILED** (`- [!]` — the
by-reference artifact resolver, `resolve_bytes` breaking its never-raise contract on transport
errors); **`S0-arch-9` OPEN** (the cross-repo conformance run, gated on the rest). Those last
two are exactly the convergence prerequisites the rewritten question names — stated as
prerequisites, never as shipped. *(Update later the same day: `S0-arch-7` passed on retry —
see its Retry note above — so only `S0-arch-9`, the cross-repo conformance run, remains
outstanding of the two. The spec proposal's own wording is unchanged and still correct: it
names both as prerequisites, and one of them is now met.)*

**Built:**
- **`DECISIONS.md`** — the dated `S0-docs-4` entry carrying the drift table, the gap state,
  and a **verbatim, apply-ready** replacement for `spec/open-questions.md:38-44` (plus a
  one-line replacement for the "Settled since last revision" bullet at line 11) under the
  house **"PROPOSAL (not applied — spec/ is protected)"** marker. No `spec/` path is in the
  commit; applying the rewrite is a spec-owner action.
- **`design/architecture.md`** (status-free) — the first-cycle scope sentence that parked both
  Benefits and RFP as eval scaffolds now parks RFP only; the
  `### Explicitly deferred past cycle 1` list drops Benefits
  (CUJ-3/11/12) and Bridge-as-client dial-out (both built in Sprint 1), leaving RFP (CUJ-9/13)
  and cold unsolicited inbound push.
- **`TODO.md`** — the cycle scope line now says the Benefits journeys followed in Sprint 1 and
  RFP stays the eval scaffold.

**Gotcha (the trap in this task):** the failure mode being fixed is *asserting a stale fact*,
so the plan's fact table was re-derived from `~/benefits-consumer` rather than transcribed, and the rewrite
keeps the question **OPEN** — a corrected premise, not a resolution. S0-docs-1's *delete-the-
resolved-question* precedent does not apply here: "two engines now exist; does the consumer
delegate over A2A, and what must converge first" is genuinely undecided. Also: the task line
asks to "point at `S0-arch-5…9`", but `spec/` is status-free and forkable, so the task ids live
here and in `DECISIONS.md` while the proposed spec text states the same content as capability
statements ("a by-reference artifact resolver", "an end-to-end conformance run").

**Verification:**
- T1: no code changed; `uv run ruff check` clean; `uv run pytest -q` → **789 passed, 31
  skipped**, identical to the pre-change baseline.
- T2: the premise checks re-run green (`GcpExtractor` + its markers in
  `~/benefits-consumer/engine/extraction/gcp.py`, `skills/benefits-quote/assets/`, the Sprint-1 close line,
  and `tests/test_pdf_extraction.py tests/test_cuj3_dial_out_deliver.py
  tests/test_cuj12_interop.py` → 14 passed, 1 skipped); `git diff --staged -- spec/` empty —
  no `spec/` path in the commit — and the stale sentence still present in `spec/`; the proposal
  block extracted from `DECISIONS.md` carries every required element and contains no task ids,
  dates, sprint names, or checkboxes; the stale premise no longer appears in `design/`,
  `docs/`, or `TODO.md`.

### S0-docs-6 — `spec/cujs.md` proposal: de-status, full Driver ids, CUJ-13 restated, CUJ-15 email intake (2026-10-01)

**What:** `spec/cujs.md` had drifted from both its own Drivers and what ships.
- (a) The "First cycle" column, its explanatory sentence, "Each built CUJ" and `Driver: — (unbuilt)` were status leakage. They were also wrong: CUJ-3/11/12 read "later" while their Drivers pass.
- (b) Bare `::name` shorthand mis-resolved two ids. CUJ-5's `test_hitl_without_park_409` lives in `tests/test_portal.py`, not `tests/address/test_park_resume.py`. CUJ-6's `test_nudge_carries_no_bearer_token` lives in `tests/test_outbound.py`, not `tests/test_sla.py`.
- (c) CUJ-13's emergent Collect is built (`bridge/rfp.py` + `benefits-rfp`, over A2A onto the durable store, plus a second industry as skills only) but was still specced as unbuilt.
- (d) Email reply intake shipped with no CUJ.

**Proposal:** `DECISIONS.md` "2026-10-01 — S0-docs-6" carries a **verbatim, apply-ready replacement of the whole body** (lines 10-51) under the house "SPEC PROPOSAL (not applied — spec/ is protected)" marker. The frontmatter is untouched (S0-docs-11). It:
- drops the status column and convention; `—` now means "no harness node, `/verify` reports not runnable";
- spells every Driver/Judge id in full;
- folds in the never-applied S3-edge-8 P-1..P-5 (CUJ-14 + the CUJ-1/4/5/10 lines), which it supersedes;
- restates CUJ-13 as the industry-neutral capability with a Driver (`tests/test_rfp.py::TestUpdateRequirements::…`, `::TestAnswer::…`, `tests/test_rfp_a2a_open.py::test_follow_up_requirements_waive_and_add_a_carrier`, `tests/test_rfp_second_use_case.py`);
- adds **CUJ-15 email reply intake** (Driver: `tests/test_email_ingress.py`, the served-host drain, the A2A-opened RFP drain; traps: quarantine never touches the board, replay is a no-op);
- gives CUJ-9 a requirement-level Judge while it stays Driver-less.

A separate broker-RFP CUJ was rejected: it would leave CUJ-13 Driver-less while its exact capability passes. Every CUJ-13/15 Judge clause was checked against an assertion in the cited test. **No `spec/` path is in the commit.** Applying the body is a spec-owner action. Residuals: `design/` CUJ-13 wording (S0-docs-9), `docs/features.md` CUJ cells (S0-docs-10), `spec/demos/rfp-demo.md` (S0-docs-7), and the CUJ-15 → CUJ-14 renumber if CUJ-14 is rejected.

**Verification:**
- T1: no code changed. `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped (identical to the HEAD baseline), no new skips.
- T2:
  - The current spec reproduces (b): exactly the two `ERROR: not found` ids.
  - The proposal block, extracted from `DECISIONS.md` onto the real frontmatter:
    - has 0 bare continuations;
    - every id collects (118), and every id runs 117 passed / 1 skipped (pre-existing `BRIDGE_TEST_GEMINI` param) / 0 failed;
    - has 15 rows and 15 Driver+Judge lines, with only CUJ-9 `—`;
    - has an empty status grep and no `<` placeholders;
    - has frontmatter lines 1-8 byte-identical.
  - CUJ-13 + CUJ-15 Drivers: 33 passed in each of `BRIDGE_EDGE_MODE=deterministic` / `hybrid` with credentials stripped.
  - jeep LLM judge: CUJ-13 and CUJ-15 both `pass=true`, `score=1.0`.
  - `git status --porcelain -- spec/` is empty.

### S0-docs-7 — `spec/standards.md`, `open-questions.md`, `demos/` proposal: nine seams, D2 posture, resolved questions out, Benefits process skill + hard-document beat (2026-10-01)

**What:** four spec files had drifted from the code.
- `standards.md:23,46` counted eight seams; the `Seam` enum has nine (`email_ingress`). D2 (`:35`) cited a `strict=True` per-party-scoping footgun, but no such flag exists anywhere.
- `open-questions.md` still asked three resolved questions: request intake, FX rate source and the Requirements-list schema. Its "Benefits/RFP deferred" settled bullet was false. The never-applied S0-docs-4 Benefits Consumer rewrite had gone stale too: its prerequisites and OCR boundary converged (S0-arch-7/9/12), and its "could not do the job" finding is false clause by clause.
- `demos/benefits-demo.md:18,32` named `benefits-quote` (a doctype skill) as the process skill; the shipped process skill is `benefits-procurement`, with its rules in `assets/standard.yaml`. The demo had no beat for the scanned-PDF and 1000-row capabilities.
- `demos/rfp-demo.md:64` Requirement fields lagged `contract/wire.py` and listed a phantom `note`.

**Proposal:** `DECISIONS.md` "2026-10-01 — S0-docs-7" carries apply-ready text under the house "SPEC PROPOSAL (not applied — spec/ is protected)" marker. No frontmatter is touched (S0-docs-11).
- **standards.md:** three whole-line replacements. The seams are listed by name with no count, Email ingress included and named as a Bridge-owned boundary. D2 states the as-built posture: isolation at the network layer, a party addresses only its own leg, and every artifact fetch is authorized against the leg that asked.
- **open-questions.md:** a full-body replacement. The three resolved questions and the deferred bullet are deleted, and retention stays. The Benefits Consumer section is restated: what the Bridge now offers for carrier quotes, the delegate-or-keep question still open, and only contract shape (wider vocabulary refused, not coerced) and provenance kept as open sub-points.
- **benefits-demo.md:** a full-body replacement. The process skill is `benefits-procurement`, the doctype schema lists its tables, a new **`3b · Hard documents`** beat is added (no renumbering — `standard.yaml` and `bridge/` docstrings cite beat 4/5), plus two depth bullets, the scanned and 1000-row demo data and validation, and the design parameters stated as chosen.
- **rfp-demo.md:64:** the fields of `contract.wire.Requirement` plus `done` and the due date. The Nimbus scenario stays, as the industry-neutral requirement.
- **Design fold-ins**, so no decision is lost with the deleted questions: `design/fulfillment-graph.md:27` (fixed FX table, live source as a one-argument swap) and `design/collect.md` (item shape, `party` = party × item, due date anchoring the chase ladder).
- **Showcase runner:** the requirement is kept verbatim (`demo-suite.md:18,38`, `rfp-demo.md:55`). The missing harness is filed as **S0-arch-20**, which is also CUJ-9's missing Driver.

**Verification:**
- T1: no code changed. `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped, identical to the HEAD baseline, with no new skips.
- T2 (`/tmp/s0d7/t2.py`): re-extracts the six `markdown` fenced blocks from this entry and splices them onto copies of the real spec files (`/tmp/s0d7/x/*.proposed.md`). 0 failures:
  1. `git status --porcelain -- spec/` is empty, and no `spec/` path is staged.
  2. `cmp` of the frontmatter is byte-identical: standards 1-10, open-questions 1-6, benefits-demo 1-10, rfp-demo 1-9.
  3. Every relative `](x.md)` link resolves against the real file's directory: 17 / 15 / 16 / 14 links, 0 misses.
  4. Every `Seam` member (9) maps to a display name that appears in the proposed Ring-2 row, Email ingress included. `\b(eight|nine)\b` → none.
  5. No `strict=True`. The D2 bullet links `../design/zones.md`, which contains "network layer only".
  6. open-questions.md has none of the three resolved headings, exactly one "Artifact retention window" and one "Feature request (incoming)". The Benefits Consumer section still asks delegate-or-keep. The stale strings (convergence prerequisites, adapter swap, no real PDF path, could not do the job, Benefits/RFP deferred, eval scaffold) → none.
  7. benefits-demo.md has the "Process skill `benefits-procurement`" line ×1 and `request benefits-procurement` ×1, no `benefits-quote` as the process, and names `standard.yaml`. The named skills exist under `skills/`. Beats run 1, 2, 3, 3b, 4, 5, 6, and 3b mentions scanned + 1000 rows. Backing tests: the two scanned-PDF tests + `tests/perf/test_scale_extraction.py` + `tests/test_cuj11_negotiate_bind_rollup.py` → 8 passed, 0 skipped. Benefits Consumer offer paragraph: `tests/conformance` + `tests/test_rfp_second_use_case.py` → 86 passed / 6 skipped (gated); with `BRIDGE_TEST_CONSUMER_REPO=~/benefits-consumer`, `-k consumer` → 36 passed, the real-consumer drift check included.
  8. `set(Requirement.model_fields) == {item, status, party, doctype_hint, reason_code, message}`, and every field plus `done` and `required|optional|satisfied|waived` appears on the proposed :64. `note` is gone.
  9. The status grep on changed lines only (task ids, sprint/cycle, deferred, not yet, shipped, built, TODO, dates, checkboxes) → empty in all four files. There are no `<` placeholders.
  10. `design/fulfillment-graph.md` changes on line 27 only and `design/collect.md` gains the `party` paragraph. Both `+` sets are status-free, and only those two design files changed.
  11. `TODO.md` has `**S0-arch-20**` ×1 (naming `demo-suite.md:18,38` + `rfp-demo.md:55`) and the counter reads `s0-arch=20`. S0-docs-7 is still `- [ ]`.

### S0-docs-8 — Design docs restated to the as-built deployment (2026-10-01)

**What:** Design docs described things the deployed system does not do. They presented Vertex Agent Engine / `VertexAiSessionService` as the GCP sessions adapter and a working party-memory Memory Bank. They described Document AI as per-doctype processors and said every external request enters through the Gateway. They omitted the Artifacts bucket and `deletion_protection`, said a PENDING item drives `_status_for`, and claimed `skills-ref validate` runs in CI. This was a docs-only task plus comment/docstring-only edits in two `bridge/` files.

**Built (seven items, status-free prose):**
- **Sessions = Cloud SQL `DatabaseSessionService`.** The runtime's durable state is Cloud SQL Postgres — ADK `DatabaseSessionService` (sessions), a2a-sdk `DatabaseTaskStore` + push-config store (tasks), and the Bridge's `SqlExchangeStore` (exchange records), all on the one `DATABASE_URL` DSN. A managed session service would be a one-adapter swap behind the Sessions seam. `VertexAiSessionService` appears nowhere in code. Locally, `InMemorySessionService` or `DatabaseSessionService` over async sqlite when `BRIDGE_SESSION_DB_URL` is set. Updated `design/gcp-substrate.md`, `design/long-running.md`, `design/deploy-resume.md`, `design/a2a-consumer.md`, `design/adk.md`, `design/seams.md`.
- **Party memory reduced to interface intent.** `Seam.memory_bank` / `MemoryBankSeam` declares no methods. The local adapter is empty. There is no GCP adapter (the registry raises). No parity test. So no event log, profile rollup or Memory Bank exists, and nothing reads or writes party memory. Outbound uses a fixed channel (`Nudge.channel = "email"`), and chase copy is the Bridge default. The intent: cross-exchange counterparty memory could feed advisory hints (disposition pre-arming, extraction hints, outbound defaults), but the seam is advisory-only — never thresholds, never auto-routing, never off-path sends. Updated `design/party-memory.md`, `design/outbound.md`, `design/seams.md`, `design/gcp-substrate.md`.
- **Extraction engine set per deployment.** `BRIDGE_EXTRACTION_ENGINE=fixture|gemini|docai`, chosen once per deployment, not per doctype. The `docai` engine is that same Gemini request with the client's existing Document AI processor run as an OCR pre-pass — one processor for the deployment, no per-doctype processor or entity map. The skill frontmatter keys `bridge-extraction-engine` and `bridge-docai-processor` are declarative and not read by the Bridge. Updated `design/fulfillment-graph.md`, `design/seams.md`, `design/skills.md`, plus Path-B diagram in `design/fulfillment-graph.md` copied verbatim from `bridge/pathb.py` docstring.
- **Zones: the email webhook exception.** With the Gateway enabled the runtime accepts only load-balancer ingress. One exception: the email intake webhook — Microsoft Graph cannot present a Google identity — is a separate public Cloud Run service (`allUsers` invoker) outside the Gateway, authenticated per notification by the subscription's clientState and running as a create-only identity that can only land new objects in the email bucket. Updated `design/zones.md`.
- **Artifacts bucket and deletion_protection in gcp-substrate.** The `gcs` module homes the Skill Registry bucket and the Artifacts bucket. GCS bucket `<prefix>-artifacts` behind ADK `GcsArtifactService`, selected by `BRIDGE_ARTIFACT_BUCKET` (a native backend swap, not a seam); versioned — a resubmission is a new version; lifecycle Delete after `artifact_retention_days` (default 365), archived versions included; never public — bytes leave only by a signed URL minted after the leg-scope check. The root `deletion_protection` variable (default `true`) guards the Cloud SQL instance and the runtime and RFP-board Cloud Run services; the demo deploy script sets it `false`. The buckets carry none — their retention is the lifecycle rules. Updated `design/gcp-substrate.md`.
- **`_status_for` depends on `ExchangeTurn.terminal`.** The task status follows the turn, not the item: the edge maps `ExchangeTurn.terminal` — terminal → `COMPLETED`, non-terminal → `INPUT_REQUIRED`. A Collector or Path-A turn is always terminal, so a `pending` item rides a `COMPLETED` task and the consumer owns the review (`bridge-hitl: report`, the default). A `pending` item parks the task as `INPUT_REQUIRED` only when the process declares `bridge-hitl: park` and the edge is hybrid: the LLM leg holds it on `request_human_review`. The other non-terminal turns are an LLM-leg escalate and an RFP's turn, which waits on its carriers. Updated `design/disposition.md`, `design/a2a-consumer.md`, `bridge/edge/executor.py` docstring. The stale "not reachable yet" comment was already gone.
- **No CI and no skills-ref.** There is no `.github/`, no CI config, and no `skills-ref` in `pyproject.toml`/`uv.lock`. What checks a skill folder is the Bridge's own loader. `skill_meta_from_dir` parses `SKILL.md` frontmatter into the Pydantic `SkillMeta`; a missing `name`/`description`/`bridge-kind` or a bad `bridge-hitl` fails the load, identically on both adapters. `uv run pytest` loads every installed skill. Updated `design/seams.md`, `design/skills.md`.

**Gotchas:**
- Line numbers in the TODO entry had drifted (outbound `:35` not `:33`, disposition `:51` not `:46`, skills-ref `skills.md:119` not `:108`, `_status_for` `executor.py:938` not `:251`). Anchored on text.
- The stale executor comment was already gone. Do not invent one to delete.
- No test reads `design/*.md`. Doc edits cannot break pytest. The `bridge/` docstring edits can, through the isolation guard's skill-name docstring scan, so skill names were kept out.
- Don't delete the skill keys from `skills/*/SKILL.md`. Tests assert them.
- Artifacts is not a seam. Don't add it to the `Seam` list or the seams table.
- Design docs kept status-free. No "not yet built", "deferred", "this cycle", "later release" or phase ids in new text.

**Handoffs (residual drift, left to other tasks):**
- S0-docs-10: `docs/features.md:151` (SEAM-1 "Vertex Agent Engine"), `:155` (SEAM-5 "local event log / Vertex Memory Bank"), `:168` (GCP-1 "consumes managed session/memory via seams") carry the same drift.
- S0-docs-9 / later: `design/bridge.md:27` and `design/adk.md:75` list Memory Bank among the showcased platform pillars.
- Code-comment status leakage, left out of scope: `bridge/seams/protocols.py` `MemoryBankSeam` and `bridge/seams/local/__init__.py` `LocalMemoryBank` docstrings ("this cycle", "P7 GCP swap", TODO pointer), and the `GcpSessions` docstring ("a documented later swap").
- Skill frontmatter: the `bridge-extraction-engine` / `bridge-docai-processor` keys in `skills/*/SKILL.md` are inert. Removing them needs `tests/address/test_skills.py:245,312` updated. Kept, because docs now say they are declarative.

**Verification:**
- T1: `uv run ruff check` clean; `uv run pytest` green (all tests passed, with no new skips); isolation guard and outbound tests stayed green.
- T2: scope-check (diff touches only `design/`, `DECISIONS.md`, `TODO.md`, `docs/features/`, `bridge/outbound.py`, `bridge/edge/executor.py`; no `spec`, `terraform`, `contract`, `wiki/evals`, `skills`); `bridge/` changes are comment/docstring-only (AST with docstrings stripped matches HEAD); all grep checks passed (no `VertexAiSessionService`, `DatabaseSessionService` present in correct files, no "four" in party-memory, no "memory write", no "(P?)" in outbound, diagram verbatim from pathb.py, etc.); no status leakage in new text.

## In Progress

_Tracked in `TODO.md` (live status lives there, not here)._

## Planned Work

_Use `/new-feature` / `/new-task`; requirements land in `spec/`._

### S0-docs-10 — Feature catalog + feature docs catch up two sprints (2026-10-01)

**What:** `docs/features.md` stopped at SEAM-8 (no `email_ingress`), lacked entries for email intake / chunking / RFP views / disposition contract / conformance / any Benefits capability (Programs, FX, standard-gap, Negotiate/bind/rollup, interop), and had stale rows (OUT-3/OUT-4/AGENT-8 read "Def … (built with Benefits)"). `docs/features/benefits.md` showed "Planned (active Sprint 1)" with unchecked acceptance boxes though Sprint 1 closed 2026-09-07. `docs/features/gcp-deployment.md` listed P7-6/P7-8 under wrong titles. `docs/upstream-adk-issues.md` cited nonexistent `agents/src/bridge_client/…` and `google-adk == 2.7.0` (lock: 2.7.1).

**Done:** `docs/features.md` header/legend changed Scope → Area (Core · Address · Benefits · RFP · Deploy), dropped the C1/Def vocabulary, added Benefits oracle (`wiki/evals/benefits/`), fixed stale rows (SEAM-1/5/8, OUT-3/4, AGENT-8), added SEAM-9 (`email_ingress`), GCP-9, CORE-9, COLL-6/7, new sections N (Benefits — BEN-1…7), O (RFP views & email intake — RFP-1/2, EMAIL-1/2), P (Extraction depth — EXT-1/2), TEST-5; tables K/L/M Scope column dropped. `docs/features/benefits.md` acceptance boxes flipped with proof, Planned Work closed. `docs/features/gcp-deployment.md` P7-6/8 titles corrected. `docs/upstream-adk-issues.md` paths/versions fixed. `DECISIONS.md` "2026-10-01 — S0-docs-10" records decisions and follow-ups.

**Decisions:**
- **(a) Scope column replaced by Area (status-free).** The Scope legend's C1/C1‑slice/Def vocabulary is sequencing (lives in `TODO.md`), not feature categorization. Area groups by what exercises the core (Core, Address, Benefits, RFP demo, Deploy substrate), matching how the code is organized. Status-free per user preference.
- **(b) IDs appended, never renumbered.** `CORE‑9`, `COLL‑6/7`, `BEN‑1…7`, `RFP‑1/2`, `EMAIL‑1/2`, `EXT‑1/2`, `TEST‑5`, `SEAM‑9`, `GCP‑9` are new IDs. Existing IDs (e.g. `COLL‑5`, `SEAM‑8`) stayed unchanged — they are cited in `TODO.md`, `roadmap.md`, `DECISIONS.md` and test docstrings.
- **(c) Gemini golden-run scope stated honestly.** The fixture leg runs every branch; the Gemini leg is proven on the PDF intake branch only (fake client + credential-gated live). Revise/bind/rollup are fixture-only for now.
- **(d) Upstream issues still open on 2.7.1.** `remote_a2a_agent.py:629` still checks `ctx.session.events[-1].author != "user"`, and the `A2A_DATA_PART_START_TAG` constant is still present. Both issues reproduce on the locked 2.7.1.

**Follow-ups (not edited here):**
- `design/seams.md` "GCP Skill Registry" text vs the as-built GCS-bucket adapter (plan Step 1b comment).
- S0-docs-9 archive placement: sits under "## Planned Work" while S0-docs-8 is under "## Completed Work". Reconcile heading placement or leave as-is with a note that production-readiness.md is chronological append-only.

**Verification:**
- T1: `uv run ruff check` → clean. `uv run pytest -q` → 1738 passed / 97 skipped, identical to baseline at HEAD c97571f.
- T2 (all 11 checks passing):
  1. Seams row count == `Seam` enum count: 9 rows, 9 enum values, names match.
  2. Stale status vocabulary grep → 0 hits.
  3. Coverage grep: all required terms present (`email_ingress`, `chunking`, `/rfps`, `calibration`, `conformance`, `bridge/program.py`, `bridge/negotiate.py`, etc.).
  4. Every cited repo path exists (no MISSING in docs/features.md, docs/upstream-adk-issues.md, docs/features/benefits.md, docs/features/gcp-deployment.md).
  5. Cited test node ids run (pass or skip via existing GCP/live gates).
  6. `docs/features/benefits.md`: 0 open boxes (`- [ ]` count), 0 "Planned (active Sprint 1)" hits.
  7. `docs/features/gcp-deployment.md` P7 titles: P7-6 "Deployed extraction adapter", P7-8 "Service-wiring guard test"; no "Vertex" or "Identity seam" hits.
  8. `docs/upstream-adk-issues.md`: no phantom `bridge_client` or `agents/src` paths; `pending_resume_target` and `resume_stamping_interceptor` found; `extract_exchange_turn` and `_TAG_START` found; issues 1/2 workaround paths correct; env line states 2.7.1.
  9. New IDs use U+2011: grep for ASCII-hyphen table rows finds 0 hits.
  10. Relative links resolve: all `](…)` links in edited docs resolve.
  11. Scope fence: `git diff --name-only` → only docs/features.md, docs/features/benefits.md, docs/features/gcp-deployment.md, docs/upstream-adk-issues.md, docs/features/production-readiness.md, DECISIONS.md, TODO.md.

### S0-docs-9 — design/ de-statused; architecture.md is the as-built repo map (2026-10-01)

**What:** `design/` had status/sequencing leaks and `architecture.md` had the wrong module paths.
- `architecture.md:3,14,23,32,54-66` carried a "First planning cycle" / "deferred past cycle 1" plan that was both status and false (RFP built; `DialOut`/`Delivery` exist; `agents/benefits/` exists), cited nonexistent `bridge_client/remote_consumer.py` (real: `agents/address/collect.py`, `wire.py`, `interceptor.py`), placed the portal under `agents/address/` (it's `bridge/portal/`), and omitted `agents/console/`, `agents/benefits/`, `frontend/shared/`, the `bridge/` FastAPI hosts and `scripts/`.
- Same class in `bridge.md:33,58-59`, `a2a-edge.md:23`, `adk.md:33,62,80`, `outbound.md:39,41`, `proactive.md:18`, `deploy-resume.md:36,57,63-64,70`, `seams.md:32`, `service-agent-architecture.md:91`, `frontend.md:56`, `patterns.md:25`, `email-intake.md:27`. Total 22 status hits.
- `outbound.md` and `proactive.md` had no documentation of the operator's manual chase or per-skill ladders.

**Done:** every status/sequencing marker removed from `design/`, and `architecture.md` rewritten to the as-built module paths. `DECISIONS.md` "2026-10-01 — S0-docs-9" records decisions and follow-ups.

**Decisions:**
- **(a) First-cycle scope was deleted, not moved.** The "First planning cycle — Core + Address" and "Explicitly deferred past cycle 1" sections in `architecture.md` are deleted entirely. That content is sequencing (already in `docs/roadmap.md` / `TODO.md`), not design. Cold unsolicited push is now stated as out of scope (no edge accepts it without an open exchange) rather than "deferred".
- **(b) The anchor `#core-vs-demo-hosts` was preserved.** The heading `#### Core vs. demo hosts` in `architecture.md` is byte-identical. The anchor is linked from `design/bridge.md:29` and `docs/features/benefits.md:73`, and is cited in `tests/test_isolation_guard.py:181`.
- **(c) a2a-edge wording was coordinated with S3-edge-1.** The a2a-edge route table and hybrid paragraphs were not changed. The "Inbound fulfillment" paragraph's last sentence now names the as-built `bridge/outbound.py` activities (`Nudge` / `DialOut` / `Delivery` → CUJ-3/12).
- **(d) The RFP ladder is derived on read, so the manual chase is its only send path.** `proactive.md` documents the RFP ladder as anchored on `due_at` and derived on read (no alarm armed), so it sends nothing itself. A carrier is nudged only by the operator's manual chase (`POST /rfps/{id}/chase`, `Nudge.seq`, `record_chase`). That chase is now documented in both `outbound.md` and `proactive.md`.
- **(e) Line-number drift.** The line numbers in the TODO entry were written at bb6cd2f, and three tasks (S0-arch-15, S3-edge-1, S0-docs-8) have since edited these files. The plan's anchors were used, not the TODO's drifted numbers.

**Follow-ups (not edited here):**
- `docs/features.md:5,194` (the "layout & first-cycle scope" link text and the C1 / Def scope legend) → S0-docs-10.
- `README.md:107` ("layout and first-cycle scope") → S0-docs-11.
- `docs/roadmap.md:40` link text "layout & scope" (planning doc, low priority).
- `skills/address-proof/assets/policy.yaml:8`: the comment "Consumed by P5-1, not this cycle." is false and is status.
- The `bridge/` comment leaks: `bridge/outbound.py` DialOut docstring "the P7 transport's job", and `bridge/followup.py` "P5-3 plugs …" / "P5-4: persist" / "later wiring, not P5-2".

**Verification:**
- T1: no code changed. `uv run ruff check` is clean. `uv run pytest -q` → 1738 passed / 97 skipped, identical to the baseline at HEAD 1238c37, with no new skips.
- T2:
  1. Leak grep → 0 hits (baseline: 22).
  2. Phantom paths grep → 0 hits.
  3. TODO.md mentions: 3 hits (architecture.md tree line + "Design vs. planning" paragraph + bridge.md planning note).
  4. Path loop → no output (no MISSING). Backticked path extraction → all exist. Import test succeeds.
  5. Anchor grep → exactly one line.
  6. Chase and ladders: `POST /rfps/{` hits in both outbound.md and proactive.md. `record_chase` + `seq` in outbound.md, `due_at` + due-date anchoring in proactive.md. Numbers cross-checked against the two policy.yaml files (Address 1/2/3, RFP 1/3/5 → due, +2d, +4d). Tests green: `tests/test_rfp_views.py`, `tests/test_rfp.py`, `tests/test_followup.py`, `tests/test_outbound.py` → 77 passed / 11 skipped.
  7. Cited test ids: all exist and pass or skip only via the existing GCP gate. 4 passed.
  8. Protected files untouched: `git diff --name-only` shows only `design/*.md`, `DECISIONS.md`, `TODO.md`, `docs/features/production-readiness.md`. No `spec/`, `bridge/`, `agents/`, `skills/`, `contract/`, `terraform/` or `wiki/evals/`.
  9. Relative links resolve: every `](x.md…)` or `](../…)` link in the edited files resolves.
