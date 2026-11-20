"""Live read-time loader for process-skill ``assets/strategy.yaml`` files."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from bridge.sla import parse_skill_frontmatter, skills_root
from bridge.strategy.models import CollectionStrategy, StrategyLoadError

DEFAULT_STRATEGY_ASSET = "assets/strategy.yaml"
_SKILL_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _compute_revision(strategy: CollectionStrategy) -> str:
    payload = strategy.model_dump(mode="json", exclude={"revision"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    return f"{strategy.version}@{digest}"


def load_strategy_from_yaml(
    raw_yaml: str,
    *,
    where: str = "<memory>",
    expected_process: str | None = None,
) -> CollectionStrategy:
    """Parse and strictly validate a declarative ``strategy.yaml`` payload."""
    try:
        doc = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise StrategyLoadError(f"{where}: invalid YAML ({exc})") from exc
    if not isinstance(doc, dict) or not doc:
        raise StrategyLoadError(
            f"{where}: strategy document must be a non-empty mapping"
        )
    try:
        parsed = CollectionStrategy.model_validate(doc)
    except ValidationError as exc:
        raise StrategyLoadError(f"{where}: invalid strategy schema ({exc})") from exc
    if expected_process is not None and parsed.process != expected_process:
        raise StrategyLoadError(
            f"{where}: strategy process {parsed.process!r} does not match skill {expected_process!r}"
        )
    return parsed.model_copy(update={"revision": _compute_revision(parsed)})


def _resolve_strategy_rel(frontmatter_text: str | None, *, where: str) -> str:
    if not frontmatter_text:
        return DEFAULT_STRATEGY_ASSET
    fm = parse_skill_frontmatter(frontmatter_text, where=where)
    meta = fm.get("metadata")
    if isinstance(meta, dict):
        rel = str(meta.get("bridge-strategy") or "").strip()
        if rel:
            return rel
    return DEFAULT_STRATEGY_ASSET


def _load_from_dir(process: str, root: Path) -> CollectionStrategy | None:
    skill_dir = (root / process).resolve()
    base = root.resolve()
    if base not in skill_dir.parents or not skill_dir.is_dir():
        return None
    skill_md = skill_dir / "SKILL.md"
    rel = (
        _resolve_strategy_rel(skill_md.read_text(encoding="utf-8"), where=str(skill_md))
        if skill_md.is_file()
        else DEFAULT_STRATEGY_ASSET
    )
    asset_path = (skill_dir / rel).resolve()
    if skill_dir not in asset_path.parents:
        raise StrategyLoadError(
            f"{skill_md}: bridge-strategy {rel!r} escapes the skill dir"
        )
    if not asset_path.is_file():
        return None
    return load_strategy_from_yaml(
        asset_path.read_text(encoding="utf-8"),
        where=str(asset_path),
        expected_process=process,
    )


def load_strategy(
    process: str,
    *,
    skills_dir: Path | None = None,
    use_registry: bool = True,
) -> CollectionStrategy | None:
    """Resolve and validate ``assets/strategy.yaml`` for ``process`` at call time.

    Returns ``None`` when the process skill is absent or declares no strategy
    file. Raises :class:`StrategyLoadError` (fail closed) whenever a strategy
    file is present but malformed. Never caches across calls so live registry
    updates take effect immediately.
    """
    if not _SKILL_SLUG_RE.fullmatch(process or ""):
        return None
    if skills_dir is not None or not use_registry:
        return _load_from_dir(process, skills_root(skills_dir))

    from bridge.ports import Port, get_adapter, resolve_mode

    registry = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
    try:
        skill_md_raw = registry.read_asset(process, "SKILL.md")
    except (KeyError, OSError):
        return None
    rel = _resolve_strategy_rel(skill_md_raw, where=f"{process}/SKILL.md")
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise StrategyLoadError(
            f"{process}/SKILL.md: bridge-strategy {rel!r} escapes the skill dir"
        )
    try:
        raw_strategy = registry.read_asset(process, rel)
    except (KeyError, OSError):
        return None
    return load_strategy_from_yaml(
        raw_strategy,
        where=f"{process}/{rel}",
        expected_process=process,
    )


def strategy_for(process: str) -> CollectionStrategy | None:
    """Read the live collection strategy for ``process`` via ``Port.skill_registry``."""
    return load_strategy(process, use_registry=True)


def render_satisfaction_prose(strategy: CollectionStrategy) -> str:
    """Deterministically generate human-readable satisfaction documentation from ``strategy``."""
    if strategy.description.strip():
        return strategy.description.strip()
    joiner = " OR " if strategy.match == "any_of" else " AND "
    branch_texts: list[str] = []
    for alt in strategy.effective_alternatives():
        rule_parts: list[str] = []
        for rule in alt.rules:
            prefix = "optional " if not rule.required else ""
            qty = f"{rule.min_count}x " if rule.min_count > 1 else ""
            qualifiers: list[str] = []
            if rule.distinct_issuers:
                qualifiers.append("distinct issuers")
            if rule.recency_days is not None and rule.recency_field:
                qualifiers.append(
                    f"{rule.recency_field} within {rule.recency_days} days"
                )
            if rule.not_expired_field:
                qualifiers.append(f"unexpired {rule.not_expired_field}")
            if rule.distinct_issuer_from:
                qualifiers.append(
                    f"issuer distinct from {', '.join(rule.distinct_issuer_from)}"
                )
            suffix = f" ({'; '.join(qualifiers)})" if qualifiers else ""
            rule_parts.append(f"{prefix}{qty}{rule.doctype}{suffix}")
        branch_texts.append(" + ".join(rule_parts))
    return f"Satisfied by: {joiner.join(branch_texts)}."
