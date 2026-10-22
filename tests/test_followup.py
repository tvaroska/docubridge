"""Tests for the follow-up ladder state machine (PROA-2)."""

from bridge.followup import (
    FollowupEvent,
    FollowupLadder,
    dispatch,
    followup_key,
    stamp_lifecycle,
)
from bridge.scheduler import VirtualClock
from bridge.sla import load_sla_policy
from contract.enums import LifecycleStatus
from contract.wire import CollectionStatus, ExchangeTurn
from tests.address._corpus import load_timeline


def test_arm_schedules_only_the_first_step():
    """A new ladder arms only the first step (overdue@1), not all at once."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    ladder.arm(clock)

    assert len(clock.pending()) == 1
    alarm = clock.pending()[0]
    assert alarm.fire_at == 1
    assert alarm.payload == LifecycleStatus.overdue
    assert ladder.lifecycle == LifecycleStatus.on_track
    assert ladder.events == []


def test_rearms_next_on_each_fire_tick_by_tick():
    """The core PROA-2 proof: each fire re-arms exactly the next step, tick by tick."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    ladder.arm(clock)

    # Tick 1: overdue fires, reminder re-armed
    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert ladder.lifecycle == LifecycleStatus.overdue
    assert len(clock.pending()) == 1
    assert clock.pending()[0].fire_at == 2
    assert clock.pending()[0].payload == LifecycleStatus.reminder

    # Tick 2: reminder fires, escalated re-armed
    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert ladder.lifecycle == LifecycleStatus.reminder
    assert len(clock.pending()) == 1
    assert clock.pending()[0].fire_at == 3
    assert clock.pending()[0].payload == LifecycleStatus.escalated

    # Tick 3: escalated fires, no re-arm (done)
    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert ladder.lifecycle == LifecycleStatus.escalated
    assert len(clock.pending()) == 0
    assert ladder.done is True

    # Verify all events recorded
    assert [(e.step, e.tick) for e in ladder.events] == [
        (LifecycleStatus.overdue, 1),
        (LifecycleStatus.reminder, 2),
        (LifecycleStatus.escalated, 3),
    ]


def test_zero_compute_between():
    """At most one pending alarm at any moment (zero compute between fires)."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    ladder.arm(clock)
    assert len(clock.pending()) == 1

    # Tick before first fire: still 1 pending, no events
    fired = clock.advance_to(0)
    assert fired == []
    assert ladder.events == []
    assert len(clock.pending()) == 1

    # After each fire, exactly 1 pending until escalated
    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert len(clock.pending()) == 1  # reminder armed

    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert len(clock.pending()) == 1  # escalated armed

    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert len(clock.pending()) == 0  # done, no re-arm


def test_holds_at_escalated():
    """The durable stall: escalated fires, then nothing refires."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    # Run to escalated
    ladder.arm(clock)
    for _ in range(3):
        fired = clock.advance(1)
        dispatch(ladder, fired, clock)

    assert ladder.lifecycle == LifecycleStatus.escalated
    assert ladder.done is True
    assert len(clock.pending()) == 0

    # Further advance fires nothing
    fired = clock.advance(5)
    assert fired == []
    assert ladder.lifecycle == LifecycleStatus.escalated

    # arm() returns None when done
    result = ladder.arm(clock)
    assert result is None
    assert len(clock.pending()) == 0


def test_cancel_stops_chase():
    """cancel() clears the pending alarm and stops the chase."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    # Arm and fire overdue (reminder re-armed)
    ladder.arm(clock)
    fired = clock.advance(1)
    dispatch(ladder, fired, clock)
    assert ladder.lifecycle == LifecycleStatus.overdue
    assert len(clock.pending()) == 1  # reminder pending

    # Cancel stops the chase
    result = ladder.cancel(clock)
    assert result is True
    assert len(clock.pending()) == 0

    # Further advance fires nothing
    fired = clock.advance(5)
    assert fired == []
    assert len(ladder.events) == 1  # only overdue recorded

    # Cancel on unarmed ladder returns False
    result2 = ladder.cancel(clock)
    assert result2 is False


def test_notifier_called_once_per_step():
    """Notifier hook is called once per step with the right FollowupEvent."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    sent: list[FollowupEvent] = []
    ladder = FollowupLadder("exchange-1", policy, notifier=sent.append)

    ladder.arm(clock)
    for _ in range(3):
        fired = clock.advance(1)
        dispatch(ladder, fired, clock)

    # Notifier called for each step
    assert len(sent) == 3
    assert [e.step for e in sent] == [
        LifecycleStatus.overdue,
        LifecycleStatus.reminder,
        LifecycleStatus.escalated,
    ]
    # Each event has correct context_id
    assert all(e.context_id == "exchange-1" for e in sent)
    # Each is a FollowupEvent
    assert all(isinstance(e, FollowupEvent) for e in sent)


def test_idempotent_rearm_single_key():
    """Re-arming the same key overwrites; only one pending alarm."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    ladder.arm(clock)
    ladder.arm(clock)  # idempotent re-arm

    assert len(clock.pending()) == 1
    assert clock.pending()[0].key == followup_key("exchange-1")


def test_dispatch_ignores_foreign_alarms():
    """dispatch() routes only the ladder's own alarms; on_fired rejects foreign."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-1", policy)

    # Schedule ladder alarm + unrelated alarm
    ladder.arm(clock)
    clock.schedule("other-key", 1)

    # Advance fires both
    fired = clock.advance(1)
    assert len(fired) == 2

    # dispatch filters to ladder's alarm only
    events = dispatch(ladder, fired, clock)
    assert len(events) == 1
    assert events[0].step == LifecycleStatus.overdue

    # on_fired raises on foreign alarm
    foreign_alarm = next(a for a in fired if a.key == "other-key")
    try:
        ladder.on_fired(foreign_alarm, clock)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "does not belong to ladder" in str(e)


def test_timeline_parity():
    """Fired events match timeline.json exchange-2 followup beats."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    ladder = FollowupLadder("exchange-2", policy)

    # Run tick-by-tick to 3
    ladder.arm(clock)
    for _ in range(3):
        fired = clock.advance(1)
        dispatch(ladder, fired, clock)

    # Load expected from corpus
    timeline = load_timeline()
    beats = [b for b in timeline["exchanges"][1]["beats"] if b["event"] == "followup"]

    # Assert parity: (tick, step) matches (tick, kind)
    assert [(e.tick, e.step.value) for e in ladder.events] == [
        (b["tick"], b["kind"]) for b in beats
    ]
    # Expected: [(1, "overdue"), (2, "reminder"), (3, "escalated")]


def test_stamp_lifecycle_projects_onto_turn_and_surfaces():
    """stamp_lifecycle projects status onto ExchangeTurn; summarize_turn surfaces it."""
    turn = ExchangeTurn(context_id="x", status=CollectionStatus())
    assert turn.lifecycle == LifecycleStatus.on_track

    # Project escalated
    t2 = stamp_lifecycle(turn, LifecycleStatus.escalated)
    assert t2.lifecycle == LifecycleStatus.escalated
    assert turn.lifecycle == LifecycleStatus.on_track  # immutable

    # Verify existing surfacing
    from bridge.edge.wire import summarize_turn

    summary = summarize_turn(t2)
    assert summary.endswith("[escalated]")
