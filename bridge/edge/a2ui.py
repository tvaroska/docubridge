"""A2UI declarative content (A2UI-1) — the Bridge → document-provider edge.

A pure renderer mapping an ``ExchangeTurn`` / ``CollectionStatus`` into a declarative
**content** view (party-status / upload-prompt / HITL-review). **Content and protocol,
never pixels:** the models carry no color/size/style/pixel field, and the renderer
**relays the agent's ``reason_code`` + ``message`` uninterpreted** — it only *copies*
those strings from the turn and arranges them into declarative blocks. Headings/labels /
action prompts are layout chrome (allowed), but the renderer must never author or rewrite
an agent explanation (the relay invariant).

**Bridge-side, not ``contract/`` (§2d).** A2UI is strictly Bridge → document provider
(``docs/features.md`` §G: "The service agent does not speak A2UI"), so A2UI content is not
part of the shared bridge↔agent wire contract; these models live here. P6-5's shared
UI/domain package will mirror them to the frontend (snake→camel) — noted, not built.

**A2UI-over-A2A mount is P6-2, not here (§0).** This module is a pure function the portal
host (P6-2) calls; no ``/a2ui`` route on ``bridge/edge/server.py``.

Isolation: imports ``contract/`` + ``pydantic`` only; NEVER ``agents/``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from contract.enums import Disposition, LifecycleStatus, RequirementStatus
from contract.wire import ExchangeTurn, LedgerEntry


class A2uiItem(BaseModel):
    """One ledger entry, rendered. ``reason_code``/``message`` are copied verbatim
    from the source ``LedgerEntry`` (the relay invariant — no Bridge prose)."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    doctype: str
    disposition: Disposition
    reason_code: str | None = None
    message: str | None = None


class A2uiRequirement(BaseModel):
    """One outstanding requirement, rendered. ``reason_code``/``message`` copied
    verbatim from the source ``Requirement``."""

    model_config = ConfigDict(extra="forbid")

    item: str
    status: RequirementStatus
    doctype_hint: str | None = None
    reason_code: str | None = None
    message: str | None = None


class A2uiAction(BaseModel):
    """What response the portal should collect back (protocol, not pixels). ``accept``
    lists mime types / candidate doctypes; ``prompt`` is layout chrome — never an agent
    explanation, and never overwrites an item/requirement ``message``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["upload", "text", "form"]
    accept: list[str] = []
    prompt: str | None = None


class A2uiView(BaseModel):
    """A declarative content view the portal renders (content + protocol, no pixels)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["party_status", "upload_prompt", "hitl_review"]
    context_id: str
    accepted: list[A2uiItem] = []
    rejected: list[A2uiItem] = []  # sent-but-not-accepted, each with its agent message
    outstanding: list[A2uiRequirement] = []
    actions: list[A2uiAction] = []
    lifecycle: LifecycleStatus = LifecycleStatus.on_track


def _item_from_entry(entry: LedgerEntry) -> A2uiItem:
    """Copy a ledger entry's fields verbatim (relay invariant)."""
    return A2uiItem(
        doc_id=entry.doc_id,
        doctype=entry.doctype,
        disposition=entry.disposition,
        reason_code=entry.reason_code,
        message=entry.message,
    )


def _requirements(turn: ExchangeTurn) -> list[A2uiRequirement]:
    """Map ``turn.status.outstanding.requirements`` → ``A2uiRequirement``s, verbatim."""
    outstanding = turn.status.outstanding
    if outstanding is None:
        return []
    return [
        A2uiRequirement(
            item=r.item,
            status=r.status,
            doctype_hint=r.doctype_hint,
            reason_code=r.reason_code,
            message=r.message,
        )
        for r in outstanding.requirements
    ]


def _candidate_doctypes(requirements: list[A2uiRequirement]) -> list[str]:
    """Candidate doctypes to accept next (from outstanding required items' hints)."""
    hints: list[str] = []
    for req in requirements:
        if (
            req.status is RequirementStatus.required
            and req.doctype_hint
            and req.doctype_hint not in hints
        ):
            hints.append(req.doctype_hint)
    return hints


def render_party_status(turn: ExchangeTurn) -> A2uiView:
    """Render a party-status view: accepted vs sent-but-not-accepted ledger entries,
    outstanding requirements, and (if anything is still required) an upload action.

    Every ``reason_code``/``message`` is copied byte-identical from the source entry /
    requirement (A2UI-1 relay invariant). The only Bridge-authored string is the action
    ``prompt`` (layout chrome), which never touches an agent ``message``.
    """
    accepted = [
        _item_from_entry(e)
        for e in turn.status.entries
        if e.disposition is Disposition.accepted
    ]
    rejected = [
        _item_from_entry(e)
        for e in turn.status.entries
        if e.disposition is not Disposition.accepted
    ]
    outstanding = _requirements(turn)

    actions: list[A2uiAction] = []
    required = [r for r in outstanding if r.status is RequirementStatus.required]
    if required:
        actions.append(
            A2uiAction(
                kind="upload",
                accept=_candidate_doctypes(outstanding),
                prompt="Please upload the outstanding document(s).",
            )
        )

    return A2uiView(
        kind="party_status",
        context_id=turn.context_id,
        accepted=accepted,
        rejected=rejected,
        outstanding=outstanding,
        actions=actions,
        lifecycle=turn.lifecycle,
    )


def render_upload_prompt(
    turn: ExchangeTurn, *, accept: list[str] | None = None
) -> A2uiView:
    """Render an upload-prompt view: outstanding requirements + one upload action.

    ``accept`` overrides the doctypes to collect (else derived from outstanding required
    hints). Requirement ``reason_code``/``message`` are relayed verbatim.
    """
    outstanding = _requirements(turn)
    accept_types = accept if accept is not None else _candidate_doctypes(outstanding)
    return A2uiView(
        kind="upload_prompt",
        context_id=turn.context_id,
        outstanding=outstanding,
        actions=[
            A2uiAction(
                kind="upload",
                accept=accept_types,
                prompt="Please upload the requested document.",
            )
        ],
        lifecycle=turn.lifecycle,
    )


def render_hitl_review(turn: ExchangeTurn, entry: LedgerEntry) -> A2uiView:
    """Render a HITL-review view for one parked entry (the reviewer's queue item).

    The parked entry rides in ``rejected`` (sent-but-not-accepted, ``pending``) carrying
    its verbatim ``reason_code``/``message``; a ``form`` action collects the reviewer's
    resolution. No agent prose is authored.
    """
    return A2uiView(
        kind="hitl_review",
        context_id=turn.context_id,
        rejected=[_item_from_entry(entry)],
        actions=[
            A2uiAction(
                kind="form",
                accept=[Disposition.accepted.value, Disposition.rejected.value],
                prompt="Resolve this document review.",
            )
        ],
        lifecycle=turn.lifecycle,
    )


def encode_a2ui_view(view: A2uiView) -> dict:
    """Serialize an ``A2uiView`` for the portal transport (P6-2). JSON-mode dump."""
    return view.model_dump(mode="json")
