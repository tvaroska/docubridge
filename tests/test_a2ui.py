"""A2UI declarative-content renderer tests (P6-1, A2UI-1).

Proves the relay invariant (each entry/requirement reason_code+message is byte-identical
to the source turn — the Bridge adds no prose), that no model carries a pixel/style field
(content-not-pixels), that the three view kinds render, and that a view round-trips
through encode_a2ui_view.
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
from contract.enums import Disposition, Gate, LifecycleStatus, RequirementStatus
from contract.wire import (
    CollectionStatus,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
)

_PIXEL_TOKENS = ("style", "color", "css", "width", "height", "pixel", "font")


def _turn() -> ExchangeTurn:
    """A turn with one accepted entry, one rejected entry (reason+message), and one
    outstanding required requirement (reason+message)."""
    accepted = LedgerEntry(
        doc_id="a1", doctype="gov-id", disposition=Disposition.accepted,
        gate=Gate.auto_approve, reason_code="auto_approved",
        message="The document was automatically approved.",
    )
    rejected = LedgerEntry(
        doc_id="r1", doctype="utility-bill", disposition=Disposition.rejected,
        gate=Gate.resubmit, reason_code="illegible",
        message="The document was unreadable — please resubmit a clearer copy.",
    )
    outstanding = RequirementsList(
        requirements=[
            Requirement(
                item="utility-bill", status=RequirementStatus.required,
                doctype_hint="utility-bill", reason_code="illegible",
                message="The document was unreadable — please resubmit a clearer copy.",
            )
        ],
        done=False,
    )
    return ExchangeTurn(
        context_id="ctx-1",
        status=CollectionStatus(entries=[accepted, rejected], outstanding=outstanding),
        lifecycle=LifecycleStatus.overdue,
    )


def test_render_party_status_relays_messages_verbatim():
    """Gate #6: every item/requirement reason_code+message byte-identical to source."""
    turn = _turn()
    view = render_party_status(turn)

    assert view.kind == "party_status"
    assert view.context_id == "ctx-1"
    assert view.lifecycle is LifecycleStatus.overdue

    # accepted / rejected split
    assert len(view.accepted) == 1
    assert len(view.rejected) == 1
    acc, rej = view.accepted[0], view.rejected[0]

    src_acc = turn.status.entries[0]
    src_rej = turn.status.entries[1]
    assert acc.reason_code == src_acc.reason_code
    assert acc.message == src_acc.message
    assert rej.reason_code == src_rej.reason_code
    assert rej.message == src_rej.message

    # outstanding requirement relayed verbatim
    assert len(view.outstanding) == 1
    req = view.outstanding[0]
    src_req = turn.status.outstanding.requirements[0]
    assert req.reason_code == src_req.reason_code
    assert req.message == src_req.message
    assert req.doctype_hint == src_req.doctype_hint
    assert req.status is src_req.status

    # An upload action exists (outstanding-required non-empty) with candidate doctypes.
    uploads = [a for a in view.actions if a.kind == "upload"]
    assert uploads
    assert "utility-bill" in uploads[0].accept


def test_no_action_when_nothing_outstanding():
    """No outstanding-required items → no upload action."""
    turn = ExchangeTurn(
        context_id="c",
        status=CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="a", doctype="gov-id", disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                )
            ],
            outstanding=RequirementsList(requirements=[], done=True),
        ),
    )
    view = render_party_status(turn)
    assert view.actions == []


def test_a2ui_models_have_no_pixel_fields():
    """Content-not-pixels: no style/color/css/width/height/pixel/font field anywhere."""
    for model in (A2uiView, A2uiItem, A2uiRequirement, A2uiAction):
        for field_name in model.model_fields:
            lowered = field_name.lower()
            for token in _PIXEL_TOKENS:
                assert token not in lowered, f"{model.__name__}.{field_name} looks like a pixel field"


def test_render_upload_prompt():
    """upload_prompt renders outstanding + one upload action; accept override honored."""
    turn = _turn()
    view = render_upload_prompt(turn, accept=["utility-bill"])
    assert view.kind == "upload_prompt"
    assert len(view.actions) == 1
    assert view.actions[0].kind == "upload"
    assert view.actions[0].accept == ["utility-bill"]
    # Requirement relayed verbatim.
    assert view.outstanding[0].message == turn.status.outstanding.requirements[0].message


def test_render_hitl_review():
    """hitl_review renders the parked entry verbatim + a form action for resolution."""
    entry = LedgerEntry(
        doc_id="p1", doctype="utility-bill", disposition=Disposition.pending,
        gate=Gate.hitl_review, reason_code="needs_review",
        message="The document requires human review (confidence: 0.72).",
    )
    turn = ExchangeTurn(context_id="ctx-h", status=CollectionStatus(entries=[entry]))
    view = render_hitl_review(turn, entry)
    assert view.kind == "hitl_review"
    assert len(view.rejected) == 1
    assert view.rejected[0].message == entry.message
    assert view.rejected[0].reason_code == entry.reason_code
    forms = [a for a in view.actions if a.kind == "form"]
    assert forms
    assert "accepted" in forms[0].accept and "rejected" in forms[0].accept


def test_a2ui_view_roundtrips():
    """A2uiView.model_validate(encode_a2ui_view(view)) == view."""
    view = render_party_status(_turn())
    restored = A2uiView.model_validate(encode_a2ui_view(view))
    assert restored == view
