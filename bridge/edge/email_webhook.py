"""Graph webhook, Cloud Scheduler drain routes, and host wiring for email intake."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from bridge.edge.email_processor import (
    MAILBOX_ENV,
    EmailProcessor,
    MailOutcome,
    _default_port,
    _ExtractorLike,
)
from bridge.email_ingress.graph import (
    MESSAGE_ID_RE,
    GraphError,
    GraphMailClient,
    MessageTooLarge,
    client_state_from_env,
)
from bridge.email_ingress.mime import landing_key, max_mail_bytes
from bridge.ports import EmailIngressPort, Port, PortMode, resolve_mode
from bridge.ports.gcp import EMAIL_BUCKET_ENV
from bridge.ports.local import EMAIL_DIR_ENV
from bridge.ports.protocols import EMAIL_KEY_PATTERN

if TYPE_CHECKING:
    from google.adk.artifacts import BaseArtifactService

    from bridge.dashboard.board import RfpBoard
    from bridge.knowledge_catalog import CatalogRole

_LOG = logging.getLogger("bridge.edge.email_ingress")

DRAIN_INTERVAL_ENV = "BRIDGE_EMAIL_DRAIN_INTERVAL"
DEFAULT_DRAIN_INTERVAL = 30.0
NOTIFICATIONS_PATH = "/email/graph/notifications"
MAX_NOTIFICATION_BYTES = 256 * 1024
MAX_VALIDATION_TOKEN = 1024
_KEY_RE = re.compile(EMAIL_KEY_PATTERN)
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """Return the request body, or ``None`` once streamed bytes exceed ``limit``."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def oversize_key(message_id: str) -> str:
    """Return the marker key for a Graph message refused as oversize (never landed)."""
    return hashlib.sha256(f"graph:{message_id}".encode()).hexdigest()[:32]


def drain_interval_from_env() -> float:
    """Parse ``BRIDGE_EMAIL_DRAIN_INTERVAL`` in seconds (unset/blank → 30; ``0`` → no loop)."""
    raw = os.environ.get(DRAIN_INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_DRAIN_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"{DRAIN_INTERVAL_ENV} must be a number of seconds; got {raw!r}"
        ) from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"{DRAIN_INTERVAL_ENV} must be a finite, non-negative number of seconds; "
            f"got {raw!r}"
        )
    return value


def _landing_configured() -> bool:
    local = resolve_mode(Port.email_ingress) is PortMode.local
    env = EMAIL_DIR_ENV if local else EMAIL_BUCKET_ENV
    return bool(os.environ.get(env, "").strip())


def email_processor_from_env(
    board: RfpBoard,
    *,
    extraction: _ExtractorLike | None = None,
    artifact_store: BaseArtifactService | None = None,
    base_url: str | None = None,
) -> EmailProcessor | None:
    """Return the env-wired processor for the board-owning host, or ``None``."""
    if not os.environ.get(MAILBOX_ENV, "").strip() or not _landing_configured():
        return None
    return EmailProcessor(
        board=board,
        extraction=extraction,
        artifact_store=artifact_store,
        base_url=base_url,
    )


