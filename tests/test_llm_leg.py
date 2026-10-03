"""Tests for the LLM-leg runtime (S3-edge-2) — model-free via ``ScriptedLlm``.

Covers: the D4 projection reads tool function responses only (never model text);
the D9 session key == artifact key (real erase, cross-context isolation, party
resolution); the App is resumable and a fresh Runner is built per invocation; the
sessions come from the seam (durable, no hardcoded InMemory); the
``ResumabilityConfig`` import is the registered TEST-4 FQN; Decision 2 holds (no
``google.adk.a2a`` import in ``bridge/``).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.genai import types

from bridge import gatepin
from bridge.agent import build_app
from bridge.collect import DocumentSubmission
from bridge.disposition import run_disposition_gate
from bridge.edge import artifacts, llm_leg
from bridge.edge.llm_leg import (
    LlmLeg,
    SessionKey,
    project_from_events,
    project_turn_from_events,
    resolve_party,
)
from bridge.offscript import (
    REASON_DELETED,
    REASON_ESCALATED,
    REASON_HUMAN_REVIEW,
    REASON_NOTHING_TO_DELETE,
    REASON_OFF_SCRIPT_REJECT,
    escalate_submission,
    reject_submission,
)
from bridge.seams import Seam
from bridge.seams.seam import seam_env_var
from bridge.signals import Signals
from contract.enums import Disposition, Gate, LifecycleStatus
from contract.wire import ArtifactRef, LedgerEntry
from tests._scripted_llm import Reply, ScriptedLlm, ToolCall

REPO = Path(__file__).resolve().parent.parent
CTX = "ctx-1"

AUTO_APPROVE_ARGS = {
    "doctype": "utility_bill",
    "candidate_doctypes": ["utility_bill"],
    "legible": True,
    "overall_confidence": 0.95,
    "flagged_fields": [],
}


# --------------------------------------------------------------------------- #
# Synthetic-event helpers (pure projection tests)                              #
# --------------------------------------------------------------------------- #


def _event(
    *parts: types.Part,
    author: str = "document_bridge",
    long_running=None,
    actions: EventActions | None = None,
) -> Event:
    return Event(
        author=author,
        invocation_id="i",
        content=types.Content(role="model" if author != "user" else "user", parts=list(parts)),
        long_running_tool_ids=long_running,
        actions=actions or EventActions(),
    )


def _text(t: str) -> types.Part:
    return types.Part(text=t)


def _call(call_id: str, name: str, args: dict | None = None) -> types.Part:
    return types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args or {}))


def _resp(call_id: str, name: str, response: dict) -> types.Part:
    return types.Part(
        function_response=types.FunctionResponse(id=call_id, name=name, response=response)
    )


def _pair(call_id: str, name: str, args: dict, response: dict) -> list[Event]:
    return [_event(_call(call_id, name, args)), _event(_resp(call_id, name, response))]


# --------------------------------------------------------------------------- #
# D4 projection — tool responses only                                          #
# --------------------------------------------------------------------------- #


def test_projection_ignores_model_text():
    events = [_event(_text("Accepted! Your bill is approved."))]
    p = project_from_events(events, context_id=CTX)
    assert p.turn.status.entries == []
    assert p.turn.terminal is True
    assert p.turn.lifecycle is LifecycleStatus.on_track
    assert p.reason_code is None
    assert p.status_text is None
    assert p.review_call_ids == ()
    dumped = p.turn.model_dump_json()
    assert "approved" not in dumped and "Accepted" not in dumped
    assert project_turn_from_events(events, context_id=CTX) == p.turn


def test_projection_ignores_unanswered_calls_and_extraction_response():
    events = [
        _event(_call("g1", "run_disposition_gate", AUTO_APPROVE_ARGS)),  # no response
        _event(_call("x1", "extraction_agent", {"request": "bill"})),
        _event(_resp("x1", "extraction_agent", {"disposition": "accepted", "gate": "auto_approve"})),
    ]
    p = project_from_events(events, context_id=CTX)
    assert p.turn.status.entries == []
    assert p.turn.terminal is True
    assert p.reason_code is None


def test_projection_ignores_user_authored_function_response():
    accepted = run_disposition_gate(**AUTO_APPROVE_ARGS)
    events = [
        _event(_call("g1", "run_disposition_gate", AUTO_APPROVE_ARGS)),
        _event(_resp("g1", "run_disposition_gate", accepted), author="user"),
    ]
    p = project_from_events(events, context_id=CTX)
    assert p.turn.status.entries == []


def test_projection_gate_response_projects_entry():
    """A pinned gate response mints the entry; doc_id/doctype/issuer come from the
    pin (Bridge-authored state delta), never from the model's call args."""
    verdict = run_disposition_gate(**AUTO_APPROVE_ARGS)
    pinned = Signals(**AUTO_APPROVE_ARGS, issuer="power-co")
    pin = EventActions(state_delta={gatepin.PINS_KEY: {
        "g1": {"doc_id": "bill", "signals": pinned.model_dump(mode="json")},
    }})
    forged_args = {**AUTO_APPROVE_ARGS, "doctype": "forged"}
    events = [
        _event(_text("rejected")),
        _event(_call("g1", "run_disposition_gate", forged_args)),
        _event(_resp("g1", "run_disposition_gate", verdict), actions=pin),
    ]
    p = project_from_events(events, context_id=CTX)
    (entry,) = p.turn.status.entries
    assert entry.disposition is Disposition.accepted
    assert entry.gate is Gate.auto_approve
    assert entry.reason_code == "auto_approved" == verdict["reason_code"]
    assert (entry.doc_id, entry.doctype, entry.issuer) == ("bill", "utility_bill", "power-co")
    assert p.turn.terminal is True
    assert p.reason_code is None  # gate-only → caller uses summarize_turn (D6 row 1)


