"""Zone 6 Pre-Execution Query & Metric Formula Validator (P8-8).

Validates Zone 5 Domain Agent queries and analytical rollups over
``OperationalStore`` and BigQuery (``bq_operational_facts_iceberg``) against:
1. Certified ``[:FEEDS_METRIC]`` ontology edges and monetary field declarations,
2. Bi-temporal watermarks (``learned_as_of`` / ``recorded_at``, ``effective_on`` / ``effective_from``), and
3. ``[:SUPERSEDES]`` active-fact precedence predicates before execution.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from bridge.knowledge_catalog.mcp_surface import (
    _CANONICAL_METRIC_BUCKETS,
    _resolve_catalog,
)
from bridge.telemetry import trace_step

if TYPE_CHECKING:
    from bridge.knowledge_catalog.catalog import KnowledgeCatalog

ViolationCode = Literal[
    "UNCERTIFIED_METRIC_BUCKET",
    "UNCERTIFIED_FIELD_FOR_METRIC",
    "NON_MONETARY_FIELD_AGGREGATION",
    "UNSUPPORTED_METRIC_OPERATION",
    "MISSING_BITEMPORAL_WATERMARK",
    "MISSING_SUPERSEDES_PRECEDENCE",
    "UNKNOWN_CONSOLIDATION_VIEW",
]

CERTIFIED_METRIC_OPERATIONS: frozenset[str] = frozenset(
    {
        "SUM_ACTIVE_DECIMAL",
        "RATIO_ACTIVE_DECIMAL",
        "BITEMPORAL_AS_OF_LOOKUP",
    }
)

_RAW_FACT_TABLES: tuple[str, ...] = (
    "bridge_operational_facts",
    "bq_operational_facts_iceberg",
)
_GOVERNED_VIEWS: frozenset[str] = frozenset(
    {
        "v_broker_placement",
        "v_claims_coverage",
        "v_underwriter_exposure",
    }
)


class UncertifiedMetricQueryError(ValueError):
    """Raised when a Domain Agent query or rollup fails Zone 6 pre-execution validation."""


class QueryValidationViolation(BaseModel):
    """Single deterministic violation detected during pre-execution query validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ViolationCode
    message: str
    field_name: str | None = None
    metric_bucket: str | None = None


