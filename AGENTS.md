# AGENTS.md — A2A Document Bridge (`docubridge`)

Clean-slate build. Requirements live in [`spec/`](spec/cujs.md) (forkable, status-free, protected); design lives in [`design/`](design/bridge.md) (status-free); live roadmap + milestone status is [`docs/roadmap.md`](docs/roadmap.md); feature catalog is [`docs/features.md`](docs/features.md). Read [`CRITICAL.md`](CRITICAL.md) and [`DECISIONS.md`](DECISIONS.md) before touching protected paths.

## 1. Toolchain
- **Python 3.12+ / `uv`:** Run `uv lock --default-index https://pypi.org/simple` before Cloud Build so workers resolve from `pypi.org`; whenever `project.version` in `pyproject.toml` bumps, update `[[package]] name = "docubridge"` in `uv.lock` too so offline `uv run` never re-resolves.
- **Lint & Types:** `pyproject.toml` enables all 16 Ruff rule families (line-length 88; E501 intentionally off; `UP042`/`RUF002` ignored) and `[tool.mypy] strict = true` on `contract` + `bridge`. Fix findings instead of blanket-`noqa`.
- **Frontend & ADK:** `pnpm` workspaces (`frontend/*`, React/TS/Vite/MUI, Playwright in `frontend/e2e`); `google-adk >= 2.11.0, < 3`, `a2a-sdk`.

## 2. Repository Map & Load-Bearing Invariants

| Path | Responsibility |
| :--- | :--- |
| [`spec/`](spec/cujs.md) | THE WHAT — requirements (`cujs.md`, `standards.md`, `demos/`, `open-questions.md`); protected, status-free |
| [`design/`](design/bridge.md) | THE HOW (as built) — `bridge.md` → 5 area docs + atom notes with `type`/`up`/`down` front-matter (`tests/test_documentation_contract.py`) |
| `contract/` & `bridge/` | Shared Pydantic wire types (`contract/`) and reusable core (`bridge/` — **never** imports `agents/` or `examples/`) |
| `examples/`, `catalog/`, `skills/` | Wire-only A2A consumer (`docubridge-examples` holds full ADK/LangGraph), `*.okf.md` doctype bundles, runtime skills |
| `demo/`, `frontend/`, `terraform/`, `scripts/` | 5-scene Showcase Console, 4 Vite apps + `e2e`, IaC + 56 `.tftest.hcl` checks, local runners + `deploy.sh`/`parity.sh` |
| [`docs/`](docs/roadmap.md) & `wiki/evals/` | Planning/ops (`roadmap.md`, `features.md`, `local-dev.md`, `upstream-adk-issues.md`); eval oracle (`expected.json` + `timeline.json`) |

- **Isolation AST Guard (`specs/ast.rules.yaml` + `tests/test_isolation_guard.py`):** `bridge/` never imports `agents/`/`examples/`; `examples/` never imports `bridge/`/`agents/`; non-test code never imports `sqlalchemy.orm`, uses `shell=True`, or passes f-strings to `.execute()`.
- **Ports, Adapters and Contracts (`bridge/ports/{local,gcp}/`):** All 14 managed boundaries resolve via `resolve_port_mode(port)` (`BRIDGE_PORT_<NAME>` → `BRIDGE_PORT_MODE` → `local`) and `get_port_adapter(port, mode)`, verified by shared `@pytest.mark.port` suites (GCP gated on `BRIDGE_TEST_GCP`; terminal-outcome parity).
- **Skill-Name Literal Guard (`test_core_names_no_skill`):** `tests/test_isolation_guard.py` fails if any non-docstring Python string literal in `bridge/` (outside `bridge/demos/`) matches an installed folder name under `skills/`.

