#!/usr/bin/env bash
# Time-warp host (presenter clock, plain REST — deliberately no SSE) — port 8004
# (docs/local-dev.md). Local-only: there is no deployed time-warp surface.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

cd "${REPO_ROOT}"
exec uv run uvicorn --factory bridge.timewarp.server:build_timewarp_app \
  --host "${TIMEWARP_HOST:-127.0.0.1}" --port "${TIMEWARP_PORT:-8004}"
