# telemetry — BigQuery OpenTelemetry Trace Analytics dataset, partitioned span table,
# Cloud Logging structured-span sink, and SQL analytics views.
#
# Gated on enable_telemetry_bq (default false) so the credential-free root plan
# remains unchanged unless explicitly enabled.

resource "google_bigquery_dataset" "telemetry" {
  count = var.enable_telemetry_bq ? 1 : 0

  project                     = var.project_id
  dataset_id                  = var.dataset_id
  friendly_name               = "DocuBridge OpenTelemetry Trace Analytics"
  description                 = "Partitioned OpenTelemetry trace spans and SQL latency/waterfall views across all 6 Enterprise Data Architecture zones and Agent Gym."
  location                    = var.region
  delete_contents_on_destroy  = !var.deletion_protection
  labels                      = var.labels
}

resource "google_bigquery_dataset_iam_member" "runtime_data_editor" {
  count = var.enable_telemetry_bq ? 1 : 0

  project    = var.project_id
  dataset_id = google_bigquery_dataset.telemetry[0].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = var.service_account_member
}

resource "google_bigquery_table" "otel_spans" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.telemetry[0].dataset_id
  table_id            = "otel_spans"
  description         = "OpenTelemetry trace spans across Zones 1-6 and Agent Gym, partitioned by start_time and clustered by name, zone, entity_id, trace_id."
  deletion_protection = var.deletion_protection
  labels              = var.labels

  time_partitioning {
    type  = "DAY"
    field = "start_time"
  }

  clustering = ["name", "zone", "entity_id", "trace_id"]

  schema = jsonencode([
    { name = "trace_id", type = "STRING", mode = "REQUIRED", description = "32-hex W3C / Cloud Trace ID" },
    { name = "span_id", type = "STRING", mode = "REQUIRED", description = "16-hex OpenTelemetry Span ID" },
    { name = "parent_span_id", type = "STRING", mode = "NULLABLE", description = "16-hex Parent Span ID" },
    { name = "name", type = "STRING", mode = "REQUIRED", description = "Step or HTTP span name (e.g. docubridge.vault.lock_worm_artifact)" },
    { name = "kind", type = "STRING", mode = "REQUIRED", description = "OpenTelemetry SpanKind (INTERNAL, SERVER, CLIENT)" },
    { name = "start_time", type = "TIMESTAMP", mode = "REQUIRED", description = "Span start timestamp (UTC)" },
    { name = "end_time", type = "TIMESTAMP", mode = "REQUIRED", description = "Span end timestamp (UTC)" },
    { name = "duration_ms", type = "FLOAT64", mode = "REQUIRED", description = "Span duration in milliseconds" },
    { name = "status", type = "STRING", mode = "REQUIRED", description = "Span status code (OK, ERROR, UNSET)" },
    { name = "status_message", type = "STRING", mode = "NULLABLE", description = "Optional error/status description" },
    { name = "service_name", type = "STRING", mode = "NULLABLE", description = "Cloud Run service or Agent Engine component name" },
    { name = "zone", type = "STRING", mode = "NULLABLE", description = "Enterprise Data Architecture zone (docubridge.zone)" },
    { name = "entity_id", type = "STRING", mode = "NULLABLE", description = "Entity / Loan / Submission ID (docubridge.entity_id)" },
    { name = "doc_id", type = "STRING", mode = "NULLABLE", description = "Document ID (docubridge.doc_id)" },
    { name = "doctype", type = "STRING", mode = "NULLABLE", description = "Document type (docubridge.doctype)" },
    { name = "actor_role", type = "STRING", mode = "NULLABLE", description = "Resolved caller role (docubridge.role / docubridge.actor_role)" },
    { name = "actor_principal", type = "STRING", mode = "NULLABLE", description = "Authenticated human subject (docubridge.actor_principal)" },
    { name = "agent_spiffe_id", type = "STRING", mode = "NULLABLE", description = "Verified SPIFFE agent identity (docubridge.agent_spiffe_id)" },
    { name = "attributes_json", type = "STRING", mode = "NULLABLE", description = "Full span attributes serialized as JSON" }
  ])
}

