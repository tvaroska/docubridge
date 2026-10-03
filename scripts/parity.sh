#!/usr/bin/env bash
# P7-9 parity — run the P7 exit gate against a provisioned/deployed project.
#
# Exercises the four exit criteria's live halves (deferred to P7-9 by P7-2..P7-6):
#   1. the whole seam suite with BRIDGE_TEST_GCP=1 (the gcp params + live roundtrips);
#   2. the deployed-path Address golden runs (tests/test_deployed_parity.py) when
#      BRIDGE_DEPLOYED_EDGE_URL is exported;
#   3. plan-time terraform (already green credential-free — re-run here for completeness);
#   4. the cross-replica read-after-write against Cloud SQL (task_store [gcp] param).
#
# Reads its config from the environment (exported by scripts/deploy.sh's printed block).
# None of these values is a secret except the DSN, which is passed through the env only —
# never written to disk (CRITICAL.md "No secrets"). Requires live GCP auth; this is the
# gated operator step of P7-9.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${BRIDGE_TEST_GCP:?export BRIDGE_TEST_GCP=1 (un-skips the gcp seam params)}"
: "${BRIDGE_GCP_TEST_DSN:?export BRIDGE_GCP_TEST_DSN=postgresql+asyncpg://… (Cloud SQL DSN)}"

echo "==> Plan-time terraform (credential-free exit criterion 3)"
( cd "${REPO_ROOT}/terraform" && env -u GOOGLE_APPLICATION_CREDENTIALS terraform test )

echo "==> Whole seam suite + live roundtrips + deployed-path golden runs"
# The full suite: BRIDGE_TEST_GCP=1 un-skips the gcp seam params (each also needs its own
# resource env — DSN/bucket/queue — exported above/by the deploy block); the cross-replica
# read-after-write [gcp] param (exit criterion 4) runs here; and when
# BRIDGE_DEPLOYED_EDGE_URL is set the deployed Address golden runs (exit criterion 2) run.
( cd "${REPO_ROOT}" && uv run pytest -q )

echo "==> Parity gate complete."