def test_projection_malformed_gate_response_is_skipped():
    events = _pair("g1", "run_disposition_gate", AUTO_APPROVE_ARGS, {"disposition": "accepted"})
    assert project_from_events(events, context_id=CTX).turn.status.entries == []


@pytest.mark.parametrize(
    ("tool", "args", "response", "reason", "terminal", "lifecycle"),
    [
        (
            "handle_deletion_request",
            {"artifact_name": "bill.pdf"},
            {"action": "deleted", "reason_code": REASON_DELETED, "message": "erased"},
            REASON_DELETED,
            True,
            LifecycleStatus.on_track,
        ),
        (
            "handle_deletion_request",
            {"artifact_name": "bill.pdf"},
            {"action": "deleted", "reason_code": REASON_NOTHING_TO_DELETE, "message": "none"},
            REASON_NOTHING_TO_DELETE,
            True,
            LifecycleStatus.on_track,
        ),
        (
            "reject_submission",
            {"reason": "selfie"},
            reject_submission("selfie"),
            REASON_OFF_SCRIPT_REJECT,
            True,
            LifecycleStatus.on_track,
        ),
        (
            "escalate_submission",
            {"reason": "odd"},
            escalate_submission("odd"),
            REASON_ESCALATED,
            False,
            LifecycleStatus.escalated,
        ),
    ],
)
def test_projection_offscript_rows(tool, args, response, reason, terminal, lifecycle):
    p = project_from_events(_pair("c1", tool, args, response), context_id=CTX)
    assert p.turn.status.entries == []
    assert not any(e.disposition is Disposition.rejected for e in p.turn.status.entries)
    assert p.reason_code == reason
    assert p.status_text == response["message"]
    assert p.turn.terminal is terminal
    assert p.turn.lifecycle is lifecycle


def _seed() -> list[LedgerEntry]:
    return [
        LedgerEntry(
            doc_id="bill",
            doctype="utility_bill",
            disposition=Disposition.pending,
            gate=Gate.hitl_review,
            issuer="power-co",
            reason_code="flagged_fields",
            message="The following fields require human review: name.",
            artifact=ArtifactRef(uri="http://bridge/artifacts/ctx-1/bill.pdf?v=0", name="bill.pdf"),
        ),
        LedgerEntry(
            doc_id="id",
            doctype="drivers_license",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            reason_code="auto_approved",
        ),
    ]


def _legacy_review_ack(reason: str) -> dict:
    """A completed (dict) ``request_human_review`` response — the pre-S3-edge-6 tool
    return. The tool now returns ``None`` (the paused shape); the completed-response
    projection path stays covered with this inline payload."""
    return {
        "action": "awaiting_human_review",
        "reason_code": REASON_HUMAN_REVIEW,
        "message": f"This submission was sent for human review: {reason}",
    }


