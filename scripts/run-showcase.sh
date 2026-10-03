#!/usr/bin/env bash
# The showcase runner: ONE process, one Agent Card, one dashboard, every demo loaded
# (docs/local-dev.md#showcase-runner). Port 8011:
#   /            the A2A edge (card at /.well-known/agent-card.json, JSON-RPC at /)
#   /ops         the Ops dashboard (Address exchanges, the Benefits program, the RFP)
#   /portal      the Provider portal
# All three surfaces share one PortalStore, one RfpBoard and one ProgramBoard
# (bridge.demos.showcase). Unless SHOWCASE_APPS=0, the ops-dashboard (5175) and
# provider-portal (5173) Vite apps start too, pointed at the mounts. Ctrl-C tears the
# group down.
#
# An ALTERNATIVE to run-all.sh and the RFP demo host, not a companion: the Vite ports
# collide with run-all.sh's.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

HOST="${SHOWCASE_HOST:-127.0.0.1}"
PORT="${SHOWCASE_PORT:-8011}"
# The card url follows the served origin (build_a2a_app honors BRIDGE_PUBLIC_URL).
export BRIDGE_PUBLIC_URL="${BRIDGE_PUBLIC_URL:-http://${HOST}:${PORT}/}"

pids=()
cleanup() {
  echo "Shutting down the showcase..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

echo "Starting the showcase core on :${PORT}..."
( cd "${REPO_ROOT}" && exec uv run uvicorn --factory bridge.demos.showcase:build_showcase_app \
    --host "${HOST}" --port "${PORT}" ) &
pids+=("$!")

if [[ "${SHOWCASE_APPS:-1}" != "0" ]]; then
  # Each Vite port is pinned (--strictPort): the hosts' CORS allowlist is 5173-5177.
  echo "Starting ops-dashboard app on :5175..."
  ( cd "${REPO_ROOT}" && VITE_DASHBOARD_URL="http://localhost:${PORT}/ops" \
      pnpm --filter ops-dashboard dev -- --port 5175 --strictPort ) &
  pids+=("$!")
  echo "Starting provider-portal app on :5173..."
  ( cd "${REPO_ROOT}" && VITE_PORTAL_URL="http://localhost:${PORT}/portal" \
      pnpm --filter provider-portal dev -- --port 5173 --strictPort ) &
  pids+=("$!")
fi

wait
