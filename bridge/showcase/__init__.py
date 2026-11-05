"""Showcase scenarios: data-driven seeds and staged beats loaded from `scenarios/`."""

from __future__ import annotations

from typing import Any

from bridge.agent_gym import AgentGymEngine
from bridge.knowledge_catalog import KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.showcase.engine import ScenarioEngine, UnknownScenarioError
from bridge.showcase.loader import (
    SCENARIOS_DIR,
    SCENARIOS_DIR_ENV,
    ScenarioLoadError,
    discover_manifests,
    load_manifest,
    scenarios_root,
)
from bridge.showcase.operations import registered_operations
from bridge.showcase.routes import register_scenario_routes
from bridge.vault import AuditVaultStore


def seed_demo_scenarios(
    *,
    vault_store: AuditVaultStore | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
) -> dict[str, Any]:
    """Seed every manifest's baseline facts (idempotent per entity)."""
    engine = ScenarioEngine(
        store=operational_store, catalog=knowledge_catalog, gym=agent_gym
    )
    return engine.seed_all(vault_store=vault_store)


__all__ = [
    "SCENARIOS_DIR",
    "SCENARIOS_DIR_ENV",
    "ScenarioEngine",
    "ScenarioLoadError",
    "UnknownScenarioError",
    "discover_manifests",
    "load_manifest",
    "register_scenario_routes",
    "registered_operations",
    "scenarios_root",
    "seed_demo_scenarios",
]
