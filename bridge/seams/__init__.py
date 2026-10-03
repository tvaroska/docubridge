"""Bridge seams — managed-service boundaries with local + GCP adapters."""

from .config import resolve_mode
from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .protocols import (
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
)
from .registry import get_adapter
from .seam import (
    SEAM_ENV_PREFIX,
    SEAM_NAMES,
    Seam,
    SeamMode,
    is_valid_seam,
    seam_env_var,
)

__all__ = [
    "GCP_ADAPTERS",
    "LOCAL_ADAPTERS",
    "SEAM_ENV_PREFIX",
    "SEAM_NAMES",
    "A2AClientSeam",
    "EmailIngressSeam",
    "ExchangeStoreSeam",
    "ExtractionSeam",
    "MemoryBankSeam",
    "SchedulerSeam",
    # Enum + mode
    "Seam",
    # ABCs
    "SeamABC",
    "SeamMode",
    "SessionsSeam",
    "SkillRegistrySeam",
    "TaskStoreSeam",
    "get_adapter",
    "is_valid_seam",
    # Registry + config
    "resolve_mode",
    "seam_env_var",
]
