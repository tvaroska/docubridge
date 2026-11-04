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
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contract.wire import LedgerEntry


def _wordset(spec: str) -> frozenset[str]:
    return frozenset(spec.split())


_HIDDEN_COL_VALUES = _wordset("false 0 no n hidden hide exclude excluded inactive off")
_VISIBLE_HEADER_HINTS = _wordset(
    "visible visibility include included show active display is_visible"
)
_ISSUER_NOISE_TOKENS = _wordset(
    "quote quotes proposal policy binder schedule addendum endorsement agreement "
    "disclosure appraisal statement guide summary final draft clean revised rev ver "
    "copy signed executed submission package packet inc incorporated llc ltd limited "
    "corp corporation co company plc lp llp na group holdings insurance assurance "
    "reinsurance underwriters underwriting syndicate mutual casualty property "
    "indemnity benefits health life financial finance bank banking lending loans "
    "mortgage services solutions partners national global international commercial "
    "enterprise carrier lender pdf docx xlsx xlsm rtf zip msg eml"
)
_RTF_SKIP_DESTINATIONS = _wordset(
    "fonttbl colortbl stylesheet info pict object header headerl headerr headerf "
    "footer footerl footerr footerf fldinst listtable listoverridetable rsidtbl "
    "generator themedata colorschememapping datastore latentstyles xmlnstbl"
)
_CELL_REF_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
_SHARED_SI_RE = re.compile(r"<(?:\w+:)?si\b[^>]*>(.*?)</(?:\w+:)?si>", re.DOTALL)
_SHARED_T_RE = re.compile(r"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", re.DOTALL)


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


class IssuerMismatchResult(BaseModel):
    """Deterministic filename-vs-content issuer alignment check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mismatch: bool
    filename_issuer_hint: str | None = None
    extracted_issuer: str | None = None
    reason_code: str | None = None
    severity: Literal["none", "warning", "quarantine"] = "none"
    detail: str = ""


def _local_tag(tag: str) -> str:
    """Strip XML `{namespace}` prefix from an ElementTree tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _walk_docx_inline(
    elem: ET.Element,
    *,
    in_del: bool,
    in_ins: bool,
    accepted_buf: list[str],
    deleted_spans: list[str],
    inserted_spans: list[str],
    saw_revision_tag: list[bool],
) -> None:
    """Recursively walk WordprocessingML inline runs respecting `<w:del>` and `<w:ins>`."""
    lname = _local_tag(elem.tag)
    if lname in ("del", "moveFrom"):
        saw_revision_tag[0] = True
        del_buf: list[str] = []
        for child in elem:
            _walk_docx_inline(
                child,
                in_del=True,
                in_ins=False,
                accepted_buf=del_buf,
                deleted_spans=deleted_spans,
                inserted_spans=inserted_spans,
                saw_revision_tag=saw_revision_tag,
            )
        joined = "".join(del_buf).strip()
        if joined:
            deleted_spans.append(joined)
        return

    if lname in ("ins", "moveTo"):
        saw_revision_tag[0] = True
        ins_buf: list[str] = []
        for child in elem:
            _walk_docx_inline(
                child,
                in_del=in_del,
                in_ins=True,
                accepted_buf=ins_buf,
                deleted_spans=deleted_spans,
                inserted_spans=inserted_spans,
                saw_revision_tag=saw_revision_tag,
            )
        joined_ins = "".join(ins_buf)
        if not in_del:
            accepted_buf.append(joined_ins)
            if joined_ins.strip():
                inserted_spans.append(joined_ins.strip())
        return

    if lname == "delText":
        if elem.text:
            if in_del:
                accepted_buf.append(elem.text)
            else:
                saw_revision_tag[0] = True
                if elem.text.strip():
                    deleted_spans.append(elem.text.strip())
        return

    if lname == "t":
        if elem.text:
            accepted_buf.append(elem.text)
        return

    if lname == "tab":
        accepted_buf.append("\t")
        return

    if lname in ("br", "cr"):
        accepted_buf.append("\n")
        return

    for child in elem:
        _walk_docx_inline(
            child,
            in_del=in_del,
            in_ins=in_ins,
            accepted_buf=accepted_buf,
            deleted_spans=deleted_spans,
            inserted_spans=inserted_spans,
            saw_revision_tag=saw_revision_tag,
        )


