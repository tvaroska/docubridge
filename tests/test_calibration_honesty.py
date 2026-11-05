"""Calibration honesty guard: agreement coefficients come from data, never constants.

``krippendorff_alpha`` / ``gwets_ac2`` in ``bridge/`` must be fed a ratings matrix
that was observed (extraction runs, reviewer labels), not a literal list/tuple
or a module-level constant of lists. A constant matrix yields a constant alpha,
which reads as a measurement but is a number typed into the source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = REPO_ROOT / "bridge"
AGREEMENT_FUNCTIONS = frozenset({"krippendorff_alpha", "gwets_ac2"})


def _module_constant_matrices(tree: ast.Module) -> set[str]:
    """Names bound at module level to a literal list/tuple (possibly starred) of rows."""
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not isinstance(value, ast.List | ast.Tuple):
            continue
        if not any(
            isinstance(elt, ast.List | ast.Tuple | ast.Starred) for elt in value.elts
        ):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def literal_matrix_calls(source: str, filename: str = "<memory>") -> list[str]:
    """``file:line`` for every agreement call whose matrix argument is a constant."""
    tree = ast.parse(source, filename=filename)
    constants = _module_constant_matrices(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) not in (
            AGREEMENT_FUNCTIONS
        ):
            continue
        matrix: ast.expr | None = node.args[0] if node.args else None
        if matrix is None:
            for kw in node.keywords:
                if kw.arg == "ratings_matrix":
                    matrix = kw.value
        if matrix is None:
            continue
        is_literal = isinstance(matrix, ast.List | ast.Tuple)
        is_constant_name = isinstance(matrix, ast.Name) and matrix.id in constants
        if is_literal or is_constant_name:
            offenders.append(f"{filename}:{node.lineno}")
    return offenders


def test_bridge_never_feeds_a_constant_matrix_to_agreement_coefficients() -> None:
    """No module under bridge/ passes a literal or constant matrix to alpha / AC2."""
    offenders: list[str] = []
    for py_path in sorted(BRIDGE_ROOT.rglob("*.py")):
        source = py_path.read_text(encoding="utf-8")
        offenders.extend(
            literal_matrix_calls(source, str(py_path.relative_to(REPO_ROOT)))
        )
    assert offenders == [], (
        "agreement coefficients computed from constants (report 'not evaluated' "
        f"instead): {offenders}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "from bridge.calibration import krippendorff_alpha\n"
        "a = krippendorff_alpha([(1.0, 1.0), (0.0, 0.0)], metric='nominal')\n",
        "from bridge.calibration import gwets_ac2\n"
        "M: tuple[tuple[float, ...], ...] = (*([(1.0, 1.0)] * 3), (0.0, 1.0))\n"
        "a = gwets_ac2(M, scale_values=(0.0, 1.0))\n",
        "import bridge.calibration as cal\n"
        "ROWS = [[1, 1], [0, 0]]\n"
        "a = cal.krippendorff_alpha(ratings_matrix=ROWS)\n",
    ],
)
def test_constant_matrix_detector_positive_control(snippet: str) -> None:
    assert literal_matrix_calls(snippet) != []


def test_constant_matrix_detector_accepts_observed_matrices() -> None:
    snippet = (
        "from bridge.calibration import krippendorff_alpha\n"
        "def score(rows):\n"
        "    matrix = [[r.a, r.b] for r in rows]\n"
        "    return krippendorff_alpha(matrix, metric='nominal')\n"
    )
    assert literal_matrix_calls(snippet) == []


def test_refinery_report_defaults_are_not_evaluated() -> None:
    """The wire model never fails open: no alpha, not passed, until the assay says so."""
    from contract.refinery import RefineryAssayReport

    report = RefineryAssayReport(
        report_id="r",
        proposal_id="p",
        candidate_revision_id="c",
        baseline_revision_id="b",
    )
    assert report.perturbation_evaluated is False
    assert report.perturbation_krippendorff_alpha is None
    assert report.workhorse_teacher_ac2 is None
    assert report.passed is False
