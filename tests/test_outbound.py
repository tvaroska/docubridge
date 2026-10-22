import dataclasses

from bridge.followup import FollowupEvent, FollowupLadder, dispatch
from bridge.outbound import (
    NUDGE_STEPS,
    FakeTransport,
    Nudge,
    OutboundClient,
    build_nudge_notifier,
    default_nudge_message,
)
from bridge.scheduler import VirtualClock
from bridge.sla import load_sla_policy
from contract.enums import LifecycleStatus


def test_send_delivers_and_returns_receipt():
    t = FakeTransport()
    c = OutboundClient(t)
    nudge = Nudge(
        context_id="ctx-1",
        party="jordan-lee",
        step=LifecycleStatus.overdue,
        channel="email",
        message="test",
    )
    r = c.send(nudge)
    assert r.resent is False
    assert t.outbox == [nudge]
    assert c.sent() == [r]
    assert r.idempotency_key == "nudge:ctx-1:overdue"


def test_idempotent_redrive_no_double_send():
    """OUT-1 crash-safety: replaying the same send does not double-deliver."""
    t = FakeTransport()
    c = OutboundClient(t)
    nudge = Nudge(
        context_id="ctx-1",
        party="jordan-lee",
        step=LifecycleStatus.overdue,
        channel="email",
        message="test",
    )
    r1 = c.send(nudge)
    r2 = c.send(nudge)
    assert r1.resent is False
    assert r2.resent is True
    assert len(t.outbox) == 1  # no double delivery
    assert len(c.sent()) == 1


def test_distinct_steps_distinct_sends():
    """Distinct steps produce distinct idempotency keys and thus distinct sends."""
    t = FakeTransport()
    c = OutboundClient(t)
    overdue_nudge = Nudge(
        context_id="ctx-1",
        party="jordan-lee",
        step=LifecycleStatus.overdue,
        channel="email",
        message="overdue",
    )
    reminder_nudge = Nudge(
        context_id="ctx-1",
        party="jordan-lee",
        step=LifecycleStatus.reminder,
        channel="email",
        message="reminder",
    )
    c.send(overdue_nudge)
    c.send(reminder_nudge)
    assert len(t.outbox) == 2
    assert t.outbox[0].step == LifecycleStatus.overdue
    assert t.outbox[1].step == LifecycleStatus.reminder


def test_nudge_carries_no_bearer_token():
    """OUT-1: the Nudge dataclass has no token/credential field."""
    field_names = {f.name for f in dataclasses.fields(Nudge)}
    assert field_names == {"context_id", "party", "step", "channel", "message", "seq"}


def test_notifier_sends_only_on_nudge_steps():
    """The notifier sends on overdue/reminder only — escalated is internal."""
    t = FakeTransport()
    c = OutboundClient(t)
    notify = build_nudge_notifier(c, party="jordan-lee")

    # Fire events for all three ladder steps
    notify(FollowupEvent(context_id="ctx-1", step=LifecycleStatus.overdue, tick=1))
    notify(FollowupEvent(context_id="ctx-1", step=LifecycleStatus.reminder, tick=2))
    notify(FollowupEvent(context_id="ctx-1", step=LifecycleStatus.escalated, tick=3))

    # Only overdue and reminder should have sent
    assert len(t.outbox) == 2
    assert [n.step for n in t.outbox] == [
        LifecycleStatus.overdue,
        LifecycleStatus.reminder,
    ]
    assert (LifecycleStatus.overdue, LifecycleStatus.reminder) == NUDGE_STEPS


def test_ladder_with_nudge_notifier_end_to_end():
    """CUJ-6 real-send beat: overdue → reminder (real nudge fired) → escalated."""
    clock = VirtualClock()
    policy = load_sla_policy("address-proof")
    t = FakeTransport()
    c = OutboundClient(t)
    ladder = FollowupLadder(
        "exchange-2",
        policy,
        notifier=build_nudge_notifier(c, party="jordan-lee"),
    )
    ladder.arm(clock)

    # Advance the clock tick by tick
    for _ in range(3):
        dispatch(ladder, clock.advance(1), clock)

    # Assert exactly two real nudges (== policy.max_nudges)
    assert len(t.outbox) == 2
    assert [n.step for n in t.outbox] == [
        LifecycleStatus.overdue,
        LifecycleStatus.reminder,
    ]
    assert policy.max_nudges == 2

    # Assert each nudge has the correct attributes
    for nudge in t.outbox:
        assert nudge.context_id == "exchange-2"
        assert nudge.party == "jordan-lee"
        assert nudge.channel == "email"

    # Assert ladder holds at escalated with no party send for it
    assert ladder.lifecycle == LifecycleStatus.escalated
    assert ladder.done is True

    # Assert all three events still recorded (escalated is internal)
    assert [(e.step, e.tick) for e in ladder.events] == [
        (LifecycleStatus.overdue, 1),
        (LifecycleStatus.reminder, 2),
        (LifecycleStatus.escalated, 3),
    ]


def test_custom_message_and_channel():
    """Notifier supports custom message and channel."""
    t = FakeTransport()
    c = OutboundClient(t)
    notify = build_nudge_notifier(
        c,
        party="p",
        channel="portal",
        message_for=lambda e: f"custom {e.step.value}",
    )

    notify(FollowupEvent(context_id="ctx-1", step=LifecycleStatus.overdue, tick=1))

    assert len(t.outbox) == 1
    assert t.outbox[0].channel == "portal"
    assert t.outbox[0].message == "custom overdue"

    # Sanity-check default_nudge_message
    event = FollowupEvent(context_id="ctx-1", step=LifecycleStatus.overdue, tick=1)
    msg = default_nudge_message(event, "p")
    assert "overdue" in msg
    assert "p" in msg