def test_projection_review_pending_from_seed():
    seed = _seed()
    response = _legacy_review_ack("flagged name")
    events = _pair("r1", "request_human_review", {"reason": "flagged name", "artifact_name": "bill.pdf"}, response)
    p = project_from_events(events, context_id=CTX, seed_entries=seed)
    bill, other = p.turn.status.entries
    assert bill.doc_id == "bill"
    assert bill.disposition is Disposition.pending
    assert bill.gate is Gate.hitl_review
    assert bill.reason_code == REASON_HUMAN_REVIEW
    assert bill.message == response["message"]
    assert bill.doctype == "utility_bill" and bill.issuer == "power-co"
    assert other == seed[1]
    assert p.turn.terminal is False
    assert p.review_call_ids == ("r1",)
    assert p.reason_code == REASON_HUMAN_REVIEW
    assert p.status_text == response["message"]


def test_projection_review_matches_artifact_uri_basename():
    seed = [_seed()[0].model_copy(update={"artifact": ArtifactRef(uri="http://h/x/bill.pdf")})]
    events = _pair("r1", "request_human_review", {"reason": "r", "artifact_name": "bill.pdf"}, _legacy_review_ack("r"))
    (bill,) = project_from_events(events, context_id=CTX, seed_entries=seed).turn.status.entries
    assert bill.reason_code == REASON_HUMAN_REVIEW


def test_projection_paused_lrft_call_is_pending():
    args = {"reason": "flagged", "artifact_name": "bill.pdf"}
    paused = [_event(_call("r1", "request_human_review", args), long_running={"r1"})]
    p = project_from_events(paused, context_id=CTX, seed_entries=_seed())
    assert p.turn.terminal is False
    assert p.review_call_ids == ("r1",)
    assert p.turn.status.entries[0].disposition is Disposition.pending
    assert p.turn.status.entries[0].reason_code == REASON_HUMAN_REVIEW
    assert p.status_text == llm_leg.PAUSED_REVIEW_MESSAGE

    unflagged = [_event(_call("r1", "request_human_review", args))]
    q = project_from_events(unflagged, context_id=CTX)
    assert q.turn.terminal is True
    assert q.review_call_ids == ()


def test_projection_precedence():
    esc_then_del = [
        *_pair("e1", "escalate_submission", {"reason": "x"}, escalate_submission("x")),
        *_pair("d1", "handle_deletion_request", {"artifact_name": "a"},
               {"action": "deleted", "reason_code": REASON_DELETED, "message": "erased"}),
    ]
    p = project_from_events(esc_then_del, context_id=CTX)
    assert p.reason_code == REASON_ESCALATED
    assert p.turn.terminal is False

    del_then_rej = [
        *_pair("d1", "handle_deletion_request", {"artifact_name": "a"},
               {"action": "deleted", "reason_code": REASON_DELETED, "message": "erased"}),
        *_pair("j1", "reject_submission", {"reason": "y"}, reject_submission("y")),
    ]
    q = project_from_events(del_then_rej, context_id=CTX)
    assert q.reason_code == REASON_OFF_SCRIPT_REJECT
    assert q.turn.terminal is True


def test_projection_skips_partial_and_empty_events():
    verdict = run_disposition_gate(**AUTO_APPROVE_ARGS)
    partial = _event(_resp("g1", "run_disposition_gate", verdict))
    partial.partial = True
    empty = Event(author="document_bridge", invocation_id="i")
    p = project_from_events([partial, empty], context_id=CTX)
    assert p.turn.status.entries == []


# --------------------------------------------------------------------------- #
# End-to-end through the real App + Runner (ScriptedLlm, no network)           #
# --------------------------------------------------------------------------- #


