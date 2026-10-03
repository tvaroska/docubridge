"""Seam mode resolution from environment."""

import os

from .seam import Seam, SeamMode, seam_env_var


def resolve_mode(seam: Seam) -> SeamMode:
    """Resolve the adapter mode for a seam.

    Per-seam override (BRIDGE_SEAM_<NAME>) wins; blank/unset falls through
    to global (BRIDGE_SEAM_MODE); global default is 'local'.
    """
    # per-seam override wins; blank/unset falls through to global
    raw = os.environ.get(seam_env_var(seam), "").strip()
    if not raw:
        raw = os.environ.get("BRIDGE_SEAM_MODE", "").strip()
    if not raw:
        return SeamMode.local
    try:
        return SeamMode(raw)
    except ValueError as e:
        raise ValueError(
            f"invalid seam mode {raw!r} for {seam.value}; expected 'local' or 'gcp'"
        ) from e
