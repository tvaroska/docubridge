"""OpenTelemetry span exporters (in-memory ring buffer + GCP Cloud Trace v2 / BigQuery)."""

from __future__ import annotations

import collections
import contextlib
import datetime
import json
import os
import sys
import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

TRACER_NAME = "docubridge.telemetry"
_MAX_BUFFER_SPANS = 1000


def _ns_to_iso8601(ns: int | None) -> str:
    if not ns:
        return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    dt = datetime.datetime.fromtimestamp(ns / 1e9, tz=datetime.UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _clean_attr_value(val: Any) -> str | int | float | bool:
    if isinstance(val, (bool, int, float, str)):
        return val
    return str(val)


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.get_span_context()
    trace_id_hex = f"{ctx.trace_id:032x}" if ctx and ctx.trace_id else ""
    span_id_hex = f"{ctx.span_id:016x}" if ctx and ctx.span_id else ""
    parent_span_id_hex = (
        f"{span.parent.span_id:016x}"
        if span.parent is not None and span.parent.span_id
        else None
    )
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    duration_ms = round(max(0, end_ns - start_ns) / 1_000_000.0, 3)
    attrs = {str(k): _clean_attr_value(v) for k, v in (span.attributes or {}).items()}
    status_code = (
        span.status.status_code.name
        if span.status and span.status.status_code
        else "UNSET"
    )
    return {
        "trace_id": trace_id_hex,
        "span_id": span_id_hex,
        "parent_span_id": parent_span_id_hex,
        "name": span.name,
        "kind": span.kind.name if span.kind else "INTERNAL",
        "start_time": _ns_to_iso8601(start_ns),
        "end_time": _ns_to_iso8601(end_ns),
        "duration_ms": duration_ms,
        "status": status_code,
        "status_message": span.status.description if span.status else None,
        "attributes": attrs,
    }


class RingBufferTelemetryExporter(SpanExporter):
    """Thread-safe ring buffer of finished OpenTelemetry spans."""

    def __init__(self, maxlen: int = _MAX_BUFFER_SPANS) -> None:
        self._lock = threading.Lock()
        self._spans: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=maxlen
        )

    def export(
        self, spans: tuple[ReadableSpan, ...] | list[ReadableSpan]
    ) -> SpanExportResult:
        """Append exported OpenTelemetry spans into the bounded in-memory ring buffer."""
        with self._lock:
            for sp in spans:
                self._spans.append(_span_to_dict(sp))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Shut down the in-memory ring buffer span exporter."""
        return None

    def clear(self) -> None:
        """Remove all recorded spans from the in-memory ring buffer."""
        with self._lock:
            self._spans.clear()

    def list_spans(
        self,
        *,
        trace_id: str | None = None,
        name_prefix: str | None = None,
        entity_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent spans from the ring buffer, optionally filtered by trace ID."""
        with self._lock:
            items = list(self._spans)
        if trace_id:
            tid = trace_id.strip().lower()
            items = [s for s in items if s.get("trace_id") == tid]
        if name_prefix:
            items = [s for s in items if s.get("name", "").startswith(name_prefix)]
        if entity_id:
            items = [
                s
                for s in items
                if s.get("attributes", {}).get("docubridge.entity_id") == entity_id
            ]
        return items[-limit:]


