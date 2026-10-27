"""Tests for the document_bridge agent (construction/wiring — no model calls)."""

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from bridge.agent import INSTRUCTION, build_app, build_document_bridge
from bridge.extraction.agent import build_extraction_agent


def _build_runner(
    model: str | None = None,
    artifact_service: BaseArtifactService | None = None,
) -> Runner:
    """Test-local host for the document_bridge App.

    The Runner + its service wiring is owned by the serving surface (adk cli /
    Cloud Run hosting the ``App``, with services swapped behind ports), never by
    the ``bridge/`` core — so this in-memory assembly lives with the tests that
    exercise the LlmAgent in isolation, not in ``bridge/agent.py``.
    """
    return Runner(
        app=build_app(model),
        artifact_service=artifact_service or InMemoryArtifactService(),
        session_service=InMemorySessionService(),
    )


def test_build_document_bridge_returns_llm_agent():
    """build_document_bridge() returns an LlmAgent with extraction and gate tools."""
    agent = build_document_bridge()

    # Should be an LlmAgent
    assert isinstance(agent, LlmAgent)

    # Should be named document_bridge
    assert agent.name == "document_bridge"

    # Should have tools
    assert hasattr(agent, "tools")
    assert len(agent.tools) > 0

    # Should include tools named extraction_agent and run_disposition_gate
    tool_names = [tool.name for tool in agent.tools]
    assert "extraction_agent" in tool_names
    assert "run_disposition_gate" in tool_names


def test_build_app_and_runner():
    """build_app() returns an App; the test-local _build_runner() returns a Runner."""
    # Build app
    app = build_app()
    assert isinstance(app, App)
    assert app.name == "document_bridge"
    assert isinstance(app.root_agent, LlmAgent)
    assert app.root_agent.name == "document_bridge"

    # Build runner (just verify it constructs without credentials)
    runner = _build_runner()
    assert isinstance(runner, Runner)


def test_instruction_guardrail_present():
    """Instruction guardrail: mentions the gate, extraction_agent, and forbids LLM minting acceptance."""
    instruction = INSTRUCTION

    # Should mention the gate by name
    assert "run_disposition_gate" in instruction

    # Should mention the extraction_agent
    assert "extraction_agent" in instruction

    # Should forbid the LLM from minting an acceptance (check for key phrases)
    # Look for phrases like "never accept", "cannot accept", "may not accept", etc.
    instruction_lower = instruction.lower()

    # Check for both "gate" mention and prohibition language
    has_prohibition = any(
        [
            "never" in instruction_lower and "accept" in instruction_lower,
            "never" in instruction_lower and "approve" in instruction_lower,
            "cannot" in instruction_lower and "accept" in instruction_lower,
            "may not" in instruction_lower and "accept" in instruction_lower,
            "must not" in instruction_lower and "accept" in instruction_lower,
        ]
    )

    assert has_prohibition, (
        "Instruction must forbid the LLM from minting an acceptance "
        "(should contain phrases like 'never accept', 'cannot approve', etc.)"
    )

    # Should describe the two-step flow (extract then gate)
    assert "first" in instruction_lower or "then" in instruction_lower, (
        "Instruction should describe the two-step extract→gate flow"
    )


def test_build_extraction_agent():
    """build_extraction_agent() returns an LlmAgent with extract_document tool."""
    agent = build_extraction_agent()

    # Should be an LlmAgent
    assert isinstance(agent, LlmAgent)

    # Should be named extraction_agent
    assert agent.name == "extraction_agent"

    # Should have tools
    assert hasattr(agent, "tools")
    assert len(agent.tools) > 0

    # Should include a tool named extract_document
    tool_names = [tool.name for tool in agent.tools]
    assert "extract_document" in tool_names


def test_build_runner_artifact_service():
    """_build_runner() returns a Runner with InMemoryArtifactService."""
    runner = _build_runner()

    # Should have an artifact_service
    assert hasattr(runner, "artifact_service")
    assert isinstance(runner.artifact_service, InMemoryArtifactService)


def test_build_app_is_resumable_by_default():
    """build_app() carries ResumabilityConfig(is_resumable=True); opt-out is explicit."""
    assert build_app().resumability_config.is_resumable is True
    assert build_app(is_resumable=False).resumability_config.is_resumable is False


def test_build_document_bridge_accepts_base_llm():
    """A BaseLlm instance is used as-is and shared with the extraction sub-agent."""
    from tests._scripted_llm import ScriptedLlm

    llm = ScriptedLlm()
    agent = build_document_bridge(llm)
    assert agent.model is llm
    (extraction_tool,) = [t for t in agent.tools if t.name == "extraction_agent"]
    assert extraction_tool.agent.model is llm

    named = build_document_bridge("x-model")
    assert named.model == "x-model"
    (named_tool,) = [t for t in named.tools if t.name == "extraction_agent"]
    assert named_tool.agent.model == "x-model"


def test_root_agent_pins_gate_args():
    """The gate inversion is the agent's own invariant: the root carries the gate
    pin as its before_tool_callback (always on); the extraction sub-agent has none
    (it offers no gate tool)."""
    from bridge.gatepin import pin_gate_args

    agent = build_document_bridge()
    assert agent.before_tool_callback is pin_gate_args
    (extraction_tool,) = [t for t in agent.tools if t.name == "extraction_agent"]
    assert extraction_tool.agent.before_tool_callback is None
    assert "a gate call made before any extraction is refused" in INSTRUCTION


def test_extract_document_declaration_hides_tool_context():
    """The model sees only ``artifact_name``; ADK injects ``tool_context``."""
    agent = build_extraction_agent()
    (tool,) = [t for t in agent.tools if t.name == "extract_document"]
    decl = tool._get_declaration()
    schema = decl.parameters_json_schema or decl.parameters.model_dump(
        exclude_none=True
    )
    assert set(schema["properties"]) == {"artifact_name"}
