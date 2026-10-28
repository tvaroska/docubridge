"""Thin Cloud Run Edge Proxy Routes (BRIDGE_AGENT_ENGINE_A2A=1)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from bridge.edge.card import DEFAULT_URL
from bridge.telemetry import format_outgoing_trace_headers

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
    """Extract A2A protocol and OpenTelemetry trace headers from `request`."""
    out: dict[str, str] = {}
    for key in (
        "a2a-version",
        "x-a2a-extensions",
        "traceparent",
        "x-cloud-trace-context",
    ):
        val = request.headers.get(key)
        if val:
            out[key] = val
    # Ensure active span context from OtelAsgiMiddleware propagates into Agent Engine
    for k, v in format_outgoing_trace_headers().items():
        out.setdefault(k, v)
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

            async def _sse_chain() -> AsyncGenerator[str, None]:
                first_data = first.get("sse_data", first)
                yield (
                    first_data
                    if isinstance(first_data, str)
                    else json.dumps(first_data)
                )
                async for item in stream_gen:
                    data = item.get("sse_data", item)
                    yield data if isinstance(data, str) else json.dumps(data)

            return EventSourceResponse(_sse_chain())

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
