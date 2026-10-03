"""Agent-Console session registry (P6-3) — the console's own session index.

The Agent Console (the GUI form of the FE-1 CLI driver) starts a run *from a
prepared request*, then reads that run's live/terminal state back to drive a
session list + a per-turn viewer + an SSE stream. That per-run record must survive
*between* HTTP requests (start now, poll/stream later), so it lives here.

**Not a seam (Decision, §2c of the plan).** Mirroring ``bridge/portal/store.py``
Decision 2b / ``bridge/edge/artifacts.py`` Decision 1 (a native backend swap, not a
managed ``Seam``): the console registry is a **local default + a documented durable
swap point**, NOT an entry in the ``Seam`` enum. No
``@pytest.mark.seam`` here; ``bridge/seams/seam.py`` is untouched.

**P7 durable swap.** ``ConsoleSessionRecord`` is a plain serializable value (the
background ``asyncio.Task`` handle is kept out of it — see ``server.py``), so P7
reuses the P4 ``DatabaseSessionService`` / Cloud SQL to persist it durably. Swap this
in-memory implementation, keep the ``ConsoleStore`` protocol.

Isolation (§2b): imports stdlib only; NEVER ``bridge/`` — the console reaches the
Bridge by card URL alone (like ``agents/address/driver.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ConsoleSessionRecord:
    """One console session: the console handle, the ADK run triple, the prepared-request
    key, a ``running|parked|done|error`` status, and the last observed run projection.

    ``turn`` holds the last observed ``ExchangeTurn.model_dump(mode="json")`` (the ledger
    the agent was handed); ``rounds`` is the gate round counter ("reasoning" depth);
    ``satisfied`` is ``is_satisfied(turn.status)`` (the Sense-B check — read from the
    gate, never re-decided by the console, §2a). The background task handle is kept in a
    separate in-memory map in the app so this record stays a plain serializable value.
    """

    session_id: str            # the console handle (== ADK session_id for simplicity)
    prepared_key: str
    app_name: str = "address"  # run_collection hard-codes this
    user_id: str = "address-driver"
    status: str = "running"    # running | parked | done | error
    turn: dict | None = None   # last observed ExchangeTurn (model_dump(mode="json"))
    rounds: int = 0
    satisfied: bool = False
    error: str | None = None


class ConsoleStore(Protocol):
    """The persistence seam-in-waiting for console sessions (native swap, §2c)."""

    def create(self, rec: ConsoleSessionRecord) -> None: ...
    def get(self, session_id: str) -> ConsoleSessionRecord: ...  # KeyError -> host 404
    def put(self, rec: ConsoleSessionRecord) -> None: ...
    def list(self) -> list[ConsoleSessionRecord]: ...


class InMemoryConsoleStore:
    """Local default: an in-process dict of session records (insertion-ordered).

    P7 swaps this for a durable store (P4 ``DatabaseSessionService`` / Cloud SQL); the
    ``ConsoleStore`` protocol is the swap boundary. NOT a managed ``Seam`` (§2c).
    """

    def __init__(self) -> None:
        self._records: dict[str, ConsoleSessionRecord] = {}

    def create(self, rec: ConsoleSessionRecord) -> None:
        self._records[rec.session_id] = rec

    def get(self, session_id: str) -> ConsoleSessionRecord:
        return self._records[session_id]  # KeyError -> host maps 404

    def put(self, rec: ConsoleSessionRecord) -> None:
        self._records[rec.session_id] = rec

    def list(self) -> list[ConsoleSessionRecord]:
        # Insertion order (dict preserves it); newest last. The UI reverses for a
        # newest-first list.
        return list(self._records.values())
