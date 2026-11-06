"""Port enumeration and adapter mode definitions (Hexagonal Ports & Adapters).

Each port is a boundary between core Bridge logic and a managed GCP service (or its
local in-memory/SQLite/filesystem counterpart). Selecting an adapter is a per-port or
global environment switch (`BRIDGE_PORT_<NAME>` / `BRIDGE_PORT_MODE`), never a code fork.
"""

from enum import Enum


class Port(str, Enum):
    """The 14 hexagonal ports — every boundary that swaps between local and GCP."""

    # Core runtime & orchestration ports (Ring 2)
    sessions = "sessions"
    task_store = "task_store"
    exchange_store = "exchange_store"
    skill_registry = "skill_registry"
    memory_bank = "memory_bank"
    scheduler = "scheduler"
    a2a_client = "a2a_client"
    extraction = "extraction"
    email_ingress = "email_ingress"

    # Enterprise Data Architecture ports (Zones 3-6)
    audit_vault = "audit_vault"
    operational_store = "operational_store"
    action_inbox = "action_inbox"
    agent_gym = "agent_gym"
    knowledge_catalog = "knowledge_catalog"


class PortMode(str, Enum):
    """Execution mode for a port adapter (`local` vs. `gcp`)."""

    local = "local"
    gcp = "gcp"


AdapterMode = PortMode

PORT_ENV_PREFIX = "BRIDGE_PORT_"


def port_env_var(port: Port) -> str:
    """Per-port env var name, e.g. `BRIDGE_PORT_SESSIONS`."""
    return f"{PORT_ENV_PREFIX}{port.value.upper()}"


PORT_NAMES: frozenset[str] = frozenset(p.value for p in Port)


def is_valid_port(name: str) -> bool:
    """Return True if `name` is a known `Port` identifier."""
    return name in PORT_NAMES
