"""OpenTelemetry span buffer, latency analytics, FinOps model split, and SQL Trace Evaluation routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from bridge.telemetry import (
    clear_recorded_spans,
    compute_sql_trace_evaluation,
    compute_trace_analytics,
    get_recorded_spans,
)


def register_telemetry_routes(router: APIRouter) -> None:
    """Register OpenTelemetry span inspection, trace analytics, and SQL evaluation routes."""

    @router.get("/v1/telemetry/spans")
    def list_telemetry_spans_endpoint(
        trace_id: str | None = None,
        name_prefix: str | None = None,
        entity_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List recent OpenTelemetry spans from the in-memory ring buffer."""
        spans = get_recorded_spans(
            trace_id=trace_id,
            name_prefix=name_prefix,
            entity_id=entity_id,
            limit=limit,
        )
        return {
            "count": len(spans),
            "spans": spans,
        }

    @router.post("/v1/telemetry/spans/clear")
    def clear_telemetry_spans_endpoint() -> dict[str, str]:
        """Clear the in-memory OpenTelemetry span ring buffer."""
        clear_recorded_spans()
        return {"status": "cleared"}

    @router.get("/v1/telemetry/analytics")
    def get_telemetry_analytics_endpoint(
        trace_id: str | None = None,
        entity_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Compute p50/p95/p99 step latency analytics, trace waterfall, and multi-model FinOps split."""
        return compute_trace_analytics(
            trace_id=trace_id,
            entity_id=entity_id,
            limit=limit,
        )

    @router.get("/v1/telemetry/sql-eval")
    @router.post("/v1/telemetry/sql-eval/run")
    def run_telemetry_sql_eval_endpoint(
        trace_id: str | None = None,
        entity_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Execute BigQuery-compatible Rule 0 SQL trajectory evaluation, selective AI.GENERATE_TABLE
        judge sampling, and Rogan-Gladen bias correction over recorded OpenTelemetry spans."""
        return compute_sql_trace_evaluation(
            trace_id=trace_id,
            entity_id=entity_id,
            limit=limit,
        )
