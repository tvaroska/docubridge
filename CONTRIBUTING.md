# Contributing to A2A Document Bridge (`docubridge`)

Thank you for contributing to **A2A Document Bridge**. This project is both a reference architecture for the **Gemini platform** on Google Cloud and a reusable A2A document mediation core.

## Core Architectural Invariants

Every pull request must preserve the following invariants (enforced automatically by `tests/test_isolation_guard.py` and `tests/test_service_wiring_guard.py`):

1. **Strict Package Firewall (`bridge/` ↔ `contract/` ↔ `agents/`):**
   - `bridge/` must **never** import `agents/`.
   - `agents/` must **never** import `bridge/`.
   - Shared wire models live exclusively in `contract/`.
2. **Zero Hardcoded Skill Names in Core (`test_core_names_no_skill`):**
   - Modules inside `bridge/` (outside `bridge/demos/` and explicitly allowlisted calibration fixtures) must never contain a string literal matching an installed folder in `skills/`. Domain behavior is loaded dynamically from `SKILL.md` manifests and `assets/policy.yaml`.
3. **No Unconditional In-Memory Stores in Production Factories:**
   - Every stateful store factory in `bridge/` must resolve its durable GCP/SQL adapter first and refuse silent in-memory fallback when `BRIDGE_PORT_MODE=gcp`.
4. **Protected Specification (`spec/`):**
   - Do not modify files under `spec/` during feature implementation; propose specification changes in `DECISIONS.md`.
5. **Downstream showcases consume over A2A, never by import (`examples/`):**
   - Anything under `examples/` imports only `a2a`, `contract/` and the stdlib — never `bridge/` or `agents/` (also enforced by `tests/test_isolation_guard.py`). The contract they rely on is [`docs/consume-over-a2a.md`](docs/consume-over-a2a.md).

## Local Verification Ladder

Before submitting changes or running `gcloud builds submit --config cloudbuild.yaml`, run:

```bash
# 1. Lint & formatting check (must pass with 0 findings)
uv run ruff check .
uv run ruff format --check .

# 2. Full unit, port, conformance & enterprise architecture test suite
uv run pytest

# 3. Frontend shared contract unit tests
pnpm --filter @docubridge/shared test

# 4. Credential-free Terraform plan-time assertions
terraform -chdir=terraform init -backend=false && terraform -chdir=terraform test
```
