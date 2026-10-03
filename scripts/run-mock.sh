#!/usr/bin/env bash
# Mock Bridge edge (scripted executor, same wire) — port 8001 (docs/local-dev.md).
#
# MUTUALLY EXCLUSIVE with run-portal.sh, which also defaults to 8001. The mock is the
# consumer-side contract double (a permanent one, not a throwaway): run it INSTEAD of
# run-core.sh when you want the scripted branches, and point run-console.sh at it with
# BRIDGE_CARD_URL. `run-all.sh` starts the real edge, not the mock.
#
# Uses serve_mock rather than `uvicorn --factory` because the mock's build takes the
# advertised card url as an argument (it has no BRIDGE_PUBLIC_URL fallback), and the
# card must advertise 8001, not the 8000 default.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

HOST="${MOCK_HOST:-127.0.0.1}"
PORT="${MOCK_PORT:-8001}"

cd "${REPO_ROOT}"
exec uv run python -c \
  "from bridge.edge.mock import serve_mock; serve_mock(host='${HOST}', port=${PORT})"
