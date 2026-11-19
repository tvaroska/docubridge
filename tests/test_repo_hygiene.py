"""Repository hygiene, spine integrity & declarative AST contract tests (`ADR-005`)."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

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

EXCLUDED_FILES = {
    "uv.lock",
    "pnpm-lock.yaml",
    "package-lock.json",
}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
    ".json",
    ".jsonl",
    ".sh",
    ".tf",
    ".tfvars",
    ".hcl",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".svg",
    ".txt",
    ".cfg",
    ".ini",
}

TEXT_NAMES = {
    "Dockerfile",
    "LICENSE",
    ".dockerignore",
    ".gcloudignore",
    ".gitignore",
    ".env.example",
    ".python-version",
}

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Hardcoded local home directory path",
        re.compile(r"/usr/local/google/" + r"home|\.c\.googlers\.com"),
    ),
    (
        "Local workstation File URI",
        re.compile(r"file:" + r"///(?:usr/local/google|Users/|home/|[A-Za-z]:/)"),
    ),
    ("Internal Buganizer ID", re.compile(r"\bb" + r"/\d{6,}\b")),
    (
        "Internal shortlink",
        re.compile(
            r"\bgo"
            + r"/(?!mod\b|sum\b|work\b|version\b|test\b|build\b|run\b)[a-zA-Z0-9_-]{3,}"
        ),
    ),
    (
        "Internal monorepo path",
        re.compile(r"//depot/" + r"google3|\bgoogle" + r"3\b"),
    ),
    (
        "Cross-workspace local path",
        re.compile(r"~/" + r"customers|~/notes|~/ideas|~/robotics|~/projects"),
    ),
    (
        "Hardcoded personal or scratchpad GCP project ID",
        re.compile(
            r"\b(?:"
            + r"btv-scratch-\d+|btv-data|documents00\d|sites-470716|btvaroska|"
            + r"podcaster01|docubridget-test|agents-469418|data-305316|"
            + r"core-380514|coding00[78]"
            + r")\b"
        ),
    ),
    ("Cookie-gated GCS URL", re.compile(r"https?://storage\.cloud\.google\.com/")),
    ("Machine kernel string", re.compile(r"\bLinux-\d+\.\d+\.\d+-")),
]

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_PROJECT_NUMBER_RE = re.compile(
    r"(?<![0-9a-fA-F])(?!100000000000\b|000000000000\b|111111111111\b)\d{12}(?![0-9a-fA-F])"
)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK_RE = re.compile(r"\[[^\]\n]+\]\(([^)\s]+)\)")
_ENV_DECL_RE = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]+)=", re.MULTILINE)
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


def _iter_tracked_text_files(root: Path = REPO_ROOT) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel_parts = path.relative_to(root).parts
        *dirs, name = rel_parts
        if any(
            (d.startswith(".") and d != ".stitch") or d in EXCLUDED_DIRS for d in dirs
        ):
            continue
        if name in EXCLUDED_FILES or rel_parts == ("tests", "test_repo_hygiene.py"):
            continue
        if name in TEXT_NAMES or (
            not name.startswith(".") and path.suffix.lower() in TEXT_SUFFIXES
        ):
            files.append(path)
    return files


def _scan_text_for_leaks(text: str) -> list[str]:
    hits: list[str] = []
    for label, pattern in FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append(f"{label} ({match.group(0)!r})")
    stripped = _UUID_RE.sub("", text)
    num_match = _PROJECT_NUMBER_RE.search(stripped)
    if num_match:
        hits.append(f"Hardcoded 12-digit GCP project number ({num_match.group(0)!r})")
    return hits


def _check_markdown_links(root: Path, md_files: list[Path]) -> list[str]:
    broken: list[str] = []
    repo_resolved = root.resolve()
    for md_file in md_files:
        text = _FENCED_CODE_RE.sub("", md_file.read_text(encoding="utf-8"))
        for match in _MD_LINK_RE.finditer(text):
            raw_target = match.group(1).split("#", 1)[0]
            if not raw_target or raw_target.startswith(
                ("http://", "https://", "mailto:")
            ):
                continue
            target = (md_file.parent / raw_target.replace("%20", " ")).resolve()
            rel = md_file.relative_to(root).as_posix()
            if not target.is_relative_to(repo_resolved):
                broken.append(f"{rel}: sibling escape ({raw_target})")
            elif not target.exists():
                broken.append(f"{rel}: missing target ({raw_target})")
    return broken


def _extract_env_vars_from_source(source: str, filename: str = "<mem>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    found: set[str] = set()
    for node in ast.walk(tree):
        key: str | None = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            fn = node.func
            is_os_getenv = (
                fn.attr == "getenv"
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "os"
            )
            is_environ_call = fn.attr in ("get", "setdefault", "pop") and (
                isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ"
            )
            if (
                (is_os_getenv or is_environ_call)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                key = node.args[0].value
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            key = node.slice.value
        if key and _ENV_NAME_RE.match(key):
            found.add(key)
    return found


def _validate_eval_yaml_doc(doc: Any, label: str) -> list[str]:
    """Fail-closed contract validator for declarative skill ``EVAL.yaml`` files."""
    errors: list[str] = []
    if not isinstance(doc, dict) or not doc:
        return [f"{label}: EVAL.yaml must be a non-empty YAML mapping"]
    items = doc.get("cases") or doc.get("rubric") or doc.get("checks")
    if not isinstance(items, list) or len(items) == 0:
        errors.append(
            f"{label}: EVAL.yaml must declare at least one non-empty cases/rubric/checks entry"
        )
    else:
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("id"):
                errors.append(f"{label}: entry[{idx}] missing required 'id'")
            severity = item.get("severity") if isinstance(item, dict) else None
            if severity is not None and severity not in {"MUST", "SHOULD"}:
                errors.append(
                    f"{label}: entry[{idx}] severity must be MUST or SHOULD, got {severity!r}"
                )
    if doc.get("execution") is not None and doc["execution"] not in {"local", "remote"}:
        errors.append(
            f"{label}: execution must be 'local' or 'remote', got {doc['execution']!r}"
        )
    return errors


def test_zero_github_workflows() -> None:
    gh_workflows = REPO_ROOT / (".git" + "hub") / "workflows"
    assert not gh_workflows.exists(), (
        "CI/CD must use cloudbuild.yaml only (zero .github/workflows/)"
    )


def test_zero_committed_pdfs() -> None:
    """Decision 18: no binary .pdf files may live in the repository tree (Cloud Storage only)."""
    found: list[str] = []
    for path in REPO_ROOT.rglob("*.pdf"):
        rel = path.relative_to(REPO_ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        found.append(rel.as_posix())
    assert found == [], (
        f"Committed PDFs must live in Cloud Storage only: {sorted(found)}"
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


def test_zero_internal_customer_project_id_or_machine_path_leaks() -> None:
    violations: list[str] = []
    for fpath in _iter_tracked_text_files():
        text = fpath.read_text(encoding="utf-8", errors="ignore")
        rel = fpath.relative_to(REPO_ROOT).as_posix()
        for hit in _scan_text_for_leaks(text):
            violations.append(f"{rel}: {hit}")
    assert not violations, "Repository hygiene violations found:\n" + "\n".join(
        violations
    )


def test_all_relative_markdown_links_resolve_within_repo() -> None:
    md_files = [
        p
        for p in sorted(REPO_ROOT.rglob("*.md"))
        if not p.is_symlink()
        and not any(
            (part.startswith(".") and part != ".stitch") or part in EXCLUDED_DIRS
            for part in p.relative_to(REPO_ROOT).parts
        )
    ]
    assert md_files, "Expected Markdown documentation files in repo"
    broken = _check_markdown_links(REPO_ROOT, md_files)
    assert not broken, "Broken or escaping markdown links found:\n" + "\n".join(broken)


def test_hygiene_scanners_catch_synthetic_leaks(tmp_path: Path) -> None:
    """Positive controls proving leak and link scanners catch real defects without false positives."""
    scratch_leak = _scan_text_for_leaks("gs://btv-scratch-" + "99-bucket/file.pdf")
    assert any("GCP project ID" in hit for hit in scratch_leak), scratch_leak

    proj_num_leak = _scan_text_for_leaks(
        "https://bridge-883686070419.us-central1.run.app"
    )
    assert any("12-digit GCP project number" in hit for hit in proj_num_leak), (
        proj_num_leak
    )

    clean_synthetic = _scan_text_for_leaks(
        "https://docubridge-showcase-bridge-100000000000.us-central1.run.app\n"
        "uuid: 550e8400-e29b-41d4-a716-446655440000\n"
        "file:///etc/passwd\n"
    )
    assert clean_synthetic == [], clean_synthetic

    sub_root = tmp_path / "repo"
    (sub_root / "docs").mkdir(parents=True)
    (sub_root / "docs" / "real.md").write_text("# Real\n", encoding="utf-8")
    good_md = sub_root / "README.md"
    good_md.write_text("See [real](docs/real.md).\n", encoding="utf-8")
    assert _check_markdown_links(sub_root, [good_md]) == []

    bad_md = sub_root / "BAD.md"
    bad_md.write_text(
        "Broken [missing](docs/nope.md) and [escape](../../outside.md).\n",
        encoding="utf-8",
    )
    bad_links = _check_markdown_links(sub_root, [bad_md])
    assert len(bad_links) == 2, bad_links


def test_env_example_covers_all_bridge_env_vars() -> None:
    """Every env var read via AST in ``bridge/**/*.py`` is documented in ``.env.example``."""
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(_ENV_DECL_RE.findall(env_example))
    assert documented, ".env.example must declare environment variables"

    used: dict[str, list[str]] = {}
    for py_path in sorted((REPO_ROOT / "bridge").rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        rel = py_path.relative_to(REPO_ROOT).as_posix()
        for var in _extract_env_vars_from_source(
            py_path.read_text(encoding="utf-8"), filename=rel
        ):
            used.setdefault(var, []).append(rel)

    missing = {
        var: files for var, files in sorted(used.items()) if var not in documented
    }
    assert not missing, (
        "Environment variables read in bridge/ missing from .env.example:\n"
        + "\n".join(f"  {var}: {', '.join(files)}" for var, files in missing.items())
    )

    synthetic = _extract_env_vars_from_source(
        'import os\nx = os.getenv("BRIDGE_SYNTHETIC_UNDECLARED")\ny = os.environ["BRIDGE_SYNTHETIC_TWO"]\n'
    )
    assert synthetic == {"BRIDGE_SYNTHETIC_UNDECLARED", "BRIDGE_SYNTHETIC_TWO"}


def test_doctype_skills_declarative_validation_and_eval_yaml() -> None:
    """Every doctype skill has valid ``schema.json`` + ``validation.yaml`` and fail-closed ``EVAL.yaml``."""
    skills_dir = REPO_ROOT / "skills"
    doctype_count = 0
    errors: list[str] = []

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), (
            f"{skill_dir.name}/SKILL.md missing frontmatter"
        )
        end = text.index("\n---\n", 4)
        fm = yaml.safe_load(text[4:end]) or {}
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        if meta.get("bridge-kind") == "doctype":
            doctype_count += 1
            schema_path = skill_dir / "assets" / "schema.json"
            val_path = skill_dir / "assets" / "validation.yaml"
            if not schema_path.is_file():
                errors.append(f"{skill_dir.name}: missing assets/schema.json")
            else:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                if schema.get("properties", {}).get("doctype", {}).get("enum") != [
                    skill_dir.name
                ]:
                    errors.append(
                        f"{skill_dir.name}: schema.json doctype enum mismatch"
                    )
            if not val_path.is_file():
                errors.append(f"{skill_dir.name}: missing assets/validation.yaml")
            else:
                val_doc = yaml.safe_load(val_path.read_text(encoding="utf-8"))
                rules = val_doc.get("rules") if isinstance(val_doc, dict) else None
                signals = val_doc.get("signals") if isinstance(val_doc, dict) else None
                if not isinstance(rules, list) or not rules:
                    errors.append(
                        f"{skill_dir.name}: assets/validation.yaml missing non-empty rules"
                    )
                if (
                    not isinstance(signals, dict)
                    or signals.get("completeness") != "agent"
                ):
                    errors.append(
                        f"{skill_dir.name}: assets/validation.yaml must set signals.completeness=agent"
                    )

        for candidate in (skill_dir / "EVAL.yaml", skill_dir / "assets" / "EVAL.yaml"):
            if candidate.is_file():
                doc = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                errors.extend(
                    _validate_eval_yaml_doc(
                        doc, candidate.relative_to(REPO_ROOT).as_posix()
                    )
                )

    assert doctype_count >= 8, f"Expected >= 8 doctype skills, found {doctype_count}"
    assert not errors, "Declarative skill validation errors:\n" + "\n".join(errors)

    assert _validate_eval_yaml_doc({}, "empty") != []
    assert _validate_eval_yaml_doc({"checks": []}, "zero-checks") != []
    assert (
        _validate_eval_yaml_doc(
            {"execution": "local", "checks": [{"id": "c1", "severity": "MUST"}]},
            "valid",
        )
        == []
    )


def _resolve_ast_grep() -> str | None:
    found = shutil.which("ast-grep")
    if found:
        return found
    local_bin = Path.home() / ".local" / "bin" / "ast-grep"
    return str(local_bin) if local_bin.is_file() else None


def test_declarative_ast_rules_and_positive_controls(tmp_path: Path) -> None:
    rules_path = REPO_ROOT / "specs" / "ast.rules.yaml"
    assert rules_path.is_file(), "Missing specs/ast.rules.yaml declarative AST contract"

    docs = [d for d in yaml.safe_load_all(rules_path.read_text(encoding="utf-8")) if d]
    assert len(docs) >= 6, "Expected >= 6 declarative AST rules in specs/ast.rules.yaml"

    for doc in docs:
        rule_id = doc.get("id")
        assert rule_id, "Every AST rule must declare an id"
        assert doc.get("severity") == "error", f"{rule_id} must set severity: error"
        meta = doc.get("metadata") or {}
        assert meta.get("test_path"), f"{rule_id} missing metadata.test_path"
        assert meta.get("invalid_examples"), (
            f"{rule_id} missing metadata.invalid_examples"
        )
        assert meta.get("valid_examples"), f"{rule_id} missing metadata.valid_examples"

    ast_grep = _resolve_ast_grep()
    if ast_grep is None:
        pytest.skip("ast-grep binary not installed on this runner")

    live_res = subprocess.run(
        [ast_grep, "scan", "--rule", str(rules_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert live_res.returncode == 0, (
        f"specs/ast.rules.yaml failed on repo:\n{live_res.stdout}\n{live_res.stderr}"
    )

    for idx, doc in enumerate(docs):
        rule_id = str(doc["id"])
        meta = doc["metadata"]
        case_dir = tmp_path / f"case_{idx}_{rule_id}"
        single_rule = case_dir / "_rule.yaml"
        target_file = case_dir / str(meta["test_path"])
        target_file.parent.mkdir(parents=True, exist_ok=True)
        single_rule.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

        for bad_code in meta["invalid_examples"]:
            target_file.write_text(f"{bad_code}\n", encoding="utf-8")
            bad_res = subprocess.run(
                [ast_grep, "scan", "--rule", str(single_rule), "."],
                cwd=case_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            assert bad_res.returncode == 1, (
                f"Positive control failed for {rule_id}: {bad_code!r} was not flagged"
            )
            assert rule_id in (bad_res.stdout + bad_res.stderr)

        for good_code in meta["valid_examples"]:
            target_file.write_text(f"{good_code}\n", encoding="utf-8")
            good_res = subprocess.run(
                [ast_grep, "scan", "--rule", str(single_rule), "."],
                cwd=case_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            assert good_res.returncode == 0, (
                f"False positive in {rule_id} on {good_code!r}:\n{good_res.stdout}"
            )
