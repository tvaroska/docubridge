"""GCP seam adapters — Cloud SQL shared stores (P7-2).

``sessions`` / ``task_store`` / ``exchange_store`` back onto Cloud SQL (Postgres)
so cross-replica read-after-write holds (design/seams.md, "the session-affinity
trap"). These are the SAME classes the local adapter builds — the ADK/a2a
``Database*`` stores and the Bridge's own ``bridge.exchange_store.SqlExchangeStore``
(the materialized-Exchange records, S0-arch-14) — only over a Postgres DSN instead of
sqlite — mock→real and local→gcp are no-ops for the agent (design/seams.md "Parity
invariants"). The GCP novelty is exactly three things:

1. **DSN source** — the ``db_url`` arg wins, else the ``DATABASE_URL`` secret env
   the runtime injects (P7-2 secrets module); NEVER an in-memory fallback (an
   in-memory shared store on multi-replica IS the session-affinity trap this task
   exists to prevent — a peer reference measured 86.8% poll-404s, DECISIONS.md
   2026-08-24).
2. **Driver** — an async **Postgres** DSN (``postgresql+asyncpg://…``) is required;
   a bare sync ``postgresql://`` fails loud (``require_async_pg_driver``).
3. **Terminal-write retry** — ``GcpTaskStore.build_task_store`` wraps the underlying
   ``DatabaseTaskStore`` in ``_TerminalRetryTaskStore`` so a lost terminal write
   (which would hang the poller to its deadline) retries with loud logging.

Heavy deps (a2a-sdk / google-adk / sqlalchemy / asyncpg) are imported lazily inside
methods, mirroring ``bridge/seams/local/__init__.py`` — importing this package pulls
none of them (the default non-gcp test suite needs no asyncpg installed). asyncpg is
the optional ``gcp`` extra, installed by the P7-9 provisioned run.
"""

import logging
import os

from ..protocols import (
    A2AClientSeam,
    EmailIngressSeam,
    ExchangeStoreSeam,
    ExtractionSeam,
    MemoryBankSeam,
    SchedulerSeam,
    SeamABC,
    SessionsSeam,
    SkillRegistrySeam,
    TaskStoreSeam,
    validate_attachment_slot,
    validate_landing_key,
)
from ..seam import Seam

_LOG = logging.getLogger("bridge.seams.gcp")

# The env the runtime injects with the Skill Registry GCS bucket name (the P7-3
# gcs module → runtime plain env, NOT a secret_key_ref: a bucket name is not a
# secret). The deployed registry reaches the app ONLY via this env.
SKILLS_BUCKET_ENV = "BRIDGE_SKILLS_BUCKET"

# The secret env the runtime injects (the P7-2 secrets module → DATABASE_URL
# secret_key_ref). The deployed DSN reaches the app ONLY via this env; the
# Bridge→Cloud SQL connection is private-IP over the VPC, never an HTTP hop
# (DECISIONS.md 2026-08-24).
DATABASE_URL_ENV = "DATABASE_URL"

# Vertex AI Agent Engine resource ID or full resource name
# (projects/<project>/locations/<location>/reasoningEngines/<id>) for managed
# Agent Sessions (VertexAiSessionService), Memory Bank (VertexAiMemoryBankService),
# and remote ReasoningEngine execution.
BRIDGE_AGENT_ENGINE_ID_ENV = "BRIDGE_AGENT_ENGINE_ID"
BRIDGE_GCP_PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
BRIDGE_GCP_LOCATION_ENV = "GOOGLE_CLOUD_LOCATION"

# The one async Postgres driver we target (asyncpg — the gcp extra). A bare
# sync ``postgresql://`` (psycopg2) slips past the local ``require_async_driver``
# (which only rejects sync sqlite), so the gcp adapter enforces async Postgres.
_ASYNC_PG_PREFIX = "postgresql+asyncpg://"


