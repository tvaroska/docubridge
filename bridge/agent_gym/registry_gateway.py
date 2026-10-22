"""Gemini platform Agent Registry, Skill Registry & Agent Gateway control-plane mixin."""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any, Literal

from bridge.agent_gym.models import (
    REASONING_MODEL,
    WORKHORSE_MODEL,
    ProcessSkillLineageRecord,
    SkillRevisionRecord,
    _board_public_url,
    _board_service_name,
    _bridge_service_name,
    _gcp_project_id,
    _gcp_project_number,
    _reasoning_engine_id,
    _skills_bucket,
    _vault_bucket,
)
from bridge.telemetry import trace_step

if TYPE_CHECKING:
    pass


class RegistryGatewayMixin:
    """Mixin providing Agent Registry, Skill Registry, and Agent Gateway + CEL evaluation."""

    def list_skill_revisions(
        self, skill_name: str | None = None
    ) -> list[SkillRevisionRecord]:
        """List immutable SkillRevision snapshots (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def get_active_skill_revision(self, skill_name: str) -> SkillRevisionRecord:
        """Return the active defaultRevision for skill_name (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def list_process_skill_lineage(
        self,
        *,
        context_id: str | None = None,
        entity_id: str | None = None,
    ) -> list[ProcessSkillLineageRecord]:
        """Return chronological process-instance skill lineage records (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _save_registry_service(self, entry: dict[str, Any]) -> None:
        """Persist a custom Agent Registry service registration (implemented by AgentGymEngine)."""
        if not hasattr(self, "_custom_registry_services"):
            self._custom_registry_services: list[dict[str, Any]] = []
        self._custom_registry_services.append(entry)

    def _list_registry_services(self) -> list[dict[str, Any]]:
        """List custom Agent Registry services (implemented by AgentGymEngine)."""
        return list(getattr(self, "_custom_registry_services", []))

    def _save_registry_binding(self, binding: dict[str, Any]) -> None:
        """Persist a custom Agent Registry binding (implemented by AgentGymEngine)."""
        if not hasattr(self, "_custom_registry_bindings"):
            self._custom_registry_bindings: list[dict[str, Any]] = []
        self._custom_registry_bindings.append(binding)

    def _list_registry_bindings(self) -> list[dict[str, Any]]:
        """List custom Agent Registry bindings (implemented by AgentGymEngine)."""
        return list(getattr(self, "_custom_registry_bindings", []))

    def get_registry_catalog(self) -> dict[str, Any]:
        """Return the unified Gemini platform Agent Registry + Skill Registry catalog
        across all 4 component types (Agents, MCP Servers, Endpoints, Skills + SkillRevisions),
        Bindings, Publishers, and Process-Instance Skill Lineage."""
        from bridge.edge.card import build_agent_card
        from bridge.edge.skills import loaded_skills
        from bridge.identity import (
            DEFAULT_ROLE_APPROVAL_LIMITS,
            DEFAULT_SPIFFE_AGENT_POLICIES,
        )

        seam_skills = loaded_skills()
        card = build_agent_card(skills=seam_skills)
        all_revs = self.list_skill_revisions()
        revs_by_skill: dict[str, list[SkillRevisionRecord]] = {}
        for rev in all_revs:
            revs_by_skill.setdefault(rev.skill_name, []).append(rev)

        skill_containers: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for meta in seam_skills:
            seen_names.add(meta.name)
            active_rev = self.get_active_skill_revision(meta.name)
            history = revs_by_skill.get(meta.name, [])
            skill_containers.append(
                {
                    "skill_name": meta.name,
                    "kind": meta.kind,
                    "publisher": "private",
                    "description": meta.description,
                    "skill_urn": active_rev.skill_urn,
                    "resource_name": f"projects/{_gcp_project_number()}/locations/global/skills/private-{meta.name}",
                    "default_revision_id": active_rev.revision_id,
                    "active_version": active_rev.version,
                    "target_state": active_rev.target_state,
                    "gcs_source_uri": active_rev.gcs_source_uri,
                    "compiled_rule_ids": list(active_rev.compiled_rule_ids),
                    "revision_count": max(1, len(history)),
                    "revisions": [r.model_dump(mode="json") for r in history]
                    if history
                    else [active_rev.model_dump(mode="json")],
                }
            )
        for skill_name, history in sorted(revs_by_skill.items()):
            if skill_name in seen_names:
                continue
            active_rev = self.get_active_skill_revision(skill_name)
            skill_containers.append(
                {
                    "skill_name": skill_name,
                    "kind": "doctype",
                    "publisher": "private",
                    "description": f"Agent Gym compiled doctype skill for {skill_name}",
                    "skill_urn": active_rev.skill_urn,
                    "resource_name": f"projects/{_gcp_project_number()}/locations/global/skills/private-{skill_name}",
                    "default_revision_id": active_rev.revision_id,
                    "active_version": active_rev.version,
                    "target_state": active_rev.target_state,
                    "gcs_source_uri": active_rev.gcs_source_uri,
                    "compiled_rule_ids": list(active_rev.compiled_rule_ids),
                    "revision_count": len(history),
                    "revisions": [r.model_dump(mode="json") for r in history],
                }
            )

        a2a_skills_list = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": list(s.tags or []),
            }
            for s in (card.skills or [])
        ]

        agents_list: list[dict[str, Any]] = [
            {
                "agent_id": f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:aiplatform:reasoningEngines:{_reasoning_engine_id()}",
                "agent_urn": f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:aiplatform:reasoningEngines:{_reasoning_engine_id()}",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "display_name": f"{card.name} (Agent Engine Runtime)",
                "registration_mode": "AUTOMATIC",
                "spec_type": "a2a-agent-card",
                "protocol": "A2A/1.0",
                "agent_principal": f"principal://agents.global.org-docubridge.system.id.goog/resources/aiplatform/projects/{_gcp_project_number()}/locations/us-central1/reasoningEngines/{_reasoning_engine_id()}",
                "spiffe_id": "spiffe://agents.docubridge.io/internal/credit-officer-agent",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": f"//aiplatform.googleapis.com/projects/{_gcp_project_number()}/locations/us-central1/reasoningEngines/{_reasoning_engine_id()}",
                },
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "security_zone": "internal",
                "a2a_skills": a2a_skills_list,
            },
            {
                "agent_id": f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:run:services:{_bridge_service_name()}",
                "agent_urn": f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:run:services:{_bridge_service_name()}",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-{_bridge_service_name()}",
                "display_name": "DocuBridge A2A Edge Proxy (Cloud Run)",
                "registration_mode": "AUTOMATIC",
                "spec_type": "a2a-agent-card",
                "protocol": "A2A/1.0",
                "agent_principal": f"principal://agents.global.org-docubridge.system.id.goog/resources/run/projects/{_gcp_project_number()}/locations/us-central1/services/{_bridge_service_name()}",
                "spiffe_id": "spiffe://agents.docubridge.io/internal/broker-agent",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": f"//run.googleapis.com/projects/{_gcp_project_number()}/locations/us-central1/services/{_bridge_service_name()}",
                },
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "security_zone": "internal",
                "a2a_skills": a2a_skills_list,
            },
        ]

        for spiffe_id, pol in DEFAULT_SPIFFE_AGENT_POLICIES.items():
            short_slug = spiffe_id.rsplit("/", 1)[-1]
            primary_role = pol.allowed_roles[0] if pol.allowed_roles else "client"
            max_limit = str(DEFAULT_ROLE_APPROVAL_LIMITS.get(primary_role, "0.00"))
            agents_list.append(
                {
                    "agent_id": f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:{short_slug}",
                    "agent_urn": f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:{short_slug}",
                    "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-{short_slug}",
                    "display_name": pol.display_name,
                    "registration_mode": "MANUAL",
                    "spec_type": "a2a-agent-card",
                    "protocol": "A2A/1.0 + A2UI/0.9",
                    "agent_principal": spiffe_id,
                    "spiffe_id": spiffe_id,
                    "runtime_reference": {
                        "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                        "uri": f"//run.googleapis.com/projects/{_gcp_project_number()}/locations/us-central1/services/{_board_service_name()}",
                    },
                    "workhorse_model": WORKHORSE_MODEL,
                    "reasoning_model": REASONING_MODEL,
                    "security_zone": pol.security_zone,
                    "allowed_channels": list(pol.allowed_channels),
                    "allowed_roles": list(pol.allowed_roles),
                    "allowed_views": list(pol.allowed_views),
                    "allow_vault_promotion": pol.allow_vault_promotion,
                    "max_approval_amount": max_limit,
                    "a2a_skills": a2a_skills_list
                    if pol.security_zone == "internal"
                    else [],
                }
            )

        mcp_servers_list: list[dict[str, Any]] = [
            {
                "mcp_server_id": f"urn:mcp:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:knowledge-catalog-mcp",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-knowledge-catalog-mcp",
                "display_name": "Zone 6 Knowledge Catalog MCP Server",
                "registration_mode": "MANUAL",
                "spec_type": "tool-spec",
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": f"//run.googleapis.com/projects/{_gcp_project_number()}/locations/us-central1/services/{_board_service_name()}",
                },
                "tools": [
                    "search_knowledge_catalog",
                    "get_glossary_term",
                    "trace_field_lineage",
                    "authorize_spiffe_access",
                    "catalog.trace_lineage",
                    "catalog.lookup_glossary",
                    "catalog.check_quality_rules",
                ],
                "data_resources": [
                    "glossary_terms",
                    "catalog_assets",
                    "spiffe_agent_policies",
                ],
            },
            {
                "mcp_server_id": f"urn:mcp:googleapis.com:projects:{_gcp_project_number()}:locations:global:bigquery-mcp",
                "resource_name": f"projects/{_gcp_project_number()}/locations/global/mcpServers/agentregistry-bigquery-mcp",
                "display_name": "Google Cloud BigQuery & Open Iceberg MCP Server",
                "registration_mode": "AUTOMATIC",
                "spec_type": "tool-spec",
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": f"//bigquery.googleapis.com/projects/{_gcp_project_id()}/datasets/docubridge_lakehouse",
                },
                "tools": [
                    "execute_sql",
                    "get_table_schema",
                    "export_iceberg_watermark",
                    "query_otel_spans",
                    "bigquery.query_iceberg",
                    "bigquery.query_otel_spans",
                ],
                "data_resources": [
                    "docubridge_lakehouse.bq_operational_facts_iceberg",
                    "docubridge_telemetry.otel_spans",
                ],
            },
            {
                "mcp_server_id": f"urn:mcp:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:alloydb-operational-store-mcp",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-alloydb-operational-store-mcp",
                "display_name": "Zone 3 Bi-Temporal Operational Store MCP Server",
                "registration_mode": "MANUAL",
                "spec_type": "tool-spec",
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": f"//sqladmin.googleapis.com/projects/{_gcp_project_id()}/instances/bridge-sql",
                },
                "tools": [
                    "query_bitemporal_facts",
                    "compute_financial_summary",
                    "list_fact_conflicts",
                    "consolidate_entry",
                    "operational_store.query_bitemporal_facts",
                    "operational_store.compute_financial_summary",
                ],
                "data_resources": [
                    "operational_store.bitemporal_facts",
                    "v_broker_placement",
                    "v_underwriter_exposure",
                    "v_claims_coverage",
                ],
            },
        ]

        endpoints_list: list[dict[str, Any]] = [
            {
                "endpoint_id": f"urn:endpoint:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:docai-layout-ocr",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/endpoints/agentregistry-docai-layout-ocr",
                "display_name": "Document AI Layout & Bounding-Polygon OCR Endpoint",
                "registration_mode": "MANUAL",
                "spec_type": "no-spec",
                "target_uri": f"https://us-documentai.googleapis.com/v1/projects/{_gcp_project_number()}/locations/us/processors/docai-layout:process",
                "security_zone": "internal",
            },
            {
                "endpoint_id": f"urn:endpoint:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:audit-vault-worm",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/endpoints/agentregistry-audit-vault-worm",
                "display_name": "Zone 1 Cloud Storage 7-Year WORM Audit Vault Endpoint",
                "registration_mode": "MANUAL",
                "spec_type": "no-spec",
                "target_uri": f"https://storage.googleapis.com/storage/v1/b/{_vault_bucket()}",
                "security_zone": "internal",
            },
            {
                "endpoint_id": f"urn:endpoint:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:us-central1:agentregistry:services:a2ui-v09-inbox",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/endpoints/agentregistry-a2ui-v09-inbox",
                "display_name": "Zone 5 A2UI v0.9 Action Inbox & Governance Surface Endpoint",
                "registration_mode": "MANUAL",
                "spec_type": "no-spec",
                "target_uri": f"{_board_public_url()}/v1/a2ui/inbox/actions",
                "security_zone": "internal",
            },
        ]

        bindings_list: list[dict[str, Any]] = [
            {
                "binding_id": "binding-engine-to-knowledge-catalog-mcp",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/bindings/binding-engine-to-knowledge-catalog-mcp",
                "source_agent": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": f"projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-knowledge-catalog-mcp",
                "target_type": "MCP_SERVER",
                "auth_provider": "agent-identity-mtls-jwt-svid",
                "delegation_mode": "RFC8693_OBO_INTERSECTION",
            },
            {
                "binding_id": "binding-engine-to-operational-store-mcp",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/bindings/binding-engine-to-operational-store-mcp",
                "source_agent": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": f"projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-alloydb-operational-store-mcp",
                "target_type": "MCP_SERVER",
                "auth_provider": "agent-identity-mtls-jwt-svid",
                "delegation_mode": "RFC8693_OBO_INTERSECTION",
            },
            {
                "binding_id": "binding-engine-to-bigquery-mcp",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/bindings/binding-engine-to-bigquery-mcp",
                "source_agent": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": f"projects/{_gcp_project_number()}/locations/global/mcpServers/agentregistry-bigquery-mcp",
                "target_type": "MCP_SERVER",
                "auth_provider": "gcp-workload-identity",
                "delegation_mode": "2LO_SERVICE_AGENT",
            },
            {
                "binding_id": "binding-engine-to-docai-endpoint",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/bindings/binding-engine-to-docai-endpoint",
                "source_agent": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-document-bridge-engine",
                "target_resource": f"projects/{_gcp_project_number()}/locations/us-central1/endpoints/agentregistry-docai-layout-ocr",
                "target_type": "ENDPOINT",
                "auth_provider": "gcp-workload-identity",
                "delegation_mode": "2LO_SERVICE_AGENT",
            },
            {
                "binding_id": "binding-credit-officer-to-a2ui-inbox",
                "resource_name": f"projects/{_gcp_project_number()}/locations/us-central1/bindings/binding-credit-officer-to-a2ui-inbox",
                "source_agent": f"projects/{_gcp_project_number()}/locations/us-central1/agents/agentregistry-credit-officer-agent",
                "target_resource": f"projects/{_gcp_project_number()}/locations/us-central1/endpoints/agentregistry-a2ui-v09-inbox",
                "target_type": "ENDPOINT",
                "auth_provider": "agent-identity-mtls-jwt-svid",
                "delegation_mode": "RFC8693_OBO_INTERSECTION",
            },
        ]

        custom_services = self._list_registry_services()
        for svc in custom_services:
            kind = svc.get("component_type")
            if kind == "AGENT":
                agents_list.append(svc["projected"])
            elif kind == "MCP_SERVER":
                mcp_servers_list.append(svc["projected"])
            elif kind == "ENDPOINT":
                endpoints_list.append(svc["projected"])

        custom_bindings = self._list_registry_bindings()
        bindings_list.extend(custom_bindings)

        return {
            "agent_registry": {
                "project_id": f"{_gcp_project_id()}",
                "project_number": f"{_gcp_project_number()}",
                "location": "us-central1",
                "governance_scope": f"projects/{_gcp_project_number()}/locations/us-central1",
                "registry_api": f"https://agentregistry.googleapis.com/v1alpha/projects/{_gcp_project_id()}/locations/us-central1",
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "agent_count": len(agents_list),
                "mcp_server_count": len(mcp_servers_list),
                "endpoint_count": len(endpoints_list),
                "binding_count": len(bindings_list),
                "agents": agents_list,
                "mcp_servers": mcp_servers_list,
                "endpoints": endpoints_list,
                "bindings": bindings_list,
                "publishers": [
                    {
                        "publisher_id": "private",
                        "display_name": f"Organization Private Publisher ({_gcp_project_id()})",
                        "urn_prefix": f"urn:skill:projects-{_gcp_project_number()}:locations:global:private-",
                    },
                    {
                        "publisher_id": "cloud.google.com",
                        "display_name": "Google Cloud Official Skills",
                        "urn_prefix": "urn:skill:cloud.google.com:cloud:",
                    },
                    {
                        "publisher_id": "discoveryengine.googleapis.com",
                        "display_name": "Gemini Enterprise Built-in Skills",
                        "urn_prefix": "urn:skill:discoveryengine.googleapis.com:discoveryengine:",
                    },
                ],
            },
            "skill_registry": {
                "bucket": f"gs://{_skills_bucket()}",
                "registry_api": f"https://agentregistry.googleapis.com/v1alpha/projects/{_gcp_project_id()}/locations/global/skills",
                "service_agent": f"service-{_gcp_project_number()}@gcp-sa-agentregistry.iam.gserviceaccount.com",
                "skill_count": len(skill_containers),
                "skills": skill_containers,
            },
            "agent_gateway": self.get_gateway_catalog(),
            "process_skill_lineage": [
                r.model_dump(mode="json") for r in self.list_process_skill_lineage()
            ],
        }

    def register_registry_service(
        self,
        *,
        service_id: str,
        display_name: str,
        component_type: Literal["AGENT", "MCP_SERVER", "ENDPOINT"],
        spec_type: Literal["a2a-agent-card", "no-spec", "tool-spec"],
        target_uri: str,
        location: str = "us-central1",
        agent_principal: str | None = None,
        tools: list[str] | None = None,
        data_resources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Writable `Service` resource registration in Agent Registry (`POST .../services?serviceId=`),
        automatically projecting onto the read-only `Agent`, `McpServer`, or `Endpoint` collection."""
        service_name = f"projects/{_gcp_project_number()}/locations/{location}/services/{service_id}"
        if component_type == "AGENT":
            urn = f"urn:agent:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:{location}:agentregistry:services:{service_id}"
            reg_res = f"projects/{_gcp_project_number()}/locations/{location}/agents/agentregistry-{service_id}"
            projected = {
                "agent_id": urn,
                "agent_urn": urn,
                "resource_name": reg_res,
                "display_name": display_name,
                "registration_mode": "MANUAL",
                "spec_type": spec_type,
                "protocol": "A2A/1.0" if spec_type == "a2a-agent-card" else "REST",
                "agent_principal": agent_principal
                or f"spiffe://agents.docubridge.io/internal/{service_id}",
                "spiffe_id": agent_principal
                or f"spiffe://agents.docubridge.io/internal/{service_id}",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": target_uri,
                },
                "workhorse_model": WORKHORSE_MODEL,
                "reasoning_model": REASONING_MODEL,
                "security_zone": "internal",
                "a2a_skills": [],
            }
        elif component_type == "MCP_SERVER":
            urn = f"urn:mcp:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:{location}:agentregistry:services:{service_id}"
            reg_res = f"projects/{_gcp_project_number()}/locations/{location}/mcpServers/agentregistry-{service_id}"
            projected = {
                "mcp_server_id": urn,
                "resource_name": reg_res,
                "display_name": display_name,
                "registration_mode": "MANUAL",
                "spec_type": spec_type,
                "protocol": "MCP/2025-11",
                "runtime_reference": {
                    "attribute": "agentregistry.googleapis.com/system/RuntimeReference",
                    "uri": target_uri,
                },
                "tools": list(tools or []),
                "data_resources": list(data_resources or []),
            }
        else:
            urn = f"urn:endpoint:projects-{_gcp_project_number()}:projects:{_gcp_project_number()}:locations:{location}:agentregistry:services:{service_id}"
            reg_res = f"projects/{_gcp_project_number()}/locations/{location}/endpoints/agentregistry-{service_id}"
            projected = {
                "endpoint_id": urn,
                "resource_name": reg_res,
                "display_name": display_name,
                "registration_mode": "MANUAL",
                "spec_type": spec_type,
                "target_uri": target_uri,
                "security_zone": "internal",
            }
        entry = {
            "name": service_name,
            "service_id": service_id,
            "component_type": component_type,
            "spec_type": spec_type,
            "registry_resource": reg_res,
            "identifier_urn": urn,
            "projected": projected,
        }
        self._save_registry_service(entry)
        return entry

    def create_registry_binding(
        self,
        *,
        binding_id: str,
        source_agent: str,
        target_resource: str,
        target_type: Literal["AGENT", "MCP_SERVER", "ENDPOINT"] = "MCP_SERVER",
        auth_provider: str = "agent-identity-mtls-jwt-svid",
        delegation_mode: str = "RFC8693_OBO_INTERSECTION",
        location: str = "us-central1",
    ) -> dict[str, Any]:
        """Create an explicit Agent Registry `Binding` connecting `source_agent` to `target_resource`."""
        if source_agent.startswith("urn:"):
            raise ValueError(
                "NOT_FOUND: Requested entity was not found. Policy bindings and route rules must specify "
                "the hierarchical resource name (projects/.../locations/.../agents/agentregistry-...), not a URN."
            )
        if target_resource.startswith("urn:"):
            raise ValueError(
                "NOT_FOUND: Requested entity was not found. Policy bindings and route rules must specify "
                "the hierarchical resource name (projects/.../locations/.../.../agentregistry-...), not a URN."
            )
        binding = {
            "binding_id": binding_id,
            "resource_name": f"projects/{_gcp_project_number()}/locations/{location}/bindings/{binding_id}",
            "source_agent": source_agent,
            "target_resource": target_resource,
            "target_type": target_type,
            "auth_provider": auth_provider,
            "delegation_mode": delegation_mode,
        }
        self._save_registry_binding(binding)
        return binding

    def get_gateway_catalog(self) -> dict[str, Any]:
        """Return the Gemini platform Agent Gateway (`networkservices.googleapis.com`)
        and IAM v3 Unified Access Policy (`iam.googleapis.com/v3beta`) control-plane catalog."""
        return {
            "service_api": f"https://networkservices.googleapis.com/v1/projects/{_gcp_project_id()}/locations/us-central1/agentGateways",
            "iam_uap_api": f"https://iam.googleapis.com/v3beta/projects/{_gcp_project_id()}/locations/global/accessPolicies",
            "gateways": [
                {
                    "gateway_id": "docubridge-client-to-agent-gw",
                    "resource_name": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                    "display_name": "DocuBridge Client-to-Agent Ingress Gateway",
                    "governed_access_path": "CLIENT_TO_AGENT",
                    "deployment_mode": "GOOGLE_MANAGED",
                    "protocols": ["A2A", "MCP", "HTTPS"],
                    "model_armor": {
                        "enabled": True,
                        "request_template": f"projects/{_gcp_project_id()}/locations/us-central1/templates/docubridge-model-armor-strict",
                        "response_template": f"projects/{_gcp_project_id()}/locations/us-central1/templates/docubridge-model-armor-strict",
                        "filters": [
                            "PI_AND_JAILBREAK_BLOCK",
                            "CLOUD_DLP_SDP_REDACT_SSN",
                            "MALICIOUS_URI_FILTER",
                        ],
                    },
                    "per_party_invoker_scoping": True,
                },
                {
                    "gateway_id": "docubridge-agent-to-anywhere-gw",
                    "resource_name": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "display_name": "DocuBridge Agent-to-Anywhere Egress Gateway",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "deployment_mode": "GOOGLE_MANAGED",
                    "protocols": ["MCP", "A2A"],
                    "registries": [
                        f"//agentregistry.googleapis.com/projects/{_gcp_project_id()}/locations/us-central1",
                        f"//agentregistry.googleapis.com/projects/{_gcp_project_id()}/locations/global",
                    ],
                    "authz_extension": {
                        "name": f"projects/{_gcp_project_id()}/locations/us-central1/authzExtensions/docubridge-iap-request-authz-ext",
                        "service": "iap.googleapis.com",
                        "fail_open": False,
                        "timeout": "1s",
                        "iap_policy_version": "V2",
                        "enforcement_mode": "ENFORCED",
                    },
                    "authz_policy": f"projects/{_gcp_project_id()}/locations/us-central1/authzPolicies/docubridge-iap-request-authz-policy",
                },
            ],
            "unified_access_policy": {
                "access_policy_id": "docubridge-agent-egress-uap",
                "resource_name": f"projects/{_gcp_project_id()}/locations/global/accessPolicies/docubridge-agent-egress-uap",
                "evaluation_order": "DENY_OVERRIDES_ALLOW",
                "permission": "iap.googleapis.com/resources.egressViaIAP",
                "rules": [
                    {
                        "rule_id": "uap-deny-destructive-or-external-mcp",
                        "effect": "DENY",
                        "description": "Block destructive MCP tools and block external portal agent from internal MCP servers",
                        "principals": ["*"],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['operational_store.drop_table', 'vault.delete_worm_object'] || "
                            "request.auth.principal.endsWith('/external-portal-agent')"
                        ),
                    },
                    {
                        "rule_id": "uap-allow-knowledge-catalog-mcp",
                        "effect": "ALLOW",
                        "description": "Allow vetted internal agents to invoke Knowledge Catalog & Lineage MCP tools",
                        "principals": [
                            f"principalSet://agents.global.org-docubridge.system.id.goog/attribute.platformContainer/aiplatform/projects/{_gcp_project_number()}"
                        ],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.name == "
                            f"f'projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-knowledge-catalog-mcp' && "
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['catalog.trace_lineage', 'catalog.list_assets', 'catalog.authorize_access']"
                        ),
                    },
                    {
                        "rule_id": "uap-allow-alloydb-operational-mcp",
                        "effect": "ALLOW",
                        "description": "Allow vetted internal agents to query and consolidate AlloyDB Bi-Temporal facts",
                        "principals": [
                            f"principalSet://agents.global.org-docubridge.system.id.goog/attribute.platformContainer/aiplatform/projects/{_gcp_project_number()}"
                        ],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.name == "
                            f"f'projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-alloydb-operational-store-mcp' && "
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['operational_store.query_as_of', 'operational_store.consolidate_entry', 'operational_store.export_iceberg_cdc']"
                        ),
                    },
                    {
                        "rule_id": "uap-allow-bigquery-lakehouse-mcp",
                        "effect": "ALLOW",
                        "description": "Allow vetted internal agents to query BigQuery Iceberg & OpenTelemetry spans",
                        "principals": [
                            f"principalSet://agents.global.org-docubridge.system.id.goog/attribute.platformContainer/aiplatform/projects/{_gcp_project_number()}"
                        ],
                        "cel_expression": (
                            "destination.agent_registry.mcp_server.name == "
                            f"f'projects/{_gcp_project_number()}/locations/us-central1/mcpServers/agentregistry-bigquery-mcp' && "
                            "destination.agent_registry.mcp_server.tool.name in "
                            "['bigquery.query_iceberg', 'bigquery.query_otel_spans']"
                        ),
                    },
                ],
            },
        }

    def _inspect_with_gcp_model_armor(
        self, raw_text: str, *, location: str = "us-central1"
    ) -> tuple[bool, str] | None:
        """Invoke the GCP Model Armor REST API (`sanitizeUserPrompt`) when enabled on GCP.

        Returns `(allowed, verdict_str)` when a live response is received, or `None`
        to fall back to deterministic local inspection when running offline/local.
        """
        if not raw_text.strip():
            return None
        live_enabled = os.environ.get(
            "BRIDGE_MODEL_ARMOR_LIVE", ""
        ).strip().lower() in {"1", "true"}
        session = getattr(self, "_model_armor_session", None)
        if not live_enabled and session is None:
            return None

        project_id = _gcp_project_id()
        template_id = (
            os.environ.get("BRIDGE_MODEL_ARMOR_TEMPLATE", "").strip()
            or "docubridge-model-armor-strict"
        )
        url = (
            f"https://modelarmor.{location}.rep.googleapis.com/v1/projects/{project_id}"
            f"/locations/{location}/templates/{template_id}:sanitizeUserPrompt"
        )
        with contextlib.suppress(Exception):
            if session is None:
                import google.auth
                from google.auth.transport.requests import AuthorizedSession

                creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                session = AuthorizedSession(creds)
            resp = session.post(
                url,
                json={"userPromptData": {"text": raw_text}},
                timeout=3.0,
            )
            if getattr(resp, "status_code", 500) == 200:
                body = resp.json() if hasattr(resp, "json") else {}
                result = body.get("sanitizationResult") or {}
                match_state = str(result.get("filterMatchState", "NO_MATCH_FOUND"))
                if match_state == "MATCH_FOUND":
                    return False, "BLOCKED (GCP_MODEL_ARMOR_API)"
                return True, "PASS (GCP_MODEL_ARMOR_API)"
        return None

    def evaluate_gateway_route(
        self,
        *,
        governed_access_path: Literal["CLIENT_TO_AGENT", "AGENT_TO_ANYWHERE"],
        source_principal: str,
        target_resource: str,
        protocol: Literal["MCP", "A2A", "HTTPS"] = "MCP",
        mcp_tool_name: str | None = None,
        payload_text: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate an inbound (`CLIENT_TO_AGENT`) or outbound (`AGENT_TO_ANYWHERE`) request
        through the Gemini platform Agent Gateway, Model Armor + Cloud DLP, Agent Registry,
        and IAM v3 Unified Access Policy (CEL)."""
        from bridge.vault import _scrub_string

        with trace_step(
            "docubridge.gateway.evaluate_route",
            {
                "docubridge.zone": "1_edge_intake",
                "docubridge.gateway.access_path": governed_access_path,
                "docubridge.gateway.source_principal": source_principal,
                "docubridge.gateway.target_resource": target_resource,
                "docubridge.gateway.protocol": protocol,
                "docubridge.gateway.mcp_tool_name": mcp_tool_name or "",
            },
        ) as span:
            if governed_access_path == "CLIENT_TO_AGENT":
                raw_text = payload_text or ""
                live_ma = self._inspect_with_gcp_model_armor(raw_text)
                lower_text = raw_text.lower()
                injection_markers = (
                    "ignore previous instructions",
                    "ignore all prior rules",
                    "system prompt override",
                    "bypass approval authority",
                    "drop table",
                )
                blocked_by_ma = (live_ma is not None and not live_ma[0]) or any(
                    m in lower_text for m in injection_markers
                )
                if blocked_by_ma:
                    verdict_label = (
                        live_ma[1]
                        if (live_ma is not None and not live_ma[0])
                        else "BLOCKED (PI_AND_JAILBREAK_FILTER)"
                    )
                    span.set_attribute(
                        "docubridge.gateway.decision", "DENY_MODEL_ARMOR"
                    )
                    return {
                        "allowed": False,
                        "http_status": 403,
                        "decision": "DENY_MODEL_ARMOR_PROMPT_INJECTION",
                        "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                        "governed_access_path": "CLIENT_TO_AGENT",
                        "model_armor_verdict": verdict_label,
                        "dlp_redacted_fields": 0,
                        "sanitized_payload": None,
                        "reason": "Model Armor template 'docubridge-model-armor-strict' blocked adversarial prompt-injection payload at CLIENT_TO_AGENT ingress.",
                    }
                sanitized_text, changed = _scrub_string(raw_text)
                redaction_count = sanitized_text.count("[REDACTED-") if changed else 0
                pass_label = (
                    live_ma[1]
                    if (live_ma is not None and live_ma[0])
                    else "PASS (PI_AND_JAILBREAK_CLEAN)"
                )
                span.set_attribute("docubridge.gateway.decision", "ALLOW_INGRESS")
                span.set_attribute("docubridge.gateway.dlp_redactions", redaction_count)
                return {
                    "allowed": True,
                    "http_status": 200,
                    "decision": "ALLOW_CLIENT_TO_AGENT_INGRESS",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                    "governed_access_path": "CLIENT_TO_AGENT",
                    "model_armor_verdict": pass_label,
                    "dlp_redacted_fields": redaction_count,
                    "sanitized_payload": sanitized_text,
                    "reason": "Ingress request passed Model Armor inspection and Cloud DLP inline SSN redaction.",
                }

            # AGENT_TO_ANYWHERE (Agent Egress)
            if source_principal.startswith("urn:") or target_resource.startswith(
                "urn:"
            ):
                span.set_attribute(
                    "docubridge.gateway.decision", "NOT_FOUND_URN_MISUSE"
                )
                return {
                    "allowed": False,
                    "http_status": 404,
                    "decision": "NOT_FOUND_URN_MISUSE",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": None,
                    "reason": (
                        "NOT_FOUND: Requested entity was not found. Agent Gateway & IAM Unified Access Policies "
                        "require the hierarchical Resource Name (projects/.../locations/.../mcpServers/agentregistry-...), not a URN."
                    ),
                }

            cat = self.get_registry_catalog()["agent_registry"]
            registered_agent_principals = (
                {a["resource_name"] for a in cat["agents"]}
                | {a["agent_principal"] for a in cat["agents"]}
                | {a["spiffe_id"] for a in cat["agents"]}
            )
            if source_principal not in registered_agent_principals:
                span.set_attribute(
                    "docubridge.gateway.decision", "DENY_SHADOW_AI_SOURCE"
                )
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNREGISTERED_SHADOW_AGENT",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "agent-registry-membership-check",
                    "reason": f"Source principal '{source_principal}' is not registered in Agent Registry (Shadow AI blocked).",
                }

            registered_targets = (
                {m["resource_name"]: m for m in cat["mcp_servers"]}
                | {e["resource_name"]: e for e in cat["endpoints"]}
                | {a["resource_name"]: a for a in cat["agents"]}
            )
            if target_resource not in registered_targets:
                span.set_attribute(
                    "docubridge.gateway.decision", "DENY_UNREGISTERED_TARGET"
                )
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNREGISTERED_TARGET_RESOURCE",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "agent-registry-target-check",
                    "reason": f"Destination '{target_resource}' is not registered in //agentregistry.googleapis.com/projects/{_gcp_project_id()}.",
                }

            # Evaluate IAM v3 Unified Access Policy: DENY rules evaluated first!
            if (
                mcp_tool_name
                in ("operational_store.drop_table", "vault.delete_worm_object")
                or source_principal.endswith("/external-portal-agent")
                or source_principal.endswith("agentregistry-external-portal-agent")
            ):
                span.set_attribute("docubridge.gateway.decision", "DENY_UAP_CEL")
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UAP_CEL_POLICY",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "uap-deny-destructive-or-external-mcp",
                    "permission": "iap.googleapis.com/resources.egressViaIAP",
                    "reason": (
                        f"IAM Unified Access Policy deny rule 'uap-deny-destructive-or-external-mcp' blocked "
                        f"source='{source_principal}' tool='{mcp_tool_name}' on target='{target_resource}'."
                    ),
                }

            target_obj = registered_targets[target_resource]
            allowed_tools = target_obj.get("tools")
            if allowed_tools and mcp_tool_name and mcp_tool_name not in allowed_tools:
                span.set_attribute(
                    "docubridge.gateway.decision", "DENY_UNDECLARED_MCP_TOOL"
                )
                return {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNDECLARED_MCP_TOOL",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "uap-default-deny",
                    "permission": "iap.googleapis.com/resources.egressViaIAP",
                    "reason": f"Tool '{mcp_tool_name}' is not permitted by any ALLOW rule on MCP server '{target_resource}'.",
                }

            span.set_attribute("docubridge.gateway.decision", "ALLOW_EGRESS")
            return {
                "allowed": True,
                "http_status": 200,
                "decision": "ALLOW_EGRESS_VIA_IAP",
                "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw",
                "governed_access_path": "AGENT_TO_ANYWHERE",
                "matched_uap_rule": "uap-allow-registered-mcp-or-a2a",
                "permission": "iap.googleapis.com/resources.egressViaIAP",
                "protocol": protocol,
                "mcp_tool_name": mcp_tool_name,
                "reason": (
                    f"IAP v2 Unified Access Policy granted 'iap.googleapis.com/resources.egressViaIAP' "
                    f"for '{source_principal}' -> '{target_resource}' (tool='{mcp_tool_name or 'A2A'}')."
                ),
            }
