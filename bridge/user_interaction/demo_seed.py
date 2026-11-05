"""Zone 5 User Interaction: scenario seeding entry point (delegates to `bridge.showcase`)."""

from __future__ import annotations

from typing import Any

from bridge.agent_gym import AgentGymEngine
from bridge.knowledge_catalog import KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.vault import AuditVaultStore


def seed_demo_scenarios(
    *,
    vault_store: AuditVaultStore | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
) -> dict[str, Any]:
    """Seed every scenario manifest's baseline facts via `ScenarioEngine.seed_all()`."""
    # Imported here: bridge.showcase depends on this package's `surfaces` module.
    from bridge.showcase import seed_demo_scenarios as _seed

    return _seed(
        vault_store=vault_store,
        operational_store=operational_store,
        knowledge_catalog=knowledge_catalog,
        agent_gym=agent_gym,
    )


__all__ = ["seed_demo_scenarios"]
