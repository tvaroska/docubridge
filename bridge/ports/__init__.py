"""Hexagonal Ports & Adapters across all 14 managed-service boundaries.

Exposes the Hexagonal Architecture `Port`, `PortMode` (`AdapterMode`), `PortABC`,
`*Port` protocols, `resolve_port_mode` (`resolve_mode`), and `get_port_adapter`
(`get_adapter`).
"""

from .config import GLOBAL_PORT_ENV, resolve_mode, resolve_port_mode
from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .port import (
    PORT_ENV_PREFIX,
    PORT_NAMES,
    AdapterMode,
    Port,
    PortMode,
    is_valid_port,
    port_env_var,
)
from .protocols import (
    EMAIL_KEY_PATTERN,
    MAX_ATTACHMENT_NAME,
    MAX_ATTACHMENT_NAME_BYTES,
    A2AClientPort,
    ActionInboxPort,
    AgentGymPort,
    AuditVaultPort,
    EmailIngressPort,
    ExchangeStorePort,
    ExtractionPort,
    KnowledgeCatalogPort,
    MemoryBankPort,
    OperationalStorePort,
    PortABC,
    SchedulerPort,
    SessionsPort,
    SkillRegistryPort,
    TaskStorePort,
    validate_attachment_slot,
    validate_landing_key,
)
from .registry import get_adapter, get_port_adapter

__all__ = [
    "EMAIL_KEY_PATTERN",
    "GCP_ADAPTERS",
    "GLOBAL_PORT_ENV",
    "LOCAL_ADAPTERS",
    "MAX_ATTACHMENT_NAME",
    "MAX_ATTACHMENT_NAME_BYTES",
    "PORT_ENV_PREFIX",
    "PORT_NAMES",
    "A2AClientPort",
    "ActionInboxPort",
    "AdapterMode",
    "AgentGymPort",
    "AuditVaultPort",
    "EmailIngressPort",
    "ExchangeStorePort",
    "ExtractionPort",
    "KnowledgeCatalogPort",
    "MemoryBankPort",
    "OperationalStorePort",
    "Port",
    "PortABC",
    "PortMode",
    "SchedulerPort",
    "SessionsPort",
    "SkillRegistryPort",
    "TaskStorePort",
    "get_adapter",
    "get_port_adapter",
    "is_valid_port",
    "port_env_var",
    "resolve_mode",
    "resolve_port_mode",
    "validate_attachment_slot",
    "validate_landing_key",
]
