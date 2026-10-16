"""Skill metadata source for the dynamic Agent Card (WIRE-4) — the live, resolved view.

``loaded_skills()`` reads the ``skill_registry`` seam on every call (a directory of
Agent Skills folders locally, a GCS bucket on GCP), so a skill folder added at runtime
regenerates the Agent Card and reaches the executor with no redeploy (SKILL-4, CUJ-8/9).
The card-generation logic (``bridge.edge.card.agent_card_from_skills``) stays pure; this
module is the source.

Decision 4 (process vs doctype skills): One ``AgentSkill`` per **process** skill;
doctype skills are the label space (``candidate_doctypes``), not separate
advertised skills.

**A bad live upload is refused, not propagated (S4-usecase-3).** Two layers:

- **(a) structural, per folder** — ``skill_meta_from_dir`` is the strict validator
  (every refusal is a ``ValueError`` naming the folder and the reason). Both adapters'
  ``list_skills`` go through ``listed_skill_meta``, which skips a malformed folder with a
  WARNING on this module's logger and keeps listing; both adapters' ``install_skill`` go
  through ``validate_skill_upload`` and refuse a malformed upload BEFORE any write.
- **(b) cross-skill, per listing** — ``loaded_skills`` applies ``resolve_label_spaces``: a
  process's label space is its declared candidate doctypes **that are installed**, and a
  process none of whose candidates is installed is not loaded at all (never advertised,
  never dispositioned against another process's label space — S0-arch-18).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from google.adk.skills._utils import _parse_skill_md_content
from google.adk.skills.models import Frontmatter
from pydantic import BaseModel, ConfigDict

from bridge.doctypes import DEFAULT_SCHEMA_ASSET

if TYPE_CHECKING:
    from bridge.rfp import RfpPolicy

logger = logging.getLogger(__name__)

# The two skill kinds (``metadata.bridge-kind``); anything else refuses the folder.
SKILL_KINDS: tuple[str, ...] = ("process", "doctype")

# A process skill's policy pointer default (``metadata.bridge-policy``) — the same
# pointer-or-default resolution as ``bridge.sla._load_policy_doc``.
DEFAULT_POLICY_ASSET = "assets/policy.yaml"


class SkillMeta(BaseModel):
    """Skill metadata extracted from a skill folder's SKILL.md frontmatter.

    Mirrors the Agent Skills spec shape plus Bridge-specific metadata keys
    (``bridge-kind``, ``bridge-candidate-doctypes``, ``bridge-schema``).
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # Lowercase-hyphen, matches skill dir + AgentSkill.id
    description: str  # Discovery surface: what + when to use (≤1024 chars)
    kind: Literal["process", "doctype"]  # mirrors metadata.bridge-kind (SKILL_KINDS)
    candidate_doctypes: tuple[str, ...] = ()  # Process skills only (bridge-candidate-doctypes)
    tags: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ("application/json", "text/plain")  # WIRE-5 dual-mode intake
    # Skill-relative pointer to the doctype's field schema (metadata.bridge-schema);
    # read through the registry seam's ``read_asset``. NOT named ``schema`` — that
    # shadows ``BaseModel.schema`` and pydantic v2 warns on it.
    schema_asset: str = ""
    # Who owns a Collector ``hitl_review`` verdict (metadata.bridge-hitl, S3-edge-1
    # D5): ``report`` (default) — the pending entry rides a terminal turn and the
    # consumer reviews it; ``park`` — the Bridge owns review and, on a hybrid edge,
    # hands the turn to the LLM leg, which parks the task on ``request_human_review``.
    hitl: Literal["park", "report"] = "report"
    # The process skill's prose satisfaction description (metadata.bridge-satisfaction,
    # COLL-2): advisory, never a rule — read by the Bridge's satisfaction assessor
    # (``bridge.satisfaction``) when one is bound. Empty → never assessed.
    satisfaction: str = ""


def _contained_asset(skill_dir: Path, rel: str, where: Path, key: str) -> Path:
    """Resolve a skill-relative frontmatter pointer; refuse one escaping the skill dir."""
    base = skill_dir.resolve()
    path = (base / rel).resolve()
    if base not in path.parents:
        raise ValueError(f"{where}: {key} {rel!r} escapes the skill dir")
    return path


