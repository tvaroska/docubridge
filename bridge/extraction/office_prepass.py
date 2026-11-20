"""Resilient multi-format Office prepass, issuer mismatch gate, and 1->N fan-out.

Provides pure-Python XML/text readers for ``.docx`` (separating accepted text from
``<w:del>`` Tracked Changes), ``.rtf``, and ``.xlsx``/``.xlsm`` (with malformed
``sharedStrings.xml`` fallback and Column-A row visibility filtering), deterministic
filename-vs-content issuer mismatch checks, and ``1 -> N`` bundled sub-entity fan-out.
"""

from __future__ import annotations

import contextlib
import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from bridge.extraction.issuer_fanout import (
    IssuerMismatchResult as IssuerMismatchResult,
)
from bridge.extraction.issuer_fanout import (
    detect_issuer_mismatch as detect_issuer_mismatch,
)
from bridge.extraction.issuer_fanout import (
    expand_bundled_extractions as expand_bundled_extractions,
)


def _wordset(spec: str) -> frozenset[str]:
    return frozenset(spec.split())


_HIDDEN_COL_VALUES = _wordset("false 0 no n hidden hide exclude excluded inactive off")
_VISIBLE_HEADER_HINTS = _wordset(
    "visible visibility include included show active display is_visible"
)
_RTF_SKIP_DESTINATIONS = _wordset(
    "fonttbl colortbl stylesheet info pict object header headerl headerr headerf "
    "footer footerl footerr footerf fldinst listtable listoverridetable rsidtbl "
    "generator themedata colorschememapping datastore latentstyles xmlnstbl"
)
_CELL_REF_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
_SHARED_SI_RE = re.compile(r"<(?:\w+:)?si\b[^>]*>(.*?)</(?:\w+:)?si>", re.DOTALL)
_SHARED_T_RE = re.compile(r"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", re.DOTALL)
_XML_UNSAFE_DECL_RE = re.compile(rb"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _safe_parse_xml(raw: bytes) -> ET.Element:
    """Parse Office Open XML bytes while rejecting DTD/ENTITY declarations (XXE guard)."""
    if _XML_UNSAFE_DECL_RE.search(raw):
        raise ET.ParseError(
            "Refusing XML payload containing DOCTYPE or ENTITY declaration"
        )
    return ET.fromstring(raw)  # noqa: S314 - DTD/ENTITY declarations rejected above (XXE guard)


class DocxRedlineExtract(BaseModel):
    """Structured `.docx` extraction separating accepted text from Tracked Changes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted_text: str
    deleted_spans: tuple[str, ...] = ()
    inserted_spans: tuple[str, ...] = ()
    has_tracked_changes: bool = False
    tables: tuple[tuple[tuple[str, ...], ...], ...] = ()


class ResilientSheetExtract(BaseModel):
    """Extracted rows from one worksheet in a resilient `.xlsx`/`.xlsm` pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    rows: tuple[tuple[str, ...], ...] = ()
    filtered_hidden_rows: int = Field(default=0, ge=0)


class ResilientWorkbookExtract(BaseModel):
    """Extracted workbook payload from `extract_xlsx_resilient`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sheets: tuple[ResilientSheetExtract, ...] = ()
    rendered_text: str = ""
    used_resilient_fallback: bool = False
    filtered_hidden_rows: int = Field(default=0, ge=0)
    warnings: tuple[str, ...] = ()


def _local_tag(tag: str) -> str:
    """Strip XML `{namespace}` prefix from an ElementTree tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


@dataclass
class _DocxTrackState:
    deleted_spans: list[str] = field(default_factory=list)
    inserted_spans: list[str] = field(default_factory=list)
    saw_revision_tag: bool = False


def _walk_docx_revision(
    elem: ET.Element,
    lname: str,
    accepted_buf: list[str],
    state: _DocxTrackState,
    *,
    in_del: bool,
) -> None:
    state.saw_revision_tag = True
    rev_buf: list[str] = []
    if lname in ("del", "moveFrom"):
        for child in elem:
            _walk_docx_inline(child, rev_buf, state, in_del=True)
        joined = "".join(rev_buf).strip()
        if joined:
            state.deleted_spans.append(joined)
        return
    for child in elem:
        _walk_docx_inline(child, rev_buf, state, in_del=in_del)
    joined_ins = "".join(rev_buf)
    if not in_del:
        accepted_buf.append(joined_ins)
        if joined_ins.strip():
            state.inserted_spans.append(joined_ins.strip())


_INLINE_LITERALS: dict[str, str] = {"tab": "\t", "br": "\n", "cr": "\n"}


def _walk_docx_inline(
    elem: ET.Element,
    accepted_buf: list[str],
    state: _DocxTrackState,
    *,
    in_del: bool = False,
) -> None:
    """Recursively walk WordprocessingML inline runs respecting `<w:del>` and `<w:ins>`."""
    lname = _local_tag(elem.tag)
    if lname in ("del", "moveFrom", "ins", "moveTo"):
        _walk_docx_revision(elem, lname, accepted_buf, state, in_del=in_del)
        return
    if lname == "delText":
        if elem.text:
            if in_del:
                accepted_buf.append(elem.text)
            else:
                state.saw_revision_tag = True
                if elem.text.strip():
                    state.deleted_spans.append(elem.text.strip())
        return
    if lname == "t":
        if elem.text:
            accepted_buf.append(elem.text)
        return
    if lname in _INLINE_LITERALS:
        accepted_buf.append(_INLINE_LITERALS[lname])
        return
    for child in elem:
        _walk_docx_inline(child, accepted_buf, state, in_del=in_del)


def _extract_paragraph_accepted(p_elem: ET.Element, state: _DocxTrackState) -> str:
    buf: list[str] = []
    _walk_docx_inline(p_elem, buf, state)
    return "".join(buf).strip()


def _extract_docx_table(
    tbl_elem: ET.Element, state: _DocxTrackState
) -> tuple[list[tuple[str, ...]], list[str]]:
    tbl_rows: list[tuple[str, ...]] = []
    rendered: list[str] = []
    for tr in tbl_elem:
        if _local_tag(tr.tag) != "tr":
            continue
        row_cells: list[str] = []
        for tc in tr:
            if _local_tag(tc.tag) != "tc":
                continue
            cell_paras = [
                ptxt
                for cell_child in tc
                if _local_tag(cell_child.tag) == "p"
                and (ptxt := _extract_paragraph_accepted(cell_child, state))
            ]
            row_cells.append(" ".join(cell_paras).strip())
        if any(row_cells):
            tbl_rows.append(tuple(row_cells))
            rendered.append(" | ".join(row_cells))
    return tbl_rows, rendered


def extract_docx_with_redlines(data: bytes) -> DocxRedlineExtract:
    """Read a ``.docx`` package, separating accepted text/tables from ``<w:del>`` redlines."""
    state = _DocxTrackState()
    lines: list[str] = []
    extracted_tables: list[tuple[tuple[str, ...], ...]] = []

    with zipfile.ZipFile(io.BytesIO(data), mode="r") as zf:
        names = set(zf.namelist())
        part_order = [
            *(sorted(n for n in names if re.match(r"^word/header\d*\.xml$", n))),
            *(["word/document.xml"] if "word/document.xml" in names else []),
            *(sorted(n for n in names if re.match(r"^word/footer\d*\.xml$", n))),
        ]
        if not part_order:
            raise ValueError("Invalid .docx archive: missing word/document.xml")

        for part_name in part_order:
            root = _safe_parse_xml(zf.read(part_name))
            container = next(
                (child for child in root if _local_tag(child.tag) == "body"),
                root,
            )
            for block in container:
                btag = _local_tag(block.tag)
                if btag == "p":
                    text = _extract_paragraph_accepted(block, state)
                    if text:
                        lines.append(text)
                elif btag == "tbl":
                    tbl_rows, tbl_lines = _extract_docx_table(block, state)
                    if tbl_rows:
                        extracted_tables.append(tuple(tbl_rows))
                        lines.extend(tbl_lines)

    has_changes = bool(
        state.saw_revision_tag or state.deleted_spans or state.inserted_spans
    )
    return DocxRedlineExtract(
        accepted_text="\n".join(lines).strip(),
        deleted_spans=tuple(state.deleted_spans),
        inserted_spans=tuple(state.inserted_spans),
        has_tracked_changes=has_changes,
        tables=tuple(extracted_tables),
    )


_RTF_SIMPLE_ESCAPES: dict[str, str] = {
    "\\": "\\",
    "{": "{",
    "}": "}",
    "~": " ",
    "_": "-",
}
_RTF_WORD_OUTPUT: dict[str, str] = {
    "par": "\n",
    "line": "\n",
    "row": "\n",
    "tab": "\t",
    "cell": " | ",
    "nestcell": " | ",
}


class _RtfParser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.n = len(src)
        self.i = 0
        self.out: list[str] = []
        self.stack: list[tuple[int, bool]] = [(1, False)]
        self.skip_ansi_chars = 0

    def _emit_if_visible(self, text: str, *, consume_skip: bool = True) -> None:
        _, ignoring = self.stack[-1]
        if consume_skip and self.skip_ansi_chars > 0:
            self.skip_ansi_chars -= 1
            return
        if not ignoring:
            self.out.append(text)

    def _handle_control_word(self) -> None:
        uc_count, ignoring = self.stack[-1]
        m = re.match(r"([A-Za-z]+)(-?\d+)? ?", self.src[self.i :])
        if not m:
            self.i += 1
            return
        word, param_str = m.group(1), m.group(2)
        self.i += m.end()
        if word in _RTF_SKIP_DESTINATIONS:
            self.stack[-1] = (uc_count, True)
        elif word == "uc" and param_str is not None:
            self.stack[-1] = (max(0, int(param_str)), ignoring)
        elif word == "u" and param_str is not None:
            code = int(param_str)
            if code < 0:
                code += 65536
            if not ignoring:
                self.out.append(chr(code))
            self.skip_ansi_chars = self.stack[-1][0]
        elif not ignoring and word in _RTF_WORD_OUTPUT:
            self.out.append(_RTF_WORD_OUTPUT[word])

    def _handle_escape(self) -> None:
        self.i += 1
        if self.i >= self.n:
            return
        nxt = self.src[self.i]
        if nxt in _RTF_SIMPLE_ESCAPES:
            self._emit_if_visible(_RTF_SIMPLE_ESCAPES[nxt], consume_skip=nxt in "\\{}")
            self.i += 1
            return
        if nxt in ("\n", "\r"):
            self._emit_if_visible("\n", consume_skip=False)
            self.i += 1
            return
        if nxt == "'" and self.i + 2 < self.n:
            hex_pair = self.src[self.i + 1 : self.i + 3]
            self.i += 3
            if self.skip_ansi_chars > 0:
                self.skip_ansi_chars -= 1
                return
            if not self.stack[-1][1]:
                with contextlib.suppress(ValueError):
                    self.out.append(
                        bytes.fromhex(hex_pair).decode("cp1252", errors="replace")
                    )
            return
        self._handle_control_word()

    def parse(self) -> str:
        while self.i < self.n:
            ch = self.src[self.i]
            uc_count, ignoring = self.stack[-1]
            if ch == "{":
                if self.src[self.i + 1 : self.i + 3] == "\\*":
                    self.stack.append((uc_count, True))
                    self.i += 3
                else:
                    self.stack.append((uc_count, ignoring))
                    self.i += 1
            elif ch == "}":
                if len(self.stack) > 1:
                    self.stack.pop()
                self.skip_ansi_chars = 0
                self.i += 1
            elif ch == "\\":
                self._handle_escape()
            elif ch in ("\r", "\n"):
                self.i += 1
            else:
                self._emit_if_visible(ch)
                self.i += 1
        return _normalize_rtf_output("".join(self.out))


def _normalize_rtf_output(raw: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in raw.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line.endswith("|"):
            line = line[:-1].rstrip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def extract_rtf_text(data: bytes | str) -> str:
    """Strip RTF control words, font/style groups, and hex/Unicode escapes to plain text."""
    src = data.decode("latin-1") if isinstance(data, bytes) else data
    return _RtfParser(src).parse()


def _col_letters_to_index(letters: str) -> int:
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _read_shared_strings_resilient(
    zf: zipfile.ZipFile,
    warnings: list[str],
) -> tuple[list[str], bool]:
    """Read `xl/sharedStrings.xml`, falling back to regex salvage on corrupt XML."""
    target = next(
        (n for n in zf.namelist() if n.lower() == "xl/sharedstrings.xml"),
        None,
    )
    if target is None:
        return [], False
    raw_bytes = zf.read(target)
    try:
        root = _safe_parse_xml(raw_bytes)
        items: list[str] = []
        for si in root:
            if _local_tag(si.tag) != "si":
                continue
            chunks = [
                elem.text or "" for elem in si.iter() if _local_tag(elem.tag) == "t"
            ]
            items.append("".join(chunks))
        return items, False
    except ET.ParseError as exc:
        warnings.append(f"sharedStrings.xml parse fallback activated: {exc}")
        decoded = raw_bytes.decode("utf-8", errors="replace")
        salvaged: list[str] = []
        for si_match in _SHARED_SI_RE.finditer(decoded):
            inner = si_match.group(1)
            t_parts = [html.unescape(m.group(1)) for m in _SHARED_T_RE.finditer(inner)]
            salvaged.append("".join(t_parts))
        return salvaged, True


def _read_workbook_rels(
    zf: zipfile.ZipFile, names: set[str], rels_path: str
) -> dict[str, str]:
    rel_map: dict[str, str] = {}
    if rels_path not in names:
        return rel_map
    for rel in _safe_parse_xml(zf.read(rels_path)):
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "").lstrip("/")
        if rid and target:
            rel_map[rid] = target if target.startswith("xl/") else f"xl/{target}"
    return rel_map


def _discover_sheets_resilient(
    zf: zipfile.ZipFile,
    warnings: list[str],
) -> tuple[list[tuple[str, str]], bool]:
    """Resolve `(sheet_name, zip_member_path)` pairs from `workbook.xml` or directory scan."""
    names = set(zf.namelist())
    wb_path = "xl/workbook.xml"
    rels_path = "xl/_rels/workbook.xml.rels"
    used_fallback = False

    if wb_path in names:
        try:
            rel_map = _read_workbook_rels(zf, names, rels_path)
            wb_root = _safe_parse_xml(zf.read(wb_path))
            resolved: list[tuple[str, str]] = []
            sheet_idx = 0
            for elem in wb_root.iter():
                if _local_tag(elem.tag) != "sheet":
                    continue
                sheet_idx += 1
                sname = elem.attrib.get("name") or f"Sheet{sheet_idx}"
                rid = next(
                    (v for k, v in elem.attrib.items() if _local_tag(k) == "id"),
                    "",
                )
                candidate = rel_map.get(rid, f"xl/worksheets/sheet{sheet_idx}.xml")
                if candidate in names:
                    resolved.append((sname, candidate))
            if resolved:
                return resolved, False
        except ET.ParseError as exc:
            warnings.append(f"workbook.xml parse fallback activated: {exc}")
            used_fallback = True

    fallback_paths = sorted(
        n for n in names if re.match(r"^xl/worksheets/[^/]+\.xml$", n)
    )
    if wb_path not in names and fallback_paths:
        warnings.append(
            "workbook.xml missing; discovered worksheets from archive listing"
        )
        used_fallback = True
    return [
        (f"Sheet{idx}", path) for idx, path in enumerate(fallback_paths, start=1)
    ], used_fallback


@dataclass
class _XlsxParseCtx:
    shared_strings: list[str]
    warnings: list[str]
    used_fallback: bool = False


def _resolve_shared_string(val_text: str, ctx: _XlsxParseCtx) -> str:
    if not val_text:
        return ""
    try:
        idx = int(val_text)
    except ValueError:
        return val_text
    if 0 <= idx < len(ctx.shared_strings):
        return ctx.shared_strings[idx].strip()
    ctx.used_fallback = True
    if len(ctx.warnings) < 10:
        ctx.warnings.append(f"Unresolved sharedStrings index {idx}")
    return ""


def _parse_cell_value(cell_elem: ET.Element, ctx: _XlsxParseCtx) -> str:
    ctype = cell_elem.attrib.get("t", "")
    if ctype == "inlineStr":
        parts = [
            sub.text or "" for sub in cell_elem.iter() if _local_tag(sub.tag) == "t"
        ]
        return "".join(parts).strip()

    val_text = next(
        (
            child.text.strip()
            for child in cell_elem
            if _local_tag(child.tag) == "v" and child.text is not None
        ),
        "",
    )
    if ctype == "s":
        return _resolve_shared_string(val_text, ctx)
    if ctype == "b":
        return "TRUE" if val_text in ("1", "true", "TRUE") else "FALSE"
    return val_text


def _dense_row_from_elem(
    row_elem: ET.Element, ctx: _XlsxParseCtx
) -> tuple[str, ...] | None:
    cells_by_col: dict[int, str] = {}
    next_col = 0
    for c_elem in row_elem:
        if _local_tag(c_elem.tag) != "c":
            continue
        ref = c_elem.attrib.get("r", "")
        m = _CELL_REF_RE.match(ref)
        col_idx = _col_letters_to_index(m.group(1)) if m else next_col
        next_col = col_idx + 1
        cells_by_col[col_idx] = _parse_cell_value(c_elem, ctx)
    if not cells_by_col:
        return None
    dense_row = tuple(cells_by_col.get(i, "") for i in range(max(cells_by_col) + 1))
    return dense_row if any(cell.strip() for cell in dense_row) else None


def _filter_visibility_row(
    dense_row: tuple[str, ...], *, saw_header: bool, col_a_is_flag_header: bool
) -> tuple[tuple[str, ...] | None, bool, bool]:
    col_a = dense_row[0].strip().lower()
    if not saw_header:
        if col_a in _VISIBLE_HEADER_HINTS:
            return dense_row[1:], True, True
        if col_a in _HIDDEN_COL_VALUES:
            return None, True, False
        return dense_row, True, False
    if col_a in _HIDDEN_COL_VALUES:
        return None, True, col_a_is_flag_header
    return (
        dense_row[1:] if col_a_is_flag_header else dense_row,
        True,
        col_a_is_flag_header,
    )


def _extract_sheet_rows(
    sroot: ET.Element,
    ctx: _XlsxParseCtx,
    *,
    respect_visibility_flag_col: bool,
    max_rows: int,
) -> tuple[list[tuple[str, ...]], int]:
    parsed_rows: list[tuple[str, ...]] = []
    sheet_filtered = 0
    saw_header = False
    col_a_is_flag_header = False

    for row_elem in sroot.iter():
        if _local_tag(row_elem.tag) != "row":
            continue
        if len(parsed_rows) >= max_rows:
            break
        if respect_visibility_flag_col and row_elem.attrib.get("hidden") in (
            "1",
            "true",
        ):
            sheet_filtered += 1
            continue
        dense_row = _dense_row_from_elem(row_elem, ctx)
        if dense_row is None:
            continue
        if respect_visibility_flag_col:
            dense_row, saw_header, col_a_is_flag_header = _filter_visibility_row(
                dense_row,
                saw_header=saw_header,
                col_a_is_flag_header=col_a_is_flag_header,
            )
            if dense_row is None:
                sheet_filtered += 1
                continue
        parsed_rows.append(dense_row)
    return parsed_rows, sheet_filtered


def extract_xlsx_resilient(
    data: bytes,
    *,
    respect_visibility_flag_col: bool = False,
    max_rows: int = 5000,
) -> ResilientWorkbookExtract:
    """Read an ``.xlsx``/``.xlsm`` workbook via direct XML with resilient fallbacks.

    Recovers gracefully when ``xl/sharedStrings.xml`` or ``xl/workbook.xml`` fails
    standard XML parsing, and optionally filters hidden rows via ``<row hidden="1">``
    or Column-A boolean visibility flags when ``respect_visibility_flag_col=True``.
    """
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data), mode="r") as zf:
        shared_strings, ss_fallback = _read_shared_strings_resilient(zf, warnings)
        sheet_specs, wb_fallback = _discover_sheets_resilient(zf, warnings)
        ctx = _XlsxParseCtx(
            shared_strings=shared_strings,
            warnings=warnings,
            used_fallback=bool(ss_fallback or wb_fallback),
        )
        sheets_out: list[ResilientSheetExtract] = []
        total_filtered = 0
        rendered_blocks: list[str] = []

        for sheet_name, member_path in sheet_specs:
            try:
                sroot = _safe_parse_xml(zf.read(member_path))
            except ET.ParseError as exc:
                ctx.used_fallback = True
                warnings.append(f"{member_path} XML parse error: {exc}")
                continue
            parsed_rows, sheet_filtered = _extract_sheet_rows(
                sroot,
                ctx,
                respect_visibility_flag_col=respect_visibility_flag_col,
                max_rows=max_rows,
            )
            total_filtered += sheet_filtered
            sheets_out.append(
                ResilientSheetExtract(
                    name=sheet_name,
                    rows=tuple(parsed_rows),
                    filtered_hidden_rows=sheet_filtered,
                )
            )
            if parsed_rows:
                row_lines = [" | ".join(r) for r in parsed_rows]
                rendered_blocks.append(
                    f"[Sheet: {sheet_name}]\n" + "\n".join(row_lines)
                )

    return ResilientWorkbookExtract(
        sheets=tuple(sheets_out),
        rendered_text="\n\n".join(rendered_blocks),
        used_resilient_fallback=ctx.used_fallback,
        filtered_hidden_rows=total_filtered,
        warnings=tuple(warnings),
    )


__all__ = [
    "DocxRedlineExtract",
    "IssuerMismatchResult",
    "ResilientSheetExtract",
    "ResilientWorkbookExtract",
    "detect_issuer_mismatch",
    "expand_bundled_extractions",
    "extract_docx_with_redlines",
    "extract_rtf_text",
    "extract_xlsx_resilient",
]