class CloudTraceV2SpanExporter(SpanExporter):
    """Exports OpenTelemetry spans to GCP Cloud Trace v2 (`traces:batchWrite`) and emits
    structured trace-correlated JSON log entries to stdout on GCP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Any = None
        self._auth_failed = False

    def _project_id(self) -> str:
        return (
            os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
            or os.environ.get("GCP_PROJECT", "").strip()
        )

    def _is_gcp_enabled(self) -> bool:
        if os.environ.get("BRIDGE_DISABLE_CLOUD_TRACE", "").strip().lower() in {
            "1",
            "true",
        }:
            return False
        port_mode = os.environ.get("BRIDGE_PORT_MODE", "").strip().lower()
        return bool(self._project_id()) and (
            port_mode == "gcp"
            or bool(os.environ.get("K_SERVICE"))
            or bool(os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"))
        )

    def _get_session(self) -> Any:
        if self._auth_failed:
            return None
        with self._lock:
            if self._session is not None:
                return self._session
            try:
                import google.auth
                from google.auth.transport.requests import AuthorizedSession

                creds, _ = google.auth.default(
                    scopes=[
                        "https://www.googleapis.com/auth/trace.append",
                        "https://www.googleapis.com/auth/cloud-platform",
                    ]
                )
                self._session = AuthorizedSession(creds)
                return self._session
            except Exception:
                self._auth_failed = True
                return None

    @staticmethod
    def _to_cloud_trace_span(
        project_id: str, span_dict: dict[str, Any]
    ) -> dict[str, Any]:
        trace_id = span_dict["trace_id"]
        span_id = span_dict["span_id"]
        span_name_res = f"projects/{project_id}/traces/{trace_id}/spans/{span_id}"

        attr_map: dict[str, Any] = {}
        for k, v in span_dict.get("attributes", {}).items():
            if isinstance(v, bool):
                attr_map[k] = {"boolValue": v}
            elif isinstance(v, int):
                attr_map[k] = {"intValue": str(v)}
            else:
                s_val = str(v)[:256]
                attr_map[k] = {"stringValue": {"value": s_val, "truncatedByteCount": 0}}

        svc_name = os.environ.get("K_SERVICE", "").strip() or (
            "vertex-agent-engine"
            if os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
            else "docubridge"
        )
        attr_map.setdefault(
            "service.name",
            {"stringValue": {"value": svc_name, "truncatedByteCount": 0}},
        )

        payload: dict[str, Any] = {
            "name": span_name_res,
            "spanId": span_id,
            "displayName": {
                "value": span_dict["name"][:128],
                "truncatedByteCount": 0,
            },
            "startTime": span_dict["start_time"],
            "endTime": span_dict["end_time"],
            "attributes": {"attributeMap": attr_map, "droppedAttributesCount": 0},
            "spanKind": span_dict.get("kind", "INTERNAL"),
        }
        if span_dict.get("parent_span_id"):
            payload["parentSpanId"] = span_dict["parent_span_id"]
        if span_dict.get("status") == "ERROR":
            payload["status"] = {
                "code": 2,
                "message": (span_dict.get("status_message") or "ERROR")[:256],
            }
        else:
            payload["status"] = {"code": 0}
        return payload

    @staticmethod
    def _to_bigquery_row(span_dict: dict[str, Any]) -> dict[str, Any]:
        attrs = span_dict.get("attributes") or {}
        svc_name = os.environ.get("K_SERVICE", "").strip() or (
            "vertex-agent-engine"
            if os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
            else "docubridge"
        )
        return {
            "insertId": f"{span_dict.get('trace_id', '')}:{span_dict.get('span_id', '')}",
            "json": {
                "trace_id": span_dict["trace_id"],
                "span_id": span_dict["span_id"],
                "parent_span_id": span_dict.get("parent_span_id"),
                "name": span_dict["name"],
                "kind": span_dict.get("kind", "INTERNAL"),
                "start_time": span_dict["start_time"],
                "end_time": span_dict["end_time"],
                "duration_ms": float(span_dict.get("duration_ms", 0.0)),
                "status": span_dict.get("status", "UNSET"),
                "status_message": span_dict.get("status_message"),
                "service_name": str(attrs.get("service.name") or svc_name),
                "zone": (
                    str(attrs["docubridge.zone"])
                    if "docubridge.zone" in attrs
                    else None
                ),
                "entity_id": (
                    str(attrs["docubridge.entity_id"])
                    if "docubridge.entity_id" in attrs
                    else None
                ),
                "doc_id": (
                    str(attrs["docubridge.doc_id"])
                    if "docubridge.doc_id" in attrs
                    else None
                ),
                "doctype": (
                    str(attrs["docubridge.doctype"])
                    if "docubridge.doctype" in attrs
                    else None
                ),
                "actor_role": (
                    str(
                        attrs.get("docubridge.actor_role")
                        or attrs.get("docubridge.role")
                    )
                    if ("docubridge.actor_role" in attrs or "docubridge.role" in attrs)
                    else None
                ),
                "actor_principal": (
                    str(attrs["docubridge.actor_principal"])
                    if "docubridge.actor_principal" in attrs
                    else None
                ),
                "agent_spiffe_id": (
                    str(attrs["docubridge.agent_spiffe_id"])
                    if "docubridge.agent_spiffe_id" in attrs
                    else None
                ),
                "attributes_json": json.dumps(attrs, sort_keys=True, default=str),
            },
        }

    @staticmethod
    def _resolve_bq_target(default_project: str) -> tuple[str, str, str] | None:
        fqn = os.environ.get("BRIDGE_TELEMETRY_BQ_TABLE", "").strip()
        if fqn:
            parts = [p.strip() for p in fqn.replace(":", ".").split(".") if p.strip()]
            if len(parts) == 3:
                return parts[0], parts[1], parts[2]
            if len(parts) == 2 and default_project:
                return default_project, parts[0], parts[1]
        if default_project:
            return default_project, "docubridge_telemetry", "otel_spans"
        return None

    def export(
        self, spans: tuple[ReadableSpan, ...] | list[ReadableSpan]
    ) -> SpanExportResult:
        """BatchWrite OpenTelemetry spans directly to the Google Cloud Trace v2 API."""
        if not self._is_gcp_enabled():
            return SpanExportResult.SUCCESS
        project_id = self._project_id()
        if not project_id:
            return SpanExportResult.SUCCESS

        span_dicts = [_span_to_dict(sp) for sp in spans]

        for sd in span_dicts:
            if not sd.get("trace_id"):
                continue
            log_entry = {
                "severity": "ERROR" if sd.get("status") == "ERROR" else "INFO",
                "message": f"[OTel Span] {sd['name']} ({sd['duration_ms']}ms)",
                "logging.googleapis.com/trace": f"projects/{project_id}/traces/{sd['trace_id']}",
                "logging.googleapis.com/spanId": sd["span_id"],
                "logging.googleapis.com/trace_sampled": True,
                "otel": {
                    "span_id": sd["span_id"],
                    "span_name": sd["name"],
                    "parent_span_id": sd.get("parent_span_id"),
                    "duration_ms": sd["duration_ms"],
                    "status": sd["status"],
                    "attributes": sd.get("attributes", {}),
                },
            }
            with contextlib.suppress(Exception):
                sys.stdout.write(json.dumps(log_entry, default=str) + "\n")
                sys.stdout.flush()

        session = self._get_session()
        if session is None:
            return SpanExportResult.SUCCESS

        valid_spans = [
            sd for sd in span_dicts if sd.get("trace_id") and sd.get("span_id")
        ]
        if not valid_spans:
            return SpanExportResult.SUCCESS

        ct_spans = [self._to_cloud_trace_span(project_id, sd) for sd in valid_spans]
        url = f"https://cloudtrace.googleapis.com/v2/projects/{project_id}/traces:batchWrite"
        with contextlib.suppress(Exception):
            session.post(url, json={"spans": ct_spans}, timeout=3.0)

        bq_target = self._resolve_bq_target(project_id)
        if bq_target is not None:
            bq_proj, bq_ds, bq_tbl = bq_target
            bq_rows = [self._to_bigquery_row(sd) for sd in valid_spans]
            bq_url = (
                f"https://bigquery.googleapis.com/bigquery/v2/projects/{bq_proj}"
                f"/datasets/{bq_ds}/tables/{bq_tbl}/insertAll"
            )
            with contextlib.suppress(Exception):
                session.post(
                    bq_url,
                    json={
                        "kind": "bigquery#tableDataInsertAllRequest",
                        "skipInvalidRows": True,
                        "ignoreUnknownValues": True,
                        "rows": bq_rows,
                    },
                    timeout=3.0,
                )

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Shut down the Cloud Trace v2 span exporter."""
        return None