def skill_meta_from_dir(skill_dir: Path) -> SkillMeta:
    """Parse a skill folder's ``SKILL.md`` frontmatter into a ``SkillMeta``.

    Pure (no seam import) — mirrors ``bridge.sla._load_policy_doc``'s frontmatter
    split. Bridge-specific metadata keys live under ``metadata.bridge-*``:
    ``bridge-kind``, ``bridge-candidate-doctypes`` (space-separated), ``bridge-tags``
    (space-separated), optional ``bridge-input-modes`` (space-separated; defaults to
    the ``SkillMeta`` dual-mode default when absent, preserving WIRE-5), optional
    ``bridge-schema`` (a skill-relative path to the doctype's field schema asset,
    read at extraction time through the registry seam's ``read_asset``), optional
    ``bridge-hitl`` (``park`` | ``report``; absent → ``report``; any other value fails
    the load with a ``ValidationError``), optional ``bridge-satisfaction`` (prose,
    advisory; absent → ``""``).

    The strict validator (S4-usecase-3): a malformed folder raises ``ValueError``
    naming the folder and the reason —

    - the frontmatter fence is missing (a half-copied / truncated ``SKILL.md``);
    - the frontmatter YAML does not parse, is not a mapping, or its ``metadata`` is
      present but not a mapping;
    - ``name``, ``description`` or ``metadata.bridge-kind`` is missing;
    - ``bridge-kind`` is not one of ``SKILL_KINDS``;
    - ``name`` differs from the folder name (a ``cp -r x x.bak`` would otherwise yield
      duplicate card ids);
    - a **process** skill's policy file (``bridge-policy``, default
      ``assets/policy.yaml``) is present but is not a YAML mapping;
    - a **doctype** skill's schema asset (``bridge-schema``, default
      ``assets/schema.json``) is present but is not valid JSON;
    - either pointer resolves outside the skill dir.

    An ABSENT policy / schema file stays legitimate (a half-installed skill, S0-arch-5):
    absent = legit, present-but-corrupt = refuse. A pydantic ``ValidationError`` (an
    invalid ``bridge-hitl``) is raised as-is; it already subclasses ``ValueError``.
    """
    md = skill_dir / "SKILL.md"
    raw = md.read_text()
    try:
        fm, _body = _parse_skill_md_content(raw)
    except ValueError as exc:
        msg = str(exc)
        if "frontmatter" in msg.lower():
            raise ValueError(
                f"{md}: missing frontmatter fence (---...---) ({msg})"
            ) from exc
        raise ValueError(f"{md}: {msg}") from exc
    meta = fm.get("metadata")
    meta = {} if meta is None else meta
    if not isinstance(meta, dict):
        raise ValueError(f"{md}: frontmatter 'metadata' is not a mapping")
    for key, block in (("name", fm), ("description", fm), ("bridge-kind", meta)):
        if key not in block:
            label = key if block is fm else f"metadata.{key}"
            raise ValueError(f"{md}: missing frontmatter key {label!r}")
    adk_fm = Frontmatter(name=str(fm["name"]), description=str(fm["description"]))
    kind = meta["bridge-kind"]
    if kind not in SKILL_KINDS:
        raise ValueError(
            f"{md}: unknown bridge-kind {kind!r} (expected process or doctype)"
        )
    if adk_fm.name != skill_dir.name:
        raise ValueError(
            f"{md}: frontmatter name {adk_fm.name!r} does not match its folder "
            f"{skill_dir.name!r}"
        )
    schema_asset = str(meta.get("bridge-schema", "") or "")
    if kind == "process":
        rel = str(meta.get("bridge-policy") or DEFAULT_POLICY_ASSET)
        policy = _contained_asset(skill_dir, rel, md, "bridge-policy")
        if policy.is_file():
            try:
                doc = yaml.safe_load(policy.read_text())
            except yaml.YAMLError as exc:
                detail = " ".join(str(exc).split())
                raise ValueError(
                    f"{md}: policy {rel} is not valid YAML ({detail})"
                ) from exc
            if not isinstance(doc, dict):
                raise ValueError(f"{md}: policy {rel} is not a mapping")
    else:
        rel = schema_asset or DEFAULT_SCHEMA_ASSET
        schema = _contained_asset(skill_dir, rel, md, "bridge-schema")
        if schema.is_file():
            try:
                json.loads(schema.read_text())
            except json.JSONDecodeError as exc:
                raise ValueError(f"{md}: schema {rel} is not valid JSON ({exc})") from exc
    return SkillMeta(
        name=fm["name"],
        description=fm["description"],
        kind=kind,
        candidate_doctypes=tuple(str(meta.get("bridge-candidate-doctypes", "")).split()),
        tags=tuple(str(meta.get("bridge-tags", "")).split()),
        schema_asset=schema_asset,
        satisfaction=str(meta.get("bridge-satisfaction", "") or "").strip(),
        **({"hitl": meta["bridge-hitl"]} if meta.get("bridge-hitl") else {}),
        **(
            {"input_modes": tuple(str(meta["bridge-input-modes"]).split())}
            if meta.get("bridge-input-modes")
            else {}
        ),
    )


