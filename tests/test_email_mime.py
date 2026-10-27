"""MIME → InboundMail (S0-arch-13): attachments only, never the body; the landing key."""

from __future__ import annotations

import dataclasses
from email.message import EmailMessage

import pytest

from bridge.email_ingress.mime import (
    InboundMail,
    landing_key,
    parse_mime,
    sanitize_filename,
)
from bridge.ports.protocols import validate_attachment_slot
from tests.email_fixtures import PNG_1PX, compose

PDF = b"%PDF-1.4 synthetic\n%%EOF\n"


def _mail(**kw) -> bytes:
    return compose("rfp-quotes+abc@broker.example", **kw)


def test_plain_body_plus_pdf_yields_one_attachment_and_the_headers():
    raw = _mail(
        attachments=[("quote.pdf", PDF, "application/pdf")],
        headers={
            "Cc": "Ops <ops@broker.example>",
            "Authentication-Results": "mx.example; dmarc=pass",
        },
    )
    mail = parse_mime(raw)
    assert [(a.index, a.filename, a.mime_type, a.data) for a in mail.attachments] == [
        (0, "quote.pdf", "application/pdf", PDF)
    ]
    assert mail.sender == "quotes@carrier-a.example"
    assert mail.recipients == ("rfp-quotes+abc@broker.example", "ops@broker.example")
    assert mail.subject == "Re: Acme voluntary benefits RFP"
    assert mail.message_id == "<quote-1@carrier-a.example>"
    assert mail.date is not None and mail.date.tzinfo is not None
    assert mail.auth_results == ("mx.example; dmarc=pass",)
    assert mail.skipped_inline == 0


def test_the_body_is_never_kept():
    fields = {f.name for f in dataclasses.fields(InboundMail)}
    assert not fields & {"body", "text", "html", "content"}
    mail = parse_mime(_mail(attachments=[("q.pdf", PDF, "application/pdf")]))
    assert all(b"Please find our quote" not in a.data for a in mail.attachments)


def test_rfc2231_non_ascii_filename_round_trips_nfc():
    raw = _mail(
        attachments=[("Devis-assurance-\u00e9t\u00e9.pdf", PDF, "application/pdf")]
    )
    assert b"filename*=" in raw  # really RFC 2231-encoded on the wire
    (att,) = parse_mime(raw).attachments
    assert att.filename == "Devis-assurance-\u00e9t\u00e9.pdf"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\x\\quote.pdf", "quote.pdf"),
        (".hidden.pdf", "hidden.pdf"),
        ("rate?sheet#2.pdf", "rate_sheet_2.pdf"),
        ("quote\u202efdp.exe", "quotefdp.exe"),  # the RTL-override spoof is stripped
        ("", "attachment-3.pdf"),
        ("..", "attachment-3.pdf"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw, index=3, mime_type="application/pdf") == expected


def test_sanitize_caps_length_keeping_the_suffix():
    name = sanitize_filename("q" * 300 + ".pdf", index=0, mime_type="application/pdf")
    assert len(name) == 128 and name.endswith(".pdf")


@pytest.mark.parametrize(
    "raw",
    [
        ". .pdf",  # strip-then-lstrip once would leave ".pdf"
        " . . .quote.pdf",
        "..\u200b..pdf",
        "\u62a5\u4ef7" * 100 + ".pdf",  # 128 CJK chars = 384 bytes > NAME_MAX
        "\U0001f4c4" * 120 + ".pdf",
        "q" * 300 + "." + "x" * 40,
        "\u62a5" * 300,
        "a/b\\c\x00d?.pdf",
    ],
)
def test_every_sanitized_name_passes_the_port_validator(raw):
    # Review fix: a sanitized name the adapters reject (ValueError) or the local
    # filesystem refuses (ENAMETOOLONG) would raise out of the drain on every run.
    name = sanitize_filename(raw, index=0, mime_type="application/pdf")
    validate_attachment_slot(0, name)
    assert not name.startswith(".") and len(name.encode()) <= 200