InMemoryTelemetryExporter = RingBufferTelemetryExporter

_INIT_LOCK = threading.Lock()
_IN_MEMORY_EXPORTER = RingBufferTelemetryExporter()
_CLOUD_TRACE_EXPORTER = CloudTraceV2SpanExporter()
_PROVIDER_INITIALIZED = False


def ensure_telemetry_initialized() -> TracerProvider:
    """Initialize the OpenTelemetry ``TracerProvider`` idempotently."""
    global _PROVIDER_INITIALIZED
    with _INIT_LOCK:
        current = trace.get_tracer_provider()
        if _PROVIDER_INITIALIZED and isinstance(current, TracerProvider):
            return current

        resource = Resource.create(
            {
                "service.name": os.environ.get("K_SERVICE", "docubridge"),
                "service.namespace": "docubridge.io",
            }
        )
        if isinstance(current, TracerProvider):
            provider = current
        else:
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)

        provider.add_span_processor(SimpleSpanProcessor(_IN_MEMORY_EXPORTER))
        if os.environ.get("BRIDGE_CLOUD_TRACE_SYNC_EXPORT", "").strip().lower() in {
            "1",
            "true",
        }:
            provider.add_span_processor(SimpleSpanProcessor(_CLOUD_TRACE_EXPORTER))
        else:
            provider.add_span_processor(
                BatchSpanProcessor(
                    _CLOUD_TRACE_EXPORTER,
                    schedule_delay_millis=500,
                    max_export_batch_size=128,
                )
            )
        _PROVIDER_INITIALIZED = True
        return provider


def get_tracer() -> trace.Tracer:
    """Return the configured ``docubridge.telemetry`` OpenTelemetry tracer."""
    ensure_telemetry_initialized()
    return trace.get_tracer(TRACER_NAME)


def get_recorded_spans(
    *,
    trace_id: str | None = None,
    name_prefix: str | None = None,
    entity_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return recorded OpenTelemetry spans from the in-memory telemetry buffer."""
    ensure_telemetry_initialized()
    return _IN_MEMORY_EXPORTER.list_spans(
        trace_id=trace_id,
        name_prefix=name_prefix,
        entity_id=entity_id,
        limit=limit,
    )


def clear_recorded_spans() -> None:
    """Clear the in-memory telemetry buffer (useful for unit test isolation)."""
    ensure_telemetry_initialized()
    _IN_MEMORY_EXPORTER.clear()
