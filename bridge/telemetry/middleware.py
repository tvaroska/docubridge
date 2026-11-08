"""OpenTelemetry context propagation, ASGI middleware, and `trace_step` helper."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import baggage, context, trace
from opentelemetry.trace import (
    NonRecordingSpan,
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
from bridge.telemetry.outcome import (
    OUTCOME_ID_HEADER,
    OUTCOME_ID_KEY,
    OUTCOME_OWNER_KEY,
    get_current_outcome_id,
    get_current_outcome_owner,
    inject_outcome_headers,
    sanitize_inbound_headers,
)

logger = logging.getLogger(__name__)

_TRACEPARENT_RE = re.compile(
    r"^\s*00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})\s*$",
    re.IGNORECASE,
)
_CLOUD_TRACE_RE = re.compile(
    r"^\s*([0-9a-f]{32})(?:/(\d+))?(?:;o=(\d+))?\s*$",
    re.IGNORECASE,
)


def parse_incoming_span_context(headers: Mapping[str, str]) -> SpanContext | None:
    """Extract parent ``SpanContext`` from ``traceparent`` or ``X-Cloud-Trace-Context``."""
    lower_headers = {k.lower(): v for k, v in headers.items()}
    tp = lower_headers.get("traceparent")
    if tp:
        m = _TRACEPARENT_RE.match(tp)
        if m:
            trace_id_hex, span_id_hex, flags_hex = m.groups()
            trace_id = int(trace_id_hex, 16)
            span_id = int(span_id_hex, 16)
            flags = TraceFlags(int(flags_hex, 16) & 0x01 or 0x01)
            if trace_id != 0 and span_id != 0:
                return SpanContext(
                    trace_id=trace_id,
                    span_id=span_id,
                    is_remote=True,
                    trace_flags=flags,
                )

    ctc = lower_headers.get("x-cloud-trace-context")
    if ctc:
        m = _CLOUD_TRACE_RE.match(ctc)
        if m:
            trace_id_hex, span_dec, _sampled = m.groups()
            trace_id = int(trace_id_hex, 16)
            span_id = int(span_dec) & ((1 << 64) - 1) if span_dec else 1
            if trace_id != 0 and span_id != 0:
                return SpanContext(
                    trace_id=trace_id,
                    span_id=span_id,
                    is_remote=True,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
    return None


def _is_trusted_inbound_caller(headers: Mapping[str, str]) -> bool:
    lower = {k.lower(): v for k, v in headers.items() if v}
    if lower.get("x-bridge-internal-hop") == "1":
        return True
    svid = lower.get("x-bridge-spiffe-svid", "")
    return bool(svid)


def _build_parent_trace_context(
    headers: Mapping[str, str] | None,
    *,
    trusted_caller: bool | None = None,
) -> context.Context | None:
    """Build an OpenTelemetry ``Context`` populated with remote ``SpanContext`` and sanitized W3C baggage."""
    if not headers:
        return None
    is_trusted = (
        _is_trusted_inbound_caller(headers)
        if trusted_caller is None
        else trusted_caller
    )
    clean_headers = sanitize_inbound_headers(headers, trusted_caller=is_trusted)
    span_ctx = parse_incoming_span_context(clean_headers)
    ctx = context.get_current()
    # Clear any stale outcome.* baggage from previous requests on the same worker thread
    entries = dict(baggage.get_all(context=ctx))
    ctx = baggage.clear(context=ctx)
    for k, v in entries.items():
        if not k.startswith("outcome."):
            ctx = baggage.set_baggage(k, v, context=ctx)

    lower_clean = {k.lower(): v for k, v in clean_headers.items()}
    raw_baggage = lower_clean.get("baggage", "")
    if raw_baggage:
        for item in raw_baggage.split(","):
            part = item.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            k_clean = k.strip()
            v_clean = v.split(";", 1)[0].strip()
            if k_clean and v_clean:
                ctx = baggage.set_baggage(k_clean, v_clean, context=ctx)

    if span_ctx is not None:
        return trace.set_span_in_context(NonRecordingSpan(span_ctx), ctx)
    had_inbound_baggage = any(
        k.lower() in ("baggage", OUTCOME_ID_HEADER) for k in headers
    )
    if raw_baggage or had_inbound_baggage:
        return ctx
    return None


@contextmanager
def parent_trace_context_from_headers(
    headers: Mapping[str, str] | None,
    *,
    trusted_caller: bool | None = None,
) -> Iterator[SpanContext | None]:
    """Attach remote ``SpanContext`` and sanitized W3C baggage extracted from ``headers``."""
    ctx = _build_parent_trace_context(headers, trusted_caller=trusted_caller)
    token = context.attach(ctx) if ctx is not None else None
    try:
        yield parse_incoming_span_context(headers or {})
    finally:
        if token is not None:
            context.detach(token)


def format_outgoing_trace_headers(
    *,
    is_external: bool = False,
) -> dict[str, str]:
    """Return ``traceparent``, ``X-Cloud-Trace-Context``, and W3C ``baggage`` headers for outgoing RPCs.

    When ``is_external=True``, ``outcome.*`` baggage and ``x-outcome-id`` are stripped so
    internal outcome keys never leak to external counterparties.
    """
    headers = inject_outcome_headers({}, is_external=is_external)
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx or not ctx.is_valid:
        return headers
    trace_id_hex = f"{ctx.trace_id:032x}"
    span_id_hex = f"{ctx.span_id:016x}"
    span_id_dec = str(ctx.span_id)
    headers["traceparent"] = f"00-{trace_id_hex}-{span_id_hex}-01"
    headers["X-Cloud-Trace-Context"] = f"{trace_id_hex}/{span_id_dec};o=1"
    if not is_external:
        out_id = get_current_outcome_id()
        if out_id:
            headers[OUTCOME_ID_HEADER] = out_id
    return headers


def current_trace_context() -> dict[str, str]:
    """Alias for ``format_outgoing_trace_headers``."""
    return format_outgoing_trace_headers()


def inject_trace_headers(
    headers: dict[str, str] | None = None,
    *,
    is_external: bool = False,
) -> dict[str, str]:
    """Inject outgoing trace and baggage headers into ``headers``."""
    merged = dict(headers or {})
    merged.update(format_outgoing_trace_headers(is_external=is_external))
    return merged


def _sync_canonical_span_attributes(span: trace.Span) -> None:
    """Mirror `docubridge.*` attributes onto top-level `_AllSpans` attributes (`source_mode`, `skill_id`, `skill_version`, `entity_id`, `finops.traffic_type`)."""
    if not span.is_recording():
        return
    raw_attrs = getattr(span, "attributes", None) or getattr(span, "_attributes", None)
    if not isinstance(raw_attrs, Mapping):
        return
    attrs = dict(raw_attrs)

    # 1. entity_id <-> docubridge.entity_id
    entity_val = attrs.get("entity_id") or attrs.get("docubridge.entity_id")
    if entity_val is not None and str(entity_val):
        s_val = str(entity_val)
        if "entity_id" not in attrs:
            span.set_attribute("entity_id", s_val)
        if "docubridge.entity_id" not in attrs:
            span.set_attribute("docubridge.entity_id", s_val)

    # 2. source_mode <-> docubridge.source_mode
    source_mode = attrs.get("source_mode") or attrs.get("docubridge.source_mode")
    if source_mode is not None and str(source_mode):
        s_val = str(source_mode)
        if "source_mode" not in attrs:
            span.set_attribute("source_mode", s_val)
        if "docubridge.source_mode" not in attrs:
            span.set_attribute("docubridge.source_mode", s_val)

    # 3. skill_id <-> docubridge.skill_id / docubridge.doctype
    skill_id = (
        attrs.get("skill_id")
        or attrs.get("docubridge.skill_id")
        or attrs.get("docubridge.doctype")
    )
    if skill_id is not None and str(skill_id):
        s_val = str(skill_id)
        if "skill_id" not in attrs:
            span.set_attribute("skill_id", s_val)
        if "docubridge.skill_id" not in attrs:
            span.set_attribute("docubridge.skill_id", s_val)

    # 4. skill_version <-> docubridge.skill_version / docubridge.skill.version
    skill_ver = (
        attrs.get("skill_version")
        or attrs.get("docubridge.skill_version")
        or attrs.get("docubridge.skill.version")
    )
    if skill_ver is not None and str(skill_ver):
        s_val = str(skill_ver)
        if "skill_version" not in attrs:
            span.set_attribute("skill_version", s_val)
        if "docubridge.skill_version" not in attrs:
            span.set_attribute("docubridge.skill_version", s_val)

    # 5. finops.traffic_type <-> docubridge.finops.traffic_type
    traffic_type = attrs.get("finops.traffic_type") or attrs.get(
        "docubridge.finops.traffic_type"
    )
    is_model_span = any(
        k in attrs
        for k in (
            "gen_ai.request.model",
            "gen_ai.response.model",
            "docubridge.model_used",
        )
    )
    if not traffic_type and is_model_span:
        traffic_type = "production_inference"
    if traffic_type is not None and str(traffic_type):
        s_val = str(traffic_type)
        if "finops.traffic_type" not in attrs:
            span.set_attribute("finops.traffic_type", s_val)
        if "docubridge.finops.traffic_type" not in attrs:
            span.set_attribute("docubridge.finops.traffic_type", s_val)

    # 6. outcome.id & outcome.owner from active W3C baggage
    out_id = get_current_outcome_id()
    if out_id and OUTCOME_ID_KEY not in attrs:
        span.set_attribute(OUTCOME_ID_KEY, out_id)
    out_owner = get_current_outcome_owner() or os.environ.get(
        "BRIDGE_APPHUB_APPLICATION_ID"
    )
    if out_owner and OUTCOME_OWNER_KEY not in attrs and (out_id or OUTCOME_ID_KEY in attrs):
        span.set_attribute(OUTCOME_OWNER_KEY, out_owner)


@contextmanager
def trace_step(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    parent_headers: Mapping[str, str] | None = None,
) -> Iterator[trace.Span]:
    """Record an OpenTelemetry span for an architectural step or tool invocation."""
    tracer = get_tracer()
    ctx = (
        _build_parent_trace_context(parent_headers)
        if parent_headers is not None
        else None
    )
    with tracer.start_as_current_span(name, context=ctx, kind=kind) as span:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    span.set_attribute(str(k), _clean_attr_value(v))
        _sync_canonical_span_attributes(span)
        try:
            yield span
            _sync_canonical_span_attributes(span)
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            _sync_canonical_span_attributes(span)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


class OtelAsgiMiddleware:
    """ASGI middleware that extracts inbound ``traceparent`` / ``X-Cloud-Trace-Context`` and sanitizes untrusted ``outcome.*`` baggage.

    Starts a ``SERVER`` span for every HTTP request and echoes ``traceparent``,
    ``x-trace-id``, and ``x-outcome-id`` (when an outcome is active) in response headers.
    """

    def __init__(
        self,
        app: Any,
        service_name: str = "docubridge-http",
        *,
        service_component: str = "http",
    ) -> None:
        self.app = app
        self.service_name = service_name
        self.service_component = service_component
        ensure_telemetry_initialized()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        headers_dict = {
            k.decode("latin1").lower(): v.decode("latin1") for k, v in raw_headers
        }
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        span_name = f"docubridge.http.{self.service_component} {method} {path}"

        parent_ctx = _build_parent_trace_context(headers_dict)
        tracer = get_tracer()

        token = context.attach(parent_ctx) if parent_ctx is not None else None
        try:
            with tracer.start_as_current_span(
                span_name,
                kind=SpanKind.SERVER,
            ) as span:
                span.set_attribute("http.request.method", method)
                span.set_attribute("url.path", path)
                span.set_attribute("service.name", self.service_name)
                span.set_attribute(
                    "docubridge.service.component", self.service_component
                )
                if "x-bridge-principal" in headers_dict:
                    span.set_attribute(
                        "docubridge.caller.principal",
                        headers_dict["x-bridge-principal"],
                    )
                if "x-bridge-role" in headers_dict:
                    span.set_attribute(
                        "docubridge.caller.role", headers_dict["x-bridge-role"]
                    )
                span_ctx = span.get_span_context()
                trace_id_hex = (
                    f"{span_ctx.trace_id:032x}" if span_ctx.is_valid else "0" * 32
                )
                span_id_hex = (
                    f"{span_ctx.span_id:016x}" if span_ctx.is_valid else "0" * 16
                )

                async def _send_with_trace(message: dict[str, Any]) -> None:
                    if message.get("type") == "http.response.start":
                        status_code = int(message.get("status", 200))
                        span.set_attribute("http.response.status_code", status_code)
                        if status_code >= 500:
                            span.set_status(
                                Status(StatusCode.ERROR, f"HTTP {status_code}")
                            )
                        else:
                            span.set_status(Status(StatusCode.OK))
                        out_headers = list(message.get("headers") or [])
                        existing_names = {
                            k.decode("latin1").lower() for k, _ in out_headers
                        }
                        out_headers.append(
                            (
                                b"traceparent",
                                f"00-{trace_id_hex}-{span_id_hex}-01".encode("latin1"),
                            )
                        )
                        out_headers.append(
                            (b"x-trace-id", trace_id_hex.encode("latin1"))
                        )
                        if "x-docubridge-trace-id" not in existing_names:
                            out_headers.append(
                                (
                                    b"x-docubridge-trace-id",
                                    trace_id_hex.encode("latin1"),
                                )
                            )
                        raw_span_attrs = (
                            getattr(span, "attributes", None)
                            or getattr(span, "_attributes", None)
                            or {}
                        )
                        active_outcome_id = get_current_outcome_id() or (
                            str(raw_span_attrs.get(OUTCOME_ID_KEY))
                            if raw_span_attrs.get(OUTCOME_ID_KEY)
                            else None
                        )
                        if active_outcome_id:
                            span.set_attribute(OUTCOME_ID_KEY, active_outcome_id)
                            if OUTCOME_ID_HEADER not in existing_names:
                                out_headers.append(
                                    (
                                        OUTCOME_ID_HEADER.encode("latin1"),
                                        active_outcome_id.encode("latin1"),
                                    )
                                )
                        active_owner = get_current_outcome_owner() or (
                            str(raw_span_attrs.get(OUTCOME_OWNER_KEY))
                            if raw_span_attrs.get(OUTCOME_OWNER_KEY)
                            else None
                        )
                        if active_owner:
                            span.set_attribute(OUTCOME_OWNER_KEY, active_owner)
                        message = {**message, "headers": out_headers}
                    await send(message)

                await self.app(scope, receive, _send_with_trace)
        finally:
            if token is not None:
                context.detach(token)
