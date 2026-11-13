"""Telemetry span exporters and provider initialization (Native OTLP Telemetry API + In-Memory Ring Buffer)."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanKind, StatusCode

from bridge.telemetry.outcome import configure_propagation

logger = logging.getLogger(__name__)

TRACER_NAME = "docubridge.enterprise"
_MAX_BUFFER_SPANS = 2000
DEFAULT_OTLP_TRACES_ENDPOINT = "https://telemetry.googleapis.com/v1/traces"
DEFAULT_GENAI_PAYLOAD_BUCKET_URI = "gs://btv-scratch-01-genai-payloads/"


def _ns_to_iso8601(ns: int | None) -> str:
    if not ns:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    sec = ns / 1_000_000_000
    return datetime.fromtimestamp(sec, tz=UTC).isoformat().replace("+00:00", "Z")


def _clean_attr_value(val: Any) -> Any:
    if isinstance(val, (str, int, float, bool)) or val is None:
        return val
    if isinstance(val, (list, tuple)):
        return [_clean_attr_value(x) for x in val]
    return str(val)


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.get_span_context()
    trace_id = f"{ctx.trace_id:032x}" if ctx and ctx.trace_id else "0" * 32
    span_id = f"{ctx.span_id:016x}" if ctx and ctx.span_id else "0" * 16
    parent_span_id = (
        f"{span.parent.span_id:016x}" if span.parent and span.parent.span_id else None
    )
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    duration_ms = round(max(0.0, (end_ns - start_ns) / 1_000_000.0), 3)
    attrs = {str(k): _clean_attr_value(v) for k, v in (span.attributes or {}).items()}
    events = [
        {
            "name": ev.name,
            "timestamp": _ns_to_iso8601(ev.timestamp),
            "attributes": {
                str(k): _clean_attr_value(v) for k, v in (ev.attributes or {}).items()
            },
        }
        for ev in (span.events or ())
    ]
    status_code = (
        span.status.status_code.name
        if span.status and span.status.status_code
        else StatusCode.UNSET.name
    )
    return {
        "name": span.name,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "kind": span.kind.name if isinstance(span.kind, SpanKind) else str(span.kind),
        "start_time": _ns_to_iso8601(start_ns),
        "end_time": _ns_to_iso8601(end_ns),
        "duration_ms": duration_ms,
        "status": status_code,
        "status_description": span.status.description if span.status else None,
        "attributes": attrs,
        "events": events,
    }


def _parse_otel_resource_attributes(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw.split(","):
        part = item.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k.strip() and v.strip():
            parsed[k.strip()] = v.strip()
    return parsed


def _resolve_gcp_project() -> str | None:
    res_attrs = _parse_otel_resource_attributes(
        os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    )
    return (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GCLOUD_PROJECT")
        or res_attrs.get("gcp.project_id")
        or None
    )


def _resolve_otlp_traces_endpoint(explicit_endpoint: str | None = None) -> str:
    if explicit_endpoint and explicit_endpoint.strip():
        ep = explicit_endpoint.strip()
    elif os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip():
        ep = os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"].strip()
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        ep = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"].strip()
    else:
        ep = DEFAULT_OTLP_TRACES_ENDPOINT
    if ep.endswith("/v1/traces"):
        return ep
    return f"{ep.rstrip('/')}/v1/traces"


def configure_genai_telemetry_env(project_id: str | None = None) -> dict[str, str]:
    """Configure environment defaults for `opentelemetry-instrumentation-google-genai`."""
    resolved_project = project_id or _resolve_gcp_project() or "btv-scratch-01"
    default_upload_path = f"gs://{resolved_project}-genai-payloads/"
    defaults = {
        "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
        "OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK": "upload",
        "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH": default_upload_path,
    }
    applied: dict[str, str] = {}
    for key, val in defaults.items():
        applied[key] = os.environ.setdefault(key, val)
    try:
        from opentelemetry.instrumentation.google_genai import (  # type: ignore[import-not-found]
            GoogleGenAiSdkInstrumentor,
        )

        instrumentor = GoogleGenAiSdkInstrumentor()
        if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
            instrumentor.instrument()
    except Exception:
        # Optional instrumentation package; safe no-op when not installed.
        pass
    return applied


def build_gcp_resource() -> Resource:
    """Detect GCP and App Hub resource attributes for native Telemetry API ingestion."""
    env_res_attrs = _parse_otel_resource_attributes(
        os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    )
    service_name = (
        os.environ.get("OTEL_SERVICE_NAME")
        or env_res_attrs.get("service.name")
        or os.environ.get("K_SERVICE")
        or "docubridge-enterprise"
    )
    project_id = _resolve_gcp_project()
    apphub_app_id = (
        os.environ.get("BRIDGE_APPHUB_APPLICATION_ID")
        or env_res_attrs.get("apphub.application.id")
        or env_res_attrs.get("gcp.apphub.application.id")
    )
    attrs: dict[str, str] = {
        "service.name": service_name,
        "service.version": os.environ.get("K_REVISION", "1.14.0"),
        "deployment.environment": os.environ.get("BRIDGE_PORT_MODE", "local"),
    }
    for k, v in env_res_attrs.items():
        attrs.setdefault(k, v)
    if project_id:
        attrs["gcp.project_id"] = project_id
        attrs.setdefault("cloud.account.id", project_id)
    if apphub_app_id:
        attrs["apphub.application.id"] = apphub_app_id

    base_resource = Resource.create(attrs)
    is_gcp_runtime = bool(
        os.environ.get("K_SERVICE")
        or os.environ.get("BRIDGE_PORT_MODE", "").lower() == "gcp"
    )
    if is_gcp_runtime:
        try:
            from opentelemetry.resourcedetector.gcp_resource_detector import (
                GoogleCloudResourceDetector,
            )

            detected = GoogleCloudResourceDetector().detect()
            return detected.merge(base_resource)
        except Exception:
            pass
    return base_resource


class RingBufferTelemetryExporter(SpanExporter):
    """Thread-safe ring-buffer exporter for test assertions and UI inspection."""

    def __init__(self, maxlen: int = _MAX_BUFFER_SPANS) -> None:
        self._lock = threading.Lock()
        self._spans: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for span in spans:
                self._spans.append(_span_to_dict(span))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def list_spans(
        self,
        *,
        trace_id: str | None = None,
        entity_id: str | None = None,
        name_prefix: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._spans)
        if trace_id:
            items = [s for s in items if s["trace_id"] == trace_id]
        if entity_id:
            items = [
                s
                for s in items
                if s["attributes"].get("docubridge.entity_id") == entity_id
                or s["attributes"].get("entity_id") == entity_id
                or s["attributes"].get("outcome.subject") == entity_id
            ]
        if name_prefix:
            items = [s for s in items if s["name"].startswith(name_prefix)]
        if limit > 0:
            items = items[-limit:]
        return items

    get_spans = list_spans


class CloudTraceV2SpanExporter(SpanExporter):
    """Native OTLP HTTP SpanExporter targeting Google Cloud Telemetry API (`https://telemetry.googleapis.com/v1/traces`).

    Retains the `CloudTraceV2SpanExporter` class name as a backward-compatible alias
    while exporting strictly via standard OpenTelemetry OTLP/HTTP (`OTLPSpanExporter`)
    to `telemetry.googleapis.com` with Google Cloud ADC bearer authentication.
    """

    def __init__(
        self,
        project_id: str | None = None,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._explicit_project_id = project_id
        self._explicit_endpoint = endpoint
        self._lock = threading.Lock()
        self._credentials: Any = None
        self._auth_request: Any = None
        self._token_expiry: float = 0.0
        self._cached_token: str | None = None
        resolved_endpoint = _resolve_otlp_traces_endpoint(endpoint)
        self._endpoint = resolved_endpoint
        init_headers = dict(headers or {})
        self._otlp_exporter = OTLPSpanExporter(
            endpoint=resolved_endpoint,
            headers=init_headers,
            timeout=10,
        )

    @property
    def endpoint(self) -> str:
        return _resolve_otlp_traces_endpoint(self._explicit_endpoint)

    def _resolve_project(self) -> str | None:
        if self._explicit_project_id:
            return self._explicit_project_id
        return _resolve_gcp_project()

    def _is_enabled(self) -> bool:
        flag = os.environ.get("BRIDGE_CLOUD_TRACE_EXPORT", "").strip().lower()
        if flag in ("0", "false", "no", "off"):
            return False
        if flag in ("1", "true", "yes", "on"):
            return True
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        ):
            return True
        return bool(
            os.environ.get("K_SERVICE")
            or os.environ.get("BRIDGE_PORT_MODE", "").lower() == "gcp"
        )

    def _auth_header(self) -> dict[str, str] | None:
        now = time.time()
        with self._lock:
            if self._cached_token and now < self._token_expiry - 60:
                headers = {"Authorization": f"Bearer {self._cached_token}"}
                proj = self._resolve_project()
                if proj:
                    headers["x-goog-user-project"] = proj
                return headers
            try:
                import google.auth
                from google.auth.transport.requests import Request as GoogleAuthRequest

                if self._credentials is None:
                    self._credentials, detected_proj = google.auth.default(
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    if not self._explicit_project_id and detected_proj:
                        self._explicit_project_id = detected_proj
                    self._auth_request = GoogleAuthRequest()
                self._credentials.refresh(self._auth_request)
                token = getattr(self._credentials, "token", None)
                if not token:
                    return None
                self._cached_token = str(token)
                self._token_expiry = now + 3000
                headers = {"Authorization": f"Bearer {self._cached_token}"}
                proj = self._resolve_project()
                if proj:
                    headers["x-goog-user-project"] = proj
                return headers
            except Exception as exc:
                logger.debug("Telemetry API OTLP auth skipped: %s", exc)
                return None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans or not self._is_enabled():
            return SpanExportResult.SUCCESS
        auth_headers = self._auth_header()
        if auth_headers:
            session = getattr(self._otlp_exporter, "_session", None)
            if session is not None and hasattr(session, "headers"):
                session.headers.update(auth_headers)
            otlp_client = getattr(self._otlp_exporter, "_client", None)
            client_hdrs = getattr(otlp_client, "_headers", None)
            if isinstance(client_hdrs, dict):
                client_hdrs.update(auth_headers)
        try:
            return self._otlp_exporter.export(spans)
        except Exception as exc:
            logger.debug("OTLP span export non-fatal error: %s", exc)
            return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            self._otlp_exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return bool(self._otlp_exporter.force_flush(timeout_millis))
        except Exception:
            return True


InMemoryTelemetryExporter = RingBufferTelemetryExporter
GcpOtlpSpanExporter = CloudTraceV2SpanExporter
SpanRecord = dict[str, Any]

_IN_MEMORY_EXPORTER = RingBufferTelemetryExporter()
_CLOUD_TRACE_EXPORTER = CloudTraceV2SpanExporter()
_PROVIDER_INITIALIZED = False
_INIT_LOCK = threading.Lock()


def ensure_telemetry_initialized() -> TracerProvider:
    """Initialize the global OpenTelemetry TracerProvider, W3C baggage propagation, and GenAI env defaults."""
    global _PROVIDER_INITIALIZED
    with _INIT_LOCK:
        configure_genai_telemetry_env()
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider):
            if not _PROVIDER_INITIALIZED:
                current.add_span_processor(SimpleSpanProcessor(_IN_MEMORY_EXPORTER))
                current.add_span_processor(
                    BatchSpanProcessor(
                        _CLOUD_TRACE_EXPORTER,
                        schedule_delay_millis=250,
                        export_timeout_millis=10000,
                    )
                )
                configure_propagation(current)
                _PROVIDER_INITIALIZED = True
            else:
                configure_propagation(current)
            return current

        resource = build_gcp_resource()
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(_IN_MEMORY_EXPORTER))
        provider.add_span_processor(
            BatchSpanProcessor(
                _CLOUD_TRACE_EXPORTER,
                schedule_delay_millis=250,
                export_timeout_millis=10000,
            )
        )
        configure_propagation(provider)
        trace.set_tracer_provider(provider)
        _PROVIDER_INITIALIZED = True
        return provider


def force_flush_telemetry(timeout_millis: int = 4000) -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        with contextlib.suppress(Exception):
            provider.force_flush(timeout_millis)


def get_tracer() -> trace.Tracer:
    ensure_telemetry_initialized()
    return trace.get_tracer(TRACER_NAME)


def get_telemetry_buffer() -> RingBufferTelemetryExporter:
    ensure_telemetry_initialized()
    return _IN_MEMORY_EXPORTER


get_telemetry_ring_buffer = get_telemetry_buffer


def get_recorded_spans(
    *,
    trace_id: str | None = None,
    entity_id: str | None = None,
    name_prefix: str | None = None,
    limit: int = 250,
) -> list[dict[str, Any]]:
    ensure_telemetry_initialized()
    return _IN_MEMORY_EXPORTER.list_spans(
        trace_id=trace_id,
        entity_id=entity_id,
        name_prefix=name_prefix,
        limit=limit,
    )


def clear_recorded_spans() -> None:
    ensure_telemetry_initialized()
    _IN_MEMORY_EXPORTER.clear()
