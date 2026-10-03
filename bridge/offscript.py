"""Off-script intent routing tools (CORE-8).

The document_bridge LlmAgent routes the messy edges of real intake — a
deletion/erasure request, a wrong/unrelated file, an out-of-scope message —
to these tools. The flexibility lives ONLY on the edges: the KYC accept/reject
of a genuine candidate document stays behind run_disposition_gate, which the
LLM can never overrule. A new intent later is a new tool + a line of
instruction, not a new flow (design/adk.md).

Imports contract + google.adk.tools (ToolContext typing) + stdlib only —
NEVER agents/.
"""
from google.adk.artifacts import BaseArtifactService
from google.adk.tools import ToolContext

# Off-script reason codes (machine-readable; travel with the ack alongside a human message)
REASON_DELETED = "deletion_completed"
REASON_NOTHING_TO_DELETE = "nothing_to_delete"
REASON_OFF_SCRIPT_REJECT = "off_script_rejected"
REASON_ESCALATED = "escalated_for_review"
REASON_HUMAN_REVIEW = "awaiting_human_review"
# No off-script tool ran and nothing was disposed (the LLM leg's model-text-only
# outcome, D6).
REASON_UNROUTED = "off_script_unrouted"

# ADK's user-namespace marker: both ``InMemoryArtifactService`` and
# ``GcsArtifactService`` treat ``filename.startswith("user:")`` as a party-wide
# artifact. Such an artifact spans every session of the party, so it is never
# inside one leg.
USER_NAMESPACE_PREFIX = "user:"


async def _erase_artifact(
    artifact_service: BaseArtifactService, *,
    app_name: str, user_id: str, session_id: str, filename: str,
) -> bool:
    """Real erase of a document artifact's bytes (all versions) — not a soft flag.

    Returns True if the artifact existed and was deleted, False if there was
    nothing to erase. Kept ADK-service-only (no ToolContext) so it unit-tests
    against a real InMemoryArtifactService without a Runner/model.

    The erase is confined to the session's own keys (addressing = isolation,
    design/zones.md). A ``user:``-namespaced name is refused before any store
    call — even from the session that saved it — because it would reach the
    party's other contexts. Only a filename listed under exactly this
    ``(app, user, session)`` is deleted, so traversal-looking names
    (``../ctx-A/bill.pdf``) and other contexts' names are never found.
    """
    if filename.startswith(USER_NAMESPACE_PREFIX):
        return False
    keys = await artifact_service.list_artifact_keys(
        app_name=app_name, user_id=user_id, session_id=session_id,
    )
    if filename not in keys:
        return False
    await artifact_service.delete_artifact(
        app_name=app_name, user_id=user_id, session_id=session_id, filename=filename,
    )
    return True


async def handle_deletion_request(artifact_name: str, tool_context: ToolContext) -> dict:
    """Handle an off-script deletion/erasure request: really erase the document's bytes.

    Deletion is a real erase (ArtifactService.delete_artifact), never a soft flag
    (design/artifacts.md). Use ONLY for genuine erase requests — never to
    dispose of a candidate document (that is the gate's job).

    Args:
        artifact_name: The artifact filename to erase.

    Returns:
        A dict with action, reason_code, and a human message.
    """
    ctx = tool_context.get_invocation_context()
    svc = ctx.artifact_service
    session = ctx.session
    erased = False
    if svc is not None and session is not None:
        erased = await _erase_artifact(
            svc, app_name=session.app_name, user_id=session.user_id,
            session_id=session.id, filename=artifact_name,
        )
    if erased:
        return {
            "action": "deleted",
            "reason_code": REASON_DELETED,
            "message": f"The document '{artifact_name}' was permanently erased.",
        }
    return {
        "action": "deleted",
        "reason_code": REASON_NOTHING_TO_DELETE,
        "message": f"No document named '{artifact_name}' was found to erase.",
    }


def reject_submission(reason: str) -> dict:
    """Reject an OFF-SCRIPT submission — a wrong/unrelated file or out-of-scope message.

    This is NOT the compliance reject: a genuine candidate document is only ever
    accepted/rejected behind run_disposition_gate. Use this only when the
    submission is not a genuine candidate document at all.

    Args:
        reason: Short explanation of why the submission is off-script.

    Returns:
        A dict with action, reason_code, and a human message.
    """
    return {
        "action": "rejected",
        "reason_code": REASON_OFF_SCRIPT_REJECT,
        "message": f"This submission can't be processed for this request: {reason}",
    }


def escalate_submission(reason: str) -> dict:
    """Escalate an off-script/ambiguous submission for human review.

    Use this for off-script ambiguous intents that need a person's eyes — a
    message that isn't a clean candidate document but requires review. For
    disposition-driven HITL (flagged field / mid-confidence extraction),
    request_human_review is the platform-native pause.

    Args:
        reason: Short explanation of why this needs a human.

    Returns:
        A dict with action, reason_code, and a human message.
    """
    return {
        "action": "escalated",
        "reason_code": REASON_ESCALATED,
        "message": f"This submission was escalated for human review: {reason}",
    }


def request_human_review(reason: str, artifact_name: str = "") -> None:
    """Park a submission for human review (platform-native HITL pause).

    Wrapped as a LongRunningFunctionTool: ADK suspends the invocation with ZERO
    compute after this returns, and a later id-matched FunctionResponse carrying the
    reviewer's resolution resumes it (design/adk.md). This records the review
    intent only — it NEVER accepts/rejects a document; run_disposition_gate remains
    the sole disposition authority and its verdict is what routed here.

    Returns ``None`` on purpose: in ADK a LongRunningFunctionTool pauses a resumable
    invocation ONLY when it returns ``None`` — the call event then carries
    ``long_running_tool_ids`` with no function response and no further model call is
    made. A non-``None`` return would be fed straight back to the model (no park).
    The pending status the party sees is Bridge-authored by the edge, never this
    tool's or the model's text.

    Args:
        reason: Short explanation of why human review is needed.
        artifact_name: Optional artifact filename under review.

    Returns:
        None — the invocation pauses until the reviewer's resolution arrives.
    """
    return None
