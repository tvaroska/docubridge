"""Isolation guard: enforces the two-way package isolation invariant.

Two directions are load-bearing:

* ``bridge/`` must never import ``agents/`` — the reusable Bridge CORE stays
  consumer-agnostic (`CRITICAL.md`: bridge/ "never imports agents/").
* ``agents/`` must never import ``bridge/`` — the mock→real Bridge swap property
  depends on consumers talking to the Bridge only over the A2A wire + ``contract/``,
  never by reaching into the core (`bridge/ports/local/__init__.py` §1.4).

``contract/`` is the ONLY package both may import.
"""

import ast
from pathlib import Path


def _imports_package(py_path: Path, package: str) -> list[str]:
    """Parse a Python file and return absolute imports of ``package``.

    Returns the offending module names (e.g. ``["agents", "agents.foo"]`` when
    ``package == "agents"``). Relative imports (``level > 0``) are never flagged;
    only ``package`` itself and ``package.*`` count (so ``agentspew`` is ignored).
    """
    violations = []

    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
    except (SyntaxError, UnicodeDecodeError):
        # If the file can't be parsed, skip it (e.g., not valid Python)
        return []

    prefix = package + "."

    for node in ast.walk(tree):
        # Check direct imports: import <package>, import <package>.foo
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(prefix):
                    violations.append(alias.name)

        # Check from imports: from <package> import ..., from <package>.foo import ...
        # Only check absolute imports (level == 0)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and (node.module == package or node.module.startswith(prefix))
        ):
            violations.append(node.module)

    return violations


def _scan(source_pkg: str, forbidden_pkg: str) -> list[tuple[str, str]]:
    """Return (relative-path, imported-name) offenders where source_pkg imports forbidden_pkg."""
    repo_root = Path(__file__).resolve().parents[1]
    source_dir = repo_root / source_pkg

    offenders = []
    if source_dir.exists():
        for py_file in source_dir.rglob("*.py"):
            # Skip __pycache__ directories
            if "__pycache__" in py_file.parts:
                continue

            for imported_name in _imports_package(py_file, forbidden_pkg):
                offenders.append((str(py_file.relative_to(repo_root)), imported_name))

    return offenders


def test_bridge_never_imports_agents():
    """The load-bearing invariant: bridge/ must never import agents/."""
    offenders = _scan("bridge", "agents")

    assert not offenders, (
        f"bridge/ must never import agents/ (the load-bearing invariant). "
        f"Found {len(offenders)} violation(s):\n"
        + "\n".join(f"  {file}: imports {name}" for file, name in offenders)
    )


def test_agents_never_imports_bridge():
    """The reverse invariant: agents/ must never import bridge/ (mock→real swap property).

    Consumers reach the Bridge only over the A2A wire + shared ``contract/`` types —
    never by importing the core. This keeps the mock Bridge and the real Bridge
    interchangeable behind the same wire.
    """
    offenders = _scan("agents", "bridge")

    assert not offenders, (
        f"agents/ must never import bridge/ (the mock→real swap invariant); "
        f"consumers talk to the Bridge over the wire + contract/ only. "
        f"Found {len(offenders)} violation(s):\n"
        + "\n".join(f"  {file}: imports {name}" for file, name in offenders)
    )