def listed_skill_meta(skill_dir: Path) -> SkillMeta | None:
    """Listing-time parse: a malformed folder is skipped with a WARNING, never raised.

    Shared by both adapters' ``list_skills`` (same parser, same tolerance on local and
    gcp). ``ValidationError`` and ``UnicodeDecodeError`` subclass ``ValueError``; the
    parser converts every other malformation into one. Logged on every call (no dedupe
    state): while a bad folder sits in the registry, each listing names it — that is the
    operator signal. ``get_skill`` stays strict and raises.
    """
    try:
        return skill_meta_from_dir(skill_dir)
    except (ValueError, OSError) as exc:
        logger.warning("skill folder %r skipped: %s", skill_dir.name, exc)
        return None


def resolve_label_spaces(skills: list[SkillMeta]) -> list[SkillMeta]:
    """Resolve each process skill's label space against the installed doctypes.

    Pure (no I/O). A process's label space is its declared ``candidate_doctypes`` that
    are installed, in declared order:

    - none declared → unchanged (S0-arch-18 case (b): an empty label space that rejects
      every document ``unsupported_doctype``);
    - all installed → the same object;
    - some missing → narrowed (WARNING naming the missing ones), so executor label space
      == Agent Card tags and a document of an uninstalled doctype is
      ``unsupported_doctype`` rather than accepted with no schema check;
    - none installed → dropped (WARNING): not advertised, ``candidate_doctypes_for`` →
      ``[]``, never another process's label space.

    Doctype skills pass through unchanged.
    """
    installed = {s.name for s in skills if s.kind == "doctype"}
    resolved: list[SkillMeta] = []
    for skill in skills:
        if skill.kind != "process" or not skill.candidate_doctypes:
            resolved.append(skill)
            continue
        present = tuple(d for d in skill.candidate_doctypes if d in installed)
        missing = [d for d in skill.candidate_doctypes if d not in installed]
        if not present:
            logger.warning(
                "process skill %r skipped: none of its candidate doctypes (%s) is "
                "installed",
                skill.name,
                ", ".join(skill.candidate_doctypes),
            )
            continue
        if missing:
            logger.warning(
                "process skill %r: candidate doctypes (%s) are not installed; omitted "
                "from its label space and Agent Card until installed",
                skill.name,
                ", ".join(missing),
            )
            skill = skill.model_copy(update={"candidate_doctypes": present})
        resolved.append(skill)
    return resolved


def validate_skill_upload(
    name: str, skill_md: str, assets: dict[str, str] | None = None
) -> SkillMeta:
    """Validate a ``install_skill`` upload in a temp staging dir, before any real write.

    Shared by both adapters' ``install_skill``. The upload is written to
    ``<tmp>/<name>`` and parsed strictly (``skill_meta_from_dir``), so a malformed
    upload raises ``ValueError`` and nothing reaches the registry — a bad re-upload can
    never overwrite a good skill. Every asset path must resolve strictly inside the
    staged dir (callers run their own traversal check against the real destination
    first). Cross-skill state is NOT checked: a process may be installed before its
    doctypes; ``resolve_label_spaces`` handles that at read time.
    """
    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
    with tempfile.TemporaryDirectory() as tmp:
        staged = (Path(tmp) / name).resolve()
        (staged / "assets").mkdir(parents=True)
        (staged / "SKILL.md").write_text(skill_md)
        for rel, content in (assets or {}).items():
            dest = (staged / rel).resolve()
            if staged not in dest.parents:
                raise ValueError(f"asset path {rel!r} escapes skill dir")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        return skill_meta_from_dir(staged)


def loaded_skills() -> list[SkillMeta]:
    """Return the resolved, loaded skill metadata via the ``skill_registry`` seam (SKILL-4).

    Reads the live registry (``LocalSkillRegistry`` — a directory of Agent Skills
    folders rooted at ``BRIDGE_SKILLS_DIR`` / the repo ``skills/`` dir). Because the
    Agent Card is rebuilt from this on every discovery GET (``card_modifier``),
    installing a skill folder at runtime regenerates the card with no redeploy.

    The adapters already skipped malformed folders (layer (a)); this applies layer (b),
    ``resolve_label_spaces``, so a process's label space is its installed candidates and
    a process with none installed is not loaded (S4-usecase-3).

    Only process skills become ``AgentSkill``s (Decision 4); doctype skills are the
    label space. The seam import is lazy to avoid an import cycle (the local adapter
    lazily imports ``skill_meta_from_dir`` from this module).
    """
    from bridge.seams import Seam, get_adapter, resolve_mode

    registry = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
    return resolve_label_spaces(registry.list_skills())


