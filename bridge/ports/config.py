"""Environment-driven port mode resolution (`BRIDGE_PORT_<NAME>` -> `BRIDGE_PORT_MODE` -> `local`)."""

import os

from .port import Port, PortMode, port_env_var

GLOBAL_PORT_ENV = "BRIDGE_PORT_MODE"


def resolve_port_mode(port: Port) -> PortMode:
    """Resolve `PortMode` for `port` from environment variables.

    Precedence:
    1. `BRIDGE_PORT_<NAME>` (per-port override)
    2. `BRIDGE_PORT_MODE` (global override)
    3. `PortMode.local` (default)

    Raises `ValueError` on an unrecognized mode value (fail loud on typo).
    """
    raw = os.environ.get(port_env_var(port), "").strip()
    if not raw:
        raw = os.environ.get(GLOBAL_PORT_ENV, "").strip()
    if not raw:
        return PortMode.local
    try:
        return PortMode(raw.lower())
    except ValueError as exc:
        raise ValueError(
            f"invalid port mode {raw!r} for {port.value}; expected 'local' or 'gcp'"
        ) from exc


resolve_mode = resolve_port_mode
