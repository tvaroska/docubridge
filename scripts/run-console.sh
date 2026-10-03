#!/usr/bin/env bash
# Agent Console host (SSE, internal zone) — port 8002 (docs/local-dev.md).
#
# The console is a service-agent consumer: it drives a Bridge over A2A, so it needs a
# card URL. Default here is the REAL edge (run-core.sh on 8000); export
# BRIDGE_CARD_URL=http://127.0.0.1:8001/.well-known/agent-card.json to drive the mock
# instead (run-mock.sh). The mock->real swap is a URL change, nothing more.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

export BRIDGE_CARD_URL="${BRIDGE_CARD_URL:-http://127.0.0.1:8000/.well-known/agent-card.json}"

cd "${REPO_ROOT}"
exec uv run uvicorn --factory agents.console.server:build_console_app \
  --host "${CONSOLE_HOST:-127.0.0.1}" --port "${CONSOLE_PORT:-8002}"
