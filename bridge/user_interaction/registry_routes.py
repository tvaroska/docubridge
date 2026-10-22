"""Zone 6 Knowledge Catalog, Agent Registry, Skill Registry, and Agent Gateway routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from bridge.agent_gym import AgentGymEngine
from bridge.knowledge_catalog import CatalogRole, KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.user_interaction.auth import (
    AuthenticationRequiredError,
    resolve_compound_identity,
)
from bridge.user_interaction.schemas import (
    CreateBindingRequest,
    EvaluateGatewayRouteRequest,
    RegisterServiceRequest,
)


def register_registry_and_catalog_routes(
    router: APIRouter,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    gym: AgentGymEngine,
    principal_roles: dict[str, CatalogRole] | None,
    require_auth: bool,
) -> None:
    """Register Agent Registry, Skill Registry, Agent Gateway, and Zone 6 Knowledge Catalog routes."""

    @router.get("/v1/registry/catalog")
    def get_registry_catalog_endpoint() -> dict[str, Any]:
        """Return the unified Gemini platform Agent Registry, Skill Registry, and Agent Gateway catalog."""
        return gym.get_registry_catalog()

    @router.get("/v1/registry/agents")
    def list_registry_agents_endpoint() -> list[dict[str, Any]]:
        """List all registered agents in the Gemini platform Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["agents"]

    @router.get("/v1/registry/mcp-servers")
    def list_registry_mcp_servers_endpoint() -> list[dict[str, Any]]:
        """List all registered MCP servers and tool specs in the Gemini platform Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["mcp_servers"]

    @router.get("/v1/registry/endpoints")
    def list_registry_endpoints_endpoint() -> list[dict[str, Any]]:
        """List all registered service endpoints in the Gemini platform Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["endpoints"]

    @router.get("/v1/registry/skills")
    def list_registry_skills_endpoint() -> dict[str, Any]:
        """Return the Gemini platform Skill Registry catalog and active skill containers."""
        return gym.get_registry_catalog()["skill_registry"]

    @router.get("/v1/registry/bindings")
    def list_registry_bindings_endpoint() -> list[dict[str, Any]]:
        """List all explicit source-to-target authentication bindings in the Agent Registry."""
        return gym.get_registry_catalog()["agent_registry"]["bindings"]

    @router.post("/v1/registry/services")
    def register_registry_service_endpoint(
        request: Request,
        body: RegisterServiceRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Register a writable Service resource in Agent Registry projecting onto AGENT, MCP_SERVER, or ENDPOINT."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot register Agent Registry services."
                )
            return gym.register_registry_service(
                service_id=body.service_id,
                display_name=body.display_name,
                component_type=body.component_type,
                spec_type=body.spec_type,
                target_uri=body.target_uri,
                location=body.location,
                agent_principal=body.agent_principal,
                tools=body.tools,
                data_resources=body.data_resources,
            )
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/registry/bindings")
    def create_registry_binding_endpoint(
        request: Request,
        body: CreateBindingRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Create an explicit Agent Registry Binding enforcing hierarchical resource names over URNs."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot create Agent Registry bindings."
                )
            return gym.create_registry_binding(
                binding_id=body.binding_id,
                source_agent=body.source_agent,
                target_resource=body.target_resource,
                target_type=body.target_type,
                auth_provider=body.auth_provider,
                delegation_mode=body.delegation_mode,
                location=body.location,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/gateway/status")
    def get_gateway_status_endpoint() -> dict[str, Any]:
        """Return the Gemini platform Agent Gateway and IAM v3 Unified Access Policy control-plane status."""
        return gym.get_gateway_catalog()

    @router.post("/v1/gateway/evaluate-route")
    def evaluate_gateway_route_endpoint(
        body: EvaluateGatewayRouteRequest,
    ) -> dict[str, Any]:
        """Evaluate a CLIENT_TO_AGENT or AGENT_TO_ANYWHERE route through Model Armor, Cloud DLP, and IAM v3 UAP CEL."""
        return gym.evaluate_gateway_route(
            governed_access_path=body.governed_access_path,
            source_principal=body.source_principal,
            target_resource=body.target_resource,
            protocol=body.protocol,
            mcp_tool_name=body.mcp_tool_name,
            payload_text=body.payload_text,
        )

    @router.get("/v1/registry/lineage/{entity_or_context_id}")
    def get_process_skill_lineage_endpoint(
        entity_or_context_id: str,
    ) -> dict[str, Any]:
        """Return bi-temporal process-instance and consolidated-fact skill revision lineage for an entity or context."""
        by_entity = gym.list_process_skill_lineage(entity_id=entity_or_context_id)
        by_context = gym.list_process_skill_lineage(context_id=entity_or_context_id)
        seen_ids: set[str] = set()
        merged = []
        for rec in [*by_entity, *by_context]:
            if rec.lineage_id not in seen_ids:
                seen_ids.add(rec.lineage_id)
                merged.append(rec)
        merged.sort(key=lambda r: (r.recorded_at, r.document_version))

        facts, _ = store.query_as_of(entity_or_context_id)
        fact_skill_lineage = [
            {
                "fact_id": f.fact_id,
                "field_path": f.field_path,
                "value": f.field_value,
                "doc_id": f.doc_id,
                "doctype": f.doctype,
                "document_version": f.version,
                "skill_version": f.skill_version,
                "skill_revision_id": f.skill_revision_id,
                "skill_urn": f.skill_urn,
                "process_skill_revision_id": f.process_skill_revision_id,
                "compiled_rule_ids": list(f.compiled_rule_ids),
                "recorded_at": f.recorded_at.isoformat(),
                "effective_from": f.effective_from.isoformat(),
            }
            for f in facts
        ]
        return {
            "query_id": entity_or_context_id,
            "process_skill_executions": [r.model_dump(mode="json") for r in merged],
            "consolidated_fact_skill_lineage": fact_skill_lineage,
        }

    @router.get("/v1/catalog/spiffe-agents")
    def get_catalog_spiffe_agents(
        security_zone: Literal["internal", "external"] | None = None,
    ) -> list[dict[str, Any]]:
        """List registered SPIFFE agent identities and least-privilege policies from Zone 6 Knowledge Catalog."""
        return [
            p.model_dump(mode="json")
            for p in catalog.list_spiffe_agents(security_zone=security_zone)
        ]

    @router.get("/v1/catalog/assets")
    def get_catalog_assets(
        external_safe_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List governed data assets registered across the 6 architecture zones."""
        return [
            a.model_dump(mode="json")
            for a in catalog.list_assets(external_safe_only=external_safe_only)
        ]

    @router.get("/v1/catalog/glossary")
    def get_catalog_glossary(
        domain: str | None = None,
        include_confidential: bool = True,
    ) -> list[dict[str, Any]]:
        """List business glossary terms and deterministic quality rules from Zone 6 Knowledge Catalog."""
        return [
            t.model_dump(mode="json")
            for t in catalog.list_glossary_terms(
                domain=domain, include_confidential=include_confidential
            )
        ]

    @router.get("/v1/catalog/lineage/{entity_id}/{field_name}")
    def get_field_lineage(
        entity_id: str,
        field_name: str,
    ) -> dict[str, Any]:
        """Trace 6-hop end-to-end field lineage from user view back to the source PDF page and WORM vault."""
        trace = catalog.trace_lineage(entity_id, field_name)
        if trace is None:
            raise HTTPException(
                status_code=404,
                detail=f"No lineage found for {entity_id}.{field_name}",
            )
        return trace.model_dump(mode="json")
