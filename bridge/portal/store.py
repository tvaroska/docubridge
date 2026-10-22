"""Provider-Portal session store (P6-2) — the durable-friendly ``PathBState`` park.

The portal drives ``bridge.pathb.step`` across ``INPUT_REQUIRED`` parks (resubmit /
HITL), so the (small, serializable) ``PathBState`` and the active park must survive
*between* HTTP requests. That persistence lives here.

**Not a seam (Decision, §2b of the plan).** Mirroring ``bridge/edge/artifacts.py``
Decision 1 (the ADK ``ArtifactService`` is a native backend swap, not a managed
``Seam``): the portal store is a **local default + a documented durable swap point**,
NOT an entry in the ``Seam`` enum. No ``@pytest.mark.seam`` here.

**P7 durable swap.** ``PathBState`` is a small serializable value
(``candidate_doctypes``/``ledger`` are tuples of pydantic models; ``attempts`` /
``max_resubmissions`` ints; ``thresholds`` a dataclass), so P7 reuses the P4
``DatabaseSessionService`` / Cloud SQL to persist a session record durably. Swap this
in-memory implementation, keep the ``PortalStore`` protocol.

Isolation: imports ``contract/`` + ``bridge.*`` + stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bridge.disposition import DispositionThresholds
from bridge.pathb import PathBState, PathBStep
from contract.enums import LifecycleStatus
from contract.wire import LedgerEntry


@dataclass
class PortalSessionRecord:
    """One portal session: its exchange context, the process policy knobs, the active
    document slot's ``PathBState``/park, and the finalized ledger across slots.

    ``active_state``/``last_step`` hold the *current* (non-terminal) document slot —
    a RESUBMIT or HITL park to continue/resolve. ``ledger`` accumulates finalized
    entries (accepted / rejected / escalated) across slots; a terminal step clears the
    active slot so the next upload starts fresh.
    """

    session_id: str
    context_id: str
    process: str
    candidate_doctypes: tuple[str, ...]
    max_resubmissions: int
    thresholds: DispositionThresholds
    active_state: PathBState | None = (
        None  # current doc slot (None => start a fresh slot)
    )
    last_step: PathBStep | None = None  # the active non-terminal park (RESUBMIT/HITL)
    ledger: tuple[LedgerEntry, ...] = ()  # finalized entries across slots
    # SLA follow-up lifecycle (P6-5). The time-warp host stamps the fired ladder step here
    # (via ``dataclasses.replace``); the dashboard read-model prefers it over the cosmetic
    # ledger derivation. Default ``on_track`` keeps every existing construction unchanged.
    lifecycle: LifecycleStatus = LifecycleStatus.on_track


class PortalStore(Protocol):
    """The persistence seam-in-waiting for portal sessions (native swap, §2b)."""

    def create(self, rec: PortalSessionRecord) -> None:
        """Create and persist a new portal upload session record."""
        ...

    def get(self, session_id: str) -> PortalSessionRecord:
        """Fetch a portal upload session by session ID, or None if not found."""
        ...  # KeyError -> host maps 404

    def put(self, rec: PortalSessionRecord) -> None:
        """Update an existing portal upload session record."""
        ...

    def list(self) -> list[PortalSessionRecord]:
        """List all portal upload session records."""
        ...  # P6-4: Ops Dashboard read-model source


class InMemoryPortalStore:
    """Local default: an in-process dict of session records.

    P7 swaps this for a durable store (P4 ``DatabaseSessionService`` / Cloud SQL); the
    ``PortalStore`` protocol is the swap boundary. NOT a managed ``Seam`` (§2b).
    """

    def __init__(self) -> None:
        self._records: dict[str, PortalSessionRecord] = {}

    def create(self, rec: PortalSessionRecord) -> None:
        """Create and store a new portal upload session in memory."""
        self._records[rec.session_id] = rec

    def get(self, session_id: str) -> PortalSessionRecord:
        """Fetch a portal upload session from memory by session ID."""
        return self._records[session_id]  # KeyError -> host maps 404

    def put(self, rec: PortalSessionRecord) -> None:
        """Save or overwrite a portal upload session in memory."""
        self._records[rec.session_id] = rec

    def list(self) -> list[PortalSessionRecord]:
        """P6-4: list all sessions (dict preserves insertion order)."""
        return list(self._records.values())
