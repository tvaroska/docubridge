"""Local port adapters (in-memory, SQLite, and local filesystem)."""

from __future__ import annotations

from ..port import Port
from ..protocols import PortABC
from .enterprise import (
    LocalActionInbox,
    LocalAgentGym,
    LocalAuditVault,
    LocalKnowledgeCatalog,
    LocalOperationalStore,
)
from .services import (
    EMAIL_DIR_ENV,
    LocalA2AClient,
    LocalEmailIngress,
    LocalExtraction,
    LocalScheduler,
    LocalSkillRegistry,
)
from .stores import (
    DEFAULT_SQLITE_DSN,
    EXCHANGE_DB_URL_ENV,
    MEMORY_DB_PATH_ENV,
    LocalExchangeStore,
    LocalMemoryBank,
    LocalSessions,
    LocalTaskStore,
    require_async_driver,
)

LOCAL_ADAPTERS: dict[Port, type[PortABC]] = {
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
    Port.knowledge_catalog: LocalKnowledgeCatalog,
}

__all__ = [
    "DEFAULT_SQLITE_DSN",
    "EMAIL_DIR_ENV",
    "EXCHANGE_DB_URL_ENV",
    "LOCAL_ADAPTERS",
    "MEMORY_DB_PATH_ENV",
    "LocalA2AClient",
    "LocalActionInbox",
    "LocalAgentGym",
    "LocalAuditVault",
    "LocalEmailIngress",
    "LocalExchangeStore",
    "LocalExtraction",
    "LocalKnowledgeCatalog",
    "LocalMemoryBank",
    "LocalOperationalStore",
    "LocalScheduler",
    "LocalSessions",
    "LocalSkillRegistry",
    "LocalTaskStore",
    "require_async_driver",
]
