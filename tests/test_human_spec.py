"""Executable verification and human-legibility gates for specs/human/."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from bridge.ports.port import Port
from specs.human import (
    BLUEPRINT,
    build_atlas_project,
    build_blueprint_dict,
    build_cockpit_entry,
    verify_human_contract,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HUMAN_DIR = REPO_ROOT / "specs" / "human"
MAX_LINE_LENGTH = 100
MAX_NON_BLANK_LINES = 650
BANNED_TERMINOLOGY_RE = re.compile(r"\bsea" + r"ms?\b", re.IGNORECASE)
NARRATION_COMMENT_RE = re.compile(
    r"^\s*#\s*(?:[=\-─#*]{8,}|\d+[.)]\s|Step \d)", re.MULTILINE
)


def _iter_human_sources() -> list[Path]:
    return sorted(path for path in HUMAN_DIR.glob("*.py") if path.is_file())


def _collect_single_char_identifiers(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if len(node.name) == 1:
                hits.append(f"line {node.lineno}: def/class {node.name!r}")
        elif isinstance(node, ast.arg) and len(node.arg) == 1:
            hits.append(f"line {node.lineno}: arg {node.arg!r}")
        elif isinstance(node, ast.Name) and len(node.id) == 1:
            hits.append(f"line {node.lineno}: name {node.id!r}")
        elif isinstance(node, ast.alias) and node.asname and len(node.asname) == 1:
            hits.append(f"line {node.lineno}: import alias {node.asname!r}")
    return hits


def _audit_human_file(src_path: Path) -> list[str]:
    """Audit one specs/human Python file against Thariq-style readability rules."""
    violations: list[str] = []
    rel = src_path.relative_to(REPO_ROOT).as_posix()
    text = src_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    non_blank = sum(1 for line in lines if line.strip())
    if non_blank > MAX_NON_BLANK_LINES:
        violations.append(
            f"{rel}: {non_blank} non-blank LOC exceeds {MAX_NON_BLANK_LINES}"
        )

    for idx, line in enumerate(lines, start=1):
        if len(line) > MAX_LINE_LENGTH:
            violations.append(
                f"{rel}:{idx}: line length {len(line)} > {MAX_LINE_LENGTH}"
            )

    if BANNED_TERMINOLOGY_RE.search(text):
        violations.append(
            f"{rel}: forbidden wording matched; use 'Ports, Adapters and Contracts'"
        )
    if NARRATION_COMMENT_RE.search(text):
        violations.append(f"{rel}: narration/banner comment prohibited")

    tree = ast.parse(text, filename=rel)
    if not ast.get_docstring(tree):
        violations.append(f"{rel}: missing module docstring")
    missing_docs = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
        and not ast.get_docstring(node)
    ]
    for node in missing_docs:
        violations.append(f"{rel}:{node.lineno}: public {node.name} missing docstring")

    for bad_ident in _collect_single_char_identifiers(tree):
        violations.append(f"{rel}: single-character identifier ({bad_ident})")
    return violations


def test_human_spec_strict_readability_rules() -> None:
    """Enforce Thariq-style human legibility: docstrings, bounds, no 1-char names."""
    files = _iter_human_sources()
    assert len(files) >= 3, "Expected modular specs/human/*.py package"
    violations = [issue for src_path in files for issue in _audit_human_file(src_path)]
    assert not violations, "Human-spec lint violations:\n" + "\n".join(violations)


def test_human_spec_contract_and_all_fourteen_ports_match_codebase() -> None:
    """Fail if any code file, AST symbol, Port, ADR, or roadmap ref drifts."""
    errors = verify_human_contract(REPO_ROOT)
    assert not errors, "Executable /human spec parity errors:\n" + "\n".join(errors)

    declared_ports = {port.port_id for port in BLUEPRINT.ports}
    enum_ports = {member.value for member in Port}
    assert declared_ports == enum_ports, (
        f"Port inventory drift: declared={sorted(declared_ports)} "
        f"vs enum={sorted(enum_ports)}"
    )


def test_human_spec_compiles_blueprint_atlas_and_cockpit_projections() -> None:
    """Compiled blueprint, Atlas L0-L3 payload, and Cockpit entry share one truth."""
    blueprint = build_blueprint_dict(REPO_ROOT)
    assert blueprint["meta"]["slug"] == "docubridge"
    assert len(blueprint["zones"]) == len(BLUEPRINT.zones)
    assert len(blueprint["nodes"]) == len(BLUEPRINT.nodes)
    assert len(blueprint["adrs"]) == len(BLUEPRINT.adrs)
    assert len(blueprint["journeys"]) == len(BLUEPRINT.journeys)
    assert "<svg" in blueprint["overview"]["heroSvg"]

    atlas = build_atlas_project()
    assert atlas["id"] == "docubridge"
    assert atlas["short_name"] == "DocuBridge"
    assert len(atlas["pillars"]) == 3

    cockpit = build_cockpit_entry()
    assert cockpit["slug"] == "docubridge"
    assert cockpit["path"] == "/docubridge/"
    assert cockpit["from_human_spec"] is True
    assert cockpit["ports_count"] == 14