def _extract_paragraph_accepted(
    p_elem: ET.Element,
    deleted_spans: list[str],
    inserted_spans: list[str],
    saw_revision_tag: list[bool],
) -> str:
    buf: list[str] = []
    _walk_docx_inline(
        p_elem,
        in_del=False,
        in_ins=False,
        accepted_buf=buf,
        deleted_spans=deleted_spans,
        inserted_spans=inserted_spans,
        saw_revision_tag=saw_revision_tag,
    )
    return "".join(buf).strip()


def extract_docx_with_redlines(data: bytes) -> DocxRedlineExtract:
    """Read a ``.docx`` package, separating accepted text/tables from ``<w:del>`` redlines."""
    deleted_spans: list[str] = []
    inserted_spans: list[str] = []
    saw_revision_tag = [False]
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
            root = ET.fromstring(zf.read(part_name))
            container = root
            for child in root:
                if _local_tag(child.tag) == "body":
                    container = child
                    break
            for block in container:
                btag = _local_tag(block.tag)
                if btag == "p":
                    text = _extract_paragraph_accepted(
                        block, deleted_spans, inserted_spans, saw_revision_tag
                    )
                    if text:
                        lines.append(text)
                elif btag == "tbl":
                    tbl_rows: list[tuple[str, ...]] = []
                    for tr in block:
                        if _local_tag(tr.tag) != "tr":
                            continue
                        row_cells: list[str] = []
                        for tc in tr:
                            if _local_tag(tc.tag) != "tc":
                                continue
                            cell_paras: list[str] = []
                            for cell_child in tc:
                                if _local_tag(cell_child.tag) == "p":
                                    ptxt = _extract_paragraph_accepted(
                                        cell_child,
                                        deleted_spans,
                                        inserted_spans,
                                        saw_revision_tag,
                                    )
                                    if ptxt:
                                        cell_paras.append(ptxt)
                            row_cells.append(" ".join(cell_paras).strip())
                        if any(row_cells):
                            tbl_rows.append(tuple(row_cells))
                            lines.append(" | ".join(row_cells))
                    if tbl_rows:
                        extracted_tables.append(tuple(tbl_rows))

    has_changes = bool(saw_revision_tag[0] or deleted_spans or inserted_spans)
    return DocxRedlineExtract(
        accepted_text="\n".join(lines).strip(),
        deleted_spans=tuple(deleted_spans),
        inserted_spans=tuple(inserted_spans),
        has_tracked_changes=has_changes,
        tables=tuple(extracted_tables),
    )


