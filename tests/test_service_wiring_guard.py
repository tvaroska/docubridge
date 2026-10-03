"""Service-wiring guard: no production-zone factory hardcodes a runtime store.

The invariant (see ``design/patterns.md`` — "Host-controlled wiring"): a ``bridge/``
factory must NEVER construct an ``InMemory*`` session / task / artifact / push-config
store **unconditionally**. The host owns durability wiring (from CLI URIs / seam
adapters / env), so an in-memory default may appear only behind a config gate:

* an ``or`` operand — ``param or InMemory…()`` (the injectable idiom), or
* a config-gated backend swap — an ``if``/``elif``/``else`` branch body, or the
  fall-through of an early-return guard clause (``if bucket: return GCS`` then
  ``return InMemory…()``).

The anti-pattern this fences against is DECISIONS.md 2026-09-07 — "Remove test-only
``build_runner`` from bridge core": ``bridge/agent.py::build_runner`` constructed
``InMemorySessionService()`` unconditionally (no param, no gate, zero production
callers). An unconditional in-memory store in a core factory silently puts a
per-replica store on the deployed path — the session-affinity trap
(``design/seams.md``): a peer reference measured an 86.8% poll-404 rate on a
multi-replica substrate. This guard turns that regression into a red test.

Scope: ``bridge/`` only (the production zone). ``agents/`` (drivers, carriers,
console) and ``tests/`` legitimately construct ``InMemory*`` unconditionally and are
out of scope per the task wording ("no factory in ``bridge/``").
"""

import ast
import re
from pathlib import Path

INMEMORY_RE = re.compile(r"^InMemory\w+$")


def _link_parents(tree: ast.AST) -> None:
    """Annotate every node with a ``.parent`` link (``ast`` provides none)."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


def _callee_name(call: ast.Call) -> str:
    """Return the call's simple callee name (``Name.id`` or ``Attribute.attr``)."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _in_if_branch(child: ast.AST, if_node: ast.If) -> bool:
    """True when ``child`` sits in the body/orelse of ``if_node`` (not its test).

    A construction inside an ``if``/``elif``/``else`` branch body is config-gated;
    one sitting only in the ``if`` *condition* is not (and is not a real pattern).
    """
    branch_nodes: set[ast.AST] = set()
    for stmt in (*if_node.body, *if_node.orelse):
        branch_nodes.update(ast.walk(stmt))
    return child in branch_nodes


def _is_guard_clause(stmt: ast.AST) -> bool:
    """True for an early-return guard: ``if <cond>: … return|raise`` with no else.

    Reaching a statement *after* such a guard implies the guard's condition was
    false, so a store constructed there is conditional (the ``build_artifact_store``
    ``if bucket: return GCS`` / fall-through ``return InMemoryArtifactService()`` form).
    """
    return (
        isinstance(stmt, ast.If)
        and not stmt.orelse
        and bool(stmt.body)
        and isinstance(
            stmt.body[-1],
            (ast.Return, ast.Raise, ast.Continue, ast.Break),
        )
    )


def _preceded_by_guard_clause(child: ast.AST, parent: ast.AST) -> bool:
    """True when an earlier sibling of ``child`` (in a block of ``parent``) exits."""
    for block_name in ("body", "orelse", "finalbody"):
        block = getattr(parent, block_name, None)
        if not isinstance(block, list) or child not in block:
            continue
        for stmt in block:
            if stmt is child:
                break
            if _is_guard_clause(stmt):
                return True
    return False


def _is_gated(call: ast.Call) -> bool:
    """True when this ``InMemory*()`` construction is behind an allowed config gate."""
    node: ast.AST = call
    while (parent := getattr(node, "parent", None)) is not None:
        # Form 1: operand of a boolean `or` — `param or InMemory…()`.
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or):
            return True
        # Form 2: inside an if/elif/else branch body (config-gated backend swap).
        if isinstance(parent, ast.If) and _in_if_branch(node, parent):
            return True
        # Form 3: fall-through after an early-return guard clause.
        if _preceded_by_guard_clause(node, parent):
            return True
        node = parent
    return False


def _scan_source(source: str, filename: str = "<test>") -> list[tuple[str, int]]:
    """Return ungated ``InMemory*`` constructions in ``source`` as (name, lineno)."""
    tree = ast.parse(source, filename=filename)
    _link_parents(tree)

    violations: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if INMEMORY_RE.match(name) and not _is_gated(node):
            violations.append((name, node.lineno))
    return violations


