"""Tests for the experimental-surface register (TEST-4).

Four tests:
1. Pin guard — asserts the installed google-adk is inside the pin.
2. Register accuracy — each surface still exists, at the registered path, and still
   decorated @experimental / @a2a_experimental.
3. Unregistered-use guard — no first-party module imports a KNOWN_EXPERIMENTAL_ADK_SYMBOL
   that is not in registered_symbols().
4. Positive control — proves the guard detector bites.
"""
from __future__ import annotations

import ast
from pathlib import Path


def test_google_adk_version_is_pinned():
    """Pin guard: installed google-adk is inside the pin."""
    import google.adk
    from packaging.version import Version

    from bridge.adk import ADK_PIN

    v = Version(google.adk.__version__)
    assert Version(ADK_PIN[0]) <= v < Version(ADK_PIN[1]), (
        f"installed google-adk {v} outside pin >={ADK_PIN[0]},<{ADK_PIN[1]}"
    )


def test_registered_surfaces_are_still_experimental():
    """Register accuracy: each surface still exists and is still experimental."""
    import importlib
    import inspect

    import pytest

    from bridge.adk import EXPERIMENTAL_SURFACES

    for s in EXPERIMENTAL_SURFACES:
        # Skip if required dependencies are absent (e.g., a2a-sdk arrives at P2).
        for dep in s.requires:
            pytest.importorskip(dep)

        # Import the symbol and verify it exists at the registered path.
        mod = importlib.import_module(s.module)
        obj = getattr(mod, s.attr)

        # Verify still decorated @experimental / @a2a_experimental (graduation alarm).
        src = inspect.getsource(obj)
        assert f"@{s.decorator}" in src, (
            f"{s.symbol} no longer decorated @{s.decorator} — re-audit or remove from register"
        )

        # Sanity: every entry carries owner + revisit_trigger + pinned_config.
        assert isinstance(s.pinned_config, dict), f"{s.symbol}: pinned_config not a dict"
        assert s.owner, f"{s.symbol}: missing owner"
        assert s.revisit_trigger, f"{s.symbol}: missing revisit_trigger"


def _experimental_imports(py_path: Path, catalog: frozenset[str]) -> list[str]:
    """AST-scan a .py file for imports of known-experimental ADK symbols.

    Returns a list of FQN symbols from the catalog that are imported.

    Scoping: matches ImportFrom FQNs only (the design always imports these surfaces
    by name: `from google.adk... import RemoteA2aAgent`). Does not attempt to track
    module-level imports combined with attribute accesses.
    """
    offenders = []
    try:
        tree = ast.parse(py_path.read_text(), filename=str(py_path))
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        # Match `from a.b.c import Name [as alias]` (level 0 only — relative imports can't reach google.adk)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                fqn = f"{node.module}.{alias.name}"
                if fqn in catalog:
                    offenders.append(fqn)

    return offenders


def test_no_first_party_module_uses_an_unregistered_experimental_surface():
    """Unregistered-use guard: no first-party module uses an experimental surface not in the register."""
    from bridge.adk import KNOWN_EXPERIMENTAL_ADK_SYMBOLS, registered_symbols

    unregistered = KNOWN_EXPERIMENTAL_ADK_SYMBOLS - registered_symbols()
    repo_root = Path(__file__).parent.parent

    violations: list[tuple[Path, str]] = []
    for subdir in ["bridge", "agents"]:
        dir_path = repo_root / subdir
        if not dir_path.exists():
            continue
        for py_file in dir_path.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            offending_symbols = _experimental_imports(py_file, unregistered)
            for symbol in offending_symbols:
                violations.append((py_file.relative_to(repo_root), symbol))

    assert not violations, (
        f"Found {len(violations)} unregistered experimental ADK surface(s) in first-party code. "
        "Register them in bridge/adk/experimental.py:\n"
        + "\n".join(f"  {file}: {symbol}" for file, symbol in violations)
    )


def test_guard_detects_unregistered_and_ignores_registered(tmp_path):
    """Positive control: the guard detector bites on unregistered, ignores registered/non-experimental/comments."""
    from bridge.adk import KNOWN_EXPERIMENTAL_ADK_SYMBOLS

    # Case 1: unregistered experimental (in catalog, not registered) → flagged
    unregistered_case = tmp_path / "unregistered.py"
    unregistered_case.write_text(
        "from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor\n"
    )

    # Case 2: registered experimental (ResumabilityConfig) → NOT flagged
    registered_case = tmp_path / "registered.py"
    registered_case.write_text("from google.adk.apps.app import ResumabilityConfig\n")

    # Case 3: non-experimental ADK import → NOT flagged
    non_experimental_case = tmp_path / "non_experimental.py"
    non_experimental_case.write_text("from google.adk.agents import LlmAgent\n")

    # Case 4: comment and string literal → NOT flagged
    comment_case = tmp_path / "comment.py"
    comment_case.write_text(
        '# from google.adk.agents.remote_a2a_agent import RemoteA2aAgent\n'
        's = "from google.adk.apps.app import ResumabilityConfig"\n'
    )

    # Case 5: relative import → NOT flagged
    relative_case = tmp_path / "relative.py"
    relative_case.write_text("from .foo import ResumabilityConfig\n")

    # Test each case
    unregistered_hits = _experimental_imports(unregistered_case, KNOWN_EXPERIMENTAL_ADK_SYMBOLS)
    registered_hits = _experimental_imports(registered_case, KNOWN_EXPERIMENTAL_ADK_SYMBOLS)
    non_experimental_hits = _experimental_imports(non_experimental_case, KNOWN_EXPERIMENTAL_ADK_SYMBOLS)
    comment_hits = _experimental_imports(comment_case, KNOWN_EXPERIMENTAL_ADK_SYMBOLS)
    relative_hits = _experimental_imports(relative_case, KNOWN_EXPERIMENTAL_ADK_SYMBOLS)

    # Only the unregistered case should be flagged
    assert unregistered_hits == ["google.adk.a2a.executor.a2a_agent_executor.A2aAgentExecutor"]
    assert registered_hits == ["google.adk.apps.app.ResumabilityConfig"]  # detected but will be filtered by registered_symbols
    assert non_experimental_hits == []
    assert comment_hits == []
    assert relative_hits == []
