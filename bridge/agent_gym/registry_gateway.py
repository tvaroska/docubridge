"""Gemini platform Agent Registry, Skill Registry & Agent Gateway control-plane mixin."""

from __future__ import annotations

from typing import Any, Literal

from bridge.agent_gym.gateway_policy import GatewayPolicyMixin as GatewayPolicyMixin
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


class RegistryGatewayMixin(GatewayPolicyMixin):
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

        port_skills = loaded_skills()
        card = build_agent_card(skills=port_skills)
        all_revs = self.list_skill_revisions()
        revs_by_skill: dict[str, list[SkillRevisionRecord]] = {}
        for rev in all_revs:
            revs_by_skill.setdefault(rev.skill_name, []).append(rev)

        skill_containers: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for meta in port_skills:
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
                    "revisions": [
                        r.model_dump(mode="json") for r in (history or [active_rev])
                    ],
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
                    "get_entity_ontology",
                    "explain_metric_formula",
                    "check_doctype_precedence",
                    "trace_field_lineage",
                    "search_knowledge_catalog",
                    "get_glossary_term",
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

    def register_registry_service(  # noqa: PLR0913 - keyword-only Agent Registry service fields
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
        projected: dict[str, Any]
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

    def create_registry_binding(  # noqa: PLR0913 - keyword-only Agent Registry binding fields
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


__all__ = ["GatewayPolicyMixin", "RegistryGatewayMixin"]
