"""P5-4 — the wake path (PROA-3): webhook receiver + task_id→session index.

Proves the consumer-side wake path (the CUSTOM piece a2a-sdk / adk web don't ship):
a "task X changed" doorbell restores a parked collect leg and drives it to terminal,
survives a process restart, is idempotent, and consumes what the P4-4 emitter emits.

**restore ≠ wake.** Restore (P4-1/P4-2) returns to the paused state; wake (this
task) is only the inbound trigger that *calls* restore. The doorbell carries only
the task id — no state rides it. All wake tests drive over the in-process mock
Bridge on a loopback ``ASGITransport`` (the A2A client rejects non-loopback http),
a durable ``DatabaseSessionService`` over a ``tmp_path`` DSN, and a file-backed
``SessionIndex``.
"""
from __future__ import annotations

import json

import httpx
from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.protobuf.json_format import MessageToDict

from agents.address.app import build_durable_session_service
from agents.address.driver import (
    _pending_long_running_call,
    _run_once,
    _seed_content,
    open_request,
    run_collection,
)
from agents.address.turn_reader import read_latest_exchange_turn
from agents.address.wake import (
    SessionIndex,
    SessionRef,
    build_wake_app,
    extract_task_id,
    peer_task_id_for,
    wake_task,
)
from bridge.edge import build_mock_bridge_app
from contract.enums import Disposition, LifecycleStatus

# The A2A client requires a loopback host for http, so the mock is served loopback.
BASE = "http://localhost"
CARD_URL = BASE + AGENT_CARD_WELL_KNOWN_PATH

USER = "u"
SID = "s"


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _park_seed(session_service, hc, scenario, *, index=None):
    """Drive turn 1 (seed) to a park on a durable session; return the peer task id."""
    await session_service.create_session(
        app_name="address", user_id=USER, session_id=SID
    )
    req = open_request(process="address-proof", scenario=scenario)
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner

    from agents.address.driver import build_address_app  # local: reuse the App path

    runner = Runner(
        app=build_address_app(CARD_URL, max_rounds=6, httpx_client=hc),
        session_service=session_service,
        artifact_service=InMemoryArtifactService(),
    )
    out = await _run_once(
        runner, user_id=USER, session_id=SID, new_message=_seed_content(req)
    )
    assert out is None, "turn 1 must park, not terminate"
    session = await session_service.get_session(
        app_name="address", user_id=USER, session_id=SID
    )
    assert _pending_long_running_call(session) is not None
    peer_task_id = peer_task_id_for(session)
    assert peer_task_id, "the parked peer task id must be recoverable off the session"
    if index is not None:
        index.put(peer_task_id, SessionRef("address", USER, SID))
    return peer_task_id


# --------------------------------------------------------------------------- #
# 1. Core wake → resume to terminal.                                          #
# --------------------------------------------------------------------------- #
async def test_wake_resumes_parked_leg_to_terminal(tmp_path):
    """A doorbell for a parked task restores + drives it to its terminal turn."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        svc = build_durable_session_service(db_url=dsn)
        # Drive+park via the public run_collection path with index population.
        # A parked seed returns None; the index now maps the peer task id.
        turn0 = await run_collection(
            CARD_URL,
            scenario="path-b-two-bills",
            durable=True,
            session_service=svc,
            session_id=SID,
            user_id=USER,
            httpx_client=hc,
            session_index=idx,
            max_resumes=0,  # park only — do not auto-resume; the doorbell does that
        )
        assert turn0 is None, "seed must park (auto-resume disabled)"
        session = await svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        peer_task_id = peer_task_id_for(session)
        assert idx.get(peer_task_id) == SessionRef("address", USER, SID)

        # The doorbell: "task <peer_task_id> changed" → wake → terminal.
        turn = await wake_task(
            peer_task_id,
            index=idx,
            card_url=CARD_URL,
            session_service=svc,
            httpx_client=hc,
        )
    finally:
        await hc.aclose()

    assert turn is not None and turn.terminal is True
    issuers = {
        e.issuer for e in turn.status.entries if e.disposition is Disposition.accepted
    }
    assert issuers == {"power-co", "aqua-util"}


async def test_wake_resumes_resubmit_clean_bill(tmp_path):
    """resubmit sibling: a doorbell wakes the parked leg to the clean accepted bill."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        svc = build_durable_session_service(db_url=dsn)
        peer_task_id = await _park_seed(svc, hc, "resubmit", index=idx)
        turn = await wake_task(
            peer_task_id,
            index=idx,
            card_url=CARD_URL,
            session_service=svc,
            httpx_client=hc,
        )
    finally:
        await hc.aclose()

    assert turn is not None and turn.terminal is True
    accepted = [
        e for e in turn.status.entries if e.disposition is Disposition.accepted
    ]
    assert any(
        e.doc_id == "bill-aquautil-clean" and e.doctype == "utility-bill"
        for e in accepted
    )


