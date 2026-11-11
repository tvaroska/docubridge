"""Assembly of the Bridge canonical A2A server (JSON-RPC 2.0 over Starlette).

Wires the ``CollectExecutor`` + task store into an ``a2a-sdk``
``DefaultRequestHandler`` and mounts the standard A2A routes on a ``Starlette``
app. Mirrors the v1 branch of ``google/adk/a2a/_compat.py`` route wiring, but
using ``a2a-sdk``'s own route factories (Decision 2 — no ADK executor).

A2A SDK note: the installed 1.x has no ``A2AStarletteApplication`` assembler, so
we compose the app from ``create_agent_card_routes`` + ``create_jsonrpc_routes``
directly (the plan's documented fallback). The JSON-RPC endpoint handles all six
spec ops (``message/send``, ``message/stream``, ``tasks/get``, ``tasks/list``,
``tasks/cancel``, ``tasks/resubscribe``) over one POST route; the agent card is
served at ``/.well-known/agent-card.json``.

**Pre-dispatch guard (S3-edge-1 G/R5/R6).** The handler is
:class:`EdgeRequestHandler`, a ``DefaultRequestHandler`` whose ``message/send`` and
``message/stream`` first call ``CollectExecutor.admit`` with the stored task and the
call-context user. A refusal (a message on a task parked for a reviewer, a resolution
with nothing pending, a malformed / unauthorized / mismatched resolution) is a
JSON-RPC error raised BEFORE the SDK touches the task — raised inside
``AgentExecutor.execute`` it would mark the existing task FAILED.

**RFP opener (S0-arch-19).** With ``BRIDGE_RFP_PROCESS`` set, ``build_a2a_app`` binds an
opener-only ``RfpBoard`` over the durable shared ``exchange_store``
(``bridge.edge.rfp_open.rfp_board_from_env``) to the executor, so a ``CollectRequest`` of
that home process — or of any emergent-Collect process installed live in the skill
registry afterwards (S4-usecase-4) — opens its RFP onto the same store the RFP board host
(``bridge.dashboard.board_host``) serves and drains email onto.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from typing import TYPE_CHECKING, Any

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import TaskStore
from a2a.types import AgentCard, Message, SendMessageRequest, Task
from google.adk.artifacts import BaseArtifactService
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount

from bridge.edge.agent_engine_a2a import (
    BridgeA2aAgentEngine,
    build_remote_a2a_routes,
    resolve_a2a_engine,
)
from bridge.edge.artifacts import (
    ARTIFACTS_PREFIX,
    build_artifact_store,
    build_fetch_app,
    build_fetch_signer,
)
from bridge.edge.card import DEFAULT_URL, build_agent_card, resolved_card_url
from bridge.edge.executor import CollectExecutor
from bridge.edge.pathroute import EdgeMode, resolve_edge_mode
from bridge.edge.pushguard import guarded_push_config_store
from bridge.edge.rfp_open import rfp_board_from_env
from bridge.ports import Port, get_adapter, resolve_mode
from bridge.satisfaction import SatisfactionAssessor, resolve_assessor
from bridge.telemetry import OtelAsgiMiddleware

if TYPE_CHECKING:
    import httpx
    from a2a.server.tasks import PushNotificationConfigStore, PushNotificationSender
    from starlette.authentication import AuthenticationBackend

    from bridge.dashboard.board import RfpBoard
    from bridge.edge.llm_leg import LlmLeg

# The JSON-RPC endpoint path (the card's interface url points here).
RPC_PATH = "/"

# SKILL-4 live doctype-add is a filesystem drop: a skill folder placed under
# ``BRIDGE_SKILLS_DIR`` (via the skill_registry port) is reflected on the next
# discovery GET — no redeploy, no HTTP write surface. An authenticated remote
# install endpoint (behind per-party Gateway scoping) is deferred to P7
# (design/zones.md); the unauthenticated write endpoint was removed (S0-sec-1).


class EdgeRequestHandler(DefaultRequestHandler):
    """``DefaultRequestHandler`` + the edge's pre-dispatch guard (G/R5/R6).

    Both send ops await :meth:`CollectExecutor.admit` with the stored task (when the
    message names one) and ``context.user`` (the authentication middleware's
    principal; ``UnauthenticatedUser`` without one) BEFORE delegating — a refusal
    never reaches the active task, so the task is unchanged. The stream variant
    raises before its first yield, which the JSON-RPC dispatcher returns as an error.
    """

    def __init__(self, *, agent_executor: CollectExecutor, **kwargs: Any) -> None:
        super().__init__(agent_executor=agent_executor, **kwargs)
        self._edge_executor = agent_executor

    async def _admit(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> None:
        message: Message = params.message
        task: Task | None = None
        if message.task_id:
            task = await self.task_store.get(message.task_id, context)
            if task is None:
                # Unknown — or another principal's (owner-scoped store) — task: the
                # SDK raises TaskNotFoundError as usual; nothing to guard.
                return
        await self._edge_executor.admit(message, task, context.user)

    async def on_message_send(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Message | Task:
        """Handle a synchronous A2A message/send request with caller identity propagation."""
        await self._admit(params, context)
        return await super().on_message_send(params, context)

    async def on_message_send_stream(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Any, None]:
        """Handle a streaming A2A message/stream request with caller identity propagation."""
        await self._admit(params, context)
        async for event in super().on_message_send_stream(params, context):
            yield event


def build_a2a_app(
    *,
    url: str = DEFAULT_URL,
    executor: CollectExecutor | None = None,
    task_store: TaskStore | None = None,
    agent_card: AgentCard | None = None,
    artifact_store: BaseArtifactService | None = None,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    push_httpx_client: httpx.AsyncClient | None = None,
    edge_mode: EdgeMode | str | None = None,
    llm_leg: LlmLeg | None = None,
    reviewers: Iterable[str] | str | None = None,
    auth_backend: AuthenticationBackend | None = None,
    satisfaction: SatisfactionAssessor | str | None = None,
    rfp_board: RfpBoard | None = None,
    a2a_engine: Any = None,
    vault_store: Any = None,
    operational_store: Any = None,
    knowledge_catalog: Any = None,
) -> Starlette:
    """Build the Bridge A2A Starlette app (with the ART-2 fetch endpoint).

    ``task_store`` is wired via the ``task_store`` port (durable when
    ``BRIDGE_TASK_DB_URL`` is set → ``DatabaseTaskStore``; else in-memory). An
    injected ``task_store`` still wins, so existing tests are unaffected.

    **ART-2 shared store (Decision 4).** One ``artifact_store`` is created (or
    injected) and bound to BOTH the executor (inbound writer, ``save_artifact``)
    and the mounted ``/artifacts`` fetch app (outbound reader, ``load_artifact``).
    When a caller passes a pre-built ``executor`` (the test pattern), its store is
    re-bound to this same instance via ``executor.bind_store`` so writer == reader
    — two instances would leave every fetch 404ing. The
    ``InMemoryArtifactService``↔``GcsArtifactService`` (``build_artifact_store``)
    and direct-serve↔signed-URL (``build_fetch_signer``) swaps are env-driven
    (``BRIDGE_ARTIFACT_BUCKET``, P7-3), encapsulated in ``artifacts.py`` (not a port).

    **P4-4 push notifications (DUR-6).** Wire ``push_config_store`` (via the
    ``task_store`` port, mirroring ``build_task_store``) and ``push_sender`` (a
    default ``BasePushNotificationSender`` over the supplied or default httpx
    client) into the handler to enable the four push-config ops and automatic
    webhook emission on task status/artifact events. Tests inject
    ``push_httpx_client`` (bound to a mock receiver) to prove emission;
    production uses a default client. P7: bind the default client to the app
    lifespan and close on shutdown.

    **S3-edge-3 edge mode (S3-edge-1 D2).** The routing mode is resolved ONCE here:
    an explicit ``edge_mode`` wins, else ``BRIDGE_EDGE_MODE``; unset/blank →
    ``deterministic`` (every message model-free, as before); any other value raises
    ``ValueError`` (fail closed — the app does not start). In ``hybrid`` mode the
    ``document_bridge`` LLM leg is constructed (or the injected ``llm_leg`` used) and
    bound to the SAME shared artifact store (writer == reader == eraser), then bound to
    the executor via ``executor.bind_edge``; text-only intents route to it. In
    deterministic mode no leg is imported or constructed and an injected ``llm_leg``
    is ignored. Its sessions come from the sessions port (``LlmLeg``), never a
    hardcoded store.

    **S3-edge-6 HITL resume authorization (S3-edge-1 D10).** ``reviewers`` is the
    reviewer principal allow-list (explicit wins, else ``BRIDGE_EDGE_REVIEWERS``;
    unset/empty → every resolution refused — fail closed). The principal is the A2A
    call-context user, populated by ``auth_backend`` (a Starlette
    ``AuthenticationBackend``) through ``AuthenticationMiddleware``; without a backend
    every caller is unauthenticated and every resolution is refused. The edge ships
    no backend: a deploy-verified principal source is a follow-up.

    **S0-arch-16 satisfaction proposal (COLL-2).** ``satisfaction`` is resolved ONCE
    here by :func:`bridge.satisfaction.resolve_assessor`: an injected assessor wins,
    else an explicit mode string, else ``BRIDGE_SATISFACTION``. Unset/blank/``off`` →
    no assessor (every deterministic turn model-free, per-entry outstanding);
    ``llm`` → the model-backed assessor on ``BRIDGE_MODEL`` (built lazily — no
    credentials needed at app build); any other value raises ``ValueError`` (fail
    closed — the app does not start). It is bound via ``executor.bind_assessor``.

    **S0-arch-19 RFP opener.** ``rfp_board`` (injected, used as-is — the test idiom) else
    :func:`bridge.edge.rfp_open.rfp_board_from_env`: with ``BRIDGE_RFP_PROCESS`` set, ONE
    opener-only ``RfpBoard`` whose home process is that emergent-Collect skill, over the
    port-resolved durable ``exchange_store`` (an in-memory store → ``RuntimeError``; a
    skill that is not emergent-Collect → ``ValueError``; the app does not start). The board
    is live: an emergent-Collect process installed in the skill registry after build is
    opened too, with no rebuild (S4-usecase-4). Unset/blank → no board and
    the edge is unchanged. Bound via ``executor.bind_rfp`` and exposed as
    ``app.state.rfp_board`` (``None`` when off). No lifespan is added: the
    ``SqlExchangeStore`` loop thread is a daemon and each op commits on its own.
    """
    # P7-5 served-card same-origin: when the caller does not pass an explicit url
    # (i.e. it is the DEFAULT_URL default), honor BRIDGE_PUBLIC_URL so a deployed
    # container advertises its Gateway-authorized origin in BOTH the card url and the
    # executor base_url — else a consumer's RemoteA2aAgent raises
    # AgentCardResolutionError at resolve time under adk >=2.7 (same-origin check).
    # An explicit url still wins, and env-unset falls back to DEFAULT_URL (local/test
    # behavior unchanged).
    if url == DEFAULT_URL:
        url = resolved_card_url()

    # Option B (A2A server on Gemini platform Agent Engine): when ``a2a_engine`` is injected
    # or ``BRIDGE_AGENT_ENGINE_A2A=1`` is set, delegate ``/.well-known/agent-card.json``
    # and JSON-RPC ``POST /`` to the Agent Engine runtime while keeping ``/artifacts``
    # mounted on the thin edge over the shared artifact store.
    active_a2a_engine = resolve_a2a_engine(a2a_engine)
    if active_a2a_engine is not None:
        if (
            isinstance(active_a2a_engine, BridgeA2aAgentEngine)
            and artifact_store is None
            and active_a2a_engine._injected.get("artifact_store") is not None
        ):
            store = active_a2a_engine.artifact_store
        else:
            store = artifact_store or build_artifact_store()
            if (
                isinstance(active_a2a_engine, BridgeA2aAgentEngine)
                and active_a2a_engine._app is None
                and active_a2a_engine._injected.get("artifact_store") is None
            ):
                active_a2a_engine._injected["artifact_store"] = store

        board = rfp_board or (
            active_a2a_engine.rfp_board
            if isinstance(active_a2a_engine, BridgeA2aAgentEngine)
            else rfp_board_from_env()
        )
        routes = [
            *build_remote_a2a_routes(active_a2a_engine, url=url, rpc_path=RPC_PATH),
            Mount(
                ARTIFACTS_PREFIX,
                app=build_fetch_app(store, signer=build_fetch_signer()),
            ),
        ]
        edge_mw = [Middleware(OtelAsgiMiddleware, service_component="edge_proxy")]
        if auth_backend is not None:
            edge_mw.append(Middleware(AuthenticationMiddleware, backend=auth_backend))
        app = Starlette(routes=routes, middleware=edge_mw)
        app.state.rfp_board = board
        app.state.a2a_engine = active_a2a_engine
        return app

    card = agent_card or build_agent_card(url)
    # ART-2 store: GCS on deploy (BRIDGE_ARTIFACT_BUCKET) else in-memory (P7-3);
    # an injected store still wins (existing tests unaffected).
    store = artifact_store or build_artifact_store()

    active_executor = executor or CollectExecutor(base_url=url)
    active_executor.bind_store(store, base_url=url)

    # D2: resolve the routing mode once, at build (invalid → ValueError here).
    mode = resolve_edge_mode(edge_mode)
    leg = None
    if mode is EdgeMode.hybrid:
        if llm_leg is None:
            # Lazy: the deterministic edge never imports or constructs the LLM leg.
            from bridge.edge.llm_leg import LlmLeg

            llm_leg = LlmLeg(artifact_store=store)
        llm_leg.bind_store(store)  # writer == reader == eraser (ART-2 Decision 4)
        leg = llm_leg
    active_executor.bind_edge(mode, llm_leg=leg, reviewers=reviewers)
    # COLL-2: resolve the satisfaction assessor once, at build (invalid → ValueError).
    active_executor.bind_assessor(resolve_assessor(satisfaction))
    # S0-arch-19: the RFP opener — injected wins; env-gated; None = no opener.
    board = rfp_board or rfp_board_from_env()
    active_executor.bind_rfp(board)
    if (
        vault_store is not None
        or operational_store is not None
        or knowledge_catalog is not None
    ):
        active_executor.bind_enterprise_stores(
            vault_store=vault_store,
            operational_store=operational_store,
            knowledge_catalog=knowledge_catalog,
        )

    store_port = get_adapter(Port.task_store, resolve_mode(Port.task_store))
    active_task_store = task_store or store_port.build_task_store()

    # P4-4: Build push_config_store via the task_store port (Decision 1) and
    # default sender (Decision 2).
    # S0-sec-2: wrap the config store so client-supplied webhook URLs that
    # resolve to non-global IPs (metadata / internal hosts) are rejected at
    # registration. Applies whether the store is injected or port-built.
    active_push_config_store = guarded_push_config_store(
        push_config_store or store_port.build_push_config_store()
    )
    if push_sender is None:
        from a2a.server.tasks import BasePushNotificationSender

        # P7: bind to app lifespan / close on shutdown
        client = push_httpx_client
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
        # SKILL-4 no-redeploy mechanism: ignore the build-time snapshot and rebuild
        # the discovery card from the live skill registry on every GET, so a skill
        # dropped into BRIDGE_SKILLS_DIR at runtime appears immediately.
        return build_agent_card(url)

    routes = (
        create_agent_card_routes(card, card_modifier=_live_card)
        + create_jsonrpc_routes(handler, RPC_PATH, enable_v0_3_compat=True)
        + [
            # signer=None locally (stream bytes); on deploy build_fetch_signer()
            # returns a GCS signer so the fetch route redirects to a signed URL
            # (bytes never on the wire, P7-3).
            Mount(
                ARTIFACTS_PREFIX,
                app=build_fetch_app(store, signer=build_fetch_signer()),
            ),
        ]
    )
    edge_mw = [Middleware(OtelAsgiMiddleware, service_component="edge")]
    if auth_backend is not None:
        edge_mw.append(Middleware(AuthenticationMiddleware, backend=auth_backend))
    app = Starlette(routes=routes, middleware=edge_mw)
    app.state.rfp_board = board
    app.state.a2a_engine = None
    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:  # pragma: no cover
    """Run the edge with uvicorn for manual runs (not exercised by tests).

    Loopback default (S0-sec-3). For the deployed Cloud Run entrypoint that binds
    ``0.0.0.0:$PORT`` see ``serve_container`` — a SEPARATE function; this one's
    loopback default is deliberately left unchanged.
    """
    import uvicorn

    url = f"http://{host}:{port}/"
    uvicorn.run(build_a2a_app(url=url), host=host, port=port)


def serve_container() -> None:  # pragma: no cover
    """Cloud Run container entrypoint — bind ``0.0.0.0:$PORT`` (P7-9).

    Cloud Run runs the image and requires the process to listen on ``0.0.0.0`` at
    the port named by the ``PORT`` env (default 8080). This is a SEPARATE entrypoint
    from ``serve()`` — the loopback default of ``serve()`` stays unchanged (S0-sec-3).
    Binding ``0.0.0.0`` here does NOT reopen S0-sec-3: on the deployed path ingress is
    Gateway-locked (``INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`` when ``enable_gateway``,
    the runtime Terraform module), so the container listens behind the Gateway, not on a
    public interface.

    The served Agent Card advertises ``BRIDGE_PUBLIC_URL`` (the Gateway-authorized
    origin, P7-5) via ``resolved_card_url(None)`` so a consumer's ``RemoteA2aAgent``
    passes the adk >=2.7 same-origin card validation. Every port routes through its
    adapter (``BRIDGE_PORT_MODE=gcp`` → the Cloud SQL / GCS / Cloud Tasks / Gemini
    adapters); no store is hardcoded here (the P7-8 service-wiring guard is the backstop).
    ``BRIDGE_EDGE_MODE`` selects the edge routing mode (``deterministic`` default |
    ``hybrid``; anything else fails start), resolved in ``build_a2a_app``.
    ``BRIDGE_RFP_PROCESS`` (set when the RFP board service is enabled) binds the RFP
    opener over the port-resolved durable exchange store (S0-arch-19).

    Wired as the ``bridge-serve`` console script (``pyproject.toml``) so the image's
    ``CMD ["bridge-serve"]`` boots it.
    """
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    app = build_a2a_app(url=resolved_card_url(None))
    # 0.0.0.0 is required by Cloud Run; ingress is Gateway-locked on the deployed path.
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":  # pragma: no cover
    serve()
