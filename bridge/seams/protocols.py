"""Seam ABCs — placeholder interfaces, domain methods added per-phase."""

import abc
from typing import TYPE_CHECKING, ClassVar

from .seam import Seam

if TYPE_CHECKING:
    from a2a.server.tasks import PushNotificationConfigStore, TaskStore
    from google.adk.memory import BaseMemoryService
    from google.adk.sessions import BaseSessionService

    from bridge.edge.skills import SkillMeta
    from bridge.exchange_store import ExchangeStore
    from bridge.outbound import A2AClient
    from bridge.scheduler import Scheduler
    from contract.extraction import ExtractionResult


class SeamABC(abc.ABC):
    """Common base for all seam adapters."""
    seam: ClassVar[Seam]


class SessionsSeam(SeamABC):
    """Agent Platform Sessions (Vertex Agent Engine ``VertexAiSessionService`` or Cloud SQL ``DatabaseSessionService``)."""
    seam = Seam.sessions

    @abc.abstractmethod
    def build_session_service(self, db_url: str | None = None) -> "BaseSessionService":
        """Return an ADK ``BaseSessionService`` (Agent Engine or durable SQL when configured)."""
        raise NotImplementedError


class TaskStoreSeam(SeamABC):
    """Task store (Cloud SQL)."""
    seam = Seam.task_store

    @abc.abstractmethod
    def build_task_store(self, db_url: str | None = None) -> "TaskStore":
        """Return an a2a-sdk ``TaskStore`` (durable when a DSN is configured)."""
        raise NotImplementedError

    @abc.abstractmethod
    def build_push_config_store(
        self, db_url: str | None = None
    ) -> "PushNotificationConfigStore":
        """Return an a2a-sdk ``PushNotificationConfigStore`` (durable when a DSN is configured).

        Rides the task_store seam (Decision 1): both are a2a-sdk Database* classes
        over the same DSN (``BRIDGE_TASK_DB_URL``) and both become Cloud SQL at P7.
        """
        raise NotImplementedError


class ExchangeStoreSeam(SeamABC):
    """Exchange store — the materialized-Exchange record store (S0-arch-14).

    An Exchange is a derived view over its tasks until exchange-only state appears
    (``design/aggregate-model.md``); then it materializes into a stored record — today
    the RFP aggregate (emergent Collect, ``bridge.dashboard.board.RfpBoard``). Records
    are versioned JSON documents keyed by ``(kind, ref)``, carrying the exchange's A2A
    context token, written compare-and-set (``bridge.exchange_store``). Local = in
    memory, or sqlite when ``BRIDGE_EXCHANGE_DB_URL`` is set; GCP = Cloud SQL over the
    shared ``DATABASE_URL`` (never in memory — the session-affinity trap).
    """
    seam = Seam.exchange_store

    @abc.abstractmethod
    def build_exchange_store(self, db_url: str | None = None) -> "ExchangeStore":
        """Return an ``ExchangeStore`` (durable when a DSN is configured)."""
        raise NotImplementedError


class SkillRegistrySeam(SeamABC):
    """Skill registry. Local = a directory of Agent Skills folders; GCP = managed
    Skill Registry (GCS) at P7."""
    seam = Seam.skill_registry

    @abc.abstractmethod
    def list_skills(self) -> list["SkillMeta"]:
        """Return ``SkillMeta`` for every installed skill folder (the Agent Card's
        discovery source).

        A malformed folder is skipped with a logged reason (a WARNING on
        ``bridge.edge.skills``), never raised: one bad folder must not take down
        discovery. Both adapters share the parser and the tolerance
        (``bridge.edge.skills.listed_skill_meta``)."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_skill(self, name: str) -> "SkillMeta":
        """Return one installed skill's metadata (raises ``KeyError`` if absent)."""
        raise NotImplementedError

    @abc.abstractmethod
    def install_skill(
        self, name: str, skill_md: str, assets: dict[str, str] | None = None
    ) -> "SkillMeta":
        """Install/overwrite a skill folder at runtime and return its parsed
        ``SkillMeta`` (SKILL-4 — the no-redeploy path).

        A malformed upload (or a traversing name / asset path) raises ``ValueError``
        BEFORE anything is written, so a bad re-upload never overwrites a good skill
        (``bridge.edge.skills.validate_skill_upload``, identical on both adapters).
        Cross-skill state (a process's doctypes) is not checked here."""
        raise NotImplementedError

    @abc.abstractmethod
    def read_asset(self, name: str, rel: str) -> str:
        """Return the text of an asset inside an installed skill folder.

        ``rel`` is the skill-relative path a ``bridge-*`` frontmatter pointer
        declares (e.g. ``assets/schema.json``). This is how the extraction engine
        resolves a doctype's field schema through the seam instead of reading the
        filesystem directly. Raises ``KeyError`` when the skill or the asset is
        absent, ``ValueError`` when ``name``/``rel`` would escape the skill folder
        (the S0-sec-1 traversal defense, identical on both adapters).
        """
        raise NotImplementedError


class MemoryBankSeam(SeamABC):
    """Memory bank (Vertex Agent Engine ``VertexAiMemoryBankService`` or local ``SqliteMemoryService`` / ``InMemoryMemoryService``)."""
    seam = Seam.memory_bank

    @abc.abstractmethod
    def build_memory_service(
        self, db_path: str | None = None
    ) -> "BaseMemoryService":
        """Return an ADK ``BaseMemoryService`` (Vertex AI Memory Bank on GCP; SQLite or in-memory locally)."""
        raise NotImplementedError


