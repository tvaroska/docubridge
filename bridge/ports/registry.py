"""Adapter factory (`get_adapter(port, mode)` / `get_port_adapter(port, mode)`)."""

from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .port import Port, PortMode
from .protocols import PortABC


def get_port_adapter(port: Port, mode: PortMode | str) -> PortABC:
    """Instantiate the adapter for `(port, mode)`."""
    if not isinstance(port, Port):
        raise ValueError(f"unknown port: {port!r}")
    mode_enum = mode if isinstance(mode, PortMode) else PortMode(str(mode).lower())
    table = LOCAL_ADAPTERS if mode_enum is PortMode.local else GCP_ADAPTERS
    cls = table[port]
    return cls()


get_adapter = get_port_adapter
