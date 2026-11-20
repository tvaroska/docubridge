"""Adapter factory (`get_adapter(port, mode)` / `get_port_adapter(port, mode)`)."""

from __future__ import annotations

from typing import Literal, overload

from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .port import Port, PortMode
from .protocols import (
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
)


@overload
def get_port_adapter(
    port: Literal[Port.sessions], mode: PortMode | str
) -> SessionsPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.task_store], mode: PortMode | str
) -> TaskStorePort: ...


@overload
def get_port_adapter(
    port: Literal[Port.exchange_store], mode: PortMode | str
) -> ExchangeStorePort: ...


@overload
def get_port_adapter(
    port: Literal[Port.skill_registry], mode: PortMode | str
) -> SkillRegistryPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.memory_bank], mode: PortMode | str
) -> MemoryBankPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.scheduler], mode: PortMode | str
) -> SchedulerPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.a2a_client], mode: PortMode | str
) -> A2AClientPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.extraction], mode: PortMode | str
) -> ExtractionPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.email_ingress], mode: PortMode | str
) -> EmailIngressPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.audit_vault], mode: PortMode | str
) -> AuditVaultPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.operational_store], mode: PortMode | str
) -> OperationalStorePort: ...


@overload
def get_port_adapter(
    port: Literal[Port.action_inbox], mode: PortMode | str
) -> ActionInboxPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.agent_gym], mode: PortMode | str
) -> AgentGymPort: ...


@overload
def get_port_adapter(
    port: Literal[Port.knowledge_catalog], mode: PortMode | str
) -> KnowledgeCatalogPort: ...


@overload
def get_port_adapter(port: Port, mode: PortMode | str) -> PortABC: ...


def get_port_adapter(port: Port, mode: PortMode | str) -> PortABC:
    """Instantiate the adapter for `(port, mode)`."""
    if not isinstance(port, Port):
        raise ValueError(f"unknown port: {port!r}")
    mode_enum = mode if isinstance(mode, PortMode) else PortMode(str(mode).lower())
    table = LOCAL_ADAPTERS if mode_enum is PortMode.local else GCP_ADAPTERS
    cls = table[port]
    return cls()


get_adapter = get_port_adapter
