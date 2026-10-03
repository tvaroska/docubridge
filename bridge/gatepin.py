"""Gate pinning — the gate inversion (CORE-2) held at the agent.

The ``document_bridge`` LlmAgent may *call* ``run_disposition_gate`` but never
supplies its inputs. Every gate argument — ``doctype``, ``candidate_doctypes`` (the
label space), ``legible``, ``overall_confidence`` and ``flagged_fields`` — is
overwritten by :func:`pin_gate_args` (the root agent's ``before_tool_callback``) from
signals the Bridge itself recorded in session state:

- ``extract_document`` records the genuine ``ExtractionResult`` of the artifact it
  read (:func:`record_extraction`); ``AgentTool`` forwards that sub-run state delta
  into the parent ``document_bridge`` session;
- a caller (the edge's Collector handoff) seeds the Collector's signals plus the
  process label space before the invocation (:func:`seed_state_delta`).

The model's call args and the ``extraction_agent`` AgentTool's text response are
never a source. A gate call with no un-gated recorded signals, or with no label
space, is refused (a Bridge-authored refusal dict that never validates as a
``DispositionVerdict``). The pin — the call id → doc_id + the exact ``Signals`` the
gate ran on — is written to session state, so it rides the gate response event's
``actions.state_delta``; the edge projection mints a ledger entry only from a pinned
gate response whose verdict equals ``dispose_signals`` over the pinned signals.

See ``design/a2a-edge.md`` (D4, "Gate pinning") and ``design/disposition.md``.
The core never imports the edge.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from google.adk.tools import ToolContext
from google.adk.tools.base_tool import BaseTool
from pydantic import BaseModel, ConfigDict, ValidationError

from bridge.signals import Signals, signals_from_result
from contract.extraction import ExtractionResult

logger = logging.getLogger(__name__)

GATE_TOOL_NAME = "run_disposition_gate"

# Session-scoped, persisted state keys (never ``temp:`` — a parked invocation resumes
# later; never ``app:``/``user:``; never ``_adk``-prefixed, which AgentTool filters out
# of the sub-run's state copy).
SIGNALS_KEY = "bridge:gate_signals"  # {doc_id: RecordedSignals dump}, insertion-ordered
LABEL_SPACE_KEY = "bridge:label_space"  # list[str] — the process's candidate doctypes
PINS_KEY = "bridge:gate_pins"  # {function_call_id: {"doc_id": str, "signals": Signals dump}}

REASON_GATE_UNPINNED = "gate_signals_missing"
REASON_LABEL_SPACE_MISSING = "label_space_missing"

_REFUSAL_MESSAGES = {
    REASON_GATE_UNPINNED: (
        "The Bridge has no recorded signals for a document to gate. Call "
        "`extraction_agent` on the document first; the Bridge fills "
        "`run_disposition_gate`'s arguments from that extraction."
    ),
    REASON_LABEL_SPACE_MISSING: (
        "The Bridge cannot gate this document: no process label space is known for "
        "this conversation. Call `extraction_agent` on the document first within a "
        "recognized process; the gate is not run on model-supplied doctypes."
    ),
}


class RecordedSignals(BaseModel):
    """One document's genuine disposition signals, as recorded by the Bridge."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source: Literal["extraction", "collector"]
    doctype: str
    issuer: str | None = None
    legible: bool
    overall_confidence: float
    flagged_fields: list[str] = []
    # A collector seed carries its request's label space; an extraction record does not.
    candidate_doctypes: list[str] | None = None
    # The function_call_id of the gate call that consumed this record (None = gateable).
    gated_by: str | None = None


def recorded_from_result(
    result: ExtractionResult,
    *,
    doc_id: str,
    source: Literal["extraction", "collector"],
    candidate_doctypes: Sequence[str] | None = None,
) -> RecordedSignals:
    """Build the record from a genuine ``ExtractionResult``.

    Goes through :func:`bridge.signals.signals_from_result`, so the flagged-field
    fallback and issuer canonicalization are byte-identical to the Collector's.
    """
    signals = signals_from_result(
        result, candidate_doctypes=list(candidate_doctypes or [])
    )
    return RecordedSignals(
        doc_id=doc_id,
        source=source,
        doctype=signals.doctype,
        issuer=signals.issuer,
        legible=signals.legible,
        overall_confidence=signals.overall_confidence,
        flagged_fields=list(signals.flagged_fields),
        candidate_doctypes=(
            list(candidate_doctypes) if candidate_doctypes is not None else None
        ),
    )


def _rerecorded(records: Mapping[str, Any], rec: RecordedSignals) -> dict[str, Any]:
    """``records`` with ``rec`` appended at the end, un-gated (a re-record of the same
    doc_id drops the old one, so a resubmission becomes gateable again)."""
    fresh = rec.model_copy(update={"gated_by": None})
    return {k: v for k, v in records.items() if k != rec.doc_id} | {
        rec.doc_id: fresh.model_dump(mode="json")
    }