## 3. Planning Workflow & Verification Ladder
- **Single Tracker:** [`docs/roadmap.md`](docs/roadmap.md) holds all open `- [ ]` (`P<phase>-<seq>`) and `- [!]` items (no root `TODO.md`; `spec/` and `design/` stay status-free). Keep local work `- [ ]`; reserve `- [x]` strictly for committed, released/deployed, and live-verified milestones, then archive via `gate roadmap-archive docubridge --done`.
- **`CRITICAL.md` Escalation & Data Discipline:** Tasks touching [`CRITICAL.md`](CRITICAL.md) paths require stronger-model review before commit; propose `spec/` edits in `DECISIONS.md` rather than editing directly. Synthetic data only ("Jordan Lee", no real PII).
- **Verification Ladder:**
  - **T1 (commit gate):** `gate test docubridge` (runs `scripts/gate.sh`: `uv lock --check --offline --no-config`, `ast-grep`, `ruff check`, `ruff format --check`, `mypy --strict contract bridge`, `pytest`). For Terraform edits: `terraform -chdir=terraform init -backend=false && terraform -chdir=terraform test`.
  - **T2 (milestone gate):** Verify against each task's acceptance criteria before closing.
  - **T3 (phase close):** CUJ simulation against the `wiki/evals/address/` oracle.

## 4. Operational Lessons & Traps
1. **Explicit Demo Seeding:** Never call `seed_demo_scenarios()` inside `build_user_interaction_router()`/`build_user_interaction_app()`; use `POST /v1/demo/seed` or `POST /v1/gym/trust-policies/reset?reseed_demo=true`.
2. **Role Resolution (`resolve_caller_role`):** Pass `query_role=role, default_role="broker"` separately (never `query_role=role or default_role`, which false-triggers anti-spoofing when `X-Bridge-Role` is set without `?role=`).
3. **Intake `"parked"` Status:** `contract.enums.Disposition` has only `accepted|rejected|pending`; map `status="parked"` to `Disposition.pending` (`Gate.hitl_review`), promoted on Day-2 via Agent Gym (`Gate.auto_approve`).
4. **Agent Gym Scope (`ADR-15`):** Learns strictly extraction/derivation/reconciliation (`EXTRACTION_HINT` + `apply_result_derivations()` / `apply_field_derivations()`), never which documents are needed/acceptable/waivable.
5. **Agent Engine (`BridgeA2aAgentEngine`):** Implements `OperationRegistrable` + `Cloneable` directly on `a2a-sdk==1.1.2` (stock `A2aAgent` rejects `streaming=True`), strips C protobuf descriptors in `__getstate__`/`clone()`, and `resolve_a2a_engine()` imports `vertexai.agent_engines`.
6. **Headless Chrome Proxy Bypass:** In `demo/record_gcp_demo.py`, pass `--no-proxy-server` to Chrome and `trust_env=False` to `httpx.AsyncClient`.
7. **GCP Deploy, Observability & Terraform Traps (M1 / `P10-4`):**
   - Runtime runs 3 per-app Cloud Run services (`bridge-kyc`, `bridge-rfp`, `bridge-meridian`); `bridge-meridian` requires `BRIDGE_RFP_PROCESS` in `meridian_plain_env`.
   - BigLake `google_bigquery_table.completions` requires top-level `schema = jsonencode(...)`; create `obs_traces` linked dataset only after `_Trace` receives first OTLP span (`exporter._client._headers` is mutable; `a2a-sdk` SSE close emits `status.code = 2`).
   - Refresh `export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"` immediately before `terraform apply`; create scheduled queries via `POST https://bigquerydatatransfer.googleapis.com/v1/projects/{project_number}/locations/{location}/transferConfigs?serviceAccountName={sa_email}` (never `bq mk --transfer_config`).
   - Never use `objectListPrefix` in GCS bucket IAM CEL conditions; scope read/write via `resource.name.startsWith("projects/_/buckets/<bucket>/objects/<prefix>/")` and grant `storage.objects.list` / `storage.objects.setRetention` via minimal custom roles for single-bucket WORM.
8. **Cleanup & Catalog Protocol:** After `git rm -rf <pkg>`, delete untracked `__pycache__/` too; instantiate `InMemoryKnowledgeCatalog(operational_store=...)` rather than `Protocol` `KnowledgeCatalog`.

## 5. Single-Branch (`main`) & Explicit Demo Isolation
All capabilities are unified on **`main`**. Always pass an explicit `Demos` or `Experiments` project to `./scripts/deploy.sh` (`PROJECT_ID=<id>`) with `BLOCKED_PROJECT_IDS=<id1>,<id2>` exported (see `.env.example`) to enforce the Terraform/deploy deny-list without committing real IDs; keep `./scripts/deploy.sh` non-prefix-matchable.
