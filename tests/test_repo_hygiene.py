"""Repository hygiene & spine integrity tests (`ADR-005`)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {
    "node_modules",
    "dist",
    "build",
    "out",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".venv",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".terraform",
}

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Hardcoded local home directory path",
        re.compile(r"/usr/local/google/" + r"home|\.c\.googlers\.com"),
    ),
    ("File URI scheme", re.compile(r"file:" + r"///")),
    ("Internal Buganizer ID", re.compile(r"\bb" + r"/\d{6,}\b")),
    (
        "Internal shortlink",
        re.compile(r"\bgo" + r"/(?!mod\b|sum\b|work\b|version\b)[a-zA-Z0-9_-]{3,}"),
    ),
    (
        "Internal monorepo path",
        re.compile(r"//depot/" + r"google3|\bgoogle" + r"3\b"),
    ),
    (
        "Cross-workspace local path",
        re.compile(r"~/" + r"customers|~/notes|~/ideas|~/robotics"),
    ),
]


def test_zero_github_workflows() -> None:
    gh_workflows = REPO_ROOT / (".git" + "hub") / "workflows"
    assert not gh_workflows.exists(), (
        "CI/CD must use cloudbuild.yaml only (zero .github/workflows/)"
    )


def test_required_docs_and_claude_md_symlink() -> None:
    agents_md = REPO_ROOT / "AGENTS.md"
    claude_md = REPO_ROOT / "CLAUDE.md"
    critical_md = REPO_ROOT / "CRITICAL.md"
    decisions_md = REPO_ROOT / "DECISIONS.md"

    for req in (agents_md, critical_md, decisions_md):
        assert req.is_file(), f"Missing required documentation file: {req.name}"

    assert agents_md.stat().st_size < 24_000, (
        "AGENTS.md must stay strictly below 24 KB truncation cap"
    )
    assert decisions_md.stat().st_size <= 25_000, "DECISIONS.md must stay <= 25 KB"
    assert claude_md.is_symlink(), "CLAUDE.md must be a symlink to AGENTS.md"
    assert claude_md.readlink().as_posix() == "AGENTS.md"
