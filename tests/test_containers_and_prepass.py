"""Offline unit tests for recursive submission containers, Office prepass, and wave replay."""

from __future__ import annotations

import io
import math
import struct
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest

from bridge.extraction.containers import (
    OLE2_MAGIC,
    apply_advisories_to_requirements,
    parse_html_reply_advisories,
    unpack_submission_container,
)
from bridge.extraction.office_prepass import (
    detect_issuer_mismatch,
    expand_bundled_extractions,
    extract_docx_with_redlines,
    extract_rtf_text,
    extract_xlsx_resilient,
)
from bridge.operational_store import (
    InMemoryOperationalStore,
    SqlOperationalStore,
    compute_financial_summary,
)
from contract.enums import Disposition, Gate, RequirementStatus
from contract.extraction import SourceAnchor
from contract.wire import ArtifactRef, LedgerEntry, Requirement


def _pack_dir_entry(
    name: str,
    obj_type: int,
    *,
    left_sibling: int = 0xFFFFFFFF,
    right_sibling: int = 0xFFFFFFFF,
    child_id: int = 0xFFFFFFFF,
    start_sector: int = 0xFFFFFFFE,
    stream_size: int = 0,
) -> bytes:
    """Pack a single 128-byte MS-CFB v3 directory entry."""
    buf = bytearray(128)
    if name:
        encoded = (name + "\x00").encode("utf-16-le")
        buf[: len(encoded)] = encoded
        struct.pack_into("<H", buf, 64, len(encoded))
    buf[66] = obj_type
    buf[67] = 1
    struct.pack_into("<3I", buf, 68, left_sibling, right_sibling, child_id)
    struct.pack_into("<I", buf, 116, start_sector)
    struct.pack_into("<Q", buf, 120, stream_size)
    return bytes(buf)


