"""Shared builders for the email-intake tests (S0-arch-13): an RFP board + composed mail.

Synthetic data only: the carriers / broker are fictional and the quote PDFs are the
benefits conformance corpus's own (read, never edited).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from bridge.dashboard.board import RfpBoard
from bridge.extraction.fixture import FixtureExtractor
from bridge.rfp import load_rfp_policy, open_rfp
from contract.enums import RequirementStatus
from contract.wire import CollectRequest, Requirement, RequirementsList
from tests import corpus_pdf

PROCESS = "benefits-rfp"  # the process skill under test (named here, never in bridge/)
ROOT = Path(__file__).resolve().parents[1]
BENEFITS_DIR = ROOT / "wiki/evals/benefits"
CONFORMANCE = BENEFITS_DIR / "conformance.json"
CI_PDF = BENEFITS_DIR / "images/benefits-conformance-ci-usd.pdf"
HI_PDF = BENEFITS_DIR / "images/benefits-conformance-hi-usd-flagged.pdf"
_CONFORMANCE_RECORDS = corpus_pdf.load_records(CONFORMANCE)
CI_PDF_BYTES: bytes = corpus_pdf.build_conformance_pdf(
    _CONFORMANCE_RECORDS["benefits-conformance-ci-usd"]
)
HI_PDF_BYTES: bytes = corpus_pdf.build_conformance_pdf(
    _CONFORMANCE_RECORDS["benefits-conformance-hi-usd-flagged"]
)

MAILBOX = "rfp-quotes@broker.example"
SECRET = b"test-intake-secret"
RFP_ID = "acme-vb-2027"
BASE_URL = "http://bridge.test/"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=14)
LINES = {
    "carrier-a": ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY"),
    "carrier-b": ("CRITICAL_ILLNESS",),
}
# A 1x1 transparent PNG (a signature logo stand-in).
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def make_board(rfp_id: str = RFP_ID) -> RfpBoard:
    board = RfpBoard(policy=load_rfp_policy(PROCESS), clock=lambda: NOW)
    request = CollectRequest(
        process=PROCESS,
        party="acme-broker",
        context_id="rfp-ctx",
        due_at=DUE,
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item=line,
                    party=party,
                    status=RequirementStatus.required,
                    doctype_hint="benefits-quote",
                )
                for party, lines in LINES.items()
                for line in lines
            ]
        ),
    )
    board.put(open_rfp(request, now=NOW - timedelta(days=1), rfp_id=rfp_id))
    return board


def extractor() -> FixtureExtractor:
    return FixtureExtractor(corpus_path=CONFORMANCE)


def compose(
    to: str,
    *,
    message_id: str = "<quote-1@carrier-a.example>",
    subject: str = "Re: Acme voluntary benefits RFP",
    sender: str = "quotes@carrier-a.example",
    attachments: list[tuple[str, bytes, str]] | None = None,
    inline_png: bool = False,
    headers: dict[str, str] | None = None,
) -> bytes:
    """An RFC 5322 reply: a text body, optional cid-logo HTML, then the attachments."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Fri, 02 Oct 2026 09:00:00 +0000"
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content("Please find our quote attached.\n")
    if inline_png:
        msg.add_alternative(
            '<p>Our quote attached.</p><img src="cid:logo">', subtype="html"
        )
        html = msg.get_payload()[1]
        html.add_related(PNG_1PX, maintype="image", subtype="png", cid="<logo>")
    for filename, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def quote_attachments(*, xlsx: bool = True) -> list[tuple[str, bytes, str]]:
    atts = [
        (CI_PDF.name, CI_PDF_BYTES, "application/pdf"),
        (HI_PDF.name, HI_PDF_BYTES, "application/pdf"),
    ]
    if xlsx:
        atts.append(("rates.xlsx", b"PK\x03\x04 synthetic rate sheet", XLSX))
    return atts
