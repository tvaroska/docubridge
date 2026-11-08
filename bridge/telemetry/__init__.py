"""End-to-end OpenTelemetry (OTel) tracing & GCP Cloud Trace / Cloud Logging correlation.

Provides:
1. Automatic W3C ``traceparent`` (``00-<trace_id>-<span_id>-<flags>``) and Google Cloud
   ``X-Cloud-Trace-Context`` (``<trace_id>/<span_id_dec>;o=1``) parent context extraction
   and propagation across FastAPI / Starlette HTTP requests and Gemini platform Agent Engine
   RPC hops.
2. Application-level child spans (:func:`trace_step`) across all 6 Enterprise Data
   Architecture zones and all 4 pillars:
   - Intake, Edge PII Redaction & WORM Audit Vault (``docubridge.intake.*``, ``docubridge.vault.*``)
   - Consolidation Rules Engine, Decimal Math & Bi-Temporal Store (``docubridge.consolidation.*``)
   - Zone 6 Knowledge Catalog & 6-Hop Lineage (``docubridge.catalog.*``)
   - SPIFFE JWT-SVID & OBO Compound Identity (``docubridge.identity.*``)
   - Zone 5 A2UI v0.9 Action Inbox & RFC 6902 Patch Audit (``docubridge.a2ui.*``)
   - Agent Gym Distillation, Synthetic Stress Suite & Programmable Trust Dial (``docubridge.agent_gym.*``)
3. Dual export:
   - In-memory ring buffer (:class:`InMemoryTelemetryExporter`) exposed via
     :func:`get_recorded_spans` and ``GET /v1/telemetry/spans`` for deterministic
     test assertions and interactive console inspection.
   - Direct GCP Cloud Trace v2 REST export (:class:`CloudTraceV2SpanExporter`,
     ``POST https://cloudtrace.googleapis.com/v2/projects/{project}/traces:batchWrite``)
     plus structured GCP Cloud Logging JSON correlation (``logging.googleapis.com/trace``
     and ``logging.googleapis.com/spanId``) when running on GCP.
"""

from __future__ import annotations

from bridge.telemetry.exporters import (
    _CLOUD_TRACE_EXPORTER,
    _IN_MEMORY_EXPORTER,
    _INIT_LOCK,
    _MAX_BUFFER_SPANS,
    _PROVIDER_INITIALIZED,
    DEFAULT_OTLP_TRACES_ENDPOINT,
    TRACER_NAME,
    CloudTraceV2SpanExporter,
    InMemoryTelemetryExporter,
    RingBufferTelemetryExporter,
    _clean_attr_value,
    _ns_to_iso8601,
    _span_to_dict,
    clear_recorded_spans,
    configure_genai_telemetry_env,
    ensure_telemetry_initialized,
    get_recorded_spans,
    get_telemetry_buffer,
    get_telemetry_ring_buffer,
    get_tracer,
)
from bridge.telemetry.middleware import (
    _CLOUD_TRACE_RE,
    _TRACEPARENT_RE,
    OtelAsgiMiddleware,
    format_outgoing_trace_headers,
    parent_trace_context_from_headers,
    parse_incoming_span_context,
    trace_step,
)
from bridge.telemetry.outcome import (
    OUTCOME_CLOSED_SPAN_NAME,
    OUTCOME_ID_HEADER,
    OUTCOME_ID_KEY,
    OUTCOME_OWNER_KEY,
    OUTCOME_PARENT_ID_KEY,
    OUTCOME_STATUS_KEY,
    OUTCOME_SUBJECT_KEY,
    VALID_OUTCOME_STATUSES,
    BaggageSpanProcessor,
    OutcomeBaggageSpanProcessor,
    OutcomeStatus,
    close_outcome,
    configure_propagation,
    current_outcome_id,
    current_outcome_owner,
    extract_outcome_context,
    get_current_outcome_id,
    get_current_outcome_owner,
    inject_outcome_headers,
    resume_outcome,
    sanitize_inbound_headers,
    start_outcome,
    strip_outcome_baggage_headers,
    without_outcome_baggage,
)
from bridge.telemetry.sql_eval import (
    _build_sql_trace_evaluation,
    _percentile,
    compute_sql_trace_evaluation,
    compute_trace_analytics,
)

__all__ = [
    "DEFAULT_OTLP_TRACES_ENDPOINT",
    "OUTCOME_CLOSED_SPAN_NAME",
    "OUTCOME_ID_HEADER",
    "OUTCOME_ID_KEY",
    "OUTCOME_OWNER_KEY",
    "OUTCOME_PARENT_ID_KEY",
    "OUTCOME_STATUS_KEY",
    "OUTCOME_SUBJECT_KEY",
    "TRACER_NAME",
    "VALID_OUTCOME_STATUSES",
    "_CLOUD_TRACE_EXPORTER",
    "_CLOUD_TRACE_RE",
    "_INIT_LOCK",
    "_IN_MEMORY_EXPORTER",
    "_MAX_BUFFER_SPANS",
    "_PROVIDER_INITIALIZED",
    "_TRACEPARENT_RE",
    "BaggageSpanProcessor",
    "CloudTraceV2SpanExporter",
    "InMemoryTelemetryExporter",
    "OtelAsgiMiddleware",
    "OutcomeBaggageSpanProcessor",
    "OutcomeStatus",
    "RingBufferTelemetryExporter",
    "_build_sql_trace_evaluation",
    "_clean_attr_value",
    "_ns_to_iso8601",
    "_percentile",
    "_span_to_dict",
    "clear_recorded_spans",
    "close_outcome",
    "compute_sql_trace_evaluation",
    "compute_trace_analytics",
    "configure_genai_telemetry_env",
    "configure_propagation",
    "current_outcome_id",
    "current_outcome_owner",
    "ensure_telemetry_initialized",
    "extract_outcome_context",
    "format_outgoing_trace_headers",
    "get_current_outcome_id",
    "get_current_outcome_owner",
    "get_recorded_spans",
    "get_telemetry_buffer",
    "get_telemetry_ring_buffer",
    "get_tracer",
    "inject_outcome_headers",
    "parent_trace_context_from_headers",
    "parse_incoming_span_context",
    "resume_outcome",
    "sanitize_inbound_headers",
    "start_outcome",
    "strip_outcome_baggage_headers",
    "trace_step",
    "without_outcome_baggage",
]