# --------------------------------------------------------------------------- #
# 2. The receiver route wakes on a synthetic bare-id doorbell.                #
# --------------------------------------------------------------------------- #
async def test_receiver_route_wakes_on_synthetic_doorbell(tmp_path):
    """POST a bare {"taskId": ...} to build_wake_app → 200 {"woke": true} → terminal.

    Proves the doorbell carries only the id (gate #3) and the route works end to end.
    """
    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        svc = build_durable_session_service(db_url=dsn)
        peer_task_id = await _park_seed(svc, hc, "path-b-two-bills", index=idx)

        wake_app = build_wake_app(idx, CARD_URL, httpx_client=hc, session_service=svc)
        async with _client(wake_app) as wake_hc:
            resp = await wake_hc.post("/wake", json={"taskId": peer_task_id})
        assert resp.status_code == 200
        assert resp.json() == {"task_id": peer_task_id, "woke": True}

        # The leg reached terminal on the durable session (no pending call left).
        session = await svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        assert _pending_long_running_call(session) is None
    finally:
        await hc.aclose()


# --------------------------------------------------------------------------- #
# 3. Restart survival (CUJ-7 through the wake path).                          #
# --------------------------------------------------------------------------- #
async def test_restart_survival_via_doorbell(tmp_path):
    """Park → drop objects → rebuild index (file) + session svc (DSN) → doorbell.

    Reaches the same terminal outcome, same session_id, turn-1 events present — no
    HTTP re-drive of turn 1, no throwaway child session. Both persistent stores
    (the index file and the durable DB) reload.
    """
    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    index_path = f"{tmp_path}/index.json"
    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        # --- Process 1: seed + park, persist index + session, then drop objects. ---
        svc = build_durable_session_service(db_url=dsn)
        idx = SessionIndex(path=index_path)
        peer_task_id = await _park_seed(svc, hc, "path-b-two-bills", index=idx)
        parked = await svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        n_events_at_park = len(parked.events)
        del svc
        del idx

        # --- Process 2: rebuild BOTH persistent stores from disk. ---
        rebuilt_idx = SessionIndex(path=index_path)
        assert rebuilt_idx.get(peer_task_id) == SessionRef("address", USER, SID)
        rebuilt_svc = build_durable_session_service(db_url=dsn)
        reloaded = await rebuilt_svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        assert reloaded is not None and reloaded.id == SID
        assert len(reloaded.events) == n_events_at_park

        # Deliver the doorbell against the rebuilt stores.
        turn = await wake_task(
            peer_task_id,
            index=rebuilt_idx,
            card_url=CARD_URL,
            session_service=rebuilt_svc,
            httpx_client=hc,
        )
        final_session = await rebuilt_svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
    finally:
        await hc.aclose()

    assert turn is not None and turn.terminal is True
    issuers = {
        e.issuer for e in turn.status.entries if e.disposition is Disposition.accepted
    }
    assert issuers == {"power-co", "aqua-util"}
    assert final_session.id == SID  # same session, no throwaway child
    assert len(final_session.events) > n_events_at_park  # resumed on top of turn 1


# --------------------------------------------------------------------------- #
# 4. Unknown / unparseable id → clean no-op (never error the sender).         #
# --------------------------------------------------------------------------- #
async def test_unknown_task_id_is_noop(tmp_path):
    """wake_task on an unknown id → None; the route returns 200 {"woke": false}."""
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    result = await wake_task("nope", index=idx, card_url=CARD_URL)
    assert result is None

    wake_app = build_wake_app(idx, CARD_URL)
    async with _client(wake_app) as wake_hc:
        resp = await wake_hc.post("/wake", json={"taskId": "nope"})
        assert resp.status_code == 200
        assert resp.json() == {"task_id": "nope", "woke": False}

        # A bodyless / non-JSON doorbell must also 200 (never error the sender).
        resp_empty = await wake_hc.post("/wake", content=b"not json")
        assert resp_empty.status_code == 200
        assert resp_empty.json() == {"task_id": None, "woke": False}


