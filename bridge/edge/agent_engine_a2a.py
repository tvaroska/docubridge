"""Vertex AI Agent Engine native A2A runtime & Cloud Run proxy adapter (Option B).

Hosts the Bridge's full A2A server — :class:`~bridge.edge.executor.CollectExecutor`,
:class:`~bridge.edge.server.EdgeRequestHandler`, dynamic :func:`~bridge.edge.card.build_agent_card`
(over ``loaded_skills()``), ``TaskStore`` (via ``Seam.task_store``),
``PushNotificationConfigStore`` (via :func:`~bridge.edge.pushguard.guarded_push_config_store`),
shared ``BaseArtifactService`` (:func:`~bridge.edge.artifacts.build_artifact_store`),
``LlmLeg`` (in ``EdgeMode.hybrid``), ``SatisfactionAssessor``, and ``RfpBoard`` opener —
inside a Vertex AI Agent Engine ``ReasoningEngine`` (:class:`BridgeA2aAgentEngine`),
and provides the thin Cloud Run edge proxy (:func:`build_remote_a2a_routes`) gated by
``BRIDGE_AGENT_ENGINE_A2A=1`` (or an injected ``a2a_engine``).

Why :class:`BridgeA2aAgentEngine` implements ``OperationRegistrable`` + ``Cloneable``
directly rather than subclassing ``vertexai.preview.reasoning_engines.A2aAgent``:

1. ``vertexai.preview.reasoning_engines.A2aAgent`` in ``google-cloud-aiplatform==1.165.1``
   targets ``a2a-sdk 0.3.x`` (importing removed ``a2a.server.apps.rest.rest_adapter`` and
   ``a2a.server.request_handlers.rest_handler``), whereas the Bridge runs on ``a2a-sdk 1.x``
   (``1.1.2``) with ``create_jsonrpc_routes`` / ``JsonRpcDispatcher``.
2. ``_wrap_a2a_operation`` in ``vertexai.agent_engines`` rejects ``capabilities.streaming=True``
   and forces ``HTTP+JSON`` only, whereas the Bridge A2A contract and ``google-adk``'s
   ``RemoteA2aAgent`` require ``JSONRPC`` transport (both v1.0 PascalCase methods such as
   ``SendMessage`` / ``SendStreamingMessage`` and v0.3 ``message/send`` / ``message/stream``),
   streaming SSE, push notifications, and the pre-dispatch ``CollectExecutor.admit`` guard.
3. Protobuf descriptors in ``a2a.types`` (C-extension ``google._upb._message.Descriptor``)
   cannot be pickled by ``cloudpickle`` before deployment. :class:`BridgeA2aAgentEngine`
   stores only picklable configuration before :meth:`BridgeA2aAgentEngine.set_up` and resets
   runtime state in :meth:`BridgeA2aAgentEngine.clone`, so ``cloudpickle.dumps`` succeeds cleanly.
4. :class:`BridgeA2aAgentEngine` also supports ADK ``Runner`` invocations on
   :meth:`BridgeA2aAgentEngine.async_stream_query` when called with ``(user_id, session_id, message)``,
   so a single deployed ``ReasoningEngine`` serves both Option A (remote ``LlmLeg`` / ADK ``App``)
   and Option B (remote A2A server).
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Iterator
from typing import TYPE_CHECKING, Any

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types import AgentCard
from google.adk.artifacts import BaseArtifactService
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from bridge.edge.artifacts import build_artifact_store
from bridge.edge.card import DEFAULT_URL, build_agent_card, resolved_card_url
from bridge.edge.executor import CollectExecutor
from bridge.edge.pathroute import EdgeMode, resolve_edge_mode
from bridge.edge.pushguard import guarded_push_config_store
from bridge.edge.rfp_open import rfp_board_from_env
from bridge.satisfaction import SatisfactionAssessor, resolve_assessor
from bridge.seams import Seam, get_adapter, resolve_mode

if TYPE_CHECKING:
    import httpx
    from a2a.server.tasks import (
        PushNotificationConfigStore,
        PushNotificationSender,
        TaskStore,
    )
    from starlette.authentication import AuthenticationBackend

    from bridge.dashboard.board import RfpBoard
    from bridge.edge.llm_leg import LlmLeg

# Opt-in environment variable that switches ``build_a2a_app`` into a thin proxy
# forwarding ``/.well-known/agent-card.json`` and JSON-RPC ``POST /`` to a
# Vertex AI Agent Engine instance hosting ``BridgeA2aAgentEngine``.
AGENT_ENGINE_A2A_ENV = "BRIDGE_AGENT_ENGINE_A2A"

# JSON-RPC methods (v1.0 PascalCase and v0.3 slash-delimited) that return an SSE
# event stream on success (or a JSON-RPC error response on pre-dispatch refusal).
STREAMING_A2A_METHODS = frozenset(
    {
        "SendStreamingMessage",
        "message/stream",
        "SubscribeToTask",
        "tasks/resubscribe",
    }
)


def is_agent_engine_a2a_enabled(value: str | bool | None = None) -> bool:
    """Return True when Option B (A2A server on Agent Engine) is enabled."""
    if isinstance(value, bool):
        return value
    raw = (
        value
        if isinstance(value, str)
        else os.environ.get(AGENT_ENGINE_A2A_ENV, "")
    )
    return raw.strip().lower() in {"1", "true", "yes"}


def resolve_a2a_engine(a2a_engine: Any = None) -> Any | None:
    """Resolve the A2A Agent Engine target when ``a2a_engine`` is passed or
    ``BRIDGE_AGENT_ENGINE_A2A=1`` is set in the environment.

    When resolved from the environment, connects to the ``ReasoningEngine`` named by
    ``BRIDGE_AGENT_ENGINE_ID`` (via :func:`bridge.seams.gcp.parse_agent_engine_resource`).
    """
    if a2a_engine is not None:
        return a2a_engine
    if not is_agent_engine_a2a_enabled():
        return None
    from bridge.seams.gcp import parse_agent_engine_resource

    proj, loc, eid = parse_agent_engine_resource(None)
    import vertexai

    if not hasattr(vertexai, "agent_engines"):
        import vertexai.agent_engines

    if proj and loc:
        vertexai.init(project=proj, location=loc)
    resource_name = (
        f"projects/{proj}/locations/{loc}/reasoningEngines/{eid}"
        if proj and loc
        else eid
    )
    return vertexai.agent_engines.get(resource_name)


def _run_coro_sync(coro: Any) -> Any:
    """Run a coroutine from a synchronous Agent Engine entrypoint, even if an event
    loop is already running on the current thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _parse_sse_events(raw_body: str) -> list[dict[str, Any]]:
    """Extract SSE ``data:`` frames from a ``text/event-stream`` payload."""
    frames: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for line in raw_body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        elif line == "" and data_lines:
            joined = "\n".join(data_lines)
            data_lines.clear()
            try:
                frames.append({"sse_data": json.loads(joined)})
            except json.JSONDecodeError:
                frames.append({"sse_data": joined})
    if data_lines:
        joined = "\n".join(data_lines)
        try:
            frames.append({"sse_data": json.loads(joined)})
        except json.JSONDecodeError:
            frames.append({"sse_data": joined})
    return frames


