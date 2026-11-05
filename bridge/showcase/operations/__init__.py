"""Beat operation registry: manifests reference operations by name only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bridge.showcase.context import OperationContext

OperationFn = Callable[[OperationContext, dict[str, Any]], dict[str, Any]]
ViewFn = Callable[[OperationContext], dict[str, Any]]

OPERATIONS: dict[str, OperationFn] = {}
VIEW_BUILDERS: dict[str, ViewFn] = {}


def operation(name: str) -> Callable[[OperationFn], OperationFn]:
    def _register(fn: OperationFn) -> OperationFn:
        if name in OPERATIONS:
            raise ValueError(f"duplicate scenario operation {name!r}")
        OPERATIONS[name] = fn
        return fn

    return _register


def view_builder(kind: str) -> Callable[[ViewFn], ViewFn]:
    def _register(fn: ViewFn) -> ViewFn:
        VIEW_BUILDERS[kind] = fn
        return fn

    return _register


def resolve_operation(name: str) -> OperationFn:
    try:
        return OPERATIONS[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown scenario operation {name!r}; registered: {sorted(OPERATIONS)}"
        ) from exc


def registered_operations() -> tuple[str, ...]:
    return tuple(sorted(OPERATIONS))


# Importing the modules registers their operations / view builders.
from bridge.showcase.operations import (  # noqa: E402
    consolidation,
    schema_evolution,
)

__all__ = [
    "OPERATIONS",
    "VIEW_BUILDERS",
    "OperationFn",
    "ViewFn",
    "operation",
    "registered_operations",
    "resolve_operation",
    "view_builder",
]
