"""Shared Ontology Graph state, resolution helpers, and MCP/query-validator mixin for Knowledge Catalog."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, cast

from contract.refinery import (
    OntologyEdge,
    OntologyEntitySpec,
    OntologyRevision,
    OntologyRevisionState,
)

if TYPE_CHECKING:
    from bridge.knowledge_catalog.catalog import KnowledgeCatalog
    from bridge.knowledge_catalog.mcp_surface import (
        DoctypePrecedenceVerdict,
        EntityOntologyResponse,
        MetricFormulaExplanation,
        OntologyMcpSurface,
    )
    from bridge.knowledge_catalog.query_validator import MetricQueryValidationResult

_SHARED_ONTOLOGY_REVISIONS: list[OntologyRevision] = []
_ACTIVE_ONTOLOGY_REVISION: OntologyRevision | None = None


def _ensure_shared_ontology() -> tuple[list[OntologyRevision], OntologyRevision]:
    """Ensure the shared baseline OntologyRevision is loaded from OKF bundles."""
    global _ACTIVE_ONTOLOGY_REVISION
    from bridge.refinery.okf_loader import (
        BASELINE_REVISION_ID,
        EMPTY_REVISION_ID,
        build_baseline_ontology_revision,
        is_empty_catalog_mode,
    )

    expected_initial_id = (
        EMPTY_REVISION_ID if is_empty_catalog_mode() else BASELINE_REVISION_ID
    )
    if (
        _ACTIVE_ONTOLOGY_REVISION is None
        or not _SHARED_ONTOLOGY_REVISIONS
        or (
            len(_SHARED_ONTOLOGY_REVISIONS) == 1
            and _SHARED_ONTOLOGY_REVISIONS[0].revision_id
            in (BASELINE_REVISION_ID, EMPTY_REVISION_ID)
            and _SHARED_ONTOLOGY_REVISIONS[0].revision_id != expected_initial_id
        )
    ):
        baseline = build_baseline_ontology_revision()
        _SHARED_ONTOLOGY_REVISIONS.clear()
        _SHARED_ONTOLOGY_REVISIONS.append(baseline)
        _ACTIVE_ONTOLOGY_REVISION = baseline
    return _SHARED_ONTOLOGY_REVISIONS, _ACTIVE_ONTOLOGY_REVISION


def _normalize_field_lookup_key(field_name_or_path: str) -> str:
    """Normalize a bare field name or dotted path to its bare leaf + full path."""
    return field_name_or_path.strip()


class OntologyCatalogMixin:
    """Mixin implementing Ontology Graph versioning, resolution, MCP surface, and query validation."""

    def get_active_ontology_revision(self) -> OntologyRevision:
        """Return the currently active OntologyRevision in Zone 6."""
        _, active = _ensure_shared_ontology()
        return active

    def list_ontology_revisions(self) -> list[OntologyRevision]:
        """Return all registered OntologyRevision snapshots in chronological order."""
        revs, _ = _ensure_shared_ontology()
        return list(revs)

    def register_ontology_revision(
        self,
        revision: OntologyRevision,
        *,
        activate: bool = True,
    ) -> OntologyRevision:
        """Register a new OntologyRevision snapshot and optionally promote it to ACTIVE."""
        global _ACTIVE_ONTOLOGY_REVISION
        revs, _ = _ensure_shared_ontology()
        updated: list[OntologyRevision] = []
        replaced = False
        for existing in revs:
            if existing.revision_id == revision.revision_id:
                updated.append(revision)
                replaced = True
            elif activate and existing.state == OntologyRevisionState.ACTIVE:
                updated.append(
                    existing.model_copy(
                        update={
                            "state": OntologyRevisionState.DEPRECATED,
                            "effective_to": revision.effective_from,
                        }
                    )
                )
            else:
                updated.append(existing)
        if not replaced:
            target_rev = (
                revision.model_copy(update={"state": OntologyRevisionState.ACTIVE})
                if activate
                else revision
            )
            updated.append(target_rev)
            revision = target_rev
        _SHARED_ONTOLOGY_REVISIONS.clear()
        _SHARED_ONTOLOGY_REVISIONS.extend(updated)
        if activate:
            _ACTIVE_ONTOLOGY_REVISION = revision
        return revision

    def reset_ontology_revisions(self) -> OntologyRevision:
        """Reset the Zone 6 Ontology Graph back to the baseline revision."""
        global _ACTIVE_ONTOLOGY_REVISION
        from bridge.refinery.okf_loader import build_baseline_ontology_revision

        baseline = build_baseline_ontology_revision()
        _SHARED_ONTOLOGY_REVISIONS.clear()
        _SHARED_ONTOLOGY_REVISIONS.append(baseline)
        _ACTIVE_ONTOLOGY_REVISION = baseline
        return baseline

    def list_ontology_edges(
        self,
        *,
        relation: str | None = None,
        source_id: str | None = None,
    ) -> list[OntologyEdge]:
        """Return OntologyEdge records from the active OntologyRevision."""
        active = self.get_active_ontology_revision()
        edges = list(active.edges)
        if relation is not None:
            edges = [e for e in edges if e.relation == relation]
        if source_id is not None:
            edges = [e for e in edges if e.source_id == source_id]
        return edges

    def _match_entity_spec(self, doctype: str) -> OntologyEntitySpec | None:
        """Match a doctype string against active OntologyEntitySpec IDs and aliases."""
        norm = (doctype or "").strip().lower()
        if not norm:
            return None
        active = self.get_active_ontology_revision()
        if norm in active.entities:
            return active.entities[norm]
        for spec in active.entities.values():
            if norm in (a.lower() for a in spec.aliases):
                return spec
        # Substring fallback ordered by longest matching token first
        candidates: list[tuple[int, int, OntologyEntitySpec]] = []
        for spec in active.entities.values():
            tokens = (spec.entity_id.lower(), *(a.lower() for a in spec.aliases))
            for tok in tokens:
                if tok and tok in norm:
                    candidates.append((len(tok), spec.source_priority, spec))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][2]
        return None

    def resolve_doctype_priority(self, doctype: str) -> tuple[int, str | None]:
        """Resolve (source_priority, consolidation_edge_id) for a doctype from the Ontology Graph."""
        spec = self._match_entity_spec(doctype)
        if spec is None:
            return 10, None
        active = self.get_active_ontology_revision()
        for edge in active.edges:
            if edge.source_id == spec.entity_id and edge.relation in (
                "SUPERSEDES",
                "SPLIT_FROM",
            ):
                prio = (
                    edge.priority_rank
                    if edge.priority_rank is not None
                    else spec.source_priority
                )
                return prio, edge.edge_id
        return (
            spec.source_priority,
            f"ontology:{spec.entity_id}:priority-{spec.source_priority}",
        )

    def resolve_metric_bucket(
        self,
        field_name: str,
        doctype: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Resolve (metric_bucket, consolidation_edge_id) via [:FEEDS_METRIC] edges."""
        leaf = (field_name or "").split(".")[-1].strip().lower()
        if not leaf:
            return None, None
        active = self.get_active_ontology_revision()
        matched_spec = self._match_entity_spec(doctype) if doctype else None
        if matched_spec is not None:
            for edge in active.edges:
                if (
                    edge.relation == "FEEDS_METRIC"
                    and edge.source_id == matched_spec.entity_id
                    and leaf in (f.lower() for f in edge.on_fields)
                ):
                    return edge.target_id.split(".")[-1], edge.edge_id
            fspec = matched_spec.fields.get(leaf)
            if fspec and fspec.feeds_metric:
                return fspec.feeds_metric, f"ontology:{matched_spec.entity_id}:{leaf}"
        for edge in active.edges:
            if edge.relation == "FEEDS_METRIC" and leaf in (
                f.lower() for f in edge.on_fields
            ):
                return edge.target_id.split(".")[-1], edge.edge_id
        for spec in active.entities.values():
            fspec = spec.fields.get(leaf)
            if fspec and fspec.feeds_metric:
                return fspec.feeds_metric, f"ontology:{spec.entity_id}:{leaf}"
        return None, None

    def is_monetary_field(self, field_name: str) -> bool:
        """Return True if the active Ontology Graph declares the field as monetary."""
        leaf = (field_name or "").split(".")[-1].strip().lower()
        if not leaf:
            return False
        active = self.get_active_ontology_revision()
        for spec in active.entities.values():
            fspec = spec.fields.get(leaf)
            if fspec is not None and fspec.monetary:
                return True
        return False

    def mcp_surface(self) -> OntologyMcpSurface:
        """Return the read-only allow-listed Ontology MCP surface over this catalog."""
        from bridge.knowledge_catalog.mcp_surface import OntologyMcpSurface

        return OntologyMcpSurface(cast("KnowledgeCatalog", self))

    def get_entity_ontology(self, doctype: str) -> EntityOntologyResponse:
        """Return the active ontology specification and edges for ``doctype``."""
        return self.mcp_surface().get_entity_ontology(doctype)

    def explain_metric_formula(
        self,
        metric_id: str,
        *,
        doctype: str | None = None,
    ) -> MetricFormulaExplanation:
        """Explain certified ``[:FEEDS_METRIC]`` edges and formulas for ``metric_id``."""
        return self.mcp_surface().explain_metric_formula(metric_id, doctype=doctype)

    def check_doctype_precedence(
        self,
        doctype_a: str,
        doctype_b: str,
        *,
        field_name: str | None = None,
    ) -> DoctypePrecedenceVerdict:
        """Compare consolidation priority between ``doctype_a`` and ``doctype_b``."""
        return self.mcp_surface().check_doctype_precedence(
            doctype_a, doctype_b, field_name=field_name
        )

    def validate_metric_query(  # noqa: PLR0913 - keyword-only ontology query validator API
        self,
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
        raise_on_invalid: bool = False,
    ) -> MetricQueryValidationResult:
        """Validate a metric rollup or SQL query against certified ontology formulas."""
        from bridge.knowledge_catalog.query_validator import validate_metric_query

        return validate_metric_query(
            metric_bucket,
            fields=fields,
            operation=operation,
            sql=sql,
            learned_as_of=learned_as_of,
            effective_on=effective_on,
            require_bitemporal_watermark=require_bitemporal_watermark,
            include_supersedes_precedence=include_supersedes_precedence,
            doctype=doctype,
            catalog=cast("KnowledgeCatalog", self),
            raise_on_invalid=raise_on_invalid,
        )

    def validate_consolidation_view(  # noqa: PLR0913 - keyword-only consolidation view validator API
        self,
        view_name: str,
        *,
        sql: str | None = None,
        learned_as_of: datetime | str | None = None,
        effective_on: datetime | str | None = None,
        require_watermarks: bool = False,
        raise_on_invalid: bool = False,
    ) -> MetricQueryValidationResult:
        """Validate a Zone 4 / Zone 5 business view definition and watermarks."""
        from bridge.knowledge_catalog.query_validator import validate_consolidation_view

        return validate_consolidation_view(
            view_name,
            sql=sql,
            learned_as_of=learned_as_of,
            effective_on=effective_on,
            require_watermarks=require_watermarks,
            catalog=cast("KnowledgeCatalog", self),
            raise_on_invalid=raise_on_invalid,
        )


__all__ = [
    "_ACTIVE_ONTOLOGY_REVISION",
    "_SHARED_ONTOLOGY_REVISIONS",
    "OntologyCatalogMixin",
    "_ensure_shared_ontology",
    "_normalize_field_lookup_key",
]