resource "google_bigquery_table" "v_step_latency_analytics" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.telemetry[0].dataset_id
  table_id            = "v_step_latency_analytics"
  description         = "Step-level p50, p95, p99, avg, max latency and error rate across all DocuBridge pipeline steps."
  deletion_protection = false
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      WITH deduped AS (
        SELECT * EXCEPT(rn)
        FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY trace_id, span_id ORDER BY start_time DESC) AS rn
          FROM `${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.otel_spans[0].table_id}`
        )
        WHERE rn = 1
      )
      SELECT
        name AS span_name,
        COALESCE(zone, 'http_asgi') AS zone,
        COUNT(*) AS span_count,
        COUNTIF(status = 'ERROR') AS error_count,
        ROUND(SAFE_DIVIDE(COUNTIF(status = 'ERROR'), COUNT(*)) * 100.0, 2) AS error_rate_pct,
        ROUND(AVG(duration_ms), 3) AS avg_duration_ms,
        ROUND(APPROX_QUANTILES(duration_ms, 100)[OFFSET(50)], 3) AS p50_duration_ms,
        ROUND(APPROX_QUANTILES(duration_ms, 100)[OFFSET(95)], 3) AS p95_duration_ms,
        ROUND(APPROX_QUANTILES(duration_ms, 100)[OFFSET(99)], 3) AS p99_duration_ms,
        ROUND(MAX(duration_ms), 3) AS max_duration_ms
      FROM deduped
      GROUP BY span_name, zone
      ORDER BY span_name ASC
    SQL
  }

  depends_on = [google_bigquery_table.otel_spans]
}

resource "google_bigquery_table" "v_trace_waterfall" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.telemetry[0].dataset_id
  table_id            = "v_trace_waterfall"
  description         = "Chronological trace waterfall with relative offset_ms per trace_id."
  deletion_protection = false
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      WITH deduped AS (
        SELECT * EXCEPT(rn)
        FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY trace_id, span_id ORDER BY start_time DESC) AS rn
          FROM `${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.otel_spans[0].table_id}`
        )
        WHERE rn = 1
      )
      SELECT
        trace_id,
        span_id,
        parent_span_id,
        name AS span_name,
        COALESCE(zone, 'http_asgi') AS zone,
        service_name,
        entity_id,
        doc_id,
        doctype,
        actor_role,
        agent_spiffe_id,
        status,
        status_message,
        start_time,
        end_time,
        ROUND(TIMESTAMP_DIFF(start_time, MIN(start_time) OVER (PARTITION BY trace_id), MICROSECOND) / 1000.0, 3) AS offset_ms,
        duration_ms,
        attributes_json
      FROM deduped
    SQL
  }

  depends_on = [google_bigquery_table.otel_spans]
}

resource "google_bigquery_table" "v_entity_pipeline_summary" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.telemetry[0].dataset_id
  table_id            = "v_entity_pipeline_summary"
  description         = "End-to-end execution summary per trace_id and entity_id across Zones 1-6 and Agent Gym."
  deletion_protection = false
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      WITH deduped AS (
        SELECT * EXCEPT(rn)
        FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY trace_id, span_id ORDER BY start_time DESC) AS rn
          FROM `${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.otel_spans[0].table_id}`
        )
        WHERE rn = 1
      )
      SELECT
        trace_id,
         MAX(entity_id) AS entity_id,
        COUNT(*) AS total_spans,
        COUNT(DISTINCT name) AS distinct_step_names,
        COUNTIF(status = 'ERROR') AS error_spans,
        COUNTIF(name = 'docubridge.intake.redact_pii') AS pii_redaction_spans,
        COUNTIF(name = 'docubridge.vault.lock_worm_artifact') AS worm_vault_locks,
        COUNTIF(name = 'docubridge.consolidation.promote_to_operational_store') AS operational_store_promotions,
        COUNTIF(name = 'docubridge.consolidation.bitemporal_query_as_of') AS bitemporal_queries,
        COUNTIF(name = 'docubridge.consolidation.bigquery_iceberg_sync') AS bigquery_cdc_syncs,
        COUNTIF(name = 'docubridge.catalog.lineage_trace') AS lineage_traces,
        COUNTIF(name = 'docubridge.identity.verify_jwt_svid') AS spiffe_svid_verifications,
        COUNTIF(name = 'docubridge.a2ui.resolve_inbox_action') AS inbox_governance_actions,
        COUNTIF(STARTS_WITH(name, 'docubridge.agent_gym.')) AS agent_gym_operations,
        MIN(start_time) AS trace_started_at,
        MAX(end_time) AS trace_completed_at,
        ROUND(SUM(duration_ms), 3) AS cumulative_span_duration_ms
      FROM deduped
      GROUP BY trace_id
      ORDER BY trace_started_at DESC
    SQL
  }

  depends_on = [google_bigquery_table.otel_spans]
}

