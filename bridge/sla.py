"""SLA/policy engine (PROA-1) — reads the process-skill ``policy.yaml`` ``sla:`` block.

Loads cadence / deadline / max_nudges / ladder from a process skill's declared policy
file (defaulting to ``assets/policy.yaml``, overridden by the SKILL.md ``bridge-policy``
frontmatter pointer). Validates invariants (monotone ladder, escalate==deadline,
max_nudges==2) and exposes a derived ``schedule()`` mapping each ladder step to the
tick it fires at, typed to the existing ``contract.enums.LifecycleStatus``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from contract.enums import LifecycleStatus

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def skills_root(skills_dir: Path | None = None) -> Path:
    """The skill registry root, resolved at CALL time.

    An explicit ``skills_dir`` wins; else ``BRIDGE_SKILLS_DIR``; else the repo
    ``skills/`` dir (the same rule as ``LocalSkillRegistry``). Never a default-argument
    constant: a skill dropped into ``BRIDGE_SKILLS_DIR`` after import must be seen.
    """
    if skills_dir is not None:
        return skills_dir
    return Path(os.environ.get("BRIDGE_SKILLS_DIR", "").strip() or SKILLS_DIR)

# The three ladder steps, in escalation order (on_track is the base state, not a step).
LADDER_STEPS: tuple[LifecycleStatus, ...] = (
    LifecycleStatus.overdue,
    LifecycleStatus.reminder,
    LifecycleStatus.escalated,
)


@dataclass(frozen=True)
class SlaPolicy:
    """The SLA / follow-up policy read from a process skill's policy.yaml ``sla:`` block.

    ``cadence``/``deadline``/``max_nudges`` are the policy knobs; ``*_ticks`` give each
    ladder step's fire tick; ``ladder`` is the ordered step vocabulary.
    """
    cadence: int
    deadline: int
    max_nudges: int
    overdue_ticks: int
    reminder_ticks: int
    escalate_ticks: int
    ladder: tuple[LifecycleStatus, ...]

    def schedule(self) -> list[tuple[int, LifecycleStatus]]:
        """Ladder steps as (fire_tick, step), sorted ascending by tick.
        For address-proof: [(1, overdue), (2, reminder), (3, escalated)]."""
        pairs = {
            LifecycleStatus.overdue: self.overdue_ticks,
            LifecycleStatus.reminder: self.reminder_ticks,
            LifecycleStatus.escalated: self.escalate_ticks,
        }
        return sorted(((pairs[s], s) for s in self.ladder), key=lambda p: p[0])


def parse_skill_frontmatter(raw: str, *, where: str) -> dict:
    """Parse the YAML frontmatter mapping out of raw ``SKILL.md`` text.

    The pure half of ``_load_skill_frontmatter``, shared with the live seam reader
    (``bridge.edge.skills.rfp_policy_for``, which reads ``SKILL.md`` through the
    ``skill_registry`` seam). ``where`` names the source in error messages. Raises
    ``ValueError`` when the ``---`` fence is missing or the frontmatter is not a mapping.
    """
    parts = raw.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"{where} missing frontmatter fence (---…---)")
    frontmatter = yaml.safe_load(parts[1])
    if frontmatter is None:
        return {}
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{where} frontmatter is not a mapping")
    return frontmatter


def _load_skill_frontmatter(skill_name: str, *, skills_dir: Path | None = None) -> dict:
    """Parse a skill's ``SKILL.md`` YAML frontmatter (the whole mapping).

    Pure (stdlib + yaml) so the pure process loaders (``bridge.rfp``) can read a skill's
    ``metadata.bridge-*`` keys without importing ``bridge.edge`` (a2a / ADK). Raises
    ``ValueError`` when the skill is absent or its frontmatter fence is missing.
    """
    skill_md = skills_root(skills_dir) / skill_name / "SKILL.md"
    if not skill_md.exists():
        raise ValueError(f"skill {skill_name!r} not found at {skill_md}")
    return parse_skill_frontmatter(skill_md.read_text(), where=str(skill_md))


def _load_policy_doc(skill_name: str, *, skills_dir: Path | None = None) -> dict:
    """Resolve + parse a process skill's declared ``policy.yaml`` (the whole doc).

    Shared by ``load_sla_policy`` (``sla:`` block) and
    ``bridge.policy.load_disposition_policy`` (``disposition:`` block): the skill's
    ``SKILL.md`` frontmatter ``metadata.bridge-policy`` pointer declares the policy
    file path (relative to the skill dir), defaulting to ``assets/policy.yaml`` if
    absent. Returns the full ``yaml.safe_load`` doc; block-level parsing/validation
    is each caller's job.
    """
    skill_dir = skills_root(skills_dir) / skill_name
    frontmatter = _load_skill_frontmatter(skill_name, skills_dir=skills_dir)

    # Resolve policy file path (default assets/policy.yaml)
    policy_rel = frontmatter.get("metadata", {}).get("bridge-policy", "assets/policy.yaml")
    policy_path = skill_dir / policy_rel
    if not policy_path.exists():
        raise ValueError(f"policy file {policy_path} not found")

    return yaml.safe_load(policy_path.read_text())


def load_sla_policy(skill_name: str, *, skills_dir: Path | None = None) -> SlaPolicy:
    """Load the SLA policy from a process skill's declared policy file.

    The skill's ``SKILL.md`` frontmatter ``metadata.bridge-policy`` pointer declares
    the policy file path (relative to the skill dir), defaulting to
    ``assets/policy.yaml`` if absent. The ``sla:`` block is parsed, validated
    (monotone ladder, escalate==deadline, max_nudges==2), and returned as a
    ``SlaPolicy`` with typed ``LifecycleStatus`` ladder steps.
    """
    policy_doc = _load_policy_doc(skill_name, skills_dir=skills_dir)
    # ``where`` is for error messages only (kept as the historical default path).
    where = str(skills_root(skills_dir) / skill_name / "assets/policy.yaml")
    return sla_policy_from_doc(policy_doc, where=where)


def sla_policy_from_doc(policy_doc: dict, *, where: str) -> SlaPolicy:
    """Parse + validate the ``sla:`` block of an already-loaded policy doc.

    The pure half of ``load_sla_policy``, shared with the live seam reader (the policy
    doc read through the ``skill_registry`` seam). ``where`` names the source in error
    messages. Validates the monotone ladder, escalate==deadline and max_nudges==2.
    """
    policy_path = where
    sla = policy_doc.get("sla")
    if not sla:
        raise ValueError(f"{policy_path} missing 'sla:' block")

    # Extract fields
    cadence = sla["cadence"]
    deadline = sla["deadline"]
    max_nudges = sla["max_nudges"]
    overdue_ticks = sla["overdue_ticks"]
    reminder_ticks = sla["reminder_ticks"]
    escalate_ticks = sla["escalate_ticks"]

    # Map ladder step strings to LifecycleStatus
    ladder_raw = sla.get("ladder", [])
    try:
        ladder = tuple(LifecycleStatus(step) for step in ladder_raw)
    except ValueError as e:
        raise ValueError(f"unknown ladder step in {policy_path}: {e}") from e

    # Validate invariants (mirrors test_skills.py:139-161)
    # 1. ladder == [overdue, reminder, escalated] (order + membership)
    if ladder != LADDER_STEPS:
        raise ValueError(
            f"ladder must be {[s.value for s in LADDER_STEPS]} in order; "
            f"got {[s.value for s in ladder]}"
        )

    # 2. escalate_ticks == deadline
    if escalate_ticks != deadline:
        raise ValueError(
            f"escalate_ticks ({escalate_ticks}) must equal deadline ({deadline})"
        )

    # 3. 0 < overdue_ticks < reminder_ticks < escalate_ticks (monotone ladder)
    if not (0 < overdue_ticks < reminder_ticks < escalate_ticks):
        raise ValueError(
            f"ladder ticks must be monotone increasing: "
            f"0 < overdue_ticks ({overdue_ticks}) < reminder_ticks ({reminder_ticks}) "
            f"< escalate_ticks ({escalate_ticks})"
        )

    # 4. max_nudges == 2 (overdue + reminder before escalation)
    if max_nudges != 2:
        raise ValueError(
            f"max_nudges must be 2 (overdue + reminder before escalation); got {max_nudges}"
        )

    return SlaPolicy(
        cadence=cadence,
        deadline=deadline,
        max_nudges=max_nudges,
        overdue_ticks=overdue_ticks,
        reminder_ticks=reminder_ticks,
        escalate_ticks=escalate_ticks,
        ladder=ladder,
    )
