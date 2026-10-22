"""Port / Seam adapter mode resolution from environment."""

import os

from .seam import Port, PortMode, Seam, SeamMode, port_env_var, seam_env_var


def resolve_mode(seam: Port | Seam) -> PortMode:
    """Resolve the adapter mode for a port (with backwards-compatible ``BRIDGE_SEAM_*`` support).

    Precedence order:
    1. Per-port override (``BRIDGE_PORT_<NAME>``)
    2. Legacy per-seam override (``BRIDGE_SEAM_<NAME>``)
    3. Global port mode (``BRIDGE_PORT_MODE``)
    4. Legacy global seam mode (``BRIDGE_SEAM_MODE``)
    5. Default: ``'local'``
    """
    raw = os.environ.get(port_env_var(seam), "").strip()
    if not raw:
        raw = os.environ.get(seam_env_var(seam), "").strip()
    if not raw:
        raw = os.environ.get("BRIDGE_PORT_MODE", "").strip()
    if not raw:
        raw = os.environ.get("BRIDGE_SEAM_MODE", "").strip()
    if not raw:
        return SeamMode.local
    try:
        return SeamMode(raw)
    except ValueError as e:
        raise ValueError(
            f"invalid port/seam mode {raw!r} (invalid seam mode {raw!r}) for {seam.value}; expected 'local' or 'gcp'"
        ) from e


resolve_port_mode = resolve_mode
