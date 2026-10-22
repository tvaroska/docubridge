"""The email-intake journey (S0-arch-13): a carrier replies to an RFP by email.

The REAL path, credential-free: a composed RFC 5322 reply carrying the benefits
conformance PDFs lands on the local ``email_ingress`` seam; ``EmailProcessor.drain``
correlates it by intake token to carrier-a's leg and files each attachment through
the fixture extraction engine → signals → the disposition gate → ``bridge.rfp.receive``.
The gated live leg swaps in the Gemini engine (terminal-outcome parity).
"""

from __future__ import annotations

import os
import time

import pytest
from google.adk.artifacts import InMemoryArtifactService

from bridge.edge.artifacts import APP_NAME, scoped_uri
from bridge.edge.email_ingress import EmailProcessor, MailOutcome
from bridge.email_ingress.correlation import intake_address, mint_token
from bridge.email_ingress.mime import landing_key
from bridge.rfp import (
    KIND_ACKNOWLEDGED,
    KIND_ATTACHMENT_UNREADABLE,
    KIND_QUOTE_INCOMPLETE,
    KIND_QUOTE_RECEIVED,
)
from bridge.seams.local import LocalEmailIngress
from contract.enums import RfpItemStatus
from tests.email_fixtures import (
    BASE_URL,
    CI_PDF,
    HI_PDF,
    MAILBOX,
    RFP_ID,
    SECRET,
    compose,
    extractor,
    make_board,
    quote_attachments,
)


class Rig:
    def __init__(self, tmp_path, *, extraction=None):
        self.board = make_board()
        self.seam = LocalEmailIngress(tmp_path)
        self.store = InMemoryArtifactService()
        self.proc = EmailProcessor(
            board=self.board,
            seam=self.seam,
            mailbox=MAILBOX,
            secret=SECRET,
            extraction=extraction or extractor(),
            artifact_store=self.store,
            base_url=BASE_URL,
        )

    @property
    def rfp(self):
        return self.board.rfps[RFP_ID]

    def land(self, raw: bytes) -> str:
        key = landing_key(raw)
        assert self.seam.land(key, raw)
        return key

    def cell(self, party, line):
        return self.rfp.cell(party, line)

    def reply(self, party="carrier-a", **kw) -> bytes:
        return compose(self.proc.reply_address(RFP_ID, party), **kw)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


async def test_reply_files_each_attachment_through_the_real_gate(rig):
    key = rig.land(rig.reply(attachments=quote_attachments(), inline_png=True))
    (outcome,) = await rig.proc.drain()

    assert outcome.status == "filed" and outcome.reason is None
    assert (outcome.rfp_id, outcome.party) == (RFP_ID, "carrier-a")
    assert outcome.skipped_inline == 1
    ci, hi = outcome.filed
    assert (ci.filename, ci.disposition, ci.gate, ci.product_line) == (
        CI_PDF.name,
        "accepted",
        "auto_approve",
        "CRITICAL_ILLNESS",
    )
    assert (hi.filename, hi.disposition, hi.gate, hi.reason_code, hi.product_line) == (
        HI_PDF.name,
        "pending",
        "hitl_review",
        "flagged_fields",
        "HOSPITAL_INDEMNITY",
    )
    assert [(u.filename, u.reason) for u in outcome.unreadable] == [
        ("rates.xlsx", "unsupported_media_type")
    ]

    ci_cell = rig.cell("carrier-a", "CRITICAL_ILLNESS")
    assert ci_cell.status is RfpItemStatus.received and len(ci_cell.versions) == 1
    hi_cell = rig.cell("carrier-a", "HOSPITAL_INDEMNITY")
    assert hi_cell.status is RfpItemStatus.incomplete
    assert (hi_cell.reason_code, hi_cell.attempts) == ("flagged_fields", 0)
    assert rig.cell("carrier-b", "CRITICAL_ILLNESS").status is RfpItemStatus.invited
    kinds = [e.kind for e in rig.rfp.audit]
    for kind in (
        KIND_ACKNOWLEDGED,
        KIND_QUOTE_RECEIVED,
        KIND_QUOTE_INCOMPLETE,
        KIND_ATTACHMENT_UNREADABLE,
    ):
        assert kind in kinds
    assert kinds.count(KIND_ATTACHMENT_UNREADABLE) == 1

    # The ledger carries an ArtifactRef BY REFERENCE: the leg-scoped fetch URI.
    leg = rig.rfp.leg_for("carrier-a")
    assert ci_cell.entry.artifact.uri == scoped_uri(
        BASE_URL, context_id=leg.context_id, filename=CI_PDF.name, version=0
    )
    assert ci_cell.entry.doc_id == f"email-{key}-0"
    part = await rig.store.load_artifact(
        app_name=APP_NAME,
        user_id="carrier-a",
        session_id=leg.context_id,
        filename=CI_PDF.name,
    )
    assert part.inline_data.data == CI_PDF.read_bytes()

    # The marker is the outcome, and the key left the queue.
    assert rig.seam.processed(key) == outcome.model_dump(mode="json")
    assert rig.seam.pending() == []
    assert "Please find our quote" not in str(rig.seam.processed(key))


