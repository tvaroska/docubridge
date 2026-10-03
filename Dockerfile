# Bridge runtime container image (P7-9) — the Cloud Run compute host for the
# deployed A2A edge. Serves the canonical Bridge A2A server bound to 0.0.0.0:$PORT
# (Cloud Run contract) via serve_container(); ingress on the deployed path is
# Gateway-locked (INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER), so 0.0.0.0 here does not
# reopen S0-sec-3.
#
# Isolation invariant: this image copies contract/ bridge/ skills/ wiki/ ONLY —
# NEVER agents/ (the Bridge core never depends on consumers; the isolation-guard
# test enforces it). The eval corpus (wiki/evals/) is a RUNTIME fixture read via
# __file__-relative paths (bridge/extraction/fixture.py, bridge/edge/artifacts.py),
# so it MUST ship in the image and the app MUST run from the source tree (docbride
# is a uv "virtual" project — deps are synced, the source is not installed as a
# wheel, so __file__ stays in the tree and the corpus resolves).
FROM python:3.12-slim

# uv provides the reproducible dependency sync (same lockfile as local/CI).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Sync ONLY the dependency closure first (better layer caching): the manifest +
# lockfile change rarely relative to source. --extra gcp pulls the deployed-path
# deps (asyncpg / google-cloud-storage / google-cloud-tasks) the Gcp seam adapters
# import lazily. --no-install-project: docbride is a virtual project (no build
# backend); we run from the copied source tree, not an installed wheel.
COPY pyproject.toml uv.lock ./
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --frozen --no-dev --extra gcp --no-install-project

# The Bridge core + its runtime fixtures. contract/ (shared wire types), bridge/
# (the core), skills/ (runtime-loaded Agent Skills), wiki/ (the eval corpus oracle,
# read at runtime). NO agents/ (isolation invariant).
COPY contract/ ./contract/
COPY bridge/ ./bridge/
COPY skills/ ./skills/
COPY wiki/ ./wiki/

# Put the synced venv first on PATH so `python` is the project interpreter (no `uv
# run` re-resolution at container start; runtime needs no network).
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080

EXPOSE 8080

# serve_container() reads $PORT (default 8080), binds 0.0.0.0, and advertises
# BRIDGE_PUBLIC_URL on the served card (adk >=2.7 same-origin, P7-5). The packaged
# equivalent is the `bridge-serve` console script (pyproject [project.scripts]); we
# invoke the function directly because the source runs un-installed (see header).
CMD ["python", "-c", "from bridge.edge.server import serve_container; serve_container()"]
