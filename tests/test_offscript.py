"""Tests for off-script routing tools (no model calls)."""

from google.adk.artifacts import InMemoryArtifactService
from google.adk.tools import LongRunningFunctionTool
from google.genai import types

from bridge.agent import INSTRUCTION, build_document_bridge
from bridge.offscript import (
    REASON_ESCALATED,
    REASON_OFF_SCRIPT_REJECT,
    REASON_UNROUTED,
    USER_NAMESPACE_PREFIX,
    _erase_artifact,
    escalate_submission,
    reject_submission,
    request_human_review,
)


def test_reject_submission_pure_ack():
    """reject_submission returns action/reason_code/message with the supplied reason."""
    result = reject_submission("looks like a selfie")

    assert result["action"] == "rejected"
    assert result["reason_code"] == REASON_OFF_SCRIPT_REJECT
    assert "looks like a selfie" in result["message"]


def test_escalate_submission_pure_ack():
    """escalate_submission returns action/reason_code/message with the supplied reason."""
    result = escalate_submission("possible fraud")

    assert result["action"] == "escalated"
    assert result["reason_code"] == REASON_ESCALATED
    assert "possible fraud" in result["message"]


async def test_erase_artifact_deletes_existing():
    """_erase_artifact performs a real delete: artifact gone from list_artifact_keys after."""
    svc = InMemoryArtifactService()

    # Save an artifact
    artifact_part = types.Part.from_bytes(data=b"fake bill image", mime_type="image/png")
    await svc.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="test_session",
        filename="bill.png",
        artifact=artifact_part,
    )

    # Verify it exists
    keys_before = await svc.list_artifact_keys(
        app_name="test_app", user_id="test_user", session_id="test_session",
    )
    assert "bill.png" in keys_before

    # Erase it
    erased = await _erase_artifact(
        svc,
        app_name="test_app",
        user_id="test_user",
        session_id="test_session",
        filename="bill.png",
    )

    # Should return True (deleted) and artifact should be gone
    assert erased is True

    keys_after = await svc.list_artifact_keys(
        app_name="test_app", user_id="test_user", session_id="test_session",
    )
    assert "bill.png" not in keys_after


async def test_erase_artifact_missing_file():
    """_erase_artifact on missing filename returns False and lists are unchanged."""
    svc = InMemoryArtifactService()

    # Don't save anything

    # Attempt to erase a nonexistent file
    erased = await _erase_artifact(
        svc,
        app_name="test_app",
        user_id="test_user",
        session_id="test_session",
        filename="nonexistent.pdf",
    )

    # Should return False (nothing to delete)
    assert erased is False

    # Keys should still be empty
    keys = await svc.list_artifact_keys(
        app_name="test_app", user_id="test_user", session_id="test_session",
    )
    assert len(keys) == 0


async def test_erase_artifact_refuses_user_namespace():
    """A ``user:``-namespaced artifact spans every session of the party, so it is
    never inside one leg: _erase_artifact refuses it from another session AND from
    the saving session itself (deliberate — the erase is confined to one leg)."""
    svc = InMemoryArtifactService()
    await svc.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="s1",
        filename="user:bill.pdf",
        artifact=types.Part.from_bytes(data=b"bill", mime_type="application/pdf"),
    )
    # The hole this closes: ADK lists the user: key under every session of the party.
    assert "user:bill.pdf" in await svc.list_artifact_keys(
        app_name="test_app", user_id="test_user", session_id="s2",
    )

    for session_id in ("s2", "s1"):
        erased = await _erase_artifact(
            svc,
            app_name="test_app",
            user_id="test_user",
            session_id=session_id,
            filename="user:bill.pdf",
        )
        assert erased is False

    still = await svc.load_artifact(
        app_name="test_app", user_id="test_user", session_id="s1",
        filename="user:bill.pdf",
    )
    assert still is not None and still.inline_data.data == b"bill"


def test_unrouted_reason_code_and_user_namespace_prefix():
    """The model-text-only reason code (D6) and ADK's user-namespace marker."""
    assert REASON_UNROUTED == "off_script_unrouted"
    assert USER_NAMESPACE_PREFIX == "user:"