def extract_rtf_text(data: bytes | str) -> str:
    """Strip RTF control words, font/style groups, and hex/Unicode escapes to plain text."""
    src = data.decode("latin-1") if isinstance(data, bytes) else data
    out: list[str] = []
    # Stack of (uc_skip_count, ignore_group)
    stack: list[tuple[int, bool]] = [(1, False)]
    i = 0
    n = len(src)
    skip_ansi_chars = 0

    while i < n:
        ch = src[i]
        uc_count, ignoring = stack[-1]

        if ch == "{":
            # Check for `{\*\dest` ignorable destination group
            if src[i + 1 : i + 3] == "\\*":
                stack.append((uc_count, True))
                i += 3
                continue
            stack.append((uc_count, ignoring))
            i += 1
            continue

        if ch == "}":
            if len(stack) > 1:
                stack.pop()
            skip_ansi_chars = 0
            i += 1
            continue

        if ch == "\\":
            i += 1
            if i >= n:
                break
            nxt = src[i]
            if nxt in ("\\", "{", "}"):
                if not ignoring and skip_ansi_chars == 0:
                    out.append(nxt)
                elif skip_ansi_chars > 0:
                    skip_ansi_chars -= 1
                i += 1
                continue
            if nxt == "~":
                if not ignoring and skip_ansi_chars == 0:
                    out.append(" ")
                i += 1
                continue
            if nxt == "_":
                if not ignoring and skip_ansi_chars == 0:
                    out.append("-")
                i += 1
                continue
            if nxt in ("\n", "\r"):
                if not ignoring:
                    out.append("\n")
                i += 1
                continue
            if nxt == "'" and i + 2 < n:
                hex_pair = src[i + 1 : i + 3]
                i += 3
                if skip_ansi_chars > 0:
                    skip_ansi_chars -= 1
                    continue
                if not ignoring:
                    with contextlib.suppress(ValueError):
                        out.append(
                            bytes.fromhex(hex_pair).decode("cp1252", errors="replace")
                        )
                continue

            # Parse alphabetic control word + optional signed integer parameter
            m = re.match(r"([A-Za-z]+)(-?\d+)? ?", src[i:])
            if not m:
                i += 1
                continue
            word = m.group(1)
            param_str = m.group(2)
            i += m.end()

            if word in _RTF_SKIP_DESTINATIONS:
                stack[-1] = (uc_count, True)
                continue
            if word == "uc" and param_str is not None:
                stack[-1] = (max(0, int(param_str)), ignoring)
                continue
            if word == "u" and param_str is not None:
                code = int(param_str)
                if code < 0:
                    code += 65536
                if not ignoring:
                    out.append(chr(code))
                skip_ansi_chars = stack[-1][0]
                continue
            if not ignoring:
                if word in ("par", "line", "row"):
                    out.append("\n")
                elif word == "tab":
                    out.append("\t")
                elif word in ("cell", "nestcell"):
                    out.append(" | ")
            continue

        if ch in ("\r", "\n"):
            i += 1
            continue

        if skip_ansi_chars > 0:
            skip_ansi_chars -= 1
            i += 1
            continue

        if not ignoring:
            out.append(ch)
        i += 1

    cleaned_lines: list[str] = []
    for raw_line in "".join(out).splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line.endswith("|"):
            line = line[:-1].rstrip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


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
        root = ET.fromstring(raw_bytes)
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
            rel_map: dict[str, str] = {}
            if rels_path in names:
                rels_root = ET.fromstring(zf.read(rels_path))
                for rel in rels_root:
                    rid = rel.attrib.get("Id", "")
                    target = rel.attrib.get("Target", "").lstrip("/")
                    if rid and target:
                        full = target if target.startswith("xl/") else f"xl/{target}"
                        rel_map[rid] = full

            wb_root = ET.fromstring(zf.read(wb_path))
            resolved: list[tuple[str, str]] = []
            sheet_idx = 0
            for elem in wb_root.iter():
                if _local_tag(elem.tag) != "sheet":
                    continue
                sheet_idx += 1
                sname = elem.attrib.get("name") or f"Sheet{sheet_idx}"
                rid = next(
                    (
                        val
                        for key, val in elem.attrib.items()
                        if _local_tag(key) == "id"
                    ),
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


def _parse_cell_value(
    cell_elem: ET.Element,
    shared_strings: list[str],
    warnings: list[str],
    fallback_flag: list[bool],
) -> str:
    ctype = cell_elem.attrib.get("t", "")
    if ctype == "inlineStr":
        parts = [
            sub.text or "" for sub in cell_elem.iter() if _local_tag(sub.tag) == "t"
        ]
        return "".join(parts).strip()

    val_text = ""
    for child in cell_elem:
        if _local_tag(child.tag) == "v" and child.text is not None:
            val_text = child.text.strip()
            break

    if ctype == "s":
        if not val_text:
            return ""
        try:
            idx = int(val_text)
        except ValueError:
            return val_text
        if 0 <= idx < len(shared_strings):
            return shared_strings[idx].strip()
        fallback_flag[0] = True
        if len(warnings) < 10:
            warnings.append(f"Unresolved sharedStrings index {idx}")
        return ""
    if ctype == "b":
        return "TRUE" if val_text in ("1", "true", "TRUE") else "FALSE"
    return val_text


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
        fallback_flag = [bool(ss_fallback or wb_fallback)]

        sheets_out: list[ResilientSheetExtract] = []
        total_filtered = 0
        rendered_blocks: list[str] = []

        for sheet_name, member_path in sheet_specs:
            raw_sheet = zf.read(member_path)
            try:
                sroot = ET.fromstring(raw_sheet)
            except ET.ParseError as exc:
                fallback_flag[0] = True
                warnings.append(f"{member_path} XML parse error: {exc}")
                continue

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

                cells_by_col: dict[int, str] = {}
                next_col = 0
                for c_elem in row_elem:
                    if _local_tag(c_elem.tag) != "c":
                        continue
                    ref = c_elem.attrib.get("r", "")
                    m = _CELL_REF_RE.match(ref)
                    col_idx = _col_letters_to_index(m.group(1)) if m else next_col
                    next_col = col_idx + 1
                    cells_by_col[col_idx] = _parse_cell_value(
                        c_elem, shared_strings, warnings, fallback_flag
                    )

                if not cells_by_col:
                    continue
                max_col = max(cells_by_col.keys())
                dense_row = tuple(cells_by_col.get(i, "") for i in range(max_col + 1))
                if not any(cell.strip() for cell in dense_row):
                    continue

                if respect_visibility_flag_col:
                    col_a = dense_row[0].strip().lower()
                    if not saw_header:
                        saw_header = True
                        if col_a in _VISIBLE_HEADER_HINTS:
                            col_a_is_flag_header = True
                            dense_row = dense_row[1:]
                        elif col_a in _HIDDEN_COL_VALUES:
                            sheet_filtered += 1
                            continue
                    else:
                        if col_a in _HIDDEN_COL_VALUES:
                            sheet_filtered += 1
                            continue
                        if col_a_is_flag_header:
                            dense_row = dense_row[1:]

                parsed_rows.append(dense_row)

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
        used_resilient_fallback=fallback_flag[0],
        filtered_hidden_rows=total_filtered,
        warnings=tuple(warnings),
    )