def test_guard_detects_a_synthetic_violation(tmp_path):
    """Positive control: verify the detector catches real violations and ignores false positives."""

    # Test case 1: Direct import of agents (should be caught)
    violation1 = tmp_path / "test1.py"
    violation1.write_text("import agents\n")
    assert _imports_package(violation1, "agents") == ["agents"], (
        "Should detect 'import agents'"
    )

    # Test case 2: From import (should be caught)
    violation2 = tmp_path / "test2.py"
    violation2.write_text("from agents.foo import bar\n")
    assert _imports_package(violation2, "agents") == ["agents.foo"], (
        "Should detect 'from agents.foo import bar'"
    )

    # Test case 3: Import agents submodule (should be caught)
    violation3 = tmp_path / "test3.py"
    violation3.write_text("import agents.address\n")
    assert _imports_package(violation3, "agents") == ["agents.address"], (
        "Should detect 'import agents.address'"
    )

    # Test case 4: Comment mentioning agents (should NOT be caught)
    false_positive1 = tmp_path / "test4.py"
    false_positive1.write_text("# import agents\nimport sys\n")
    assert _imports_package(false_positive1, "agents") == [], (
        "Should ignore agents in comments"
    )

    # Test case 5: String mentioning agents (should NOT be caught)
    false_positive2 = tmp_path / "test5.py"
    false_positive2.write_text('x = "import agents"\n')
    assert _imports_package(false_positive2, "agents") == [], (
        "Should ignore agents in strings"
    )

    # Test case 6: Module name starting with 'agents' but not exactly 'agents' (should NOT be caught)
    false_positive3 = tmp_path / "test6.py"
    false_positive3.write_text("import agentspew\n")
    assert _imports_package(false_positive3, "agents") == [], (
        "Should not flag 'agentspew' (not 'agents' or 'agents.*')"
    )

    # Test case 7: Multiple violations in one file
    violation4 = tmp_path / "test7.py"
    violation4.write_text(
        "import agents\nfrom agents.foo import bar\nimport agents.baz\n"
    )
    violations = _imports_package(violation4, "agents")
    assert len(violations) == 3, f"Should detect 3 violations, got {violations}"
    assert "agents" in violations
    assert "agents.foo" in violations
    assert "agents.baz" in violations

    # Test case 8: Relative import (should NOT be caught - only absolute imports are violations)
    # This would be a relative import from within bridge/, which is allowed
    false_positive4 = tmp_path / "test8.py"
    false_positive4.write_text("from .agents import something\n")
    assert _imports_package(false_positive4, "agents") == [], (
        "Should ignore relative imports (level > 0)"
    )


def test_guard_detects_reverse_violation(tmp_path):
    """Positive control for the reverse direction: the detector catches agents→bridge imports."""

    # Direct + from imports of bridge (should be caught)
    violation1 = tmp_path / "rev1.py"
    violation1.write_text(
        "import bridge\nfrom bridge.edge import server\nimport bridge.agent\n"
    )
    violations = _imports_package(violation1, "bridge")
    assert len(violations) == 3, f"Should detect 3 bridge violations, got {violations}"
    assert "bridge" in violations
    assert "bridge.edge" in violations
    assert "bridge.agent" in violations

    # contract/ imports are always allowed (the shared package) — never flagged as bridge
    allowed = tmp_path / "rev2.py"
    allowed.write_text("from contract.wire import CollectRequest\nimport contract\n")
    assert _imports_package(allowed, "bridge") == [], (
        "Should not flag contract/ imports"
    )

    # Near-miss package name (should NOT be caught)
    near_miss = tmp_path / "rev3.py"
    near_miss.write_text("import bridgehead\n")
    assert _imports_package(near_miss, "bridge") == [], (
        "Should not flag 'bridgehead' (not 'bridge' or 'bridge.*')"
    )


# --------------------------------------------------------------------------- #
# Core vs. demo hosts (S0-arch-15)                                             #
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
DEMOS_DIR = REPO_ROOT / "bridge" / "demos"

# Files outside ``bridge/demos/`` allowed to carry a string constant that EQUALS an
# installed skill name — each a named exception with its reason (design/architecture.md
# "Core vs. demo hosts"). The test also fails if an entry stops naming a skill, so the
# list cannot rot into a blanket waiver.
SKILL_NAME_ALLOWLIST = {
    # The mock Bridge test double, scripted from the Address corpus (P2-5 mock→real swap).
    "bridge/edge/mock.py": "mock Bridge scripted from the Address eval corpus",
    # The corpus calibration tool maps each corpus file to its label space (CORPORA).
    "bridge/calibration.py": "calibration tool: corpus file → label space",
}


