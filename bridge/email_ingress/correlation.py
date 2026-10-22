"""Intake token correlation (S0-arch-13): which open RFP leg is this reply for?

Every (RFP, carrier) leg owns one **intake token** — an HMAC over ``rfp_id`` and
``party`` under ``BRIDGE_EMAIL_TOKEN_SECRET``, truncated to 80 bits of lowercase
base32 (alphanumeric, safe in an email local part). The token rides the reply
address as a plus-tag (``rfp-quotes+<token>@example.com``) with a subject tag
(``[ref:<token>]``) as the fallback for carriers whose MTA strips plus-addressing or
who BCC the intake mailbox.

**Leg isolation.** A token names exactly one carrier leg. It is not a capability
beyond that leg: a carrier quoting another carrier's token can at most file onto
that one leg (the same "a reference is not a capability" posture as the artifact
fetch). Tokens are recomputed and compared in constant time (``hmac.compare_digest``)
against every open leg — O(legs), no token table to leak or migrate.

Pure stdlib. Imports ``bridge.rfp`` for typing only — no ``google.*``, never ``agents/``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge.email_ingress.mime import InboundMail
    from bridge.rfp import RfpCollection, RfpLeg

TOKEN_SECRET_ENV = "BRIDGE_EMAIL_TOKEN_SECRET"
MAILBOX_ENV = "BRIDGE_EMAIL_MAILBOX"
TOKEN_LEN = 16  # 16 base32 chars = 80 bits
_TOKEN_RE = re.compile(rf"^[a-z0-9]{{{TOKEN_LEN}}}$")
_SUBJECT_TAG_RE = re.compile(rf"\[ref:([a-z0-9]{{{TOKEN_LEN}}})\]", re.IGNORECASE)


class AmbiguousToken(ValueError):
    """The mail names two or more distinct legs — quarantined, never guessed."""


def token_secret_from_env() -> bytes:
    """``BRIDGE_EMAIL_TOKEN_SECRET`` as bytes; ``RuntimeError`` when unset. No default."""
    raw = os.environ.get(TOKEN_SECRET_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"email intake requires {TOKEN_SECRET_ENV} (the intake-token HMAC key); "
            "refusing a default secret."
        )
    return raw.encode()


def mint_token(secret: bytes, rfp_id: str, party: str) -> str:
    """The leg's intake token: ``b32(HMAC-SHA256(secret, rfp_id\\nparty))[:16]``, lowercase."""
    if not secret:
        raise ValueError("intake token secret must not be empty")
    digest = hmac.new(secret, f"{rfp_id}\n{party}".encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii").lower()[:TOKEN_LEN]


def _split_mailbox(mailbox: str) -> tuple[str, str]:
    local, sep, domain = (mailbox or "").strip().lower().rpartition("@")
    if not sep or not local or not domain:
        raise ValueError(f"invalid intake mailbox {mailbox!r}")
    return local.partition("+")[0], domain


def intake_address(mailbox: str, token: str) -> str:
    """``rfp-quotes@x.com`` + token → ``rfp-quotes+<token>@x.com``."""
    if not _TOKEN_RE.fullmatch(token or ""):
        raise ValueError(f"invalid intake token {token!r}")
    local, domain = _split_mailbox(mailbox)
    return f"{local}+{token}@{domain}"


def subject_tag(token: str) -> str:
    """The subject fallback carrier of the token: ``[ref:<token>]``."""
    if not _TOKEN_RE.fullmatch(token or ""):
        raise ValueError(f"invalid intake token {token!r}")
    return f"[ref:{token}]"


@dataclass(frozen=True)
class IntakeAddressing:
    """A leg's reply addressing: the plus-addressed intake address + the subject tag.

    The invite / "chase now" copy and the carrier checklist show a carrier where to reply
    (S0-frontend-1). Delegates to ``mint_token`` / ``intake_address`` / ``subject_tag`` —
    the same token the intake edge correlates on. The mailbox shape and a non-empty secret
    are checked at construction.
    """

    mailbox: str
    secret: bytes

    def __post_init__(self) -> None:
        _split_mailbox(self.mailbox)  # ValueError on a malformed mailbox
        if not self.secret:
            raise ValueError("intake token secret must not be empty")

    @classmethod
    def from_env(cls) -> IntakeAddressing | None:
        """From ``BRIDGE_EMAIL_MAILBOX`` + ``BRIDGE_EMAIL_TOKEN_SECRET``.

        ``None`` when the mailbox is unset/blank (email intake not enabled — no reply
        address is shown). With the mailbox set, a missing secret raises ``RuntimeError``
        (``token_secret_from_env``: refuse loud, never a default).
        """
        mailbox = os.environ.get(MAILBOX_ENV, "").strip()
        if not mailbox:
            return None
        return cls(mailbox=mailbox, secret=token_secret_from_env())

    def token(self, rfp_id: str, party: str) -> str:
        """Mint or extract a deterministic intake routing token for an exchange and slot."""
        return mint_token(self.secret, rfp_id, party)

    def reply_address(self, rfp_id: str, party: str) -> str:
        """The plus-addressed intake address ``party`` replies to on ``rfp_id``."""
        return intake_address(self.mailbox, self.token(rfp_id, party))

    def reply_subject_tag(self, rfp_id: str, party: str) -> str:
        """The ``[ref:<token>]`` subject fallback for the leg."""
        return subject_tag(self.token(rfp_id, party))


def find_tokens(mail: InboundMail, mailbox: str) -> list[str]:
    """Distinct candidate tokens, in order: recipient plus-tags, then subject tags.

    A plus-tag counts only on an address whose base local part and domain equal the
    intake mailbox's (case-insensitive) — a plus-tag on a foreign address is ignored.
    """
    base, domain = _split_mailbox(mailbox)
    found: dict[str, None] = {}
    for addr in mail.recipients:
        local, sep, dom = addr.lower().rpartition("@")
        if not sep:
            continue
        head, plus, tag = local.partition("+")
        if plus and head == base and dom == domain and _TOKEN_RE.fullmatch(tag):
            found.setdefault(tag, None)
    for tag in _SUBJECT_TAG_RE.findall(mail.subject or ""):
        found.setdefault(tag.lower(), None)
    return list(found)


def correlate(
    tokens: Iterable[str],
    board_rfps: Iterable[RfpCollection],
    secret: bytes,
) -> tuple[RfpCollection, RfpLeg] | None:
    """Resolve the candidate tokens to exactly one open (RFP, leg).

    Returns the match, ``None`` when no token names an open leg, and raises
    :class:`AmbiguousToken` when the tokens name two or more distinct legs.
    """
    candidates = [t for t in dict.fromkeys(tokens) if _TOKEN_RE.fullmatch(t)]
    if not candidates:
        return None
    matches: dict[tuple[str, str], tuple[RfpCollection, RfpLeg]] = {}
    for rfp in board_rfps:
        for leg in rfp.legs:
            expected = mint_token(secret, rfp.rfp_id, leg.party)
            if any(hmac.compare_digest(expected, c) for c in candidates):
                matches[(rfp.rfp_id, leg.party)] = (rfp, leg)
    if len(matches) > 1:
        raise AmbiguousToken(
            f"tokens name {len(matches)} distinct legs: {sorted(matches)}"
        )
    return next(iter(matches.values()), None)


__all__ = [
    "MAILBOX_ENV",
    "TOKEN_SECRET_ENV",
    "AmbiguousToken",
    "IntakeAddressing",
    "correlate",
    "find_tokens",
    "intake_address",
    "mint_token",
    "subject_tag",
    "token_secret_from_env",
]