async def test_replay_is_a_noop(rig):
    raw = rig.reply(attachments=quote_attachments())
    key = rig.land(raw)
    await rig.proc.drain()
    before = rig.rfp.model_dump()
    assert rig.seam.land(landing_key(raw), raw) is False
    assert await rig.proc.drain() == []
    assert rig.rfp.model_dump() == before
    assert (await rig.proc.process(key)).model_dump() == rig.seam.processed(key)
    assert rig.rfp.model_dump() == before


async def test_a_second_mail_is_a_new_artifact_version(rig):
    rig.land(rig.reply(attachments=quote_attachments(xlsx=False)))
    await rig.proc.drain()
    rig.land(
        rig.reply(
            message_id="<quote-2@carrier-a.example>",
            attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")],
        )
    )
    (outcome,) = await rig.proc.drain()
    assert outcome.filed[0].artifact_uri.endswith("?v=1")
    assert len(rig.cell("carrier-a", "CRITICAL_ILLNESS").versions) == 2


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (
            lambda r: compose(
                intake_address(MAILBOX, mint_token(b"other", RFP_ID, "carrier-a"))
            ),
            "no_exchange_token",
        ),
        (lambda r: compose("broker@broker.example"), "no_exchange_token"),
        (
            lambda r: r.reply(
                headers={
                    "Authentication-Results": "mx.broker.example; spf=pass; DMARC=fail"
                }
            ),
            "sender_authentication_failed",
        ),
        (
            lambda r: compose(
                r.proc.reply_address(RFP_ID, "carrier-a"),
                subject=r.proc.reply_subject_tag(RFP_ID, "carrier-b"),
            ),
            "ambiguous_token",
        ),
        (lambda r: b"\x00\x01 not a message", "unparseable"),
    ],
    ids=["foreign-secret", "no-token", "dmarc-fail", "ambiguous", "unparseable"],
)
async def test_quarantine_never_touches_the_board(rig, make, reason, caplog):
    before = rig.rfp.model_dump()
    key = rig.land(make(rig))
    (outcome,) = await rig.proc.drain()
    assert (outcome.status, outcome.reason) == ("quarantined", reason)
    assert outcome.rfp_id is None and outcome.filed == []
    assert rig.rfp.model_dump() == before
    assert rig.seam.pending() == []
    assert rig.seam.processed(key)["reason"] == reason
    assert any(
        "quarantined" in r.message and r.levelname == "WARNING" for r in caplog.records
    )


async def test_subject_tag_only_reply_files_normally(rig):
    tag = rig.proc.reply_subject_tag(RFP_ID, "carrier-a")
    rig.land(
        compose(
            "broker@broker.example",
            subject=f"Quote {tag}",
            attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")],
        )
    )
    (outcome,) = await rig.proc.drain()
    assert (outcome.status, outcome.party) == ("filed", "carrier-a")
    assert rig.cell("carrier-a", "CRITICAL_ILLNESS").status is RfpItemStatus.received


async def test_deterministic_engine_refusal_is_unreadable_and_the_rest_still_file(rig):
    atts = [
        ("unknown-stem.pdf", b"%PDF-1.4 x", "application/pdf"),
        (CI_PDF.name, CI_PDF.read_bytes(), "application/pdf"),
    ]
    rig.land(rig.reply(attachments=atts))
    (outcome,) = await rig.proc.drain()
    assert [(u.filename, u.reason) for u in outcome.unreadable] == [
        ("unknown-stem.pdf", "extraction_failed: KeyError")
    ]
    assert [f.filename for f in outcome.filed] == [CI_PDF.name]
    assert rig.cell("carrier-a", "CRITICAL_ILLNESS").status is RfpItemStatus.received


async def test_hostile_filenames_never_wedge_the_drain(rig):
    # Review fix: ". .pdf" used to sanitize to ".pdf" (refused by the seam) and a
    # 128-char CJK name overflowed NAME_MAX — both raised out of every drain.
    long_cjk = "\u62a5\u4ef7" * 64 + ".xlsx"
    atts = [
        (". .pdf", b"%PDF-1.4 x", "application/pdf"),
        (long_cjk, b"PK\x03\x04", "application/vnd.ms-excel"),
        (CI_PDF.name, CI_PDF.read_bytes(), "application/pdf"),
    ]
    key = rig.land(rig.reply(attachments=atts))
    (outcome,) = await rig.proc.drain()
    assert outcome.status == "filed" and rig.seam.pending() == []
    assert [f.filename for f in outcome.filed] == [CI_PDF.name]
    names = [u.filename for u in outcome.unreadable]
    assert names[0] == "pdf" and names[1].endswith(".xlsx")
    assert rig.seam.processed(key)["status"] == "filed"


