"""``bridge.edge`` — the Bridge's canonical A2A edge (JSON-RPC 2.0 front door).

Stands up the Bridge's A2A server on ``a2a-sdk`` exposing all six spec ops
(``message/send``, ``message/stream``, ``tasks/get``, ``tasks/list``,
``tasks/cancel``, ``tasks/resubscribe``), with the domain contract carried
*inside* standard A2A parts/artifacts and no document bytes ever inline.

Two load-bearing decisions (see module docstrings for detail):
- **Decision 1:** the hybrid edge — structured traffic (assertions,
  ``CollectRequest``s, file submissions) runs on the deterministic, model-free P1-4
  ``Collector`` / Path-A validate-only; behind ``BRIDGE_EDGE_MODE=hybrid`` a text-only
  intent is handed to the ``document_bridge`` ``LlmAgent`` leg, whose turn is still a
  projected domain ``ExchangeTurn`` (never model text). Default mode: deterministic.
- **Decision 2:** built directly on ``a2a-sdk``'s first-class ``AgentExecutor``
  ABC, NOT ADK's ``@a2a_experimental`` ``A2aAgentExecutor``.

**Isolation guard:** ``bridge/edge/`` is under ``bridge/`` and must NEVER import
``agents/`` (``tests/test_isolation_guard.py``). It imports only ``contract/``,
``bridge.*``, ``a2a.*``, and their proto/Starlette deps.
"""
from bridge.edge.a2ui import (
    A2uiAction,
    A2uiItem,
    A2uiRequirement,
    A2uiView,
    encode_a2ui_view,
    render_hitl_review,
    render_party_status,
    render_upload_prompt,
)
from bridge.edge.agent_engine_a2a import (
    AGENT_ENGINE_A2A_ENV,
    BridgeA2aAgentEngine,
    agent_engine_app,
    build_remote_a2a_routes,
    is_agent_engine_a2a_enabled,
    resolve_a2a_engine,
)
from bridge.edge.artifacts import (
    ARTIFACTS_PREFIX,
    build_fetch_app,
    persist_submission,
    resolve_bytes,
    scoped_uri,
)
from bridge.edge.card import agent_card_from_skills, build_agent_card
from bridge.edge.executor import CollectExecutor
from bridge.edge.intake import decode_intake, parse_text_request
from bridge.edge.mock import (
    DEFAULT_SCENARIO,
    SCENARIOS,
    Scenario,
    ScriptedBridgeExecutor,
    build_mock_bridge_app,
    select_scenario,
)
from bridge.edge.pathroute import (
    EdgeMode,
    Path,
    Route,
    classify_path,
    classify_route,
    decode_assertion,
    resolve_edge_mode,
)
from bridge.edge.server import build_a2a_app
from bridge.edge.skills import SkillMeta, loaded_skills, skill_meta_from_dir
from bridge.edge.wire import (
    decode_collect_request,
    decode_exchange_turn,
    decode_submissions,
    encode_collect_request,
    encode_exchange_turn,
    status_message_for,
    summarize_turn,
)

__all__ = [
    "AGENT_ENGINE_A2A_ENV",
    "ARTIFACTS_PREFIX",
    "DEFAULT_SCENARIO",
    "SCENARIOS",
    "A2uiAction",
    "A2uiItem",
    "A2uiRequirement",
    "A2uiView",
    "BridgeA2aAgentEngine",
    "CollectExecutor",
    "EdgeMode",
    "Path",
    "Route",
    "Scenario",
    "ScriptedBridgeExecutor",
    "SkillMeta",
    "agent_card_from_skills",
    "agent_engine_app",
    "build_a2a_app",
    "build_agent_card",
    "build_fetch_app",
    "build_mock_bridge_app",
    "build_remote_a2a_routes",
    "classify_path",
    "classify_route",
    "decode_assertion",
    "decode_collect_request",
    "decode_exchange_turn",
    "decode_intake",
    "decode_submissions",
    "encode_a2ui_view",
    "encode_collect_request",
    "encode_exchange_turn",
    "is_agent_engine_a2a_enabled",
    "loaded_skills",
    "parse_text_request",
    "persist_submission",
    "render_hitl_review",
    "render_party_status",
    "render_upload_prompt",
    "resolve_a2a_engine",
    "resolve_bytes",
    "resolve_edge_mode",
    "scoped_uri",
    "select_scenario",
    "skill_meta_from_dir",
    "status_message_for",
    "summarize_turn",
]