class MetricQueryValidationResult(BaseModel):
    """Structured pre-execution validation verdict for a metric query or view."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    ontology_revision_id: str
    target_metric: str
    certified_edge_ids: tuple[str, ...] = ()
    certified_fields: tuple[str, ...] = ()
    bitemporal_predicates_verified: bool = True
    precedence_predicates_verified: bool = True
    violations: tuple[QueryValidationViolation, ...] = ()


def _resolve_target_buckets(
    metric_bucket: str,
    catalog: KnowledgeCatalog,
) -> tuple[set[str], set[str]]:
    """Return (target_buckets, allowed_non_monetary_fields) for ``metric_bucket``."""
    raw = (metric_bucket or "").strip()
    if not raw:
        return set(), set()

    leaf = raw.split(".")[-1].strip().lower()
    if leaf in _CANONICAL_METRIC_BUCKETS:
        if leaf == "deductible_to_limit_pct":
            return (
                {
                    "deductible_to_limit_pct",
                    "total_deductible",
                    "total_coverage_limit",
                    "total_insured_value",
                },
                set(),
            )
        return {leaf}, set()

    glossary = {
        t.term_id: t for t in catalog.list_glossary_terms(include_confidential=True)
    }
    term = glossary.get(raw)
    if term is not None:
        if term.term_id == "term.premium":
            return {"total_premium"}, set()
        if term.term_id == "term.deductible_to_limit_ratio":
            return (
                {
                    "deductible_to_limit_pct",
                    "total_deductible",
                    "total_coverage_limit",
                    "total_insured_value",
                },
                set(),
            )
        if term.term_id == "term.loan_to_value_ratio":
            return (
                {
                    "total_coverage_limit",
                    "total_insured_value",
                    "total_deductible",
                },
                set(),
            )
        return {term.term_id}, {f.lower() for f in term.canonical_fields}

    return set(), set()


def _sql_has_supersedes_precedence(sql_text: str) -> bool:
    """Return True if ``sql_text`` enforces active-fact precedence deduplication."""
    low = sql_text.lower()
    if any(view in low for view in _GOVERNED_VIEWS) and not any(
        tbl in low for tbl in _RAW_FACT_TABLES
    ):
        return True
    has_partition = bool(re.search(r"partition\s+by\s+entity_id\s*,\s*field_path", low))
    has_priority_order = "source_priority" in low and "desc" in low
    has_rn_filter = bool(re.search(r"\brn\s*=\s*1\b", low))
    return has_partition and has_priority_order and has_rn_filter


def _sql_has_bitemporal_predicates(sql_text: str) -> bool:
    """Return True if ``sql_text`` binds or projects bi-temporal watermarks."""
    low = sql_text.lower()
    return "recorded_at" in low and "effective_from" in low


def validate_metric_query(
    metric_bucket: str,
    *,
    fields: Sequence[str] = (),
    operation: str = "SUM_ACTIVE_DECIMAL",
    sql: str | None = None,
    learned_as_of: datetime | str | None = None,
    effective_on: datetime | str | None = None,
    require_bitemporal_watermark: bool = False,
    include_supersedes_precedence: bool = True,
    doctype: str | None = None,
    catalog: KnowledgeCatalog | None = None,
    raise_on_invalid: bool = False,
) -> MetricQueryValidationResult:
    """Deterministically validate a metric rollup or analytical query before execution."""
    resolved_catalog = _resolve_catalog(catalog)
    active = resolved_catalog.get_active_ontology_revision()

    with trace_step(
        "docubridge.catalog.validate_metric_query",
        {
            "docubridge.zone": "6_knowledge_catalog_agent_gym",
            "docubridge.metric_bucket": metric_bucket,
            "docubridge.operation": operation,
            "docubridge.ontology_revision_id": active.revision_id,
        },
    ) as span:
        violations: list[QueryValidationViolation] = []
        certified_edges: list[str] = []
        certified_fields: list[str] = []

        target_buckets, glossary_fields = _resolve_target_buckets(
            metric_bucket, resolved_catalog
        )
        if not target_buckets:
            violations.append(
                QueryValidationViolation(
                    code="UNCERTIFIED_METRIC_BUCKET",
                    message=(
                        f"Metric bucket or glossary term '{metric_bucket}' is not "
                        f"certified in Zone 6 OntologyRevision '{active.revision_id}'."
                    ),
                    metric_bucket=metric_bucket,
                )
            )

        norm_op = (operation or "").strip().upper()
        if norm_op not in CERTIFIED_METRIC_OPERATIONS:
            violations.append(
                QueryValidationViolation(
                    code="UNSUPPORTED_METRIC_OPERATION",
                    message=(
                        f"Metric operation '{operation}' is not a certified deterministic "
                        f"operation (allowed: {', '.join(sorted(CERTIFIED_METRIC_OPERATIONS))})."
                    ),
                    metric_bucket=metric_bucket,
                )
            )

        for raw_field in fields:
            leaf = (raw_field or "").split(".")[-1].strip().lower()
            if not leaf:
                continue
            resolved_bucket, edge_id = resolved_catalog.resolve_metric_bucket(
                leaf, doctype=doctype
            )
            in_target = (
                resolved_bucket is not None
                and resolved_bucket.lower() in target_buckets
            ) or (leaf in glossary_fields)

            if not in_target:
                violations.append(
                    QueryValidationViolation(
                        code="UNCERTIFIED_FIELD_FOR_METRIC",
                        message=(
                            f"Field '{raw_field}' has no certified [:FEEDS_METRIC] edge "
                            f"feeding '{metric_bucket}' (resolved bucket: "
                            f"{resolved_bucket!r})."
                        ),
                        field_name=raw_field,
                        metric_bucket=metric_bucket,
                    )
                )
                continue

            if norm_op in (
                "SUM_ACTIVE_DECIMAL",
                "RATIO_ACTIVE_DECIMAL",
            ) and not resolved_catalog.is_monetary_field(leaf):
                violations.append(
                    QueryValidationViolation(
                        code="NON_MONETARY_FIELD_AGGREGATION",
                        message=(
                            f"Field '{raw_field}' is not declared monetary in active "
                            f"OntologyRevision '{active.revision_id}' and cannot be "
                            f"aggregated with '{norm_op}'."
                        ),
                        field_name=raw_field,
                        metric_bucket=metric_bucket,
                    )
                )
                continue

            certified_fields.append(leaf)
            if edge_id and edge_id not in certified_edges:
                certified_edges.append(edge_id)

        if not fields and target_buckets:
            for edge in active.edges:
                if edge.relation != "FEEDS_METRIC":
                    continue
                edge_bucket = edge.target_id.split(".")[-1].strip().lower()
                if edge_bucket in target_buckets:
                    if edge.edge_id not in certified_edges:
                        certified_edges.append(edge.edge_id)
                    for f in edge.on_fields:
                        fl = f.lower()
                        if fl not in certified_fields:
                            certified_fields.append(fl)

        bitemporal_ok = True
        precedence_ok = True

        has_param_watermark = learned_as_of is not None or effective_on is not None
        if sql is not None:
            sql_bitemp = _sql_has_bitemporal_predicates(sql)
            sql_prec = _sql_has_supersedes_precedence(sql)
            if not sql_bitemp and not has_param_watermark:
                bitemporal_ok = False
                violations.append(
                    QueryValidationViolation(
                        code="MISSING_BITEMPORAL_WATERMARK",
                        message=(
                            "Analytical SQL query over operational facts omits "
                            "bi-temporal watermarks (recorded_at / learned_as_of and "
                            "effective_from / effective_on)."
                        ),
                        metric_bucket=metric_bucket,
                    )
                )
            if not sql_prec:
                precedence_ok = False
                violations.append(
                    QueryValidationViolation(
                        code="MISSING_SUPERSEDES_PRECEDENCE",
                        message=(
                            "Analytical SQL query scans raw operational facts without "
                            "[:SUPERSEDES] active-fact precedence resolution "
                            "(ROW_NUMBER() OVER (PARTITION BY entity_id, field_path "
                            "ORDER BY source_priority DESC ...) WHERE rn = 1)."
                        ),
                        metric_bucket=metric_bucket,
                    )
                )
        else:
            if require_bitemporal_watermark and not has_param_watermark:
                bitemporal_ok = False
                violations.append(
                    QueryValidationViolation(
                        code="MISSING_BITEMPORAL_WATERMARK",
                        message=(
                            f"Metric query for '{metric_bucket}' requires an explicit "
                            f"bi-temporal watermark (learned_as_of or effective_on)."
                        ),
                        metric_bucket=metric_bucket,
                    )
                )
            if not include_supersedes_precedence:
                precedence_ok = False
                violations.append(
                    QueryValidationViolation(
                        code="MISSING_SUPERSEDES_PRECEDENCE",
                        message=(
                            f"Metric query for '{metric_bucket}' disabled [:SUPERSEDES] "
                            f"active-fact precedence resolution."
                        ),
                        metric_bucket=metric_bucket,
                    )
                )

        is_valid = len(violations) == 0
        span.set_attribute("docubridge.query_valid", is_valid)
        span.set_attribute("docubridge.violation_count", len(violations))

        result = MetricQueryValidationResult(
            valid=is_valid,
            ontology_revision_id=active.revision_id,
            target_metric=metric_bucket,
            certified_edge_ids=tuple(certified_edges),
            certified_fields=tuple(certified_fields),
            bitemporal_predicates_verified=bitemporal_ok,
            precedence_predicates_verified=precedence_ok,
            violations=tuple(violations),
        )
        if raise_on_invalid and not result.valid:
            joined = "; ".join(f"[{v.code}] {v.message}" for v in result.violations)
            raise UncertifiedMetricQueryError(joined)
        return result


def validate_consolidation_view(
    view_name: str,
    *,
    sql: str | None = None,
    learned_as_of: datetime | str | None = None,
    effective_on: datetime | str | None = None,
    require_watermarks: bool = False,
    catalog: KnowledgeCatalog | None = None,
    raise_on_invalid: bool = False,
) -> MetricQueryValidationResult:
    """Validate a Zone 4 / Zone 5 business view definition and execution parameters."""
    from bridge.operational_store.iceberg_sync import SQL_VIEW_DEFINITIONS

    resolved_catalog = _resolve_catalog(catalog)
    active = resolved_catalog.get_active_ontology_revision()

    with trace_step(
        "docubridge.catalog.validate_consolidation_view",
        {
            "docubridge.zone": "6_knowledge_catalog_agent_gym",
            "docubridge.view_name": view_name,
            "docubridge.ontology_revision_id": active.revision_id,
        },
    ) as span:
        violations: list[QueryValidationViolation] = []
        cleaned_view = (view_name or "").strip()

        if cleaned_view not in SQL_VIEW_DEFINITIONS:
            violations.append(
                QueryValidationViolation(
                    code="UNKNOWN_CONSOLIDATION_VIEW",
                    message=(
                        f"View '{cleaned_view}' is not a registered Zone 4 business view "
                        f"(expected one of: {', '.join(sorted(SQL_VIEW_DEFINITIONS))})."
                    ),
                    metric_bucket=cleaned_view,
                )
            )
            result = MetricQueryValidationResult(
                valid=False,
                ontology_revision_id=active.revision_id,
                target_metric=cleaned_view,
                bitemporal_predicates_verified=False,
                precedence_predicates_verified=False,
                violations=tuple(violations),
            )
            span.set_attribute("docubridge.query_valid", False)
            if raise_on_invalid:
                raise UncertifiedMetricQueryError(result.violations[0].message)
            return result

        target_sql = sql if sql is not None else SQL_VIEW_DEFINITIONS[cleaned_view]
        bitemporal_ok = _sql_has_bitemporal_predicates(target_sql)
        precedence_ok = _sql_has_supersedes_precedence(target_sql)

        if not precedence_ok:
            violations.append(
                QueryValidationViolation(
                    code="MISSING_SUPERSEDES_PRECEDENCE",
                    message=(
                        f"Consolidation view '{cleaned_view}' omits [:SUPERSEDES] "
                        f"active-fact precedence windowing (PARTITION BY entity_id, "
                        f"field_path ORDER BY source_priority DESC ... WHERE rn = 1)."
                    ),
                    metric_bucket=cleaned_view,
                )
            )

        has_watermarks = learned_as_of is not None or effective_on is not None
        if not bitemporal_ok or (require_watermarks and not has_watermarks):
            bitemporal_ok = False
            violations.append(
                QueryValidationViolation(
                    code="MISSING_BITEMPORAL_WATERMARK",
                    message=(
                        f"Consolidation view '{cleaned_view}' is missing required "
                        f"bi-temporal watermarks (effective_from / effective_on and "
                        f"recorded_at / learned_as_of)."
                    ),
                    metric_bucket=cleaned_view,
                )
            )

        certified_edges: list[str] = []
        certified_fields: list[str] = []
        for edge in active.edges:
            if edge.relation == "FEEDS_METRIC":
                if edge.edge_id not in certified_edges:
                    certified_edges.append(edge.edge_id)
                for f in edge.on_fields:
                    fl = f.lower()
                    if fl not in certified_fields:
                        certified_fields.append(fl)

        is_valid = len(violations) == 0
        span.set_attribute("docubridge.query_valid", is_valid)
        result = MetricQueryValidationResult(
            valid=is_valid,
            ontology_revision_id=active.revision_id,
            target_metric=cleaned_view,
            certified_edge_ids=tuple(certified_edges),
            certified_fields=tuple(certified_fields),
            bitemporal_predicates_verified=bitemporal_ok,
            precedence_predicates_verified=precedence_ok,
            violations=tuple(violations),
        )
        if raise_on_invalid and not result.valid:
            joined = "; ".join(f"[{v.code}] {v.message}" for v in result.violations)
            raise UncertifiedMetricQueryError(joined)
        return result
