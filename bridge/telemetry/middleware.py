"""W3C traceparent / Google X-Cloud-Trace-Context propagation, trace_step, and ASGI middleware."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator, Mapping
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
)

from bridge.telemetry.exporters import (
    _clean_attr_value,
    ensure_telemetry_initialized,
    get_tracer,
)

_TRACEPARENT_RE = re.compile(
    r"^[ \t]*00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})[ \t]*$"
)
_CLOUD_TRACE_RE = re.compile(
    r"^[ \t]*([0-9a-fA-F]{32})(?:/([0-9]+))?(?:;o=([01]))?[ \t]*$"
)


def parse_incoming_span_context(
    headers: Mapping[str, str] | None,
) -> SpanContext | None:
    """Extract a parent ``SpanContext`` from W3C ``traceparent`` or Google
    ``X-Cloud-Trace-Context`` request headers."""
    if not headers:
        return None
    lower = {
        k.lower(): v
        for k, v in headers.items()
        if isinstance(k, str) and isinstance(v, str)
    }

    tp = lower.get("traceparent", "").strip()
    if tp:
        m = _TRACEPARENT_RE.match(tp)
        if m:
            trace_id_int = int(m.group(1), 16)
            span_id_int = int(m.group(2), 16)
            flags_int = int(m.group(3), 16)
            if trace_id_int != 0 and span_id_int != 0:
                return SpanContext(
                    trace_id=trace_id_int,
                    span_id=span_id_int,
                    is_remote=True,
                    trace_flags=TraceFlags(flags_int & 0x01 or 0x01),
                )

    xct = lower.get("x-cloud-trace-context", "").strip()
    if xct:
        m = _CLOUD_TRACE_RE.match(xct)
        if m:
            trace_id_int = int(m.group(1), 16)
            span_dec = m.group(2)
            span_id_int = (int(span_dec) & 0xFFFFFFFFFFFFFFFF) if span_dec else 1
            if span_id_int == 0:
                span_id_int = 1
            if trace_id_int != 0:
                return SpanContext(
                    trace_id=trace_id_int,
                    span_id=span_id_int,
                    is_remote=True,
                    trace_flags=TraceFlags.SAMPLED,
                )
    return None


def format_outgoing_trace_headers() -> dict[str, str]:
    """Build ``traceparent`` and ``x-cloud-trace-context`` headers from the active OTel span."""
    current_span = trace.get_current_span()
    ctx = current_span.get_span_context() if current_span else None
    if not ctx or not ctx.is_valid:
        return {}
    trace_hex = f"{ctx.trace_id:032x}"
    span_hex = f"{ctx.span_id:016x}"
    flags_hex = "01" if (ctx.trace_flags & TraceFlags.SAMPLED) else "00"
    return {
        "traceparent": f"00-{trace_hex}-{span_hex}-{flags_hex}",
        "x-cloud-trace-context": f"{trace_hex}/{ctx.span_id};o=1",
    }


@contextlib.contextmanager
def parent_trace_context_from_headers(
    headers: Mapping[str, str] | None,
) -> Iterator[SpanContext | None]:
    """Attach the remote ``SpanContext`` parsed from ``headers`` for the duration of the block."""
    ensure_telemetry_initialized()
    remote_ctx = parse_incoming_span_context(headers)
    if remote_ctx is None:
        yield None
        return
    ctx = trace.set_span_in_context(NonRecordingSpan(remote_ctx))
    token = otel_context.attach(ctx)
    try:
        yield remote_ctx
    finally:
        otel_context.detach(token)


@contextlib.contextmanager
def trace_step(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    """Start an OpenTelemetry span for a pipeline step, recording attributes and exceptions."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name, kind=kind) as span:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    span.set_attribute(k, _clean_attr_value(v))
        try:
            yield span
            if span.status.status_code == StatusCode.UNSET:
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


class OtelAsgiMiddleware:
    """ASGI middleware that extracts incoming ``traceparent`` / ``X-Cloud-Trace-Context``
    headers, starts a server span for each HTTP request, and echoes ``X-Trace-Id`` on
    the response."""

    def __init__(self, app: Any, service_component: str = "http") -> None:
        self.app = app
        self.service_component = service_component

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or ()
        hdr_dict = {
            k.decode("latin-1", errors="ignore").lower(): v.decode(
                "latin-1", errors="ignore"
            )
            for k, v in raw_headers
        }
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        span_name = f"docubridge.http.{self.service_component} {method} {path}"

        with (
            parent_trace_context_from_headers(hdr_dict),
            trace_step(
                span_name,
                {
                    "http.method": method,
                    "http.target": path,
                    "docubridge.component": self.service_component,
                },
                kind=SpanKind.SERVER,
            ) as span,
        ):
            ctx = span.get_span_context()
            trace_id_hex = f"{ctx.trace_id:032x}" if ctx and ctx.is_valid else ""
            span_id_hex = f"{ctx.span_id:016x}" if ctx and ctx.is_valid else ""

            async def _wrapped_send(message: dict[str, Any]) -> None:
                if message.get("type") == "http.response.start":
                    status_code = int(message.get("status", 200))
                    span.set_attribute("http.status_code", status_code)
                    if status_code >= 500:
                        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
                    if trace_id_hex:
                        headers_list = list(message.get("headers") or ())
                        headers_list.append(
                            (b"x-docubridge-trace-id", trace_id_hex.encode("ascii"))
                        )
                        if span_id_hex:
                            headers_list.append(
                                (
                                    b"traceparent",
                                    f"00-{trace_id_hex}-{span_id_hex}-01".encode(
                                        "ascii"
                                    ),
                                )
                            )
                        message = {**message, "headers": headers_list}
                await send(message)

            await self.app(scope, receive, _wrapped_send)