class SchedulerSeam(SeamABC):
    """Scheduler (Cloud Tasks)."""
    seam = Seam.scheduler

    @abc.abstractmethod
    def build_scheduler(self) -> "Scheduler":
        """Return a Scheduler (in-process VirtualClock locally; Cloud Tasks at P7)."""
        raise NotImplementedError


class A2AClientSeam(SeamABC):
    """A2A client (outbound). Local = injected fake transport; GCP = Agent-Identity (P7)."""
    seam = Seam.a2a_client

    @abc.abstractmethod
    def build_client(self) -> "A2AClient":
        """Return an outbound A2A client (fake transport locally; Agent Identity at P7)."""
        raise NotImplementedError


class ExtractionSeam(SeamABC):
    """Extraction (Gemini / Document AI)."""
    seam = Seam.extraction

    @abc.abstractmethod
    async def extract(self, artifact_name: str) -> "ExtractionResult":
        """Extract normalized fields + disposition signals for a document artifact."""


# --------------------------------------------------------------------------- #
# email_ingress — the shared validation both adapters run BEFORE any I/O       #
# --------------------------------------------------------------------------- #

# A landing key is the 32-hex prefix of a sha256 (``bridge.email_ingress.landing_key``).
EMAIL_KEY_PATTERN = r"^[0-9a-f]{32}$"
MAX_ATTACHMENT_NAME = 128
MAX_ATTACHMENT_NAME_BYTES = 200  # under a filesystem NAME_MAX (255 bytes)


def validate_landing_key(key: str) -> str:
    """Return ``key`` when it is a 32-hex landing key, else raise ``ValueError``.

    Runs before any storage I/O on both adapters, so a crafted key can never name a
    path / object outside the landing layout (the S0-sec-1 traversal posture).
    """
    import re

    if not isinstance(key, str) or not re.fullmatch(EMAIL_KEY_PATTERN, key):
        raise ValueError(f"invalid landing key {key!r}: must match {EMAIL_KEY_PATTERN}")
    return key


def validate_attachment_slot(index: int, filename: str) -> None:
    """Reject a negative index or an unsanitized attachment filename (``ValueError``).

    The filename must already be sanitized (``bridge.email_ingress.mime``): a single
    path segment, no ``/`` / ``\\`` / NUL, not ``.``-led (so never ``..``), 1..128
    chars and at most 200 UTF-8 bytes (under a filesystem NAME_MAX), and no ``?`` /
    ``#`` (the returned uri must parse back to the same path —
    ``bridge.extraction.resolve`` reads a ``file://`` path with ``urlsplit``).
    """
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError(f"invalid attachment index {index!r}: must be an int >= 0")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > MAX_ATTACHMENT_NAME
        or len(filename.encode()) > MAX_ATTACHMENT_NAME_BYTES
        or any(ch in filename for ch in ("/", "\\", "\x00", "?", "#"))
        or filename.startswith(".")
    ):
        raise ValueError(f"invalid attachment filename {filename!r}")


class EmailIngressSeam(SeamABC):
    """Email ingress. Local = a landing directory; GCP = a GCS landing bucket.

    The deployed transport (M365 Graph change notifications,
    ``bridge.email_ingress.graph``) feeds ``land()``; its local analogue is the
    ``python -m bridge.email_ingress land file.eml`` CLI. Graph is NOT part of this
    ABC — the seam is the landing store, which IS the queue: ``pending()`` lists
    landed-but-unprocessed mail in landing order and ``mark_processed`` drains it.

    Layout (identical on both adapters): ``inbox/<key>.eml``,
    ``attachments/<key>/<index>/<filename>``, ``processed/<key>.json``. Every write is
    **create-only** (a re-delivery is a duplicate, never an overwrite), and every key /
    filename is validated before any I/O (``validate_landing_key`` /
    ``validate_attachment_slot``).
    """
    seam = Seam.email_ingress

    @abc.abstractmethod
    def land(self, key: str, raw: bytes) -> bool:
        """Store one raw RFC 5322 message create-only; ``False`` = already landed."""
        raise NotImplementedError

    @abc.abstractmethod
    def pending(self) -> list[str]:
        """Landed, not yet processed keys — in LANDING order (a later quote = a new version)."""
        raise NotImplementedError

    @abc.abstractmethod
    def read(self, key: str) -> bytes:
        """The landed raw message; ``KeyError`` when absent."""
        raise NotImplementedError

    @abc.abstractmethod
    def put_attachment(
        self, key: str, index: int, filename: str, data: bytes, mime_type: str
    ) -> str:
        """Store one attachment create-only + idempotently; return an EXTRACTION-
        RESOLVABLE uri (``file:///…/attachments/<key>/<i>/<filename>`` locally,
        ``gs://<bucket>/attachments/<key>/<i>/<filename>`` on GCP). The original
        filename is kept (one directory per index) so a fixture stem still resolves."""
        raise NotImplementedError

    @abc.abstractmethod
    def mark_processed(self, key: str, outcome: dict) -> bool:
        """Write the create-only JSON outcome marker; ``False`` = already marked."""
        raise NotImplementedError

    @abc.abstractmethod
    def processed(self, key: str) -> dict | None:
        """The stored outcome marker, or ``None`` when the key is not processed."""
        raise NotImplementedError
