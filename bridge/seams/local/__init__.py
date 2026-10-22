"""Placeholder local adapters — filled in per-phase."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bridge.extraction.fixture import FixtureExtractor

from ..protocols import (
    A2AClientSeam,
    ActionInboxPort,
    AgentGymPort,
    AuditVaultPort,
    EmailIngressSeam,
    ExchangeStoreSeam,
    ExtractionSeam,
    MemoryBankSeam,
    OperationalStorePort,
    SchedulerSeam,
    SeamABC,
    SessionsSeam,
    SkillRegistrySeam,
    TaskStoreSeam,
    validate_attachment_slot,
    validate_landing_key,
)
from ..seam import Port, Seam

# The landing directory of the local email_ingress adapter (S0-arch-13). No silent
# default: an unset dir fails loud at first use (never at construction).
EMAIL_DIR_ENV = "BRIDGE_EMAIL_DIR"

# The DSN of the local exchange_store adapter (S0-arch-14). Unset → in-memory records.
EXCHANGE_DB_URL_ENV = "BRIDGE_EXCHANGE_DB_URL"

# The SQLite path of the local memory_bank adapter. Unset → in-memory service.
MEMORY_DB_PATH_ENV = "BRIDGE_MEMORY_DB_PATH"

# Default sqlite DSN prefix — the async driver is mandatory (see require_async_driver).
# An empty path after the driver is an in-memory async sqlite database.
DEFAULT_SQLITE_DSN = "sqlite+aiosqlite:///"


def require_async_driver(dsn: str) -> str:
    """Reject a sync sqlite DSN early with a clear message (design gotcha,
    design/long-running.md:39).

    ``DatabaseSessionService``/``DatabaseTaskStore`` run on SQLAlchemy's asyncio
    extension, which needs an async driver; a bare ``sqlite:///`` URL fails with a
    cryptic "the asyncio extension requires an async driver". Use
    ``sqlite+aiosqlite:///…``.

    NOTE: duplicated identically in ``agents/address/app.py`` (§1.4 — ``agents/``
    must not import ``bridge/``, enforced by ``tests/test_isolation_guard.py``;
    parity is by DSN + ADK class, not a shared object).
    """
    if dsn.startswith("sqlite:///") or dsn == "sqlite://":
        raise ValueError(
            f"DatabaseSessionService/DatabaseTaskStore need an async driver; "
            f"use 'sqlite+aiosqlite:///…' not {dsn!r}"
        )
    return dsn


class LocalSessions(SessionsSeam):
    """Local sessions adapter (DUR-1).

    Builds a durable ``DatabaseSessionService`` over an async sqlite DSN when one
    is configured (``db_url`` arg or ``BRIDGE_SESSION_DB_URL`` env), else an
    in-memory service (keeps the default test/dev path file-free).
    """

    def build_session_service(self, db_url: str | None = None) -> Any:
        """Construct an in-memory ADK session service for local execution."""
        dsn = db_url or os.environ.get("BRIDGE_SESSION_DB_URL", "").strip()
        if not dsn:
            from google.adk.sessions import InMemorySessionService

            return InMemorySessionService()
        require_async_driver(dsn)
        from google.adk.sessions import DatabaseSessionService
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
        return DatabaseSessionService(db_url=dsn, **kwargs)


class LocalTaskStore(TaskStoreSeam):
    """Local task-store adapter (DUR-2).

    Builds a durable ``DatabaseTaskStore`` over an ``AsyncEngine`` when a DSN is
    configured (``db_url`` arg or ``BRIDGE_TASK_DB_URL`` env), else an in-memory
    store. Note ``DatabaseTaskStore`` takes an engine, not a URL.
    """

    def build_task_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory A2A task store for local execution."""
        dsn = db_url or os.environ.get("BRIDGE_TASK_DB_URL", "").strip()
        if not dsn:
            from a2a.server.tasks import InMemoryTaskStore

            return InMemoryTaskStore()
        require_async_driver(dsn)
        from a2a.server.tasks import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
        return DatabaseTaskStore(engine=create_async_engine(dsn, **kwargs))

    def build_push_config_store(self, db_url: str | None = None) -> Any:
        """Build a push notification config store (in-memory or durable).

        Mirrors ``build_task_store`` exactly (Decision 1): both are a2a-sdk
        Database* classes over the same DSN (``BRIDGE_TASK_DB_URL``).
        """
        dsn = db_url or os.environ.get("BRIDGE_TASK_DB_URL", "").strip()
        if not dsn:
            from a2a.server.tasks import InMemoryPushNotificationConfigStore

            return InMemoryPushNotificationConfigStore()
        require_async_driver(dsn)
        from a2a.server.tasks import DatabasePushNotificationConfigStore
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if dsn.startswith("sqlite") else {}
        return DatabasePushNotificationConfigStore(
            engine=create_async_engine(dsn, **kwargs), create_table=True
        )


