"""BigQuery SQL schema parity tests between ``bridge.telemetry.sql_eval`` and canonical ``bq-eval`` v0.2.3 views.

Embeds the canonical ``bq-eval`` Step 1-3 BigQuery view/table column contracts directly so
``docubridge`` stays 100% standalone with zero cross-repo runtime imports.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
from typing import Any

import pytest

from bridge.telemetry.sql_eval import (
    _build_sql_trace_evaluation,
    compute_sql_trace_evaluation,
    compute_trace_analytics,
)


def _cols(names: str) -> frozenset[str]:
    return frozenset(names.split())


CANONICAL_BQ_EVAL_SCHEMAS: dict[str, frozenset[str]] = {
    "otel_spans": _cols(
        """
        trace_id span_id parent_span_id span_name start_time end_time receive_time
        latency_ms status_code otel_span_kind span_kind service_name
        gcp_apphub_application_id gcp_apphub_service_id gcp_apphub_workload_id
        agent_name model_name input_tokens cached_input_tokens output_tokens
        thinking_tokens tool_name tool_call_status rule0_pass finops_traffic_type
        outcome_id outcome_owner source_mode skill_id skill_version entity_id
        attributes
        """
    ),
    "v_step1_trace_eval": _cols(
        """
        trace_id apphub_application_id outcome_id outcome_owner source_mode
        skill_id skill_version entity_id business_unit cost_center trace_start_time
        trace_end_time trace_receive_time trace_latency_ms ingestion_lag_ms
        span_count tool_call_count tool_error_count step1_pass
        hit_long_context_cliff total_input_tokens total_cached_input_tokens
        total_uncached_input_tokens total_output_tokens total_thinking_tokens
        cache_hit_ratio weighted_burndown_tokens trace_cost_usd
        """
    ),
    "v_trace_sql_evaluation": _cols(
        """
        trace_id apphub_application_id outcome_id outcome_owner source_mode
        skill_id skill_version entity_id trace_start_time trace_end_time
        trace_receive_time trace_latency_ms ingestion_lag_ms span_count
        tool_call_count tool_error_count rule0_pass escalated_to_high_reasoning
        hit_long_context_cliff primary_traffic_type total_input_tokens
        total_cached_input_tokens total_uncached_input_tokens total_output_tokens
        total_thinking_tokens weighted_burndown_tokens production_inference_usd
        conflict_escalation_usd agent_gym_eval_usd span_autorater_judge_usd
        trace_span_cost_usd business_unit sla_latency_ms cost_center
        cache_hit_ratio thinking_token_ratio
        """
    ),
    "v_step2_all_trace_rubric_prompts": _cols(
        """
        trace_id outcome_id apphub_application_id source_mode skill_id
        skill_version business_unit cost_center trace_start_time trace_latency_ms
        step1_pass tool_error_count trace_cost_usd rubric_id dimension
        passing_score judge_model prompt
        """
    ),
    "v_step2_trace_rubric_prompts": _cols(
        """
        trace_id outcome_id apphub_application_id source_mode skill_id
        skill_version business_unit cost_center trace_start_time trace_latency_ms
        step1_pass tool_error_count trace_cost_usd rubric_id dimension
        passing_score judge_model prompt
        """
    ),
    "v_agent_eval_and_finops_summary": _cols(
        """
        apphub_application_id rubric_id total_traces production_traces
        rule0_pass_rate tool_success_rate p95_latency_ms avg_cache_hit_ratio
        total_weighted_burndown_tokens judged_samples calibration_status gwet_ac2
        passes_irr_gate raw_judge_pass_rate calibrated_pass_rate
        calibrated_ci_95_lower calibrated_ci_95_upper production_inference_usd
        conflict_escalation_usd agent_gym_eval_usd autorater_judge_usd
        total_spend_usd cost_per_verified_outcome_usd
        """
    ),
    "v_cost_per_verified_outcome": _cols(
        """
        apphub_application_id source_mode agent_name total_outcomes
        verified_outcomes unverified_or_failed_outcomes total_traces
        total_input_tokens total_output_tokens total_thinking_tokens
        measured_model_cost_usd measured_judge_cost_usd estimated_infra_cost_usd
        total_ai_cost_usd total_cost_with_infra_usd
        cost_per_verified_outcome_ai_usd cost_per_verified_outcome_usd
        """
    ),
    "v_attribution_coverage": _cols(
        """
        apphub_application_id total_traces attributed_traces unattributed_traces
        attributed_spend_usd unattributed_spend_usd total_spend_usd
        attribution_coverage_pct passes_coverage_gate
        """
    ),
    "sme_labels": _cols(
        """
        label_id trace_id outcome_id apphub_application_id rubric_id rater_id
        rule0_pass human_ordinal_score human_pass judge_ordinal_score judge_pass
        labeled_at
        """
    ),
    "eval_results": _cols(
        """
        eval_id eval_run_id trace_id outcome_id apphub_application_id rubric_id
        judge_model sampling_stratum rule0_pass reasoning_trace ordinal_score
        binary_pass judge_input_tokens judge_output_tokens judge_thinking_tokens
        evaluated_at
        """
    ),
}

CANONICAL_BQ_EVAL_MODELS: frozenset[str] = frozenset(
    {"gemini_3_8_flash_judge", "gemini_4_argon_judge"}
)

LEGACY_FORBIDDEN_COLUMNS: tuple[str, ...] = (
    "total_spans",
    "workhorse_calls",
    "reasoning_calls",
    "gate_1_pii_redacted_before_extract",
    "gate_2_spiffe_zero_trust_verified",
    "gate_3_decimal_math_and_bitemporal_audit",
    "gate_4_oversight_anti_rubber_stamp",
    "gate_5_finops_workhorse_right_sized",
    "rule0_sql_all_passed",
    "needs_llm_judge_sampling",
    "trace_started_at",
    "coverage_gate_pass",
)

_SQL_KEYWORDS: frozenset[str] = _cols(
    """
    ABS AI AND AS ASC AVG BETWEEN BOOL BY CASE CAST COALESCE CONCAT COUNT COUNTIF
    CURRENT_TIMESTAMP DESC DISTINCT DIV ELSE END FALSE FARM_FINGERPRINT FLOAT64
    FROM GENERATE_TABLE GENERATE_UUID GREATEST GROUP HAVING IF IN INNER INT64 IS
    JOIN LEAST LEFT LENGTH LIMIT LOGICAL_AND LOGICAL_OR MAX MIN MOD MODEL NOT
    NULL NULLIF ON OR ORDER OUTER OVER PARTITION REPLACE RIGHT ROUND SAFE_CAST
    SAFE_DIVIDE SELECT STRING STRING_AGG STRUCT SUM TABLE THEN TRUE UNION USING
    WHEN WHERE WITH
    """
)


def _normalize_bq_ai_tvf_for_sqlglot(sql: str) -> str:
    """Normalize BigQuery ``AI.GENERATE_TABLE`` ``MODEL``/``TABLE`` keywords for sqlglot."""
    normalized = re.sub(
        r"(AI\.GENERATE_TABLE\s*\(\s*)MODEL\s+",
        r"\1",
        sql,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(AI\.GENERATE_TABLE\s*\([^;]*?,\s*)TABLE\s+",
        r"\1",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalized


def _find_unescaped_multiline_single_quoted_strings(sql: str) -> list[str]:
    """Return invalid GoogleSQL single-quoted string literals containing raw newlines."""
    violations: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        if sql[i : i + 2] == "--":
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif sql[i : i + 2] == "/*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif sql[i] == "'":
            j = i + 1
            has_newline = False
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "\n":
                    has_newline = True
                if sql[j] == "'":
                    j += 1
                    break
                j += 1
            if has_newline:
                violations.append(sql[i : min(j, i + 120)])
            i = j
        else:
            i += 1
    return violations


def _strip_comments_and_strings(sql: str) -> str:
    """Replace SQL comments and single-quoted string literals with whitespace."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        if sql[i : i + 2] == "--":
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
            out.append("\n")
        elif sql[i : i + 2] == "/*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        elif sql[i] == "'":
            i += 1
            while i < n:
                if sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            out.append("''")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _extract_output_schema_columns(sql: str) -> set[str]:
    """Extract column names declared in BigQuery ``'<col> <TYPE> ...' AS output_schema``."""
    match = re.search(
        r"'([^']+)'\s+AS\s+output_schema\b",
        sql,
        flags=re.IGNORECASE,
    )
    if not match:
        return set()
    cols: set[str] = set()
    for part in match.group(1).split(","):
        tokens = part.strip().split()
        if tokens:
            cols.add(tokens[0])
    return cols


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_select_output_names(select_body: str) -> set[str]:
    """Return projected output column names from a ``SELECT ... FROM`` clause."""
    match = re.search(
        r"\bSELECT\b(.*?)\bFROM\b",
        select_body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return set()
    names: set[str] = set()
    for item in _split_top_level_commas(match.group(1)):
        alias_m = re.search(r"\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*$", item, re.IGNORECASE)
        if alias_m:
            names.add(alias_m.group(1))
            continue
        col_m = re.search(r"(?:^|\.)([a-zA-Z_][a-zA-Z0-9_]*)\s*$", item)
        if col_m:
            names.add(col_m.group(1))
    return names


def _split_ctes_and_main_query(
    cleaned_sql: str,
) -> tuple[list[tuple[str, str]], str]:
    """Split a cleaned query into ``[(cte_name, cte_body), ...]`` and main ``SELECT``."""
    stripped = cleaned_sql.strip().rstrip(";")
    if not re.match(r"^WITH\b", stripped, flags=re.IGNORECASE):
        return [], stripped
    ctes: list[tuple[str, str]] = []
    pos = len("WITH")
    n = len(stripped)
    while pos < n:
        while pos < n and stripped[pos].isspace():
            pos += 1
        name_m = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(", stripped[pos:], re.I)
        if not name_m:
            break
        cte_name = name_m.group(1)
        body_start = pos + name_m.end()
        depth = 1
        idx = body_start
        while idx < n and depth > 0:
            if stripped[idx] == "(":
                depth += 1
            elif stripped[idx] == ")":
                depth -= 1
            idx += 1
        cte_body = stripped[body_start : idx - 1].strip()
        ctes.append((cte_name, cte_body))
        pos = idx
        while pos < n and stripped[pos].isspace():
            pos += 1
        if pos < n and stripped[pos] == ",":
            pos += 1
        else:
            break
    return ctes, stripped[pos:].strip()


def _validate_select_block_pure_python(
    block: str,
    *,
    cte_schemas: dict[str, set[str]],
    output_schema_cols: set[str],
    label: str,
) -> list[str]:
    """Validate every table/view and column referenced in one SELECT block."""
    errors: list[str] = []
    aliases: dict[str, set[str]] = {}
    in_scope_columns: set[str] = set()

    for m in re.finditer(
        r"\b(?:FROM|JOIN)\s+`([^`]+)`(?:\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?",
        block,
        flags=re.IGNORECASE,
    ):
        fqn = m.group(1)
        alias = m.group(2)
        view_name = fqn.split(".")[-1]
        if view_name not in CANONICAL_BQ_EVAL_SCHEMAS:
            errors.append(f"{label}: unknown view/table {view_name!r} ({fqn})")
            continue
        cols = set(CANONICAL_BQ_EVAL_SCHEMAS[view_name])
        in_scope_columns |= cols
        if alias and alias.upper() not in _SQL_KEYWORDS:
            aliases[alias] = cols

    for m in re.finditer(r"\bMODEL\s+`([^`]+)`", block, flags=re.IGNORECASE):
        model_name = m.group(1).split(".")[-1]
        if model_name not in CANONICAL_BQ_EVAL_MODELS:
            errors.append(f"{label}: unknown remote judge model {model_name!r}")

    for m in re.finditer(
        r"\b(?:FROM|JOIN|TABLE)\s+([a-zA-Z_][a-zA-Z0-9_]*)\b(?:\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?",
        block,
        flags=re.IGNORECASE,
    ):
        ref_name = m.group(1)
        alias = m.group(2)
        if ref_name in cte_schemas:
            cols = set(cte_schemas[ref_name])
            in_scope_columns |= cols
            if alias and alias.upper() not in _SQL_KEYWORDS:
                aliases[alias] = cols

    if re.search(r"\bAI\.GENERATE_TABLE\s*\(", block, flags=re.IGNORECASE):
        in_scope_columns |= output_schema_cols

    if not in_scope_columns:
        errors.append(f"{label}: could not resolve any source table/view schema")
        return errors

    # Strip backticked identifiers, AS aliases, and AI.GENERATE_TABLE STRUCT metadata before column scan.
    scan_text = re.sub(r"`[^`]+`", " ", block)
    scan_text = re.sub(
        r"\bSTRUCT\s*\([^)]*\)",
        " ",
        scan_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    defined_aliases = {
        m.group(1)
        for m in re.finditer(
            r"\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", scan_text, flags=re.IGNORECASE
        )
    }
    scan_text = re.sub(
        r"\bAS\s+[a-zA-Z_][a-zA-Z0-9_]*\b",
        " ",
        scan_text,
        flags=re.IGNORECASE,
    )

    for qm in re.finditer(
        r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b", scan_text
    ):
        qual, col = qm.group(1), qm.group(2)
        if qual.upper() == "AI" and col.upper() == "GENERATE_TABLE":
            continue
        if qual in aliases:
            if col not in aliases[qual]:
                errors.append(
                    f"{label}: column {qual}.{col} does not exist in canonical schema"
                )
        else:
            errors.append(f"{label}: unknown table alias {qual!r} on {qual}.{col}")

    scan_no_qual = re.sub(
        r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\b",
        " ",
        scan_text,
    )
    skip_idents = (
        set(aliases)
        | set(cte_schemas)
        | defined_aliases
        | {"output_schema", "model_params"}
    )
    for tok_m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b(?!\s*\()", scan_no_qual):
        ident = tok_m.group(1)
        if ident.upper() in _SQL_KEYWORDS or ident in skip_idents:
            continue
        if ident not in in_scope_columns:
            errors.append(
                f"{label}: unqualified column {ident!r} does not exist in canonical schema"
            )

    return errors


def _validate_query_schema_parity(sql: str, label: str) -> list[str]:
    """Validate BigQuery SQL syntax and column parity against ``CANONICAL_BQ_EVAL_SCHEMAS``."""
    errors: list[str] = []
    bad_strings = _find_unescaped_multiline_single_quoted_strings(sql)
    if bad_strings:
        errors.append(
            f"{label}: raw newline inside single-quoted GoogleSQL literal: {bad_strings}"
        )

    output_schema_cols = _extract_output_schema_columns(sql)
    cleaned = _strip_comments_and_strings(sql)
    ctes, main_block = _split_ctes_and_main_query(cleaned)
    cte_schemas: dict[str, set[str]] = {}
    for cte_name, cte_body in ctes:
        errors.extend(
            _validate_select_block_pure_python(
                cte_body,
                cte_schemas=cte_schemas,
                output_schema_cols=set(),
                label=f"{label}:{cte_name}",
            )
        )
        cte_schemas[cte_name] = _extract_select_output_names(cte_body)

    errors.extend(
        _validate_select_block_pure_python(
            main_block,
            cte_schemas=cte_schemas,
            output_schema_cols=output_schema_cols,
            label=label,
        )
    )
    return errors


def _validate_with_sqlglot_if_installed(sql: str, label: str) -> bool:
    """Parse with ``sqlglot`` BigQuery dialect and assert AST column parity if installed."""
    try:
        sqlglot: Any = importlib.import_module("sqlglot")
        exp: Any = importlib.import_module("sqlglot.expressions")
    except ImportError:
        return False

    normalized = _normalize_bq_ai_tvf_for_sqlglot(sql)
    stmts = [s for s in sqlglot.parse(normalized, read="bigquery") if s is not None]
    assert len(stmts) == 1, f"{label}: expected 1 BigQuery statement, got {len(stmts)}"
    stmt = stmts[0]
    assert not isinstance(stmt, exp.Command), (
        f"{label}: fell back to unparsed Command AST: {stmt.sql(dialect='bigquery')}"
    )

    output_schema_cols = _extract_output_schema_columns(sql)
    cte_columns: dict[str, set[str]] = {}

    def _check_select_scope(select_node: Any, scope_label: str) -> set[str]:
        aliases: dict[str, set[str]] = {}
        allowed_cols: set[str] = set()
        for tbl in select_node.find_all(exp.Table):
            if tbl.find_ancestor(exp.Select) is not select_node:
                continue
            tname = tbl.name
            if not tname:
                continue
            if tname in CANONICAL_BQ_EVAL_MODELS:
                continue
            if tname in cte_columns:
                cols = set(cte_columns[tname])
            else:
                assert tname in CANONICAL_BQ_EVAL_SCHEMAS, (
                    f"{scope_label}: unknown BigQuery view/table {tname!r}"
                )
                cols = set(CANONICAL_BQ_EVAL_SCHEMAS[tname])
            allowed_cols |= cols
            alias = tbl.alias
            if alias:
                aliases[alias] = cols

        if "AI.GENERATE_TABLE" in select_node.sql(dialect="bigquery").upper():
            allowed_cols |= output_schema_cols
            for cte_cols in cte_columns.values():
                allowed_cols |= cte_cols

        projection_aliases = {
            proj.alias
            for proj in select_node.expressions
            if isinstance(proj, exp.Alias)
        }
        for col in select_node.find_all(exp.Column):
            if col.find_ancestor(exp.Select) is not select_node:
                continue
            if col.find_ancestor(exp.Struct) is not None:
                continue
            col_name = col.name
            if (
                not col_name
                or col_name in cte_columns
                or col_name in projection_aliases
            ):
                continue
            qual = col.table
            if qual:
                assert qual in aliases, (
                    f"{scope_label}: unknown alias {qual!r} on {qual}.{col_name}"
                )
                assert col_name in aliases[qual], (
                    f"{scope_label}: column {qual}.{col_name} missing from canonical schema"
                )
            else:
                assert col_name in allowed_cols, (
                    f"{scope_label}: column {col_name!r} missing from canonical schema"
                )

        return {
            proj.alias_or_name for proj in select_node.expressions if proj.alias_or_name
        }

    for cte in stmt.find_all(exp.CTE):
        cte_columns[cte.alias_or_name] = _check_select_scope(
            cte.this, f"{label}:{cte.alias_or_name}"
        )

    _check_select_scope(stmt, label)
    return True


def test_all_sql_eval_queries_match_canonical_bq_eval_schemas() -> None:
    """Every query emitted by ``compute_sql_trace_evaluation()`` references only canonical columns."""
    result = compute_sql_trace_evaluation()
    queries = result["sql_queries"]
    expected_keys = {
        "rule0_trajectory_sql",
        "ai_generate_table_judge_sql",
        "rogan_gladen_sme_alignment_sql",
        "cost_per_verified_outcome_sql",
        "attribution_coverage_sql",
    }
    assert set(queries) == expected_keys

    for key, sql in queries.items():
        errors = _validate_query_schema_parity(sql, key)
        assert not errors, f"Schema parity errors in {key}:\n" + "\n".join(errors)
        _validate_with_sqlglot_if_installed(sql, key)


def test_rule0_trajectory_sql_uses_canonical_v_step1_trace_eval_columns() -> None:
    """``rule0_trajectory_sql`` queries canonical ``v_step1_trace_eval`` with zero legacy columns."""
    sql = _build_sql_trace_evaluation(spans=[])["sql_queries"]["rule0_trajectory_sql"]
    assert "FROM `docubridge-showcase.agent_eval.v_step1_trace_eval`" in sql
    assert "ORDER BY trace_start_time DESC" in sql
    for col in (
        "trace_id",
        "outcome_id",
        "apphub_application_id",
        "entity_id",
        "source_mode",
        "trace_start_time",
        "trace_latency_ms",
        "span_count",
        "tool_call_count",
        "tool_error_count",
        "step1_pass",
        "hit_long_context_cliff",
        "trace_cost_usd",
    ):
        assert col in sql, f"Expected canonical v_step1_trace_eval column {col!r}"
    for forbidden in LEGACY_FORBIDDEN_COLUMNS:
        assert forbidden not in sql, (
            f"Legacy nonexistent column {forbidden!r} leaked into SQL"
        )


def test_ai_generate_table_judge_sql_enforces_cot_schema_and_model_params() -> None:
    """``ai_generate_table_judge_sql`` enforces CoT ``output_schema``, JSON ``model_params``, and XML fencing."""
    sql = _build_sql_trace_evaluation(spans=[])["sql_queries"][
        "ai_generate_table_judge_sql"
    ]
    assert (
        "'reasoning_trace STRING NOT NULL, ordinal_score INT64 NOT NULL' AS output_schema"
        in sql
    )
    assert "AS temperature" not in sql
    assert "<untrusted_text>" in sql
    assert "</untrusted_text>" in sql

    params_match = re.search(r"'(\{[^']+\})'\s+AS\s+model_params", sql)
    assert params_match is not None, "Missing JSON string literal AS model_params"
    model_params = json.loads(params_match.group(1))
    assert model_params == {
        "generation_config": {
            "temperature": 0.0,
            "max_output_tokens": 2048,
            "thinking_config": {"thinking_level": "LOW"},
        }
    }
    for forbidden in LEGACY_FORBIDDEN_COLUMNS:
        assert forbidden not in sql


def test_rogan_gladen_sql_queries_calibrated_view_with_zero_hardcoded_constants() -> (
    None
):
    """``rogan_gladen_sme_alignment_sql`` queries ``v_agent_eval_and_finops_summary`` with zero hardcoded TPR/TNR."""
    eval_payload = _build_sql_trace_evaluation(spans=[])
    sql = eval_payload["sql_queries"]["rogan_gladen_sme_alignment_sql"]
    assert (
        "FROM `docubridge-showcase.agent_eval.v_agent_eval_and_finops_summary`" in sql
    )
    for forbidden in ("0.985", "0.960", "tpr_hat", "tnr_hat", "rule0_sql_all_passed"):
        assert forbidden not in sql, (
            f"Forbidden hardcoded/legacy token {forbidden!r} in SQL"
        )
    for col in (
        "calibrated_pass_rate",
        "calibrated_ci_95_lower",
        "calibrated_ci_95_upper",
        "gwet_ac2",
        "passes_irr_gate",
    ):
        assert col in sql
    assert eval_payload["statistical_calibration"]["calibrated_tpr"] is None
    assert eval_payload["statistical_calibration"]["calibrated_tnr"] is None


def test_cpvo_and_attribution_coverage_sql_align_with_step1_views() -> None:
    """``cost_per_verified_outcome_sql`` and ``attribution_coverage_sql`` match Step-1 view columns."""
    queries = _build_sql_trace_evaluation(spans=[])["sql_queries"]
    cpvo_sql = queries["cost_per_verified_outcome_sql"]
    assert (
        "FROM `docubridge-showcase.agent_eval.v_cost_per_verified_outcome`" in cpvo_sql
    )
    for col in (
        "apphub_application_id",
        "source_mode",
        "agent_name",
        "verified_outcomes",
        "total_outcomes",
        "cost_per_verified_outcome_usd",
    ):
        assert col in cpvo_sql

    attr_sql = queries["attribution_coverage_sql"]
    assert "FROM `docubridge-showcase.agent_eval.v_attribution_coverage`" in attr_sql
    assert "passes_coverage_gate" in attr_sql
    assert "coverage_gate_pass" not in attr_sql

    analytics = compute_trace_analytics()
    views = analytics["bigquery_target"]["views"]
    assert views["v_agent_eval_and_finops_summary"].endswith(
        ".v_agent_eval_and_finops_summary"
    )
    assert views["v_trace_sql_evaluation"].endswith(".v_trace_sql_evaluation")


def test_parity_validator_positive_controls_catch_nonexistent_columns() -> None:
    """Positive control: parity validator rejects legacy nonexistent columns and unknown views."""
    bad_step1 = (
        "SELECT trace_id, total_spans, rule0_sql_all_passed, needs_llm_judge_sampling\n"
        "FROM `demo.agent_eval.v_step1_trace_eval`\n"
        "ORDER BY trace_started_at DESC;"
    )
    step1_errors = _validate_query_schema_parity(bad_step1, "bad_step1")
    assert any("total_spans" in err for err in step1_errors)
    assert any("rule0_sql_all_passed" in err for err in step1_errors)
    assert any("trace_started_at" in err for err in step1_errors)

    bad_attr = (
        "SELECT apphub_application_id, coverage_gate_pass\n"
        "FROM `demo.agent_eval.v_attribution_coverage`;"
    )
    attr_errors = _validate_query_schema_parity(bad_attr, "bad_attr")
    assert any("coverage_gate_pass" in err for err in attr_errors)

    if importlib.util.find_spec("sqlglot") is not None:
        with pytest.raises(
            AssertionError,
            match=r"total_spans|rule0_sql_all_passed|trace_started_at",
        ):
            _validate_with_sqlglot_if_installed(bad_step1, "bad_step1")
        with pytest.raises(AssertionError, match="coverage_gate_pass"):
            _validate_with_sqlglot_if_installed(bad_attr, "bad_attr")
