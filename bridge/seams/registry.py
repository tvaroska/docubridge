"""Port / Seam adapter registry."""

from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .protocols import PortABC
from .seam import Port, PortMode


def get_adapter(seam: Port, mode: PortMode | str) -> PortABC:
    """Get an adapter instance for the given port/seam and mode.

    ``local`` and ``gcp`` resolve all 13 ports through ``LOCAL_ADAPTERS`` and
    ``GCP_ADAPTERS`` respectively.
    """
    mode = PortMode(mode)
    if mode is PortMode.local:
        return LOCAL_ADAPTERS[seam]()
    if seam in GCP_ADAPTERS:
        return GCP_ADAPTERS[seam]()
    raise NotImplementedError(f"gcp adapter for {seam.value} lands at P7")


get_port_adapter = get_adapter
