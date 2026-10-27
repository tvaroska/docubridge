"""GCP scheduler (Cloud Tasks) adapter unit tests (P7-4).

Credential-free, ALWAYS run (no ``adapter`` fixture, no BRIDGE_TEST_GCP gate, no
google-cloud-tasks installed): a fake Cloud Tasks client injected via
``build_scheduler(client=...)`` exercises the **arming half** — ``schedule`` mints
the right OIDC-authenticated Cloud Task, idempotent re-arm is delete-then-create on
a derived task name, ``cancel`` deletes by name, ``now`` is wall-clock, and
``advance``/``advance_to`` honestly raise (the poll→push divergence). The live queue
roundtrip is gated on ``BRIDGE_TEST_GCP`` (a P7-9 input). Mirrors
``test_gcp_ports.py`` / ``test_gcp_skill_registry.py``.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from bridge.ports import Port, PortMode, get_adapter
from bridge.ports.gcp import (
    BRIDGE_FOLLOWUP_CALLBACK_URL_ENV,
    BRIDGE_RUNTIME_SA_EMAIL_ENV,
    BRIDGE_TASKS_QUEUE_ENV,
    GcpScheduler,
    _task_id,
)
from bridge.scheduler import Alarm, Scheduler

_QUEUE = "projects/test-project/locations/us-central1/queues/bridge-followups"
_CALLBACK = "https://bridge-runtime-abc.a.run.app/followup"
_SA = "bridge-runtime@test-project.iam.gserviceaccount.com"


class _FakeTasksClient:
    """A dict-backed Cloud Tasks client double: ``create_task`` / ``delete_task`` /
    ``list_tasks`` over the task dicts the adapter builds (no google-cloud-tasks)."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.deletes: list[str] = []

    def create_task(self, parent, task):
        self.creates.append({"parent": parent, "task": task})
        self.tasks[task["name"]] = task
        return task

    def delete_task(self, name):
        from google.api_core.exceptions import NotFound

        self.deletes.append(name)
        if name not in self.tasks:
            raise NotFound(f"task {name} not found")
        del self.tasks[name]

    def list_tasks(self, parent):
        return list(self.tasks.values())


def _build(client, **overrides):
    cfg = {"queue_path": _QUEUE, "callback_url": _CALLBACK, "oidc_sa_email": _SA}
    cfg.update(overrides)
    return get_adapter(Port.scheduler, PortMode.gcp).build_scheduler(
        client=client, **cfg
    )


# --- wired, not stubbed ----------------------------------------------------


def test_get_adapter_returns_gcp_scheduler():
    """gcp scheduler no longer raises NotImplementedError (P7-4)."""
    adapter = get_adapter(Port.scheduler, PortMode.gcp)
    assert isinstance(adapter, GcpScheduler)
    assert adapter.port is Port.scheduler


def test_built_scheduler_is_scheduler_protocol():
    sched = _build(_FakeTasksClient())
    assert isinstance(sched, Scheduler)


# --- refuse-without-config (the session-affinity trap analogue) ------------


def test_build_refuses_without_queue_env(monkeypatch):
    """No queue arg + no BRIDGE_TASKS_QUEUE env → RuntimeError naming the env. The
    gcp scheduler NEVER falls back to a silent in-process clock (multi-replica would
    silently drop armed alarms across replicas)."""
    for env in (
        BRIDGE_TASKS_QUEUE_ENV,
        BRIDGE_FOLLOWUP_CALLBACK_URL_ENV,
        BRIDGE_RUNTIME_SA_EMAIL_ENV,
    ):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(RuntimeError, match=BRIDGE_TASKS_QUEUE_ENV):
        get_adapter(Port.scheduler, PortMode.gcp).build_scheduler()


def test_build_resolves_config_from_env(monkeypatch):
    """With no args, build_scheduler resolves all three config values from env."""
    monkeypatch.setenv(BRIDGE_TASKS_QUEUE_ENV, _QUEUE)
    monkeypatch.setenv(BRIDGE_FOLLOWUP_CALLBACK_URL_ENV, _CALLBACK)
    monkeypatch.setenv(BRIDGE_RUNTIME_SA_EMAIL_ENV, _SA)
    sched = get_adapter(Port.scheduler, PortMode.gcp).build_scheduler(
        client=_FakeTasksClient()
    )
    assert isinstance(sched, Scheduler)


# --- schedule mints the right OIDC-authenticated task ----------------------


def test_schedule_mints_oidc_task():
    client = _FakeTasksClient()
    sched = _build(client)
    alarm = sched.schedule("followup:ctx-1", 1234567890, payload="reminder")

    assert isinstance(alarm, Alarm)
    assert alarm.fire_at == 1234567890
    assert alarm.payload == "reminder"

    assert len(client.creates) == 1
    req = client.creates[0]
    assert req["parent"] == _QUEUE
    task = req["task"]

    # Deterministic task name derived from the key (idempotent re-arm handle).
    assert task["name"] == f"{_QUEUE}/tasks/{_task_id('followup:ctx-1')}"
    # schedule_time == fire_at (absolute epoch seconds).
    assert task["schedule_time"]["seconds"] == 1234567890

    http = task["http_request"]
    assert http["http_method"] == "POST"
    assert http["url"] == _CALLBACK
    # OIDC token: SA email + audience == the callback URL (P7-1 token-creator grant).
    assert http["oidc_token"]["service_account_email"] == _SA
    assert http["oidc_token"]["audience"] == _CALLBACK
    # Body is compact JSON {key, payload}.
    assert json.loads(http["body"]) == {"key": "followup:ctx-1", "payload": "reminder"}


