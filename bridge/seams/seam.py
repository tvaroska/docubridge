"""Seam enum and mode configuration."""

from enum import Enum


class Seam(str, Enum):
    """The managed-service boundaries in the Bridge (one local + one GCP adapter each)."""
    sessions = "sessions"
    task_store = "task_store"
    exchange_store = "exchange_store"
    skill_registry = "skill_registry"
    memory_bank = "memory_bank"
    scheduler = "scheduler"
    a2a_client = "a2a_client"
    extraction = "extraction"
    email_ingress = "email_ingress"


class SeamMode(str, Enum):
    """Seam adapter mode (local vs GCP)."""
    local = "local"
    gcp = "gcp"


SEAM_ENV_PREFIX = "BRIDGE_SEAM_"


def seam_env_var(seam: Seam) -> str:
    """Return the environment variable name for a seam's mode override.

    Example: Seam.task_store -> "BRIDGE_SEAM_TASK_STORE"
    """
    return SEAM_ENV_PREFIX + seam.value.upper()


SEAM_NAMES: frozenset[str] = frozenset(s.value for s in Seam)


def is_valid_seam(name: str) -> bool:
    """Check if a string is a valid seam name."""
    return name in SEAM_NAMES
