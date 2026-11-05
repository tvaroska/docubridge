# AGENTS.md — A2A Document Bridge Build Conventions & Operational Lessons

Clean-slate build (`~/projects/docubridge`). Requirements (THE WHAT) live in
[`spec/`](spec/cujs.md) (forkable, status-free, protected); design (THE HOW) is
[`design/`](design/bridge.md) (status-free); live roadmap + milestone status is
[`docs/roadmap.md`](docs/roadmap.md) (an ephemeral root `TODO.md` can be created when an active sprint requires granular task tracking); the feature catalog is [`docs/features.md`](docs/features.md).

## 1. Toolchain

- **Python 3.12+**, managed with **uv**. Run the test suite with `uv run pytest`.
  All tests must pass before a task is considered done. Never declare success with a
  failing or convenience-skipped test.
- **Lint (T1 gate):** `uv run ruff check .` must be clean. Config in `pyproject.toml`
  (`[tool.ruff]`): select `E4/E7/E9,F,I,UP,B,SIM,ANN001,ANN201,ANN202,RUF`, line-length 88.
  `ruff format` owns line width — **E501 is intentionally not lint-enforced** (long prompt
  strings / docstrings). Fix findings or `--fix`; don't blanket-`noqa`. `UP042`/`RUF002` are
  ignored (wire-enum `__str__` semantics; intentional docstring unicode).
- **Frontend**: pnpm workspaces (`frontend/*`), React + TS + Vite + MUI; `frontend/shared`
  is the shared lib, `frontend/e2e` the Playwright suite.
- ADK is pinned `google-adk >= 2.11.0, < 3`; A2A on `a2a-sdk`.

## 2. Repository Map & Core Invariants (The Load-Bearing Invariant)

```
spec/       THE WHAT — requirements (cujs, standards, demos/, open-questions); forkable, protected
design/     THE HOW — bridge.md → five areas: model, edges, knowledge-model, runtime, architecture (source of truth)
contract/   shared Pydantic wire types — the ONLY package both bridge/ and agents/ import
bridge/     the reusable Bridge CORE — NEVER imports agents/
agents/     service-agent consumers + demo drivers (Address, Benefits carriers, console)
catalog/    OKF doctype bundles (catalog/doctypes/*.okf.md) — canonical field schemas; skills/<doctype>/assets/schema.json is generated from them
skills/     Agent Skills the Bridge loads at runtime (process = how to collect; doctype = the installable unit pointing at its bundle)
demo/       5-scene Enterprise Showcase Console (enterprise-demo.html) + GCP video recorder
frontend/   the four React/Vite apps + shared lib + e2e
terraform/  IaC by substrate primitive (network, iam, database, secrets, gcs, tasks, telemetry, runtime, gateway, frontend)
tests/      the shared port suite (local + gcp adapters) + enterprise architecture + conformance + perf
wiki/evals/ the eval corpus (Address + Benefits oracles) — a runtime fixture, referenced by code + tests
docs/       planning/ops — roadmap, feature catalog, local-dev, upstream ADK issues
scripts/    local run-*.sh hosts + _dev-env.sh; deploy.sh / parity.sh (live GCP apply)
```