def test_byte_cap_keeps_the_suffix_and_cuts_on_a_character_boundary():
    name = sanitize_filename(
        "\u62a5" * 128 + ".pdf", index=0, mime_type="application/pdf"
    )
    assert name.endswith(".pdf") and len(name.encode()) <= 200
    assert name == "\u62a5" * ((200 - 4) // 3) + ".pdf"


def test_traversal_filename_on_the_wire_is_reduced_to_its_basename():
    (att,) = parse_mime(
        _mail(attachments=[("../../etc/passwd.pdf", PDF, "application/pdf")])
    ).attachments
    assert att.filename == "passwd.pdf"


def test_octet_stream_with_pdf_suffix_resolves_to_application_pdf():
    (att,) = parse_mime(
        _mail(attachments=[("quote.pdf", PDF, "application/octet-stream")])
    ).attachments
    assert att.mime_type == "application/pdf"


def test_inline_cid_image_is_skipped_and_counted():
    mail = parse_mime(
        _mail(inline_png=True, attachments=[("quote.pdf", PDF, "application/pdf")])
    )
    assert [a.filename for a in mail.attachments] == ["quote.pdf"]
    assert mail.skipped_inline == 1


def test_an_image_explicitly_attached_is_kept():
    mail = parse_mime(_mail(attachments=[("scan.png", PNG_1PX, "image/png")]))
    assert [(a.filename, a.mime_type) for a in mail.attachments] == [
        ("scan.png", "image/png")
    ]


def _forward(
    inner: bytes, *, extra: list[tuple[str, bytes, str]] | None = None
) -> bytes:
    outer = EmailMessage()
    outer["From"] = "broker@broker.example"
    outer["To"] = "rfp-quotes+abc@broker.example"
    outer["Subject"] = "Fwd: quote"
    outer["Message-ID"] = "<fwd@broker.example>"
    outer.set_content("Forwarding.")
    from email import message_from_bytes, policy

    outer.add_attachment(message_from_bytes(inner, policy=policy.default))
    for filename, data, mime in extra or []:
        maintype, subtype = mime.split("/", 1)
        outer.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=filename
        )
    return outer.as_bytes()


def test_forwarded_message_is_flattened_exactly_one_level():
    deepest = _mail(
        message_id="<deep@x>", attachments=[("deep.pdf", PDF, "application/pdf")]
    )
    middle = compose(
        "someone@carrier-a.example",
        message_id="<mid@x>",
        attachments=[("forwarded.pdf", PDF, "application/pdf")],
    )
    # One level: the forwarded mail's own attachment joins the list.
    mail = parse_mime(_forward(middle, extra=[("own.pdf", PDF, "application/pdf")]))
    assert [a.filename for a in mail.attachments] == ["forwarded.pdf", "own.pdf"]

    # Two levels: the inner forward stays ONE opaque .eml (visible, never flattened).
    from email import message_from_bytes, policy

    mid = message_from_bytes(middle, policy=policy.default)
    mid.add_attachment(message_from_bytes(deepest, policy=policy.default))
    mail = parse_mime(_forward(mid.as_bytes()))
    names = [a.filename for a in mail.attachments]
    assert "deep.pdf" not in names
    assert names[0] == "forwarded.pdf"
    assert mail.attachments[1].mime_type == "message/rfc822"
    assert mail.attachments[1].filename.endswith(".eml")
    assert b"deep.pdf" in mail.attachments[1].data


def test_oversize_and_too_many_attachments_raise_reason_tokens(monkeypatch):
    raw = _mail(attachments=[("q.pdf", PDF, "application/pdf")])
    with pytest.raises(ValueError, match=r"^oversize$"):
        parse_mime(raw, max_bytes=len(raw) - 1)
    monkeypatch.setenv("BRIDGE_EMAIL_MAX_BYTES", str(len(raw) - 1))
    with pytest.raises(ValueError, match=r"^oversize$"):
        parse_mime(raw)
    many = _mail(attachments=[(f"q{i}.pdf", PDF, "application/pdf") for i in range(3)])
    with pytest.raises(ValueError, match=r"^too_many_attachments$"):
        parse_mime(many, max_bytes=10**7, max_attachments=2)


@pytest.mark.parametrize("raw", [b"", b"   \r\n", b"\x00\x01\x02 not a message"])
def test_unparseable_input(raw):
    with pytest.raises(ValueError, match=r"^unparseable$"):
        parse_mime(raw)


def test_landing_key_is_stable_across_message_id_variants():
    keys = {
        landing_key(compose("a@b.example", message_id=mid))
        for mid in ("<Quote-1@Carrier-A.example>", "  <quote-1@carrier-a.example> ")
    }
    assert len(keys) == 1
    (key,) = keys
    assert len(key) == 32 and all(c in "0123456789abcdef" for c in key)
    # A different body under the same Message-ID is the same mail (Graph re-delivery).
    assert (
        landing_key(
            compose(
                "x@y.example", message_id="<quote-1@carrier-a.example>", subject="other"
            )
        )
        == key
    )


def test_landing_key_falls_back_to_a_content_hash():
    msg = EmailMessage()
    msg["From"] = "a@b.example"
    msg.set_content("no message id")
    raw = msg.as_bytes()
    assert landing_key(raw) == landing_key(raw)
    assert landing_key(raw) != landing_key(raw + b"x")
