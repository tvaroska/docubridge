"""Zone 6 — Knowledge Catalog: Unified Data Map, Lineage & Governance."""

from __future__ import annotations

from bridge.knowledge_catalog.catalog import (
    InMemoryKnowledgeCatalog,
    KnowledgeCatalog,
    _normalize_field_lookup_key,
    authorize_access,
    knowledge_catalog_from_env,
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
    QualityRule,
)

__all__ = [
    "DEFAULT_ACCESS_POLICIES",
    "DEFAULT_GLOSSARY_TERMS",
    "DEFAULT_SYSTEM_ASSETS",
    "AccessDecision",
    "AccessPolicy",
    "AgentSemanticContext",
    "AssetLocationZone",
    "BusinessGlossaryTerm",
    "CatalogAsset",
    "CatalogRole",
    "DeliveryChannel",
    "InMemoryKnowledgeCatalog",
    "KnowledgeCatalog",
    "LineageEdge",
    "LineageNode",
    "LineageTrace",
    "QualityRule",
    "_normalize_field_lookup_key",
    "authorize_access",
    "knowledge_catalog_from_env",
]
