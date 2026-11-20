"""HTML reply-table advisory parsing for solicited Collect containers (ADR-14 D4)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict

from contract.wire import Requirement

AdvisoryKind = Literal[
    "deferred_external_quote",
    "attested_carry_forward",
    "population_wide_exempt",
    "pending_unavailable",
    "general_note",
]

_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")


class ReplyItemAdvisory(BaseModel):
    """Structured item-status advisory parsed from an inline HTML email reply table."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_key: str
    raw_status: str
    kind: AdvisoryKind
    recommended_requirement_status: Literal["waiver_recommended", "required"]
    suggested_due_date: str | None = None
    note: str = ""
    source_envelope_id: str | None = None


def _extract_date_hint(text: str) -> str | None:
    """Extract an ISO YYYY-MM-DD date from free-text status notes when present."""
    iso_match = _ISO_DATE_RE.search(text)
    if iso_match:
        return iso_match.group(1)
    slash_match = _SLASH_DATE_RE.search(text)
    if slash_match:
        month, day, year = (
            int(slash_match.group(1)),
            int(slash_match.group(2)),
            int(slash_match.group(3)),
        )
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _classify_advisory_row(
    item_key: str,
    raw_status: str,
    note_text: str,
    source_envelope_id: str | None,
) -> ReplyItemAdvisory:
    """Classify an HTML reply-table row into one of the five canonical advisory kinds."""
    combined = f"{raw_status} {note_text}".strip()
    lower = combined.lower()
    due_date = _extract_date_hint(combined)

    if any(
        phrase in lower
        for phrase in (
            "carry forward",
            "carried forward",
            "no change",
            "unchanged",
            "remain in force",
            "attested",
            "incumbent terms",
        )
    ):
        kind: AdvisoryKind = "attested_carry_forward"
        req_status: Literal["waiver_recommended", "required"] = "waiver_recommended"
    elif any(
        phrase in lower
        for phrase in (
            "not applicable",
            "n/a",
            "population-wide exempt",
            "population exempt",
            "no eligible",
            "exempt",
            "waived",
        )
    ):
        kind = "population_wide_exempt"
        req_status = "waiver_recommended"
    elif any(
        phrase in lower
        for phrase in (
            "external quote",
            "quoted separately",
            "separate carrier",
            "external portal",
            "separate submission",
            "deferred external",
            "standalone desk",
        )
    ):
        kind = "deferred_external_quote"
        req_status = "required" if due_date is not None else "waiver_recommended"
    elif any(
        phrase in lower
        for phrase in (
            "pending",
            "unavailable",
            "awaiting",
            "to follow",
            "in progress",
            "forthcoming",
            "expected by",
        )
    ):
        kind = "pending_unavailable"
        req_status = "required"
    else:
        kind = "general_note"
        req_status = "required"

    return ReplyItemAdvisory(
        item_key=item_key.strip(),
        raw_status=raw_status.strip(),
        kind=kind,
        recommended_requirement_status=req_status,
        suggested_due_date=due_date,
        note=note_text.strip(),
        source_envelope_id=source_envelope_id,
    )


class _HtmlTableParser(HTMLParser):
    """Minimal stdlib HTML table extractor for structured counterparty reply tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._cell_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        t = tag.lower()
        if t == "table":
            self._in_table = True
            self._current_table = []
        elif t == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif t in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_buf = []
        elif t == "br" and self._in_cell:
            self._cell_buf.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._in_cell:
            cell_text = re.sub(r"\s+", " ", "".join(self._cell_buf)).strip()
            self._current_row.append(cell_text)
            self._in_cell = False
        elif t == "tr" and self._in_row:
            if any(self._current_row):
                self._current_table.append(self._current_row)
            self._in_row = False
        elif t == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False


def parse_html_reply_advisories(
    html_text: str,
    *,
    source_envelope_id: str | None = None,
) -> tuple[ReplyItemAdvisory, ...]:
    """Parse inline HTML reply status tables into structured ``ReplyItemAdvisory`` items."""
    if not html_text or "<table" not in html_text.lower():
        return ()
    parser = _HtmlTableParser()
    parser.feed(html_text)
    advisories: list[ReplyItemAdvisory] = []

    header_tokens = {
        "item",
        "document",
        "requirement",
        "line",
        "coverage",
        "schedule",
        "status",
        "disposition",
        "notes",
        "comment",
        "comments",
    }
    for rows in parser.tables:
        if not rows:
            continue
        start_idx = 0
        first_lower = [c.lower().strip() for c in rows[0]]
        if len(rows) >= 2 and any(
            any(tok in col for tok in header_tokens) for col in first_lower
        ):
            start_idx = 1
        for row in rows[start_idx:]:
            if len(row) < 2:
                continue
            item_key = row[0].strip()
            raw_status = row[1].strip()
            note_text = " | ".join(c.strip() for c in row[2:] if c.strip())
            if not item_key or not (raw_status or note_text):
                continue
            advisories.append(
                _classify_advisory_row(
                    item_key=item_key,
                    raw_status=raw_status,
                    note_text=note_text,
                    source_envelope_id=source_envelope_id,
                )
            )
    return tuple(advisories)


def apply_advisories_to_requirements(
    requirements: Sequence[Requirement],
    advisories: Sequence[ReplyItemAdvisory],
) -> tuple[list[Requirement], str | None]:
    """Annotate Sense-B ``Requirement`` items with reply-table advisories (ADR-14 D4).

    Never sets ``RequirementStatus.waived`` on Bridge authority; preserves caller
    requirement statuses verbatim and surfaces ``waiver_recommended:<kind>`` for the
    calling agent to accept or decline.
    """
    by_norm_key: dict[str, ReplyItemAdvisory] = {
        re.sub(r"[^a-z0-9]+", "", adv.item_key.lower()): adv for adv in advisories
    }
    updated: list[Requirement] = []
    latest_due: str | None = None
    for req in requirements:
        norm_item = re.sub(r"[^a-z0-9]+", "", req.item.lower())
        matched = by_norm_key.get(norm_item)
        if matched is None:
            for key_slug, cand in by_norm_key.items():
                if key_slug and (key_slug in norm_item or norm_item in key_slug):
                    matched = cand
                    break
        if matched is None:
            updated.append(req)
            continue
        is_waiver_rec = matched.recommended_requirement_status == "waiver_recommended"
        if matched.suggested_due_date and (
            latest_due is None or matched.suggested_due_date > latest_due
        ):
            latest_due = matched.suggested_due_date
        msg_parts = [matched.raw_status]
        if is_waiver_rec:
            msg_parts.insert(0, "waiver recommended")
        if matched.note:
            msg_parts.append(matched.note)
        if matched.suggested_due_date:
            msg_parts.append(f"due {matched.suggested_due_date}")
        reason = f"waiver_recommended:{matched.kind}" if is_waiver_rec else matched.kind
        updated.append(
            req.model_copy(
                update={
                    "status": req.status,
                    "reason_code": reason,
                    "message": " — ".join(p for p in msg_parts if p),
                }
            )
        )
    return updated, latest_due


__all__ = [
    "AdvisoryKind",
    "ReplyItemAdvisory",
    "apply_advisories_to_requirements",
    "parse_html_reply_advisories",
]
