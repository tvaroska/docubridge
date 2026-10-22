"""Port / Seam enum and adapter mode configuration (Hexagonal Ports & Adapters)."""

from enum import Enum


class Port(str, Enum):
    """The 13 managed-service boundaries (Ports) in Document Bridge (one local + one GCP adapter each)."""

    sessions = "sessions"
    task_store = "task_store"
    exchange_store = "exchange_store"
    skill_registry = "skill_registry"
    memory_bank = "memory_bank"
    scheduler = "scheduler"
    a2a_client = "a2a_client"
    extraction = "extraction"
    email_ingress = "email_ingress"
    audit_vault = "audit_vault"
    operational_store = "operational_store"
    action_inbox = "action_inbox"
    agent_gym = "agent_gym"


# Backwards-compatible alias for existing callers and tests.
Seam = Port


class PortMode(str, Enum):
    """Port adapter mode (local vs GCP)."""

    local = "local"
    gcp = "gcp"


AdapterMode = PortMode
SeamMode = PortMode

PORT_ENV_PREFIX = "BRIDGE_PORT_"
SEAM_ENV_PREFIX = "BRIDGE_SEAM_"


def port_env_var(port: Port) -> str:
    """Return the primary environment variable name for a port's adapter mode override.

    Example: Port.task_store -> "BRIDGE_PORT_TASK_STORE"
    """
    return PORT_ENV_PREFIX + port.value.upper()


def seam_env_var(seam: Seam) -> str:
    """Return the legacy environment variable name for a seam's mode override.

    Example: Seam.task_store -> "BRIDGE_SEAM_TASK_STORE"
    """
    return SEAM_ENV_PREFIX + seam.value.upper()


PORT_NAMES: frozenset[str] = frozenset(p.value for p in Port)
SEAM_NAMES: frozenset[str] = PORT_NAMES


def is_valid_port(name: str) -> bool:
    """Check if a string is a valid port name."""
    return name in PORT_NAMES


def is_valid_seam(name: str) -> bool:
    """Check if a string is a valid port/seam name."""
    return name in SEAM_NAMES