def _build_synthetic_ole2_msg(
    *,
    subject: str,
    sender: str,
    to_addr: str,
    plain_body: str,
    html_body: str,
    attachment_filename: str,
    attachment_bytes: bytes,
) -> bytes:
    """Build a valid binary 512-byte-sector OLE2/CFBF `.msg` file in memory.

    Places all streams >= 4096 bytes (by setting mini_cutoff=0 in the CFBF header so
    every stream uses regular 512-byte FAT sectors cleanly without needing a separate
    MiniFAT), exercising full MS-CFB header, DIFAT, FAT, Directory red-black sibling
    traversal, MAPI UTF-16LE streams, and binary attachment storages.
    """
    sector_size = 512
    stream_defs: list[tuple[str, bytes]] = [
        ("__substg1.0_0037001F", subject.encode("utf-16-le")),
        ("__substg1.0_0C1F001F", sender.encode("utf-16-le")),
        ("__substg1.0_0E04001F", to_addr.encode("utf-16-le")),
        ("__substg1.0_1000001F", plain_body.encode("utf-16-le")),
        ("__substg1.0_1013001F", html_body.encode("utf-16-le")),
    ]
    att_stream_defs: list[tuple[str, bytes]] = [
        ("__substg1.0_3707001F", attachment_filename.encode("utf-16-le")),
        ("__substg1.0_370E001F", "application/zip".encode("utf-16-le")),
        ("__substg1.0_37010102", attachment_bytes),
    ]

    sectors: list[bytes] = []
    fat: list[int] = []

    def _alloc_stream(payload: bytes) -> tuple[int, int]:
        if not payload:
            return 0xFFFFFFFE, 0
        n_sec = math.ceil(len(payload) / sector_size)
        first = len(sectors)
        for i in range(n_sec):
            chunk = payload[i * sector_size : (i + 1) * sector_size]
            sectors.append(chunk.ljust(sector_size, b"\x00"))
            fat.append(first + i + 1 if i + 1 < n_sec else 0xFFFFFFFE)
        return first, len(payload)

    root_streams_meta = [(name, *_alloc_stream(raw)) for name, raw in stream_defs]
    att_streams_meta = [(name, *_alloc_stream(raw)) for name, raw in att_stream_defs]

    # Directory layout:
    # 0: Root Entry -> child_id = 1
    # 1..5: root MAPI streams chained via right_sibling -> 6: __attach_version1.0_#00000000
    # 7..9: attachment streams chained via right_sibling (child of 6 is 7)
    dir_entries: list[bytes] = []
    dir_entries.append(
        _pack_dir_entry(
            "Root Entry", 5, child_id=1, start_sector=0xFFFFFFFE, stream_size=0
        )
    )
    for idx, (sname, start_s, sz) in enumerate(root_streams_meta, start=1):
        right = idx + 1
        dir_entries.append(
            _pack_dir_entry(
                sname,
                2,
                right_sibling=right,
                start_sector=start_s,
                stream_size=sz,
            )
        )
    # Entry 6: attachment storage
    dir_entries.append(_pack_dir_entry("__attach_version1.0_#00000000", 1, child_id=7))
    for offset, (aname, start_s, sz) in enumerate(att_streams_meta):
        entry_idx = 7 + offset
        right = entry_idx + 1 if offset + 1 < len(att_streams_meta) else 0xFFFFFFFF
        dir_entries.append(
            _pack_dir_entry(
                aname,
                2,
                right_sibling=right,
                start_sector=start_s,
                stream_size=sz,
            )
        )
    while len(dir_entries) % 4 != 0:
        dir_entries.append(_pack_dir_entry("", 0))

    dir_bytes = b"".join(dir_entries)
    first_dir_sec, _ = _alloc_stream(dir_bytes)

    # Allocate 1 FAT sector covering all allocated sectors + itself
    fat_sec_id = len(sectors)
    fat.append(0xFFFFFFFD)
    while len(fat) < 128:
        fat.append(0xFFFFFFFF)
    fat_sector_bytes = struct.pack("<128I", *fat[:128])
    sectors.append(fat_sector_bytes)

    header = bytearray(512)
    header[:8] = OLE2_MAGIC
    struct.pack_into("<H", header, 24, 0x003E)  # minor version
    struct.pack_into("<H", header, 26, 0x0003)  # major version 3 (512B sectors)
    struct.pack_into("<H", header, 28, 0xFFFE)  # little-endian byte order
    struct.pack_into("<H", header, 30, 9)  # sector shift (2^9 = 512)
    struct.pack_into("<H", header, 32, 6)  # mini-sector shift (2^6 = 64)
    struct.pack_into("<I", header, 44, 1)  # num FAT sectors
    struct.pack_into("<I", header, 48, first_dir_sec)
    struct.pack_into("<I", header, 56, 0)  # mini_cutoff = 0 -> regular FAT streams
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)  # first MiniFAT sector
    struct.pack_into("<I", header, 64, 0)  # num MiniFAT sectors
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)  # first DIFAT sector
    struct.pack_into("<I", header, 72, 0)  # num DIFAT sectors
    difat_slots = [fat_sec_id] + [0xFFFFFFFF] * 108
    struct.pack_into("<109I", header, 76, *difat_slots)

    return bytes(header) + b"".join(sectors)


