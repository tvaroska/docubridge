"""S4-usecase-1: per-request skill consumers resolve ``BRIDGE_SKILLS_DIR`` at read time.

A skill dropped into ``BRIDGE_SKILLS_DIR`` AFTER import must be visible to the policy
loaders that no caller passes ``skills_dir`` to (``design/skills.md``, "Read-time audit").
"""

from __future__ import annotations

import pytest

from bridge.negotiate import load_negotiate_policy
from bridge.policy import load_disposition_policy
from bridge.rfp import load_rfp_policy
from bridge.sla import load_sla_policy, skills_root
from bridge.standard import load_program_standard
from tests._skill_fixtures import POLICY_YAML, PROCESS, SKILL_MD


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    skill = tmp_path / PROCESS
    (skill / "assets").mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL_MD)
    (skill / "assets" / "policy.yaml").write_text(POLICY_YAML)
    return tmp_path


def test_skills_root_follows_the_env_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    assert skills_root() == tmp_path
    assert skills_root(tmp_path / "x") == tmp_path / "x"  # explicit wins


def test_rfp_policy_sees_a_skill_added_to_the_live_registry(live_dir):
    policy = load_rfp_policy(PROCESS)
    assert policy.item_field == "credential"
    assert policy.candidate_doctypes == ("w9-form", "insurance-cert")


def test_sla_and_disposition_policy_see_a_live_skill(live_dir):
    assert load_sla_policy(PROCESS).deadline == 5
    assert load_disposition_policy(PROCESS).thresholds().resubmit_below == 0.55


def test_negotiate_and_standard_loaders_resolve_the_live_root(live_dir):
    # No negotiate:/standard.yaml in the synthetic skill: the point is that the live
    # skill is FOUND (a repo-only default would raise "skill ... not found").
    for loader in (load_negotiate_policy, load_program_standard):
        with pytest.raises(ValueError) as exc:
            loader(PROCESS)
        # Found (then it fails on the missing block/file), never "skill ... not found".
        assert f"skill {PROCESS!r} not found" not in str(exc.value)
