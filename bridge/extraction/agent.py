"""Extraction agent (ADK layer) — wraps the extraction seam as an AgentTool."""

import os

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.planners import BuiltInPlanner
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from bridge import gatepin
from bridge.classification import canonicalize_issuer
from bridge.seams import Seam, get_adapter, resolve_mode

DEFAULT_MODEL = "gemini-3.8-flash"
WORKHORSE_MODEL = DEFAULT_MODEL
REASONING_MODEL = "gemini-4-argon"

EXTRACTION_INSTRUCTION = """You are the extraction agent. Given a document artifact, call the extract_document tool and return its structured result. Do not judge acceptance — that is the gate's job."""


async def extract_document(
    artifact_name: str, tool_context: ToolContext | None = None
) -> dict:
    """Extract normalized fields and disposition signals from a document artifact.

    Args:
        artifact_name: The artifact filename or path to extract.
        tool_context: Injected by ADK (hidden from the model's declaration). When
            present, the genuine signals are recorded into session state under
            ``doc_id=artifact_name`` (``bridge.gatepin.record_extraction``) — the
            only source the gate pin reads; this tool's returned dict, and the
            ``extraction_agent``'s text, are never a gate input.

    Returns:
        A dict with classification, key_fields, and signals.
    """
    adapter = get_adapter(Seam.extraction, resolve_mode(Seam.extraction))
    result = await adapter.extract(artifact_name)
    if tool_context is not None:
        gatepin.record_extraction(
            tool_context.state, doc_id=artifact_name, result=result
        )
    return {
        "classification": {
            "doctype": result.fields.doctype,
            "issuer": canonicalize_issuer(result.fields.issuer),
        },
        "key_fields": result.fields.key_fields,
        "signals": {
            "legible": result.legible,
            "overall_confidence": result.overall_confidence,
            "flagged_fields": result.flagged_fields,
            "field_confidence": result.field_confidence,
        },
    }


def resolve_model(model: str | BaseLlm | None = None) -> str | BaseLlm:
    """Resolve the effective workhorse model: an explicit name or ``BaseLlm`` wins.

    ``None`` / ``""`` fall back to ``BRIDGE_MODEL`` env, else ``DEFAULT_MODEL``
    (``gemini-3.8-flash``). A ``BaseLlm`` instance (e.g. the model-free test double) is
    returned unchanged — tested with ``is None`` / ``== ""``, never truthiness, so an
    instance is never re-routed to a model name.
    """
    if model is None or (isinstance(model, str) and model == ""):
        return os.environ.get("BRIDGE_MODEL", DEFAULT_MODEL)
    return model


def resolve_reasoning_model(model: str | BaseLlm | None = None) -> str | BaseLlm:
    """Resolve the high-reasoning specialist model (``gemini-4-argon`` by default).

    Used for cross-document conflict adjudication, Agent Gym rule synthesis,
    independent autorater evaluation, and nightly ``SKILL.md`` compilation.
    """
    if model is None or (isinstance(model, str) and model == ""):
        return os.environ.get("BRIDGE_REASONING_MODEL", REASONING_MODEL)
    return model


def build_extraction_agent(model: str | BaseLlm | None = None) -> LlmAgent:
    """Build the extraction_agent LlmAgent with the extract_document tool.

    Args:
        model: Override model — a model name or a ``BaseLlm`` instance (defaults to
            BRIDGE_MODEL env or gemini-2.5-flash).

    Returns:
        An LlmAgent configured for extraction.
    """
    effective_model = resolve_model(model)
    return LlmAgent(
        name="extraction_agent",
        description="Extracts normalized document fields and disposition signals from a submitted artifact.",
        model=effective_model,
        mode="single_turn",
        instruction=EXTRACTION_INSTRUCTION,
        planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_budget=0)),
        tools=[FunctionTool(func=extract_document)],
    )
