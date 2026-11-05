"""Path-B extraction graph (PATH-B) — the deterministic, durable-friendly state
machine the human portal drives.

Graph shape (``design/extraction.md``)::

    receive_document → extract → quality_gate ─┬─ pass ─→ confidence_gate ─┬─ auto_approve → ACCEPT
                                               │                          └─ hitl_review → HITL (park)
                                               └── quality-gate fail ──┬─ attempts <= cap → RESUBMIT (park)
                                                                       └─ attempts  > cap → ESCALATE (park)
    (wrong doctype ─→ unsupported ─→ REJECT)

This is a **Bridge-side** engine: pure (no ``google.adk``, no ``a2a``) so it unit-tests
without a Runner/model, reusing ``bridge.signals`` + ``bridge.disposition`` (the sole
disposition authority — the graph routes on ``verdict.gate``, it never re-decides).

**Escalation is NOT rejection (§2a / ``design/a2a-edge.md``).** When extraction
keeps failing to *read* a document, Path B routes it to a **human** (``escalated``,
disposition ``PENDING``) — never ``rejected``. "The document was never judged wrong; the
Bridge just could not read it." Only a wrong doctype (``Gate.unsupported``) is a
terminal reject — that is a judgment, a different branch from the illegibility loop.

**The ``max_resubmissions`` counting boundary (§2b).** ``attempts`` = number of
documents that have failed the quality gate so far (starts 0). On each quality-gate
failure: increment ``attempts``; if ``attempts <= max_resubmissions`` → park-resubmit;
else → escalate. With ``max_resubmissions == 3`` the sequence is::

    fail#1 → RESUBMIT   (attempts 1)
    fail#2 → RESUBMIT   (attempts 2)
    fail#3 → RESUBMIT   (attempts 3, still at the cap)
    fail#4 → ESCALATE   (attempts 4, exceeds the cap → route to human, PENDING)

**Durability is the host's job (P4 machinery + P6-2), not the graph's.** ``step`` is a
single node-traversal returning a small serializable ``PathBState`` (attempts, ledger)
the host persists across the ``INPUT_REQUIRED`` park; the *loop* (feed the next
resubmitted doc) is the caller's, threading ``PathBStep.state``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from bridge.collect import project_ledger_entry
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    DispositionThresholds,
    DispositionVerdict,
)
from bridge.offscript import REASON_ESCALATED, REASON_HUMAN_REVIEW
from bridge.ports import Port, get_adapter, resolve_mode
from bridge.ports.protocols import ExtractionPort
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import Disposition, Gate
from contract.wire import ArtifactRef, LedgerEntry

ESCALATE_MESSAGE = (
    "This document could not be read after repeated attempts and was sent "
    "for human review."
)


class PathBAction(str, Enum):
    """The routing decision a single graph traversal emits."""

    ACCEPT = "accept"  # auto_approve -> terminal accepted (this doc slot)
    REJECT = "reject"  # unsupported doctype -> terminal rejected (a judgment)
    RESUBMIT = "resubmit"  # quality gate, attempts <= cap -> park for a fresh document
    HITL = "hitl"  # confidence gate -> park for human review (pending)
    ESCALATE = (
        "escalate"  # quality gate over cap -> route to human (pending, NOT rejected)
    )


@dataclass(frozen=True)
class PathBState:
    """Durable-friendly graph state for one requested-document slot in an exchange."""

    candidate_doctypes: tuple[str, ...]
    max_resubmissions: int = 3
    thresholds: DispositionThresholds = DEFAULT_THRESHOLDS
    attempts: int = 0  # quality-gate failures so far (§2b)
    ledger: tuple[LedgerEntry, ...] = ()  # every version seen, newest last


@dataclass(frozen=True)
class PathBStep:
    """Outcome of feeding one document into the graph (one node-traversal)."""

    action: PathBAction
    verdict: DispositionVerdict
    entry: LedgerEntry
    reason_code: str
    message: str
    state: PathBState  # updated state (attempts, ledger) to thread on
    terminal: bool  # ACCEPT/REJECT True; RESUBMIT/HITL/ESCALATE False


async def step(
    state: PathBState,
    *,
    doc_id: str,
    artifact_name: str,
    artifact: ArtifactRef | None = None,
    extractor: ExtractionPort | None = None,
) -> PathBStep:
    """extract → dispose → route. One node-traversal of the Path-B graph.

    Resolves the extraction port lazily (like ``bridge.collect.Collector``) unless
    injected. Appends a ``LedgerEntry`` (reusing ``project_ledger_entry``). Routes on
    ``verdict.gate`` per §2a/§2b. RESUBMIT/HITL/ESCALATE are non-terminal parks;
    ACCEPT/REJECT are terminal.
    """
    ext = extractor or get_adapter(Port.extraction, resolve_mode(Port.extraction))
    result = await ext.extract(artifact_name)
    signals = signals_from_result(
        result, candidate_doctypes=list(state.candidate_doctypes)
    )
    verdict = dispose_signals(signals, state.thresholds)
    entry = project_ledger_entry(
        doc_id=doc_id, result=result, verdict=verdict, artifact=artifact
    )
    ledger = (*state.ledger, entry)

    if verdict.gate is Gate.auto_approve:
        return PathBStep(
            action=PathBAction.ACCEPT,
            verdict=verdict,
            entry=entry,
            reason_code=verdict.reason_code,
            message=verdict.message,
            state=replace(state, ledger=ledger),
            terminal=True,
        )

    if verdict.gate is Gate.unsupported:
        return PathBStep(
            action=PathBAction.REJECT,
            verdict=verdict,
            entry=entry,
            reason_code=verdict.reason_code,
            message=verdict.message,
            state=replace(state, ledger=ledger),
            terminal=True,
        )

    if verdict.gate is Gate.hitl_review:
        return PathBStep(
            action=PathBAction.HITL,
            verdict=verdict,
            entry=entry,
            reason_code=REASON_HUMAN_REVIEW,
            message=verdict.message,
            state=replace(state, ledger=ledger),
            terminal=False,
        )

    # Gate.resubmit -> quality-gate failure: count, then park or escalate (§2a/§2b).
    attempts = state.attempts + 1
    if attempts <= state.max_resubmissions:
        return PathBStep(
            action=PathBAction.RESUBMIT,
            verdict=verdict,
            entry=entry,
            reason_code=verdict.reason_code,
            message=verdict.message,
            state=replace(state, ledger=ledger, attempts=attempts),
            terminal=False,
        )

    # Cap exceeded -> escalate to a human: PENDING, route-to-human, NOT rejected (§2a).
    esc_verdict = verdict.model_copy(update={"disposition": Disposition.pending})
    esc_entry = project_ledger_entry(
        doc_id=doc_id, result=result, verdict=esc_verdict, artifact=artifact
    )
    ledger_esc = (*state.ledger, esc_entry)
    return PathBStep(
        action=PathBAction.ESCALATE,
        verdict=esc_verdict,
        entry=esc_entry,
        reason_code=REASON_ESCALATED,
        message=ESCALATE_MESSAGE,
        state=replace(state, ledger=ledger_esc, attempts=attempts),
        terminal=True,
    )


def resolve_hitl(
    step_result: PathBStep, resolution: Disposition, *, message: str
) -> PathBStep:
    """Finalize a HITL/ESCALATE park with the reviewer's resolution.

    The reviewer — not the gate — makes the HITL call (timeline exchange-2 tick7
    ``hitl_resolved: accepted``). Returns a **terminal** ``PathBStep`` carrying the
    resolved entry (updated disposition + the reviewer's message), and rewrites the
    parked entry in the threaded ledger so the persisted state reads the resolution.
    """
    if step_result.action not in (PathBAction.HITL, PathBAction.ESCALATE):
        raise ValueError(
            f"resolve_hitl only finalizes a HITL/ESCALATE park; got {step_result.action}"
        )
    if resolution not in (Disposition.accepted, Disposition.rejected):
        raise ValueError(
            f"a reviewer resolves to accepted or rejected; got {resolution}"
        )

    resolved_entry = step_result.entry.model_copy(
        update={"disposition": resolution, "message": message}
    )
    # Rewrite the parked (last) entry in the threaded ledger with the resolution.
    ledger = (*step_result.state.ledger[:-1], resolved_entry)
    new_state = replace(step_result.state, ledger=ledger)
    action = (
        PathBAction.ACCEPT if resolution is Disposition.accepted else PathBAction.REJECT
    )
    return PathBStep(
        action=action,
        verdict=step_result.verdict.model_copy(update={"disposition": resolution}),
        entry=resolved_entry,
        reason_code=step_result.reason_code,
        message=message,
        state=new_state,
        terminal=True,
    )