class LocalExchangeStore(ExchangeStoreSeam):
    """Local exchange_store adapter (S0-arch-14).

    Builds a durable ``SqlExchangeStore`` over an async sqlite DSN when one is
    configured (``db_url`` arg or ``BRIDGE_EXCHANGE_DB_URL`` env), else an
    ``InMemoryExchangeStore`` (keeps the default test/dev path file-free — mirrors
    ``LocalSessions``). The SQL store opens nothing until its first operation.
    """

    def build_exchange_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQLite-backed exchange store for local execution."""
        from bridge.exchange_store import InMemoryExchangeStore, SqlExchangeStore

        dsn = db_url or os.environ.get(EXCHANGE_DB_URL_ENV, "").strip()
        if not dsn:
            return InMemoryExchangeStore()
        return SqlExchangeStore(require_async_driver(dsn))


class LocalSkillRegistry(SkillRegistrySeam):
    """Local skill_registry adapter (SKILL-4): a directory of Agent Skills folders.

    Root is ``BRIDGE_SKILLS_DIR`` (default the repo ``skills/`` dir).
    ``install_skill`` writes a folder at runtime so the Agent Card regenerates with
    no redeploy (``loaded_skills()`` reads through this seam on every discovery GET).
    GCP = managed Skill Registry (GCS) at P7. The parser is imported lazily inside
    methods, mirroring ``LocalScheduler`` (avoids a bridge.edge ↔ bridge.seams cycle).

    A bad live upload is refused, not propagated (S4-usecase-3): ``list_skills`` skips
    a malformed folder with a logged reason (``listed_skill_meta``) and keeps listing;
    ``install_skill`` refuses a malformed upload (``validate_skill_upload``) before any
    write, so a bad re-upload never overwrites a good skill.
    """

    def _root(self) -> Path:
        from bridge.sla import SKILLS_DIR

        return Path(os.environ.get("BRIDGE_SKILLS_DIR", "").strip() or SKILLS_DIR)

    def list_skills(self) -> list[Any]:
        """List all skill packages discovered in the local filesystem skills directory."""
        from bridge.edge.skills import listed_skill_meta

        root = self._root()
        skills = []
        if root.exists():
            for child in sorted(root.iterdir()):
                if child.is_dir() and (child / "SKILL.md").exists():
                    meta = listed_skill_meta(child)
                    if meta is not None:
                        skills.append(meta)
        return skills

    def get_skill(self, name: str) -> Any:
        """Load a single skill package definition from the local filesystem by name."""
        from bridge.edge.skills import skill_meta_from_dir

        skill_dir = self._root() / name
        if not (skill_dir / "SKILL.md").exists():
            raise KeyError(name)
        return skill_meta_from_dir(skill_dir)

    def install_skill(
        self, name: str, skill_md: str, assets: dict[str, str] | None = None
    ) -> Any:
        """Copy and register a skill package directory into the local skills root."""
        import re

        from bridge.edge.skills import skill_meta_from_dir, validate_skill_upload

        # Path-traversal defense (S0-sec-1): the skill name is a single directory
        # segment, and every asset must resolve inside the skill dir. Every path is
        # checked against the REAL destination before anything is staged or written,
        # so a crafted name/rel can't escape BRIDGE_SKILLS_DIR (nor the staging dir).
        if not re.fullmatch(r"[a-z0-9-]+", name or ""):
            raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
        root = self._root().resolve()
        skill_dir = (root / name).resolve()
        if skill_dir.parent != root:
            raise ValueError(f"invalid skill name {name!r}: escapes skills root")
        assets = assets or {}
        for rel in assets:
            dest = (skill_dir / rel).resolve()
            if dest != skill_dir and skill_dir not in dest.parents:
                raise ValueError(f"asset path {rel!r} escapes skill dir")

        # Refuse a malformed upload before any write (S4-usecase-3).
        validate_skill_upload(name, skill_md, assets)

        (skill_dir / "assets").mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md)
        for rel, content in assets.items():
            dest = (skill_dir / rel).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        return skill_meta_from_dir(skill_dir)

    def read_asset(self, name: str, rel: str) -> str:
        """Read a raw asset file from a locally installed skill directory."""
        import re

        # The same S0-sec-1 traversal defense as install_skill, applied on the read
        # side — strict containment here (an asset must live strictly INSIDE the
        # skill dir; install_skill tolerates dest == skill_dir for its own folder).
        if not re.fullmatch(r"[a-z0-9-]+", name or ""):
            raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
        root = self._root().resolve()
        skill_dir = (root / name).resolve()
        if skill_dir.parent != root:
            raise ValueError(f"invalid skill name {name!r}: escapes skills root")
        path = (skill_dir / rel).resolve()
        if skill_dir not in path.parents:
            raise ValueError(f"asset path {rel!r} escapes skill dir")
        if not path.exists():
            raise KeyError(f"{name}/{rel}")
        return path.read_text()


class LocalMemoryBank(MemoryBankSeam):
    """Local memory_bank adapter — ADK ``SqliteMemoryService`` or ``InMemoryMemoryService``.

    Builds a durable ``SqliteMemoryService`` when a path is configured (``db_path`` arg
    or ``BRIDGE_MEMORY_DB_PATH`` env), else ``InMemoryMemoryService`` (mirrors
    ``LocalSessions``).
    """

    def build_memory_service(self, db_path: str | None = None) -> Any:
        """Construct an in-memory ADK memory service for local execution."""
        path = (db_path or os.environ.get(MEMORY_DB_PATH_ENV, "")).strip()
        if not path:
            from google.adk.memory import InMemoryMemoryService

            return InMemoryMemoryService()
        from google.adk.memory import SqliteMemoryService

        return SqliteMemoryService(db_path=path)


class LocalScheduler(SchedulerSeam):
    """Local scheduler adapter (SEAM-6): an in-process VirtualClock."""

    def build_scheduler(self) -> Any:
        """Construct a deterministic virtual-clock scheduler for local execution."""
        from bridge.scheduler import VirtualClock

        return VirtualClock()


class LocalA2AClient(A2AClientSeam):
    """Local a2a_client adapter (SEAM-7, OUT-1): outbound client over an injected fake transport."""

    def build_client(self, transport: Any = None) -> Any:
        """Construct an in-memory outbound A2A client and transport for local execution."""
        from bridge.outbound import OutboundClient

        return OutboundClient(transport)


class LocalExtraction(ExtractionSeam):
    """Local extraction adapter — the deterministic fixture engine (SEAM-8)."""

    def __init__(self) -> None:
        self._engine = FixtureExtractor()

    async def extract(self, artifact_name: str) -> Any:
        """Run deterministic local document extraction over bytes and filename."""
        return await self._engine.extract(artifact_name)


class LocalEmailIngress(EmailIngressSeam):
    """Local email_ingress adapter (S0-arch-13): a landing directory.

    Root is the ``root`` ctor arg, else ``BRIDGE_EMAIL_DIR`` — resolved on FIRST USE
    (``get_adapter`` builds every local adapter with no env, so construction never
    raises); neither set → ``RuntimeError`` naming the env, never a silent default.

    Layout: ``inbox/<key>.eml``, ``attachments/<key>/<index>/<filename>``,
    ``processed/<key>.json``. Every write is create-only and torn-write safe: the
    bytes go to a temp file in the destination dir, then ``os.link`` publishes it
    (``FileExistsError`` = a duplicate), so a half-written file never looks landed.
    Keys / filenames are validated before any I/O and every resolved path is
    re-checked for containment (the S0-sec-1 ``read_asset`` posture).
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root_arg = root

    def _root(self) -> Path:
        raw = self._root_arg or os.environ.get(EMAIL_DIR_ENV, "").strip()
        if not raw:
            raise RuntimeError(
                f"local email_ingress requires a landing directory ({EMAIL_DIR_ENV} "
                "env or root=); refusing a silent default."
            )
        return Path(raw).resolve()

    def _contained(self, *parts: str) -> Path:
        root = self._root()
        path = root.joinpath(*parts).resolve()
        if root not in path.parents:
            raise ValueError(f"email_ingress path {'/'.join(parts)!r} escapes the root")
        return path

    @staticmethod
    def _create_only(path: Path, data: bytes) -> bool:
        import tempfile

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return False
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".landing-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                return False
            return True
        finally:
            os.unlink(tmp)

    def land(self, key: str, raw: bytes) -> bool:
        """Store an inbound raw MIME email message in the local ingress directory."""
        validate_landing_key(key)
        return self._create_only(self._contained("inbox", f"{key}.eml"), raw)

    def pending(self) -> list[str]:
        """List pending message IDs in the local email ingress queue."""
        import logging
        import re

        from ..protocols import EMAIL_KEY_PATTERN

        inbox = self._root() / "inbox"
        if not inbox.is_dir():
            return []
        done = self._root() / "processed"
        rows = []
        for child in inbox.iterdir():
            if child.name.startswith(".landing-"):
                continue  # an in-flight temp file, not a landed message
            key = child.name.removesuffix(".eml")
            if child.suffix != ".eml" or not re.fullmatch(EMAIL_KEY_PATTERN, key):
                logging.getLogger("bridge.seams.local").warning(
                    "email_ingress: skipping unexpected inbox entry %r", child.name
                )
                continue
            if (done / f"{key}.json").exists():
                continue
            rows.append((child.stat().st_mtime_ns, key))
        return [key for _, key in sorted(rows)]

    def read(self, key: str) -> bytes:
        """Read the raw MIME bytes for a message ID from the local ingress store."""
        validate_landing_key(key)
        path = self._contained("inbox", f"{key}.eml")
        if not path.exists():
            raise KeyError(key)
        return path.read_bytes()

    def put_attachment(
        self, key: str, index: int, filename: str, data: bytes, mime_type: str
    ) -> str:
        """Store an extracted MIME attachment in the local ingress attachment store."""
        validate_landing_key(key)
        validate_attachment_slot(index, filename)
        path = self._contained("attachments", key, str(index), filename)
        self._create_only(path, data)  # existing → same uri (idempotent)
        # Unencoded on purpose: the resolver reads ``urlsplit(uri).path`` verbatim,
        # and the validated filename carries no ``?`` / ``#``.
        return f"file://{path.as_posix()}"

    def mark_processed(self, key: str, outcome: dict) -> bool:
        """Mark an inbound email message ID as processed in the local ingress store."""
        import json

        validate_landing_key(key)
        body = json.dumps(outcome, sort_keys=True).encode()
        return self._create_only(self._contained("processed", f"{key}.json"), body)

    def processed(self, key: str) -> dict | None:
        """List all processed message IDs in the local email ingress store."""
        import json

        validate_landing_key(key)
        path = self._contained("processed", f"{key}.json")
        if not path.exists():
            return None
        return json.loads(path.read_text())


