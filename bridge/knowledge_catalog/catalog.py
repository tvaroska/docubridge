"""Zone 6 — Knowledge Catalog Protocol, In-Memory Implementation & Authorization."""

from __future__ import annotations

import os
from typing import Literal, Protocol, runtime_checkable

from bridge.identity import (
    DEFAULT_SPIFFE_AGENT_POLICIES,
    ShadowAgentError,
    SpiffeAgentPolicy,
    SpiffeAuthenticationError,
    authorize_spiffe_agent,
)
from bridge.knowledge_catalog.models import (
    DEFAULT_ACCESS_POLICIES,
    DEFAULT_GLOSSARY_TERMS,
    DEFAULT_SYSTEM_ASSETS,
    AccessDecision,
    AccessPolicy,
    AgentSemanticContext,
    AssetLocationZone,
    BusinessGlossaryTerm,
    CatalogAsset,
    CatalogRole,
    DeliveryChannel,
    LineageEdge,
    LineageNode,
    LineageTrace,
)
from bridge.operational_store import (
    ConsolidatedFact,
    OperationalStore,
    operational_store_from_env,
)
from bridge.telemetry import trace_step
from contract.refinery import (
    OntologyEdge,
    OntologyEntitySpec,
    OntologyRevision,
    OntologyRevisionState,
)
from contract.wire import LedgerEntry

_SHARED_ONTOLOGY_REVISIONS: list[OntologyRevision] = []
_ACTIVE_ONTOLOGY_REVISION: OntologyRevision | None = None


def _ensure_shared_ontology() -> tuple[list[OntologyRevision], OntologyRevision]:
    """Ensure the shared baseline OntologyRevision is loaded from OKF bundles."""
    global _ACTIVE_ONTOLOGY_REVISION
    if _ACTIVE_ONTOLOGY_REVISION is None or not _SHARED_ONTOLOGY_REVISIONS:
        from bridge.refinery.okf_loader import build_baseline_ontology_revision

        baseline = build_baseline_ontology_revision()
        _SHARED_ONTOLOGY_REVISIONS.clear()
        _SHARED_ONTOLOGY_REVISIONS.append(baseline)
        _ACTIVE_ONTOLOGY_REVISION = baseline
    return _SHARED_ONTOLOGY_REVISIONS, _ACTIVE_ONTOLOGY_REVISION