def _zip_of(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_recursive_zip_msg_zip_unpack_dedup_and_provenance() -> None:
    """Unpacks nested `.zip -> .msg -> .zip` archives, deduplicating SHA-256 leaves."""
    leaf_pdf = b"%PDF-1.4\n% Northwind Manufacturing 2027 Property Quote\n%%EOF\n"
    leaf_xlsx = b" schedule-placeholder-bytes"
    inner_zip = _zip_of(
        {
            "quotes/Northwind_Property_Quote.pdf": leaf_pdf,
            "../../traversal/Schedule_Locations.txt": leaf_xlsx,
        }
    )
    html_reply = """
    <html><body>
      <p>Please see attached carrier package and line-by-line status below:</p>
      <table>
        <tr><th>Line / Requirement</th><th>Status</th><th>Notes</th></tr>
        <tr><td>Dental Schedule</td><td>Carried forward — no change</td><td>Incumbent terms remain in force</td></tr>
        <tr><td>Executive Life</td><td>Quoted separately via external portal</td><td>Expected by 2026-11-20</td></tr>
      </table>
    </body></html>
    """
    msg_bytes = _build_synthetic_ole2_msg(
        subject="RE: Apex Group Benefits 2027 Renewal Package",
        sender="underwriting@harborview-example.com",
        to_addr="intake@docubridge-example.com",
        plain_body="Attached is the inner renewal zip.",
        html_body=html_reply,
        attachment_filename="Inner_Carrier_Package.zip",
        attachment_bytes=inner_zip,
    )
    # Include a duplicate copy of leaf_pdf at the outer zip level to verify SHA-256 dedup
    outer_zip = _zip_of(
        {
            "Broker_Forwarded_Reply.msg": msg_bytes,
            "Duplicate_Northwind_Quote.pdf": leaf_pdf,
        }
    )

    result = unpack_submission_container(
        outer_zip,
        "Submission_Bundle.zip",
        context_id="ctx-northwind-2027",
    )

    assert result.context_id == "ctx-northwind-2027"
    assert len(result.artifacts) == 2
    assert result.skipped_duplicates == 1
    assert len(result.duplicate_sha256s) == 1

    filenames = {a.filename for a in result.artifacts}
    assert filenames == {"Northwind_Property_Quote.pdf", "Schedule_Locations.txt"}
    for art in result.artifacts:
        assert art.context_id == "ctx-northwind-2027"
        assert art.parent_envelope_id is not None
        assert len(art.envelope_chain) >= 3
        assert art.envelope_chain[0] == "ctx-northwind-2027:root"

    assert len(result.envelopes) == 1
    env = result.envelopes[0]
    assert env.subject == "RE: Apex Group Benefits 2027 Renewal Package"
    assert env.sender == "underwriting@harborview-example.com"
    assert len(result.advisories) == 2
    kinds = {adv.item_key: adv.kind for adv in result.advisories}
    assert kinds["Dental Schedule"] == "attested_carry_forward"
    assert kinds["Executive Life"] == "deferred_external_quote"


def test_eml_html_reply_advisories_all_five_kinds_and_sense_b_mutation() -> None:
    """Parses all 5 `ReplyItemAdvisory` kinds from `.eml` HTML tables and mutates Requirements."""
    html_table = """
    <table>
      <tr><th>Requirement</th><th>Disposition</th><th>Broker Commentary</th></tr>
      <tr><td>Vision Plan</td><td>Attested unchanged — carry forward</td><td>2026 schedule stays active</td></tr>
      <tr><td>retiree-medical</td><td>Not applicable / population-wide exempt</td><td>No retiree class</td></tr>
      <tr><td>stop-loss-quote</td><td>Quoted separately by standalone desk</td><td>External quote due 2026-12-05</td></tr>
      <tr><td>claims-loss-runs</td><td>Pending — awaiting actuarial signoff</td><td>Expected by 11/28/2026</td></tr>
      <tr><td>census-roster</td><td>Submitted with 2 subsidiary tabs</td><td>See notes on seasonal staff</td></tr>
    </table>
    """
    msg = EmailMessage()
    msg["Subject"] = "Apex Group Benefits RFP Status Update"
    msg["From"] = "broker@apex-benefits-example.com"
    msg["To"] = "collect@docubridge-example.com"
    msg.set_content("Plain fallback summary.")
    msg.add_alternative(html_table, subtype="html")
    msg.add_attachment(
        b"col_a,col_b\n1,2\n",
        maintype="text",
        subtype="csv",
        filename="Census_Roster.csv",
    )

    res = unpack_submission_container(
        msg.as_bytes(),
        "Status_Update.eml",
        context_id="ctx-apex-rfp-01",
    )
    assert len(res.artifacts) == 1
    assert res.artifacts[0].filename == "Census_Roster.csv"
    assert len(res.advisories) == 5

    by_key = {a.item_key: a for a in res.advisories}
    assert by_key["Vision Plan"].kind == "attested_carry_forward"
    assert by_key["Vision Plan"].recommended_requirement_status == "waived"
    assert by_key["retiree-medical"].kind == "population_wide_exempt"
    assert by_key["retiree-medical"].recommended_requirement_status == "waived"
    assert by_key["stop-loss-quote"].kind == "deferred_external_quote"
    assert by_key["stop-loss-quote"].suggested_due_date == "2026-12-05"
    assert by_key["stop-loss-quote"].recommended_requirement_status == "required"
    assert by_key["claims-loss-runs"].kind == "pending_unavailable"
    assert by_key["claims-loss-runs"].suggested_due_date == "2026-11-28"
    assert by_key["census-roster"].kind == "general_note"

    reqs = [
        Requirement(item="Vision Plan", status=RequirementStatus.required),
        Requirement(item="retiree-medical", status=RequirementStatus.required),
        Requirement(item="stop-loss-quote", status=RequirementStatus.required),
        Requirement(item="claims-loss-runs", status=RequirementStatus.required),
    ]
    mutated, latest_due = apply_advisories_to_requirements(reqs, res.advisories)
    status_map = {r.item: r.status for r in mutated}
    assert status_map["Vision Plan"] == RequirementStatus.waived
    assert status_map["retiree-medical"] == RequirementStatus.waived
    assert status_map["stop-loss-quote"] == RequirementStatus.required
    assert status_map["claims-loss-runs"] == RequirementStatus.required
    assert latest_due == "2026-12-05"
    assert parse_html_reply_advisories("no tables here") == ()


def test_container_zip_bomb_and_depth_defenses() -> None:
    """Rejects high-ratio zip bombs and excessive nesting depth deterministically."""
    # 512 KB of zeros compresses > 200x, tripping max_compression_ratio=25.0
    bomb_zip = _zip_of({"zeros.bin": b"\x00" * (512 * 1024)})
    with pytest.raises(ValueError, match="compression ratio"):
        unpack_submission_container(
            bomb_zip,
            "bomb.zip",
            context_id="ctx-sec-1",
            max_compression_ratio=25.0,
        )

    nested = _zip_of({"leaf.txt": b"hello"})
    for idx in range(4):
        nested = _zip_of({f"layer-{idx}.zip": nested})
    with pytest.raises(ValueError, match="max_depth"):
        unpack_submission_container(
            nested,
            "deep.zip",
            context_id="ctx-sec-2",
            max_depth=2,
        )


def test_extract_docx_with_redlines_separates_deleted_and_inserted_spans() -> None:
    """Excludes `<w:del>` redlines from `accepted_text` while capturing `deleted_spans`."""
    doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p>
          <w:r><w:t>Policy Deductible: </w:t></w:r>
          <w:del w:author="Underwriter"><w:r><w:delText>$50,000</w:delText></w:r></w:del>
          <w:ins w:author="Underwriter"><w:r><w:t>$100,000</w:t></w:r></w:ins>
        </w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Coverage</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Limit</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Windstorm</w:t></w:r></w:p></w:tc>
            <w:tc>
              <w:p>
                <w:del><w:r><w:delText>$10,000,000</w:delText></w:r></w:del>
                <w:ins><w:r><w:t>$25,000,000</w:t></w:r></w:ins>
              </w:p>
            </w:tc>
          </w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """
    docx_bytes = _zip_of({"word/document.xml": doc_xml.encode("utf-8")})
    res = extract_docx_with_redlines(docx_bytes)

    assert res.has_tracked_changes is True
    assert "$100,000" in res.accepted_text
    assert "$25,000,000" in res.accepted_text
    assert "$50,000" not in res.accepted_text
    assert "$10,000,000" not in res.accepted_text
    assert res.deleted_spans == ("$50,000", "$10,000,000")
    assert res.inserted_spans == ("$100,000", "$25,000,000")
    assert res.tables[0][1] == ("Windstorm", "$25,000,000")


def test_extract_rtf_text_strips_destinations_and_decodes_escapes() -> None:
    """Strips RTF font/generator groups and decodes hex/Unicode escapes and table cells."""
    rtf = (
        r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}{\*\generator SyntheticWriter 1.0;}"
        r"\pard Carrier: Northwind Mutual\par "
        r"Quote Fee: \u8364?12,500 \'96 Binding\par "
        r"\trowd Tier 1\cell $450.00\cell\row}"
    )
    txt = extract_rtf_text(rtf.encode("latin-1"))
    assert "Arial" not in txt
    assert "SyntheticWriter" not in txt
    assert "Carrier: Northwind Mutual" in txt
    assert "€12,500" in txt
    assert "Tier 1 | $450.00" in txt


def test_extract_xlsx_resilient_visibility_filter_and_broken_shared_strings() -> None:
    """Filters hidden Column-A rows and recovers when `xl/sharedStrings.xml` is corrupt."""
    workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="RateSchedule" sheetId="1" r:id="rId1"/></sheets>
    </workbook>"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
    </Relationships>"""
    # Deliberately malformed/truncated XML after two valid <si> entries
    broken_ss_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<sst><si><t>Visible</t></si><si><t>PlanTier</t></si>"
        "<si><t>EmployeeOnly</t></si><si><t>HiddenDraftTier</t></si><UNCLOSED_CORRUPT"
    )
    sheet_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1">
          <c r="A1" t="s"><v>0</v></c>
          <c r="B1" t="s"><v>1</v></c>
          <c r="C1" t="inlineStr"><is><t>MonthlyRate</t></is></c>
        </row>
        <row r="2">
          <c r="A2" t="b"><v>1</v></c>
          <c r="B2" t="s"><v>2</v></c>
          <c r="C2"><v>415.25</v></c>
        </row>
        <row r="3">
          <c r="A3" t="b"><v>0</v></c>
          <c r="B3" t="s"><v>3</v></c>
          <c r="C3"><v>999.99</v></c>
        </row>
      </sheetData>
    </worksheet>"""
    corrupt_xlsx = _zip_of(
        {
            "xl/workbook.xml": workbook_xml.encode("utf-8"),
            "xl/_rels/workbook.xml.rels": rels_xml.encode("utf-8"),
            "xl/sharedStrings.xml": broken_ss_xml.encode("utf-8"),
            "xl/worksheets/sheet1.xml": sheet_xml.encode("utf-8"),
        }
    )

    # Confirm standard ElementTree fails on broken_ss_xml directly
    with pytest.raises(ET.ParseError):
        ET.fromstring(broken_ss_xml)

    wb = extract_xlsx_resilient(corrupt_xlsx, respect_visibility_flag_col=True)
    assert wb.used_resilient_fallback is True
    assert wb.filtered_hidden_rows == 1
    assert len(wb.sheets) == 1
    assert wb.sheets[0].rows == (
        ("PlanTier", "MonthlyRate"),
        ("EmployeeOnly", "415.25"),
    )
    assert "999.99" not in wb.rendered_text


def test_detect_issuer_mismatch_and_bundled_fanout() -> None:
    """Flags filename-vs-content issuer mismatches and fans out 1->N bundled plans."""
    candidates = ("Northwind Mutual", "Harborview Underwriters", "Summit Life")
    aligned = detect_issuer_mismatch(
        "Northwind_Mutual_2027_Property_Quote.pdf",
        "Northwind Mutual Insurance Co.",
        candidate_issuers=candidates,
    )
    assert aligned.mismatch is False
    assert aligned.severity == "none"

    misaligned = detect_issuer_mismatch(
        "Northwind_Mutual_2027_Property_Quote.pdf",
        "Harborview Underwriters Ltd.",
        candidate_issuers=candidates,
    )
    assert misaligned.mismatch is True
    assert misaligned.reason_code == "issuer_filename_content_mismatch"
    assert misaligned.severity == "quarantine"

    parent = LedgerEntry(
        doc_id="doc-apex-guide-2027",
        doctype="benefits-quote",
        issuer="Summit Life",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields={"currency": "USD", "effective_date": "2027-01-01"},
        tables={
            "rates": [
                {"plan_type": "HDHP", "deductible": "$3,000.00", "premium": "$410.00"},
                {"plan_type": "PPO", "deductible": "$1,000.00", "premium": "$620.00"},
                {"plan_type": "Dental", "deductible": "$50.00", "premium": "$45.00"},
            ]
        },
        vault_uri="gs://docubridge-vault/apex-guide-2027.pdf",
        artifact=ArtifactRef(uri="gs://docubridge-vault/apex-guide-2027.pdf"),
        source_anchors={
            "fields.key_fields.effective_date": SourceAnchor(page_number=1)
        },
    )
    children = expand_bundled_extractions(parent, split_key="plan_type")
    assert [c.doc_id for c in children] == [
        "doc-apex-guide-2027#hdhp",
        "doc-apex-guide-2027#ppo",
        "doc-apex-guide-2027#dental",
    ]
    assert all(c.vault_uri == parent.vault_uri for c in children)
    assert children[0].key_fields["deductible"] == "$3,000.00"
    assert children[1].key_fields["premium"] == "$620.00"


def test_bitemporal_multi_wave_recorded_at_replay_memory_and_sql(
    tmp_path: Path,
) -> None:
    """Replays t1 -> t2 -> t3 multi-wave arrivals using injectable `recorded_at`."""
    t1 = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 2, 14, 30, 0, tzinfo=UTC)
    t3 = datetime(2026, 11, 4, 18, 15, 0, tzinfo=UTC)
    eff = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)

    wave1_quote = LedgerEntry(
        doc_id="doc-nw-wave1-quote",
        doctype="commercial-property-quote",
        issuer="Harborview Underwriters",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields={
            "currency": "USD",
            "premium": "$310,000.00",
            "deductible": "$100,000.00",
        },
    )
    wave2_revised_quote = LedgerEntry(
        doc_id="doc-nw-wave2-quote",
        doctype="commercial-property-quote",
        issuer="Harborview Underwriters",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields={
            "currency": "USD",
            "premium": "$295,000.00",
            "deductible": "$100,000.00",
        },
    )
    wave3_endorsement = LedgerEntry(
        doc_id="doc-nw-wave3-endorsement",
        doctype="endorsement",
        issuer="Harborview Underwriters",
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields={
            "currency": "USD",
            "deductible": "$250,000.00",
        },
    )

    sql_dsn = f"sqlite+aiosqlite:///{tmp_path / 'wave_replay.db'}"
    stores = [InMemoryOperationalStore(), SqlOperationalStore(sql_dsn)]
    try:
        for store in stores:
            store.promote_submission(
                wave1_quote,
                entity_id="DEAL-NORTHWIND-2027",
                context_id="ctx-nw-waves",
                recorded_at=t1,
                effective_from=eff,
                version=1,
            )
            store.promote_submission(
                wave2_revised_quote,
                entity_id="DEAL-NORTHWIND-2027",
                context_id="ctx-nw-waves",
                recorded_at=t2,
                effective_from=eff,
                version=2,
            )
            store.promote_submission(
                wave3_endorsement,
                entity_id="DEAL-NORTHWIND-2027",
                context_id="ctx-nw-waves",
                recorded_at=t3,
                effective_from=eff,
                version=3,
            )

            w1_facts, w1_conflicts = store.bitemporal_query_as_of(
                "DEAL-NORTHWIND-2027", learned_as_of=t1
            )
            s1 = compute_financial_summary(w1_facts)
            assert s1.total_premium == "310000.00"
            assert s1.total_deductible == "100000.00"
            assert len(w1_conflicts) == 0

            w2_facts, w2_conflicts = store.bitemporal_query_as_of(
                "DEAL-NORTHWIND-2027", learned_as_of=t2
            )
            s2 = compute_financial_summary(w2_facts)
            assert s2.total_premium == "295000.00"
            assert s2.total_deductible == "100000.00"
            assert len(w2_conflicts) == 1

            w3_facts, w3_conflicts = store.bitemporal_query_as_of(
                "DEAL-NORTHWIND-2027", learned_as_of=t3
            )
            s3 = compute_financial_summary(w3_facts)
            assert s3.total_premium == "295000.00"
            assert s3.total_deductible == "250000.00"
            assert len(w3_conflicts) == 2
    finally:
        for store in stores:
            store.close()
