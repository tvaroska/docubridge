"""COLL-2: the Bridge's best-effort satisfaction proposal (advisory, opt-in).

A process skill may carry a prose satisfaction description
(``metadata.bridge-satisfaction``). With an assessor bound, the Bridge asks it — a
model, never a rule engine — to read that prose against the turn's classified ledger
and propose what is still outstanding, in the existing requirement vocabulary
(``required`` / ``optional`` / ``satisfied``). "Looks done" ⇔ the proposal has at
least one item and nothing ``required`` (:func:`looks_done`).

The proposal is ADVISORY: completeness (sense B) is the requesting app's decision, so
``RequirementsList.done`` is ALWAYS ``False`` here and the Bridge never proposes
``waived``. It rides ``CollectionStatus.outstanding`` (no contract change) and
REPLACES the per-entry list only when the assessment succeeds; on any failure (no
rule, no assessor, exception, timeout, empty/malformed output) it falls back to
:func:`bridge.collect.project_outstanding` — best-effort never fails a turn and is
never worse than the per-entry list.

Opt-in: ``BRIDGE_SATISFACTION=llm`` (resolved once at app build by
:func:`resolve_assessor`); unset/blank/``off`` → no assessor, every turn model-free.
Not a seam (the model resolves like the ``document_bridge`` agent's, ``BRIDGE_MODEL``).

ADK-free: imports ``contract`` + stdlib + pydantic only; the model-backed assessor
lives in :mod:`bridge.satisfaction_llm` and is imported lazily.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from bridge.collect import project_outstanding
from contract.enums import RequirementStatus
from contract.wire import LedgerEntry, Requirement, RequirementsList

logger = logging.getLogger(__name__)

SATISFACTION_ENV = "BRIDGE_SATISFACTION"
PROPOSAL_REASON_CODE = "satisfaction_proposal"
DEFAULT_TIMEOUT_S = 30.0
MAX_ITEMS = 20
MAX_MESSAGE_CHARS = 300

_OFF = ("", "off")
_LLM = "llm"


class ProposedItem(BaseModel):
    """One item an assessor proposes (also the model's JSON item shape)."""

    model_config = ConfigDict(extra="ignore")

    item: str  # human label, e.g. "proof of address"
    status: Literal["required", "optional", "satisfied"]
    doctype_hint: str | None = None
    message: str = ""


@runtime_checkable
class SatisfactionAssessor(Protocol):
    """Interprets a prose satisfaction description against a ledger (advisory)."""

    async def assess(
        self,
        *,
        rule: str,
        entries: Sequence[LedgerEntry],
        candidate_doctypes: Sequence[str],
    ) -> list[ProposedItem]:
        """Evaluate whether extracted document signals satisfy the requested collection spec."""
        ...


async def propose_outstanding(
    entries: Sequence[LedgerEntry],
    *,
    rule: str,
    candidate_doctypes: Sequence[str],
    assessor: SatisfactionAssessor | None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> RequirementsList:
    """The COLL-2 proposal for ``entries``, else the per-entry fallback.

    Never raises on an assessor failure and never sets ``done`` (always ``False``).
    Assessor hints outside ``candidate_doctypes`` are dropped; messages are truncated
    to :data:`MAX_MESSAGE_CHARS` and the list to :data:`MAX_ITEMS`. Ledger contents
    are never logged.
    """
    fallback_entries = list(entries)
    if not rule.strip() or assessor is None:
        return project_outstanding(fallback_entries)
    try:
        items = await asyncio.wait_for(
            assessor.assess(
                rule=rule,
                entries=fallback_entries,
                candidate_doctypes=list(candidate_doctypes),
            ),
            timeout,
        )
        requirements = _sanitize(items, set(candidate_doctypes))
    except Exception as exc:  # best-effort: TimeoutError, model, parse, anything
        logger.warning(
            "satisfaction proposal failed (%s); falling back to per-entry outstanding",
            type(exc).__name__,
        )
        return project_outstanding(fallback_entries)
    if not requirements:
        # An empty proposal cannot express "looks done" — keep the per-entry list.
        logger.warning(
            "satisfaction proposal was empty; falling back to per-entry outstanding"
        )
        return project_outstanding(fallback_entries)
    return RequirementsList(requirements=requirements, done=False)


def _sanitize(
    items: Sequence[ProposedItem] | None, labels: set[str]
) -> list[Requirement]:
    """Map assessor items to proposal ``Requirement``s: blank items dropped, hints
    outside the label space nulled, messages truncated, at most :data:`MAX_ITEMS`.
    A non-``ProposedItem`` value (e.g. a raw dict) is validated into one first."""
    requirements: list[Requirement] = []
    for raw in items or []:
        proposed = ProposedItem.model_validate(raw)
        label = proposed.item.strip()
        if not label:
            continue
        hint = proposed.doctype_hint if proposed.doctype_hint in labels else None
        message = proposed.message.strip()[:MAX_MESSAGE_CHARS]
        requirements.append(
            Requirement(
                item=label,
                status=RequirementStatus(proposed.status),
                doctype_hint=hint,
                reason_code=PROPOSAL_REASON_CODE,
                message=message or None,
            )
        )
        if len(requirements) >= MAX_ITEMS:
            break
    return requirements


def looks_done(outstanding: RequirementsList | None) -> bool:
    """Whether a proposal reads "looks done": ≥1 item and none ``required``.

    Advisory only — the requesting app's gate owns done. ``optional`` items do not
    block; an empty or absent list is never "looks done".
    """
    if outstanding is None or not outstanding.requirements:
        return False
    return all(
        r.status is not RequirementStatus.required for r in outstanding.requirements
    )


def resolve_assessor(
    value: SatisfactionAssessor | str | None = None,
) -> SatisfactionAssessor | None:
    """Resolve the satisfaction assessor ONCE (at app build).

    An injected assessor (any non-str object with ``assess``) wins. Otherwise an
    explicit non-blank mode string, else ``BRIDGE_SATISFACTION``: unset/blank/``off``
    → ``None`` (model-free); ``llm`` → the model-backed
    :class:`~bridge.satisfaction_llm.LlmSatisfactionAssessor` on ``BRIDGE_MODEL``
    (its model is built lazily, so no credentials are needed here). Any other value
    → ``ValueError`` (exact, case-sensitive match; fail closed, mirroring
    ``bridge.edge.pathroute.resolve_edge_mode``).
    """
    if value is not None and not isinstance(value, str):
        if not hasattr(value, "assess"):
            raise TypeError(
                f"satisfaction assessor {type(value).__name__} has no assess()"
            )
        return value
    raw = (value or "").strip() or os.environ.get(SATISFACTION_ENV, "").strip()
    if raw in _OFF:
        return None
    if raw == _LLM:
        from bridge.satisfaction_llm import LlmSatisfactionAssessor

        return LlmSatisfactionAssessor()
    raise ValueError(
        f"invalid satisfaction mode {raw!r} for {SATISFACTION_ENV}; "
        "expected 'off' or 'llm'"
    )
