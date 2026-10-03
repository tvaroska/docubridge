#!/usr/bin/env bash
# Provider Portal host (A2UI Path-B, external zone) — port 8001 (docs/local-dev.md).
# Pairs with the provider-portal Vite app; see run-all.sh for the whole stack.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/_dev-env.sh"

cd "${REPO_ROOT}"
exec uv run uvicorn --factory bridge.portal.server:build_portal_app \
  --host "${PORTAL_HOST:-127.0.0.1}" --port "${PORTAL_PORT:-8001}"
