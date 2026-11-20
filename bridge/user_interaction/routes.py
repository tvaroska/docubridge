"""Zone 5 User Interaction: FastAPI router composer and standalone ASGI application."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI

from bridge.a2ui_inbox import ActionInboxStore, action_inbox_store_from_env
from bridge.agent_gym import AgentGymEngine, agent_gym_from_env
from bridge.knowledge_catalog import (
    CatalogRole,
    KnowledgeCatalog,
    knowledge_catalog_from_env,
)
from bridge.operational_store import OperationalStore, operational_store_from_env
from bridge.refinery import schema_refinery_from_env
from bridge.telemetry import OtelAsgiMiddleware
from bridge.user_interaction.a2ui_routes import register_a2ui_routes
from bridge.user_interaction.demo_session import register_demo_session_routes
from bridge.user_interaction.gym_routes import register_gym_routes
from bridge.user_interaction.intake_routes import register_intake_and_demo_routes
from bridge.user_interaction.refinery_routes import register_refinery_routes
from bridge.user_interaction.registry_routes import (
    register_registry_and_catalog_routes,
)
from bridge.user_interaction.telemetry_routes import register_telemetry_routes


def build_user_interaction_router(  # noqa: PLR0913 - public FastAPI router dependency injection contract
    *,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    inbox_store: ActionInboxStore | None = None,
    agent_gym: AgentGymEngine | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> APIRouter:
    """Create a FastAPI router exposing Zone 5 A2UI, A2UI v0.9 Action Progress/Inbox,
    Agent Gym continuous learning, and Zone 6 Knowledge Catalog endpoints."""
    # Imported here: bridge.showcase depends on this package's `surfaces` module.
    from bridge.showcase import ScenarioEngine, register_scenario_routes

    store = operational_store or operational_store_from_env()
    catalog = knowledge_catalog or knowledge_catalog_from_env(operational_store=store)
    if hasattr(catalog, "bind_operational_store"):
        catalog.bind_operational_store(store)
    inbox = action_inbox_store_from_env(inbox_store)
    gym = agent_gym_from_env(agent_gym)
    refinery = schema_refinery_from_env(knowledge_catalog=catalog)
    scenario_engine = ScenarioEngine(
        store=store, catalog=catalog, inbox=inbox, gym=gym, refinery=refinery
    )
    router = APIRouter()

    register_demo_session_routes(router, require_auth=require_auth)
    register_intake_and_demo_routes(
        router,
        store=store,
        catalog=catalog,
        gym=gym,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )
    register_scenario_routes(
        router,
        engine=scenario_engine,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )
    register_a2ui_routes(
        router,
        store=store,
        catalog=catalog,
        inbox=inbox,
        gym=gym,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )
    register_gym_routes(
        router,
        store=store,
        catalog=catalog,
        inbox=inbox,
        gym=gym,
        principal_roles=principal_roles,
        require_auth=require_auth,
        scenario_engine=scenario_engine,
    )
    register_registry_and_catalog_routes(
        router,
        store=store,
        catalog=catalog,
        gym=gym,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )
    register_telemetry_routes(
        router,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )
    register_refinery_routes(
        router,
        catalog=catalog,
        inbox=inbox,
        schema_refinery=refinery,
        principal_roles=principal_roles,
        require_auth=require_auth,
    )

    return router


def build_user_interaction_app(  # noqa: PLR0913 - public FastAPI app dependency injection contract
    *,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    inbox_store: ActionInboxStore | None = None,
    agent_gym: AgentGymEngine | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
    oidc_verifier: Callable[..., dict[str, Any]] | None = None,
) -> FastAPI:
    """Build a standalone FastAPI application for Zone 5 A2UI and Zone 6 Catalog."""
    app = FastAPI(
        title="DocuBridge Enterprise Data & A2UI Delivery Service",
        version="1.5.0",
    )
    if oidc_verifier is not None:
        app.state.oidc_verifier = oidc_verifier
    app.add_middleware(OtelAsgiMiddleware, service_component="user_interaction")
    app.include_router(
        build_user_interaction_router(
            operational_store=operational_store,
            knowledge_catalog=knowledge_catalog,
            inbox_store=inbox_store,
            agent_gym=agent_gym,
            principal_roles=principal_roles,
            require_auth=require_auth,
        )
    )
    return app