def test_schedule_carries_no_bearer_token():
    """OIDC, not a bearer token / API key: no Authorization header, no credential in
    the body (mirrors test_nudge_carries_no_bearer_token)."""
    client = _FakeTasksClient()
    sched = _build(client)
    sched.schedule("followup:ctx-1", 100, payload="overdue")
    http = client.creates[0]["task"]["http_request"]
    assert "authorization" not in {k.lower() for k in http["headers"]}
    assert set(json.loads(http["body"])) == {"key", "payload"}


# --- idempotent re-arm (crash-safe, same key overwrites) -------------------


def test_idempotent_rearm_delete_then_create():
    client = _FakeTasksClient()
    sched = _build(client)
    name = f"{_QUEUE}/tasks/{_task_id('followup:ctx-1')}"

    sched.schedule("followup:ctx-1", 100, payload="overdue")
    sched.schedule("followup:ctx-1", 200, payload="reminder")  # re-arm same key

    # Both arms target the SAME derived task name (not two distinct tasks).
    assert [c["task"]["name"] for c in client.creates] == [name, name]
    # Delete-then-create observed on the re-arm (crash-safe overwrite).
    assert name in client.deletes
    # Exactly one pending task (the latest arm won).
    assert list(client.tasks) == [name]
    assert client.tasks[name]["schedule_time"]["seconds"] == 200


# --- cancel deletes by name (bool contract) --------------------------------


def test_cancel_returns_true_then_false():
    client = _FakeTasksClient()
    sched = _build(client)
    sched.schedule("followup:ctx-1", 100)

    assert sched.cancel("followup:ctx-1") is True  # existed → deleted
    assert sched.cancel("followup:ctx-1") is False  # already gone → NotFound
    assert sched.cancel("followup:nope") is False  # never existed → NotFound


# --- now() is wall-clock; advance/advance_to honestly raise ----------------


def test_now_is_wall_clock_int():
    sched = _build(_FakeTasksClient())
    now = sched.now()
    assert isinstance(now, int)
    assert now >= 0


def test_advance_and_advance_to_raise():
    sched = _build(_FakeTasksClient())
    with pytest.raises(NotImplementedError, match="wall-clock/push"):
        sched.advance(1)
    with pytest.raises(NotImplementedError, match="wall-clock/push"):
        sched.advance_to(1)


# --- pending() round-trips fire_at + reconstructs key/payload --------------


def test_pending_reconstructs_alarms():
    client = _FakeTasksClient()
    sched = _build(client)
    sched.schedule("followup:ctx-1", 500, payload="reminder")

    pending = sched.pending()
    assert len(pending) == 1
    alarm = pending[0]
    assert alarm.key == "followup:ctx-1"
    assert alarm.fire_at == 500
    assert alarm.payload == "reminder"


# --- lazy import: pkg import pulls no Cloud Tasks client -------------------


def test_importing_gcp_pkg_does_not_import_cloud_tasks():
    import bridge.ports.gcp  # noqa: F401

    assert "bridge.ports.gcp" in sys.modules
    assert "google.cloud.tasks" not in sys.modules
    assert "google.cloud.tasks_v2" not in sys.modules


# --- live roundtrip, gated (a P7-9 input; mirror _gcp_dsn_or_skip) ----------


def test_gcp_scheduler_live_roundtrip():
    """Against a provisioned queue (BRIDGE_TEST_GCP=1 + the queue/callback/SA envs):
    arm a near-future task, observe it in pending(), then cancel it. Does NOT wait
    for a real firing — that is the P7-9 deployed run."""
    if os.environ.get("BRIDGE_TEST_GCP") != "1":
        pytest.skip(
            "set BRIDGE_TEST_GCP=1 to run the live Cloud Tasks roundtrip (P7-9)"
        )
    queue = os.environ.get(BRIDGE_TASKS_QUEUE_ENV, "").strip()
    callback = os.environ.get(BRIDGE_FOLLOWUP_CALLBACK_URL_ENV, "").strip()
    sa = os.environ.get(BRIDGE_RUNTIME_SA_EMAIL_ENV, "").strip()
    if not (queue and callback and sa):
        pytest.skip(
            "a provisioned queue + callback URL + SA email (the three envs) are a "
            "P7-9 input for the live scheduler roundtrip"
        )

    sched = get_adapter(Port.scheduler, PortMode.gcp).build_scheduler()
    key = "followup:live-roundtrip-test"
    sched.schedule(key, sched.now() + 3600, payload="reminder")
    try:
        assert key in [a.key for a in sched.pending()]
    finally:
        assert sched.cancel(key) is True
