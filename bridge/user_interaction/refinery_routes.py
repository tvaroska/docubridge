"""Zone 5 & Zone 6 FastAPI Routes for the Schema Refinery & Ontology Graph."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bridge.a2ui_inbox import ActionInboxStore
from bridge.knowledge_catalog import KnowledgeCatalog
from bridge.refinery import SchemaRefineryEngine, schema_refinery_from_env
from bridge.user_interaction.schemas import (
    PromoteRefineryProposalRequest,
    RunRefineryAssayRequest,
)


def register_refinery_routes(
    router: APIRouter,
    *,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore | None = None,
    schema_refinery: SchemaRefineryEngine | None = None,
) -> None:
    """Mount ``/v1/refinery/*`` routes onto the Zone 5 API router."""
    refinery = schema_refinery_from_env(
        schema_refinery,
        knowledge_catalog=catalog,
    )

    @router.get("/v1/refinery/revisions")
    def get_refinery_revisions(
        domain: str | None = Query(default=None),
        relation: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """List active and historical OntologyRevision snapshots, edges, and proposals."""
        active = refinery.get_active_revision()
        edges = list(active.edges)
        if relation is not None:
            edges = [e for e in edges if e.relation == relation]
        proposals = refinery.list_proposals(domain=domain)
        reports = refinery.list_assay_reports()
        return {
            "active_revision": active.model_dump(mode="json"),
            "revisions": [r.model_dump(mode="json") for r in refinery.list_revisions()],
            "edges": [e.model_dump(mode="json") for e in edges],
            "proposals": [p.model_dump(mode="json") for p in proposals],
            "assay_reports": [rep.model_dump(mode="json") for rep in reports],
        }

    @router.post("/v1/refinery/assay")
    def post_refinery_assay(body: RunRefineryAssayRequest) -> dict[str, Any]:
        """Run bimodal field sparsity detection & the 3-Tier Refinery Assay Gate."""
        cluster_a = set(body.cluster_a_fields) if body.cluster_a_fields else None
        cluster_b = set(body.cluster_b_fields) if body.cluster_b_fields else None
        proposal, report, candidate_rev = refinery.run_taxonomy_assay(
            domain=body.domain,
            target_entity_id=body.target_entity_id,
            cluster_a_fields=cluster_a,
            cluster_b_fields=cluster_b,
        )
        return {
            "proposal": proposal.model_dump(mode="json"),
            "assay_report": report.model_dump(mode="json"),
            "candidate_revision": candidate_rev.model_dump(mode="json"),
        }

    @router.post("/v1/refinery/promote")
    def post_refinery_promote(body: PromoteRefineryProposalRequest) -> dict[str, Any]:
        """Promote an assay-passed proposal into a new active OntologyRevision."""
        try:
            promoted_rev = refinery.promote_proposal(
                body.proposal_id,
                revision_id=body.revision_id,
                version=body.version,
                action_inbox=inbox,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updated_prop = refinery.get_proposal(body.proposal_id)
        return {
            "promoted_revision": promoted_rev.model_dump(mode="json"),
            "proposal": (
                updated_prop.model_dump(mode="json")
                if updated_prop is not None
                else None
            ),
            "revisions": [r.model_dump(mode="json") for r in refinery.list_revisions()],
        }

    @router.post("/v1/refinery/reset")
    def post_refinery_reset() -> dict[str, Any]:
        """Reset Schema Refinery proposals and Zone 6 Ontology Graph back to v1.1 baseline."""
        baseline = refinery.reset()
        return {
            "status": "reset",
            "active_revision": baseline.model_dump(mode="json"),
        }
