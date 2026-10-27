# A2A Document Bridge — build conventions

Clean-slate build. Requirements (THE WHAT) live in [`spec/`](spec/cujs.md) (forkable,
status-free, protected); design (THE HOW) is [`design/`](design/bridge.md) (status-free);
live status + sequencing is [`TODO.md`](TODO.md); the feature catalog is
[`docs/features.md`](docs/features.md).

## Toolchain
- **Python 3.12+**, managed with **uv**. Run the test suite with `uv run pytest`.
  All tests must pass before a task is considered done. Never declare success with a
  failing or convenience-skipped test.
- **Lint (T1 gate):** `uv run ruff check` must be clean. Config in `pyproject.toml`
  (`[tool.ruff]`): select `E4/E7/E9,F,I,UP,B,SIM,ANN001,ANN201,ANN202,RUF`, line-length 88.
  `ruff format` owns line width — **E501 is intentionally not lint-enforced** (long prompt
  strings / docstrings). Fix findings or `--fix`; don't blanket-`noqa`. `UP042`/`RUF002` are
  ignored (wire-enum `__str__` semantics; intentional docstring unicode).
- **Frontend**: pnpm workspaces (`frontend/*`), React + TS + Vite + MUI; `frontend/shared`
  is the shared lib, `frontend/e2e` the Playwright suite.
- ADK is pinned `google-adk >= 2.11.0, < 3`; A2A on `a2a-sdk`.

## Repo shape (the load-bearing invariant)
```
spec/       THE WHAT — requirements (cujs, standards, demos/, open-questions); forkable, protected
design/     THE HOW — architecture, ports, zones, patterns + per-atom design notes (source of truth)
contract/   shared Pydantic wire types — the ONLY package both bridge/ and agents/ import
bridge/     the reusable Bridge CORE — NEVER imports agents/
agents/     service-agent consumers + demo drivers (Address, Benefits carriers, console)
skills/     Agent Skills the Bridge loads at runtime
frontend/   the four apps + shared lib + e2e
terraform/  IaC by substrate primitive
tests/      the shared port suite (local + gcp adapters) + demo golden runs;
            conformance/ (Benefits consumer conformance), perf/ (scale harness)
wiki/evals/ the eval corpus (Address + Benefits oracles) — a runtime fixture, referenced by code + tests
docs/       planning/ops — roadmap, feature catalog, local-dev, upstream ADK issues
scripts/    local run-*.sh hosts + _dev-env.sh; deploy.sh / parity.sh (live GCP apply)
```
- `bridge/` **never imports** `agents/`. An isolation-guard test enforces this.
- Every managed-service boundary is a **Port** (`bridge/ports/`):
  an ABC/Protocol across all 13 boundaries (`sessions`, `task_store`, `exchange_store`,
  `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`,
  `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`) with a **local** adapter
  (`bridge/ports/local/`) and a **GCP** adapter (`bridge/ports/gcp/`), exercised by shared
  parametrized tests (`@pytest.mark.port`, `adapter` fixture over `("local","gcp")`, GCP gated
  on `BRIDGE_TEST_GCP`). Parity is **terminal-outcome**, never ledger-identical.


## Planning Workflow (gen-3)

This project uses a sprint/phase-based planning workflow managed through slash commands.
Live status lives in ONE place (`TODO.md`); `spec/` and `design/` stay status-free.

### Layout

| Path | Purpose |
|------|---------|
| `TODO.md` | Tasks + live status/counts (the ONLY place status lives) |
| `DECISIONS.md` | Append-only decision/learning log (committed) |
| `CRITICAL.md` | Registry of protected/sensitive paths (auto-escalate) |
| `spec/` | THE WHAT — requirements. Forkable, status-free, **protected**. `cujs.md`, `standards.md`, `demos/*.md`, `open-questions.md` |
| `design/` | THE HOW (as built) — `architecture.md`, `ports.md`, `zones.md`, `patterns.md` + per-atom design notes; the design source of truth |
| `docs/` | Planning/ops — `roadmap.md`, `features.md`, `upstream-adk-issues.md` |
| `wiki/evals/` | The eval corpus (Address + Benefits oracles) — a runtime fixture, referenced by code + tests |

Committed: `TODO.md`, `spec/`, `design/`, `docs/`, `wiki/evals/`, `DECISIONS.md`, `CRITICAL.md`.

### Commands

- `/open` — top 3 open tasks (surfaces `- [!]` failed items)
- `/implement {task-id}` — implement, run the verification ladder, archive
- `/new-task` — add a critical issue to Sprint 0
- `/new-feature` — define a feature; writes requirements to `spec/`
- `/replan` — sprint/phase transition; runs the T3 CUJ gate
- `/verify [cuj-id]` — run CUJ simulation(s) on demand
- `/status` — planning summary + live progress

### Checkbox states

`- [ ]` open · `- [x]` done · `- [!]` attempted-but-failed (awaiting follow-up).

### Task ID format

This repo keeps its native `P<phase>-<seq>` scheme (e.g. `P1-3`), grouped under phase
headings in `TODO.md`; a task is unblocked when every task it "needs" is `- [x]`.
`TODO.md` carries a counter comment `<!-- Counters: p0=N p1=N ... -->` for per-phase seq.
(This is a deliberate deviation from the gen-3 `S<sprint>-<feature>-<seq>` default — see
`DECISIONS.md`.)

