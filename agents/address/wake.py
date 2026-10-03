"""The wake path (PROA-3) — the CUSTOM inbound HTTP surface a2a-sdk doesn't ship.

``a2a-sdk`` is **sender-only** for push notifications and ``adk web`` ships no
webhook receiver, so the *consumer-side wake path* is custom (design/deploy-
resume.md:22-23, design/a2a-consumer.md "Durable state restore"). This module
is that path: a webhook receiver + a persistent ``task_id → SessionRef`` index that
turns a "task X changed" doorbell into a resumed parked collect leg.

**restore ≠ wake** (design/deploy-resume.md:16). *Restore* — return to the
identical paused state — already exists (P4-1/P4-2: durable ``DatabaseSessionService``
+ ``ResumabilityConfig`` + a fresh ``Runner`` fed an id-matched ``FunctionResponse``,
no HTTP). *Wake* — this task — is only the inbound trigger that *calls* restore
again. **The doorbell carries only "task X changed"; it carries no state and does
not re-enter the loop.** ``wake_task`` reuses the driver's single restore path
(``_resume_parked``) — there is one restore path, not two.

This module NEVER imports ``bridge/`` (isolation convention — ``agents/address``
reaches the Bridge by card URL only; the doorbell is parsed as generic JSON, no
``a2a``/``bridge`` type import). The receiver is **unauthenticated locally**;
OIDC / Agent-Identity auth on the doorbell and a Cloud SQL-backed index are P7.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agents.address.app import build_durable_session_service
from agents.address.driver import _pending_long_running_call, _resume_parked

DEFAULT_WAKE_PATH = "/wake"
# Mirrors interceptor.A2A_TASK_ID (do not import the private interceptor module).
A2A_TASK_ID_META = "a2a:task_id"


@dataclass(frozen=True)
class SessionRef:
    """What restore needs to re-open a parked leg: the ADK (app, user, session) triple."""

    app_name: str
    user_id: str
    session_id: str


class SessionIndex:
    """Persistent ``task_id -> SessionRef`` map (PROA-3).

    In-memory by default; JSON-file-backed and crash-safe when a path is configured
    (``path=`` arg or ``BRIDGE_WAKE_INDEX_PATH`` env), so the index survives a
    process restart — it is itself one of the "persistent stores" wake restores
    from. Writes are atomic-ish (temp file + ``os.replace``) so a crash mid-write
    can't corrupt the map. P7: a Cloud SQL row behind this same surface.
    """

    def __init__(self, *, path: str | None = None) -> None:
        self._path = path or os.environ.get("BRIDGE_WAKE_INDEX_PATH", "").strip() or None
        self._map: dict[str, SessionRef] = {}
        if self._path and os.path.exists(self._path):
            with open(self._path) as fh:
                raw = json.loads(fh.read() or "{}")
            self._map = {k: SessionRef(**v) for k, v in raw.items()}

    def put(self, task_id: str, ref: SessionRef) -> None:
        self._map[task_id] = ref
        self._flush()

    def get(self, task_id: str) -> SessionRef | None:
        return self._map.get(task_id)

    def _flush(self) -> None:
        if not self._path:
            return
        tmp = f"{self._path}.tmp"
        with open(tmp, "w") as fh:
            json.dump({k: vars(v) for k, v in self._map.items()}, fh)
        os.replace(tmp, self._path)  # atomic-ish; a crash mid-write can't corrupt


def extract_task_id(body) -> str | None:
    """Recover the changed task id from a ``PushNotificationEvent`` doorbell body.

    Accepts ``bytes | str | dict``. The doorbell carries only "task X changed"
    (a2a-sdk serializes a ``Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent``
    to JSON); parse defensively for ``taskId``/``task_id``/``id`` at top level, then
    inside a nested ``task``/``statusUpdate``/``artifactUpdate``. The id is the only
    load-bearing field — do NOT over-fit the SDK envelope. No ``a2a``/``bridge``
    type import (isolation).
    """
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    if isinstance(body, str):
        try:
            body = json.loads(body or "{}")
        except (ValueError, TypeError):
            return None
    if not isinstance(body, dict):
        return None
    for key in ("taskId", "task_id", "id"):
        v = body.get(key)
        if isinstance(v, str) and v:
            return v
    for nested in (
        "task",
        "statusUpdate",
        "status_update",
        "artifactUpdate",
        "artifact_update",
    ):
        inner = body.get(nested)
        if isinstance(inner, dict):
            found = extract_task_id(inner)
            if found:
                return found
    return None


def peer_task_id_for(session) -> str | None:
    """Read the parked peer (Bridge) task id off the session (``custom_metadata``).

    ``RemoteA2aAgent`` stamps ``a2a:task_id`` onto every relayed event's
    ``custom_metadata`` (interceptor.A2A_TASK_ID); the parked function-**call**
    event carries it. Mirrors ``interceptor.pending_resume_target``'s read: scan
    events newest-first for a function-call event whose ``custom_metadata`` carries
    ``a2a:task_id``. This is the key the index maps — the **Bridge's** task, not
    the consumer's own session id.
    """
    for event in reversed(list(getattr(session, "events", None) or [])):
        getter = getattr(event, "get_function_calls", None)
        calls = getter() if callable(getter) else None
        if calls:
            meta = getattr(event, "custom_metadata", None) or {}
            tid = meta.get(A2A_TASK_ID_META)
            if isinstance(tid, str) and tid:
                return tid
    return None


async def wake_task(
    task_id: str,
    *,
    index: SessionIndex,
    card_url: str,
    session_service=None,  # test seam; else built from BRIDGE_SESSION_DB_URL
    httpx_client=None,  # test seam: in-process mock to the Bridge
    process: str = "address-proof",
    max_rounds: int | None = None,
    max_resumes: int | None = None,
):
    """Wake a parked leg on a "task X changed" doorbell (restore, then run_async).

    Look up the ``SessionRef``, restore the durable session, find the pending
    ``LongRunningFunctionTool`` call, and feed an id-matched ``FunctionResponse``
    to a fresh resumable ``Runner`` (stock restore via ``_resume_parked``).

    Idempotent (§2d): an unknown task id (not in the index), a missing session, or
    no pending call (already terminal / already woken) → no-op returning ``None``.
    Returns the terminal ``ExchangeTurn`` or ``None``.
    """
    ref = index.get(task_id)
    if ref is None:
        return None  # unknown task: nothing to wake
    svc = session_service or build_durable_session_service()
    session = await svc.get_session(
        app_name=ref.app_name, user_id=ref.user_id, session_id=ref.session_id
    )
    if session is None or _pending_long_running_call(session) is None:
        return None  # already terminal / already woken (idempotent)
    kwargs = {}
    if max_rounds is not None:
        kwargs["max_rounds"] = max_rounds
    return await _resume_parked(
        svc,
        app_name=ref.app_name,
        user_id=ref.user_id,
        session_id=ref.session_id,
        card_url=card_url,
        httpx_client=httpx_client,
        process=process,
        max_resumes=max_resumes,
        **kwargs,
    )


def build_wake_app(
    index: SessionIndex,
    card_url: str,
    *,
    path: str = DEFAULT_WAKE_PATH,
    httpx_client=None,
    session_service=None,
) -> Starlette:
    """The webhook RECEIVER (the custom inbound surface a2a-sdk doesn't ship).

    A single ``POST`` route: read body -> ``extract_task_id`` -> ``wake_task`` ->
    200. A doorbell must never error the sender, so unknown / unparseable bodies
    also return 200 (``{"woke": false}``). P7: a Cloud Run route + OIDC /
    Agent-Identity auth on the doorbell.
    """

    async def hook(request: Request) -> JSONResponse:
        tid = extract_task_id(await request.body())
        woke = False
        if tid:
            turn = await wake_task(
                tid,
                index=index,
                card_url=card_url,
                httpx_client=httpx_client,
                session_service=session_service,
            )
            woke = turn is not None
        return JSONResponse({"task_id": tid, "woke": woke})

    return Starlette(routes=[Route(path, hook, methods=["POST"])])
