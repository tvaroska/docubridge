"""Intake-token correlation (S0-arch-13): one token names exactly one carrier leg."""

from __future__ import annotations

import dataclasses

import pytest

from bridge.email_ingress.correlation import (
    AmbiguousToken,
    correlate,
    find_tokens,
    intake_address,
    mint_token,
    subject_tag,
    token_secret_from_env,
)
from bridge.email_ingress.mime import InboundMail
from tests.email_fixtures import MAILBOX, RFP_ID, SECRET, make_board

MAIL = InboundMail(
    message_id=None,
    sender="quotes@carrier-a.example",
    recipients=(),
    subject="",
    date=None,
    auth_results=(),
    attachments=(),
    skipped_inline=0,
)


def _mail(recipients=(), subject=""):
    return dataclasses.replace(MAIL, recipients=tuple(recipients), subject=subject)


def test_token_is_deterministic_and_differs_per_secret_rfp_and_party():
    token = mint_token(SECRET, RFP_ID, "carrier-a")
    assert token == mint_token(SECRET, RFP_ID, "carrier-a")
    assert len(token) == 16 and token.isalnum() and token == token.lower()
    others = {
        mint_token(b"other-secret", RFP_ID, "carrier-a"),
        mint_token(SECRET, "other-rfp", "carrier-a"),
        mint_token(SECRET, RFP_ID, "carrier-b"),
    }
    assert token not in others and len(others) == 3
    with pytest.raises(ValueError):
        mint_token(b"", RFP_ID, "carrier-a")


def test_intake_address_and_subject_tag_format():
    token = mint_token(SECRET, RFP_ID, "carrier-a")
    assert intake_address(MAILBOX, token) == f"rfp-quotes+{token}@broker.example"
    assert intake_address("RFP-Quotes@Broker.Example", token) == (
        f"rfp-quotes+{token}@broker.example"
    )
    assert subject_tag(token) == f"[ref:{token}]"
    with pytest.raises(ValueError):
        intake_address(MAILBOX, "not a token")
    with pytest.raises(ValueError):
        intake_address("no-at-sign", token)


def test_plus_address_found_and_foreign_plus_tag_ignored():
    token = mint_token(SECRET, RFP_ID, "carrier-a")
    foreign = mint_token(SECRET, RFP_ID, "carrier-b")
    mail = _mail(
        recipients=[
            f"rfp-quotes+{token}@broker.example",
            f"someone+{foreign}@broker.example",  # not the intake mailbox
            f"rfp-quotes+{foreign}@elsewhere.example",  # not the intake domain
        ]
    )
    assert find_tokens(mail, MAILBOX) == [token]


def test_subject_tag_fallback_for_a_bcc_style_reply():
    token = mint_token(SECRET, RFP_ID, "carrier-a")
    mail = _mail(
        recipients=["broker@broker.example"],
        subject=f"Re: RFP [REF:{token.upper()}] quote",
    )
    assert find_tokens(mail, MAILBOX) == [token]


def test_correlate_resolves_the_one_leg_over_a_two_rfp_board():
    board = make_board()
    board.put(make_board("globex-2027").rfps["globex-2027"])
    token = mint_token(SECRET, "globex-2027", "carrier-b")
    rfp, leg = correlate([token], board.rfps.values(), SECRET)
    assert (rfp.rfp_id, leg.party) == ("globex-2027", "carrier-b")
    assert leg.context_id == rfp.leg_for("carrier-b").context_id


def test_unknown_or_wrong_secret_token_is_none():
    board = make_board()
    assert correlate([], board.rfps.values(), SECRET) is None
    assert correlate(["aaaaaaaaaaaaaaaa"], board.rfps.values(), SECRET) is None
    wrong = mint_token(b"another-secret", RFP_ID, "carrier-a")
    assert correlate([wrong], board.rfps.values(), SECRET) is None


def test_two_distinct_legs_are_ambiguous_but_a_repeat_is_not():
    board = make_board()
    a = mint_token(SECRET, RFP_ID, "carrier-a")
    b = mint_token(SECRET, RFP_ID, "carrier-b")
    with pytest.raises(AmbiguousToken):
        correlate([a, b], board.rfps.values(), SECRET)
    mail = _mail(recipients=[f"rfp-quotes+{a}@broker.example"], subject=f"[ref:{a}]")
    assert find_tokens(mail, MAILBOX) == [a]
    assert correlate(find_tokens(mail, MAILBOX), board.rfps.values(), SECRET)[1].party == (
        "carrier-a"
    )


def test_secret_from_env_refuses_a_default(monkeypatch):
    monkeypatch.delenv("BRIDGE_EMAIL_TOKEN_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_TOKEN_SECRET"):
        token_secret_from_env()
    monkeypatch.setenv("BRIDGE_EMAIL_TOKEN_SECRET", "s3cret")
    assert token_secret_from_env() == b"s3cret"
