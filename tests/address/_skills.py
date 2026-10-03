"""Shared skill folder parser for address eval parity tests.

Small, dependency-light parser mirroring the _corpus.py idiom. Provides:
- parse_skill_md: split frontmatter from body
- load_skill: load a skill folder with frontmatter, body, and assets
"""
import json
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


def parse_skill_md(md_path: Path) -> dict:
    """Split leading `---\n...\n---` YAML frontmatter from the Markdown body.

    Returns {"frontmatter": <dict>, "body": <str>}.
    """
    text = md_path.read_text()

    # Require frontmatter fence
    if not text.startswith("---\n"):
        raise ValueError(f"{md_path.name}: SKILL.md must start with '---' frontmatter fence")

    # Find the closing fence
    rest = text[4:]  # Skip opening "---\n"
    closing_fence = rest.find("\n---\n")
    if closing_fence == -1:
        raise ValueError(f"{md_path.name}: SKILL.md missing closing '---' fence")

    frontmatter_block = rest[:closing_fence]
    body = rest[closing_fence + 5:].strip()  # Skip "\n---\n"

    frontmatter = yaml.safe_load(frontmatter_block)
    if frontmatter is None:
        frontmatter = {}

    return {"frontmatter": frontmatter, "body": body}


def load_skill(name: str) -> dict:
    """Return {"name","dir","frontmatter","body","metadata","assets": {...}}.

    Loads assets/*.json via json, assets/*.yaml via yaml.safe_load, on demand.
    """
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        raise FileNotFoundError(f"Skill directory not found: {skill_dir}")

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")

    parsed = parse_skill_md(skill_md)
    frontmatter = parsed["frontmatter"]
    body = parsed["body"]
    metadata = frontmatter.get("metadata", {})

    # Load assets
    assets = {}
    assets_dir = skill_dir / "assets"
    if assets_dir.exists():
        for asset_file in assets_dir.iterdir():
            if asset_file.suffix == ".json":
                assets[asset_file.name] = json.loads(asset_file.read_text())
            elif asset_file.suffix == ".yaml":
                assets[asset_file.name] = yaml.safe_load(asset_file.read_text())

    return {
        "name": name,
        "dir": skill_dir,
        "frontmatter": frontmatter,
        "body": body,
        "metadata": metadata,
        "assets": assets,
    }