class BridgeA2aAgentEngine:
    """Vertex AI Agent Engine runtime hosting the Bridge A2A server (Option B) and
    ADK ``document_bridge`` App (Option A).

    Conforms to Vertex AI Agent Engine's ``OperationRegistrable``, ``Cloneable``,
    ``Queryable``, ``AsyncQueryable``, ``StreamQueryable``, and ``AsyncStreamQueryable``
    protocols without keeping any unpicklable C-extension protobuf descriptors on
    ``self`` prior to :meth:`set_up`.
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        project: str | None = None,
        location: str | None = None,
        edge_mode: EdgeMode | str | None = None,
        reviewers: Iterable[str] | str | None = None,
        satisfaction: SatisfactionAssessor | str | None = None,
        executor: CollectExecutor | None = None,
        task_store: TaskStore | None = None,
        artifact_store: BaseArtifactService | None = None,
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        push_httpx_client: httpx.AsyncClient | None = None,
        llm_leg: LlmLeg | None = None,
        auth_backend: AuthenticationBackend | None = None,
        rfp_board: RfpBoard | None = None,
    ) -> None:
        reviewers_cfg: tuple[str, ...] | str | None
        if reviewers is None or isinstance(reviewers, str):
            reviewers_cfg = reviewers
        else:
            reviewers_cfg = tuple(reviewers)

        edge_mode_cfg = (
            edge_mode.value if isinstance(edge_mode, EdgeMode) else edge_mode
        )
        self._config: dict[str, Any] = {
            "url": url,
            "project": project,
            "location": location,
            "edge_mode": edge_mode_cfg,
            "reviewers": reviewers_cfg,
            "satisfaction": satisfaction if isinstance(satisfaction, str) else None,
        }
        # Optional test-injected collaborators (cleared on clone() for cloudpickle).
        self._injected: dict[str, Any] = {
            "executor": executor,
            "task_store": task_store,
            "artifact_store": artifact_store,
            "push_config_store": push_config_store,
            "push_sender": push_sender,
            "push_httpx_client": push_httpx_client,
            "llm_leg": llm_leg,
            "auth_backend": auth_backend,
            "satisfaction_obj": (
                None if isinstance(satisfaction, str) else satisfaction
            ),
            "rfp_board": rfp_board,
        }
        self._app: Starlette | None = None
        self._artifact_store: BaseArtifactService | None = None
        self._task_store: TaskStore | None = None
        self._executor: CollectExecutor | None = None
        self._rfp_board: RfpBoard | None = None
        self._adk_leg: LlmLeg | None = None

    def clone(self) -> BridgeA2aAgentEngine:
        """Return a clean, picklable copy prior to ``set_up()`` (``Cloneable`` protocol)."""
        return BridgeA2aAgentEngine(
            url=self._config["url"],
            project=self._config.get("project"),
            location=self._config.get("location"),
            edge_mode=self._config["edge_mode"],
            reviewers=self._config["reviewers"],
            satisfaction=self._config["satisfaction"],
        )

    def __getstate__(self) -> dict[str, Any]:
        """Strip post-``set_up`` runtime handles so ``cloudpickle.dumps`` never
        encounters ``google._upb._message.Descriptor`` objects from ``a2a.types``."""
        return {
            "_config": dict(self._config),
            "_injected": {
                k: None
                for k in (
                    "executor",
                    "task_store",
                    "artifact_store",
                    "push_config_store",
                    "push_sender",
                    "push_httpx_client",
                    "llm_leg",
                    "auth_backend",
                    "satisfaction_obj",
                    "rfp_board",
                )
            },
            "_app": None,
            "_artifact_store": None,
            "_task_store": None,
            "_executor": None,
            "_rfp_board": None,
            "_adk_leg": None,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)

    def _resolved_url(self, override_url: str | None = None) -> str:
        if override_url:
            return override_url
        cfg_url = self._config.get("url", DEFAULT_URL)
        if cfg_url == DEFAULT_URL:
            return resolved_card_url()
        return cfg_url

    def set_up(self) -> None:
        """Construct the A2A Starlette dispatcher, ``CollectExecutor``, ``EdgeRequestHandler``,
        and seam-backed stores inside the Vertex AI Agent Engine worker."""
        if self._app is not None:
            return

        project = self._config.get("project")
        if project and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            os.environ["GOOGLE_CLOUD_PROJECT"] = project
        location = self._config.get("location") or os.environ.get(
            "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION"
        )
        if location and not os.environ.get("GOOGLE_CLOUD_LOCATION"):
            os.environ["GOOGLE_CLOUD_LOCATION"] = location
        if project or os.environ.get("GOOGLE_CLOUD_PROJECT"):
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
        ae_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "").strip()
        if ae_id and not os.environ.get("BRIDGE_AGENT_ENGINE_ID"):
            os.environ["BRIDGE_AGENT_ENGINE_ID"] = ae_id
        if (
            os.environ.get("BRIDGE_SEAM_MODE", "").strip() == "gcp"
            and not os.environ.get("DATABASE_URL", "").strip()
        ):
            os.environ.setdefault("BRIDGE_SEAM_TASK_STORE", "local")
            os.environ.setdefault(
                "BRIDGE_TASK_DB_URL",
                "sqlite+aiosqlite:////tmp/bridge_tasks.sqlite",
            )
            os.environ.setdefault("BRIDGE_SEAM_EXCHANGE_STORE", "local")
            os.environ.setdefault(
                "BRIDGE_EXCHANGE_DB_URL",
                "sqlite+aiosqlite:////tmp/bridge_exchange.sqlite",
            )

        from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes

        from bridge.edge.server import RPC_PATH, EdgeRequestHandler

        url = self._resolved_url()
        card = build_agent_card(url)
        store = self._injected.get("artifact_store") or build_artifact_store()

        active_executor = self._injected.get("executor") or CollectExecutor(
            base_url=url
        )
        active_executor.bind_store(store, base_url=url)

        mode = resolve_edge_mode(self._config.get("edge_mode"))
        leg = self._injected.get("llm_leg")
        if mode is EdgeMode.hybrid:
            if leg is None:
                from bridge.edge.llm_leg import LlmLeg

                leg = LlmLeg(artifact_store=store)
            leg.bind_store(store)
        else:
            leg = None

        active_executor.bind_edge(
            mode, llm_leg=leg, reviewers=self._config.get("reviewers")
        )
        satisfaction_arg = (
            self._injected.get("satisfaction_obj")
            or self._config.get("satisfaction")
        )
        active_executor.bind_assessor(resolve_assessor(satisfaction_arg))

        board = self._injected.get("rfp_board") or rfp_board_from_env()
        active_executor.bind_rfp(board)

        store_seam = get_adapter(Seam.task_store, resolve_mode(Seam.task_store))
        active_task_store = (
            self._injected.get("task_store") or store_seam.build_task_store()
        )
        active_push_config_store = guarded_push_config_store(
            self._injected.get("push_config_store")
            or store_seam.build_push_config_store()
        )

        push_sender = self._injected.get("push_sender")
        if push_sender is None:
            from a2a.server.tasks import BasePushNotificationSender

            client = self._injected.get("push_httpx_client")
            if client is None:
                import httpx

                client = httpx.AsyncClient()
            active_push_sender = BasePushNotificationSender(
                httpx_client=client, config_store=active_push_config_store
            )
        else:
            active_push_sender = push_sender

        handler = EdgeRequestHandler(
            agent_executor=active_executor,
            task_store=active_task_store,
            agent_card=card,
            push_config_store=active_push_config_store,
            push_sender=active_push_sender,
        )

        async def _live_card(_snapshot: AgentCard) -> AgentCard:
            return build_agent_card(self._resolved_url())

        routes = create_agent_card_routes(
            card, card_modifier=_live_card
        ) + create_jsonrpc_routes(handler, RPC_PATH)

        auth_backend = self._injected.get("auth_backend")
        if auth_backend is None:
            app = Starlette(routes=routes)
        else:
            app = Starlette(
                routes=routes,
                middleware=[
                    Middleware(AuthenticationMiddleware, backend=auth_backend)
                ],
            )
        app.state.rfp_board = board

        self._app = app
        self._artifact_store = store
        self._task_store = active_task_store
        self._executor = active_executor
        self._rfp_board = board
        self._adk_leg = leg

    @property
    def artifact_store(self) -> BaseArtifactService:
        """The shared ``BaseArtifactService`` bound to the engine's executor."""
        self.set_up()
        assert self._artifact_store is not None
        return self._artifact_store

    @property
    def task_store(self) -> TaskStore:
        """The ``TaskStore`` bound to the engine's ``EdgeRequestHandler``."""
        self.set_up()
        assert self._task_store is not None
        return self._task_store

    @property
    def executor(self) -> CollectExecutor:
        """The ``CollectExecutor`` running inside the engine."""
        self.set_up()
        assert self._executor is not None
        return self._executor

    @property
    def rfp_board(self) -> RfpBoard | None:
        """The opener-only ``RfpBoard`` bound to the engine, when configured."""
        self.set_up()
        return self._rfp_board

    async def _dispatch_asgi(
        self,
        *,
        method: str,
        path: str,
        body: bytes = b"",
        user: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Drive the internal Starlette A2A app in-memory for a request."""
        self.set_up()
        assert self._app is not None
        if user is not None and self._executor is not None:
            # Ensure base_url stays aligned with resolved_url
            self._executor.bind_store(
                self._artifact_store, base_url=self._resolved_url()
            )

        raw_headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
        ]
        has_version_header = False
        if headers:
            for k, v in headers.items():
                k_lower = k.lower()
                if k_lower == "a2a-version":
                    has_version_header = True
                if k_lower != "content-type":
                    raw_headers.append(
                        (k_lower.encode("latin-1"), v.encode("latin-1"))
                    )
        if not has_version_header and body:
            try:
                parsed_body = json.loads(body.decode("utf-8"))
                rpc_method = (
                    parsed_body.get("method", "")
                    if isinstance(parsed_body, dict)
                    else ""
                )
                if isinstance(rpc_method, str) and rpc_method and "/" not in rpc_method:
                    raw_headers.append((b"a2a-version", b"1.0"))
            except Exception:
                pass

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 0),
            "server": ("agent-engine.internal", 80),
        }
        if user:
            scope["user"] = SimpleUser(user)
            scope["auth"] = AuthCredentials(["authenticated"])

        request_sent = False
        disconnect_event = asyncio.Event()

        async def _receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            await disconnect_event.wait()
            return {"type": "http.disconnect"}

        status_code = 200
        resp_headers: dict[str, str] = {}
        chunks: list[bytes] = []

        async def _send(message: dict[str, Any]) -> None:
            nonlocal status_code
            msg_type = message.get("type")
            if msg_type == "http.response.start":
                status_code = int(message.get("status", 200))
                for k, v in message.get("headers", ()):
                    resp_headers[k.decode("latin-1").lower()] = v.decode("latin-1")
            elif msg_type == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    chunks.append(bytes(chunk))
                if not message.get("more_body", False):
                    disconnect_event.set()

        await self._app(scope, _receive, _send)
        return status_code, resp_headers, b"".join(chunks)

    async def async_get_agent_card(
        self, *, url: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Return the live ``AgentCard`` JSON dictionary built from ``loaded_skills()``."""
        del kwargs
        self.set_up()
        target_url = self._resolved_url(url)
        card = build_agent_card(target_url)
        return agent_card_to_dict(card)

    def get_agent_card(
        self, *, url: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Synchronous wrapper for :meth:`async_get_agent_card`."""
        return _run_coro_sync(self.async_get_agent_card(url=url, **kwargs))

    async def async_handle_a2a_jsonrpc(
        self,
        payload: dict[str, Any] | str | None = None,
        *,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a unary A2A JSON-RPC 2.0 request inside Agent Engine."""
        self.set_up()
        if url and self._executor is not None and self._artifact_store is not None:
            self._executor.bind_store(self._artifact_store, base_url=url)
        effective_payload = payload if payload is not None else kwargs
        if isinstance(effective_payload, str):
            raw_body = effective_payload.encode("utf-8")
        else:
            raw_body = json.dumps(effective_payload).encode("utf-8")

        _status, _headers, body_bytes = await self._dispatch_asgi(
            method="POST",
            path="/",
            body=raw_body,
            user=user,
            headers=headers,
        )
        return json.loads(body_bytes.decode("utf-8"))

    def handle_a2a_jsonrpc(
        self,
        payload: dict[str, Any] | str | None = None,
        *,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous wrapper for :meth:`async_handle_a2a_jsonrpc`."""
        return _run_coro_sync(
            self.async_handle_a2a_jsonrpc(
                payload=payload, user=user, headers=headers, url=url, **kwargs
            )
        )

    async def async_handle_a2a_jsonrpc_stream(
        self,
        payload: dict[str, Any] | str | None = None,
        *,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute a streaming A2A JSON-RPC 2.0 request (``SendStreamingMessage``,
        ``message/stream``, ``SubscribeToTask``, ``tasks/resubscribe``) inside Agent Engine.

        - If the pre-dispatch guard (``CollectExecutor.admit``) or JSON-RPC validator
          rejects the request before SSE streaming begins, yields a single
          ``{"jsonrpc_response": <dict>}`` so the caller receives a standard JSON-RPC
          error response without opening an event stream.
        - Otherwise yields ``{"sse_data": <dict>}`` for each SSE ``data:`` frame emitted.
        """
        self.set_up()
        if url and self._executor is not None and self._artifact_store is not None:
            self._executor.bind_store(self._artifact_store, base_url=url)
        effective_payload = payload if payload is not None else kwargs
        if isinstance(effective_payload, str):
            raw_body = effective_payload.encode("utf-8")
        else:
            raw_body = json.dumps(effective_payload).encode("utf-8")

        _status, resp_headers, body_bytes = await self._dispatch_asgi(
            method="POST",
            path="/",
            body=raw_body,
            user=user,
            headers=headers,
        )
        content_type = resp_headers.get("content-type", "")
        text = body_bytes.decode("utf-8")
        if "text/event-stream" in content_type:
            for item in _parse_sse_events(text):
                yield item
        else:
            yield {"jsonrpc_response": json.loads(text)}

    def handle_a2a_jsonrpc_stream(
        self,
        payload: dict[str, Any] | str | None = None,
        *,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        """Synchronous streaming wrapper for :meth:`async_handle_a2a_jsonrpc_stream`."""

        async def _collect() -> list[dict[str, Any]]:
            return [
                item
                async for item in self.async_handle_a2a_jsonrpc_stream(
                    payload=payload, user=user, headers=headers, url=url, **kwargs
                )
            ]

        yield from _run_coro_sync(_collect())

    async def async_query(
        self,
        *,
        op: str | None = None,
        payload: dict[str, Any] | str | None = None,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Unified async query entrypoint supporting ``op="get_agent_card"`` and
        ``op="a2a_jsonrpc"`` (or direct JSON-RPC kwargs)."""
        if op == "get_agent_card":
            return await self.async_get_agent_card(url=url)
        return await self.async_handle_a2a_jsonrpc(
            payload=payload, user=user, headers=headers, url=url, **kwargs
        )

    def query(
        self,
        *,
        op: str | None = None,
        payload: dict[str, Any] | str | None = None,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Unified sync query entrypoint."""
        return _run_coro_sync(
            self.async_query(
                op=op,
                payload=payload,
                user=user,
                headers=headers,
                url=url,
                **kwargs,
            )
        )

    async def async_stream_query(
        self,
        *,
        op: str | None = None,
        payload: dict[str, Any] | str | None = None,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        message: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Unified async streaming entrypoint supporting both:
        1. Option B A2A JSON-RPC streaming (``op="a2a_jsonrpc_stream"`` or ``payload``/``jsonrpc``), and
        2. Option A ADK ``Runner`` streaming (``user_id``, ``session_id``, ``message``).
        """
        self.set_up()
        if (
            op == "a2a_jsonrpc_stream"
            or payload is not None
            or "jsonrpc" in kwargs
            or "method" in kwargs
        ):
            async for item in self.async_handle_a2a_jsonrpc_stream(
                payload=payload, user=user, headers=headers, url=url, **kwargs
            ):
                yield item
            return

        if user_id is not None and session_id is not None and message is not None:
            import google.genai.types as genai_types

            if self._adk_leg is None:
                from bridge.edge.llm_leg import LlmLeg

                self._adk_leg = LlmLeg(artifact_store=self._artifact_store)
            content = (
                message
                if isinstance(message, genai_types.Content)
                else genai_types.Content.model_validate(message)
            )
            async for event in self._adk_leg.runner().run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
            ):
                yield event.model_dump(mode="json", exclude_none=True)
            return

        raise ValueError(
            "BridgeA2aAgentEngine.async_stream_query requires either an A2A JSON-RPC "
            "payload (payload=/jsonrpc=) or ADK parameters (user_id=, session_id=, message=)."
        )

    def stream_query(
        self,
        *,
        op: str | None = None,
        payload: dict[str, Any] | str | None = None,
        user: str | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        message: Any = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        """Unified sync streaming entrypoint."""

        async def _collect() -> list[dict[str, Any]]:
            return [
                item
                async for item in self.async_stream_query(
                    op=op,
                    payload=payload,
                    user=user,
                    headers=headers,
                    url=url,
                    user_id=user_id,
                    session_id=session_id,
                    message=message,
                    **kwargs,
                )
            ]

        yield from _run_coro_sync(_collect())

    def register_operations(self) -> dict[str, list[str]]:
        """Register sync, async, and streaming A2A + ADK operations with Vertex AI Agent Engine."""
        return {
            "": ["query", "get_agent_card", "handle_a2a_jsonrpc"],
            "async": [
                "async_query",
                "async_get_agent_card",
                "async_handle_a2a_jsonrpc",
            ],
            "stream": ["stream_query", "handle_a2a_jsonrpc_stream"],
            "async_stream": [
                "async_stream_query",
                "async_handle_a2a_jsonrpc_stream",
            ],
        }


# --------------------------------------------------------------------------- #
# Thin Cloud Run Edge Proxy Routes (BRIDGE_AGENT_ENGINE_A2A=1)                #
# --------------------------------------------------------------------------- #


def _extract_authenticated_user(request: Request) -> str | None:
    """Return the authenticated caller's username from ``request.scope['user']`` when
    populated by ``AuthenticationMiddleware``, or ``None`` for unauthenticated callers."""
    user = request.scope.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        return getattr(user, "display_name", None) or getattr(user, "username", None)
    return None


async def _invoke_engine_card(a2a_engine: Any, *, url: str) -> dict[str, Any]:
    """Fetch the live ``AgentCard`` dict from ``a2a_engine`` (supporting both direct
    ``async_get_agent_card`` / ``get_agent_card`` methods and ``async_query`` / ``query``)."""
    if hasattr(a2a_engine, "async_get_agent_card"):
        res = await a2a_engine.async_get_agent_card(url=url)
    elif hasattr(a2a_engine, "get_agent_card"):
        res = a2a_engine.get_agent_card(url=url)
    elif hasattr(a2a_engine, "async_query"):
        res = await a2a_engine.async_query(op="get_agent_card", url=url)
    elif hasattr(a2a_engine, "query"):
        res = a2a_engine.query(op="get_agent_card", url=url)
    else:
        raise TypeError(
            f"Unsupported a2a_engine {type(a2a_engine).__name__}: missing "
            "async_get_agent_card / async_query"
        )
    if isinstance(res, dict):
        return res
    return dict(res)


async def _invoke_engine_jsonrpc(
    a2a_engine: Any,
    *,
    payload: dict[str, Any] | str,
    user: str | None,
    headers: dict[str, str] | None = None,
    url: str,
) -> dict[str, Any]:
    """Forward a unary JSON-RPC 2.0 request to ``a2a_engine``."""
    if hasattr(a2a_engine, "async_handle_a2a_jsonrpc"):
        res = await a2a_engine.async_handle_a2a_jsonrpc(
            payload=payload, user=user, headers=headers, url=url
        )
    elif hasattr(a2a_engine, "handle_a2a_jsonrpc"):
        res = a2a_engine.handle_a2a_jsonrpc(
            payload=payload, user=user, headers=headers, url=url
        )
    elif hasattr(a2a_engine, "async_query"):
        res = await a2a_engine.async_query(
            op="a2a_jsonrpc", payload=payload, user=user, headers=headers, url=url
        )
    elif hasattr(a2a_engine, "query"):
        res = a2a_engine.query(
            op="a2a_jsonrpc", payload=payload, user=user, headers=headers, url=url
        )
    else:
        raise TypeError(
            f"Unsupported a2a_engine {type(a2a_engine).__name__}: missing "
            "async_handle_a2a_jsonrpc / async_query"
        )
    if isinstance(res, dict):
        return res
    return dict(res)


async def _invoke_engine_jsonrpc_stream(
    a2a_engine: Any,
    *,
    payload: dict[str, Any] | str,
    user: str | None,
    headers: dict[str, str] | None = None,
    url: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Forward a streaming JSON-RPC 2.0 request to ``a2a_engine``."""
    if hasattr(a2a_engine, "async_handle_a2a_jsonrpc_stream"):
        async for chunk in a2a_engine.async_handle_a2a_jsonrpc_stream(
            payload=payload, user=user, headers=headers, url=url
        ):
            yield dict(chunk) if not isinstance(chunk, dict) else chunk
    elif hasattr(a2a_engine, "handle_a2a_jsonrpc_stream"):
        for chunk in a2a_engine.handle_a2a_jsonrpc_stream(
            payload=payload, user=user, headers=headers, url=url
        ):
            yield dict(chunk) if not isinstance(chunk, dict) else chunk
    elif hasattr(a2a_engine, "async_stream_query"):
        async for chunk in a2a_engine.async_stream_query(
            op="a2a_jsonrpc_stream",
            payload=payload,
            user=user,
            headers=headers,
            url=url,
        ):
            yield dict(chunk) if not isinstance(chunk, dict) else chunk
    elif hasattr(a2a_engine, "stream_query"):
        for chunk in a2a_engine.stream_query(
            op="a2a_jsonrpc_stream",
            payload=payload,
            user=user,
            headers=headers,
            url=url,
        ):
            yield dict(chunk) if not isinstance(chunk, dict) else chunk
    else:
        raise TypeError(
            f"Unsupported a2a_engine {type(a2a_engine).__name__}: missing "
            "async_handle_a2a_jsonrpc_stream / async_stream_query"
        )


def _forwarded_a2a_headers(request: Request) -> dict[str, str]:
    """Extract A2A protocol headers (`a2a-version`, `x-a2a-extensions`) from `request`."""
    out: dict[str, str] = {}
    for key in ("a2a-version", "x-a2a-extensions"):
        val = request.headers.get(key)
        if val:
            out[key] = val
    return out


def build_remote_a2a_routes(
    a2a_engine: Any,
    *,
    url: str = DEFAULT_URL,
    rpc_path: str = "/",
) -> list[BaseRoute]:
    """Build Starlette routes (``GET /.well-known/agent-card.json`` and ``POST /``)
    that proxy A2A discovery and JSON-RPC 2.0 (unary + SSE streaming) to ``a2a_engine``."""
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
    from sse_starlette.sse import EventSourceResponse

    async def _get_agent_card(_request: Request) -> JSONResponse:
        card_dict = await _invoke_engine_card(a2a_engine, url=url)
        return JSONResponse(card_dict)

    async def _post_rpc(request: Request) -> Response:
        raw_bytes = await request.body()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        user = _extract_authenticated_user(request)
        fwd_headers = _forwarded_a2a_headers(request)

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            resp = await _invoke_engine_jsonrpc(
                a2a_engine,
                payload=raw_text,
                user=user,
                headers=fwd_headers,
                url=url,
            )
            return JSONResponse(resp)

        method = parsed.get("method") if isinstance(parsed, dict) else None
        if isinstance(method, str) and method in STREAMING_A2A_METHODS:
            stream_gen = _invoke_engine_jsonrpc_stream(
                a2a_engine,
                payload=parsed,
                user=user,
                headers=fwd_headers,
                url=url,
            )
            try:
                first = await anext(stream_gen)
            except StopAsyncIteration:
                return EventSourceResponse(iter(()))

            if "jsonrpc_response" in first:
                await stream_gen.aclose()
                return JSONResponse(first["jsonrpc_response"])

            async def _sse_Chain() -> AsyncGenerator[str, None]:
                first_data = first.get("sse_data", first)
                yield (
                    first_data
                    if isinstance(first_data, str)
                    else json.dumps(first_data)
                )
                async for item in stream_gen:
                    data = item.get("sse_data", item)
                    yield data if isinstance(data, str) else json.dumps(data)

            return EventSourceResponse(_sse_Chain())

        resp = await _invoke_engine_jsonrpc(
            a2a_engine,
            payload=parsed if isinstance(parsed, dict) else raw_text,
            user=user,
            headers=fwd_headers,
            url=url,
        )
        return JSONResponse(resp)

    return [
        Route(AGENT_CARD_WELL_KNOWN_PATH, _get_agent_card, methods=["GET"]),
        Route(rpc_path, _post_rpc, methods=["POST"]),
    ]


# Module-level deployable Agent Engine application instance for Vertex AI Agent Engine.
agent_engine_app = BridgeA2aAgentEngine()
