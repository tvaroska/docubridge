"""Agent Gateway, Model Armor, and IAM v3 Unified Access Policy (CEL) evaluation mixin."""

from __future__ import annotations

import contextlib
import os
from typing import Any, Literal

from bridge.agent_gym.models import (
    _gcp_project_id,
    _gcp_project_number,
)
from bridge.telemetry import trace_step


class GatewayPolicyMixin:
    """Mixin providing Agent Gateway catalog, Model Armor inspection, and CEL route evaluation."""

    def get_registry_catalog(self) -> dict[str, Any]:
        """Return the unified Agent Registry catalog (implemented by RegistryGatewayMixin)."""
        raise NotImplementedError

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
                session = AuthorizedSession(creds)  # type: ignore[no-untyped-call]
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

    def _evaluate_client_to_agent(
        self, payload_text: str | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from bridge.vault import _scrub_string

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
            return (
                {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_MODEL_ARMOR_PROMPT_INJECTION",
                    "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                    "governed_access_path": "CLIENT_TO_AGENT",
                    "model_armor_verdict": verdict_label,
                    "dlp_redacted_fields": 0,
                    "sanitized_payload": None,
                    "reason": "Model Armor template 'docubridge-model-armor-strict' blocked adversarial prompt-injection payload at CLIENT_TO_AGENT ingress.",
                },
                {"docubridge.gateway.decision": "DENY_MODEL_ARMOR"},
            )
        sanitized_text, changed = _scrub_string(raw_text)
        redaction_count = sanitized_text.count("[REDACTED-") if changed else 0
        pass_label = (
            live_ma[1]
            if (live_ma is not None and live_ma[0])
            else "PASS (PI_AND_JAILBREAK_CLEAN)"
        )
        return (
            {
                "allowed": True,
                "http_status": 200,
                "decision": "ALLOW_CLIENT_TO_AGENT_INGRESS",
                "gateway": f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-client-to-agent-gw",
                "governed_access_path": "CLIENT_TO_AGENT",
                "model_armor_verdict": pass_label,
                "dlp_redacted_fields": redaction_count,
                "sanitized_payload": sanitized_text,
                "reason": "Ingress request passed Model Armor inspection and Cloud DLP inline SSN redaction.",
            },
            {
                "docubridge.gateway.decision": "ALLOW_INGRESS",
                "docubridge.gateway.dlp_redactions": redaction_count,
            },
        )

    def _evaluate_agent_to_anywhere(
        self,
        *,
        source_principal: str,
        target_resource: str,
        protocol: Literal["MCP", "A2A", "HTTPS"],
        mcp_tool_name: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        gw_name = f"projects/{_gcp_project_id()}/locations/us-central1/agentGateways/docubridge-agent-to-anywhere-gw"
        if source_principal.startswith("urn:") or target_resource.startswith("urn:"):
            return (
                {
                    "allowed": False,
                    "http_status": 404,
                    "decision": "NOT_FOUND_URN_MISUSE",
                    "gateway": gw_name,
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": None,
                    "reason": (
                        "NOT_FOUND: Requested entity was not found. Agent Gateway & IAM Unified Access Policies "
                        "require the hierarchical Resource Name (projects/.../locations/.../mcpServers/agentregistry-...), not a URN."
                    ),
                },
                {"docubridge.gateway.decision": "NOT_FOUND_URN_MISUSE"},
            )

        cat = self.get_registry_catalog()["agent_registry"]
        registered_agent_principals = (
            {a["resource_name"] for a in cat["agents"]}
            | {a["agent_principal"] for a in cat["agents"]}
            | {a["spiffe_id"] for a in cat["agents"]}
        )
        if source_principal not in registered_agent_principals:
            verdict = "DENIED_BY_SIMULATED_POLICY"
            return (
                {
                    "allowed": False,
                    "http_status": 403,
                    "decision": verdict,
                    "shim_verdict": verdict,
                    "gate_2_identity_verified_demo": False,
                    "gateway": gw_name,
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "agent-registry-membership-check",
                    "reason": f"Source principal '{source_principal}' is not registered in Agent Registry (Shadow AI blocked).",
                },
                {
                    "docubridge.gateway.decision": verdict,
                    "gate_2_identity_verified_demo": False,
                },
            )

        registered_targets = (
            {m["resource_name"]: m for m in cat["mcp_servers"]}
            | {e["resource_name"]: e for e in cat["endpoints"]}
            | {a["resource_name"]: a for a in cat["agents"]}
        )
        if target_resource not in registered_targets:
            return (
                {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNREGISTERED_TARGET_RESOURCE",
                    "gateway": gw_name,
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "agent-registry-target-check",
                    "reason": f"Destination '{target_resource}' is not registered in //agentregistry.googleapis.com/projects/{_gcp_project_id()}.",
                },
                {"docubridge.gateway.decision": "DENY_UNREGISTERED_TARGET"},
            )

        if (
            mcp_tool_name
            in ("operational_store.drop_table", "vault.delete_worm_object")
            or source_principal.endswith("/external-portal-agent")
            or source_principal.endswith("agentregistry-external-portal-agent")
        ):
            return (
                {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UAP_CEL_POLICY",
                    "gateway": gw_name,
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "uap-deny-destructive-or-external-mcp",
                    "permission": "iap.googleapis.com/resources.egressViaIAP",
                    "reason": (
                        f"IAM Unified Access Policy deny rule 'uap-deny-destructive-or-external-mcp' blocked "
                        f"source='{source_principal}' tool='{mcp_tool_name}' on target='{target_resource}'."
                    ),
                },
                {"docubridge.gateway.decision": "DENY_UAP_CEL"},
            )

        target_obj = registered_targets[target_resource]
        allowed_tools = target_obj.get("tools")
        if allowed_tools and mcp_tool_name and mcp_tool_name not in allowed_tools:
            return (
                {
                    "allowed": False,
                    "http_status": 403,
                    "decision": "DENY_UNDECLARED_MCP_TOOL",
                    "gateway": gw_name,
                    "governed_access_path": "AGENT_TO_ANYWHERE",
                    "matched_uap_rule": "uap-default-deny",
                    "permission": "iap.googleapis.com/resources.egressViaIAP",
                    "reason": f"Tool '{mcp_tool_name}' is not permitted by any ALLOW rule on MCP server '{target_resource}'.",
                },
                {"docubridge.gateway.decision": "DENY_UNDECLARED_MCP_TOOL"},
            )

        return (
            {
                "allowed": True,
                "http_status": 200,
                "decision": "ALLOW_EGRESS_VIA_IAP",
                "gateway": gw_name,
                "governed_access_path": "AGENT_TO_ANYWHERE",
                "matched_uap_rule": "uap-allow-registered-mcp-or-a2a",
                "permission": "iap.googleapis.com/resources.egressViaIAP",
                "protocol": protocol,
                "mcp_tool_name": mcp_tool_name,
                "reason": (
                    f"IAP v2 Unified Access Policy granted 'iap.googleapis.com/resources.egressViaIAP' "
                    f"for '{source_principal}' -> '{target_resource}' (tool='{mcp_tool_name or 'A2A'}')."
                ),
            },
            {"docubridge.gateway.decision": "ALLOW_EGRESS"},
        )

    def evaluate_gateway_route(  # noqa: PLR0913 - keyword-only gateway route evaluation inputs
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
                result, attrs = self._evaluate_client_to_agent(payload_text)
            else:
                result, attrs = self._evaluate_agent_to_anywhere(
                    source_principal=source_principal,
                    target_resource=target_resource,
                    protocol=protocol,
                    mcp_tool_name=mcp_tool_name,
                )
            for attr_k, attr_v in attrs.items():
                span.set_attribute(attr_k, attr_v)
            return result


__all__ = ["GatewayPolicyMixin"]