def parse_agent_engine_resource(
    raw: str | None = None,
    *,
    project: str | None = None,
    location: str | None = None,
) -> tuple[str | None, str | None, str]:
    """Resolve ``(project, location, agent_engine_id)`` from an Agent Engine ID or full resource path.

    Accepts either:
    - a full resource path ``projects/<project>/locations/<location>/reasoningEngines/<id>``
      (explicit ``project``/``location`` args still win if supplied), or
    - a short engine ID ``<id>``, resolving ``project`` and ``location`` from args or
      ``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION`` env vars.

    Raises ``RuntimeError`` when no Agent Engine ID is provided (neither arg nor
    ``BRIDGE_AGENT_ENGINE_ID`` env), and ``ValueError`` if a resource path is malformed.
    """
    import re

    val = (raw or os.environ.get(BRIDGE_AGENT_ENGINE_ID_ENV, "")).strip()
    if not val:
        raise RuntimeError(
            f"GCP Agent Engine seam requires an Agent Engine ID "
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

    ``DatabaseSessionService``/``DatabaseTaskStore`` run on SQLAlchemy's asyncio
    extension, which needs an async driver. The local ``require_async_driver`` only
    rejects sync *sqlite*; a bare ``postgresql://…`` (psycopg2) would slip through
    it, so the Cloud SQL adapter additionally requires ``postgresql+asyncpg://…``
    (mirrors ``bridge/seams/local`` ``require_async_driver`` wording).
    """
    if not dsn.startswith(_ASYNC_PG_PREFIX):
        raise ValueError(
            f"Cloud SQL seam needs an async Postgres driver; "
            f"use 'postgresql+asyncpg://…' not {dsn!r}"
        )
    return dsn


def _resolve_dsn(db_url: str | None = None, env: str = DATABASE_URL_ENV) -> str:
    """Resolve the Cloud SQL DSN: ``db_url`` arg wins, else the secret env.

    Raises ``RuntimeError`` when neither is set — the gcp adapter REFUSES an
    in-memory fallback (that is the session-affinity trap, a correctness invariant
    not a style choice; design/seams.md, CRITICAL.md).
    """
    dsn = (db_url or os.environ.get(env, "")).strip()
    if not dsn:
        raise RuntimeError(
            "GCP seam requires a Cloud SQL DSN (DATABASE_URL secret env or db_url=); "
            "refusing an in-memory fallback (the session-affinity trap)."
        )
    return require_async_pg_driver(dsn)


class GcpSessions(SessionsSeam):
    """GCP sessions adapter (P7-2 / Agent Engine Option A).

    Supports two managed backends behind the same ``SessionsSeam``:
    1. **Vertex AI Agent Engine** (``VertexAiSessionService``) when ``agent_engine_id``
       or ``BRIDGE_AGENT_ENGINE_ID`` env is set (and no explicit ``db_url`` override
       is passed).
    2. **Cloud SQL Postgres** (``DatabaseSessionService``) over the ``DATABASE_URL``
       secret env or ``db_url`` arg.

    Refuses an in-memory fallback when neither is configured (the session-affinity trap).
    """

    def __init__(
        self,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self._agent_engine_id = agent_engine_id
        self._project = project
        self._location = location

    def build_session_service(
        self,
        db_url: str | None = None,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
        vertex_factory=None,
    ):
        raw_engine_id = (
            agent_engine_id
            or self._agent_engine_id
            or os.environ.get(BRIDGE_AGENT_ENGINE_ID_ENV, "")
        ).strip()
        if raw_engine_id and db_url is None:
            proj, loc, eid = parse_agent_engine_resource(
                raw_engine_id,
                project=project or self._project,
                location=location or self._location,
            )
            if vertex_factory is not None:
                return vertex_factory(
                    project=proj, location=loc, agent_engine_id=eid
                )
            from google.adk.sessions import VertexAiSessionService

            return VertexAiSessionService(
                project=proj, location=loc, agent_engine_id=eid
            )

        from google.adk.sessions import DatabaseSessionService

        return DatabaseSessionService(db_url=_resolve_dsn(db_url))


class GcpMemoryBank(MemoryBankSeam):
    """GCP memory_bank adapter — Vertex AI Agent Engine ``VertexAiMemoryBankService``.

    Resolves ``(project, location, agent_engine_id)`` from args or
    ``BRIDGE_AGENT_ENGINE_ID`` / ``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION``
    envs. Refuses a silent in-memory fallback when ``BRIDGE_AGENT_ENGINE_ID`` is unset.
    ``vertex_factory`` is injectable for credential-free unit tests without
    ``google-cloud-aiplatform`` installed.
    """

    def __init__(
        self,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self._agent_engine_id = agent_engine_id
        self._project = project
        self._location = location

    def build_memory_service(
        self,
        db_path: str | None = None,
        *,
        agent_engine_id: str | None = None,
        project: str | None = None,
        location: str | None = None,
        vertex_factory=None,
    ):
        del db_path  # local-only SQLite path parameter; unused on Vertex Memory Bank
        proj, loc, eid = parse_agent_engine_resource(
            agent_engine_id or self._agent_engine_id,
            project=project or self._project,
            location=location or self._location,
        )
        if vertex_factory is not None:
            return vertex_factory(project=proj, location=loc, agent_engine_id=eid)
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=proj, location=loc, agent_engine_id=eid
        )


class GcpTaskStore(TaskStoreSeam):
    """GCP task_store adapter (P7-2) — Cloud SQL ``DatabaseTaskStore`` + retry.

    Builds the same a2a-sdk ``DatabaseTaskStore`` the local adapter builds, over an
    ``AsyncEngine`` on the Cloud SQL DSN, wrapped in ``_TerminalRetryTaskStore`` so
    terminal-state writes retry with loud logging (design/seams.md).
    """

    def build_task_store(self, db_url: str | None = None):
        from a2a.server.tasks import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import create_async_engine

        inner = DatabaseTaskStore(engine=create_async_engine(_resolve_dsn(db_url)))
        return _TerminalRetryTaskStore(inner)

    def build_push_config_store(self, db_url: str | None = None):
        """Build the Cloud SQL ``DatabasePushNotificationConfigStore``.

        Mirrors the local adapter (rides the same DSN); the terminal-write retry
        concern is the task store, so this is not wrapped.
        """
        from a2a.server.tasks import DatabasePushNotificationConfigStore
        from sqlalchemy.ext.asyncio import create_async_engine

        return DatabasePushNotificationConfigStore(
            engine=create_async_engine(_resolve_dsn(db_url)), create_table=True
        )


class GcpExchangeStore(ExchangeStoreSeam):
    """GCP exchange_store adapter (S0-arch-14) — Cloud SQL ``SqlExchangeStore``.

    The same ``SqlExchangeStore`` the local adapter builds, over the Cloud SQL
    Postgres DSN (``DATABASE_URL`` — the same database the sessions / task_store
    seams use, its own ``bridge_exchange_records`` table). Refuses an in-memory
    fallback (``_resolve_dsn``): per-replica records would split an RFP across
    Cloud Run instances. The compare-and-set write keeps replicas honest. Nothing
    (engine, asyncpg) is opened or imported until the first operation.
    """

    def build_exchange_store(self, db_url: str | None = None):
        from bridge.exchange_store import SqlExchangeStore

        return SqlExchangeStore(_resolve_dsn(db_url))


def _terminal_retry_task_store():
    """Lazily build the ``_TerminalRetryTaskStore`` class over the a2a-sdk
    ``TaskStore`` base.

    ``TaskStore`` is imported at call time (not module import) so importing this
    package stays dep-free (mirrors the local pkg's lazy imports). The class is
    defined once and cached on first use.
    """
    from a2a.server.agent_execution.active_task import TERMINAL_TASK_STATES
    from a2a.server.tasks import TaskStore

    class _TerminalRetryStore(TaskStore):
        """A ``TaskStore`` wrapper: terminal-state saves retry with loud logging.

        "A lost terminal write hangs the poller to its deadline, so it fails far
        louder than a dropped intermediate ``working`` write" (design/seams.md).
        Only terminal saves (completed/failed/canceled/rejected — the a2a-sdk
        ``TERMINAL_TASK_STATES`` set, never hardcoded) retry; non-terminal saves
        pass straight through (a single attempt). ``get``/``delete``/``list``
        delegate unchanged. Unit-testable without GCP: inject a fake inner store.
        """

        def __init__(self, inner, *, attempts: int = 3, log=_LOG) -> None:
            self._inner = inner
            self._attempts = attempts
            self._log = log

        async def save(self, task, context) -> None:
            terminal = task.status.state in TERMINAL_TASK_STATES
            attempts = self._attempts if terminal else 1
            last: Exception | None = None
            for i in range(attempts):
                try:
                    return await self._inner.save(task, context)
                except Exception as e:  # retry-and-reraise the terminal write
                    last = e
                    if terminal:
                        self._log.warning(
                            "terminal task %s save failed (attempt %d/%d): %r",
                            task.id,
                            i + 1,
                            attempts,
                            e,
                        )
            if terminal:
                self._log.error(
                    "terminal task %s save exhausted %d retries: %r",
                    task.id,
                    attempts,
                    last,
                )
            raise last

        async def get(self, task_id, context):
            return await self._inner.get(task_id, context)

        async def delete(self, task_id, context) -> None:
            return await self._inner.delete(task_id, context)

        async def list(self, params, context):
            return await self._inner.list(params, context)

    return _TerminalRetryStore


def _TerminalRetryTaskStore(inner, **kwargs):  # class-like factory name
    """Construct the terminal-retry task-store wrapper around ``inner``.

    A thin factory over the lazily-built ``_TerminalRetryStore`` class so the
    a2a-sdk import stays inside a method (package import pulls no heavy deps).
    """
    return _terminal_retry_task_store()(inner, **kwargs)


def _resolve_bucket(bucket_name: str | None = None, env: str = SKILLS_BUCKET_ENV) -> str:
    """Resolve the Skill Registry GCS bucket: ``bucket_name`` arg wins, else env.

    Raises ``RuntimeError`` when neither is set — the gcp adapter REFUSES a silent
    fallback (mirrors ``_resolve_dsn``; a misconfigured registry must fail loud, not
    quietly serve an empty/wrong bucket). The bucket is provisioned by the P7-3 gcs
    Terraform module and its name injected as the ``BRIDGE_SKILLS_BUCKET`` env.
    """
    name = (bucket_name or os.environ.get(env, "")).strip()
    if not name:
        raise RuntimeError(
            f"GCP skill_registry requires a GCS bucket ({env} env or bucket_name=); "
            "refusing a silent fallback."
        )
    return name


def _validate_skill_paths(name: str, rels) -> None:
    """Reject a traversing skill name / asset path BEFORE any storage I/O.

    The identical S0-sec-1 traversal defense as ``LocalSkillRegistry.install_skill``
    (name must match ``^[a-z0-9-]+$``; every asset must resolve inside the skill's
    object prefix ``<name>/``), applied here against a POSIX object namespace (GCS
    blob names, not a real filesystem) so a crafted name/rel can never escape the
    skill's prefix. Runs before ``_bucket()`` so no client is even built for a bad
    request (no network — a reference is not a capability). ``rels`` is any iterable
    of skill-relative paths (``install_skill``'s assets, or one ``read_asset`` rel).
    """
    import posixpath
    import re

    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
    for rel in rels or ():
        # Normalize <name>/<rel> in the POSIX object namespace; a traversal collapses
        # to something outside "<name>/…" (or to bare "<name>"), which is rejected.
        dest = posixpath.normpath(f"{name}/{rel}")
        if dest != name and not dest.startswith(f"{name}/"):
            raise ValueError(f"asset path {rel!r} escapes skill dir")


def _download_prefix(bucket, prefix: str, dest) -> None:
    """Download every blob under ``prefix`` into ``dest`` preserving relative paths.

    ``prefix`` is ``<name>/``; a blob ``<name>/SKILL.md`` lands at
    ``dest/SKILL.md``. Directory placeholder blobs (the trailing-slash key) are
    skipped. ``dest`` is a ``Path``.
    """
    for blob in bucket.list_blobs(prefix=prefix):
        rel = blob.name[len(prefix):]
        if not rel:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))


class GcpSkillRegistry(SkillRegistrySeam):
    """GCP skill_registry adapter (P7-3) — a GCS bucket of Agent Skills folders.

    The SAME folder format as ``LocalSkillRegistry`` (``<name>/SKILL.md``,
    ``<name>/<asset>``) — "the format is the seam contract" (design/seams.md), so
    local→gcp is a no-op for the Agent Card. The GCP novelty is only the store: GCS
    objects instead of a directory. Reads download-to-temp and reuse the identical
    ``skill_meta_from_dir`` parser (never re-implement frontmatter parsing);
    ``install_skill`` reuses the identical S0-sec-1 traversal defense before any
    upload. The bucket name comes from the arg or ``BRIDGE_SKILLS_BUCKET`` env;
    neither set → refuse (RuntimeError), never a silent fallback.

    A bad live upload is refused, not propagated (S4-usecase-3), with the SAME helpers
    as local: ``list_skills`` skips a malformed folder with a logged reason
    (``listed_skill_meta``) and keeps listing; ``install_skill`` refuses a malformed
    upload (``validate_skill_upload``) before any client is built or object written.

    Heavy deps (``google.cloud.storage``) and the edge parser are imported lazily
    inside methods, mirroring ``bridge/seams/local`` — importing this package pulls
    neither (test_gcp_skill_registry enforces this). ``google-cloud-storage`` is the
    optional ``gcp`` extra, installed by the P7-9 provisioned run.
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self._bucket_name = bucket_name

    def _bucket(self):
        # Resolve (refuse-without-bucket) BEFORE importing the client, so a
        # misconfigured registry fails loud with the clear RuntimeError even when
        # google-cloud-storage is not installed (the default non-gcp suite).
        name = _resolve_bucket(self._bucket_name)
        from google.cloud.storage import Client

        return Client().bucket(name)

    def list_skills(self):
        import tempfile
        from pathlib import Path

        from bridge.edge.skills import listed_skill_meta

        bucket = self._bucket()
        have_skill_md: set[str] = set()
        for blob in bucket.list_blobs():
            parts = blob.name.split("/", 1)
            if len(parts) < 2:
                continue
            if blob.name == f"{parts[0]}/SKILL.md":
                have_skill_md.add(parts[0])
        metas = []
        for name in sorted(have_skill_md):  # deterministic (mirrors local sorted)
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / name
                _download_prefix(bucket, f"{name}/", dest)
                meta = listed_skill_meta(dest)
            if meta is not None:
                metas.append(meta)
        return metas

    def get_skill(self, name: str):
        import tempfile
        from pathlib import Path

        from bridge.edge.skills import skill_meta_from_dir

        bucket = self._bucket()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / name
            _download_prefix(bucket, f"{name}/", dest)
            if not (dest / "SKILL.md").exists():
                raise KeyError(name)
            return skill_meta_from_dir(dest)

    def install_skill(self, name, skill_md, assets=None):
        import tempfile
        from pathlib import Path

        from bridge.edge.skills import skill_meta_from_dir, validate_skill_upload

        # Traversal defense, then the malformed-upload refusal (S4-usecase-3), BOTH
        # before any I/O: no client is built and nothing is uploaded for a bad request.
        _validate_skill_paths(name, assets or {})
        validate_skill_upload(name, skill_md, assets)
        bucket = self._bucket()
        bucket.blob(f"{name}/SKILL.md").upload_from_string(skill_md)
        for rel, content in (assets or {}).items():
            bucket.blob(f"{name}/{rel}").upload_from_string(content)
        # Return meta parsed from a roundtrip of what was written (local semantics).
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / name
            _download_prefix(bucket, f"{name}/", dest)
            return skill_meta_from_dir(dest)

    def read_asset(self, name, rel):
        # Traversal defense BEFORE any I/O (no client built for a bad request) —
        # the same defense install_skill applies, on the read side.
        _validate_skill_paths(name, [rel])
        blob = self._bucket().blob(f"{name}/{rel}")
        if not blob.exists():
            raise KeyError(f"{name}/{rel}")
        return blob.download_as_text()


# ---------------------------------------------------------------------------
# scheduler (P7-4) — Cloud Tasks bridge-followups queue
# ---------------------------------------------------------------------------

# The env the runtime injects with the full Cloud Tasks queue resource path
# (projects/<P>/locations/<L>/queues/bridge-followups). The tasks module (P7-4)
# outputs it as queue_path; the runtime module injects it as this PLAIN env (a
# queue path is not a secret). The deployed scheduler reaches the queue ONLY here.
BRIDGE_TASKS_QUEUE_ENV = "BRIDGE_TASKS_QUEUE"

# The URL Cloud Tasks POSTs the follow-up callback to (the OIDC audience + the
# HTTP target). This is the runtime's OWN service URL — a Terraform self-reference
# cycle, so it is injected out-of-band at deploy (P7-9), never wired module→module
# (mirrors the DB DSN VALUE injection, DECISIONS.md 2026-09-07 P7-2).
BRIDGE_FOLLOWUP_CALLBACK_URL_ENV = "BRIDGE_FOLLOWUP_CALLBACK_URL"

# The bridge-runtime SA email the OIDC token is minted FOR. Cloud Tasks signs the
# callback with an OIDC token for this identity; the P7-1 self-scoped
# serviceAccountTokenCreator grant is exactly what authorizes that minting.
BRIDGE_RUNTIME_SA_EMAIL_ENV = "BRIDGE_RUNTIME_SA_EMAIL"

# The honest poll→push divergence message (see GcpScheduler docstring).
_ADVANCE_UNSUPPORTED = (
    "Cloud Tasks is wall-clock/push-driven: the queue fires the OIDC callback at "
    "schedule_time. Virtual advance()/advance_to() is a local-only VirtualClock "
    "test affordance and has no meaning against a real queue (terminal-outcome "
    "parity, not ledger-identical)."
)


def _resolve_scheduler_config(value: str | None, env: str, what: str) -> str:
    """Resolve one required scheduler config value: arg wins, else env, else refuse.

    Raises ``RuntimeError`` naming ``env`` when neither is set — the gcp scheduler
    REFUSES a silent in-process fallback. An in-process alarm store on multi-replica
    Cloud Run would silently drop armed alarms on the replica that did not arm them
    — the scheduler analogue of the session-affinity trap (mirrors ``_resolve_dsn``'s
    refuse-in-memory posture; design/seams.md, CRITICAL.md).
    """
    v = (value or os.environ.get(env, "")).strip()
    if not v:
        raise RuntimeError(
            f"GCP scheduler requires {what} ({env} env or the build_scheduler arg); "
            "refusing a silent in-process fallback (the scheduler analogue of the "
            "session-affinity trap)."
        )
    return v


def _task_id(key: str) -> str:
    """Derive a deterministic Cloud Tasks task id from an alarm ``key``.

    Cloud Tasks task ids must match ``[A-Za-z0-9_-]``; sanitize every other char to
    ``_`` so the same ``key`` always maps to the same task name (the crash-safe
    idempotent re-arm — re-arming a key delete-then-creates the SAME task, mirroring
    ``VirtualClock``'s "same key overwrites"). The follow-up keys are
    ``followup:<uuid-hex>`` (only ``:`` is replaced → no collision).
    """
    import re

    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


def _schedule_seconds(schedule_time) -> int:
    """Best-effort epoch-seconds from a task's ``schedule_time`` (dict, datetime, or
    protobuf ``Timestamp``) — used by ``pending()`` to round-trip ``fire_at``."""
    if schedule_time is None:
        return 0
    if isinstance(schedule_time, dict):
        return int(schedule_time.get("seconds", 0))
    if hasattr(schedule_time, "timestamp"):  # datetime (proto-plus Timestamp accessor)
        return int(schedule_time.timestamp())
    if hasattr(schedule_time, "seconds"):  # raw protobuf Timestamp
        return int(schedule_time.seconds)
    return 0


class _GcpCloudTasksScheduler:
    """Cloud Tasks-backed ``Scheduler`` (P7-4) — satisfies ``bridge.scheduler.Scheduler``.

    Same abstraction as the local ``VirtualClock``, different backend: each armed
    alarm is a Cloud Task in the ``bridge-followups`` queue, fired by the queue
    POSTing an OIDC-authenticated request to the follow-up callback URL at
    ``schedule_time``. The impedance mismatch is honest, not faked:

    - ``now()`` is **wall time** (``int(time.time())``); local returns a virtual
      counter. Both int; the ladder only orders/compares them.
    - ``advance``/``advance_to`` **raise** ``NotImplementedError`` — Cloud Tasks IS
      the clock; there is no local poll loop to fast-forward.
    """

    def __init__(
        self, *, queue_path: str, callback_url: str, oidc_sa_email: str, client=None
    ) -> None:
        self._queue_path = queue_path
        self._callback_url = callback_url
        self._oidc_sa_email = oidc_sa_email
        self._client_obj = client  # injected fake wins; else built lazily

    def _client(self):
        # Lazy: importing this package must not pull google.cloud.tasks (a structural
        # test enforces it). An injected client (the credential-free tests) wins.
        if self._client_obj is None:
            from google.cloud import tasks_v2

            self._client_obj = tasks_v2.CloudTasksClient()
        return self._client_obj

    def _task_name(self, key: str) -> str:
        return f"{self._queue_path}/tasks/{_task_id(key)}"

    def _delete_quietly(self, client, name: str) -> None:
        import contextlib

        from google.api_core.exceptions import NotFound

        with contextlib.suppress(NotFound):
            client.delete_task(name=name)

    def now(self) -> int:
        import time

        return int(time.time())

    def schedule(self, key: str, fire_at: int, payload=None):
        """Mint a Cloud Task firing at ``fire_at`` (absolute epoch seconds).

        The task POSTs a compact JSON body ``{"key", "payload"}`` to the callback
        URL, authenticated by an OIDC token for the runtime SA (audience == the
        callback URL). Idempotent re-arm: the task name is derived deterministically
        from ``key``, so re-arming the same key delete-then-creates the SAME task
        (crash-safe, mirroring ``VirtualClock``'s same-key-overwrites). NOTE: the
        ``payload`` must be JSON-serializable — the follow-up payload is a
        ``LifecycleStatus`` str, so this holds.
        """
        import json

        from bridge.scheduler import Alarm

        client = self._client()
        name = self._task_name(key)
        # Crash-safe re-arm: delete-then-create the same name. (Cloud Tasks dedups a
        # recently-deleted name for ~1h; the follow-up ladder re-arms only after a
        # step FIRES — the fired task is already consumed — so the delete clears any
        # still-pending arm before the next create.)
        self._delete_quietly(client, name)
        body = json.dumps(
            {"key": key, "payload": payload}, separators=(",", ":")
        ).encode()
        task = {
            "name": name,
            "schedule_time": {"seconds": int(fire_at)},
            "http_request": {
                "http_method": "POST",
                "url": self._callback_url,
                "headers": {"Content-Type": "application/json"},
                "body": body,
                # OIDC (not a bearer token / API key): the P7-1 self-scoped
                # serviceAccountTokenCreator grant authorizes minting this; the
                # receiving Cloud Run service validates it (per-party run.invoker, P7-5).
                "oidc_token": {
                    "service_account_email": self._oidc_sa_email,
                    "audience": self._callback_url,
                },
            },
        }
        client.create_task(parent=self._queue_path, task=task)
        return Alarm(key=key, fire_at=int(fire_at), payload=payload)

    def cancel(self, key: str) -> bool:
        """Delete the task by its derived name; ``True`` if it existed, ``False`` on
        ``NotFound`` (mirrors ``VirtualClock.cancel``'s bool contract)."""
        from google.api_core.exceptions import NotFound

        client = self._client()
        try:
            client.delete_task(name=self._task_name(key))
            return True
        except NotFound:
            return False

    def pending(self):
        """List armed tasks as ``Alarm``s (best-effort: ``fire_at`` round-trips from
        ``schedule_time``, ``key``/``payload`` from the JSON body; parity is
        terminal-outcome, not ledger-identical). Used by the gated live roundtrip
        test to observe an armed task."""
        import json

        from bridge.scheduler import Alarm

        alarms = []
        for task in self._client().list_tasks(parent=self._queue_path):
            if isinstance(task, dict):
                http = task.get("http_request", {}) or {}
                body = http.get("body", b"")
                st = task.get("schedule_time")
            else:
                body = task.http_request.body
                st = task.schedule_time
            key, payload = "", None
            if body:
                data = json.loads(body)
                key, payload = data.get("key", ""), data.get("payload")
            alarms.append(Alarm(key=key, fire_at=_schedule_seconds(st), payload=payload))
        return alarms

    def advance(self, ticks: int):
        raise NotImplementedError(_ADVANCE_UNSUPPORTED)

    def advance_to(self, tick: int):
        raise NotImplementedError(_ADVANCE_UNSUPPORTED)


class GcpScheduler(SchedulerSeam):
    """GCP scheduler adapter (P7-4) — the ``bridge-followups`` Cloud Tasks queue.

    Satisfies the SAME ``Scheduler`` protocol the local ``VirtualClock`` does, so the
    follow-up-ladder logic (``bridge/followup.py``) is adapter-agnostic — only *who
    calls* ``on_fired`` differs (a test/timewarp ``advance`` loop locally; the OIDC
    callback on the deployed path). Parity is **terminal-outcome** (the ladder reaches
    ``escalated`` on both paths), never ledger-identical: local is poll-driven
    (``advance`` returns fired alarms), Cloud Tasks is push-driven (real wall clock;
    the queue POSTs the OIDC callback at ``schedule_time``), so ``advance``/
    ``advance_to`` honestly raise ``NotImplementedError`` here.

    Config (arg wins, else env, else refuse-loud — never a silent in-process fallback):
    ``BRIDGE_TASKS_QUEUE`` (queue resource path), ``BRIDGE_FOLLOWUP_CALLBACK_URL``
    (OIDC audience + HTTP target), ``BRIDGE_RUNTIME_SA_EMAIL`` (the OIDC token SA).

    **Deferred to P7-9 (logged, not silent).** This adapter delivers the **arming
    half**: it mints the correct OIDC-authenticated Cloud Task in the right queue with
    the right ``schedule_time`` and idempotent re-arm — all proven credential-free
    here. The HTTP endpoint that RECEIVES the Cloud Tasks POST and re-drives the
    ladder (``FollowupLadder.on_fired`` → re-arm the next step), and the live
    terminal-outcome firing (arm → the queue fires → the ladder reaches ``escalated``
    on the deployed path), are the P7-9 deployed-path parity run / the follow-up-worker
    wiring — the same "follow-up behind the edge" deferral logged in TODO.md and
    DECISIONS.md 2026-08-21 (S0-arch-1). The timewarp fast-forward host
    (``bridge/timewarp/host.py``) stays a local-only ``VirtualClock`` dev tool and
    never runs against this adapter.

    Heavy deps (``google.cloud.tasks_v2``) are imported lazily inside methods —
    importing this package pulls NOTHING new (a structural test enforces it).
    ``google-cloud-tasks`` is the optional ``gcp`` extra, installed by the P7-9 run.
    """

    def __init__(
        self,
        queue_path: str | None = None,
        callback_url: str | None = None,
        oidc_sa_email: str | None = None,
    ) -> None:
        self._queue_path = queue_path
        self._callback_url = callback_url
        self._oidc_sa_email = oidc_sa_email

    def build_scheduler(
        self,
        client=None,
        *,
        queue_path: str | None = None,
        callback_url: str | None = None,
        oidc_sa_email: str | None = None,
    ):
        """Build the Cloud Tasks scheduler (injectable-seam idiom, P7-8 wiring-guard).

        An **injected fake ``client`` wins** (the credential-free tests); when
        ``client is None`` a real ``CloudTasksClient`` is lazily built on first use.
        Config args win over the instance defaults, which win over env. Called with
        no args by the seam parity test → resolves entirely from env (and raises
        ``RuntimeError`` naming the queue env when unset — refuse-without-config).
        """
        return _GcpCloudTasksScheduler(
            queue_path=_resolve_scheduler_config(
                queue_path or self._queue_path,
                BRIDGE_TASKS_QUEUE_ENV,
                "a Cloud Tasks queue path",
            ),
            callback_url=_resolve_scheduler_config(
                callback_url or self._callback_url,
                BRIDGE_FOLLOWUP_CALLBACK_URL_ENV,
                "a follow-up callback URL",
            ),
            oidc_sa_email=_resolve_scheduler_config(
                oidc_sa_email or self._oidc_sa_email,
                BRIDGE_RUNTIME_SA_EMAIL_ENV,
                "the runtime SA email for the OIDC token",
            ),
            client=client,
        )


# ---------------------------------------------------------------------------
# a2a_client (P7-5) — the Agent-Identity outbound transport
# ---------------------------------------------------------------------------

# The Gateway-authorized host (origin) that outbound A2A must resolve to — e.g.
# ``bridge-runtime-abc.a.run.app`` or the LB domain. Injected by the runtime module
# as a PLAIN env (a hostname is not a secret). A peer card whose advertised host
# differs is a misconfiguration and is refused loud (reconcile_card_host), NEVER
# silently repointed (the adk >=2.7 same-origin re-verify finding below).
BRIDGE_GATEWAY_HOST_ENV = "BRIDGE_GATEWAY_HOST"

# NOTE: the OIDC ID token is minted for the SAME bridge-runtime SA the scheduler
# uses, so the a2a_client adapter REUSES ``BRIDGE_RUNTIME_SA_EMAIL_ENV`` (defined
# above for the scheduler) — one workload identity, one env.


def _resolve_a2a_config(value: str | None, env: str, what: str) -> str:
    """Resolve one required a2a_client config value: arg wins, else env, else refuse.

    Raises ``RuntimeError`` naming ``env`` when neither is set — the gcp a2a_client
    REFUSES a silent in-process/fake-transport fallback. A fake transport on the
    deployed path would silently SWALLOW outbound sends (the nudge/dial/deliver never
    leave the box) — the a2a_client analogue of the session-affinity trap (mirrors
    ``_resolve_scheduler_config``'s refuse-loud posture; design/seams.md, CRITICAL.md).
    """
    v = (value or os.environ.get(env, "")).strip()
    if not v:
        raise RuntimeError(
            f"GCP a2a_client requires {what} ({env} env or the build_client arg); "
            "refusing a silent in-process/fake-transport fallback (the a2a_client "
            "analogue of the session-affinity trap)."
        )
    return v


def _host_token(value: str) -> str:
    """Normalize a host / URL / host:port to a comparable ``host[:port]`` token.

    Accepts a bare host (``host.example``), a host:port, or a full URL
    (``https://host.example/rpc``) and returns the ``hostname`` plus ``:port`` when a
    port is present. Used by ``reconcile_card_host`` so a peer card URL and the
    Gateway-authorized host compare on origin, not on path/scheme.
    """
    from urllib.parse import urlparse

    parsed = urlparse(value if "//" in value else f"//{value}")
    host = parsed.hostname or ""
    return f"{host}:{parsed.port}" if parsed.port is not None else host


def reconcile_card_host(advertised_url: str, gateway_host: str) -> str:
    """Reconcile a peer card's advertised URL against the Gateway-authorized host.

    Under ``google-adk >=2.7`` (verified 2.7.1) same-origin card validation
    (``RemoteA2aAgent._validate_card_rpc_targets``: a card fetched over the network
    may only aim RPC traffic at its own origin), the 2.3-era ``.mtls`` card-repoint
    hack from the vibeflix-audit reference is WRONG and is deliberately NOT ported —
    a repoint would now HARD-FAIL the same-origin check. Our outbound path is
    ``bridge.outbound.OutboundClient`` (not ``RemoteA2aAgent``), so the reconciliation
    is ours to do explicitly: the advertised host MUST equal the Gateway-authorized
    host. A mismatch is a misconfiguration and raises ``ValueError`` naming BOTH hosts
    (refuse-loud) — never a silent repoint (mirrors the seam's refuse-without-config
    posture). A match returns ``advertised_url`` unchanged.
    """
    advertised = _host_token(advertised_url)
    authorized = _host_token(gateway_host)
    if advertised != authorized:
        raise ValueError(
            f"peer Agent Card host {advertised!r} (from {advertised_url!r}) does not "
            f"match the Gateway-authorized host {authorized!r}; refusing to send "
            "(adk >=2.7 same-origin — no .mtls repoint). Fix the peer card or "
            "BRIDGE_GATEWAY_HOST, do not repoint."
        )
    return advertised_url


class _AgentIdentityTransport:
    """Agent-Identity outbound transport (P7-5) — satisfies ``OutboundTransport``.

    Swaps ONLY the wire under ``bridge.outbound.OutboundClient``: the client's
    idempotent, crash-safe re-drive (OUT-1) is reused unchanged; this transport adds
    workload authentication. On a message that carries a peer RPC URL (a ``DialOut``'s
    ``card_url``) it:

    1. reconciles the advertised host against the Gateway-authorized host
       (``reconcile_card_host`` — refuse-loud on mismatch, no ``.mtls`` repoint), then
    2. mints a workload **OIDC ID token** with ``audience == the reconciled URL`` — the
       Agent Identity workload auth, NOT a long-lived party bearer token — and
    3. POSTs the request with an ``Authorization: Bearer <id_token>`` header.

    The OIDC token is a per-call transport header, NEVER a field on the message (the
    ``test_nudge_carries_no_bearer_token`` invariant family).

    ``id_token_fetcher`` and ``sender`` are injectable (default ``None`` → the real
    ``google.oauth2.id_token``/``httpx`` built lazily on first use) — the credential-
    free test seam, exactly like ``_GcpCloudTasksScheduler(client=...)``. Heavy deps
    (``google.auth``/``google.oauth2``/``httpx``) are imported lazily inside methods so
    importing this package pulls none of them (a structural test enforces it).

    **Deferred live half (logged, not silent).** A message that carries no RPC URL
    (a ``Nudge``'s party channel, a ``Delivery``'s recipient) has its authenticated
    wire send deferred to the real carriers (S1-benefits-6) / the provisioned deployed
    run (P7-9): the transport is a no-op for it here so terminal-outcome parity with
    the local fake transport holds (the receipt is still minted by ``OutboundClient``).
    The DialOut send itself is minimal (the token/host/headers are proven credential-
    free; the real network POST is the same P7-9 gated half as ``GcpScheduler``).
    """

    def __init__(
        self, *, gateway_host: str, sa_email: str, id_token_fetcher=None, sender=None
    ) -> None:
        self._gateway_host = gateway_host
        self._sa_email = sa_email
        self._id_token_fetcher = id_token_fetcher  # injected fake wins; else lazy real
        self._sender = sender

    def _fetch_id_token(self, audience: str) -> str:
        if self._id_token_fetcher is not None:
            return self._id_token_fetcher(audience)
        import google.auth.transport.requests
        from google.oauth2 import id_token

        return id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience
        )

    def _post(self, url: str, headers: dict[str, str], body: bytes) -> None:
        if self._sender is not None:
            self._sender(url=url, headers=headers, body=body)
            return
        import httpx

        # The real network send — the DEFERRED live half (P7-9 provisioned run).
        httpx.Client().post(url, headers=headers, content=body)

    def deliver(self, message) -> None:
        target_url = getattr(message, "card_url", None)
        if not target_url:
            # Nudge / Delivery carry a party channel / recipient, not an https RPC
            # URL — the authenticated wire send for those is the deferred live half
            # (S1-benefits-6 / P7-9). No-op here so terminal-outcome parity with the
            # local fake transport holds (OutboundClient still mints the receipt).
            return
        url = reconcile_card_host(target_url, self._gateway_host)  # refuse-loud
        id_token = self._fetch_id_token(url)  # Agent Identity: audience == reconciled url
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json",
        }
        body = str(getattr(message, "message", "")).encode()
        self._post(url, headers, body)


class GcpA2AClient(A2AClientSeam):
    """GCP a2a_client adapter (P7-5) — Agent-Identity outbound transport.

    Returns the SAME ``bridge.outbound.OutboundClient`` the local adapter returns,
    swapping ONLY the transport for ``_AgentIdentityTransport`` (workload OIDC auth +
    Gateway-host reconciliation). Because only the wire differs, local↔gcp parity is
    **terminal-outcome** (identical receipts for send/dial/deliver), never ledger-
    identical — the idempotent crash-safe re-drive lives in ``OutboundClient`` and is
    not reimplemented here.

    **The adk >=2.7 same-origin re-verify finding (DECISIONS.md P7-5).** The 2.3-era
    ``.mtls`` card-repoint hack (vibeflix-audit) MUST NOT be ported: adk >=2.7
    enforces that a network-fetched card may only aim RPC traffic at its own origin,
    so a repoint would hard-fail. The correct fix is (a) the served Bridge card
    advertises its Gateway-authorized origin (``BRIDGE_PUBLIC_URL``, edge/card.py),
    and (b) this outbound path reconciles the peer card host against the
    Gateway-authorized host and REFUSES LOUD on mismatch (``reconcile_card_host``),
    never a silent repoint.

    Config (arg wins, else env, else refuse-loud — never a silent fake-transport
    fallback, which would silently swallow outbound sends on the deployed path):
    ``BRIDGE_GATEWAY_HOST`` (the authorized host) and ``BRIDGE_RUNTIME_SA_EMAIL`` (the
    workload SA the OIDC token is minted for, shared with the scheduler).

    **Deferred to S1-benefits-6 / P7-9 (logged, not silent).** This adapter delivers
    the transport-construction + host-reconciliation + token-minting half, proven
    credential-free (injected fetcher/sender). The actual authenticated wire send to a
    live counterparty (real ADK/LangGraph carriers, S1-benefits-6) and the provisioned
    deployed roundtrip (P7-9) are the gated/deferred half — mirroring ``GcpScheduler``'s
    arming-half / gated-live-roundtrip posture. Heavy deps are lazy; the live send is
    a ``BRIDGE_TEST_GCP``-gated test.
    """

    def __init__(
        self, gateway_host: str | None = None, sa_email: str | None = None
    ) -> None:
        self._gateway_host = gateway_host
        self._sa_email = sa_email

    def build_client(
        self,
        *,
        gateway_host: str | None = None,
        sa_email: str | None = None,
        id_token_fetcher=None,
        sender=None,
    ):
        """Build the Agent-Identity outbound client (injectable-seam idiom).

        Injected ``id_token_fetcher``/``sender`` win (the credential-free tests);
        when ``None`` the real ``google.oauth2.id_token``/``httpx`` are built lazily on
        first use. Config args win over the instance defaults, which win over env.
        Called with no config → resolves from env and raises ``RuntimeError`` naming
        ``BRIDGE_GATEWAY_HOST`` when unset (refuse-without-config). Returns the SAME
        ``OutboundClient`` the local adapter does — only the transport differs.
        """
        from bridge.outbound import OutboundClient

        transport = _AgentIdentityTransport(
            gateway_host=_resolve_a2a_config(
                gateway_host or self._gateway_host,
                BRIDGE_GATEWAY_HOST_ENV,
                "the Gateway-authorized host",
            ),
            sa_email=_resolve_a2a_config(
                sa_email or self._sa_email,
                BRIDGE_RUNTIME_SA_EMAIL_ENV,
                "the runtime SA email for the OIDC token",
            ),
            id_token_fetcher=id_token_fetcher,
            sender=sender,
        )
        return OutboundClient(transport)


# ---------------------------------------------------------------------------
# extraction (P7-6) — the Gemini extraction engine behind the seam
# ---------------------------------------------------------------------------

# The extraction ENGINE selector (fixture|gemini|docai). NOTE the axis: extraction is
# the CAPABILITY axis, deployed either way and selected by config — NOT a local↔gcp
# store swap like the other seven seams (design/seams.md). So this "gcp" adapter picks
# the deployed Gemini engine; ``docai`` is that same engine with the client's Document
# AI processor as an OCR pre-pass (BRIDGE_DOCAI_PROCESSOR, design/docai-prepass.md).
# Default: gemini.
BRIDGE_EXTRACTION_ENGINE_ENV = "BRIDGE_EXTRACTION_ENGINE"


class GcpExtraction(ExtractionSeam):
    """GCP extraction adapter (P7-6) — the deployed Gemini engine behind the seam.

    NOTE the axis: extraction is the CAPABILITY axis (``fixture|gemini|docai``),
    deployed either way and selected by config — NOT a local↔gcp store swap like the
    other seven seams (design/seams.md). This "gcp" adapter selects the deployed
    Gemini engine; ``docai`` is the same Gemini engine with the client's Document AI
    processor (``BRIDGE_DOCAI_PROCESSOR``) run as an OCR/Layout/Form pre-pass for
    scanned/raster documents, and it refuses loud without a processor — never a
    silent downgrade to plain Gemini. It NEVER silently falls back to the fixture
    engine: a misconfigured/unsupported engine fails loud (mirrors the sibling GCP
    adapters' refuse-loud posture).

    Heavy deps (``google.genai``, ``google.cloud.documentai``) are lazy inside the
    engine (a structural test enforces it); importing this package pulls nothing new.
    """

    def __init__(self, engine: str | None = None) -> None:
        self._engine_name = engine

    def _resolve_engine(self):
        """Select the extraction engine: arg wins, else env, else gemini default.

        ``gemini`` → the deployed ``GeminiExtractor``; ``docai`` →
        ``GeminiExtractor(prepass=DocAIPrepass())`` — ``RuntimeError`` naming
        ``BRIDGE_DOCAI_PROCESSOR`` when unset, ``ValueError`` when malformed (checked
        here, before any fetch; no client is built yet); ``fixture`` → an EXPLICIT
        opt-in only (the gcp adapter never SILENTLY falls back to fixture); anything
        else → ``ValueError`` (refuse-loud on misconfig).
        """
        name = (
            self._engine_name
            or os.environ.get(BRIDGE_EXTRACTION_ENGINE_ENV, "gemini")
        ).strip() or "gemini"
        if name == "gemini":
            from bridge.extraction.gemini import GeminiExtractor

            return GeminiExtractor()
        if name == "docai":
            from bridge.extraction.docai import DocAIPrepass
            from bridge.extraction.gemini import GeminiExtractor

            return GeminiExtractor(prepass=DocAIPrepass())
        if name == "fixture":
            # Explicit opt-in only; the gcp adapter never SILENTLY falls back to fixture.
            from bridge.extraction.fixture import FixtureExtractor

            return FixtureExtractor()
        raise ValueError(
            f"unknown extraction engine {name!r}: expected fixture|gemini|docai"
        )

    async def extract(self, artifact_name: str):
        return await self._resolve_engine().extract(artifact_name)


# ---------------------------------------------------------------------------
# email_ingress (S0-arch-13) — the GCS mailbox landing bucket
# ---------------------------------------------------------------------------

# The env the runtime + the email webhook service get with the landing bucket name
# (the gcs module → a PLAIN env; a bucket name is not a secret).
EMAIL_BUCKET_ENV = "BRIDGE_EMAIL_BUCKET"


class GcpEmailIngress(EmailIngressSeam):
    """GCP email_ingress adapter (S0-arch-13) — a GCS landing bucket.

    The SAME object layout as ``LocalEmailIngress`` (``inbox/<key>.eml``,
    ``attachments/<key>/<i>/<filename>``, ``processed/<key>.json``). Every write is
    create-only via ``if_generation_match=0``:

    * ``PreconditionFailed`` (412) → the object already exists → a duplicate.
    * ``Forbidden`` (403) → ``blob.exists()``: present → a duplicate, else re-raise.
      The webhook SA holds only ``roles/storage.objectCreator`` (no delete), and GCS
      may answer an overwrite attempt by such an identity with 403 rather than 412.

    The bucket comes from the arg or ``BRIDGE_EMAIL_BUCKET`` — resolved (refusing
    loud without one) BEFORE ``google.cloud.storage`` is imported, mirroring
    ``GcpSkillRegistry._bucket``. Keys / filenames are validated before any client is
    built. ``put_attachment`` returns ``gs://`` — the extraction resolver passes it to
    Vertex by reference (no download).
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self._bucket_name = bucket_name

    def _name(self) -> str:
        name = (self._bucket_name or os.environ.get(EMAIL_BUCKET_ENV, "")).strip()
        if not name:
            raise RuntimeError(
                f"GCP email_ingress requires a GCS bucket ({EMAIL_BUCKET_ENV} env or "
                "bucket_name=); refusing a silent fallback."
            )
        return name

    def _bucket(self):
        name = self._name()
        from google.cloud.storage import Client

        return Client().bucket(name)

    @staticmethod
    def _create_only(blob, data: bytes, content_type: str) -> bool:
        from google.api_core.exceptions import Forbidden, PreconditionFailed

        try:
            blob.upload_from_string(
                data, content_type=content_type, if_generation_match=0
            )
        except PreconditionFailed:
            return False
        except Forbidden:
            if blob.exists():
                return False
            raise
        return True

    def land(self, key: str, raw: bytes) -> bool:
        validate_landing_key(key)
        blob = self._bucket().blob(f"inbox/{key}.eml")
        return self._create_only(blob, raw, "message/rfc822")

    def pending(self) -> list[str]:
        import re

        from ..protocols import EMAIL_KEY_PATTERN

        bucket = self._bucket()
        done = {
            blob.name[len("processed/") : -len(".json")]
            for blob in bucket.list_blobs(prefix="processed/")
            if blob.name.endswith(".json")
        }
        rows = []
        for blob in bucket.list_blobs(prefix="inbox/"):
            rel = blob.name[len("inbox/") :]
            key = rel.removesuffix(".eml")
            if not rel.endswith(".eml") or not re.fullmatch(EMAIL_KEY_PATTERN, key):
                _LOG.warning("email_ingress: skipping unexpected inbox object %r", blob.name)
                continue
            if key in done:
                continue
            rows.append((blob.time_created, key))
        return [key for _, key in sorted(rows)]

    def read(self, key: str) -> bytes:
        validate_landing_key(key)
        blob = self._bucket().blob(f"inbox/{key}.eml")
        if not blob.exists():
            raise KeyError(key)
        return blob.download_as_bytes()

    def put_attachment(self, key, index, filename, data, mime_type) -> str:
        validate_landing_key(key)
        validate_attachment_slot(index, filename)
        name = f"attachments/{key}/{index}/{filename}"
        bucket = self._bucket()
        self._create_only(bucket.blob(name), data, mime_type)  # existing → same uri
        return f"gs://{bucket.name}/{name}"

    def mark_processed(self, key: str, outcome: dict) -> bool:
        import json

        validate_landing_key(key)
        blob = self._bucket().blob(f"processed/{key}.json")
        body = json.dumps(outcome, sort_keys=True).encode()
        return self._create_only(blob, body, "application/json")

    def processed(self, key: str) -> dict | None:
        import json

        validate_landing_key(key)
        blob = self._bucket().blob(f"processed/{key}.json")
        if not blob.exists():
            return None
        return json.loads(blob.download_as_bytes())


GCP_ADAPTERS: dict[Seam, type[SeamABC]] = {
    # All nine seams are wired to their GCP adapters:
    # - sessions: VertexAiSessionService (BRIDGE_AGENT_ENGINE_ID) or Cloud SQL (DATABASE_URL)
    # - memory_bank: VertexAiMemoryBankService (BRIDGE_AGENT_ENGINE_ID)
    # - task_store / exchange_store: Cloud SQL (P7-2 / S0-arch-14)
    # - skill_registry: GCS (P7-3)
    # - scheduler: Cloud Tasks (P7-4)
    # - a2a_client: Agent-Identity transport (P7-5)
    # - extraction: Gemini / Document AI pre-pass (P7-6)
    # - email_ingress: GCS landing bucket (S0-arch-13)
    Seam.sessions: GcpSessions,
    Seam.task_store: GcpTaskStore,
    Seam.exchange_store: GcpExchangeStore,
    Seam.skill_registry: GcpSkillRegistry,
    Seam.memory_bank: GcpMemoryBank,
    Seam.scheduler: GcpScheduler,
    Seam.a2a_client: GcpA2AClient,
    Seam.extraction: GcpExtraction,
    # The GCS mailbox landing bucket (S0-arch-13).
    Seam.email_ingress: GcpEmailIngress,
}