### Verification ladder

- **T1 (every /implement — gates the commit):** `uv run pytest` green (plus lint/type-check
  where configured). All tests must pass — never declare success with a failing or
  convenience-skipped test.
- **T2 (every /implement — gates flipping `- [x]`):** exercise the artifact against the
  task's own acceptance criteria. Files existing / build green is NOT done.
- **T3 (phase-close via /replan + on-demand via /verify):** CUJ simulation against the
  eval corpus. The Address eval corpus is the oracle (see Data discipline).

### CRITICAL escalation

Any task touching a path in `CRITICAL.md` auto-escalates: stronger model + mandatory
review before commit. `spec/` is protected — agents PROPOSE changes to spec, never edit
it during `/implement`.

## Task workflow (plan-implement)
- Each task: implement → `uv run pytest` green → flip its `- [ ]` to `- [x]` in `TODO.md`
  → commit path-scoped on the current branch (do not push).
- Match the style, naming, and port/adapter idioms of the surrounding code.

## Data discipline
- **Synthetic data only, no real PII.** The eval party "Jordan Lee" is synthetic.
- The Address eval corpus (`wiki/evals/address/`) is the oracle — no Address behavior
  is "done" until it reproduces `expected.json` (+ `timeline.json` where relevant).

## Hard-Won Lessons & Gotchas (Enterprise Data Architecture, Agent Gym & GCP Deploy)

1. **Skill-Name Literal Guard (`tests/test_isolation_guard.py::test_core_names_no_skill`):**
   - `test_core_names_no_skill` scans every Python AST string literal in `bridge/` (outside `bridge/demos/`) and fails if any non-docstring literal matches an installed folder name in `skills/` (`address-proof`, `gov-id`, `utility-bill`, `benefits-procurement`, `benefits-quote`, `benefits-rfp`). Never hardcode an installed skill name in `bridge/` core modules.
2. **Explicit Demo Seeding (`POST /v1/demo/seed`) vs. Router Init:**
   - Never call `seed_demo_scenarios()` unconditionally inside `build_user_interaction_router()` or `build_user_interaction_app()`, because unit tests construct fresh `InMemoryOperationalStore` / `SqlOperationalStore` instances and assert exact fact counts or empty initial state. Keep seeding explicit via `POST /v1/demo/seed` and `POST /v1/gym/trust-policies/reset?reseed_demo=true`.
3. **Role Resolution (`query_role` vs. `default_role` in `resolve_caller_role`):**
   - Never pass `query_role=role or default_role` into `resolve_caller_role()`. If a request carries `X-Bridge-Role: underwriter` (or an IAP email) without a `?role=` query param, passing `default_role="broker"` as `query_role` triggers the anti-spoofing guard (`PermissionError: cannot impersonate role 'broker'`). Always pass `query_role=role, default_role="broker"` separately so `default_role` only applies when no authenticated role and no `query_role` are present.
4. **Wire `Disposition` Enum vs. `"parked"` Intake Status:**
   - `contract.enums.Disposition` only defines `accepted`, `rejected`, and `pending` (there is no `Disposition.parked`). Endpoints accepting `status="parked"` (`IngestSubmissionRequest`) must map `"parked"` to `Disposition.pending` (`Gate.hitl_review`), and Day-2 Agent Gym auto-promotion upgrades `Disposition.pending` → `Disposition.accepted` (`Gate.auto_approve`).
5. **Cloud Build `uv.lock` Index & Headless Chrome DevTools Proxy Bypass:**
   - If regenerating `uv.lock` before `gcloud builds submit`, always pass `--default-index https://pypi.org/simple` so external Cloud Build workers resolve wheels from `https://files.pythonhosted.org` rather than a local workstation mirror.
   - In `demo/record_gcp_demo.py`, always pass `--no-proxy-server` to headless Chrome and `trust_env=False` to `httpx.AsyncClient(trust_env=False)` when polling `http://127.0.0.1:<port>/json`, otherwise environment proxy variables intercept localhost DevTools connections.
6. **Cloud Run Warm Instances & Terraform Auth:**
   - Both `docubridge-showcase-bridge` and `docubridge-showcase-bridge-rfp-board` run with `min_instances = 1` (`terraform/modules/runtime/main.tf`) so live `/demo` showcase sessions have zero cold-start latency.
   - When running `terraform` outside GCE metadata environments, export `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"` or update Cloud Run services directly via `gcloud run services update`.
7. **Offline `uv.lock` Version Sync, `git rm` `__pycache__` Cleanup & `KnowledgeCatalog` Protocol:**
   - Whenever `project.version` in `pyproject.toml` is bumped, also update `version = "..."` on the `[[package]] name = "docubridge"` entry in `uv.lock` so `uv run` does not attempt a network re-lock in offline sandboxes.
   - Running `git rm -rf <pkg>` leaves untracked `__pycache__/` directories on disk; follow with `rm -rf <pkg>` when tests assert directory removal.
   - `bridge.knowledge_catalog.KnowledgeCatalog` is a `typing.Protocol`; instantiate `InMemoryKnowledgeCatalog(operational_store=...)` when binding `CollectExecutor.bind_enterprise_stores(...)` or `build_a2a_app(...)`.
