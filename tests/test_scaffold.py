"""Baseline sanity test so `uv run pytest` is green before P0-1 lands the real tree.

Replaced/expanded by the P0 foundation tasks (isolation-guard, port harness).
"""


def test_python_runtime_is_312_plus():
    import sys

    assert sys.version_info >= (3, 12)
