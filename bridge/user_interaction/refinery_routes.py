"""Zone 5 & Zone 6 FastAPI Routes for the Schema Refinery & Ontology Graph."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.params import Depends

from bridge.a2ui_inbox import ActionInboxStore
from bridge.knowledge_catalog import CatalogRole, KnowledgeCatalog
from bridge.refinery import (
    SchemaRefineryEngine,
    UseCaseOnboardingManifest,
    onboard_use_case,
    schema_refinery_from_env,
)
from bridge.user_interaction.demo_session import (
    mutating_auth_dependency,
    read_auth_dependency,
)
from bridge.user_interaction.schemas import (
    OnboardDoctypeRequest,
    PromoteRefineryProposalRequest,
    RunRefineryAssayRequest,
)


def _register_refinery_write_routes(
    router: APIRouter,
    refinery: SchemaRefineryEngine,
    inbox: ActionInboxStore | None,
    mutating_deps: Sequence[Depends],
) -> None:
    @router.post("/v1/refinery/assay", dependencies=mutating_deps)
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

    @router.post("/v1/refinery/onboard", dependencies=mutating_deps)
    def post_refinery_onboard(body: OnboardDoctypeRequest) -> dict[str, Any]:
        """Induce a candidate DRAFT doctype + `.okf.md` bundle from 1-5 examples and run the 3-Tier Assay Gate."""
        try:
            result = refinery.onboard_doctype_from_examples(
                entity_id=body.entity_id,
                samples=body.samples,
                paired_target_examples=body.paired_target_examples,
                spine_entity_id=body.spine_entity_id,
                context_set=body.context_set,
                domain=body.domain,
                source_priority=body.source_priority,
                supersedes_spine=body.supersedes_spine,
                aliases=body.aliases,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post("/v1/refinery/onboard-use-case", dependencies=mutating_deps)
    def post_refinery_onboard_use_case(
        body: UseCaseOnboardingManifest,
    ) -> dict[str, Any]:
        """Onboard a complete use-case agent (all its doctypes + process skill) onto DocuBridge."""
        try:
            result = onboard_use_case(body, refinery=refinery)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post("/v1/refinery/promote", dependencies=mutating_deps)
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

    @router.post("/v1/refinery/reset", dependencies=mutating_deps)
    def post_refinery_reset() -> dict[str, Any]:
        """Reset Schema Refinery proposals and Zone 6 Ontology Graph back to v1.1 baseline."""
        baseline = refinery.reset()
        return {
            "status": "reset",
            "active_revision": baseline.model_dump(mode="json"),
        }


def register_refinery_routes(  # noqa: PLR0913 - public FastAPI route registrar
    router: APIRouter,
    *,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore | None = None,
    schema_refinery: SchemaRefineryEngine | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> None:
    """Mount ``/v1/refinery/*`` routes onto the Zone 5 API router."""
    refinery = schema_refinery_from_env(
        schema_refinery,
        knowledge_catalog=catalog,
    )
    read_deps = [
        read_auth_dependency(
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=False,
        )
    ]
    mutating_deps = [
        mutating_auth_dependency(
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=False,
        )
    ]

    @router.get("/v1/refinery/revisions", dependencies=read_deps)
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

    _register_refinery_write_routes(router, refinery, inbox, mutating_deps)
