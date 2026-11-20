#!/usr/bin/env bash
# Canonical verification gate for docubridge.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

export PATH="${HOME}/.local/bin:${PATH}"
export PYTHONDONTWRITEBYTECODE=1

uv lock --check --offline --no-config
if [[ -f "specs/ast.rules.yaml" ]] && command -v ast-grep >/dev/null 2>&1; then
  ast-grep scan --rule specs/ast.rules.yaml
fi
uv run --no-sync ruff check --no-cache .
uv run --no-sync ruff format --check --no-cache .
uv run --no-sync mypy --strict --cache-dir=/tmp/top3-mypy-docubridge contract bridge
exec uv run --no-sync pytest -p no:cacheprovider "$@"
