"""Unit tests for P8-7 (Read-Only Ontology MCP Surface) and P8-8 (Pre-Execution Query Validator)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridge.knowledge_catalog import (
    ONTOLOGY_MCP_ALLOWED_TOOLS,
    ONTOLOGY_MCP_FORBIDDEN_MUTATION_TOOLS,
    InMemoryKnowledgeCatalog,
    OntologyMcpSurface,
    SqlKnowledgeCatalog,
    UncertifiedMetricQueryError,
    validate_consolidation_view,
    validate_metric_query,
)
from bridge.operational_store import (
    InMemoryOperationalStore,
    consolidate_entry,
)
from contract.enums import Disposition, Gate
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry


def test_mcp_surface_tool_schemas_are_strictly_read_only() -> None:
    """P8-7: Only the 4 read-only tools are exposed; zero mutation tools exist."""
    catalog = InMemoryKnowledgeCatalog()
    surface = catalog.mcp_surface()
    tools = surface.list_tools()

    assert tuple(t.name for t in tools) == ONTOLOGY_MCP_ALLOWED_TOOLS
    for spec in tools:
        assert spec.read_only is True
        assert spec.input_schema["type"] == "object"
        assert spec.input_schema["additionalProperties"] is False
        assert len(spec.input_schema["required"]) >= 1

    # No forbidden mutation tool appears in the MCP tool list
    exposed_names = {t.name for t in tools}
    assert exposed_names.isdisjoint(ONTOLOGY_MCP_FORBIDDEN_MUTATION_TOOLS)


@pytest.mark.parametrize(
    "forbidden_tool",
    [
        "onboard_doctype_from_examples",
        "onboard_use_case",
        "register_proposal",
        "promote_proposal",
        "register_ontology_revision",
        "reset_ontology_revisions",
        "drop_ontology_graph",
    ],
)
def test_mcp_surface_rejects_mutation_and_unlisted_tools(forbidden_tool: str) -> None:
    """P8-7: State-changing ontology mutations are deterministically rejected."""
    surface = OntologyMcpSurface(InMemoryKnowledgeCatalog())
    with pytest.raises(PermissionError, match=r"not allow-listed|forbidden"):
        surface.call_tool(forbidden_tool, {"entity_id": "test-entity"})


def test_mcp_get_entity_ontology_and_aliases() -> None:
    """P8-7: get_entity_ontology resolves entity IDs and aliases from active revision."""
    catalog = InMemoryKnowledgeCatalog()
    resp = catalog.get_entity_ontology("credit-agreement")
    assert resp.entity_id == "cre-credit-agreement"
    assert resp.source_priority == 30
    assert "loan_amount" in resp.fields
    assert resp.fields["loan_amount"]["monetary"] is True
    assert resp.fields["loan_amount"]["feeds_metric"] == "total_coverage_limit"
    assert resp.entity_description["what_it_is"]
    assert resp.entity_description["distinguish_from"]
    assert len(resp.outgoing_edges) >= 1

    # Via MCP call_tool dispatcher
    dispatched = catalog.mcp_surface().call_tool(
        "get_entity_ontology", {"doctype": "trid-closing-disclosure"}
    )
    assert dispatched["entity_id"] == "trid-closing-disclosure"
    assert dispatched["source_priority"] == 40

    with pytest.raises(KeyError, match="Unknown doctype"):
        catalog.get_entity_ontology("nonexistent-unlisted-doctype-xyz")


def test_mcp_explain_metric_formula() -> None:
    """P8-7: explain_metric_formula returns certified [:FEEDS_METRIC] edges and fields."""
    catalog = InMemoryKnowledgeCatalog()
    explanation = catalog.explain_metric_formula("total_coverage_limit")
    assert explanation.canonical_bucket == "total_coverage_limit"
    assert explanation.metric_operation == "SUM_ACTIVE_DECIMAL"
    assert explanation.requires_bitemporal_watermark is True
    assert explanation.requires_supersedes_precedence is True
    assert len(explanation.feeding_edges) >= 1
    feeding_field_names = {f["field_name"] for f in explanation.feeding_fields}
    assert "loan_amount" in feeding_field_names
    assert "coverage_limit" in feeding_field_names

    # Filtered by doctype and invoked via call_tool
    scoped = catalog.mcp_surface().call_tool(
        "explain_metric_formula",
        {"metric_id": "term.premium", "doctype": "commercial-property-quote"},
    )
    assert scoped["canonical_bucket"] == "total_premium"
    assert scoped["glossary_term_id"] == "term.premium"
    assert all(
        f["entity_id"] == "commercial-property-quote"
        for f in scoped["feeding_fields"]
    )

    with pytest.raises(KeyError, match="Uncertified metric"):
        catalog.explain_metric_formula("uncertified_random_metric")


def test_mcp_check_doctype_precedence() -> None:
    """P8-7: check_doctype_precedence resolves authority tiers and direct [:SUPERSEDES] edges."""
    catalog = InMemoryKnowledgeCatalog()
    verdict = catalog.check_doctype_precedence(
        "trid-closing-disclosure",
        "cre-credit-agreement",
        field_name="origination_fee",
    )
    assert verdict.priority_a == 40
    assert verdict.priority_b == 30
    assert verdict.winning_doctype == "trid-closing-disclosure"
    assert verdict.direct_supersedes_edge_id is not None

    # Equal-priority tie falls back to bi-temporal tie-breaker order
    tie = catalog.mcp_surface().call_tool(
        "check_doctype_precedence",
        {"doctype_a": "mai-appraisal", "doctype_b": "commercial-property-quote"},
    )
    assert tie["priority_a"] == 20
    assert tie["priority_b"] == 20
    assert tie["winning_doctype"] is None
    assert tie["tie_breaker_order"] == [
        "source_priority",
        "effective_from",
        "recorded_at",
        "version",
    ]


def test_mcp_trace_field_lineage() -> None:
    """P8-7: trace_field_lineage returns 6-hop W3C PROV-O lineage over KnowledgeCatalog."""
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    surface = catalog.mcp_surface()

    missing = surface.call_tool(
        "trace_field_lineage",
        {"entity_id": "DEAL-TEST-100", "field_name": "loan_amount"},
    )
    assert missing["found"] is False
    assert missing["trace"] is None

    entry = LedgerEntry(
        doc_id="doc-cd-100",
        doctype="trid-closing-disclosure",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        issuer="First National Bank",
        key_fields={
            "loan_amount": "$25,000,000.00",
            "origination_fee": "$125,000.00",
            "effective_date": "2026-10-15",
        },
        source_anchors={
            "fields.key_fields.loan_amount": SourceAnchor(
                page_number=2,
                snippet="Loan Amount: $25,000,000.00",
            )
        },
    )
    facts = consolidate_entry(
        entry,
        entity_id="DEAL-TEST-100",
        context_id="ctx-test-100",
        recorded_at=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
    )
    store.append_facts(facts)
    catalog.register_submission_lineage(entry, facts)

    found = surface.call_tool(
        "trace_field_lineage",
        {"entity_id": "DEAL-TEST-100", "field_name": "loan_amount"},
    )
    assert found["found"] is True
    assert found["trace"]["source_doc_id"] == "doc-cd-100"
    assert found["trace"]["source_doctype"] == "trid-closing-disclosure"
    assert len(found["trace"]["nodes"]) == 6


def test_validate_metric_query_accepts_certified_formulas_and_sql() -> None:
    """P8-8: Certified [:FEEDS_METRIC] fields, bi-temporal bounds, and precedence pass."""
    catalog = InMemoryKnowledgeCatalog()
    res = catalog.validate_metric_query(
        "total_coverage_limit",
        fields=("loan_amount", "coverage_limit"),
        operation="SUM_ACTIVE_DECIMAL",
        learned_as_of=datetime(2026, 11, 1, tzinfo=UTC),
        effective_on=datetime(2026, 11, 1, tzinfo=UTC),
        require_bitemporal_watermark=True,
    )
    assert res.valid is True
    assert res.violations == ()
    assert "loan_amount" in res.certified_fields
    assert len(res.certified_edge_ids) >= 1

    governed_sql = (
        "WITH active_facts AS ("
        "SELECT *, ROW_NUMBER() OVER ("
        "PARTITION BY entity_id, field_path "
        "ORDER BY source_priority DESC, effective_from DESC, recorded_at DESC, version DESC"
        ") AS rn FROM bq_operational_facts_iceberg "
        "WHERE recorded_at <= TIMESTAMP('2026-11-01T00:00:00Z') "
        "AND effective_from <= TIMESTAMP('2026-11-01T00:00:00Z')"
        ") SELECT SUM(numeric_value) FROM active_facts WHERE rn = 1"
    )
    sql_res = validate_metric_query(
        "total_premium",
        fields=("premium",),
        sql=governed_sql,
        catalog=catalog,
    )
    assert sql_res.valid is True
    assert sql_res.bitemporal_predicates_verified is True
    assert sql_res.precedence_predicates_verified is True


def test_validate_metric_query_rejects_uncertified_formulas_and_missing_predicates() -> None:
    """P8-8: Queries bypassing [:FEEDS_METRIC] or omitting bi-temporal/precedence fail."""
    catalog = InMemoryKnowledgeCatalog()

    # 1. Field does not feed the requested metric bucket (appraised_value -> total_premium)
    bad_field = catalog.validate_metric_query(
        "total_premium",
        fields=("appraised_value",),
    )
    assert bad_field.valid is False
    assert any(v.code == "UNCERTIFIED_FIELD_FOR_METRIC" for v in bad_field.violations)

    # 2. Unsupported float operation
    bad_op = catalog.validate_metric_query(
        "total_premium",
        fields=("premium",),
        operation="AVG_FLOAT",
    )
    assert bad_op.valid is False
    assert any(v.code == "UNSUPPORTED_METRIC_OPERATION" for v in bad_op.violations)

    # 3. Missing bi-temporal watermarks & missing [:SUPERSEDES] precedence in raw SQL
    raw_sql = (
        "SELECT SUM(numeric_value) FROM bq_operational_facts_iceberg "
        "WHERE field_path = 'fields.key_fields.loan_amount'"
    )
    bad_sql = catalog.validate_metric_query(
        "total_coverage_limit",
        fields=("loan_amount",),
        sql=raw_sql,
    )
    assert bad_sql.valid is False
    codes = {v.code for v in bad_sql.violations}
    assert "MISSING_BITEMPORAL_WATERMARK" in codes
    assert "MISSING_SUPERSEDES_PRECEDENCE" in codes

    with pytest.raises(UncertifiedMetricQueryError, match="MISSING_SUPERSEDES_PRECEDENCE"):
        catalog.validate_metric_query(
            "total_coverage_limit",
            fields=("loan_amount",),
            sql=raw_sql,
            raise_on_invalid=True,
        )


def test_validate_consolidation_view_enforces_precedence_and_watermarks() -> None:
    """P8-8: validate_consolidation_view checks Zone 4 SQL views and watermarks."""
    catalog = InMemoryKnowledgeCatalog()

    for view_name in (
        "v_broker_placement",
        "v_claims_coverage",
        "v_underwriter_exposure",
    ):
        verdict = validate_consolidation_view(view_name, catalog=catalog)
        assert verdict.valid is True, f"{view_name} should pass: {verdict.violations}"
        assert verdict.bitemporal_predicates_verified is True
        assert verdict.precedence_predicates_verified is True

    # Unknown view is rejected
    unknown = validate_consolidation_view("v_unregistered_custom_view", catalog=catalog)
    assert unknown.valid is False
    assert unknown.violations[0].code == "UNKNOWN_CONSOLIDATION_VIEW"

    # View SQL stripped of [:SUPERSEDES] windowing is rejected
    unconsolidated_view_sql = (
        "CREATE VIEW v_broker_placement AS "
        "SELECT entity_id, field_path, field_value, recorded_at, effective_from "
        "FROM bridge_operational_facts;"
    )
    bad_view = validate_consolidation_view(
        "v_broker_placement",
        sql=unconsolidated_view_sql,
        catalog=catalog,
    )
    assert bad_view.valid is False
    assert any(v.code == "MISSING_SUPERSEDES_PRECEDENCE" for v in bad_view.violations)

    # Requiring explicit runtime watermarks without supplying them fails
    missing_wm = validate_consolidation_view(
        "v_claims_coverage",
        require_watermarks=True,
        catalog=catalog,
    )
    assert missing_wm.valid is False
    assert any(v.code == "MISSING_BITEMPORAL_WATERMARK" for v in missing_wm.violations)


def test_sql_knowledge_catalog_mcp_and_validator_parity(tmp_path: Path) -> None:
    """P8-7 & P8-8: SqlKnowledgeCatalog and InMemoryKnowledgeCatalog behave identically."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/catalog_mcp.db"
    sql_catalog = SqlKnowledgeCatalog(dsn)
    try:
        ont = sql_catalog.get_entity_ontology("trid-closing-disclosure")
        assert ont.entity_id == "trid-closing-disclosure"
        assert ont.source_priority == 40

        formula = sql_catalog.explain_metric_formula("total_coverage_limit")
        assert formula.canonical_bucket == "total_coverage_limit"
        assert "loan_amount" in {f["field_name"] for f in formula.feeding_fields}

        prec = sql_catalog.check_doctype_precedence(
            "trid-closing-disclosure", "mai-appraisal"
        )
        assert prec.winning_doctype == "trid-closing-disclosure"

        valid_q = sql_catalog.validate_metric_query(
            "total_coverage_limit",
            fields=("loan_amount",),
            learned_as_of=datetime(2026, 11, 1, tzinfo=UTC),
            require_bitemporal_watermark=True,
        )
        assert valid_q.valid is True

        invalid_q = sql_catalog.validate_metric_query(
            "total_coverage_limit",
            fields=("effective_date",),
        )
        assert invalid_q.valid is False
        assert any(
            v.code == "UNCERTIFIED_FIELD_FOR_METRIC" for v in invalid_q.violations
        )
    finally:
        sql_catalog.close()
