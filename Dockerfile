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
# so it MUST ship in the image and the app MUST run from the source tree (docubridge
# is a uv "virtual" project — deps are synced, the source is not installed as a
# wheel, so __file__ stays in the tree and the corpus resolves).
FROM python:3.12-slim-bookworm

# uv provides the reproducible dependency sync (same lockfile as local/CI).
COPY --from=ghcr.io/astral-sh/uv:0.6-python3.12-bookworm-slim /uv /uvx /bin/

# Run as a non-root system user (UID 10001) per Google Cloud container security standards.
RUN groupadd --system --gid 10001 appuser && \
    useradd --system --uid 10001 --gid appuser --create-home --home-dir /home/appuser appuser

WORKDIR /app

# Sync ONLY the dependency closure first (better layer caching): the manifest +
# lockfile change rarely relative to source. --extra gcp pulls the deployed-path
# deps (asyncpg / google-cloud-storage / google-cloud-tasks) the Gcp seam adapters
# import lazily. --no-install-project: docubridge is a virtual project (no build
# backend); we run from the copied source tree, not an installed wheel.
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --frozen --no-dev --extra gcp --no-install-project && \
    chown -R appuser:appuser /app

# The Bridge core + its runtime fixtures. contract/ (shared wire types), bridge/
# (the core), skills/ (runtime-loaded Agent Skills), wiki/ (the eval corpus oracle,
# read at runtime). NO agents/ (isolation invariant).
COPY --chown=appuser:appuser contract/ ./contract/
COPY --chown=appuser:appuser bridge/ ./bridge/
COPY --chown=appuser:appuser skills/ ./skills/
COPY --chown=appuser:appuser wiki/ ./wiki/
COPY --chown=appuser:appuser demo/ ./demo/

# Install the project in editable mode without re-resolving dependencies so the
# `bridge-serve` console script is registered in `/app/.venv/bin/bridge-serve`
# while `__file__` remains rooted in `/app/bridge/` for runtime `wiki/evals/` lookup.
RUN uv pip install --no-deps -e . && \
    chown -R appuser:appuser /app

# Put the synced venv first on PATH so `python` and `bridge-serve` resolve directly.
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080

USER appuser
EXPOSE 8080

# `bridge-serve` invokes `bridge.edge.server:serve_container`, binding 0.0.0.0:$PORT
# and advertising BRIDGE_PUBLIC_URL on the served Agent Card (P7-5).
CMD ["bridge-serve"]
