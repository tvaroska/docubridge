"""Shared constants, DSN validators, and Agent Engine resource resolvers for GCP port adapters."""

from __future__ import annotations

import logging
import os

_LOG = logging.getLogger("bridge.ports.gcp")

# The env the runtime injects with the Skill Registry GCS bucket name (the P7-3
# gcs module -> runtime plain env, NOT a secret_key_ref: a bucket name is not a
# secret). The deployed registry reaches the app ONLY via this env.
SKILLS_BUCKET_ENV = "BRIDGE_SKILLS_BUCKET"

# The secret env the runtime injects (the P7-2 secrets module -> DATABASE_URL
# secret_key_ref). The deployed DSN reaches the app ONLY via this env; the
# Bridge->Cloud SQL connection is private-IP over the VPC, never an HTTP hop
# (DECISIONS.md 2026-08-24).
DATABASE_URL_ENV = "DATABASE_URL"

# Gemini platform Agent Engine resource ID or full resource name
# (projects/<project>/locations/<location>/reasoningEngines/<id>) for managed
# Agent Sessions (VertexAiSessionService), Memory Bank (VertexAiMemoryBankService),
# and remote ReasoningEngine execution.
BRIDGE_AGENT_ENGINE_ID_ENV = "BRIDGE_AGENT_ENGINE_ID"
BRIDGE_GCP_PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
BRIDGE_GCP_LOCATION_ENV = "GOOGLE_CLOUD_LOCATION"

# Cloud Tasks queue resource path, OIDC callback URL, and runtime SA email (P7-4).
BRIDGE_TASKS_QUEUE_ENV = "BRIDGE_TASKS_QUEUE"
BRIDGE_FOLLOWUP_CALLBACK_URL_ENV = "BRIDGE_FOLLOWUP_CALLBACK_URL"
BRIDGE_RUNTIME_SA_EMAIL_ENV = "BRIDGE_RUNTIME_SA_EMAIL"

# Gateway-authorized host for outbound A2A (P7-5).
BRIDGE_GATEWAY_HOST_ENV = "BRIDGE_GATEWAY_HOST"

# Extraction engine selector (fixture|gemini|docai, P7-6).
BRIDGE_EXTRACTION_ENGINE_ENV = "BRIDGE_EXTRACTION_ENGINE"

# GCS mailbox landing bucket name (S0-arch-13).
EMAIL_BUCKET_ENV = "BRIDGE_EMAIL_BUCKET"

# The one async Postgres driver we target (asyncpg — the gcp extra).
_ASYNC_PG_PREFIX = "postgresql+asyncpg://"


def parse_agent_engine_resource(
    raw: str | None = None,
    *,
    project: str | None = None,
    location: str | None = None,
) -> tuple[str | None, str | None, str]:
    """Resolve `(project, location, agent_engine_id)` from an Agent Engine ID or full resource path.

    Accepts either:
    - a full resource path `projects/<project>/locations/<location>/reasoningEngines/<id>`
      (explicit `project`/`location` args still win if supplied), or
    - a short engine ID `<id>`, resolving `project` and `location` from args or
      `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` env vars.

    Raises `RuntimeError` when no Agent Engine ID is provided (neither arg nor
    `BRIDGE_AGENT_ENGINE_ID` env), and `ValueError` if a resource path is malformed.
    """
    import re

    val = (raw or os.environ.get(BRIDGE_AGENT_ENGINE_ID_ENV, "")).strip()
    if not val:
        raise RuntimeError(
            f"GCP Agent Engine port requires an Agent Engine ID "
            f"({BRIDGE_AGENT_ENGINE_ID_ENV} env or agent_engine_id=); "
            "refusing an in-memory fallback (the session-affinity trap)."
        )
    arg_proj = (project or "").strip() or None
    arg_loc = (location or "").strip() or None
    env_proj = os.environ.get(BRIDGE_GCP_PROJECT_ENV, "").strip() or None
    env_loc = os.environ.get(BRIDGE_GCP_LOCATION_ENV, "").strip() or None
    if "/" in val:
        m = re.fullmatch(
            r"projects/([^/]+)/locations/([^/]+)/reasoningEngines/([^/]+)", val
        )
        if not m:
            raise ValueError(
                f"invalid Agent Engine resource name {val!r}: expected "
                "'projects/<project>/locations/<location>/reasoningEngines/<id>' or short '<id>'"
            )
        p_from_path, l_from_path, engine_id = m.groups()
        resolved_proj = arg_proj or p_from_path or env_proj
        resolved_loc = arg_loc or l_from_path or env_loc
    else:
        engine_id = val
        resolved_proj = arg_proj or env_proj
        resolved_loc = arg_loc or env_loc
    return resolved_proj, resolved_loc, engine_id


def require_async_pg_driver(dsn: str) -> str:
    """Reject a sync/non-async Postgres DSN early with a clear message.

    `DatabaseSessionService`/`DatabaseTaskStore` run on SQLAlchemy's asyncio
    extension, which needs an async driver. The local `require_async_driver` only
    rejects sync *sqlite*; a bare `postgresql://…` (psycopg2) would slip through
    it, so the Cloud SQL adapter additionally requires `postgresql+asyncpg://…`
    (mirrors `bridge/ports/local` `require_async_driver` wording).
    """
    if not dsn.startswith(_ASYNC_PG_PREFIX):
        raise ValueError(
            f"Cloud SQL port needs an async Postgres driver; "
            f"use 'postgresql+asyncpg://…' not {dsn!r}"
        )
    return dsn


def _resolve_dsn(db_url: str | None = None, env: str = DATABASE_URL_ENV) -> str:
    """Resolve the Cloud SQL DSN: `db_url` arg wins, else the secret env.

    Raises `RuntimeError` when neither is set — the gcp adapter REFUSES an
    in-memory fallback (that is the session-affinity trap, a correctness invariant
    not a style choice; `design/ports.md`, `CRITICAL.md`).
    """
    dsn = (db_url or os.environ.get(env, "")).strip()
    if not dsn:
        raise RuntimeError(
            "GCP port requires a Cloud SQL DSN (DATABASE_URL secret env or db_url=); "
            "refusing an in-memory fallback (the session-affinity trap)."
        )
    return require_async_pg_driver(dsn)


def _require_async_store_driver(dsn: str) -> str:
    """Validate that a SQL store DSN uses an async driver (asyncpg or aiosqlite)."""
    if dsn.startswith("sqlite:///") or dsn == "sqlite://":
        raise ValueError(
            f"SQL store port requires an async driver; use 'sqlite+aiosqlite:///…' not {dsn!r}"
        )
    if (
        dsn.startswith("postgresql://") or dsn.startswith("postgres://")
    ) and not dsn.startswith(_ASYNC_PG_PREFIX):
        raise ValueError(
            f"Cloud SQL port requires an async Postgres driver; use 'postgresql+asyncpg://…' not {dsn!r}"
        )
    return dsn