resource "google_bigquery_table" "v_trace_sql_evaluation" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.telemetry[0].dataset_id
  table_id            = "v_trace_sql_evaluation"
  description         = "Rule 0 deterministic SQL trajectory evaluation across all 5 governance/ordering gates and selective AI.GENERATE_TABLE judge sampling."
  deletion_protection = false
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      WITH deduped AS (
        SELECT * EXCEPT(rn)
        FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY trace_id, span_id ORDER BY start_time DESC) AS rn
          FROM `${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.otel_spans[0].table_id}`
        )
        WHERE rn = 1
      ),
      per_trace AS (
        SELECT
          trace_id,
          COALESCE(MAX(entity_id), 'all') AS entity_id,
          COUNT(*) AS total_spans,
          COUNTIF(status = 'ERROR') AS error_spans,
          COUNTIF(JSON_VALUE(attributes_json, '$."gen_ai.request.model"') = 'gemini-3.8-flash') AS workhorse_calls,
          COUNTIF(JSON_VALUE(attributes_json, '$."gen_ai.request.model"') = 'gemini-4-argon') AS reasoning_calls,
          COUNTIF(JSON_VALUE(attributes_json, '$."docubridge.finops.traffic_type"') = 'conflict_escalation') AS conflict_escalation_calls,
          SUM(COALESCE(SAFE_CAST(JSON_VALUE(attributes_json, '$."gen_ai.usage.input_tokens"') AS INT64), 0) + COALESCE(SAFE_CAST(JSON_VALUE(attributes_json, '$."gen_ai.usage.output_tokens"') AS INT64), 0)) AS total_tokens,
          (
            COUNTIF(name = 'docubridge.intake.collect_document') = 0
            OR (
              COUNTIF(name = 'docubridge.intake.redact_pii' AND status = 'OK') > 0
              AND COUNTIF(name = 'docubridge.vault.lock_worm_artifact' AND status = 'OK') > 0
              AND MIN(IF(name = 'docubridge.intake.redact_pii', start_time, NULL)) <= MIN(IF(name = 'docubridge.intake.collect_document', start_time, NULL))
            )
          ) AS gate_1_pii_redacted_before_extract,
          (
            COUNTIF(name IN ('docubridge.consolidation.promote_to_operational_store', 'docubridge.a2ui.render_surface')) = 0
            OR (
              COUNTIF(name = 'docubridge.identity.verify_jwt_svid' AND status = 'OK') > 0
              AND COUNTIF(agent_spiffe_id IS NOT NULL) > 0
            )
          ) AS gate_2_spiffe_zero_trust_verified,
          (
            COUNTIF(STARTS_WITH(name, 'docubridge.consolidation.') AND status = 'ERROR') = 0
          ) AS gate_3_decimal_math_and_bitemporal_audit,
          (
            COUNTIF(
              name = 'docubridge.agent_gym.distill_signal'
              AND COALESCE(SAFE_CAST(JSON_VALUE(attributes_json, '$."docubridge.oversight.rsr_score"') AS FLOAT64), 1.0) < 0.80
            ) = 0
          ) AS gate_4_oversight_anti_rubber_stamp,
          (
            COUNTIF(status = 'ERROR') = 0
            AND COUNTIF(JSON_VALUE(attributes_json, '$."docubridge.finops.traffic_type"') = 'conflict_escalation') <= 1
          ) AS gate_5_finops_workhorse_right_sized,
          MIN(start_time) AS trace_started_at,
          ROUND(SUM(duration_ms), 3) AS cumulative_span_duration_ms
        FROM deduped
        GROUP BY trace_id
      )
      SELECT
        *,
        (
          gate_1_pii_redacted_before_extract
          AND gate_2_spiffe_zero_trust_verified
          AND gate_3_decimal_math_and_bitemporal_audit
          AND gate_4_oversight_anti_rubber_stamp
          AND gate_5_finops_workhorse_right_sized
        ) AS rule0_sql_all_passed,
        (
          NOT (
            gate_1_pii_redacted_before_extract
            AND gate_2_spiffe_zero_trust_verified
            AND gate_3_decimal_math_and_bitemporal_audit
            AND gate_4_oversight_anti_rubber_stamp
            AND gate_5_finops_workhorse_right_sized
          )
          OR conflict_escalation_calls > 0
          OR MOD(ABS(FARM_FINGERPRINT(trace_id)), 100) < 5
        ) AS needs_llm_judge_sampling
      FROM per_trace
      ORDER BY trace_started_at DESC
    SQL
  }

  depends_on = [google_bigquery_table.otel_spans]
}

resource "google_logging_project_sink" "otel_spans_to_bq" {
  count = var.enable_telemetry_bq ? 1 : 0

  project                = var.project_id
  name                   = "docubridge-otel-spans-bq"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.telemetry[0].dataset_id}"
  filter                 = "jsonPayload.otel.span_id:*"
  unique_writer_identity = true

  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "sink_writer" {
  count = var.enable_telemetry_bq ? 1 : 0

  project    = var.project_id
  dataset_id = google_bigquery_dataset.telemetry[0].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.otel_spans_to_bq[0].writer_identity
}