def _core_files() -> list[Path]:
    """Every ``bridge/**/*.py`` outside the fenced demo-host package."""
    return [
        py
        for py in (REPO_ROOT / "bridge").rglob("*.py")
        if "__pycache__" not in py.parts and DEMOS_DIR not in py.parents
    ]


def _imports_demos(py_path: Path) -> list[str]:
    """``bridge.demos`` imports in a file — incl. ``from bridge import demos``."""
    hits = _imports_package(py_path, "bridge.demos")
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except (SyntaxError, UnicodeDecodeError):
        return hits
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "bridge"
            and any(alias.name == "demos" for alias in node.names)
        ):
            hits.append("bridge.demos")
    return hits


def _installed_skill_names() -> set[str]:
    return {p.parent.name for p in (REPO_ROOT / "skills").glob("*/SKILL.md")}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of the docstring Constant of each Module / ClassDef / (Async)FunctionDef."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _skill_name_literals(py_path: Path, names: set[str]) -> list[tuple[int, str]]:
    """(line, value) of every non-docstring string constant EQUAL to a skill name."""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    docstrings = _docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value in names
    ]


def test_core_never_imports_demo_hosts():
    """No module outside ``bridge/demos/`` imports the demo-host package."""
    assert DEMOS_DIR.is_dir()
    offenders = [
        (str(py.relative_to(REPO_ROOT)), name)
        for py in _core_files()
        for name in _imports_demos(py)
    ]
    assert not offenders, (
        "bridge/demos/ is demo-host glue — the core must never import it. Found:\n"
        + "\n".join(f"  {file}: imports {name}" for file, name in offenders)
    )


def test_demo_import_detector_positive_control(tmp_path):
    cases = {
        "import bridge.demos.rfp_demo\n": ["bridge.demos.rfp_demo"],
        "from bridge.demos.rfp_demo import build_rfp_demo_app\n": [
            "bridge.demos.rfp_demo"
        ],
        "from bridge import demos\n": ["bridge.demos"],
        "from bridge import dashboard\n": [],
        "import bridge.demosaic\n": [],
        "x = 'bridge.demos'\n": [],
    }
    for i, (src, expected) in enumerate(cases.items()):
        py = tmp_path / f"d{i}.py"
        py.write_text(src)
        assert _imports_demos(py) == expected, src


def test_core_names_no_skill():
    """No string constant in the core (docstrings excluded) equals an installed skill
    name, outside the commented allowlist — the core learns process / label space / item
    key from the process skill (``bridge.rfp.load_rfp_policy``)."""
    names = _installed_skill_names()
    assert {"benefits-rfp", "benefits-quote", "address-proof"} <= names
    hits = {
        str(py.relative_to(REPO_ROOT)): found
        for py in _core_files()
        if (found := _skill_name_literals(py, names))
    }
    offenders = {f: h for f, h in hits.items() if f not in SKILL_NAME_ALLOWLIST}
    assert not offenders, (
        "the core names a skill — read it from the process skill (RfpPolicy) or move the "
        "glue to bridge/demos/:\n"
        + "\n".join(
            f"  {file}:{line}: {value!r}"
            for file, found in offenders.items()
            for line, value in found
        )
    )
    stale = sorted(set(SKILL_NAME_ALLOWLIST) - set(hits))
    assert not stale, f"allowlisted files no longer name a skill — drop them: {stale}"


def test_skill_name_detector_positive_control(tmp_path):
    names = {"benefits-rfp"}
    flagged = tmp_path / "flagged.py"
    flagged.write_text('X = "benefits-rfp"\n')
    assert _skill_name_literals(flagged, names) == [(1, "benefits-rfp")]
    docstring_only = tmp_path / "doc.py"
    docstring_only.write_text(
        '"""Mentions benefits-rfp."""\n\n\ndef f():\n    """benefits-rfp"""\n'
        '    return "the benefits-rfp prose"\n'
    )
    assert _skill_name_literals(docstring_only, names) == []
    in_call = tmp_path / "call.py"
    in_call.write_text('def f():\n    """Doc."""\n    return load("benefits-rfp")\n')
    assert _skill_name_literals(in_call, names) == [(3, "benefits-rfp")]