def _distinctive_issuer_tokens(label: str) -> set[str]:
    """Extract lowercase distinctive brand tokens from an issuer name or filename."""
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", label or "")
    raw_tokens = re.findall(r"[A-Za-z0-9]+", expanded.lower())
    out: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < 3 or tok in _ISSUER_NOISE_TOKENS:
            continue
        if (
            tok.isdigit()
            or re.match(r"^(?:19|20)\d{2}$", tok)
            or re.match(r"^v\d+$", tok)
        ):
            continue
        out.add(tok)
    return out


def detect_issuer_mismatch(
    filename: str,
    extracted_issuer: str | None,
    candidate_issuers: Sequence[str] = (),
) -> IssuerMismatchResult:
    """Detect mismatches between an issuer named in ``filename`` and ``extracted_issuer``."""
    cleaned_extracted = (extracted_issuer or "").strip() or None
    stem = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]

    file_tokens = _distinctive_issuer_tokens(stem)
    if not cleaned_extracted or not file_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=None,
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail="Insufficient filename or extracted issuer signal to flag mismatch.",
        )

    ext_tokens = _distinctive_issuer_tokens(cleaned_extracted)
    if not ext_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=None,
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail="Extracted issuer carries only generic entity tokens.",
        )

    matched_candidate: str | None = None
    for cand in candidate_issuers:
        cand_tokens = _distinctive_issuer_tokens(cand)
        if cand_tokens and cand_tokens.issubset(file_tokens):
            matched_candidate = cand.strip()
            break
        if cand_tokens and (cand_tokens & file_tokens):
            matched_candidate = cand.strip()

    if matched_candidate is not None:
        hint_tokens = _distinctive_issuer_tokens(matched_candidate)
        if hint_tokens & ext_tokens:
            return IssuerMismatchResult(
                mismatch=False,
                filename_issuer_hint=matched_candidate,
                extracted_issuer=cleaned_extracted,
                severity="none",
                detail=f"Filename issuer hint {matched_candidate!r} matches content issuer.",
            )
        return IssuerMismatchResult(
            mismatch=True,
            filename_issuer_hint=matched_candidate,
            extracted_issuer=cleaned_extracted,
            reason_code="issuer_filename_content_mismatch",
            severity="quarantine",
            detail=(
                f"Filename indicates issuer {matched_candidate!r}, "
                f"but document content asserts {cleaned_extracted!r}."
            ),
        )

    if file_tokens & ext_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=" ".join(sorted(file_tokens)),
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail="Filename tokens align with extracted issuer.",
        )

    # Heuristic prefix hint when candidate_issuers is omitted: inspect leading segment
    leading_seg = re.split(r"[_\-]+", stem, maxsplit=1)[0]
    leading_tokens = _distinctive_issuer_tokens(leading_seg)
    if leading_tokens and not (leading_tokens & ext_tokens):
        hint_label = leading_seg.strip()
        return IssuerMismatchResult(
            mismatch=True,
            filename_issuer_hint=hint_label,
            extracted_issuer=cleaned_extracted,
            reason_code="issuer_filename_content_mismatch",
            severity="quarantine",
            detail=(
                f"Filename prefix {hint_label!r} conflicts with extracted "
                f"content issuer {cleaned_extracted!r}."
            ),
        )

    return IssuerMismatchResult(
        mismatch=False,
        filename_issuer_hint=None,
        extracted_issuer=cleaned_extracted,
        severity="none",
        detail="No conflicting issuer prefix detected in filename.",
    )


