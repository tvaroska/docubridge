"""Discover and validate `scenarios/<name>/scenario.yaml` manifests.

Resolution mirrors `bridge.sla.skills_root`: an explicit directory wins, then
`BRIDGE_SCENARIOS_DIR`, then the repo `scenarios/` folder — resolved at call time
so a folder dropped in after import is seen.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from contract.scenario import ScenarioManifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = _REPO_ROOT / "scenarios"
SCENARIOS_DIR_ENV = "BRIDGE_SCENARIOS_DIR"
MANIFEST_FILENAME = "scenario.yaml"
DEMO_STATIC_DIR = _REPO_ROOT / "demo"
DEMO_STATIC_DIR_ENV = "BRIDGE_DEMO_STATIC_DIR"


class ScenarioLoadError(ValueError):
    """A manifest is missing, unparsable, or fails wire validation."""


def scenarios_root(scenarios_dir: Path | None = None) -> Path:
    if scenarios_dir is not None:
        return scenarios_dir
    return Path(os.environ.get(SCENARIOS_DIR_ENV, "").strip() or SCENARIOS_DIR)


def demo_static_root(static_dir: Path | None = None) -> Path:
    """Folder the `/demo*` and `/ebc*` console assets are served from.

    A downstream showcase ships its own console files and points
    `BRIDGE_DEMO_STATIC_DIR` at them; the repo `demo/` folder is only the default.
    """
    if static_dir is not None:
        return static_dir
    return Path(os.environ.get(DEMO_STATIC_DIR_ENV, "").strip() or DEMO_STATIC_DIR)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScenarioLoadError(f"{path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ScenarioLoadError(f"{path}: top level must be a mapping")
    return doc


def _inline_pdf_specs(doc: dict[str, Any], folder: Path) -> None:
    """Replace each `pdf_assets[].spec` file reference with its `document_title` + `pages`."""
    stage = doc.get("stage")
    if not isinstance(stage, dict):
        return
    for asset in stage.get("pdf_assets") or []:
        spec_rel = asset.pop("spec", None)
        if spec_rel is None:
            continue
        spec = _read_yaml(folder / spec_rel)
        asset.setdefault(
            "document_title", spec.get("document_title", asset.get("title"))
        )
        asset.setdefault("pages", spec.get("pages", []))


def load_manifest(path: Path) -> ScenarioManifest:
    doc = _read_yaml(path)
    _inline_pdf_specs(doc, path.parent)
    try:
        manifest = ScenarioManifest.model_validate(doc)
    except ValidationError as exc:
        raise ScenarioLoadError(f"{path}: {exc}") from exc
    if manifest.id != path.parent.name:
        raise ScenarioLoadError(
            f"{path}: manifest id {manifest.id!r} must equal its folder name "
            f"{path.parent.name!r}"
        )
    return manifest


def discover_manifests(
    scenarios_dir: Path | None = None,
) -> tuple[ScenarioManifest, ...]:
    """All manifests under the scenarios root, ordered by (`order`, `id`)."""
    root = scenarios_root(scenarios_dir)
    if not root.is_dir():
        return ()
    manifests = [load_manifest(p) for p in sorted(root.glob(f"*/{MANIFEST_FILENAME}"))]
    ids = [m.id for m in manifests]
    if len(ids) != len(set(ids)):
        raise ScenarioLoadError(f"duplicate scenario ids under {root}: {ids}")
    return tuple(sorted(manifests, key=lambda m: (m.order, m.id)))


__all__ = [
    "DEMO_STATIC_DIR",
    "DEMO_STATIC_DIR_ENV",
    "MANIFEST_FILENAME",
    "SCENARIOS_DIR",
    "SCENARIOS_DIR_ENV",
    "ScenarioLoadError",
    "demo_static_root",
    "discover_manifests",
    "load_manifest",
    "scenarios_root",
]
