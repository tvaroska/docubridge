"""Zone 6 Read-Only Allow-Listed Ontology MCP Surface (P8-7).

Exposes a strictly read-only, allow-listed MCP tool surface over Zone 6
``KnowledgeCatalog`` for calling A2A and MCP agents:
- ``get_entity_ontology``
- ``explain_metric_formula``
- ``check_doctype_precedence``
- ``trace_field_lineage``

All state-changing ontology mutations (``onboard_doctype_from_examples``,
``register_proposal``, ``promote_proposal``, ``register_ontology_revision``,
``reset_ontology_revisions``) are isolated to the Assay-Gated admin control
plane and deterministically rejected on this MCP surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bridge.telemetry import trace_step

if TYPE_CHECKING:
    from bridge.knowledge_catalog.catalog import KnowledgeCatalog

ONTOLOGY_MCP_ALLOWED_TOOLS: tuple[str, ...] = (
    "get_entity_ontology",
    "explain_metric_formula",
    "check_doctype_precedence",
    "trace_field_lineage",
)

ONTOLOGY_MCP_FORBIDDEN_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "onboard_doctype_from_examples",
        "onboard_use_case",
        "register_proposal",
        "promote_proposal",
        "register_ontology_revision",
        "reset_ontology_revisions",
        "register_asset",
        "register_spiffe_agent",
        "register_submission_lineage",
    }
)

_CANONICAL_METRIC_BUCKETS: dict[str, tuple[str, str]] = {
    "total_premium": ("SUM_ACTIVE_DECIMAL", "term.premium"),
    "total_deductible": ("SUM_ACTIVE_DECIMAL", "term.deductible_to_limit_ratio"),
    "total_coverage_limit": ("SUM_ACTIVE_DECIMAL", "term.loan_to_value_ratio"),
    "total_insured_value": ("SUM_ACTIVE_DECIMAL", "term.loan_to_value_ratio"),
    "deductible_to_limit_pct": (
        "RATIO_ACTIVE_DECIMAL",
        "term.deductible_to_limit_ratio",
    ),
}


class McpToolSpec(BaseModel):
    """Allow-listed read-only MCP tool descriptor for Zone 6 Knowledge Catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    read_only: Literal[True] = True
    input_schema: dict[str, Any]


class EntityOntologyResponse(BaseModel):
    """Read-only ontology specification and 1-hop graph neighborhood for an entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    version: str
    entity_id: str
    context_set: str
    source_priority: int
    aliases: tuple[str, ...] = ()
    declares_issuer: bool = False
    issuer_description: str = ""
    entity_description: dict[str, str]
    fields: dict[str, dict[str, Any]]
    outgoing_edges: tuple[dict[str, Any], ...] = ()
    incoming_edges: tuple[dict[str, Any], ...] = ()


class MetricFormulaExplanation(BaseModel):
    """Certified field-to-metric mapping and formula lineage from Zone 6."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    metric_id: str
    canonical_bucket: str
    metric_operation: str
    glossary_term_id: str | None = None
    display_name: str | None = None
    calculation_formula: str | None = None
    feeding_edges: tuple[dict[str, Any], ...] = ()
    feeding_fields: tuple[dict[str, Any], ...] = ()
    requires_bitemporal_watermark: bool = True
    requires_supersedes_precedence: bool = True


