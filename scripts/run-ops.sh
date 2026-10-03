#!/usr/bin/env bash
# Ops Dashboard host (Bridge-side read model, SSE) — port 8003 (docs/local-dev.md).
#
# NOTE the store caveat: each host built by its own factory gets its OWN in-memory
# portal store, so a session started in the portal is NOT visible to a separately
# launched dashboard. One shared store across portal + dashboard + time-warp is the
# wired-deployment story (`store=` injection); locally these are independent surfaces.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

cd "${REPO_ROOT}"
exec uv run uvicorn --factory bridge.dashboard.server:build_dashboard_app \
  --host "${OPS_HOST:-127.0.0.1}" --port "${OPS_PORT:-8003}"