class LocalAuditVault(AuditVaultPort):
    """Local audit_vault adapter (Zone 3): in-memory WORM vault or GCS when bucket is supplied."""

    def build_vault(self, bucket_name: str | None = None) -> Any:
        """Construct an in-memory Audit Vault (or GCS vault when a bucket is explicitly configured)."""
        from bridge.vault import (
            VAULT_BUCKET_ENV,
            GcpAuditVaultStore,
            InMemoryAuditVaultStore,
        )

        bucket = (bucket_name or os.environ.get(VAULT_BUCKET_ENV, "")).strip()
        if bucket:
            return GcpAuditVaultStore(bucket)
        return InMemoryAuditVaultStore()


class LocalOperationalStore(OperationalStorePort):
    """Local operational_store adapter (Zone 3–4): in-memory or SQLite-backed bi-temporal store."""

    def build_operational_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed OperationalStore for local execution."""
        from bridge.operational_store import (
            _SQL_CACHE_LOCK,
            _SQL_STORE_CACHE,
            OPERATIONAL_DB_ENV,
            InMemoryOperationalStore,
            SqlOperationalStore,
        )

        dsn = (
            db_url
            or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        )
        if not dsn:
            return InMemoryOperationalStore()
        require_async_driver(dsn)
        with _SQL_CACHE_LOCK:
            cached = _SQL_STORE_CACHE.get(dsn)
            if cached is None:
                cached = SqlOperationalStore(dsn)
                _SQL_STORE_CACHE[dsn] = cached
        return cached


class LocalActionInbox(ActionInboxPort):
    """Local action_inbox adapter (Zone 5): in-memory singleton or SQLite-backed inbox store."""

    def build_action_inbox_store(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed ActionInboxStore for local execution."""
        import bridge.a2ui_inbox as inbox_mod
        from bridge.a2ui_inbox import (
            _SQL_INBOX_CACHE,
            _SQL_INBOX_LOCK,
            INBOX_DB_ENV,
            InMemoryActionInboxStore,
            SqlActionInboxStore,
        )

        mode = os.environ.get("BRIDGE_INBOX_STORE_MODE", "").strip().lower()
        if mode == "memory" and not db_url:
            if inbox_mod._DEFAULT_INBOX_STORE is None:
                inbox_mod._DEFAULT_INBOX_STORE = InMemoryActionInboxStore()
            return inbox_mod._DEFAULT_INBOX_STORE

        dsn = (
            db_url
            or os.environ.get(INBOX_DB_ENV, "").strip()
            or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        )
        if dsn:
            require_async_driver(dsn)
            with _SQL_INBOX_LOCK:
                cached = _SQL_INBOX_CACHE.get(dsn)
                if cached is None:
                    cached = SqlActionInboxStore(dsn)
                    _SQL_INBOX_CACHE[dsn] = cached
            return cached

        if inbox_mod._DEFAULT_INBOX_STORE is None:
            inbox_mod._DEFAULT_INBOX_STORE = InMemoryActionInboxStore()
        return inbox_mod._DEFAULT_INBOX_STORE