def _slugify_key(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    return slug or "item"


def expand_bundled_extractions(
    parent_entry: LedgerEntry,
    *,
    split_key: str | None = None,
    table_name: str | None = None,
    sub_entities: Sequence[Mapping[str, Any]] | None = None,
) -> list[LedgerEntry]:
    """Fan out one bundled ``LedgerEntry`` into ``N`` keyed child ``LedgerEntry`` records.

    Preserves the single vaulted artifact provenance (``vault_uri``, ``artifact``,
    ``source_anchors``) while keying each child ``doc_id`` as ``{parent.doc_id}#{slug}``.
    """
    if sub_entities:
        children: list[LedgerEntry] = []
        for idx, sub in enumerate(sub_entities, start=1):
            nested_kf = sub.get("key_fields")
            if isinstance(nested_kf, Mapping):
                child_kf = {
                    **parent_entry.key_fields,
                    **{str(k): str(v) for k, v in nested_kf.items() if v is not None},
                }
            else:
                child_kf = {
                    **parent_entry.key_fields,
                    **{
                        str(k): str(v)
                        for k, v in sub.items()
                        if k not in ("tables", "sub_entity_id") and v is not None
                    },
                }
            label_val = str(
                sub.get("sub_entity_id")
                or (child_kf.get(split_key) if split_key else None)
                or child_kf.get("plan_code")
                or child_kf.get("plan_type")
                or child_kf.get("product_line")
                or child_kf.get("subsidiary")
                or f"part-{idx}"
            )
            child_tables = (
                dict(sub["tables"])
                if isinstance(sub.get("tables"), Mapping)
                else dict(parent_entry.tables)
            )
            children.append(
                parent_entry.model_copy(
                    update={
                        "doc_id": f"{parent_entry.doc_id}#{_slugify_key(label_val)}",
                        "key_fields": child_kf,
                        "tables": child_tables,
                    }
                )
            )
        return children

    target_tbl = table_name
    if target_tbl is None and parent_entry.tables:
        target_tbl = next(iter(parent_entry.tables.keys()))
    if not target_tbl or target_tbl not in parent_entry.tables:
        return [parent_entry]

    rows = parent_entry.tables[target_tbl]
    if not rows:
        return [parent_entry]

    resolved_key = split_key
    if resolved_key is None:
        candidate_cols = (
            "sub_entity_id",
            "plan_code",
            "plan_id",
            "plan_type",
            "product_line",
            "subsidiary",
            "entity_name",
            "location_id",
        )
        first_row = rows[0]
        resolved_key = next((c for c in candidate_cols if c in first_row), None)
    if not resolved_key:
        return [parent_entry]

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        group_val = (row.get(resolved_key) or "").strip()
        if not group_val:
            continue
        grouped.setdefault(group_val, []).append(row)

    if not grouped:
        return [parent_entry]

    fanned: list[LedgerEntry] = []
    for group_val, group_rows in grouped.items():
        merged_kf = dict(parent_entry.key_fields)
        merged_kf[resolved_key] = group_val
        # Promote single-valued columns across the group's rows into child key_fields
        all_cols = {col for r in group_rows for col in r}
        for col in sorted(all_cols):
            distinct_vals = {
                r[col].strip() for r in group_rows if col in r and r[col].strip()
            }
            if len(distinct_vals) == 1 and col not in merged_kf:
                merged_kf[col] = next(iter(distinct_vals))
        updated_tables = dict(parent_entry.tables)
        updated_tables[target_tbl] = list(group_rows)
        fanned.append(
            parent_entry.model_copy(
                update={
                    "doc_id": f"{parent_entry.doc_id}#{_slugify_key(group_val)}",
                    "key_fields": merged_kf,
                    "tables": updated_tables,
                }
            )
        )
    return fanned or [parent_entry]


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