@contextlib.asynccontextmanager
async def email_drain_loop(
    processor: EmailProcessor, interval: float
) -> AsyncIterator[None]:
    """Drain ``processor`` periodically while open (``interval <= 0`` disables polling)."""
    if interval <= 0:
        yield
        return

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await processor.drain()
            except Exception:  # noqa: BLE001 - periodic loop must log and retry on next tick
                _LOG.exception("periodic email drain failed; will retry next tick")

    task = asyncio.create_task(_loop(), name="bridge-email-drain")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def mount_email_drain_routes(
    app: FastAPI,
    processor: EmailProcessor,
    *,
    port: EmailIngressPort | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> None:
    """Bind gated ``POST /email/drain`` and ``GET /email/outcomes/{key}`` onto ``app``."""
    from bridge.user_interaction.demo_session import email_drain_auth_dependency

    marker_port = port if port is not None else processor.port
    drain_dep = email_drain_auth_dependency(
        principal_roles=principal_roles, require_auth=require_auth
    )
    outcome_dep = email_drain_auth_dependency(
        principal_roles=principal_roles,
        require_auth=require_auth,
        allow_operator_read=True,
    )

    @app.post("/email/drain", dependencies=[drain_dep])
    async def drain() -> list[dict[str, Any]]:
        return [o.model_dump(mode="json") for o in await processor.drain()]

    @app.get("/email/outcomes/{key}", response_model=None, dependencies=[outcome_dep])
    async def outcome(key: str) -> Response | dict[str, Any]:
        if not _KEY_RE.fullmatch(key):
            return Response(status_code=404)
        marker = await run_in_threadpool(marker_port.processed, key)
        return Response(status_code=404) if marker is None else marker


def _validate_graph_item(
    item: object, *, client_state: str, tenant_id: str | None
) -> str | None:
    if not isinstance(item, dict):
        return None
    state = item.get("clientState")
    resource = item.get("resourceData")
    message_id = resource.get("id") if isinstance(resource, dict) else None
    if not isinstance(state, str) or not hmac.compare_digest(
        state.encode(), client_state.encode()
    ):
        _LOG.warning("email notification ignored: clientState mismatch")
        return None
    if tenant_id and item.get("tenantId") != tenant_id:
        _LOG.warning("email notification ignored: tenantId mismatch")
        return None
    if not isinstance(message_id, str) or not MESSAGE_ID_RE.fullmatch(message_id):
        _LOG.warning("email notification ignored: invalid message id")
        return None
    return message_id


async def _ingest_graph_message(
    message_id: str, *, graph: GraphMailClient, port: EmailIngressPort
) -> Literal["landed", "duplicate", "ignored", "retry"]:
    try:
        raw = await run_in_threadpool(graph.fetch_mime, message_id)
    except MessageTooLarge:
        key = oversize_key(message_id)
        outcome = MailOutcome(key=key, status="quarantined", reason="oversize")
        _LOG.warning("email %s quarantined: oversize (graph id)", key)
        try:
            await run_in_threadpool(
                port.mark_processed, key, outcome.model_dump(mode="json")
            )
        except Exception:  # noqa: BLE001 - marker write error asks Graph to retry via 503
            _LOG.exception("email oversize marker failed; asking Graph to retry")
            return "retry"
        return "ignored"
    except GraphError as exc:
        _LOG.warning("email fetch failed (%s); asking Graph to retry", exc)
        return "retry"
    key = landing_key(raw)
    try:
        fresh = await run_in_threadpool(port.land, key, raw)
    except Exception:  # noqa: BLE001 - landing write error asks Graph to retry via 503
        _LOG.exception("email landing failed; asking Graph to retry")
        return "retry"
    return "landed" if fresh else "duplicate"


@dataclass(frozen=True)
class _WebhookDeps:
    port: EmailIngressPort
    graph: GraphMailClient | None
    client_state: str | None
    processor: EmailProcessor | None
    tenant_id: str | None


async def _run_background_drain(processor: EmailProcessor) -> None:
    try:
        await processor.drain()
    except Exception:  # noqa: BLE001 - background drain retries on next webhook/tick
        _LOG.exception("background email drain failed; will retry on next drain")


async def _handle_graph_notifications(
    request: Request,
    background: BackgroundTasks,
    validation_token: str | None,
    deps: _WebhookDeps,
) -> Response:
    if deps.graph is None or not deps.client_state:
        return Response(status_code=404)
    if validation_token is not None:
        return (
            Response(status_code=400)
            if len(validation_token) > MAX_VALIDATION_TOKEN
            else PlainTextResponse(validation_token, status_code=200)
        )
    body = await _read_capped(request, MAX_NOTIFICATION_BYTES)
    if body is None:
        return Response(status_code=413)
    try:
        items = json.loads(body)["value"]
        if not isinstance(items, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return Response(status_code=400)

    counts = {"landed": 0, "duplicates": 0, "ignored": 0}
    for item in items:
        message_id = _validate_graph_item(
            item, client_state=deps.client_state, tenant_id=deps.tenant_id
        )
        if message_id is None:
            counts["ignored"] += 1
            continue
        verdict = await _ingest_graph_message(
            message_id, graph=deps.graph, port=deps.port
        )
        if verdict == "retry":
            return Response(status_code=503)
        counts["duplicates" if verdict == "duplicate" else verdict] += 1
    if deps.processor is not None and counts["landed"]:
        background.add_task(_run_background_drain, deps.processor)
    return JSONResponse(counts, status_code=202)


def build_email_app(
    *,
    port: EmailIngressPort | None = None,
    graph: GraphMailClient | None = None,
    client_state: str | None = None,
    processor: EmailProcessor | None = None,
    tenant_id: str | None = None,
) -> FastAPI:
    """Build the standalone email intake host (webhook + optional drain)."""
    if graph is not None and not client_state:
        raise ValueError("build_email_app(graph=...) requires client_state")
    if graph is not None:
        max_mail_bytes()
    resolved_port = (
        port
        if port is not None
        else (processor.port if processor is not None else _default_port())
    )
    deps = _WebhookDeps(
        port=resolved_port,
        graph=graph,
        client_state=client_state,
        processor=processor,
        tenant_id=tenant_id,
    )
    app = FastAPI(title="Document Bridge — email intake")

    @app.post(NOTIFICATIONS_PATH)
    async def graph_notifications(
        request: Request,
        background: BackgroundTasks,
        validationToken: str | None = None,
    ) -> Response:
        return await _handle_graph_notifications(
            request, background, validationToken, deps
        )

    if processor is not None:
        mount_email_drain_routes(app, processor, port=resolved_port)
    return app


def serve_email_webhook_container() -> None:  # pragma: no cover
    """Cloud Run entrypoint for the public, webhook-only email service."""
    import uvicorn

    graph = GraphMailClient.from_env()
    tenant = graph.tenant_id if _GUID_RE.fullmatch(graph.tenant_id) else None
    app = build_email_app(
        graph=graph, client_state=client_state_from_env(), tenant_id=tenant
    )
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)  # noqa: S104 - Cloud Run requires 0.0.0.0 bind
