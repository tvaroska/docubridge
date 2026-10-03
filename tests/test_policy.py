"""Disposition-policy loader tests (P6-1) + SLA-loader regression.

Proves ``load_disposition_policy`` reads the ``disposition:`` block of the
address-proof ``policy.yaml`` (the Path-B graph's resubmit cap + gate thresholds
come from the pack) and that the shared ``_load_policy_doc`` refactor left
``load_sla_policy`` behavior-identical.
"""

import pytest

from bridge.disposition import DispositionThresholds
from bridge.policy import DispositionPolicy, load_disposition_policy
from bridge.sla import load_sla_policy


def test_load_disposition_policy():
    """address-proof: max_resubmissions==3, thresholds == DispositionThresholds()."""
    p = load_disposition_policy("address-proof")
    assert p.max_resubmissions == 3
    assert p.auto_approve_at == 0.85
    assert p.resubmit_below == 0.55
    assert p.thresholds() == DispositionThresholds()


def test_thresholds_projection_drops_cap():
    """thresholds() carries only the two gate knobs (max_resubmissions is graph-only)."""
    p = DispositionPolicy(auto_approve_at=0.9, resubmit_below=0.5, max_resubmissions=2)
    t = p.thresholds()
    assert t.auto_approve_at == 0.9
    assert t.resubmit_below == 0.5


def test_sla_policy_unchanged():
    """Regression: _load_policy_doc extraction left load_sla_policy identical."""
    p = load_sla_policy("address-proof")
    assert p.cadence == 2
    assert p.deadline == 3
    assert p.max_nudges == 2
    assert p.overdue_ticks == 1
    assert p.reminder_ticks == 2
    assert p.escalate_ticks == 3


def test_missing_disposition_block_rejected(tmp_path):
    """A policy.yaml with no disposition: block raises."""
    skill_dir = tmp_path / "no-disp"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: no-disp\nmetadata:\n  bridge-policy: assets/policy.yaml\n---\nbody\n"
    )
    assets = skill_dir / "assets"
    assets.mkdir()
    (assets / "policy.yaml").write_text("sla:\n  deadline: 3\n")
    with pytest.raises(ValueError, match="missing 'disposition:' block"):
        load_disposition_policy("no-disp", skills_dir=tmp_path)


def test_negative_max_resubmissions_rejected(tmp_path):
    """max_resubmissions < 0 raises."""
    skill_dir = tmp_path / "bad-cap"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad-cap\nmetadata:\n  bridge-policy: assets/policy.yaml\n---\nbody\n"
    )
    assets = skill_dir / "assets"
    assets.mkdir()
    (assets / "policy.yaml").write_text(
        "disposition:\n  auto_approve_at: 0.85\n  resubmit_below: 0.55\n  max_resubmissions: -1\n"
    )
    with pytest.raises(ValueError, match="max_resubmissions must be >= 0"):
        load_disposition_policy("bad-cap", skills_dir=tmp_path)


def test_inverted_thresholds_rejected_naming_skill(tmp_path):
    """resubmit_below > auto_approve_at fails at load with a ValueError naming the skill."""
    skill_dir = tmp_path / "inverted"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: inverted\nmetadata:\n  bridge-policy: assets/policy.yaml\n---\nbody\n"
    )
    assets = skill_dir / "assets"
    assets.mkdir()
    (assets / "policy.yaml").write_text(
        "disposition:\n  auto_approve_at: 0.5\n  resubmit_below: 0.6\n  max_resubmissions: 3\n"
    )
    with pytest.raises(ValueError, match="skill 'inverted' 'disposition:' thresholds invalid"):
        load_disposition_policy("inverted", skills_dir=tmp_path)
