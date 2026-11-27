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
        doc_id="a1",
        doctype="gov-id",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        reason_code="auto_approved",
        message="The document was automatically approved.",
    )
    rejected = LedgerEntry(
        doc_id="r1",
        doctype="utility-bill",
        disposition=Disposition.rejected,
        gate=Gate.resubmit,
        reason_code="illegible",
        message="The document was unreadable — please resubmit a clearer copy.",
    )
    outstanding = RequirementsList(
        requirements=[
            Requirement(
                item="utility-bill",
                status=RequirementStatus.required,
                doctype_hint="utility-bill",
                reason_code="illegible",
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
                    doc_id="a",
                    doctype="gov-id",
                    disposition=Disposition.accepted,
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
                assert token not in lowered, (
                    f"{model.__name__}.{field_name} looks like a pixel field"
                )


def test_render_upload_prompt():
    """upload_prompt renders outstanding + one upload action; accept override honored."""
    turn = _turn()
    view = render_upload_prompt(turn, accept=["utility-bill"])
    assert view.kind == "upload_prompt"
    assert len(view.actions) == 1
    assert view.actions[0].kind == "upload"
    assert view.actions[0].accept == ["utility-bill"]
    # Requirement relayed verbatim.
    assert (
        view.outstanding[0].message == turn.status.outstanding.requirements[0].message
    )


def test_render_hitl_review():
    """hitl_review renders the parked entry verbatim + a form action for resolution."""
    entry = LedgerEntry(
        doc_id="p1",
        doctype="utility-bill",
        disposition=Disposition.pending,
        gate=Gate.hitl_review,
        reason_code="needs_review",
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


def _seed_broker_transfer_item(*, max_approval_amount: object = None):
    from decimal import Decimal

    from bridge.a2ui_inbox import InMemoryActionInboxStore

    inbox = InMemoryActionInboxStore()
    item = inbox.create_action(
        entity_id="LOAN-MERIDIAN-001",
        context_id="ctx-p14-3",
        session_id="sess-p14-3",
        title="Approve broker disbursement",
        external_title="Pending review",
        hint="Verify transfer amount",
        external_hint="Under review",
        assigned_role="broker",
        target_view="v_broker_placement",
        max_approval_amount=(
            Decimal(str(max_approval_amount))
            if max_approval_amount is not None
            else None
        ),
        data_model={"transfer": {"approvedAmount": "1000.00"}},
        pause_for_review=True,
    )
    return inbox, item


def test_client_cannot_resolve_broker_assigned_item(monkeypatch):
    """P14-3(a): an external `client` cannot resolve a broker-assigned inbox item (store + HTTP)."""
    import pytest
    from fastapi.testclient import TestClient

    from bridge.a2ui_inbox import A2uiClientActionRequest
    from bridge.identity import (
        SPIFFE_SVID_SECRET_ENV,
        USER_TOKEN_HEADER,
        mint_user_token,
    )
    from bridge.user_interaction import build_user_interaction_app

    inbox, item = _seed_broker_transfer_item(max_approval_amount="50000.00")
    action = A2uiClientActionRequest(
        version="v0.9",
        action={"name": "accept", "context": {"decision": "accept"}},
    )
    with pytest.raises(PermissionError, match="cannot resolve inbox item"):
        inbox.resolve_action(
            item.item_id,
            action_request=action,
            actor_principal="client@borrower.example",
            actor_role="client",
            channel="custom_web",
        )

    # Same refusal over HTTP POST /v1/a2ui/inbox/items/{id}/action
    monkeypatch.setenv("K_SERVICE", "bridge-meridian")
    monkeypatch.setenv(SPIFFE_SVID_SECRET_ENV, "p14-3-inbox-signing-secret-2026")
    app = build_user_interaction_app(inbox_store=inbox, require_auth=True)
    client = TestClient(app)
    client_jwt = mint_user_token(
        "usr:client@borrower.example",
        "client",
        allowed_entities=["LOAN-MERIDIAN-001"],
    )
    resp = client.post(
        f"/v1/a2ui/inbox/items/{item.item_id}/action?channel=custom_web",
        headers={USER_TOKEN_HEADER: client_jwt},
        json=action.model_dump(mode="json"),
    )
    assert resp.status_code == 403


def test_missing_limit_claim_falls_back_to_role_limit(monkeypatch):
    """P14-3(b): token without `max_approval_amount` falls back to `DEFAULT_ROLE_APPROVAL_LIMITS`."""
    import pytest
    from fastapi.testclient import TestClient

    from bridge.a2ui_inbox import A2uiClientActionRequest
    from bridge.identity import (
        SPIFFE_SVID_SECRET_ENV,
        USER_TOKEN_HEADER,
        mint_user_token,
    )
    from bridge.user_interaction import build_user_interaction_app

    # Store-level check: item has no max_approval_amount AND caller passes None
    inbox, item = _seed_broker_transfer_item(max_approval_amount=None)
    huge_action = A2uiClientActionRequest(
        version="v0.9",
        action={
            "name": "edit_and_approve",
            "context": {
                "decision": "edit",
                "transfer": {"approvedAmount": str(10**12)},
            },
        },
    )
    with pytest.raises(ValueError, match="exceeds maximum authority limit"):
        inbox.resolve_action(
            item.item_id,
            action_request=huge_action,
            actor_principal="broker@docubridge.io",
            actor_role="broker",
            channel="gemini_app",
            principal_max_approval_amount=None,
        )

    # HTTP-level check: signed token minted without max_approval_amount claim
    monkeypatch.setenv("K_SERVICE", "bridge-meridian")
    monkeypatch.setenv(SPIFFE_SVID_SECRET_ENV, "p14-3-limit-signing-secret-2026")
    app = build_user_interaction_app(inbox_store=inbox, require_auth=True)
    client = TestClient(app)
    broker_jwt = mint_user_token("usr:broker@docubridge.io", "broker")
    resp = client.post(
        f"/v1/a2ui/inbox/items/{item.item_id}/action?channel=gemini_app",
        headers={USER_TOKEN_HEADER: broker_jwt},
        json=huge_action.model_dump(mode="json"),
    )
    assert resp.status_code == 422


def test_negative_approved_amount_rejected_without_limit():
    """P14-3(b): negative `approvedAmount` is rejected even when no explicit limit is configured."""
    import pytest

    from bridge.a2ui_inbox import A2uiClientActionRequest

    inbox, item = _seed_broker_transfer_item(max_approval_amount=None)
    neg_action = A2uiClientActionRequest(
        version="v0.9",
        action={
            "name": "edit_and_approve",
            "context": {
                "decision": "edit",
                "transfer": {"approvedAmount": "-5"},
            },
        },
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        inbox.resolve_action(
            item.item_id,
            action_request=neg_action,
            actor_principal="broker@docubridge.io",
            actor_role="broker",
            channel="gemini_app",
            principal_max_approval_amount=None,
        )


def test_resolved_inbox_item_cannot_be_resolved_twice_memory_and_sql(
    tmp_path, monkeypatch
) -> None:
    """P16-4: terminal inbox items refuse re-resolution in memory, SQL (CAS + 0 RAM leak), and HTTP 409."""
    import pytest
    from fastapi.testclient import TestClient

    from bridge.a2ui_inbox import (
        A2uiClientActionRequest,
        InboxItemAlreadyResolvedError,
        SqlActionInboxStore,
    )
    from bridge.identity import (
        SPIFFE_SVID_SECRET_ENV,
        USER_TOKEN_HEADER,
        mint_user_token,
    )
    from bridge.user_interaction import build_user_interaction_app

    accept_action = A2uiClientActionRequest(
        version="v0.9",
        action={"name": "accept", "context": {"decision": "accept"}},
    )
    reject_action = A2uiClientActionRequest(
        version="v0.9",
        action={"name": "reject", "context": {"decision": "reject"}},
    )

    mem_inbox, mem_item = _seed_broker_transfer_item(max_approval_amount="50000.00")
    first_mem = mem_inbox.resolve_action(
        mem_item.item_id,
        action_request=accept_action,
        actor_principal="broker@docubridge.io",
        actor_role="broker",
        channel="gemini_app",
    )
    assert first_mem["task_state"] == "completed"
    with pytest.raises(InboxItemAlreadyResolvedError, match="already resolved"):
        mem_inbox.resolve_action(
            mem_item.item_id,
            action_request=reject_action,
            actor_principal="broker@docubridge.io",
            actor_role="broker",
            channel="gemini_app",
        )
    assert mem_inbox.get_item(mem_item.item_id).task_state == "completed"
    assert len(mem_inbox.list_audit_records(mem_item.item_id)) == 1

    mem_inbox_fail, mem_fail_item = _seed_broker_transfer_item(
        max_approval_amount="50000.00"
    )
    mem_inbox_fail.resolve_action(
        mem_fail_item.item_id,
        action_request=reject_action,
        actor_principal="broker@docubridge.io",
        actor_role="broker",
        channel="gemini_app",
    )
    with pytest.raises(InboxItemAlreadyResolvedError, match="already resolved"):
        mem_inbox_fail.resolve_action(
            mem_fail_item.item_id,
            action_request=accept_action,
            actor_principal="broker@docubridge.io",
            actor_role="broker",
            channel="gemini_app",
        )

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'inbox_p16_4.db'}"
    sql_inbox = SqlActionInboxStore(dsn)
    try:
        sql_item = sql_inbox.create_action(
            entity_id="LOAN-MERIDIAN-001",
            title="Review wire transfer limit",
            hint="Verify wire authorization.",
            assigned_role="broker",
            data_model={"transfer": {"approvedAmount": "10000.00"}},
            pause_for_review=True,
            max_approval_amount="50000.00",
        )
        first_sql = sql_inbox.resolve_action(
            sql_item.item_id,
            action_request=accept_action,
            actor_principal="broker@docubridge.io",
            actor_role="broker",
            channel="gemini_app",
        )
        assert first_sql["task_state"] == "completed"
        assert sql_inbox._audits == []
        assert len(sql_inbox.list_audit_records(sql_item.item_id)) == 1

        with pytest.raises(InboxItemAlreadyResolvedError, match="already resolved"):
            sql_inbox.resolve_action(
                sql_item.item_id,
                action_request=reject_action,
                actor_principal="broker@docubridge.io",
                actor_role="broker",
                channel="gemini_app",
            )
        reloaded = sql_inbox.get_item(sql_item.item_id)
        assert reloaded is not None and reloaded.task_state == "completed"
        assert len(sql_inbox.list_audit_records(sql_item.item_id)) == 1
        assert sql_inbox._audits == []

        with pytest.raises(InboxItemAlreadyResolvedError, match="already resolved"):
            sql_inbox.advance_step(
                sql_item.item_id,
                step_id=reloaded.steps[0].step_id,
                status="working",
            )

        monkeypatch.setenv("K_SERVICE", "bridge-meridian")
        monkeypatch.setenv(SPIFFE_SVID_SECRET_ENV, "p16-4-inbox-signing-secret-2026")
        app = build_user_interaction_app(inbox_store=sql_inbox, require_auth=True)
        client = TestClient(app)
        broker_jwt = mint_user_token(
            "usr:broker@docubridge.io",
            "broker",
            allowed_entities=["LOAN-MERIDIAN-001"],
        )
        resp = client.post(
            f"/v1/a2ui/inbox/items/{sql_item.item_id}/action?channel=gemini_app",
            headers={USER_TOKEN_HEADER: broker_jwt},
            json=reject_action.model_dump(mode="json"),
        )
        assert resp.status_code == 409
        prog_resp = client.post(
            f"/v1/a2ui/inbox/items/{sql_item.item_id}/progress",
            headers={USER_TOKEN_HEADER: broker_jwt},
            json={"step_id": reloaded.steps[0].step_id, "status": "working"},
        )
        assert prog_resp.status_code == 409
    finally:
        sql_inbox.close()
