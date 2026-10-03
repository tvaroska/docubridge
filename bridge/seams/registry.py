"""Seam adapter registry."""

from .gcp import GCP_ADAPTERS
from .local import LOCAL_ADAPTERS
from .protocols import SeamABC
from .seam import Seam, SeamMode


def get_adapter(seam: Seam, mode: SeamMode | str) -> SeamABC:
    """Get an adapter instance for the given seam and mode.

    ``local`` always resolves. In ``gcp`` mode the seams implemented so far —
    the shared stores (sessions / task_store / exchange_store, P7-2), the GCS
    skill_registry (P7-3), the Cloud Tasks scheduler (P7-4), the Agent-Identity
    a2a_client (P7-5), the Gemini extraction engine (P7-6), and the GCS
    email_ingress landing bucket (S0-arch-13) — dispatch to
    ``GCP_ADAPTERS``; only ``memory_bank`` (deferred, empty by design) still raises
    ``NotImplementedError``.
    """
    mode = SeamMode(mode)
    if mode is SeamMode.local:
        return LOCAL_ADAPTERS[seam]()
    if seam in GCP_ADAPTERS:
        return GCP_ADAPTERS[seam]()
    raise NotImplementedError(f"gcp adapter for {seam.value} lands at P7")
