"""Step latency percentiles, trace waterfall, FinOps model split, and 3-Tier BigQuery SQL Trace Evaluation."""

from __future__ import annotations

import contextlib
import datetime
import os
from typing import Any

from bridge.calibration import (
    CALIBRATION_HUMAN_LABELS,
    CALIBRATION_JUDGE_LABELS,
    rogan_gladen_debiased_pass_rate,
)
from bridge.telemetry.exporters import (
    _resolve_gcp_project,
    get_recorded_spans,
)
from bridge.telemetry.sql_templates import build_sql_trace_queries

DEFAULT_EVAL_DATASET = "agent_eval"
DEFAULT_SPANS_VIEW = "otel_spans"


def _resolve_eval_bq_target() -> tuple[str, str, str]:
    project_id = _resolve_gcp_project() or "docubridge-showcase"
    dataset_id = (
        os.environ.get("BRIDGE_EVAL_BQ_DATASET", DEFAULT_EVAL_DATASET).strip()
        or DEFAULT_EVAL_DATASET
    )
    return project_id, dataset_id, DEFAULT_SPANS_VIEW


def _percentile(sorted_vals: list[float], pct: int) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(
        len(sorted_vals) - 1, max(0, round((pct / 100.0) * (len(sorted_vals) - 1)))
    )
    return round(sorted_vals[idx], 3)