class LocalAgentGym(AgentGymPort):
    """Local agent_gym adapter (Zone 6): in-memory singleton or SQLite-backed Agent Gym engine."""

    def build_agent_gym(self, db_url: str | None = None) -> Any:
        """Construct an in-memory or SQL-backed AgentGymEngine for local execution."""
        import bridge.agent_gym.sql_store as gym_sql
        from bridge.agent_gym.engine import InMemoryAgentGymEngine
        from bridge.agent_gym.models import (
            DATABASE_URL_ENV,
            GYM_DB_ENV,
            OPERATIONAL_DB_ENV,
        )
        from bridge.agent_gym.sql_store import (
            _SQL_GYM_CACHE,
            _SQL_GYM_LOCK,
            SqlAgentGymEngine,
        )

        dsn = (
            db_url
            or os.environ.get(GYM_DB_ENV, "").strip()
            or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
            or os.environ.get(DATABASE_URL_ENV, "").strip()
        )
        if dsn:
            require_async_driver(dsn)
            with _SQL_GYM_LOCK:
                cached = _SQL_GYM_CACHE.get(dsn)
                if cached is None:
                    cached = SqlAgentGymEngine(dsn)
                    _SQL_GYM_CACHE[dsn] = cached
            return cached

        if gym_sql._DEFAULT_GYM_ENGINE is None:
            gym_sql._DEFAULT_GYM_ENGINE = InMemoryAgentGymEngine()
        return gym_sql._DEFAULT_GYM_ENGINE


LOCAL_ADAPTERS: dict[Port, type[SeamABC]] = {
    Port.sessions: LocalSessions,
    Port.task_store: LocalTaskStore,
    Port.exchange_store: LocalExchangeStore,
    Port.skill_registry: LocalSkillRegistry,
    Port.memory_bank: LocalMemoryBank,
    Port.scheduler: LocalScheduler,
    Port.a2a_client: LocalA2AClient,
    Port.extraction: LocalExtraction,
    Port.email_ingress: LocalEmailIngress,
    Port.audit_vault: LocalAuditVault,
    Port.operational_store: LocalOperationalStore,
    Port.action_inbox: LocalActionInbox,
    Port.agent_gym: LocalAgentGym,
}