class _Flaky:
    async def extract(self, name):
        raise RuntimeError("429 resource exhausted")


async def test_transient_engine_error_leaves_everything_for_the_next_drain(tmp_path):
    rig = Rig(tmp_path, extraction=_Flaky())
    before = rig.rfp.model_dump()
    key = rig.land(rig.reply(attachments=quote_attachments()))
    with pytest.raises(RuntimeError, match="429"):
        await rig.proc.drain()
    assert rig.rfp.model_dump() == before
    assert rig.seam.pending() == [key] and rig.seam.processed(key) is None

    rig.proc.extraction = extractor()
    (outcome,) = await rig.proc.drain()
    assert outcome.status == "filed" and len(outcome.filed) == 2
    assert rig.cell("carrier-a", "CRITICAL_ILLNESS").status is RfpItemStatus.received


async def test_correlated_mail_without_attachments_is_an_acknowledgement(rig):
    rig.land(rig.reply())
    (outcome,) = await rig.proc.drain()
    assert (outcome.status, outcome.reason) == ("filed", "no_attachments")
    assert (
        rig.cell("carrier-a", "CRITICAL_ILLNESS").status is RfpItemStatus.acknowledged
    )
    assert rig.cell("carrier-b", "CRITICAL_ILLNESS").status is RfpItemStatus.invited


async def test_drain_processes_in_landing_order(rig):
    first = rig.land(rig.reply(message_id="<a@x>"))
    time.sleep(0.02)  # distinct landing timestamps (coarse filesystem clock)
    second = rig.land(rig.reply("carrier-b", message_id="<b@x>"))
    outcomes = await rig.proc.drain()
    assert [o.key for o in outcomes] == [first, second]
    assert all(isinstance(o, MailOutcome) for o in outcomes)


def test_processor_refuses_without_secret_or_mailbox(tmp_path, monkeypatch):
    monkeypatch.delenv("BRIDGE_EMAIL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("BRIDGE_EMAIL_MAILBOX", raising=False)
    kw = dict(
        board=make_board(),
        seam=LocalEmailIngress(tmp_path),
        extraction=extractor(),
        artifact_store=InMemoryArtifactService(),
    )
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_MAILBOX"):
        EmailProcessor(secret=SECRET, **kw)
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_TOKEN_SECRET"):
        EmailProcessor(mailbox=MAILBOX, **kw)
    proc = EmailProcessor(mailbox=MAILBOX, secret=SECRET, **kw)
    token = mint_token(SECRET, RFP_ID, "carrier-a")
    assert (
        proc.reply_address(RFP_ID, "carrier-a") == f"rfp-quotes+{token}@broker.example"
    )
    assert proc.reply_subject_tag(RFP_ID, "carrier-a") == f"[ref:{token}]"


@pytest.mark.skipif(
    os.environ.get("BRIDGE_TEST_GEMINI") != "1",
    reason="set BRIDGE_TEST_GEMINI=1 (+ Vertex creds) to run the live Gemini engine",
)
async def test_live_gemini_files_the_emailed_quote(
    tmp_path, monkeypatch
):  # pragma: no cover
    from bridge.extraction.gemini import GeminiExtractor

    monkeypatch.setenv("BRIDGE_ALLOW_FILE_URI", "1")
    rig = Rig(tmp_path, extraction=GeminiExtractor())
    rig.land(
        rig.reply(attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")])
    )
    (outcome,) = await rig.proc.drain()
    assert outcome.status == "filed"
    # Terminal-outcome parity, never float equality.
    assert rig.cell("carrier-a", "CRITICAL_ILLNESS").status is RfpItemStatus.received


def test_cli_land_prints_landed_then_duplicate(tmp_path, monkeypatch, capsys):
    from bridge.email_ingress.__main__ import main

    monkeypatch.setenv("BRIDGE_EMAIL_DIR", str(tmp_path / "landing"))
    monkeypatch.delenv("BRIDGE_SEAM_EMAIL_INGRESS", raising=False)
    monkeypatch.delenv("BRIDGE_SEAM_MODE", raising=False)
    eml = tmp_path / "reply.eml"
    eml.write_bytes(compose(MAILBOX))
    key = landing_key(eml.read_bytes())
    assert main(["land", str(eml)]) == 0
    assert main(["land", str(eml)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"landed {key}", f"duplicate {key}"]
    assert LocalEmailIngress(tmp_path / "landing").pending() == [key]
