# AGENTS.md — A2A Document Bridge Agent Guide & Lessons Learned

This file mirrors [`CLAUDE.md`](CLAUDE.md) and captures the repository invariants, architecture boundaries, verification gates, and operational lessons learned across the 6-Zone Enterprise Data Architecture, Agent Gym, Vertex AI Agent Engine, and GCP (`docubridge-showcase`) deployments.

## 1. Repository Map & Core Invariants

```
spec/       THE WHAT — requirements (cujs, standards, demos/, open-questions); forkable, protected
design/     THE HOW — architecture, seams, zones, patterns, agent-gym, harness-evolve (source of truth)
contract/   shared Pydantic wire types — the ONLY package both bridge/ and agents/ import
bridge/     the reusable Bridge CORE — NEVER imports agents/
agents/     service-agent consumers + demo drivers (Address, Benefits carriers, console)
skills/     Agent Skills the Bridge loads at runtime
demo/       5-scene Enterprise Showcase Console (enterprise-demo.html) + GCP video recorder
frontend/   the four React/Vite apps + shared lib + e2e
terraform/  IaC by substrate primitive (network, iam, database, secrets, gcs, tasks, telemetry, runtime, gateway, frontend)
tests/      the shared seam suite (local + gcp adapters) + enterprise architecture + conformance + perf
```

- **Isolation Invariant (`tests/test_isolation_guard.py`):** `bridge/` **never imports** `agents/`, and `agents/` **never imports** `bridge/`. Both import `contract/`.
- **Ports & Adapters (`bridge/ports/` & `bridge/seams/`):** All 13 managed-service boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`) resolve through `resolve_port_mode(port)` (`BRIDGE_PORT_<NAME>` → `BRIDGE_SEAM_<NAME>` → `BRIDGE_PORT_MODE` → `BRIDGE_SEAM_MODE` → `local`) and `get_port_adapter(port, mode)`.
- **Skill-Name Literal Guard (`test_core_names_no_skill`):** `tests/test_isolation_guard.py` scans every Python AST string literal in `bridge/` (outside `bridge/demos/`) and fails if any non-docstring literal equals an installed folder name in `skills/` (`address-proof`, `gov-id`, `utility-bill`, `benefits-procurement`, `benefits-quote`, `benefits-rfp`, `commercial-loan-underwriting`, `trid-closing-disclosure`). Never hardcode an installed skill folder name inside `bridge/` core modules.

- **Protected Spec (`CRITICAL.md`):** Never edit `spec/` during implementation tasks; propose spec changes in `DECISIONS.md` instead.

## 2. Verification Ladder

- **Lint:** `uv run ruff check .` (must pass with 0 errors).
- **Unit & Enterprise Suite:** `uv run pytest` (including `tests/test_enterprise_data_architecture.py`, `tests/test_isolation_guard.py`, `tests/test_service_wiring_guard.py`, `tests/test_gemini_extraction.py`).
- **Terraform Plan-Time Tests:** `terraform -chdir=terraform init -backend=false && terraform -chdir=terraform test` (42 assertions across 13 `.tftest.hcl` files, credential-free).

## 3. Hard-Won Lessons Learned & Gotchas

1. **Explicit Demo Seeding (`POST /v1/demo/seed`) vs. Router Init:**
   - Never call `seed_demo_scenarios()` unconditionally inside `build_user_interaction_router()` or `build_user_interaction_app()`. Unit tests pass fresh `InMemoryOperationalStore` / `SqlOperationalStore` instances and assert exact record counts or empty initial state. Keep seeding explicit via `POST /v1/demo/seed` and `POST /v1/gym/trust-policies/reset?reseed_demo=true`.
2. **Role Resolution (`query_role` vs. `default_role` in `resolve_caller_role`):**
   - Never pass `query_role=role or default_role` into `resolve_caller_role()`. When a caller sends `X-Bridge-Role: underwriter` (or an IAP email) without a `?role=` query parameter, passing `default_role="broker"` as `query_role` falsely triggers the anti-spoofing check (`PermissionError: has role 'underwriter', cannot impersonate role 'broker'`). Always pass `query_role=role, default_role="broker"` separately so `default_role` is used only when both `verified_role` and `query_role` are `None`.
3. **Wire `Disposition` Enum vs. `"parked"` Intake Status:**
   - `contract.enums.Disposition` only defines `accepted`, `rejected`, and `pending` (there is no `Disposition.parked`). Endpoints accepting `status="parked"` (`IngestSubmissionRequest`) must map `"parked"` to `Disposition.pending` (`Gate.hitl_review`), and Day-2 Agent Gym auto-promotion upgrades `Disposition.pending` → `Disposition.accepted` (`Gate.auto_approve`).
4. **End-to-End Agent Gym Day-2 Payoff Wiring:**
   - Promoted `EXTRACTION_HINT` rules in `AgentGymEngine` (`bridge/agent_gym/`) are wired in two places: (a) `GeminiExtractor._catalog()` / `build_extraction_prompt(schemas, extraction_hints=...)` (`bridge/extraction/gemini.py`) dynamically rebuilds the extraction prompt when promoted rule IDs change, and (b) `ingest_accepted_submission()` (`bridge/user_interaction/surfaces.py`) applies matching hints to `key_fields`, auto-promotes `Disposition.pending` → `Disposition.accepted`, and records `docubridge.applied_extraction_hints` on the OpenTelemetry span.
5. **Vertex AI Agent Engine (`BridgeA2aAgentEngine`) Gotchas:**
   - `vertexai.preview.reasoning_engines.A2aAgent` in `google-cloud-aiplatform==1.165.1` targets `a2a-sdk 0.3.x` and rejects `capabilities.streaming=True`; `BridgeA2aAgentEngine` (`bridge/edge/agent_engine_a2a.py`) implements `OperationRegistrable` + `Cloneable` directly on `a2a-sdk==1.1.2` and strips C-extension protobuf descriptors in `__getstate__`/`clone()` for `cloudpickle`.
   - `resolve_a2a_engine()` must explicitly `import vertexai.agent_engines` (not just `import vertexai`).
6. **Cloud Build `uv.lock` Index & Headless Chrome DevTools Proxy Bypass:**
   - When regenerating `uv.lock` before `gcloud builds submit`, always run `uv lock --default-index https://pypi.org/simple` so remote Cloud Build workers resolve packages from `https://files.pythonhosted.org` rather than a local workstation mirror.
   - In `demo/record_gcp_demo.py`, always pass `--no-proxy-server` to headless Chrome and `trust_env=False` to `httpx.AsyncClient(trust_env=False)` when polling `http://127.0.0.1:<port>/json`, otherwise proxy environment variables intercept localhost DevTools connections.
7. **Cloud Run Warm Instances & Terraform Auth:**
   - Both `docubridge-showcase-bridge` and `docubridge-showcase-bridge-rfp-board` are configured with `min_instances = 1` (`terraform/modules/runtime/main.tf`) to prevent cold starts during live demos.
   - When running `terraform` outside GCE metadata environments, either export `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"` or update Cloud Run services via `gcloud run services update`.
