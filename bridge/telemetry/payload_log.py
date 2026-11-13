"""Structured trace-correlated payload logger for Cloud Logging (`obs_logs._AllLogs`).

Emits a single JSON line per evaluated turn to stdout when Cloud Observability OTLP is
enabled so `agent_eval.trace_payloads` has deterministic `user_prompt`,
`retrieved_context`, and `final_response` fields on both `Path-A` (0-token) and `Path-B`
traces.
"""

from __future__ import annotations

import json
import os
import sys

from opentelemetry import trace


def emit_trace_payload_log(
    *,
    user_prompt: str,
    retrieved_context: str,
    final_response: str,
    apphub_application_id: str | None = None,
) -> None:
    """Write a trace-correlated JSON payload record when running under Cloud Observability."""
    if not (
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        or os.environ.get("BRIDGE_PORT_MODE", "").strip() == "gcp"
    ):
        return
    span_ctx = trace.get_current_span().get_span_context()
    if not span_ctx or not span_ctx.is_valid:
        return
    trace_hex = f"{span_ctx.trace_id:032x}"
    span_hex = f"{span_ctx.span_id:016x}"
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    app_id = (
        apphub_application_id
        or os.environ.get("BRIDGE_APPHUB_APPLICATION_ID", "").strip()
        or "unregistered_app"
    )
    record: dict[str, object] = {
        "severity": "INFO",
        "message": "bq_eval.trace_payload",
        "trace_id": trace_hex,
        "apphub.application.id": app_id,
        "user_prompt": user_prompt[:16000],
        "retrieved_context": retrieved_context[:32000],
        "final_response": final_response[:16000],
    }
    if project_id:
        record["logging.googleapis.com/trace"] = (
            f"projects/{project_id}/traces/{trace_hex}"
        )
        record["logging.googleapis.com/spanId"] = span_hex
    sys.stdout.write(json.dumps(record, ensure_ascii=True) + "\n")
    sys.stdout.flush()
