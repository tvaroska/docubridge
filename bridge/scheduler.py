"""Virtual clock scheduler — the local adapter behind PORT-6.

An in-process, tick-based alarm store. Ticks are virtual-time integers; no wall
clock or asyncio is involved. Designed to match the Cloud Tasks shape (arm an
alarm to fire at a future time carrying a payload) so the P7 GCP adapter can
satisfy the same ``Scheduler`` protocol.

Design notes:
- **Fire order** is ``(fire_at, insertion)`` — stable and deterministic, so two
  alarms at the same tick fire in the order they were armed.
- **Idempotent re-arm** (same ``key`` overwrites) is the crash-safe property the
  wiki calls for (``bridge-proactive.md:16``); full persistence across a process
  restart is out of scope here and rides P5-4's persistent stores.
- **No callback invocation.** ``advance`` *returns* the fired alarms; P5-2 owns
  the dispatcher that reacts to a fired alarm and re-arms the next ladder step.
  This keeps the primitive minimal and the "zero compute between" contract P5-2's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Alarm:
    """A scheduled alarm. ``key`` is the caller's idempotency handle; ``payload``
    is opaque to the clock (P5-2 will put the ladder step / exchange id here)."""

    key: str
    fire_at: int
    payload: Any = None


@runtime_checkable
class Scheduler(Protocol):
    """The scheduler port's abstraction. Local = VirtualClock; GCP = Cloud Tasks (P7)."""

    def now(self) -> int:
        """Return the current UTC timestamp from the scheduler clock."""
        ...

    def schedule(self, key: str, fire_at: int, payload: Any = None) -> Alarm:
        """Schedule a callback to fire at a target UTC timestamp under a unique key."""
        ...

    def cancel(self, key: str) -> bool:
        """Cancel a previously scheduled callback by its key."""
        ...

    def pending(self) -> list[Alarm]:
        """Return all currently scheduled keys and their due timestamps."""
        ...

    def advance(self, ticks: int) -> list[Alarm]:
        """Advance the scheduler clock by a relative duration and run due callbacks."""
        ...

    def advance_to(self, tick: int) -> list[Alarm]:
        """Advance the scheduler clock to a target UTC timestamp and run due callbacks."""
        ...


class VirtualClock:
    """In-process virtual clock behind the scheduler port (PORT-6).

    Deterministic and idempotent: re-arming the same ``key`` replaces the pending
    alarm (crash-safe re-arm); advancing fires each due alarm exactly once, in
    ``(fire_at, insertion-order)`` order, and removes it so re-advancing never refires.
    """

    def __init__(self) -> None:
        self._now = 0
        self._alarms: dict[str, Alarm] = {}
        self._seq: dict[
            str, int
        ] = {}  # insertion order per live key, for stable tiebreak
        self._counter = 0

    def now(self) -> int:
        """Return the current virtual UTC timestamp."""
        return self._now

    def schedule(self, key: str, fire_at: int, payload: Any = None) -> Alarm:
        # fire_at in the past-or-now relative to _now is allowed: it fires on the next advance.
        """Register a callback at a due timestamp on the deterministic virtual clock."""
        alarm = Alarm(key=key, fire_at=fire_at, payload=payload)
        if key not in self._seq:
            self._seq[key] = self._counter
            self._counter += 1
        self._alarms[key] = alarm  # idempotent re-arm: same key overwrites
        return alarm

    def cancel(self, key: str) -> bool:
        """Cancel a pending callback on the virtual clock by its key."""
        self._seq.pop(key, None)
        return self._alarms.pop(key, None) is not None

    def pending(self) -> list[Alarm]:
        """Return all pending callbacks ordered by due timestamp on the virtual clock."""
        return [self._alarms[k] for k in self._sorted_keys()]

    def advance(self, ticks: int) -> list[Alarm]:
        """Advance the virtual clock by the given timedelta and execute due callbacks."""
        if ticks < 0:
            raise ValueError("cannot advance the virtual clock backwards")
        return self.advance_to(self._now + ticks)

    def advance_to(self, tick: int) -> list[Alarm]:
        """Advance the virtual clock to a target timestamp and execute due callbacks."""
        if tick < self._now:
            raise ValueError("cannot move the virtual clock backwards")
        self._now = tick
        due = [k for k in self._sorted_keys() if self._alarms[k].fire_at <= self._now]
        fired = [self._alarms[k] for k in due]
        for k in due:
            self._alarms.pop(k, None)
            self._seq.pop(k, None)
        return fired

    def _sorted_keys(self) -> list[str]:
        return sorted(
            self._alarms, key=lambda k: (self._alarms[k].fire_at, self._seq[k])
        )