# --------------------------------------------------------------------------- #
# Scenario literals (P9-1): bridge/ must not name a showcase scenario           #
# --------------------------------------------------------------------------- #
SCENARIOS_DIR = REPO_ROOT / "scenarios"

# Keys inside ``scenarios/*/scenario.yaml`` whose string values identify a scenario.
_SCENARIO_IDENTITY_KEYS = frozenset(
    {
        "entity_id",
        "client",
        "borrower",
        "program",
        "client_name",
        "borrower_name",
        "day2_entity_id",
        "day2_client",
        "day2_borrower",
    }
)

# Files outside ``bridge/showcase/`` that still carry a scenario literal — each a named
# exception with its reason. The test also fails if an entry stops naming a scenario,
# so the list cannot rot into a blanket waiver. These predate P9-1 and are tracked
# for removal in the roadmap's Phase 9 follow-ups.
SCENARIO_LITERAL_ALLOWLIST = {
    "bridge/refinery/assay.py": "historical replay corpus pins the seeded deal ids",
    "bridge/refinery/benefits_assay.py": "historical replay corpus pins the seeded deal id",
}


def _scenario_identity_strings() -> set[str]:
    """Every entity id / client / borrower / program string declared by a manifest."""
    import yaml

    found: set[str] = set()

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
            if "related_entity_ids" in node and isinstance(
                node["related_entity_ids"], dict
            ):
                found.update(str(v) for v in node["related_entity_ids"].values())
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and key in _SCENARIO_IDENTITY_KEYS and node.strip():
            if not node.startswith("${"):
                found.add(node)

    for manifest in SCENARIOS_DIR.glob("*/scenario.yaml"):
        walk(yaml.safe_load(manifest.read_text(encoding="utf-8")))
    return found


def _scenario_literals(py_path: Path, needles: set[str]) -> list[tuple[int, str]]:
    """(line, value) of every non-docstring string constant CONTAINING a scenario string."""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    docstrings = _docstring_nodes(tree)
    lowered = {n.lower() for n in needles}
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and any(n in node.value.lower() for n in lowered)
    ]


def test_showcase_names_no_scenario_literals():
    """No string constant anywhere in ``bridge/`` (``bridge/showcase/`` included,
    docstrings excluded) names a scenario entity id, client, borrower or program —
    those live only in ``scenarios/<name>/scenario.yaml``."""
    needles = _scenario_identity_strings()
    assert len(needles) >= 8, needles
    hits = {
        str(py.relative_to(REPO_ROOT)): found
        for py in _core_files()
        if (found := _scenario_literals(py, needles))
    }
    offenders = {f: h for f, h in hits.items() if f not in SCENARIO_LITERAL_ALLOWLIST}
    assert not offenders, (
        "bridge/ names a showcase scenario — move the string into scenarios/<name>/"
        "scenario.yaml and read it from the manifest:\n"
        + "\n".join(
            f"  {file}:{line}: {value[:80]!r}"
            for file, found in offenders.items()
            for line, value in found
        )
    )
    stale = sorted(set(SCENARIO_LITERAL_ALLOWLIST) - set(hits))
    assert not stale, (
        f"allowlisted files no longer name a scenario — drop them: {stale}"
    )


def test_scenario_literal_detector_positive_control(tmp_path):
    needles = {"LOAN-EXAMPLE-001", "Example Client LLC"}
    flagged = tmp_path / "flagged.py"
    flagged.write_text('URI = "gs://bucket/vault/LOAN-EXAMPLE-001/doc"\n')
    assert _scenario_literals(flagged, needles) == [
        (1, "gs://bucket/vault/LOAN-EXAMPLE-001/doc")
    ]
    docstring_only = tmp_path / "doc.py"
    docstring_only.write_text('"""Seeds Example Client LLC."""\n\nX = 1\n')
    assert _scenario_literals(docstring_only, needles) == []
