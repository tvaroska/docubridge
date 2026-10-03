"""MIME → ``InboundMail`` (S0-arch-13): headers + attachments, never the body.

A landed RFC 5322 message is parsed into the few headers the intake edge correlates
and gates on, plus its real attachments (the carrier's quote PDFs). The message
**body is never kept** — not on the dataclass, not in a marker — so the intake edge
persists no free-text counterparty content beyond the documents themselves (the
synthetic-data / PII posture).

What counts as an attachment (a recursive walk, not ``iter_attachments``, so an
HTML-embedded image nested under ``multipart/related`` is still seen and counted):

* a leaf part that is ``Content-Disposition: attachment``, carries a filename, or is
  not a text body part;
* NOT a body part (``text/plain`` / ``text/html`` without a filename / attachment
  disposition) — skipped silently;
* NOT an embedded image (``image/*`` with a ``Content-ID`` that is not marked
  ``attachment`` — signature logos, HTML ``cid:`` images) — skipped and counted in
  ``skipped_inline``;
* a forwarded ``message/rfc822`` is flattened **exactly one level**: its own
  attachments join the list; a message forwarded inside the forward is kept as one
  opaque ``.eml`` attachment (unreadable downstream, but visible — never dropped).

Filenames are sanitized here, once (NFC, last path segment, control/format chars and
leading dots stripped, ``?``/``#`` replaced, ≤ 128 chars and ≤ 200 UTF-8 bytes keeping
the suffix), so the
seam adapters only *validate* (``bridge.seams.protocols.validate_attachment_slot``).

Pure stdlib + ``bridge.extraction.resolve`` (the one suffix↔mime table). No
``google.*``, no ``a2a``, never ``agents/``.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from bridge.extraction.resolve import SUFFIX_MIME, mime_for

# The raw-message cap (25 MiB — above every mainstream MTA's attachment limit once
# base64-inflated). Overridable with ``BRIDGE_EMAIL_MAX_BYTES``, read at CALL time.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
MAX_BYTES_ENV = "BRIDGE_EMAIL_MAX_BYTES"
DEFAULT_MAX_ATTACHMENTS = 20
MAX_FILENAME = 128
# A filesystem NAME_MAX is 255 BYTES: 128 non-ASCII chars can exceed it, and an
# ENAMETOOLONG on the local landing dir would wedge the drain on that one mail.
MAX_FILENAME_BYTES = 200

# The headers whose addresses can carry the plus-addressed intake token.
_RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To")
_BODY_TYPES = ("text/plain", "text/html")


@dataclass(frozen=True)
class MailAttachment:
    """One real attachment: sanitized filename, effective mime type, raw bytes."""

    index: int
    filename: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class InboundMail:
    """The parsed headers + attachments of one landed message. No body, ever."""

    message_id: str | None
    sender: str | None
    recipients: tuple[str, ...]
    subject: str
    date: datetime | None
    auth_results: tuple[str, ...]
    attachments: tuple[MailAttachment, ...]
    skipped_inline: int


def max_mail_bytes() -> int:
    """The raw-message cap: ``BRIDGE_EMAIL_MAX_BYTES`` when set (read now), else 25 MiB."""
    raw = os.environ.get(MAX_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{MAX_BYTES_ENV} must be an int; got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{MAX_BYTES_ENV} must be >= 1; got {value}")
    return value


def _suffix_for(mime_type: str) -> str:
    for suffix, mime in SUFFIX_MIME.items():
        if mime == mime_type:
            return suffix
    return ".eml" if mime_type == "message/rfc822" else ".bin"


def sanitize_filename(raw: str | None, *, index: int, mime_type: str) -> str:
    """A safe single-segment filename for an attachment (see the module docstring)."""
    name = unicodedata.normalize("NFC", raw or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    # Drop control (Cc) and format (Cf — bidi overrides, zero-width) characters: a
    # right-to-left override in a filename is a classic extension-spoofing trick.
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    name = name.strip()
    while name.startswith("."):  # ". .pdf" must not come back as ".pdf"
        name = name.lstrip(".").strip()
    name = name.replace("?", "_").replace("#", "_")
    if len(name) > MAX_FILENAME or len(name.encode()) > MAX_FILENAME_BYTES:
        stem, suffix = os.path.splitext(name)
        if len(suffix) > 16 or len(suffix.encode()) > 64:
            suffix = ""
        stem = stem[: MAX_FILENAME - len(suffix)]
        budget = MAX_FILENAME_BYTES - len(suffix.encode())
        # Cut on a character boundary, then drop anything a cut left dangling.
        stem = stem.encode()[:budget].decode(errors="ignore").rstrip()
        name = stem + suffix if stem else ""
    return name or f"attachment-{index}{_suffix_for(mime_type)}"


def _addresses(msg: Message, headers: tuple[str, ...]) -> tuple[str, ...]:
    values = [str(v) for h in headers for v in (msg.get_all(h) or [])]
    seen: dict[str, None] = {}
    for _, addr in getaddresses(values):
        addr = addr.strip().lower()
        if addr and "@" in addr:
            seen.setdefault(addr, None)
    return tuple(seen)


def _date(msg: Message) -> datetime | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(str(raw))
    except (TypeError, ValueError, IndexError):
        return None


def _leaves(part: Message, depth: int):
    """Yield ``(kind, part)``: ``leaf`` parts, or ``opaque`` for a 2nd-level forward."""
    if part.get_content_type() == "message/rfc822":
        if depth == 0:
            payload = part.get_payload()
            inner = payload[0] if isinstance(payload, list) and payload else None
            if isinstance(inner, Message):
                yield from _leaves(inner, depth + 1)
                return
        yield "opaque", part
        return
    if part.is_multipart():
        for sub in part.get_payload():
            if isinstance(sub, Message):
                yield from _leaves(sub, depth)
        return
    yield "leaf", part


def _opaque_bytes(part: Message) -> bytes:
    payload = part.get_payload()
    inner = payload[0] if isinstance(payload, list) and payload else None
    if isinstance(inner, Message):
        return inner.as_bytes(policy=email.policy.default)
    return part.get_payload(decode=True) or b""


def parse_mime(
    raw: bytes,
    *,
    max_bytes: int | None = None,
    max_attachments: int = DEFAULT_MAX_ATTACHMENTS,
) -> InboundMail:
    """Parse one raw message. ``ValueError`` carries the quarantine reason token.

    * ``oversize`` — ``len(raw)`` over ``max_bytes`` (default :func:`max_mail_bytes`);
    * ``too_many_attachments`` — more than ``max_attachments`` real attachments;
    * ``unparseable`` — empty / non-message input, or the parser itself failed.
    """
    limit = max_mail_bytes() if max_bytes is None else max_bytes
    if len(raw) > limit:
        raise ValueError("oversize")
    if not raw or not raw.strip():
        raise ValueError("unparseable")
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        if not msg.keys():
            raise ValueError("unparseable")
        attachments: list[MailAttachment] = []
        skipped_inline = 0
        for kind, part in _leaves(msg, 0):
            ctype = part.get_content_type()
            disposition = part.get_content_disposition()
            declared = part.get_filename()
            if kind == "leaf":
                if (
                    ctype.startswith("image/")
                    and part.get("Content-ID")
                    and disposition != "attachment"
                ):
                    skipped_inline += 1
                    continue
                if ctype in _BODY_TYPES and disposition != "attachment" and not declared:
                    continue  # the body — never kept
                data = part.get_payload(decode=True) or b""
            else:
                data = _opaque_bytes(part)
            index = len(attachments)
            mime_type = ctype
            if mime_type == "application/octet-stream" and declared:
                mime_type = mime_for(sanitize_filename(declared, index=index, mime_type=ctype))
            filename = sanitize_filename(declared, index=index, mime_type=mime_type)
            if mime_type == "application/octet-stream":
                mime_type = mime_for(filename)
            attachments.append(
                MailAttachment(
                    index=index, filename=filename, mime_type=mime_type, data=data
                )
            )
            if len(attachments) > max_attachments:
                raise ValueError("too_many_attachments")
        _, sender = parseaddr(str(msg.get("From") or ""))
        message_id = str(msg.get("Message-ID") or "").strip() or None
        return InboundMail(
            message_id=message_id,
            sender=sender.lower() or None,
            recipients=_addresses(msg, _RECIPIENT_HEADERS),
            subject=str(msg.get("Subject") or ""),
            date=_date(msg),
            auth_results=tuple(
                str(v) for v in (msg.get_all("Authentication-Results") or [])
            ),
            attachments=tuple(attachments),
            skipped_inline=skipped_inline,
        )
    except ValueError as exc:
        if str(exc) in ("oversize", "too_many_attachments", "unparseable"):
            raise
        raise ValueError("unparseable") from exc
    except Exception as exc:  # a malformed message must quarantine, not crash a drain
        raise ValueError("unparseable") from exc


def normalize_message_id(value: str | None) -> str:
    """``  <ABC@Example.COM> `` → ``abc@example.com`` (whitespace, ``<>``, case)."""
    return (value or "").strip().strip("<>").strip().lower()


def landing_key(raw: bytes) -> str:
    """The 32-hex landing key: sha256 of the normalized Message-ID, else of the bytes.

    Headers only (``BytesHeaderParser``), so it is cheap on a 25 MiB message. Keying
    on the Message-ID makes a Graph re-delivery — or the same mail sent to two of our
    addresses — a duplicate at ``land()``.
    """
    message_id = ""
    try:
        headers = BytesHeaderParser(policy=email.policy.default).parsebytes(raw)
        message_id = normalize_message_id(str(headers.get("Message-ID") or ""))
    except Exception:
        message_id = ""
    material = f"message-id:{message_id}".encode() if message_id else raw
    return hashlib.sha256(material).hexdigest()[:32]


__all__ = [
    "DEFAULT_MAX_ATTACHMENTS",
    "DEFAULT_MAX_BYTES",
    "MAX_BYTES_ENV",
    "InboundMail",
    "MailAttachment",
    "landing_key",
    "max_mail_bytes",
    "normalize_message_id",
    "parse_mime",
    "sanitize_filename",
]