class DoctypePrecedenceVerdict(BaseModel):
    """Deterministic authority hierarchy comparison between two document types."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    doctype_a: str
    resolved_entity_a: str | None = None
    priority_a: int
    edge_id_a: str | None = None
    doctype_b: str
    resolved_entity_b: str | None = None
    priority_b: int
    edge_id_b: str | None = None
    winning_doctype: str | None = None
    direct_supersedes_edge_id: str | None = None
    tie_breaker_order: tuple[str, ...] = Field(
        default=(
            "source_priority",
            "effective_from",
            "recorded_at",
            "version",
        )
    )
    explanation: str


ONTOLOGY_MCP_TOOL_SPECS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        name="get_entity_ontology",
        description=(
            "Return the active OntologyRevision entity schema, contrastive "
            "descriptions, field specifications, and 1-hop graph edges for a "
            "document type or alias."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "doctype": {
                    "type": "string",
                    "description": "Entity ID or doctype alias to inspect.",
                }
            },
            "required": ["doctype"],
            "additionalProperties": False,
        },
    ),
    McpToolSpec(
        name="explain_metric_formula",
        description=(
            "Explain the certified [:FEEDS_METRIC] graph edges, contributing "
            "monetary fields, and business glossary formula for a target "
            "metric bucket or glossary term."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "metric_id": {
                    "type": "string",
                    "description": (
                        "Target FinancialSummary bucket (e.g. total_coverage_limit), "
                        "glossary term ID (e.g. term.premium), or feeding field name."
                    ),
                },
                "doctype": {
                    "type": "string",
                    "description": "Optional doctype filter for feeding fields.",
                },
            },
            "required": ["metric_id"],
            "additionalProperties": False,
        },
    ),
    McpToolSpec(
        name="check_doctype_precedence",
        description=(
            "Compare two document types against the active Zone 6 Ontology Graph "
            "to determine which document carries higher consolidation authority "
            "via [:SUPERSEDES] / [:SPLIT_FROM] edges and source_priority tiers."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "doctype_a": {
                    "type": "string",
                    "description": "First document type or alias.",
                },
                "doctype_b": {
                    "type": "string",
                    "description": "Second document type or alias.",
                },
                "field_name": {
                    "type": "string",
                    "description": "Optional canonical field name being consolidated.",
                },
            },
            "required": ["doctype_a", "doctype_b"],
            "additionalProperties": False,
        },
    ),
    McpToolSpec(
        name="trace_field_lineage",
        description=(
            "Trace the 6-hop W3C PROV-O lineage chain for an entity and field "
            "from Zone 4/5 views back to Zone 3 facts and Zone 2 WORM vault PDF pages."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Target deal, loan, or placement entity ID.",
                },
                "field_name": {
                    "type": "string",
                    "description": "Canonical field name or dotted field path.",
                },
            },
            "required": ["entity_id", "field_name"],
            "additionalProperties": False,
        },
    ),
)


def _resolve_catalog(catalog: KnowledgeCatalog | None) -> KnowledgeCatalog:
    if catalog is not None:
        return catalog
    from bridge.knowledge_catalog.catalog import knowledge_catalog_from_env

    return knowledge_catalog_from_env()


class OntologyMcpSurface:
    """Strictly read-only, allow-listed MCP tool surface over Zone 6 KnowledgeCatalog."""

    def __init__(self, catalog: KnowledgeCatalog | None = None) -> None:
        self._catalog = _resolve_catalog(catalog)

    @property
    def catalog(self) -> KnowledgeCatalog:
        """Return the backing Zone 6 KnowledgeCatalog."""
        return self._catalog

    def list_tools(self) -> list[McpToolSpec]:
        """Return the allow-listed read-only MCP tool specifications."""
        return list(ONTOLOGY_MCP_TOOL_SPECS)

    def get_entity_ontology(self, doctype: str) -> EntityOntologyResponse:
        """Return the active ontology specification and edges for ``doctype``."""
        with trace_step(
            "docubridge.catalog.mcp.get_entity_ontology",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doctype": doctype,
            },
        ) as span:
            active = self._catalog.get_active_ontology_revision()
            norm = (doctype or "").strip().lower()
            if not norm:
                raise KeyError("doctype must be a non-empty string")

            matched = active.entities.get(norm)
            if matched is None:
                for spec in active.entities.values():
                    if norm in (a.lower() for a in spec.aliases):
                        matched = spec
                        break
            if matched is None:
                candidates = []
                for spec in active.entities.values():
                    for tok in (
                        spec.entity_id.lower(),
                        *(a.lower() for a in spec.aliases),
                    ):
                        if tok and tok in norm:
                            candidates.append((len(tok), spec.source_priority, spec))
                if candidates:
                    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                    matched = candidates[0][2]

            if matched is None:
                span.set_attribute("docubridge.entity_found", False)
                raise KeyError(
                    f"Unknown doctype or ontology entity '{doctype}' in active revision "
                    f"'{active.revision_id}'."
                )

            span.set_attribute("docubridge.entity_found", True)
            span.set_attribute("docubridge.resolved_entity_id", matched.entity_id)

            fields_payload: dict[str, dict[str, Any]] = {}
            for fname, fspec in matched.fields.items():
                fields_payload[fname] = {
                    "name": fspec.name,
                    "field_type": fspec.field_type,
                    "required": fspec.required,
                    "monetary": fspec.monetary,
                    "feeds_metric": fspec.feeds_metric,
                    "synonyms": list(fspec.synonyms),
                    "description": {
                        "what_it_is": fspec.description.what_it_is,
                        "where_to_read": fspec.description.where_to_read,
                        "distinguish_from": fspec.description.distinguish_from,
                        "rendered": fspec.description.render(),
                    },
                }

            outgoing = tuple(
                e.model_dump(mode="json")
                for e in active.edges
                if e.source_id == matched.entity_id
            )
            incoming = tuple(
                e.model_dump(mode="json")
                for e in active.edges
                if e.target_id == matched.entity_id
            )

            return EntityOntologyResponse(
                revision_id=active.revision_id,
                version=active.version,
                entity_id=matched.entity_id,
                context_set=matched.context_set,
                source_priority=matched.source_priority,
                aliases=matched.aliases,
                declares_issuer=matched.declares_issuer,
                issuer_description=matched.issuer_description,
                entity_description={
                    "what_it_is": matched.entity_description.what_it_is,
                    "where_to_read": matched.entity_description.where_to_read,
                    "distinguish_from": matched.entity_description.distinguish_from,
                    "rendered": matched.entity_description.render(),
                },
                fields=fields_payload,
                outgoing_edges=outgoing,
                incoming_edges=incoming,
            )

    def explain_metric_formula(
        self,
        metric_id: str,
        *,
        doctype: str | None = None,
    ) -> MetricFormulaExplanation:
        """Explain certified ``[:FEEDS_METRIC]`` edges and formulas for ``metric_id``."""
        with trace_step(
            "docubridge.catalog.mcp.explain_metric_formula",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.metric_id": metric_id,
                "docubridge.doctype": doctype,
            },
        ) as span:
            active = self._catalog.get_active_ontology_revision()
            raw = (metric_id or "").strip()
            if not raw:
                raise KeyError("metric_id must be a non-empty string")

            glossary_by_id = {
                t.term_id: t
                for t in self._catalog.list_glossary_terms(include_confidential=True)
            }
            matched_term = glossary_by_id.get(raw)
            leaf_target = raw.split(".")[-1].strip().lower()

            canonical_bucket: str | None = None
            if leaf_target in _CANONICAL_METRIC_BUCKETS:
                canonical_bucket = leaf_target
            elif matched_term is not None:
                if matched_term.term_id == "term.premium":
                    canonical_bucket = "total_premium"
                elif matched_term.term_id == "term.deductible_to_limit_ratio":
                    canonical_bucket = "deductible_to_limit_pct"
                elif matched_term.term_id == "term.loan_to_value_ratio":
                    canonical_bucket = "total_coverage_limit"
                else:
                    canonical_bucket = matched_term.term_id
            else:
                bucket_from_field, _ = self._catalog.resolve_metric_bucket(
                    leaf_target, doctype=doctype
                )
                if bucket_from_field is not None:
                    canonical_bucket = bucket_from_field

            if canonical_bucket is None:
                span.set_attribute("docubridge.metric_found", False)
                raise KeyError(
                    f"Uncertified metric or glossary term '{metric_id}' in active "
                    f"revision '{active.revision_id}'."
                )

            default_op, default_term_id = _CANONICAL_METRIC_BUCKETS.get(
                canonical_bucket,
                ("BITEMPORAL_AS_OF_LOOKUP", raw if matched_term else ""),
            )
            if matched_term is None and default_term_id:
                matched_term = glossary_by_id.get(default_term_id)

            target_entity_id: str | None = None
            if doctype:
                ent_resp = self.get_entity_ontology(doctype)
                target_entity_id = ent_resp.entity_id

            buckets_to_match = {canonical_bucket}
            if canonical_bucket == "deductible_to_limit_pct":
                buckets_to_match.update(
                    {
                        "total_deductible",
                        "total_coverage_limit",
                        "total_insured_value",
                    }
                )

            feeding_edges: list[dict[str, Any]] = []
            for edge in active.edges:
                if edge.relation != "FEEDS_METRIC":
                    continue
                if target_entity_id and edge.source_id != target_entity_id:
                    continue
                edge_bucket = edge.target_id.split(".")[-1].strip().lower()
                if edge_bucket in buckets_to_match:
                    feeding_edges.append(edge.model_dump(mode="json"))

            feeding_fields: list[dict[str, Any]] = []
            seen_pairs: set[tuple[str, str]] = set()
            for spec in active.entities.values():
                if target_entity_id and spec.entity_id != target_entity_id:
                    continue
                for fname, fspec in spec.fields.items():
                    f_bucket = (fspec.feeds_metric or "").strip().lower()
                    if f_bucket in buckets_to_match:
                        _, edge_id = self._catalog.resolve_metric_bucket(
                            fname, doctype=spec.entity_id
                        )
                        pair = (spec.entity_id, fname)
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            feeding_fields.append(
                                {
                                    "entity_id": spec.entity_id,
                                    "field_name": fname,
                                    "feeds_metric": fspec.feeds_metric,
                                    "monetary": fspec.monetary,
                                    "source_priority": spec.source_priority,
                                    "edge_id": edge_id,
                                }
                            )

            metric_op = default_op
            if feeding_edges and feeding_edges[0].get("metric_operation"):
                metric_op = str(feeding_edges[0]["metric_operation"])

            span.set_attribute("docubridge.metric_found", True)
            span.set_attribute("docubridge.canonical_bucket", canonical_bucket)
            return MetricFormulaExplanation(
                revision_id=active.revision_id,
                metric_id=raw,
                canonical_bucket=canonical_bucket,
                metric_operation=metric_op,
                glossary_term_id=(
                    matched_term.term_id if matched_term is not None else None
                ),
                display_name=(
                    matched_term.display_name if matched_term is not None else None
                ),
                calculation_formula=(
                    matched_term.calculation_formula
                    if matched_term is not None
                    else f"SUM(active_facts.{canonical_bucket})"
                ),
                feeding_edges=tuple(feeding_edges),
                feeding_fields=tuple(feeding_fields),
                requires_bitemporal_watermark=True,
                requires_supersedes_precedence=True,
            )

    def check_doctype_precedence(
        self,
        doctype_a: str,
        doctype_b: str,
        *,
        field_name: str | None = None,
    ) -> DoctypePrecedenceVerdict:
        """Compare consolidation priority between ``doctype_a`` and ``doctype_b``."""
        with trace_step(
            "docubridge.catalog.mcp.check_doctype_precedence",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doctype_a": doctype_a,
                "docubridge.doctype_b": doctype_b,
                "docubridge.field_name": field_name,
            },
        ) as span:
            active = self._catalog.get_active_ontology_revision()
            prio_a, edge_a = self._catalog.resolve_doctype_priority(doctype_a)
            prio_b, edge_b = self._catalog.resolve_doctype_priority(doctype_b)

            resolved_a: str | None = None
            resolved_b: str | None = None
            try:
                resolved_a = self.get_entity_ontology(doctype_a).entity_id
            except KeyError:
                resolved_a = None
            try:
                resolved_b = self.get_entity_ontology(doctype_b).entity_id
            except KeyError:
                resolved_b = None

            direct_edge_id: str | None = None
            leaf = (field_name or "").split(".")[-1].strip().lower()
            if resolved_a and resolved_b:
                for edge in active.edges:
                    if edge.relation not in ("SUPERSEDES", "SPLIT_FROM"):
                        continue
                    pair_matches = (
                        edge.source_id == resolved_a and edge.target_id == resolved_b
                    ) or (edge.source_id == resolved_b and edge.target_id == resolved_a)
                    if not pair_matches:
                        continue
                    if (
                        leaf
                        and edge.on_fields
                        and leaf not in (f.lower() for f in edge.on_fields)
                    ):
                        continue
                    direct_edge_id = edge.edge_id
                    break

            if prio_a > prio_b:
                winner: str | None = doctype_a
                explanation = (
                    f"'{doctype_a}' (priority {prio_a}) supersedes '{doctype_b}' "
                    f"(priority {prio_b}) under active revision '{active.revision_id}'."
                )
            elif prio_b > prio_a:
                winner = doctype_b
                explanation = (
                    f"'{doctype_b}' (priority {prio_b}) supersedes '{doctype_a}' "
                    f"(priority {prio_a}) under active revision '{active.revision_id}'."
                )
            else:
                winner = None
                explanation = (
                    f"'{doctype_a}' and '{doctype_b}' share equal authority tier "
                    f"(priority {prio_a}); winner resolves deterministically by "
                    f"(effective_from DESC, recorded_at DESC, version DESC)."
                )

            span.set_attribute("docubridge.winning_doctype", winner or "tie")
            return DoctypePrecedenceVerdict(
                revision_id=active.revision_id,
                doctype_a=doctype_a,
                resolved_entity_a=resolved_a,
                priority_a=prio_a,
                edge_id_a=edge_a,
                doctype_b=doctype_b,
                resolved_entity_b=resolved_b,
                priority_b=prio_b,
                edge_id_b=edge_b,
                winning_doctype=winner,
                direct_supersedes_edge_id=direct_edge_id,
                explanation=explanation,
            )

    def trace_field_lineage(self, entity_id: str, field_name: str) -> dict[str, Any]:
        """Trace W3C PROV-O field lineage for ``(entity_id, field_name)``."""
        active = self._catalog.get_active_ontology_revision()
        trace = self._catalog.trace_lineage(entity_id, field_name)
        if trace is None:
            return {
                "found": False,
                "entity_id": entity_id,
                "field_name": field_name,
                "active_ontology_revision_id": active.revision_id,
                "trace": None,
            }
        return {
            "found": True,
            "entity_id": entity_id,
            "field_name": field_name,
            "active_ontology_revision_id": active.revision_id,
            "trace": trace.model_dump(mode="json"),
        }

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch an allow-listed read-only MCP tool call; reject any mutation."""
        cleaned = (tool_name or "").strip()
        if cleaned in ONTOLOGY_MCP_FORBIDDEN_MUTATION_TOOLS:
            raise PermissionError(
                f"MCP tool '{cleaned}' is a state-changing ontology mutation and is "
                f"forbidden on the read-only Zone 6 MCP surface. Use the Assay-Gated "
                f"Schema Refinery admin control plane instead."
            )
        if cleaned not in ONTOLOGY_MCP_ALLOWED_TOOLS:
            raise PermissionError(
                f"MCP tool '{cleaned}' is not allow-listed on the Zone 6 Ontology MCP "
                f"surface (allowed: {', '.join(ONTOLOGY_MCP_ALLOWED_TOOLS)})."
            )

        args = dict(arguments or {})
        if cleaned == "get_entity_ontology":
            doctype = str(args.get("doctype") or args.get("entity_id") or "")
            return self.get_entity_ontology(doctype).model_dump(mode="json")
        if cleaned == "explain_metric_formula":
            metric_id = str(args.get("metric_id") or args.get("metric_bucket") or "")
            doctype_arg = args.get("doctype")
            return self.explain_metric_formula(
                metric_id,
                doctype=str(doctype_arg) if doctype_arg else None,
            ).model_dump(mode="json")
        if cleaned == "check_doctype_precedence":
            doctype_a = str(args.get("doctype_a") or "")
            doctype_b = str(args.get("doctype_b") or "")
            field_arg = args.get("field_name")
            return self.check_doctype_precedence(
                doctype_a,
                doctype_b,
                field_name=str(field_arg) if field_arg else None,
            ).model_dump(mode="json")
        entity_id = str(args.get("entity_id") or "")
        field_name = str(args.get("field_name") or args.get("field_path") or "")
        return self.trace_field_lineage(entity_id, field_name)
