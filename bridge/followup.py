"""Follow-up ladder (PROA-2) — the SLA chase state machine.

Drives an exchange through on_track -> overdue -> reminder -> escalated by arming
exactly one VirtualClock alarm at a time (a single per-exchange key). When a step
fires, the ladder stamps the lifecycle status, records a FollowupEvent, invokes an
optional notifier (P5-3 plugs the real party-channel send here), and re-arms the
NEXT step -- until `escalated` fires, after which it holds terminally (the durable
stall). "Zero compute between" is literal: between fires there is exactly one
pending alarm and no work runs.

Imports contract + bridge.scheduler/bridge.sla only. NO google.adk, NO a2a,
NEVER agents/. Synchronous (tick math, no asyncio).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from bridge.scheduler import Alarm, Scheduler
from bridge.sla import SlaPolicy
from contract.enums import LifecycleStatus
from contract.wire import ExchangeTurn

FOLLOWUP_KEY_PREFIX = "followup:"


def followup_key(context_id: str) -> str:
    """The single alarm key a ladder re-arms (one pending alarm per exchange)."""
    return f"{FOLLOWUP_KEY_PREFIX}{context_id}"


@dataclass(frozen=True)
class FollowupEvent:
    """A recorded ladder firing (the 'each event recorded' of CUJ-6)."""
    context_id: str
    step: LifecycleStatus   # overdue | reminder | escalated
    tick: int               # clock.now() when it fired


# P5-3 plugs the real party-channel send here; default is a no-op.
Notifier = Callable[["FollowupEvent"], None]


class FollowupLadder:
    """The re-arming follow-up ladder for one exchange (PROA-2)."""

    def __init__(
        self, context_id: str, policy: SlaPolicy, *, notifier: Notifier | None = None
    ) -> None:
        self.context_id = context_id
        self._schedule = policy.schedule()      # [(1,overdue),(2,reminder),(3,escalated)]
        self._notifier = notifier
        self.lifecycle: LifecycleStatus = LifecycleStatus.on_track
        self.events: list[FollowupEvent] = []   # P5-4: persist across restart
        self._idx = 0                           # index of the NEXT step to fire

    @property
    def key(self) -> str:
        return followup_key(self.context_id)

    @property
    def done(self) -> bool:
        """True once the last step (escalated) has fired — the terminal stall."""
        return self._idx >= len(self._schedule)

    def arm(self, clock: Scheduler) -> Alarm | None:
        """Arm the next ladder step (idempotent re-arm of the single key). Returns
        None when the ladder is done (escalated already fired — hold, do not re-arm)."""
        if self.done:
            return None
        fire_at, step = self._schedule[self._idx]
        return clock.schedule(self.key, fire_at, payload=step)

    def on_fired(self, alarm: Alarm, clock: Scheduler) -> FollowupEvent:
        """React to this ladder's fired alarm: stamp lifecycle, record the event,
        notify, and re-arm the next step (zero compute between). Do NOT re-arm past
        escalated (the durable stall). Raises ValueError if `alarm` isn't ours."""
        if alarm.key != self.key:
            raise ValueError(f"alarm {alarm.key!r} does not belong to ladder {self.key!r}")
        step: LifecycleStatus = alarm.payload
        self.lifecycle = step
        event = FollowupEvent(context_id=self.context_id, step=step, tick=clock.now())
        self.events.append(event)          # P5-4: persist
        self._idx += 1
        if self._notifier is not None:
            self._notifier(event)          # P5-3: the real send
        self.arm(clock)                    # re-arm next (no-op when done)
        return event

    def cancel(self, clock: Scheduler) -> bool:
        """Party responded before escalation — stop chasing (clear the pending alarm).
        Returns True if an alarm was pending. Does not rewind recorded events; leaves
        `lifecycle` as-is (arrival→re-chase reset is later wiring, not P5-2)."""
        return clock.cancel(self.key)


def dispatch(
    ladder: FollowupLadder, fired: list[Alarm], clock: Scheduler
) -> list[FollowupEvent]:
    """Route the clock's fired alarms to the ladder that owns them, re-arming.
    P5-4's wake path reuses this to fan a doorbell's fired alarms to the right ladder."""
    return [ladder.on_fired(a, clock) for a in fired if a.key == ladder.key]


def stamp_lifecycle(turn: ExchangeTurn, status: LifecycleStatus) -> ExchangeTurn:
    """Project a ladder lifecycle onto an ExchangeTurn (immutable copy). Feeds the
    existing edge surfacing (bridge/edge/wire.py summarize_turn appends [<status>])
    and, at P5-4, the durable exchange_turn artifact."""
    return turn.model_copy(update={"lifecycle": status})
