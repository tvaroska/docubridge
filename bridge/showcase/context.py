"""Execution context handed to every scenario beat operation.

Operations are pure functions of `(ctx, params)`. All runtime values an operation
produces (vault URIs, rule ids, compiled skill versions) land in `ctx.docs` /
`ctx.vars` so later operations and the manifest's display strings can reference
them with `${...}` placeholders instead of Python code.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, overload

from bridge.a2ui_inbox import ActionInboxStore
from bridge.agent_gym import AgentGymEngine
from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.knowledge_catalog import KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.refinery import SchemaRefineryEngine
from contract.scenario import ScenarioManifest

_PLACEHOLDER_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.:-]*)\}")


class PlaceholderError(KeyError):
    """A `${...}` reference in a manifest did not resolve."""


@dataclass
class OperationContext:
    """Mutable per-scenario runtime handed to beat operations."""

    manifest: ScenarioManifest
    state: dict[str, Any]
    store: OperationalStore
    catalog: KnowledgeCatalog
    inbox: ActionInboxStore
    gym: AgentGymEngine
    refinery: SchemaRefineryEngine | None
    pdf_bytes: Callable[[str], bytes]
    pdf_url: Callable[[str], str]
    docs: dict[str, dict[str, Any]] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)

    @property
    def entity_id(self) -> str:
        return self.manifest.entity_id

    def entity_for(self, alias: str | None) -> str:
        """Resolve an entity alias (`primary` or a key of `related_entity_ids`)."""
        if alias is None or alias in ("", "primary"):
            return self.manifest.entity_id
        try:
            return self.manifest.related_entity_ids[alias]
        except KeyError as exc:
            raise PlaceholderError(f"unknown entity alias {alias!r}") from exc

    def _lookup_colon(self, head: str, tail: str, full_name: str) -> object:
        if head == "entity":
            return self.entity_for(tail)
        if head == "pdf_url":
            return self.pdf_url(tail)
        if head == "doc":
            alias, _, fld = tail.partition(".")
            try:
                return self.docs[alias][fld]
            except KeyError as exc:
                raise PlaceholderError(full_name) from exc
        raise PlaceholderError(full_name)

    def _lookup_dot(self, head: str, tail: str, full_name: str) -> object:
        if head == "agent" and tail in self.manifest.agents:
            return self.manifest.agents[tail]
        if head == "principal" and tail in self.manifest.principals:
            return self.manifest.principals[tail]
        if head == "model":
            models = {"workhorse": WORKHORSE_MODEL, "reasoning": REASONING_MODEL}
            if tail in models:
                return models[tail]
        if head == "display":
            display_fields = {
                "client": self.manifest.display.client,
                "program": self.manifest.display.program,
                **self.manifest.display.labels,
            }
            if tail in display_fields:
                return display_fields[tail]
        raise PlaceholderError(full_name)

    def lookup(self, name: str) -> object:
        """Resolve one placeholder name.

        Namespaces: `entity_id`, `entity:<alias>`, `doc:<alias>.<field>`,
        `pdf_url:<asset_id>`, `agent.<key>`, `principal.<key>`, `model.workhorse`,
        `model.reasoning`, `display.<key>`, and any key previously stored in `vars`.
        """
        if name in self.vars:
            return self.vars[name]
        if name == "entity_id":
            return self.manifest.entity_id
        head, sep, tail = name.partition(":")
        if sep:
            return self._lookup_colon(head, tail, name)
        head, sep, tail = name.partition(".")
        if sep:
            return self._lookup_dot(head, tail, name)
        raise PlaceholderError(name)

    @overload
    def render(self, obj: dict[str, Any]) -> dict[str, Any]: ...

    @overload
    def render(self, obj: list[Any]) -> list[Any]: ...

    @overload
    def render(self, obj: str) -> str: ...

    @overload
    def render(self, obj: object) -> object: ...

    def render(self, obj: object) -> object:
        """Deep-copy `obj` with every `${...}` placeholder substituted.

        A string that is exactly one placeholder keeps the referenced value's type
        (so `"${inbox_item_id}"` stays whatever the store returned); embedded
        placeholders are string-formatted.
        """
        if isinstance(obj, str):
            whole = _PLACEHOLDER_RE.fullmatch(obj)
            if whole:
                return self.lookup(whole.group(1))
            return _PLACEHOLDER_RE.sub(lambda m: str(self.lookup(m.group(1))), obj)
        if isinstance(obj, dict):
            return {k: self.render(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.render(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self.render(v) for v in obj)
        return obj


@overload
def resolve_by_beat(obj: dict[str, Any], beat: int) -> dict[str, Any]: ...


@overload
def resolve_by_beat(obj: list[Any], beat: int) -> list[Any]: ...


@overload
def resolve_by_beat(obj: object, beat: int) -> object: ...


def resolve_by_beat(obj: object, beat: int) -> object:
    """Materialise beat-conditional view data.

    * a dict `{"by_beat": {n: value, ...}}` becomes the value of the largest `n <= beat`
      (`None` when no key applies);
    * list items carrying `from_beat` / `until_beat` are kept only when the beat falls
      inside the window, and those two keys are stripped from the kept item.
    """
    if isinstance(obj, dict):
        if set(obj) == {"by_beat"}:
            table = {int(k): v for k, v in obj["by_beat"].items()}
            applicable = [k for k in table if k <= beat]
            return resolve_by_beat(table[max(applicable)], beat) if applicable else None
        return {k: resolve_by_beat(v, beat) for k, v in obj.items()}
    if isinstance(obj, list):
        kept: list[Any] = []
        for item in obj:
            if isinstance(item, dict) and ("from_beat" in item or "until_beat" in item):
                lo = int(item.get("from_beat", 0))
                hi = int(item.get("until_beat", 10**6))
                if not lo <= beat <= hi:
                    continue
                item = {
                    k: v
                    for k, v in item.items()
                    if k not in ("from_beat", "until_beat")
                }
            kept.append(resolve_by_beat(item, beat))
        return kept
    return obj


__all__ = ["OperationContext", "PlaceholderError", "resolve_by_beat"]
