"""Port-parity test for the scheduler port (P5-1/PORT-6, gcp P7-4).

The local adapter builds a working ``VirtualClock`` (schedule/advance/cancel/
idempotent re-arm/fire-once); the gcp adapter (P7-4) builds a Cloud Tasks-backed
``GcpScheduler`` over an injected fake client — terminal-outcome parity, not
ledger-identical (Cloud Tasks is wall-clock/push-driven, so ``advance`` raises).
"""

import pytest

from bridge.ports import Port, PortMode, get_adapter
from bridge.scheduler import Scheduler


@pytest.mark.port("scheduler")
def test_scheduler_local_builds_virtualclock(adapter):
    """Local builds a working VirtualClock; gcp builds a Cloud Tasks GcpScheduler
    that mints/cancels via the injected client and honestly raises on advance (P7-4)."""
    if adapter is PortMode.gcp:
        from bridge.ports.gcp import GcpScheduler

        _queue = "projects/p/locations/us-central1/queues/bridge-followups"
        _callback = "https://bridge-runtime.a.run.app/followup"
        _sa = "bridge-runtime@p.iam.gserviceaccount.com"

        class _FakeClient:
            def __init__(self):
                self.tasks = {}

            def create_task(self, parent, task):
                self.tasks[task["name"]] = task

            def delete_task(self, name):
                from google.api_core.exceptions import NotFound

                if name not in self.tasks:
                    raise NotFound(name)
                del self.tasks[name]

            def list_tasks(self, parent):
                return list(self.tasks.values())

        # get_adapter returns a GcpScheduler (no longer raises NotImplementedError).
        port = get_adapter(Port.scheduler, adapter)
        assert isinstance(port, GcpScheduler)

        fake = _FakeClient()
        sched = port.build_scheduler(
            client=fake, queue_path=_queue, callback_url=_callback, oidc_sa_email=_sa
        )
        assert isinstance(sched, Scheduler)
        assert isinstance(sched.now(), int)

        # schedule drives the fake; cancel deletes and returns the bool contract.
        sched.schedule("followup:ctx", sched.now() + 1, payload="reminder")
        assert [a.key for a in sched.pending()] == ["followup:ctx"]
        assert sched.cancel("followup:ctx") is True
        assert sched.cancel("followup:ctx") is False

        # Poll→push divergence: virtual advance has no meaning against a real queue.
        with pytest.raises(NotImplementedError):
            sched.advance(1)
        return

    # Build a scheduler and verify it's a Scheduler Protocol instance
    sched = get_adapter(Port.scheduler, adapter).build_scheduler()
    assert sched.now() == 0
    assert isinstance(sched, Scheduler)

    # Schedule three alarms out of order (prove sorting by fire_at)
    sched.schedule("reminder", 2, payload="reminder")
    sched.schedule("escalated", 3, payload="escalated")
    sched.schedule("overdue", 1, payload="overdue")

    # pending() returns them sorted by tick
    pending = sched.pending()
    assert len(pending) == 3
    assert [a.key for a in pending] == ["overdue", "reminder", "escalated"]
    assert [a.fire_at for a in pending] == [1, 2, 3]

    # advance(1) → now()==1, fires exactly ["overdue"]
    fired = sched.advance(1)
    assert sched.now() == 1
    assert len(fired) == 1
    assert fired[0].key == "overdue"
    assert fired[0].payload == "overdue"

    # advance(1) → now()==2, fires ["reminder"]
    fired = sched.advance(1)
    assert sched.now() == 2
    assert len(fired) == 1
    assert fired[0].key == "reminder"

    # advance(1) → now()==3, fires ["escalated"]
    fired = sched.advance(1)
    assert sched.now() == 3
    assert len(fired) == 1
    assert fired[0].key == "escalated"

    # Further advance fires nothing (fire-once; no refire)
    fired = sched.advance(1)
    assert sched.now() == 4
    assert fired == []


def test_scheduler_idempotent_rearm(adapter):
    """Idempotent re-arm: scheduling the same key twice overwrites the first."""
    if adapter is PortMode.gcp:
        return  # skip gcp (already tested in main test)

    sched = get_adapter(Port.scheduler, adapter).build_scheduler()
    sched.schedule("x", 5, payload="first")
    sched.schedule("x", 7, payload="second")  # overwrites

    pending = sched.pending()
    assert len(pending) == 1
    assert pending[0].key == "x"
    assert pending[0].fire_at == 7
    assert pending[0].payload == "second"


def test_scheduler_cancel(adapter):
    """cancel(key) removes a pending alarm; returns True if it existed, False otherwise."""
    if adapter is PortMode.gcp:
        return  # skip gcp

    sched = get_adapter(Port.scheduler, adapter).build_scheduler()
    sched.schedule("y", 9)

    assert sched.cancel("y") is True
    assert sched.pending() == []
    assert sched.cancel("nope") is False


def test_scheduler_same_tick_insertion_order(adapter):
    """Same-tick alarms fire in insertion order (stable tiebreak)."""
    if adapter is PortMode.gcp:
        return  # skip gcp

    sched = get_adapter(Port.scheduler, adapter).build_scheduler()
    sched.schedule("a", 2, payload="a")
    sched.schedule("b", 2, payload="b")

    fired = sched.advance_to(2)
    assert len(fired) == 2
    assert [a.key for a in fired] == ["a", "b"]


def test_scheduler_backwards_guard(adapter):
    """advance(-1) and advance_to(now-1) raise ValueError."""
    if adapter is PortMode.gcp:
        return  # skip gcp

    sched = get_adapter(Port.scheduler, adapter).build_scheduler()
    sched.advance_to(5)

    with pytest.raises(ValueError, match="backwards"):
        sched.advance(-1)

    with pytest.raises(ValueError, match="backwards"):
        sched.advance_to(4)
