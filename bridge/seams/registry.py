"""Seam adapter registry."""

from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .protocols import SeamABC
from .seam import Seam, SeamMode


def get_adapter(seam: Seam, mode: SeamMode | str) -> SeamABC:
    """Get an adapter instance for the given seam and mode.

    ``local`` and ``gcp`` resolve all nine seams through ``LOCAL_ADAPTERS`` and
    ``GCP_ADAPTERS`` respectively (including ``GcpSessions`` on Agent Engine or
    Cloud SQL, and ``GcpMemoryBank`` on Agent Engine Memory Bank).
    """
    mode = SeamMode(mode)
    if mode is SeamMode.local:
        return LOCAL_ADAPTERS[seam]()
    if seam in GCP_ADAPTERS:
        return GCP_ADAPTERS[seam]()
    raise NotImplementedError(f"gcp adapter for {seam.value} lands at P7")