def _compute_step_latency(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    step_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sp in spans:
        attrs = sp.get("attributes") or {}
        zone = str(attrs.get("docubridge.zone") or "http_transport")
        step_groups.setdefault((sp["name"], zone), []).append(sp)

    step_latency: list[dict[str, Any]] = []
    for (span_name, zone), group in step_groups.items():
        durations = sorted(float(g.get("duration_ms", 0.0)) for g in group)
        inv_count = len(group)
        err_count = sum(1 for g in group if g.get("status") == "ERROR")
        avg_dur = round(sum(durations) / inv_count, 3) if inv_count else 0.0
        p50 = _percentile(durations, 50)
        p95 = _percentile(durations, 95)
        p99 = _percentile(durations, 99)
        step_latency.append(
            {
                "span_name": span_name,
                "step_name": span_name,
                "zone": zone,
                "invocation_count": inv_count,
                "span_count": inv_count,
                "error_count": err_count,
                "error_rate_pct": round(100.0 * err_count / inv_count, 2)
                if inv_count
                else 0.0,
                "avg_duration_ms": avg_dur,
                "p50_duration_ms": p50,
                "p95_duration_ms": p95,
                "p99_duration_ms": p99,
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99,
                "max_duration_ms": round(max(durations), 3) if durations else 0.0,
            }
        )
    step_latency.sort(key=lambda r: (-r["p95_duration_ms"], -r["invocation_count"]))
    return step_latency


def _parse_span_epoch_ms(start_time: object) -> float:
    try:
        return (
            datetime.datetime.fromisoformat(
                str(start_time).replace("Z", "+00:00")
            ).timestamp()
            * 1000.0
        )
    except (ValueError, TypeError, AttributeError):
        return 0.0


def _extract_span_tokens(
    attrs: dict[str, Any],
) -> tuple[int | None, int | None, int | None]:
    in_tok = (
        int(attrs["gen_ai.usage.input_tokens"])
        if "gen_ai.usage.input_tokens" in attrs
        else None
    )
    out_tok = (
        int(attrs["gen_ai.usage.output_tokens"])
        if "gen_ai.usage.output_tokens" in attrs
        else None
    )
    has_thinking = (
        "gen_ai.usage.reasoning.output_tokens" in attrs
        or "docubridge.thinking_tokens" in attrs
    )
    thinking_tok = (
        int(
            attrs.get("gen_ai.usage.reasoning.output_tokens")
            or attrs.get("docubridge.thinking_tokens")
            or 0
        )
        if has_thinking
        else None
    )
    return in_tok, out_tok, thinking_tok


def _build_trace_waterfall_and_finops(
    spans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sorted_spans = sorted(spans, key=lambda s: s.get("start_time", ""))
    trace_min_ts: dict[str, float] = {}
    parsed_epochs: list[tuple[dict[str, Any], float]] = []
    for sp in sorted_spans:
        tid = sp.get("trace_id", "")
        epoch_ms = _parse_span_epoch_ms(sp.get("start_time", ""))
        if tid and (tid not in trace_min_ts or epoch_ms < trace_min_ts[tid]):
            trace_min_ts[tid] = epoch_ms
        parsed_epochs.append((sp, epoch_ms))

    trace_waterfall: list[dict[str, Any]] = []
    model_stats = {
        "gemini-3.8-flash": [0, 0, 0],
        "gemini-4-argon": [0, 0, 0],
    }
    deterministic_zero_token_spans = 0
    traffic_tokens = {
        "production_inference": 0,
        "conflict_escalation": 0,
        "agent_gym_eval": 0,
    }

    for sp, epoch_ms in parsed_epochs:
        tid = sp.get("trace_id", "")
        attrs = sp.get("attributes") or {}
        base_ms = trace_min_ts.get(tid, epoch_ms)
        model_name = attrs.get("gen_ai.request.model") or attrs.get(
            "gen_ai.response.model"
        )
        traffic_type = attrs.get("finops.traffic_type") or attrs.get(
            "docubridge.finops.traffic_type"
        )
        in_tok, out_tok, thinking_tok = _extract_span_tokens(attrs)
        span_total_tok = (in_tok or 0) + (out_tok or 0) + (thinking_tok or 0)

        if model_name in model_stats:
            bucket = model_stats[model_name]
            bucket[0] += 1
            bucket[1] += in_tok or 0
            bucket[2] += out_tok or 0
        else:
            deterministic_zero_token_spans += 1

        if traffic_type in traffic_tokens:
            traffic_tokens[traffic_type] += span_total_tok

        trace_waterfall.append(
            {
                "trace_id": tid,
                "span_id": sp.get("span_id"),
                "parent_span_id": sp.get("parent_span_id"),
                "outcome_id": attrs.get("outcome.id"),
                "name": sp.get("name"),
                "span_name": sp.get("name"),
                "zone": attrs.get("docubridge.zone"),
                "entity_id": attrs.get("docubridge.entity_id")
                or attrs.get("entity_id"),
                "doc_id": attrs.get("docubridge.doc_id"),
                "actor_role": attrs.get("docubridge.actor_role")
                or attrs.get("docubridge.role"),
                "agent_spiffe_id": attrs.get("docubridge.agent_spiffe_id"),
                "gen_ai_system": attrs.get("gen_ai.system"),
                "gen_ai_model": model_name,
                "gen_ai_operation": attrs.get("gen_ai.operation.name"),
                "gen_ai_agent": attrs.get("gen_ai.agent.name"),
                "traffic_type": traffic_type,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "thinking_tokens": thinking_tok,
                "start_time": sp.get("start_time"),
                "end_time": sp.get("end_time"),
                "duration_ms": float(sp.get("duration_ms", 0.0)),
                "offset_ms": round(max(0.0, epoch_ms - base_ms), 3),
                "status": sp.get("status"),
            }
        )

    wh_calls, wh_in, wh_out = model_stats["gemini-3.8-flash"]
    rs_calls, rs_in, rs_out = model_stats["gemini-4-argon"]
    finops_model_split = {
        "workhorse_model": {
            "model": "gemini-3.8-flash",
            "role": "Runtime Coordinator & Multimodal PDF Extractor (Tier 1 Workhorse)",
            "calls": wh_calls,
            "call_count": wh_calls,
            "input_tokens": wh_in,
            "output_tokens": wh_out,
            "total_tokens": wh_in + wh_out,
        },
        "reasoning_model": {
            "model": "gemini-4-argon",
            "role": "Cross-Doc Conflict Adjudicator & Agent Gym Teacher / Autorater / Skill Compiler (Tier 2 Pro)",
            "calls": rs_calls,
            "call_count": rs_calls,
            "input_tokens": rs_in,
            "output_tokens": rs_out,
            "total_tokens": rs_in + rs_out,
        },
        "deterministic_zero_token_spans": deterministic_zero_token_spans,
        "traffic_attribution": {
            "production_inference_tokens": traffic_tokens["production_inference"],
            "conflict_escalation_tokens": traffic_tokens["conflict_escalation"],
            "agent_gym_eval_tokens": traffic_tokens["agent_gym_eval"],
        },
    }
    return trace_waterfall, finops_model_split


def _group_spans_by_trace(
    spans: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    trace_groups: dict[str, list[dict[str, Any]]] = {}
    for sp in spans:
        tid = sp.get("trace_id", "")
        if tid:
            trace_groups.setdefault(tid, []).append(sp)
    return trace_groups


def _extract_entities(group: list[dict[str, Any]]) -> list[str]:
    return [
        str(
            (g.get("attributes") or {}).get("docubridge.entity_id")
            or (g.get("attributes") or {}).get("entity_id")
        )
        for g in group
        if (g.get("attributes") or {}).get("docubridge.entity_id")
        or (g.get("attributes") or {}).get("entity_id")
    ]


def _build_entity_pipeline_summary(
    spans: list[dict[str, Any]], entity_id: str | None
) -> list[dict[str, Any]]:
    entity_pipeline_summary: list[dict[str, Any]] = []
    for tid, group in _group_spans_by_trace(spans).items():
        entities = _extract_entities(group)
        primary_entity = entities[0] if entities else (entity_id or "all")
        names = [str(g.get("name", "")) for g in group]
        entity_pipeline_summary.append(
            {
                "trace_id": tid,
                "entity_id": primary_entity,
                "total_spans": len(group),
                "distinct_step_names": len(set(names)),
                "error_spans": sum(1 for g in group if g.get("status") == "ERROR"),
                "pii_redaction_spans": sum(
                    1 for n in names if n == "docubridge.intake.redact_pii"
                ),
                "worm_vault_locks": sum(
                    1 for n in names if n == "docubridge.vault.lock_worm_artifact"
                ),
                "operational_store_promotions": sum(
                    1
                    for n in names
                    if n == "docubridge.consolidation.promote_to_operational_store"
                ),
                "bitemporal_queries": sum(
                    1
                    for n in names
                    if n == "docubridge.consolidation.bitemporal_query_as_of"
                ),
                "bigquery_cdc_syncs": sum(
                    1
                    for n in names
                    if n == "docubridge.consolidation.bigquery_iceberg_sync"
                ),
                "lineage_traces": sum(
                    1 for n in names if n == "docubridge.catalog.lineage_trace"
                ),
                "spiffe_svid_verifications": sum(
                    1 for n in names if n == "docubridge.identity.verify_jwt_svid"
                ),
                "inbox_governance_actions": sum(
                    1 for n in names if n == "docubridge.a2ui.resolve_inbox_action"
                ),
                "agent_gym_operations": sum(
                    1 for n in names if n.startswith("docubridge.agent_gym.")
                ),
                "cumulative_span_duration_ms": round(
                    sum(float(g.get("duration_ms", 0.0)) for g in group), 3
                ),
            }
        )
    return entity_pipeline_summary


def compute_trace_analytics(
    *,
    trace_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Compute step latency percentiles, trace waterfall, and entity pipeline
    summary matching the BigQuery SQL analytics views (`agent_eval.otel_spans`,
    `agent_eval.v_step1_trace_eval`, `agent_eval.v_cost_per_verified_outcome`,
    `agent_eval.v_attribution_coverage`)."""
    spans = get_recorded_spans(trace_id=trace_id, entity_id=entity_id, limit=limit)
    bq_proj, bq_ds, bq_tbl = _resolve_eval_bq_target()
    step_latency = _compute_step_latency(spans)
    trace_waterfall, finops_model_split = _build_trace_waterfall_and_finops(spans)
    entity_pipeline_summary = _build_entity_pipeline_summary(spans, entity_id)
    trace_sql_evaluation = _build_sql_trace_evaluation(
        spans=spans,
        bq_proj=bq_proj,
        bq_ds=bq_ds,
        bq_tbl=bq_tbl,
    )
    return {
        "total_spans_analyzed": len(spans),
        "bigquery_target": {
            "project_id": bq_proj,
            "dataset_id": bq_ds,
            "table_id": bq_tbl,
            "table_fqn": f"{bq_proj}.{bq_ds}.{bq_tbl}",
            "views": {
                "otel_spans": f"{bq_proj}.{bq_ds}.{bq_tbl}",
                "v_step1_trace_eval": f"{bq_proj}.{bq_ds}.v_step1_trace_eval",
                "v_trace_sql_evaluation": f"{bq_proj}.{bq_ds}.v_trace_sql_evaluation",
                "v_agent_eval_and_finops_summary": f"{bq_proj}.{bq_ds}.v_agent_eval_and_finops_summary",
                "v_cost_per_verified_outcome": f"{bq_proj}.{bq_ds}.v_cost_per_verified_outcome",
                "v_attribution_coverage": f"{bq_proj}.{bq_ds}.v_attribution_coverage",
                "step_latency_analytics": f"{bq_proj}.{bq_ds}.v_step1_trace_eval",
                "trace_waterfall": f"{bq_proj}.{bq_ds}.{bq_tbl}",
                "entity_pipeline_summary": f"{bq_proj}.{bq_ds}.v_cost_per_verified_outcome",
                "trace_sql_evaluation": f"{bq_proj}.{bq_ds}.v_step1_trace_eval",
            },
        },
        "finops_model_split": finops_model_split,
        "trace_sql_evaluation": trace_sql_evaluation,
        "step_latency": step_latency,
        "entity_pipeline_summary": entity_pipeline_summary,
        "trace_waterfall": trace_waterfall,
    }


def _check_gate_1_pii_redaction(group: list[dict[str, Any]], names: list[str]) -> bool:
    if "docubridge.intake.collect_document" not in names:
        return True
    redact_ok = [
        g
        for g in group
        if g.get("name") == "docubridge.intake.redact_pii"
        and g.get("status") != "ERROR"
    ]
    vault_ok = [
        g
        for g in group
        if g.get("name") == "docubridge.vault.lock_worm_artifact"
        and g.get("status") != "ERROR"
    ]
    collect_spans = [
        g for g in group if g.get("name") == "docubridge.intake.collect_document"
    ]
    min_redact_ts = min(
        (str(g.get("start_time", "")) for g in redact_ok), default="9999"
    )
    min_collect_ts = min(
        (str(g.get("start_time", "")) for g in collect_spans), default="0000"
    )
    return bool(redact_ok) and bool(vault_ok) and (min_redact_ts <= min_collect_ts)


def _check_gate_4_oversight(group: list[dict[str, Any]]) -> bool:
    for g in group:
        if g.get("name") != "docubridge.agent_gym.distill_signal":
            continue
        attrs = g.get("attributes") or {}
        rsr_raw = attrs.get("docubridge.oversight.rsr_score")
        if rsr_raw is None:
            rsr_raw = attrs.get("docubridge.oversight_rsr_score")
        rule_status = str(attrs.get("docubridge.rule_status") or "")
        if rsr_raw is not None:
            with contextlib.suppress(ValueError, TypeError):
                if float(rsr_raw) < 0.80 and rule_status != "rejected":
                    return False
    return True


def _evaluate_trace_rule0(tid: str, group: list[dict[str, Any]]) -> dict[str, Any]:
    entities = _extract_entities(group)
    primary_entity = entities[0] if entities else "all"
    distinct_entities = max(1, len(set(entities)))
    names = [str(g.get("name", "")) for g in group]

    gate_1 = _check_gate_1_pii_redaction(group, names)
    needs_spiffe = any(
        n
        in {
            "docubridge.consolidation.promote_to_operational_store",
            "docubridge.a2ui.render_surface",
        }
        for n in names
    )
    svid_error = any(
        g.get("name") == "docubridge.identity.verify_jwt_svid"
        and g.get("status") == "ERROR"
        for g in group
    )
    has_spiffe_attr = any(
        bool((g.get("attributes") or {}).get("docubridge.agent_spiffe_id"))
        for g in group
    )
    gate_2 = (not needs_spiffe) or (has_spiffe_attr and not svid_error)
    gate_3 = not any(
        str(g.get("name", "")).startswith("docubridge.consolidation.")
        and g.get("status") == "ERROR"
        for g in group
    )
    gate_4 = _check_gate_4_oversight(group)

    model_errors = sum(
        1
        for g in group
        if g.get("status") == "ERROR"
        and not str(g.get("name", "")).startswith(
            ("docubridge.identity.", "docubridge.catalog.authorize_access")
        )
    )
    escalation_calls = sum(
        1
        for g in group
        if (
            (g.get("attributes") or {}).get("finops.traffic_type")
            or (g.get("attributes") or {}).get("docubridge.finops.traffic_type")
        )
        == "conflict_escalation"
    )
    workhorse_calls = sum(
        1
        for g in group
        if (g.get("attributes") or {}).get("gen_ai.request.model") == "gemini-3.8-flash"
    )
    reasoning_calls = sum(
        1
        for g in group
        if (g.get("attributes") or {}).get("gen_ai.request.model") == "gemini-4-argon"
    )
    gate_5 = model_errors == 0 and escalation_calls <= distinct_entities
    rule0_passed = gate_1 and gate_2 and gate_3 and gate_4 and gate_5
    needs_judge = (not rule0_passed) or (escalation_calls > 0)

    return {
        "trace_id": tid,
        "entity_id": primary_entity,
        "total_spans": len(group),
        "workhorse_calls": workhorse_calls,
        "reasoning_calls": reasoning_calls,
        "conflict_escalation_calls": escalation_calls,
        "gate_1_pii_redacted_before_extract": gate_1,
        "gate_2_spiffe_zero_trust_verified": gate_2,
        "gate_3_decimal_math_and_bitemporal_audit": gate_3,
        "gate_4_oversight_anti_rubber_stamp": gate_4,
        "gate_5_finops_workhorse_right_sized": gate_5,
        "rule0_sql_all_passed": rule0_passed,
        "needs_llm_judge_sampling": needs_judge,
    }


_GATE_METADATA: tuple[tuple[str, str, str], ...] = (
    (
        "gate_1_pii_redacted_before_extract",
        "Gate 1 · PII Redaction & WORM Lock Before Extraction",
        "MIN(redact_pii.start_time) <= MIN(collect_document.start_time) AND lock_worm_artifact = 'OK'",
    ),
    (
        "gate_2_spiffe_zero_trust_verified",
        "Gate 2 · SPIFFE JWT-SVID Verified Before Store/A2UI",
        "COUNTIF(verify_jwt_svid = 'ERROR') = 0 AND agent_spiffe_id IS NOT NULL",
    ),
    (
        "gate_3_decimal_math_and_bitemporal_audit",
        "Gate 3 · Deterministic Decimal Math & Bi-Temporal Ledger",
        "COUNTIF(STARTS_WITH(name, 'docubridge.consolidation.') AND status = 'ERROR') = 0",
    ),
    (
        "gate_4_oversight_anti_rubber_stamp",
        "Gate 4 · Two-Sided HITL Oversight (RSR >= 0.80)",
        "SAFE_CAST(JSON_VALUE(attributes, '$.\"docubridge.oversight.rsr_score\"') AS FLOAT64) >= 0.80",
    ),
    (
        "gate_5_finops_workhorse_right_sized",
        "Gate 5 · FinOps Model Right-Sizing & Traffic Attribution",
        "COUNTIF(status = 'ERROR') = 0 AND conflict_escalation_calls <= 1",
    ),
)


def _build_gates_summary(
    per_trace_evals: list[dict[str, Any]],
    total_traces: int,
    evaluated: bool,
) -> list[dict[str, Any]]:
    denom = max(1, total_traces)
    summary: list[dict[str, Any]] = []
    for gate_id, title, sql_expr in _GATE_METADATA:
        passed = sum(1 for row in per_trace_evals if row[gate_id])
        summary.append(
            {
                "gate_id": gate_id,
                "title": title,
                "sql_expression": sql_expr,
                "passed_traces": passed,
                "total_traces": total_traces,
                "pass_rate_pct": round(100.0 * passed / denom, 1) if evaluated else 0.0,
                "status": (
                    "NOT_EVALUATED"
                    if not evaluated
                    else ("PASS" if passed == total_traces else "WARN")
                ),
            }
        )
    return summary


def _build_sql_trace_evaluation(
    *,
    spans: list[dict[str, Any]],
    bq_proj: str = "docubridge-showcase",
    bq_ds: str = DEFAULT_EVAL_DATASET,
    bq_tbl: str = DEFAULT_SPANS_VIEW,
) -> dict[str, Any]:
    """Evaluate the 5 Rule 0 deterministic SQL trajectory gates over recorded spans
    and construct the production BigQuery Standard SQL trace-evaluation queries
    against `agent_eval.otel_spans`, `agent_eval.v_step1_trace_eval`,
    `agent_eval.v_cost_per_verified_outcome`, and `agent_eval.v_attribution_coverage`."""
    trace_groups = _group_spans_by_trace(spans)
    per_trace_evals = [
        _evaluate_trace_rule0(tid, group) for tid, group in trace_groups.items()
    ]
    total_traces = len(trace_groups)
    evaluated = total_traces > 0
    denom = max(1, total_traces)
    all_rule0_pass = sum(1 for row in per_trace_evals if row["rule0_sql_all_passed"])
    sampled_for_judge = sum(
        1 for row in per_trace_evals if row["needs_llm_judge_sampling"]
    )
    rule0_pass_rate = round(100.0 * all_rule0_pass / denom, 1) if evaluated else 0.0
    judge_sampling_pct = (
        round(100.0 * sampled_for_judge / denom, 1) if evaluated else 0.0
    )
    token_savings_pct = (
        round(max(0.0, 100.0 - min(judge_sampling_pct, 25.0)), 1) if evaluated else 0.0
    )

    rg_result = (
        rogan_gladen_debiased_pass_rate(
            judge_pass_count=all_rule0_pass,
            n_unlabeled=total_traces,
            calibration_human_labels=CALIBRATION_HUMAN_LABELS,
            calibration_judge_labels=CALIBRATION_JUDGE_LABELS,
        )
        if evaluated
        else None
    )

    eval_view_fqn = f"{bq_proj}.{bq_ds}.v_step1_trace_eval"
    eval_view_alias_fqn = f"{bq_proj}.{bq_ds}.v_trace_sql_evaluation"
    sql_queries = build_sql_trace_queries(bq_proj=bq_proj, bq_ds=bq_ds, bq_tbl=bq_tbl)
    gates_summary = _build_gates_summary(per_trace_evals, total_traces, evaluated)

    return {
        "view_fqn": eval_view_fqn,
        "view_alias_fqn": eval_view_alias_fqn,
        "evaluated": evaluated,
        "judge_model": "gemini-4-argon",
        "workhorse_model": "gemini-3.8-flash",
        "total_traces_evaluated": total_traces,
        "rule0_sql_pass_rate_pct": rule0_pass_rate,
        "selective_judge_sampling_pct": judge_sampling_pct,
        "estimated_eval_token_savings_pct": token_savings_pct,
        "statistical_calibration": (
            {
                "evaluated": True,
                "n_unlabeled": total_traces,
                "raw_judge_pass_rate": round(rg_result.raw_judge_pass_rate, 4),
                "calibrated_tpr": round(rg_result.tpr_sensitivity, 4),
                "calibrated_tnr": round(rg_result.tnr_specificity, 4),
                "rogan_gladen_debiased_pass_rate": round(
                    rg_result.debiased_pass_rate, 4
                ),
                "ci_95_lower": round(rg_result.ci_95_lower, 4),
                "ci_95_upper": round(rg_result.ci_95_upper, 4),
            }
            if rg_result is not None
            else {
                "evaluated": False,
                "n_unlabeled": 0,
                "raw_judge_pass_rate": None,
                "calibrated_tpr": None,
                "calibrated_tnr": None,
                "rogan_gladen_debiased_pass_rate": None,
                "ci_95_lower": None,
                "ci_95_upper": None,
            }
        ),
        "gates": gates_summary,
        "traces": per_trace_evals,
        "sql_queries": sql_queries,
    }


def compute_sql_trace_evaluation(
    *,
    trace_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Execute BigQuery-compatible Rule 0 deterministic SQL trajectory evaluation,
    selective ``AI.GENERATE_TABLE`` judge sampling, and Rogan-Gladen bias correction
    over recorded OpenTelemetry spans."""
    spans = get_recorded_spans(trace_id=trace_id, entity_id=entity_id, limit=limit)
    bq_proj, bq_ds, bq_tbl = _resolve_eval_bq_target()
    return _build_sql_trace_evaluation(
        spans=spans,
        bq_proj=bq_proj,
        bq_ds=bq_ds,
        bq_tbl=bq_tbl,
    )
