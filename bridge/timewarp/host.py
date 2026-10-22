"""Time-warp engine (P6-5) — pure, no fastapi.

``TimewarpHost`` composes three existing primitives and adds nothing new to the domain:
- one ``bridge.scheduler.VirtualClock`` (P5-1) — deterministic, tick-based stage time;
- a registry of ``bridge.followup.FollowupLadder`` (P5-2), one per armed portal session,
  keyed by ``session_id``;
- the **shared** ``bridge.portal.store.PortalStore`` the running Provider Portal writes to
  and the Ops Dashboard reads — so a fast-forward is visible on their SSE streams.

On each ``advance``/``step`` it drives the clock, routes fired alarms through
``bridge.followup.dispatch`` (which re-arms the next ladder step, zero compute between), and
stamps the fired ladder's ``lifecycle`` onto the shared ``PortalSessionRecord`` via
``dataclasses.replace`` so ``bridge.dashboard.readmodel`` projects it.

The clock advances ONLY by explicit calls — no background timers (deterministic stage time,
the whole point). ``pause``/``resume`` toggle a flag; while paused, ``advance``/``step`` are
rejected (``TimewarpPaused``) for a clean demo.

Isolation: imports ``contract/`` + ``bridge.*`` + stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

from dataclasses import replace

from bridge.followup import FollowupLadder, dispatch
from bridge.portal.store import PortalStore
from bridge.scheduler import Scheduler, VirtualClock
from bridge.sla import load_sla_policy
from contract.enums import LifecycleStatus


class TimewarpPaused(Exception):
    """Raised when ``advance``/``step`` is called while the host is paused (→ 409)."""


class TimewarpHost:
    """The time-warp engine: one virtual clock + a ladder registry over a shared store."""

    def __init__(self, store: PortalStore, clock: Scheduler | None = None) -> None:
        self._store = store
        self._clock: Scheduler = clock or VirtualClock()
        self._ladders: dict[str, FollowupLadder] = {}  # session_id -> ladder
        self._paused = False

    # ------------------------------------------------------------------ arm --
    def arm(self, session_id: str) -> dict:
        """Arm a session's SLA ladder from tick 0 (idempotent: re-arm resets its ladder).

        Reads the session record from the shared store (``KeyError`` → host maps 404),
        loads the SLA policy for its ``process``, builds a fresh ``FollowupLadder`` on the
        record's ``context_id``, and arms the first step. Returns the new host state.
        """
        rec = self._store.get(session_id)  # KeyError -> host maps 404
        policy = load_sla_policy(rec.process)
        ladder = FollowupLadder(rec.context_id, policy)
        ladder.arm(self._clock)
        self._ladders[session_id] = ladder
        # Reset the stamped lifecycle so re-arm starts clean.
        self._store.put(replace(rec, lifecycle=LifecycleStatus.on_track))
        return self.state()

    # -------------------------------------------------------------- advance --
    def advance(self, ticks: int) -> dict:
        """Advance the virtual clock by ``ticks``, firing due ladder steps exactly once.

        Rejected while paused (``TimewarpPaused``). ``VirtualClock`` raises ``ValueError``
        on negative/backward moves (the host maps that to 400).
        """
        if self._paused:
            raise TimewarpPaused("time-warp is paused; resume before advancing")
        self._apply(self._clock.advance(ticks))
        self._drain()
        return self.state()

    def advance_to(self, tick: int) -> dict:
        """Advance the clock to an absolute ``tick`` (scrubber semantics)."""
        if self._paused:
            raise TimewarpPaused("time-warp is paused; resume before advancing")
        self._apply(self._clock.advance_to(tick))
        self._drain()
        return self.state()

    def step(self) -> dict:
        """Advance by one tick (single-step)."""
        return self.advance(1)

    def _drain(self) -> None:
        """Fire ladder steps the fast-forward jumped over. Each ``FollowupLadder`` arms only
        ONE alarm at a time and re-arms the next step on fire (zero compute between); a single
        big jump therefore fires only the currently-armed step. Re-firing due-again alarms at
        the same ``now`` walks the ladder the rest of the way — deterministically, each step
        still fires exactly once."""
        while True:
            more = self._clock.advance_to(self._clock.now())
            if not more:
                return
            self._apply(more)

    def _apply(self, fired: list) -> None:
        """Route fired alarms to their ladders and stamp the new lifecycle onto the store."""
        for session_id, ladder in self._ladders.items():
            events = dispatch(ladder, fired, self._clock)
            if events:
                rec = self._store.get(session_id)
                self._store.put(replace(rec, lifecycle=ladder.lifecycle))

    # ---------------------------------------------------------- pause/reset --
    def pause(self) -> dict:
        """Pause automatic virtual-clock progression on the timewarp host."""
        self._paused = True
        return self.state()

    def resume(self) -> dict:
        """Resume virtual-clock progression on the timewarp host."""
        self._paused = False
        return self.state()

    def reset(self) -> dict:
        """Replay: rebuild the clock, clear ladders, and restore every armed record's
        lifecycle to ``on_track`` (the dashboard escalation queue empties). Re-arming from
        tick 0 is a fresh ``arm`` call."""
        armed = list(self._ladders.keys())
        self._clock = VirtualClock()
        self._ladders = {}
        self._paused = False
        for session_id in armed:
            try:
                rec = self._store.get(session_id)
            except KeyError:
                continue
            self._store.put(replace(rec, lifecycle=LifecycleStatus.on_track))
        return self.state()

    # -------------------------------------------------------------- state ---
    def state(self) -> dict:
        """The host's snake_case JSON state: clock, pause flag, pending alarms, exchanges."""
        pending = [
            {"key": a.key, "fire_at": a.fire_at, "step": _step_value(a.payload)}
            for a in self._clock.pending()
        ]
        exchanges = [
            {
                "session_id": session_id,
                "context_id": ladder.context_id,
                "lifecycle": ladder.lifecycle.value,
                "done": ladder.done,
                "events": [
                    {"step": e.step.value, "tick": e.tick} for e in ladder.events
                ],
            }
            for session_id, ladder in self._ladders.items()
        ]
        return {
            "now": self._clock.now(),
            "paused": self._paused,
            "pending": pending,
            "exchanges": exchanges,
        }


def _step_value(payload: object) -> str | None:
    """Render an alarm payload (a ``LifecycleStatus`` ladder step) as its wire value."""
    if isinstance(payload, LifecycleStatus):
        return payload.value
    return None