def _scan_bridge() -> list[tuple[str, str, int]]:
    """Scan ``bridge/`` for ungated ``InMemory*`` constructions.

    Returns ``(relpath, callee_name, lineno)`` per offender. Files that fail to
    parse are skipped (matching the isolation guard).
    """
    repo_root = Path(__file__).resolve().parents[1]
    bridge_dir = repo_root / "bridge"

    offenders: list[tuple[str, str, int]] = []
    if not bridge_dir.exists():
        return offenders

    for py_file in bridge_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            violations = _scan_source(source, filename=str(py_file))
        except SyntaxError:
            continue
        rel = str(py_file.relative_to(repo_root))
        offenders.extend((rel, name, lineno) for name, lineno in violations)

    return offenders


def test_no_bridge_factory_hardcodes_inmemory_store():
    """No ``bridge/`` factory constructs an ``InMemory*`` store unconditionally.

    An in-memory default is allowed ONLY behind the injectable ``param or
    InMemory…()`` idiom or a DSN/env-gated backend swap (an ``if``/``else`` branch
    or an early-return guard clause). An unconditional construction puts a
    per-replica store on the deployed path (the session-affinity trap).
    """
    offenders = _scan_bridge()

    assert not offenders, (
        "bridge/ factories must not hardcode an in-memory runtime store. "
        "The host owns durability wiring; use the `param or InMemory…()` idiom or "
        "a DSN/env-gated `if`/`else` (or early-return guard) backend swap. "
        f"Found {len(offenders)} ungated construction(s):\n"
        + "\n".join(f"  {file}:{lineno}: {name}" for file, name, lineno in offenders)
    )


def test_guard_detects_a_synthetic_violation():
    """Positive control: the detector flags the anti-pattern and clears every gate form."""

    # (a) Unconditional construction in a function body — the `build_runner` shape.
    violations = _scan_source(
        "def f():\n    x = InMemorySessionService()\n    return x\n"
    )
    assert violations == [("InMemorySessionService", 2)], (
        f"unconditional construction should flag once, got {violations}"
    )

    # (b) `param or InMemory…()` — the injectable idiom (gated).
    assert _scan_source("def f(param):\n    return param or InMemoryTaskStore()\n") == [], (
        "the `param or InMemory…()` idiom must be allowed"
    )

    # (c) `if not dsn: return InMemory…()` — DSN-gated branch body (gated).
    assert _scan_source(
        "def f(dsn):\n    if not dsn:\n        return InMemoryTaskStore()\n    return db(dsn)\n"
    ) == [], "an if-branch body construction must be allowed"

    # (d) `else: return InMemory…()` — else-branch body (gated).
    assert _scan_source(
        "def f(b):\n    if b:\n        g()\n    else:\n        return InMemoryArtifactService()\n"
    ) == [], "an else-branch body construction must be allowed"

    # (e) A class *definition* named InMemory* is not a Call — never flagged.
    assert _scan_source("class InMemoryPortalStore:\n    pass\n") == [], (
        "a class definition is not a construction"
    )

    # (f) An import of an InMemory* symbol is not a Call — never flagged.
    assert _scan_source("from a2a.server.tasks import InMemoryTaskStore\n") == [], (
        "an import is not a construction"
    )

    # (g) Mentions in a comment / string never parse to a Call — never flagged.
    assert _scan_source('# InMemoryTaskStore()\nx = "InMemoryTaskStore()"\n') == [], (
        "comment/string mentions are not constructions"
    )

    # (h) Attribute-form unconditional construction — `mod.InMemoryTaskStore()`.
    violations = _scan_source("def f(mod):\n    x = mod.InMemoryTaskStore()\n    return x\n")
    assert violations == [("InMemoryTaskStore", 2)], (
        f"attribute-form unconditional construction should flag once, got {violations}"
    )

    # (i) Early-return guard clause then fall-through — the build_artifact_store shape.
    assert _scan_source(
        "def f(bucket):\n"
        "    if bucket:\n"
        "        return GcsArtifactService(bucket)\n"
        "    return InMemoryArtifactService()\n"
    ) == [], "a construction after an early-return guard clause must be allowed"