def authorize_access(
    *,
    role: CatalogRole,
    channel: DeliveryChannel,
    target_view: str,
    policies: dict[CatalogRole, AccessPolicy] | None = None,
    agent_spiffe_id: str | None = None,
    svid_verified: bool = False,
    spiffe_policies: dict[str, SpiffeAgentPolicy] | None = None,
) -> AccessDecision:
    """Evaluate whether ``role`` (and optional ``agent_spiffe_id``) on ``channel`` may query ``target_view``."""
    with trace_step(
        "docubridge.catalog.authorize_access",
        {
            "docubridge.zone": "6_knowledge_catalog_agent_gym",
            "docubridge.role": role,
            "docubridge.channel": channel,
            "docubridge.target_view": target_view,
            "docubridge.agent_spiffe_id": agent_spiffe_id,
            "docubridge.svid_verified": svid_verified,
        },
    ) as span:
        if agent_spiffe_id is not None:
            try:
                authorize_spiffe_agent(
                    agent_spiffe_id,
                    channel=channel,
                    role=role,
                    target_view=target_view,
                    agent_policies=spiffe_policies,
                )
            except (
                ShadowAgentError,
                SpiffeAuthenticationError,
                PermissionError,
            ) as exc:
                span.set_attribute("docubridge.access_allowed", False)
                return AccessDecision(
                    allowed=False,
                    role=role,
                    channel=channel,
                    target_view=target_view,
                    reason=str(exc),
                    agent_spiffe_id=agent_spiffe_id,
                    svid_verified=svid_verified,
                )

        registry = policies if policies is not None else DEFAULT_ACCESS_POLICIES
        policy = registry.get(role)
        if policy is None:
            span.set_attribute("docubridge.access_allowed", False)
            return AccessDecision(
                allowed=False,
                role=role,
                channel=channel,
                target_view=target_view,
                reason=f"Unknown role '{role}'.",
                agent_spiffe_id=agent_spiffe_id,
                svid_verified=svid_verified,
            )
        if channel not in policy.allowed_channels:
            span.set_attribute("docubridge.access_allowed", False)
            return AccessDecision(
                allowed=False,
                role=role,
                channel=channel,
                target_view=target_view,
                reason=(
                    f"Role '{role}' is not permitted on delivery channel '{channel}' "
                    f"(allowed: {', '.join(policy.allowed_channels)})."
                ),
                agent_spiffe_id=agent_spiffe_id,
                svid_verified=svid_verified,
            )
        if target_view not in policy.allowed_views:
            span.set_attribute("docubridge.access_allowed", False)
            return AccessDecision(
                allowed=False,
                role=role,
                channel=channel,
                target_view=target_view,
                reason=(
                    f"Role '{role}' is not authorized to access view '{target_view}' "
                    f"(allowed: {', '.join(policy.allowed_views)})."
                ),
                agent_spiffe_id=agent_spiffe_id,
                svid_verified=svid_verified,
            )
        span.set_attribute("docubridge.access_allowed", True)
        return AccessDecision(
            allowed=True,
            role=role,
            channel=channel,
            target_view=target_view,
            reason="Authorized by Knowledge Catalog governance policy.",
            agent_spiffe_id=agent_spiffe_id,
            svid_verified=svid_verified,
            allow_internal_conflict_notes=policy.allow_internal_conflict_notes,
            allow_competitor_quotes=policy.allow_competitor_quotes,
            allow_raw_vault_uri=policy.allow_raw_vault_uri,
            redacted_fields=policy.redacted_fields,
        )


def _normalize_field_lookup_key(field_name_or_path: str) -> str:
    """Normalize a bare field name or dotted path to its bare leaf + full path."""
    return field_name_or_path.strip()