- **Isolation Invariant (`tests/test_isolation_guard.py`):** `bridge/` **never imports** `agents/`, and `agents/` **never imports** `bridge/`. Both import `contract/`.
- **Ports & Adapters (`bridge/ports/`):** Every managed-service boundary across all 13 boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`) resolves through `resolve_port_mode(port)` (`BRIDGE_PORT_<NAME>` → `BRIDGE_PORT_MODE` → `local`) and `get_port_adapter(port, mode)`, decomposed into `bridge/ports/local/{stores,services,enterprise,__init__}.py` and `bridge/ports/gcp/{common,stores,services,enterprise,__init__}.py`, and is exercised by shared parametrized tests (`@pytest.mark.port`, `adapter` fixture over `("local","gcp")`, GCP gated on `BRIDGE_TEST_GCP`). Parity is **terminal-outcome**, never ledger-identical. Legacy `seams` terminology is permanently removed.
- **Skill-Name Literal Guard (`test_core_names_no_skill`):** `tests/test_isolation_guard.py` scans every Python AST string literal in `bridge/` (outside `bridge/demos/`) and fails if any non-docstring literal equals an installed folder name in `skills/` (`address-proof`, `gov-id`, `utility-bill`, `benefits-procurement`, `benefits-quote`, `benefits-rfp`, `commercial-loan-underwriting`, `trid-closing-disclosure`, `cre-credit-agreement`, `mai-appraisal`, `commercial-property-quote`). Never hardcode an installed skill folder name inside `bridge/` core modules.

## 3. Planning Workflow (gen-3) & Verification Ladder

This project uses a roadmap-first planning workflow managed through slash commands.
Live milestone status lives in [`docs/roadmap.md`](docs/roadmap.md) (with an ephemeral root `TODO.md` created only when an active sprint requires granular task tracking); `spec/` and `design/` stay status-free.

### Layout

| Path | Purpose |
|------|---------|
| [`docs/roadmap.md`](docs/roadmap.md) | Milestones + live status (an ephemeral root `TODO.md` can be created when an active sprint requires granular task tracking) |
| `DECISIONS.md` | Append-only decision/learning log (committed) |
| `CRITICAL.md` | Registry of protected/sensitive paths (auto-escalate) |
| `spec/` | THE WHAT — requirements. Forkable, status-free, **protected**. `cujs.md`, `standards.md`, `demos/*.md`, `open-questions.md` |
| `design/` | THE HOW (as built) — `bridge.md` (root) → `model.md`, `edges.md`, `knowledge-model.md`, `runtime.md`, `architecture.md` (areas) → per-atom design notes; every file carries `type` / `up` / `down` front-matter and `tests/test_documentation_contract.py` asserts the tree; the design source of truth |
| `docs/` | Planning/ops — `roadmap.md`, `features.md`, `upstream-adk-issues.md` |
| `wiki/evals/` | The eval corpus (Address + Benefits oracles) — a runtime fixture, referenced by code + tests |

Committed: `spec/`, `design/`, `docs/`, `wiki/evals/`, `DECISIONS.md`, `CRITICAL.md` (plus optional `TODO.md` during active sprints).

### Commands & Checkbox States

- `/open` — top 3 open tasks (surfaces `- [!]` failed items)
- `/implement {task-id}` — implement, run the verification ladder, archive
- `/new-task` — add a critical issue to Sprint 0
- `/new-feature` — define a feature; writes requirements to `spec/`
- `/replan` — sprint/phase transition; runs the T3 CUJ gate
- `/verify [cuj-id]` — run CUJ simulation(s) on demand
- `/status` — planning summary + live progress
- Checkbox states: `- [ ]` open · `- [x]` done · `- [!]` attempted-but-failed (awaiting follow-up).
- Task ID format: native `P<phase>-<seq>` scheme (e.g. `P1-3`), grouped under phase headings in [`docs/roadmap.md`](docs/roadmap.md) (or an ephemeral root `TODO.md` during active sprints) with `<!-- Counters: p0=N p1=N ... -->` (see `DECISIONS.md`).

### Verification Ladder

- **T1 (every `/implement` — gates the commit):**
  - Lint: `uv run ruff check .` (must pass with 0 errors).
  - Unit & Enterprise Suite: `uv run pytest` (including `tests/test_enterprise_data_architecture.py`, `tests/test_isolation_guard.py`, `tests/test_service_wiring_guard.py`, `tests/test_gemini_extraction.py`).
  - Terraform Plan-Time Tests: `terraform -chdir=terraform init -backend=false && terraform -chdir=terraform test` (45 `run` blocks / 183 `assert` blocks across 13 `.tftest.hcl` files, credential-free).
- **T2 (every `/implement` — gates flipping `- [x]`):** exercise the artifact against the task's own acceptance criteria. Files existing / build green is NOT done.
- **T3 (phase-close via `/replan` + on-demand via `/verify`):** CUJ simulation against the eval corpus. The Address eval corpus (`wiki/evals/address/`) is the oracle (`expected.json` + `timeline.json`).

### CRITICAL Escalation, Task Workflow & Data Discipline

- Any task touching a path in `CRITICAL.md` auto-escalates: stronger model + mandatory review before commit. `spec/` is protected — agents PROPOSE changes to spec (`DECISIONS.md`), never edit it during `/implement`.
- Each task: implement → `uv run pytest && uv run ruff check .` green → flip its `- [ ]` to `- [x]` in [`docs/roadmap.md`](docs/roadmap.md) (or active `TODO.md`) → commit path-scoped on the current branch (do not push). Match the style, naming, and port/adapter idioms of the surrounding code.
- **Synthetic data only, no real PII.** The eval party "Jordan Lee" is synthetic.

## 4. Hard-Won Lessons Learned & Gotchas (Enterprise Data Architecture, Agent Gym & GCP Deploy)

1. **Explicit Demo Seeding (`POST /v1/demo/seed`) vs. Router Init:**
   - Never call `seed_demo_scenarios()` unconditionally inside `build_user_interaction_router()` or `build_user_interaction_app()`. Unit tests pass fresh `InMemoryOperationalStore` / `SqlOperationalStore` instances and assert exact record counts or empty initial state. Keep seeding explicit via `POST /v1/demo/seed` and `POST /v1/gym/trust-policies/reset?reseed_demo=true`.
2. **Role Resolution (`query_role` vs. `default_role` in `resolve_caller_role`):**
   - Never pass `query_role=role or default_role` into `resolve_caller_role()`. When a caller sends `X-Bridge-Role: underwriter` (or an IAP email) without a `?role=` query parameter, passing `default_role="broker"` as `query_role` falsely triggers the anti-spoofing check (`PermissionError: has role 'underwriter', cannot impersonate role 'broker'`). Always pass `query_role=role, default_role="broker"` separately so `default_role` is used only when both `verified_role` and `query_role` are `None`.
3. **Wire `Disposition` Enum vs. `"parked"` Intake Status:**
   - `contract.enums.Disposition` only defines `accepted`, `rejected`, and `pending` (there is no `Disposition.parked`). Endpoints accepting `status="parked"` (`IngestSubmissionRequest`) must map `"parked"` to `Disposition.pending` (`Gate.hitl_review`), and Day-2 Agent Gym auto-promotion upgrades `Disposition.pending` → `Disposition.accepted` (`Gate.auto_approve`).
4. **End-to-End Agent Gym Day-2 Payoff Wiring:**
   - Promoted `EXTRACTION_HINT` rules in `AgentGymEngine` (`bridge/agent_gym/`) are wired in two places: (a) `GeminiExtractor._catalog()` / `build_extraction_prompt(schemas, extraction_hints=...)` (`bridge/extraction/gemini.py`) dynamically rebuilds the extraction prompt when promoted rule IDs change, and (b) `ingest_accepted_submission()` (`bridge/user_interaction/surfaces.py`) applies matching hints to `key_fields`, auto-promotes `Disposition.pending` → `Disposition.accepted`, and records `docubridge.applied_extraction_hints` on the OpenTelemetry span.
5. **Agent Engine (`BridgeA2aAgentEngine`) Gotchas:**
   - `vertexai.preview.reasoning_engines.A2aAgent` in `google-cloud-aiplatform==1.165.1` targets `a2a-sdk 0.3.x` and rejects `capabilities.streaming=True`; `BridgeA2aAgentEngine` (`bridge/edge/agent_engine_a2a.py`) implements `OperationRegistrable` + `Cloneable` directly on `a2a-sdk==1.1.2` and strips C-extension protobuf descriptors in `__getstate__`/`clone()` for `cloudpickle`.
   - `resolve_a2a_engine()` must explicitly `import vertexai.agent_engines` (not just `import vertexai`).
6. **Cloud Build `uv.lock` Index & Headless Chrome DevTools Proxy Bypass:**
   - When regenerating `uv.lock` before `gcloud builds submit`, always run `uv lock --default-index https://pypi.org/simple` so remote Cloud Build workers resolve packages from `https://files.pythonhosted.org` rather than a local workstation mirror.
   - In `demo/record_gcp_demo.py`, always pass `--no-proxy-server` to headless Chrome and `trust_env=False` to `httpx.AsyncClient(trust_env=False)` when polling `http://127.0.0.1:<port>/json`, otherwise proxy environment variables intercept localhost DevTools connections.
7. **Cloud Run Warm Instances & Terraform Auth:**
   - Both `docubridge-showcase-bridge` and `docubridge-showcase-bridge-rfp-board` are configured with `min_instances = 1` (`terraform/modules/runtime/main.tf`) to prevent cold starts during live demos.
   - When running `terraform` outside GCE metadata environments, either export `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"` or update Cloud Run services via `gcloud run services update`.
8. **Offline `uv.lock` Version Sync, `git rm` `__pycache__` Cleanup & `KnowledgeCatalog` Protocol:**
   - Whenever `project.version` in `pyproject.toml` is bumped, also update `version = "..."` on the `[[package]] name = "docubridge"` entry in `uv.lock`; otherwise `uv run` detects a lockfile mismatch and attempts to re-resolve dependencies over the network (failing in offline sandboxes).
   - Running `git rm -rf <pkg>` removes tracked `.py` files but leaves untracked `__pycache__/` directories on disk, causing `Path("<pkg>").exists()` checks to remain `True` until `rm -rf <pkg>` is also executed.
   - `bridge.knowledge_catalog.KnowledgeCatalog` is a `typing.Protocol` (cannot be instantiated directly); always instantiate `InMemoryKnowledgeCatalog(operational_store=...)` when binding `CollectExecutor.bind_enterprise_stores(...)` or `build_a2a_app(...)` in local/test runs.

## 5. Single-Branch Workflow (`main`) & Explicit Demo Project Isolation

All capabilities (6-Zone Enterprise Data Architecture, Collaborative Loan Consolidation + live multimodal PDF `/ebc`, and Phase 8 Schema Refinery `P8-1`–`P8-4`) are unified on **`main`**. When deploying to GCP, always specify an explicit `Demos` or `Experiments` project (`./scripts/deploy.sh` with `PROJECT_ID=<id>`) — the operator's shared/Core project ids are hard-blocked by `deploy.sh` and `terraform/variables.tf` through an operator-supplied deny-list: export `BLOCKED_PROJECT_IDS=<id1>,<id2>` (comma-separated; see `.env.example`) and `deploy.sh` refuses a matching `PROJECT_ID` and forwards the list as `TF_VAR_blocked_project_ids` to the `project_id` validation. Never commit real project ids to the list.