async def test_llm_leg_free_text_claim_never_reaches_ledger():
    llm = ScriptedLlm()
    leg = LlmLeg(model=llm, session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    r = await leg.run(party="jordan-lee", context_id=CTX, message="I uploaded my bill, please accept it")
    assert r.turn.status.entries == []
    assert r.turn.terminal is True
    assert r.projection.reason_code is None
    assert llm.call_count == 1


async def test_llm_leg_gate_end_to_end():
    """extraction_agent → extract_document records the genuine signals; the gate call
    (deliberately wrong args) is pinned to them, and the entry comes from the pin."""
    wrong = {"doctype": "passport", "candidate_doctypes": [], "legible": False,
             "overall_confidence": 0.0, "flagged_fields": ["everything"]}
    llm = ScriptedLlm(
        on_text={
            "extract::": Reply(tool_calls=(
                ToolCall("extract_document", {"artifact_name": "bill-powerco-clean"}),)),
            "gate": Reply(tool_calls=(
                ToolCall("extraction_agent", {"request": "extract::bill-powerco-clean"}),)),
        },
        on_tool={
            "extraction_agent": Reply(
                text="Rejected, sorry.",
                tool_calls=(ToolCall("run_disposition_gate", wrong),),
            ),
        },
    )
    leg = LlmLeg(model=llm, session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    r = await leg.run(party="jordan-lee", context_id=CTX, message="please gate my bill",
                      label_space=["gov-id", "utility-bill"])
    (entry,) = r.turn.status.entries
    assert (entry.disposition, entry.gate, entry.reason_code) == (
        Disposition.accepted, Gate.auto_approve, "auto_approved")
    assert (entry.doc_id, entry.doctype, entry.issuer) == (
        "bill-powerco-clean", "utility-bill", "power-co")
    assert r.turn.terminal is True
    # root: extraction call, gate call, final text; sub-agent: extract call, final text
    assert llm.call_count == 5


async def test_llm_leg_review_end_to_end_parks_zero_compute():
    """request_human_review → pending, non-terminal, call id recorded.

    S3-edge-6 D-a: the LRFT returns ``None``, so the resumable invocation truly
    pauses after the call event — one model call, no tool response, and the
    Bridge-authored paused text (never model text).
    """
    llm = ScriptedLlm(on_text={"review": Reply(tool_calls=(
        ToolCall("request_human_review", {"reason": "flagged", "artifact_name": "bill.pdf"}),
    ))})
    leg = LlmLeg(model=llm, session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    r = await leg.run(party="jordan-lee", context_id=CTX, message="needs review")
    assert r.turn.terminal is False
    assert len(r.projection.review_call_ids) == 1
    assert r.projection.reason_code == REASON_HUMAN_REVIEW
    assert r.projection.status_text == llm_leg.PAUSED_REVIEW_MESSAGE
    assert llm.call_count == 1


async def test_scripted_llm_rejects_unoffered_tool():
    llm = ScriptedLlm(on_text={"x": Reply(tool_calls=(ToolCall("no_such_tool"),))})
    leg = LlmLeg(model=llm, session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    with pytest.raises(ValueError, match="not offered"):
        await leg.run(party="p", context_id=CTX, message="x")


# --------------------------------------------------------------------------- #
# D9 — session key == artifact key                                             #
# --------------------------------------------------------------------------- #


def test_app_name_lockstep():
    assert build_app().name == artifacts.APP_NAME == llm_leg.APP_NAME == "document_bridge"


async def _persist_bill(store, *, party: str = "jordan-lee", context_id: str = CTX) -> None:
    version = await artifacts.persist_submission(
        store,
        party=party,
        context_id=context_id,
        submission=DocumentSubmission(
            doc_id="bill", artifact_name="bill.pdf",
            artifact=ArtifactRef(uri="bill.pdf", name="bill.pdf"),
        ),
        resolver=lambda uri: (b"%PDF-1.4 synthetic", "application/pdf"),
    )
    assert version == 0


def _delete_llm() -> ScriptedLlm:
    return ScriptedLlm(on_text={"delete": Reply(
        tool_calls=(ToolCall("handle_deletion_request", {"artifact_name": "bill.pdf"}),),
    )})


async def _keys(store, *, user_id: str = "jordan-lee", session_id: str = CTX) -> list[str]:
    return await store.list_artifact_keys(
        app_name="document_bridge", user_id=user_id, session_id=session_id)


async def test_session_key_equals_artifact_key_and_deletion_erases():
    store = InMemoryArtifactService()
    sessions = InMemorySessionService()
    await _persist_bill(store)
    assert await _keys(store) == ["bill.pdf"]
    leg = LlmLeg(model=_delete_llm(), artifact_store=store, session_service=sessions)
    r = await leg.run(party="jordan-lee", context_id=CTX, message="please delete bill.pdf")
    assert r.key == SessionKey("document_bridge", "jordan-lee", CTX)
    assert await sessions.get_session(
        app_name="document_bridge", user_id="jordan-lee", session_id=CTX) is not None
    assert await _keys(store) == []
    assert r.projection.reason_code == REASON_DELETED
    assert r.turn.status.entries == []
    assert r.turn.terminal is True


async def test_party_resolved_from_store_when_absent():
    store = InMemoryArtifactService()
    await _persist_bill(store)
    leg = LlmLeg(model=_delete_llm(), artifact_store=store,
                 session_service=InMemorySessionService())
    r = await leg.run(party=None, context_id=CTX, message="delete bill.pdf")
    assert r.key == SessionKey("document_bridge", "jordan-lee", CTX)
    assert r.projection.reason_code == REASON_DELETED
    assert await _keys(store) == []


@pytest.mark.parametrize("party", [None, "jordan-lee"])
async def test_cannot_erase_another_contexts_artifact(party):
    store = InMemoryArtifactService()
    await _persist_bill(store)
    leg = LlmLeg(model=_delete_llm(), artifact_store=store,
                 session_service=InMemorySessionService())
    r = await leg.run(party=party, context_id="ctx-2", message="delete bill.pdf")
    expected_user = party or artifacts.UNSCOPED_USER
    assert r.key == SessionKey("document_bridge", expected_user, "ctx-2")
    assert r.projection.reason_code == REASON_NOTHING_TO_DELETE
    assert await _keys(store) == ["bill.pdf"]


async def test_resolve_party_fails_closed_on_ambiguity():
    store = InMemoryArtifactService()
    await _persist_bill(store, party="jordan-lee")
    await _persist_bill(store, party="someone-else")
    assert artifacts.owning_parties(store, CTX) == {"jordan-lee", "someone-else"}
    assert resolve_party(store, party=None, context_id=CTX) == artifacts.UNSCOPED_USER
    assert resolve_party(store, party="jordan-lee", context_id=CTX) == "jordan-lee"
    assert resolve_party(store, party=None, context_id="ctx-none") == artifacts.UNSCOPED_USER


def test_session_key_requires_context_id():
    with pytest.raises(ValueError, match="context_id"):
        llm_leg.session_key(InMemoryArtifactService(), party="p", context_id="")


async def test_session_service_from_seam(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_SESSION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/s.db")
    monkeypatch.delenv("BRIDGE_SEAM_MODE", raising=False)
    monkeypatch.delenv(seam_env_var(Seam.sessions), raising=False)
    leg = LlmLeg(model=ScriptedLlm(), artifact_store=InMemoryArtifactService())
    assert isinstance(leg.session_service, DatabaseSessionService)
    r = await leg.run(party="jordan-lee", context_id=CTX, message="hello")
    assert r.turn.terminal is True

    other = LlmLeg()  # same env, nothing injected — never run (no model call)
    assert isinstance(other.session_service, DatabaseSessionService)
    session = await other.session_service.get_session(
        app_name="document_bridge", user_id="jordan-lee", session_id=CTX)
    assert session is not None
    authors = [e.author for e in session.events]
    assert "user" in authors and "document_bridge" in authors


async def test_ensure_session_is_idempotent():
    leg = LlmLeg(model=ScriptedLlm(), session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    key = SessionKey("document_bridge", "jordan-lee", CTX)
    first = await leg.ensure_session(key, state={"k": 1})
    second = await leg.ensure_session(key)
    assert first.id == second.id == CTX
    assert second.state.get("k") == 1


# --------------------------------------------------------------------------- #
# App resumable; fresh Runner per invocation                                   #
# --------------------------------------------------------------------------- #


def test_llm_leg_app_is_resumable():
    leg = LlmLeg(session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    assert leg.app.resumability_config.is_resumable is True
    r1, r2 = leg.runner(), leg.runner()
    assert r1.resumability_config.is_resumable is True
    assert r1 is not r2
    assert r1.artifact_service is leg.artifact_store
    assert r1.session_service is leg.session_service


def test_bind_store_shares_the_edge_store():
    leg = LlmLeg(session_service=InMemorySessionService(),
                 artifact_store=InMemoryArtifactService())
    shared = InMemoryArtifactService()
    leg.bind_store(shared)
    assert leg.artifact_store is shared
    assert leg.runner().artifact_service is shared


# --------------------------------------------------------------------------- #
# TEST-4 + Decision 2 (AST scans)                                              #
# --------------------------------------------------------------------------- #


def test_resumability_import_is_registered_fqn():
    from bridge.adk import registered_symbols

    tree = ast.parse((REPO / "bridge" / "agent.py").read_text())
    fqns = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    assert "google.adk.apps.app.ResumabilityConfig" in fqns
    assert "google.adk.apps.app.ResumabilityConfig" in registered_symbols()


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.append(node.module)
    return mods


def test_no_adk_a2a_executor_in_bridge():
    offenders = [
        (str(py.relative_to(REPO)), mod)
        for py in (REPO / "bridge").rglob("*.py")
        if "__pycache__" not in py.parts
        for mod in _imported_modules(py)
        if mod.startswith("google.adk.a2a")
    ]
    assert offenders == []
    leg_mods = _imported_modules(REPO / "bridge" / "edge" / "llm_leg.py")
    assert not [m for m in leg_mods if "a2a" in m.split(".")], leg_mods