def test_offscript_tools_wired_into_agent():
    """build_document_bridge() tools includes the three off-script tools."""
    agent = build_document_bridge()

    tool_names = [t.name for t in agent.tools]

    # Should have all three off-script tools
    assert "handle_deletion_request" in tool_names
    assert "reject_submission" in tool_names
    assert "escalate_submission" in tool_names

    # Should still have the original tools
    assert "extraction_agent" in tool_names
    assert "run_disposition_gate" in tool_names


def test_instruction_mentions_offscript_tools_and_preserves_guardrail():
    """INSTRUCTION mentions all three off-script tools and still has the inversion guardrail."""
    instruction = INSTRUCTION

    # Should mention all three off-script tools by name
    assert "handle_deletion_request" in instruction
    assert "reject_submission" in instruction
    assert "escalate_submission" in instruction

    # Should still mention the gate
    assert "run_disposition_gate" in instruction

    # Should still forbid the LLM from overruling the gate
    instruction_lower = instruction.lower()
    has_prohibition = any([
        "never" in instruction_lower and "accept" in instruction_lower,
        "never" in instruction_lower and "approve" in instruction_lower,
        "cannot" in instruction_lower and "accept" in instruction_lower,
        "may not" in instruction_lower and "accept" in instruction_lower,
        "must not" in instruction_lower and "accept" in instruction_lower,
    ])

    assert has_prohibition, (
        "Instruction must still forbid the LLM from overruling the gate "
        "(should contain phrases like 'never accept', 'may never overrule', etc.)"
    )


def test_request_human_review_returns_none_to_pause():
    """request_human_review returns ``None`` — the only return that pauses an ADK
    LongRunningFunctionTool (S3-edge-6 D-a); the pending status is edge-authored."""
    assert request_human_review("expiry field flagged") is None
    assert request_human_review("expiry field flagged", "bill.pdf") is None


def test_request_human_review_wired_as_long_running():
    """build_document_bridge wires request_human_review as a LongRunningFunctionTool."""
    agent = build_document_bridge()

    # Find the request_human_review tool
    hitl_tool = None
    for tool in agent.tools:
        if tool.name == "request_human_review":
            hitl_tool = tool
            break

    # Should be wired and be a LongRunningFunctionTool
    assert hitl_tool is not None, "request_human_review should be wired into the agent"
    assert isinstance(hitl_tool, LongRunningFunctionTool), (
        "request_human_review should be a LongRunningFunctionTool, not a plain FunctionTool"
    )
    assert hitl_tool.is_long_running is True, (
        "The tool's is_long_running flag should be True (zero-compute pause)"
    )

    # Pre-existing tools should still be present (no regression)
    tool_names = [t.name for t in agent.tools]
    assert "run_disposition_gate" in tool_names
    assert "extraction_agent" in tool_names
    assert "escalate_submission" in tool_names


def test_instruction_routes_hitl_review_and_preserves_inversion():
    """INSTRUCTION mentions request_human_review and hitl_review routing; inversion preserved."""
    instruction = INSTRUCTION

    # Should mention the HITL tool and the gate verdict
    assert "request_human_review" in instruction
    assert "hitl_review" in instruction

    # Should still mention all the other tools
    assert "run_disposition_gate" in instruction
    assert "escalate_submission" in instruction

    # Should still forbid overruling the gate (same check as the existing test)
    instruction_lower = instruction.lower()
    has_prohibition = any([
        "never" in instruction_lower and ("accept" in instruction_lower or "approve" in instruction_lower),
        "may never overrule" in instruction_lower,
    ])
    assert has_prohibition, "Instruction must still forbid overruling run_disposition_gate"


def test_id_matched_resume_shape():
    """Id-matched FunctionResponse resume shape round-trips correctly."""
    # Build the reviewer's FunctionResponse Content (the resume message)
    call_id = "call-123"
    call_name = "request_human_review"
    resolution = {"resolution": "accepted", "reviewer": "ops@example.com", "notes": "Document verified"}

    resume_content = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=call_name,
                    response=resolution,
                )
            )
        ]
    )

    # Verify it round-trips
    assert resume_content.role == "user"
    assert len(resume_content.parts) == 1

    fn_response = resume_content.parts[0].function_response
    assert fn_response.id == call_id
    assert fn_response.name == call_name
    assert fn_response.response["resolution"] == "accepted"
    assert fn_response.response["reviewer"] == "ops@example.com"