def record_signals(state: Any, rec: RecordedSignals) -> None:
    """Record ``rec`` into session ``state`` (a new top-level value, so the ADK state
    delta tracks it; nested dicts are never mutated in place)."""
    state[SIGNALS_KEY] = _rerecorded(state.get(SIGNALS_KEY) or {}, rec)


def record_extraction(state: Any, *, doc_id: str, result: ExtractionResult) -> None:
    """Record the genuine extraction of ``doc_id`` (called inside ``extract_document``)."""
    record_signals(state, recorded_from_result(result, doc_id=doc_id, source="extraction"))


def seed_state_delta(
    current_state: Mapping[str, Any],
    *,
    label_space: Sequence[str] | None = None,
    signals: Sequence[RecordedSignals] = (),
) -> dict[str, Any]:
    """The pure state delta that seeds a session before an invocation
    (``Runner.run_async(state_delta=...)``). Empty dict = nothing to seed."""
    delta: dict[str, Any] = {}
    if label_space is not None:
        delta[LABEL_SPACE_KEY] = list(label_space)
    if signals:
        records: dict[str, Any] = dict(current_state.get(SIGNALS_KEY) or {})
        for rec in signals:
            records = _rerecorded(records, rec)
        delta[SIGNALS_KEY] = records
    return delta


def refusal(reason_code: str) -> dict[str, Any]:
    """The Bridge-authored refusal a refused gate call returns instead of a verdict."""
    return {
        "refused": True,
        "reason_code": reason_code,
        "message": _REFUSAL_MESSAGES[reason_code],
    }


def _refuse(reason_code: str, call_id: str | None) -> dict[str, Any]:
    logger.info("gate call %s refused: %s", call_id or "<no id>", reason_code)
    return refusal(reason_code)


def _latest_ungated(
    records: Mapping[str, Any],
) -> tuple[str, RecordedSignals] | None:
    """The most recently recorded record not yet consumed by a gate call (with its key)."""
    for key, value in reversed(list(records.items())):
        try:
            rec = RecordedSignals.model_validate(value)
        except ValidationError:
            continue  # a malformed record is never a pin source
        if rec.gated_by is None and rec.doc_id == key:
            return key, rec
    return None


def pin_gate_args(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    """The root agent's ``before_tool_callback``: pin ``run_disposition_gate``'s inputs.

    A no-op (``None``, ``args`` untouched) for every other tool. For the gate:
    choose the most recently recorded un-gated record, overwrite ``args`` IN PLACE
    with its signals plus the label space (the record's own, else the session's),
    mark the record consumed, write the pin, and return ``None`` so ADK runs the real
    gate on the pinned args. Returns the refusal dict (the tool is skipped) when the
    call has no id, no un-gated record exists, or no label space is known.
    """
    if tool.name != GATE_TOOL_NAME:
        return None
    call_id = tool_context.function_call_id
    if not call_id:
        return _refuse(REASON_GATE_UNPINNED, call_id)
    state = tool_context.state
    records: dict[str, Any] = dict(state.get(SIGNALS_KEY) or {})
    latest = _latest_ungated(records)
    if latest is None:
        return _refuse(REASON_GATE_UNPINNED, call_id)
    doc_id, rec = latest
    label_space = (
        rec.candidate_doctypes
        if rec.candidate_doctypes is not None
        else state.get(LABEL_SPACE_KEY)
    )
    if label_space is None:  # an explicit [] is a (useless) legal label space
        return _refuse(REASON_LABEL_SPACE_MISSING, call_id)
    signals = Signals(
        doctype=rec.doctype,
        candidate_doctypes=list(label_space),
        legible=rec.legible,
        overall_confidence=rec.overall_confidence,
        flagged_fields=list(rec.flagged_fields),
        issuer=rec.issuer,
    )
    # Overwrite in place: ADK hands this same dict to the tool. Exactly the five gate
    # params (the gate has no issuer param).
    args.clear()
    args.update(
        doctype=signals.doctype,
        candidate_doctypes=list(signals.candidate_doctypes),
        legible=signals.legible,
        overall_confidence=signals.overall_confidence,
        flagged_fields=list(signals.flagged_fields),
    )
    state[SIGNALS_KEY] = {
        **records,
        doc_id: {**records[doc_id], "gated_by": call_id},
    }
    state[PINS_KEY] = {
        **(state.get(PINS_KEY) or {}),
        call_id: {"doc_id": doc_id, "signals": signals.model_dump(mode="json")},
    }
    logger.info("gate call %s pinned to %s", call_id, doc_id)
    return None


def pinned_signals(pin: Any) -> tuple[str, Signals] | None:
    """Parse a :data:`PINS_KEY` value → ``(doc_id, Signals)``; ``None`` when malformed."""
    if not isinstance(pin, Mapping):
        return None
    doc_id = pin.get("doc_id")
    if not isinstance(doc_id, str) or not doc_id:
        return None
    try:
        return doc_id, Signals.model_validate(pin.get("signals"))
    except ValidationError:
        return None
