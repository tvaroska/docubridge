#!/usr/bin/env bash
# Bridge core inbound A2A edge — port 8000 (docs/local-dev.md port map).
# The card `url` follows the served origin via BRIDGE_PUBLIC_URL, which
# `build_a2a_app` honors when no explicit url is passed (the P7-5 same-origin rule).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

HOST="${CORE_HOST:-127.0.0.1}"
PORT="${CORE_PORT:-8000}"
export BRIDGE_PUBLIC_URL="${BRIDGE_PUBLIC_URL:-http://${HOST}:${PORT}/}"

cd "${REPO_ROOT}"
exec uv run uvicorn --factory bridge.edge.server:build_a2a_app \
  --host "${HOST}" --port "${PORT}"