def candidate_doctypes_for(process: str) -> list[str]:
    """Return the candidate doctypes (the label space) for a process skill.

    Reads the process skill's ``candidate_doctypes`` (``metadata.bridge-candidate-
    doctypes``) from ``loaded_skills()`` — the declared candidates **that are
    installed** (``resolve_label_spaces``). An unresolved process (no loaded process
    skill of that name, one declaring no ``bridge-candidate-doctypes``, or one none of
    whose candidates is installed) → ``[]``:
    an empty label space, so the label-space gate rejects every document
    ``unsupported_doctype``. The Bridge never borrows another process's doctypes
    (S0-arch-18). Used by ``CollectExecutor._candidate_doctypes`` (Decision 5:
    consolidated source).
    """
    for skill in loaded_skills():
        if skill.kind == "process" and skill.name == process:
            return list(skill.candidate_doctypes)
    return []


def hitl_mode_for(process: str) -> Literal["park", "report"]:
    """Return the process skill's HITL mode (``metadata.bridge-hitl``, D5).

    An unresolved process (no loaded process skill of that name) → ``report``: the
    Bridge never parks a task for a process that did not opt in.
    """
    for skill in loaded_skills():
        if skill.kind == "process" and skill.name == process:
            return skill.hitl
    return "report"


def satisfaction_for(process: str) -> str:
    """Return the process skill's prose satisfaction description (COLL-2).

    ``metadata.bridge-satisfaction`` of the loaded process skill named ``process``;
    an unresolved process (or one without the key) → ``""`` — it is never assessed.
    """
    for skill in loaded_skills():
        if skill.kind == "process" and skill.name == process:
            return skill.satisfaction
    return ""


def rfp_policy_for(process: str) -> RfpPolicy:
    """Return the RFP policy of a live emergent-Collect process skill (S4-usecase-4).

    The live resolver an ``RfpBoard`` binds as ``live_policy`` on the deployed hosts
    (``rfp_board_from_env``, ``build_rfp_board_app``): read on EVERY call, entirely
    through the ``skill_registry`` seam, so a process installed after the host was built
    — locally or into the GCS bucket — opens, lists, checklists and files as an RFP with
    no rebuild:

    - the process must be a loaded ``kind == "process"`` skill in ``loaded_skills()``
      (the narrowed label space, S4-usecase-3 layer (b)); absent — not installed,
      structurally refused, or none of its candidates installed — raises ``ValueError``;
    - ``SKILL.md`` and the declared policy file (``bridge-policy``, default
      ``assets/policy.yaml``) are read with ``registry.read_asset`` — never the
      filesystem loaders, which would not see a gcp-installed skill;
    - both are validated by the same pure parser as the build-time loader
      (``bridge.rfp.rfp_policy_from_docs``), so "is this an RFP process" has ONE test:
      kind ``process`` + pattern ``collect`` + candidates + an ``rfp:`` block;
    - ``candidate_doctypes`` is the NARROWED label space (installed candidates only), so
      the board's label space equals the Agent Card tags.

    Any refusal is a ``ValueError`` (a ``KeyError`` / ``OSError`` from ``read_asset`` —
    absent, or removed mid-read — is converted);
    nothing broader is caught. A sync registry read — callers on the event loop accept
    that, like ``candidate_doctypes_for``.
    """
    from bridge.rfp import rfp_policy_from_docs
    from bridge.seams import Seam, get_adapter, resolve_mode
    from bridge.sla import parse_skill_frontmatter

    meta = next(
        (s for s in loaded_skills() if s.kind == "process" and s.name == process),
        None,
    )
    if meta is None:
        raise ValueError(f"skill {process!r} is not an installed process skill")
    registry = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
    try:
        frontmatter = parse_skill_frontmatter(
            registry.read_asset(process, "SKILL.md"), where=f"{process}/SKILL.md"
        )
    except (KeyError, OSError) as exc:
        raise ValueError(f"skill {process!r} SKILL.md not readable: {exc}") from exc
    fm_meta = frontmatter.get("metadata") or {}
    policy_rel = str(
        (fm_meta.get("bridge-policy") if isinstance(fm_meta, dict) else None)
        or DEFAULT_POLICY_ASSET
    )
    where = f"{process}/{policy_rel}"

    def load_policy_doc() -> dict:
        try:
            return yaml.safe_load(registry.read_asset(process, policy_rel))
        except (KeyError, OSError) as exc:
            raise ValueError(f"policy file {where} not found") from exc
        except yaml.YAMLError as exc:
            raise ValueError(f"policy file {where} is not valid YAML") from exc

    policy = rfp_policy_from_docs(process, frontmatter, load_policy_doc, where=where)
    if meta.candidate_doctypes != policy.candidate_doctypes:
        policy = dataclasses.replace(policy, candidate_doctypes=meta.candidate_doctypes)
    return policy
