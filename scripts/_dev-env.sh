#!/usr/bin/env bash
# Shared local-dev env for the scripts/run-*.sh surface hosts (docs/local-dev.md).
# Sourced, never executed. Every value is an override-able default: export the var
# before calling a run script and that wins (`FOO="${FOO:-default}"`).
#
# Local means local: the seams resolve to their in-process/in-memory adapters and the
# extraction engine is the deterministic fixture engine over the eval corpus — no GCP
# credentials, no Gemini calls, no billed anything. The canonical index of every
# variable the code reads is `.env.example`.

# Seams: every storage/infra seam on its local adapter (per-seam BRIDGE_SEAM_* overrides
# still work — that is how you move ONE boundary onto a managed service at a time).
# Extraction is its own axis (BRIDGE_EXTRACTION_ENGINE, fixture by default).
export BRIDGE_SEAM_MODE="${BRIDGE_SEAM_MODE:-local}"

# Skill registry root — the repo's own skills/ dir unless pointed elsewhere.
export BRIDGE_SKILLS_DIR="${BRIDGE_SKILLS_DIR:-${REPO_ROOT}/skills}"

# A local run must never pick up deployed-path resources by accident: the gcp seam
# adapters refuse-loud rather than silently falling back, and these envs are what they
# key on. Unset here so a stale shell export cannot cross the local/deployed line.
unset DATABASE_URL BRIDGE_SESSION_DB_URL BRIDGE_TASK_DB_URL 2>/dev/null || true
unset BRIDGE_ARTIFACT_BUCKET BRIDGE_SKILLS_BUCKET BRIDGE_TASKS_QUEUE 2>/dev/null || true

# uvicorn's default log line is enough; keep reload OFF (the ASGI apps hold in-memory
# state — a reload silently drops every live session).
export UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
