"""Inbound email intake (S0-arch-13): a carrier answers a solicited RFP by email.

A dedicated M365 mailbox receives the carrier's reply; a Graph change notification
feeds the raw RFC 5322 bytes onto the ``email_ingress`` seam (create-only landing);
the intake edge (``bridge.edge.email_ingress``) parses them, correlates the reply to
one open RFP leg by its intake token, and files each attachment through the same
extraction → disposition gate → ledger path as an A2A or portal submission.

* :mod:`~bridge.email_ingress.mime` — MIME → :class:`InboundMail` (headers +
  attachments, never the body) and the idempotent landing key.
* :mod:`~bridge.email_ingress.correlation` — the per-leg HMAC intake token, the
  plus-address / subject-tag carriers and ``correlate``.
* :mod:`~bridge.email_ingress.graph` — the Graph client (the deployed transport into
  ``land()``); ``python -m bridge.email_ingress land <file.eml>`` is the local one.

Pure stdlib (+ lazy ``httpx`` for Graph). No ``google.*``, never ``agents/``.
"""

from bridge.email_ingress.correlation import (
    TOKEN_SECRET_ENV,
    AmbiguousToken,
    correlate,
    find_tokens,
    intake_address,
    mint_token,
    subject_tag,
    token_secret_from_env,
)
from bridge.email_ingress.graph import GraphError, GraphMailClient
from bridge.email_ingress.mime import (
    InboundMail,
    MailAttachment,
    landing_key,
    parse_mime,
)

__all__ = [
    "TOKEN_SECRET_ENV",
    "AmbiguousToken",
    "GraphError",
    "GraphMailClient",
    "InboundMail",
    "MailAttachment",
    "correlate",
    "find_tokens",
    "intake_address",
    "landing_key",
    "mint_token",
    "parse_mime",
    "subject_tag",
    "token_secret_from_env",
]
