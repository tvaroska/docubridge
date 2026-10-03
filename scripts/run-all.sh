#!/usr/bin/env bash
# Bring up the whole local demo stack: the five Python hosts (8000-8004) plus the four
# Vite apps (5173-5176), each app pointed at its host. Ctrl-C tears the group down.
# Port map + caveats: docs/local-dev.md. Env index: .env.example.
# Each host here holds its own store; the one-process, one-card, one-dashboard showcase
# is scripts/run-showcase.sh.
#
# Starts the REAL edge on 8000, not the mock — the mock (run-mock.sh) wants 8001, which
# the portal holds here. Run the mock stack by hand when you want the scripted branches.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pids=()
cleanup() {
  echo "Shutting down the local stack..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

for svc in core portal console ops timewarp; do
  echo "Starting ${svc} host..."
  bash "${REPO_ROOT}/scripts/run-${svc}.sh" &
  pids+=("$!")
done

# The apps read their host URL from VITE_* at dev-server start; each Vite port is
# pinned (--strictPort) because the hosts' CORS allowlist is the 5173-5177 range.
export VITE_PORTAL_URL="${VITE_PORTAL_URL:-http://localhost:8001}"
export VITE_CONSOLE_URL="${VITE_CONSOLE_URL:-http://localhost:8002}"
export VITE_DASHBOARD_URL="${VITE_DASHBOARD_URL:-http://localhost:8003}"
export VITE_TIMEWARP_URL="${VITE_TIMEWARP_URL:-http://localhost:8004}"
# The presenter stage embeds the other three apps (Split-Screen mode).
export VITE_PORTAL_APP_URL="${VITE_PORTAL_APP_URL:-http://localhost:5173}"
export VITE_CONSOLE_APP_URL="${VITE_CONSOLE_APP_URL:-http://localhost:5174}"
export VITE_DASHBOARD_APP_URL="${VITE_DASHBOARD_APP_URL:-http://localhost:5175}"

start_app() {  # start_app <pnpm-filter> <vite-port>
  echo "Starting $1 app on :$2..."
  ( cd "${REPO_ROOT}" && pnpm --filter "$1" dev -- --port "$2" --strictPort ) &
  pids+=("$!")
}

start_app provider-portal 5173
start_app agent-console 5174
start_app ops-dashboard 5175
start_app timewarp 5176

wait