# --------------------------------------------------------------------------- #
# 5. Idempotent double doorbell.                                              #
# --------------------------------------------------------------------------- #
async def test_idempotent_double_doorbell(tmp_path):
    """Park → wake to terminal → same doorbell again → second wake is a no-op."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        svc = build_durable_session_service(db_url=dsn)
        peer_task_id = await _park_seed(svc, hc, "path-b-two-bills", index=idx)

        first = await wake_task(
            peer_task_id, index=idx, card_url=CARD_URL,
            session_service=svc, httpx_client=hc,
        )
        assert first is not None and first.terminal is True
        session_after_first = await svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        n_events = len(session_after_first.events)

        # A duplicate doorbell for the now-terminal task: no pending call → no-op.
        second = await wake_task(
            peer_task_id, index=idx, card_url=CARD_URL,
            session_service=svc, httpx_client=hc,
        )
        session_after_second = await svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
    finally:
        await hc.aclose()

    assert second is None, "a duplicate doorbell must not double-resume"
    assert len(session_after_second.events) == n_events, "ledger unchanged"


# --------------------------------------------------------------------------- #
# 6. Escalated stall survives restart; doorbell is a no-op.                   #
# --------------------------------------------------------------------------- #
async def test_escalated_stall_survives_restart_noop_wake(tmp_path):
    """escalated-stall holds non-terminal with lifecycle=escalated across restart.

    The durable store — not a new ladder snapshot — carries it (P4-5). A doorbell
    for a stall that never terminates drives no terminal turn → wake_task None.
    """
    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    app_mock = build_mock_bridge_app(url=BASE + "/")
    hc = _client(app_mock)
    try:
        svc = build_durable_session_service(db_url=dsn)
        peer_task_id = await _park_seed(svc, hc, "escalated-stall", index=idx)

        # The escalated lifecycle is on the parked turn's persisted artifact.
        parked = await svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        turn = read_latest_exchange_turn(
            type("C", (), {"session": parked, "state": None})()
        )
        assert turn is not None
        assert turn.lifecycle is LifecycleStatus.escalated
        assert turn.terminal is False
        del svc

        # Restart: rebuild the session service from the DSN; the stall persisted.
        rebuilt_svc = build_durable_session_service(db_url=dsn)
        reloaded = await rebuilt_svc.get_session(
            app_name="address", user_id=USER, session_id=SID
        )
        reloaded_turn = read_latest_exchange_turn(
            type("C", (), {"session": reloaded, "state": None})()
        )
        assert reloaded_turn.lifecycle is LifecycleStatus.escalated

        # A doorbell for the stall drives no terminal turn (it re-parks) → None.
        woke = await wake_task(
            peer_task_id, index=idx, card_url=CARD_URL,
            session_service=rebuilt_svc, httpx_client=hc, max_resumes=3,
        )
    finally:
        await hc.aclose()

    assert woke is None, "an escalated stall never reaches terminal via the doorbell"


# --------------------------------------------------------------------------- #
# 7. extract_task_id over the doorbell shapes.                                #
# --------------------------------------------------------------------------- #
def test_wake_module_does_not_import_bridge():
    """Convention (gate #7): agents/address/wake.py must not import bridge/ (§1.2)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "agents" / "address" / "wake.py"
    text = src.read_text()
    assert "import bridge" not in text
    assert "from bridge" not in text


def test_extract_task_id_shapes():
    """extract_task_id recovers the id from every doorbell shape; None otherwise."""
    assert extract_task_id({"taskId": "t1"}) == "t1"
    assert extract_task_id({"task_id": "t1"}) == "t1"
    assert extract_task_id({"id": "t1", "contextId": "c1"}) == "t1"
    assert extract_task_id({"statusUpdate": {"taskId": "t1"}}) == "t1"
    assert extract_task_id({"artifact_update": {"task_id": "t1"}}) == "t1"
    assert extract_task_id(b'{"taskId": "t1"}') == "t1"
    assert extract_task_id('{"taskId": "t1"}') == "t1"
    assert extract_task_id({}) is None
    assert extract_task_id("not json") is None
    assert extract_task_id(b"") is None
    assert extract_task_id(12345) is None


def test_extract_task_id_over_real_push_events():
    """Lock the SDK shape: a serialized Task / status / artifact update parses."""
    task = Task(id="task-abc", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    assert extract_task_id(json.dumps(MessageToDict(task))) == "task-abc"

    su = TaskStatusUpdateEvent(
        task_id="task-abc",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
    )
    assert extract_task_id(json.dumps(MessageToDict(su))) == "task-abc"

    au = TaskArtifactUpdateEvent(task_id="task-abc", context_id="ctx-1")
    assert extract_task_id(json.dumps(MessageToDict(au))) == "task-abc"


# --------------------------------------------------------------------------- #
# 8. Emitter ↔ receiver loop via a captured doorbell (no live re-entrancy).   #
# --------------------------------------------------------------------------- #
async def test_emitter_receiver_loop_via_captured_doorbell(tmp_path):
    """P4-4 sender emits a doorbell; its captured body feeds extract_task_id + wake.

    §2b: prove the loop in two clean steps rather than wiring the live sender into
    the receiver (which deadlocks / reenters — the Bridge is mid-``message/send``
    when the sender awaits the POST, and the receiver's ``wake_task`` calls back
    into the same in-process Bridge). Step (1): the real
    ``BasePushNotificationSender`` POSTs a doorbell into a recorder that captures
    the body. Step (2): the captured body drives ``extract_task_id`` + ``wake_task``
    against the (now quiescent) Bridge. The task id is the only load-bearing field,
    so the captured id keys the parked leg via the index — the emitter↔receiver
    seam, without live re-entrancy.
    """
    import uuid

    from a2a.client import ClientConfig, ClientFactory
    from a2a.types import (
        Message,
        Role,
        SendMessageRequest,
        TaskPushNotificationConfig,
    )
    from a2a.utils.constants import TransportProtocol

    from bridge.edge import build_mock_bridge_app as _mock
    from bridge.edge import encode_collect_request
    from contract.wire import CollectRequest

    class Recorder:
        def __init__(self):
            self.posts = []

        async def handle_request(self, request):
            if request.method == "POST":
                self.posts.append(await request.aread())
            return httpx.Response(200, json={"status": "ok"})

    recorder = Recorder()
    push_client = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handle_request),
        base_url="http://receiver",
    )
    bridge_app = _mock(url=BASE + "/", push_httpx_client=push_client)

    dsn = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    idx = SessionIndex(path=f"{tmp_path}/index.json")
    hc = _client(bridge_app)
    try:
        # --- Step 1: drive a real Bridge exchange that emits a captured doorbell. ---
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(BASE + "/")
        parts = encode_collect_request(
            CollectRequest(process="address-proof", message="scenario:path-b-two-bills")
        )
        msg1 = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)
        emit_task_id = None
        emit_context_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg1)):
            if ev.HasField("task"):
                emit_task_id = ev.task.id
                emit_context_id = ev.task.context_id
        assert emit_task_id is not None
        await client.create_task_push_notification_config(
            TaskPushNotificationConfig(
                tenant="t", id=uuid.uuid4().hex, task_id=emit_task_id,
                url="http://receiver/hook",
            )
        )
        msg2 = Message(
            role=Role.ROLE_USER, message_id=uuid.uuid4().hex,
            parts=encode_collect_request(CollectRequest(process="address-proof")),
            context_id=emit_context_id, task_id=emit_task_id,
        )
        async for _ev in client.send_message(SendMessageRequest(message=msg2)):
            pass

        assert recorder.posts, "the P4-4 sender must emit a doorbell"
        captured_body = next(
            (b for b in recorder.posts if extract_task_id(b)), None
        )
        assert captured_body is not None
        captured_id = extract_task_id(captured_body)
        assert captured_id == emit_task_id, "the captured body names the changed task"

        # --- Step 2: park a consumer leg and key it under the captured id; the
        # doorbell (captured, not live) then wakes it against the quiescent Bridge. ---
        svc = build_durable_session_service(db_url=dsn)
        await _park_seed(svc, hc, "resubmit")
        idx.put(captured_id, SessionRef("address", USER, SID))

        woke = await wake_task(
            extract_task_id(captured_body),  # the real captured doorbell body → id
            index=idx, card_url=CARD_URL, session_service=svc, httpx_client=hc,
        )
    finally:
        await hc.aclose()
        await push_client.aclose()

    assert woke is not None and woke.terminal is True