@runtime_checkable
class KnowledgeCatalog(Protocol):
    """Protocol for the Zone 6 Unified Knowledge Catalog."""

    def list_glossary_terms(
        self, *, domain: str | None = None, include_confidential: bool = True
    ) -> list[BusinessGlossaryTerm]:
        """Return business glossary terms filtered by domain, parent glossary ID, or search query."""
        ...

    def register_asset(self, asset: CatalogAsset) -> None:
        """Register or update a catalog asset in the knowledge catalog."""
        ...

    def list_assets(
        self,
        *,
        zone: AssetLocationZone | None = None,
        external_safe_only: bool = False,
    ) -> list[CatalogAsset]:
        """Return catalog assets filtered by asset type, domain, sensitivity tier, or search query."""
        ...

    def register_spiffe_agent(self, policy: SpiffeAgentPolicy) -> None:
        """Register or update a SPIFFE workload identity card in the knowledge catalog."""
        ...

    def list_spiffe_agents(
        self, *, security_zone: Literal["internal", "external"] | None = None
    ) -> list[SpiffeAgentPolicy]:
        """Return registered SPIFFE workload identities, optionally filtered by trust domain."""
        ...

    def register_submission_lineage(
        self,
        entry: LedgerEntry,
        facts: list[ConsolidatedFact],
    ) -> list[LineageTrace]:
        """Record end-to-end W3C PROV-O lineage edges and derived assets for a document submission."""
        ...

    def trace_lineage(
        self, entity_id: str, field_name_or_path: str
    ) -> LineageTrace | None:
        """Trace upstream and downstream W3C PROV-O lineage edges for a target catalog asset."""
        ...

    def list_lineage_for_entity(self, entity_id: str) -> list[LineageTrace]:
        """Return all lineage edges associated with a specific entity ID."""
        ...

    def build_agent_context(
        self,
        *,
        role: CatalogRole,
        channel: DeliveryChannel,
        target_view: str,
        agent_spiffe_id: str | None = None,
        svid_verified: bool = False,
    ) -> AgentSemanticContext:
        """Assemble the grounded catalog, glossary, and SPIFFE context payload for an agent."""
        ...

    def get_active_ontology_revision(self) -> OntologyRevision:
        """Return the currently active OntologyRevision in Zone 6."""
        ...

    def list_ontology_revisions(self) -> list[OntologyRevision]:
        """Return all registered OntologyRevision snapshots in chronological order."""
        ...

    def register_ontology_revision(
        self,
        revision: OntologyRevision,
        *,
        activate: bool = True,
    ) -> OntologyRevision:
        """Register a new OntologyRevision snapshot and optionally promote it to ACTIVE."""
        ...

    def reset_ontology_revisions(self) -> OntologyRevision:
        """Reset the Zone 6 Ontology Graph back to the baseline revision."""
        ...

    def list_ontology_edges(
        self,
        *,
        relation: str | None = None,
        source_id: str | None = None,
    ) -> list[OntologyEdge]:
        """Return OntologyEdge records from the active OntologyRevision."""
        ...

    def resolve_doctype_priority(self, doctype: str) -> tuple[int, str | None]:
        """Resolve (source_priority, consolidation_edge_id) for a doctype from the Ontology Graph."""
        ...

    def resolve_metric_bucket(
        self,
        field_name: str,
        doctype: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Resolve (metric_bucket, consolidation_edge_id) via [:FEEDS_METRIC] edges."""
        ...

    def is_monetary_field(self, field_name: str) -> bool:
        """Return True if the active Ontology Graph declares the field as monetary."""
        ...


class InMemoryKnowledgeCatalog:
    """In-memory implementation of the Zone 6 Unified Knowledge Catalog."""

    def __init__(
        self,
        *,
        glossary_terms: tuple[BusinessGlossaryTerm, ...] = DEFAULT_GLOSSARY_TERMS,
        system_assets: tuple[CatalogAsset, ...] = DEFAULT_SYSTEM_ASSETS,
        access_policies: dict[CatalogRole, AccessPolicy] | None = None,
        spiffe_policies: dict[str, SpiffeAgentPolicy] | None = None,
        operational_store: OperationalStore | None = None,
    ) -> None:
        self._glossary: dict[str, BusinessGlossaryTerm] = {
            t.term_id: t for t in glossary_terms
        }
        self._assets: dict[str, CatalogAsset] = {a.asset_id: a for a in system_assets}
        self._policies: dict[CatalogRole, AccessPolicy] = (
            dict(access_policies)
            if access_policies is not None
            else dict(DEFAULT_ACCESS_POLICIES)
        )
        self._spiffe_policies: dict[str, SpiffeAgentPolicy] = (
            dict(spiffe_policies)
            if spiffe_policies is not None
            else dict(DEFAULT_SPIFFE_AGENT_POLICIES)
        )
        self._operational_store = operational_store
        # (entity_id, field_path) -> list of LineageTrace ordered by recorded_at
        self._lineage: dict[tuple[str, str], list[LineageTrace]] = {}

    def register_spiffe_agent(self, policy: SpiffeAgentPolicy) -> None:
        """Register or update a vetted SPIFFE agent workload policy."""
        self._spiffe_policies[policy.spiffe_id] = policy

    def list_spiffe_agents(
        self, *, security_zone: Literal["internal", "external"] | None = None
    ) -> list[SpiffeAgentPolicy]:
        """Return registered SPIFFE workload identities, optionally filtered by trust domain."""
        agents = list(self._spiffe_policies.values())
        if security_zone is not None:
            agents = [a for a in agents if a.security_zone == security_zone]
        return agents

    def bind_operational_store(self, store: OperationalStore) -> None:
        """Bind a durable OperationalStore for stateless lineage reconstruction."""
        self._operational_store = store

    def list_glossary_terms(
        self, *, domain: str | None = None, include_confidential: bool = True
    ) -> list[BusinessGlossaryTerm]:
        """Return business glossary terms filtered by domain, parent glossary ID, or search query."""
        terms = list(self._glossary.values())
        if domain is not None:
            terms = [t for t in terms if t.domain == domain]
        if not include_confidential:
            terms = [t for t in terms if not t.confidential]
        return terms

    def register_asset(self, asset: CatalogAsset) -> None:
        """Register or update a catalog asset in the in-memory knowledge catalog."""
        self._assets[asset.asset_id] = asset

    def list_assets(
        self,
        *,
        zone: AssetLocationZone | None = None,
        external_safe_only: bool = False,
    ) -> list[CatalogAsset]:
        """Return catalog assets filtered by asset type, domain, sensitivity tier, or search query."""
        assets = list(self._assets.values())
        if zone is not None:
            assets = [a for a in assets if a.zone == zone]
        if external_safe_only:
            assets = [
                a for a in assets if a.classification in ("public", "client_shared")
            ]
        return assets

    def _build_trace_from_fact(
        self, fact: ConsolidatedFact, fallback_vault_uri: str | None = None
    ) -> LineageTrace:
        vault_uri = (
            fact.vault_uri or fallback_vault_uri or f"vault://local/vault/{fact.doc_id}"
        )
        page_num = fact.source_anchor.page_number if fact.source_anchor else 1
        source_pdf_uri = (
            fact.source_anchor.artifact_uri
            if fact.source_anchor and fact.source_anchor.artifact_uri
            else f"{vault_uri}#page={page_num}"
        )
        src_node = LineageNode(
            node_id=f"pdf:{fact.entity_id}:{fact.doc_id}:{fact.field_path}:p{page_num}",
            node_type="source_pdf",
            label=f"Source PDF ({fact.doctype}, page {page_num})",
            uri=source_pdf_uri,
            zone=AssetLocationZone.INBOUND,
            metadata={
                "page_number": page_num,
                "doctype": fact.doctype,
                "skill_urn": fact.skill_urn,
                "skill_version": fact.skill_version,
                "skill_revision_id": fact.skill_revision_id,
                "process_skill_revision_id": fact.process_skill_revision_id,
                "compiled_rule_ids": list(fact.compiled_rule_ids),
            },
        )
        vault_node = LineageNode(
            node_id=f"vault:{fact.entity_id}:{fact.doc_id}",
            node_type="vault_object",
            label="Cloud Storage Audit Vault (WORM)",
            uri=vault_uri,
            zone=AssetLocationZone.AUDIT_VAULT,
        )
        fact_node = LineageNode(
            node_id=f"fact:{fact.fact_id}",
            node_type="consolidated_fact",
            label=f"AlloyDB Consolidated Fact ({fact.field_path})",
            uri=f"alloydb://docubridge-enterprise-data/operational/bridge_operational_facts/{fact.fact_id}",
            zone=AssetLocationZone.OPERATIONAL_STORE,
            metadata={
                "source_priority": fact.source_priority,
                "recorded_at": fact.recorded_at.isoformat(),
                "effective_from": fact.effective_from.isoformat(),
                "skill_urn": fact.skill_urn,
                "skill_version": fact.skill_version,
                "skill_revision_id": fact.skill_revision_id,
                "process_skill_revision_id": fact.process_skill_revision_id,
                "compiled_rule_ids": list(fact.compiled_rule_ids),
            },
        )
        view_node = LineageNode(
            node_id=f"view:v_broker_placement:{fact.entity_id}:{fact.field_path}",
            node_type="business_view",
            label="Role-Based Business Views (Broker / Claims / Underwriter)",
            uri="alloydb://docubridge-enterprise-data/views/v_broker_placement",
            zone=AssetLocationZone.BUSINESS_VIEW,
        )
        bq_node = LineageNode(
            node_id=f"bq:{fact.fact_id}",
            node_type="bigquery_table",
            label="BigQuery Analytics Sync (Open Iceberg)",
            uri="bigquery://docubridge_lakehouse.bq_operational_facts_iceberg",
            zone=AssetLocationZone.BIGQUERY_ICEBERG,
        )
        a2ui_node = LineageNode(
            node_id=f"a2ui:{fact.entity_id}:{fact.field_path}",
            node_type="a2ui_widget",
            label="Domain Agent A2UI Widget (Click value -> source PDF)",
            uri=f"a2ui://citation/{fact.entity_id}/{fact.field_path}#page={page_num}",
            zone=AssetLocationZone.USER_INTERACTION_A2UI,
        )
        edges = (
            LineageEdge(
                upstream_id=src_node.node_id,
                downstream_id=vault_node.node_id,
                transformation=(
                    f"Intake validation ({fact.doctype}@{fact.skill_revision_id} "
                    f"v{fact.skill_version}), PII redaction & WORM promotion"
                ),
            ),
            LineageEdge(
                upstream_id=vault_node.node_id,
                downstream_id=fact_node.node_id,
                transformation=(
                    f"Consolidation Rules Engine (priority={fact.source_priority}, "
                    f"skill={fact.skill_revision_id}, bi-temporal append)"
                ),
            ),
            LineageEdge(
                upstream_id=fact_node.node_id,
                downstream_id=view_node.node_id,
                transformation="Role-based SQL view projection with source anchor preservation",
            ),
            LineageEdge(
                upstream_id=fact_node.node_id,
                downstream_id=bq_node.node_id,
                transformation="Append-only CDC stream to BigQuery Open Iceberg table",
            ),
            LineageEdge(
                upstream_id=view_node.node_id,
                downstream_id=a2ui_node.node_id,
                transformation="Domain Agent A2UI component streaming",
            ),
        )
        return LineageTrace(
            entity_id=fact.entity_id,
            field_path=fact.field_path,
            current_value=fact.field_value,
            source_doc_id=fact.doc_id,
            source_doctype=fact.doctype,
            vault_uri=vault_uri,
            source_anchor=fact.source_anchor,
            recorded_at=fact.recorded_at,
            effective_from=fact.effective_from,
            consolidation_rule=(
                f"source_priority_hierarchy(priority={fact.source_priority}, "
                f"doctype={fact.doctype}, skill_revision={fact.skill_revision_id}, "
                f"ontology_revision={fact.ontology_revision_id})"
            ),
            skill_version=fact.skill_version,
            skill_revision_id=fact.skill_revision_id,
            skill_urn=fact.skill_urn,
            process_skill_revision_id=fact.process_skill_revision_id,
            compiled_rule_ids=fact.compiled_rule_ids,
            ontology_revision_id=fact.ontology_revision_id,
            consolidation_edge_id=fact.consolidation_edge_id,
            nodes=(src_node, vault_node, fact_node, view_node, bq_node, a2ui_node),
            edges=edges,
        )

    def _hydrate_from_store(self, entity_id: str) -> None:
        if self._operational_store is None:
            return
        active_facts, _ = self._operational_store.query_as_of(entity_id)
        for fact in active_facts:
            trace = self._build_trace_from_fact(fact)
            self._lineage.setdefault((fact.entity_id, fact.field_path), []).append(
                trace
            )
            leaf = fact.field_path.split(".")[-1]
            if leaf != fact.field_path:
                self._lineage.setdefault((fact.entity_id, leaf), []).append(trace)

    def register_submission_lineage(
        self,
        entry: LedgerEntry,
        facts: list[ConsolidatedFact],
    ) -> list[LineageTrace]:
        """Record end-to-end lineage from source PDF -> Vault -> AlloyDB -> Views -> BQ -> A2UI."""
        with trace_step(
            "docubridge.catalog.register_lineage",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doc_id": entry.doc_id,
                "docubridge.doctype": entry.doctype,
                "docubridge.fact_count": len(facts),
                "docubridge.entity_id": facts[0].entity_id if facts else None,
            },
        ) as span:
            vault_uri = entry.vault_uri or f"vault://local/vault/{entry.doc_id}"
            self.register_asset(
                CatalogAsset(
                    asset_id=f"asset.vault.{entry.doc_id}",
                    name=f"Audit Vault Record ({entry.doc_id})",
                    asset_type="vault_archive",
                    zone=AssetLocationZone.AUDIT_VAULT,
                    physical_uri=vault_uri,
                    owner_domain=entry.doctype,
                    classification="internal_only",
                    schema_fields=tuple(sorted(entry.key_fields.keys())),
                )
            )

            traces: list[LineageTrace] = []
            for fact in facts:
                trace = self._build_trace_from_fact(fact, fallback_vault_uri=vault_uri)
                self._lineage.setdefault((fact.entity_id, fact.field_path), []).append(
                    trace
                )
                leaf = fact.field_path.split(".")[-1]
                if leaf != fact.field_path:
                    self._lineage.setdefault((fact.entity_id, leaf), []).append(trace)
                traces.append(trace)
            span.set_attribute("docubridge.registered_trace_count", len(traces))
            return traces

    def trace_lineage(
        self, entity_id: str, field_name_or_path: str
    ) -> LineageTrace | None:
        """Trace upstream and downstream W3C PROV-O lineage edges for a target catalog asset."""
        with trace_step(
            "docubridge.catalog.lineage_trace",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.entity_id": entity_id,
                "docubridge.field_name": field_name_or_path,
            },
        ) as span:
            key = _normalize_field_lookup_key(field_name_or_path)
            history = self._lineage.get((entity_id, key))
            if not history:
                self._hydrate_from_store(entity_id)
                history = self._lineage.get((entity_id, key))
            if not history:
                span.set_attribute("docubridge.lineage_found", False)
                return None
            result = history[-1]
            span.set_attribute("docubridge.lineage_found", True)
            span.set_attribute("docubridge.source_doc_id", result.source_doc_id)
            span.set_attribute("docubridge.hop_count", len(result.nodes))
            if result.source_anchor:
                span.set_attribute(
                    "docubridge.page_number", result.source_anchor.page_number
                )
            return result

    def list_lineage_for_entity(self, entity_id: str) -> list[LineageTrace]:
        """Return all lineage edges associated with a specific entity ID."""
        results = [
            traces[-1]
            for (ent_id, fpath), traces in self._lineage.items()
            if ent_id == entity_id and fpath.startswith("fields.") and traces
        ]
        if not results:
            self._hydrate_from_store(entity_id)
            results = [
                traces[-1]
                for (ent_id, fpath), traces in self._lineage.items()
                if ent_id == entity_id and fpath.startswith("fields.") and traces
            ]
        return results

    def build_agent_context(
        self,
        *,
        role: CatalogRole,
        channel: DeliveryChannel,
        target_view: str,
        agent_spiffe_id: str | None = None,
        svid_verified: bool = False,
    ) -> AgentSemanticContext:
        """Assemble the grounded catalog, glossary, and SPIFFE context payload for an agent."""
        decision = authorize_access(
            role=role,
            channel=channel,
            target_view=target_view,
            policies=self._policies,
            agent_spiffe_id=agent_spiffe_id,
            svid_verified=svid_verified,
            spiffe_policies=self._spiffe_policies,
        )
        external_only = channel == "custom_web"
        glossary = self.list_glossary_terms(
            include_confidential=decision.allow_internal_conflict_notes
        )
        assets = self.list_assets(external_safe_only=external_only)
        return AgentSemanticContext(
            role=role,
            channel=channel,
            target_view=target_view,
            agent_spiffe_id=agent_spiffe_id,
            glossary_terms=tuple(glossary),
            visible_assets=tuple(assets),
            access_decision=decision,
        )

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


def knowledge_catalog_from_env(
    *, operational_store: OperationalStore | None = None
) -> KnowledgeCatalog:
    """Resolve the Zone 6 KnowledgeCatalog from environment configuration."""
    store = operational_store or operational_store_from_env()
    backend = os.environ.get("BRIDGE_KNOWLEDGE_CATALOG_BACKEND", "").strip().lower()
    if backend == "dataplex":
        return InMemoryKnowledgeCatalog(operational_store=store)
    return InMemoryKnowledgeCatalog(operational_store=store)
