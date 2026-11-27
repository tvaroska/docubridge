"""Sample document file extraction helpers for CLI use-case onboarding."""

from __future__ import annotations

import argparse
import html
import json
import re
import zlib
from collections.abc import Mapping
from pathlib import Path

import yaml

from bridge.refinery.onboarding import SampleDocumentExample

_XML_TAG_RE = re.compile(r"<[^>]+>")
_PDF_STREAM_RE = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.S)
_PDF_TEXT_TOKEN_RE = re.compile(
    r"\(((?:\\.|[^\\()])+)\)\s*Tj|\[((?:\\.|[^\[\]])+)\]\s*TJ"
)


def extract_pdf_text_bytes(raw_bytes: bytes) -> str:
    """Extract readable ASCII/UTF-8 text strings from uncompressed or Flate PDF streams."""
    lines: list[str] = []
    for match in _PDF_STREAM_RE.finditer(raw_bytes):
        chunk = match.group(1)
        decompressed = chunk
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decompressed = zlib.decompress(chunk, wbits)
                break
            except zlib.error:
                continue
        text = decompressed.decode("latin-1", errors="ignore")
        for tok_match in _PDF_TEXT_TOKEN_RE.finditer(text):
            single, array = tok_match.group(1), tok_match.group(2)
            if single:
                cleaned = (
                    single.replace(r"\(", "(")
                    .replace(r"\)", ")")
                    .replace(r"\\", "\\")
                    .strip()
                )
                if cleaned:
                    lines.append(cleaned)
            elif array:
                parts = re.findall(r"\(((?:\\.|[^\\()])+)\)", array)
                joined = "".join(
                    p.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
                    for p in parts
                ).strip()
                if joined:
                    lines.append(joined)
    if lines:
        return "\n".join(lines)
    return raw_bytes.decode("utf-8", errors="ignore")


def extract_svg_or_xml_text(raw_text: str) -> str:
    """Extract readable lines from SVG ``<text>`` or HTML/XML elements."""
    replaced = re.sub(
        r"</(?:text|tspan|p|div|tr|li|h[1-6])>", "\n", raw_text, flags=re.I
    )
    stripped = _XML_TAG_RE.sub(" ", replaced)
    unescaped = html.unescape(stripped)
    out_lines: list[str] = []
    for line in unescaped.splitlines():
        norm = re.sub(r"\s+", " ", line).strip()
        if norm:
            out_lines.append(norm)
    return "\n".join(out_lines)


def load_sample_from_file(
    path: Path,
    *,
    sample_id: str | None = None,
) -> SampleDocumentExample:
    """Load a ``SampleDocumentExample`` from a local document or fixture file."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Sample document file not found: {path}")
    sid = sample_id or resolved.stem
    suffix = resolved.suffix.lower()

    if suffix in (".json", ".yaml", ".yml"):
        raw = resolved.read_text(encoding="utf-8")
        data = json.loads(raw) if suffix == ".json" else yaml.safe_load(raw)
        if isinstance(data, Mapping):
            if "extracted_fields" in data or "raw_text" in data:
                payload = dict(data)
                payload.setdefault("sample_id", sid)
                payload.setdefault("filename", resolved.name)
                return SampleDocumentExample.model_validate(payload)
            return SampleDocumentExample(
                sample_id=sid,
                filename=resolved.name,
                extracted_fields={
                    str(k): str(v) for k, v in data.items() if v is not None
                },
            )
        raise ValueError(f"Expected a JSON/YAML mapping in {path}")

    if suffix in (".svg", ".xml", ".html", ".htm"):
        text = extract_svg_or_xml_text(resolved.read_text(encoding="utf-8"))
        return SampleDocumentExample(
            sample_id=sid,
            filename=resolved.name,
            raw_text=text,
        )

    if suffix == ".pdf":
        text = extract_pdf_text_bytes(resolved.read_bytes())
        return SampleDocumentExample(
            sample_id=sid,
            filename=resolved.name,
            raw_text=text,
        )

    if suffix in (".docx", ".rtf", ".xlsx"):
        from bridge.extraction.office_prepass import (
            extract_docx_with_redlines,
            extract_rtf_text,
            extract_xlsx_resilient,
        )

        raw_bytes = resolved.read_bytes()
        if suffix == ".docx":
            office_text = extract_docx_with_redlines(raw_bytes).accepted_text
        elif suffix == ".rtf":
            office_text = extract_rtf_text(raw_bytes)
        else:
            office_text = extract_xlsx_resilient(raw_bytes).rendered_text
        return SampleDocumentExample(
            sample_id=sid,
            filename=resolved.name,
            raw_text=office_text,
        )

    raw_text = resolved.read_text(encoding="utf-8", errors="replace")
    return SampleDocumentExample(
        sample_id=sid,
        filename=resolved.name,
        raw_text=raw_text,
    )


def build_onboarding_cli_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser for ``create-schema`` and ``onboard`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="python -m bridge.refinery.use_case_onboard",
        description="Create OKF/Skill schemas from sample documents or onboard a use-case agent.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_schema = sub.add_parser(
        "create-schema",
        help="Induce an OKF .okf.md bundle and doctype skill folder from 1-5 sample documents.",
    )
    p_schema.add_argument("--doctype", required=True, help="Doctype entity slug")
    p_schema.add_argument(
        "--doc",
        action="append",
        default=[],
        required=True,
        help="Path to a sample document (.pdf, .svg, .docx, .xlsx, .json, .yaml, .txt); repeatable (1-5)",
    )
    p_schema.add_argument(
        "--paired-target",
        default=None,
        help="Optional JSON/YAML file of paired target rows",
    )
    p_schema.add_argument(
        "--spine", default=None, help="Optional shared spine entity ID"
    )
    p_schema.add_argument(
        "--context-set",
        default="enterprise-document-intake",
        help="Ontology context set",
    )
    p_schema.add_argument("--domain", default="general", help="Domain tag")
    p_schema.add_argument(
        "--priority", type=int, default=20, help="Source priority tier (default 20)"
    )
    p_schema.add_argument(
        "--write-dir",
        type=Path,
        default=None,
        help="Write catalog/doctypes and skills/<doctype> under this directory",
    )
    p_schema.add_argument(
        "--install",
        action="store_true",
        help="Install into the active SkillRegistry and KnowledgeCatalog",
    )
    p_schema.add_argument(
        "--live-llm",
        action="store_true",
        help="Use live gemini-4-argon for contrastive synthesis",
    )

    p_onboard = sub.add_parser(
        "onboard",
        help="Onboard a use-case agent manifest (sample documents + process skill) locally or to a remote Bridge URL.",
    )
    p_onboard.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to use-case onboarding manifest (.yaml or .json)",
    )
    p_onboard.add_argument(
        "--bridge-url",
        default=None,
        help="Remote DocuBridge dashboard/API URL (e.g. https://<service>.run.app); omit for local",
    )
    p_onboard.add_argument(
        "--write-dir",
        type=Path,
        default=None,
        help="Optional output directory to write induced OKF & skill files",
    )
    p_onboard.add_argument(
        "--live-llm",
        action="store_true",
        help="Use live gemini-4-argon for contrastive synthesis",
    )
    return parser
