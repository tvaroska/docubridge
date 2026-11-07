"""SLA/policy engine tests (P5-1, PROA-1) + compose integration.

Tests ``load_sla_policy`` against the address-proof corpus, validates invariants,
and proves the VirtualClock + SlaPolicy composition — the port-level shape of the
CUJ-6 follow-up ladder (P5-2 adds the re-arming state machine).
"""

from pathlib import Path

import pytest

from bridge.scheduler import VirtualClock
from bridge.sla import LADDER_STEPS, load_sla_policy
from contract.enums import LifecycleStatus
from tests.corpus_and_skills._corpus import load_timeline


class TestLoadSlaPolicy:
    """Test loading SLA policy from address-proof skill."""

    def test_load_address_proof(self):
        """Load address-proof policy and verify all fields."""
        p = load_sla_policy("address-proof")

        assert p.cadence == 2
        assert p.deadline == 3
        assert p.max_nudges == 2
        assert p.overdue_ticks == 1
        assert p.reminder_ticks == 2
        assert p.escalate_ticks == 3
        assert p.ladder == (
            LifecycleStatus.overdue,
            LifecycleStatus.reminder,
            LifecycleStatus.escalated,
        )
        assert p.ladder == LADDER_STEPS

    def test_schedule(self):
        """schedule() returns ladder steps as (tick, step) pairs."""
        p = load_sla_policy("address-proof")
        sched = p.schedule()

        assert sched == [
            (1, LifecycleStatus.overdue),
            (2, LifecycleStatus.reminder),
            (3, LifecycleStatus.escalated),
        ]

    def test_timeline_parity(self):
        """Policy numbers match timeline.json SLA."""
        p = load_sla_policy("address-proof")
        timeline = load_timeline()
        # Timeline SLA is the same across both exchanges
        sla = timeline["exchanges"][0]["sla"]

        assert p.overdue_ticks == sla["overdue_ticks"]
        assert p.reminder_ticks == sla["reminder_ticks"]
        assert p.escalate_ticks == sla["escalate_ticks"]


class TestSlaValidation:
    """Test policy validation (invariants)."""

    def test_ladder_wrong_order_rejected(self, tmp_path):
        """Ladder with wrong order is rejected."""
        self._write_bad_policy(
            tmp_path, {"ladder": ["reminder", "overdue", "escalated"]}
        )
        with pytest.raises(ValueError, match="ladder must be"):
            load_sla_policy("bad-skill", skills_dir=tmp_path)

    def test_ladder_wrong_members_rejected(self, tmp_path):
        """Ladder with wrong members is rejected."""
        self._write_bad_policy(tmp_path, {"ladder": ["overdue", "reminder"]})
        with pytest.raises(ValueError, match="ladder must be"):
            load_sla_policy("bad-skill", skills_dir=tmp_path)

    def test_escalate_not_equal_deadline_rejected(self, tmp_path):
        """escalate_ticks != deadline is rejected."""
        self._write_bad_policy(tmp_path, {"deadline": 3, "escalate_ticks": 4})
        with pytest.raises(ValueError, match=r"escalate_ticks.*must equal deadline"):
            load_sla_policy("bad-skill", skills_dir=tmp_path)

    def test_non_monotone_ticks_rejected(self, tmp_path):
        """Non-monotone ladder ticks are rejected."""
        self._write_bad_policy(
            tmp_path,
            {"overdue_ticks": 2, "reminder_ticks": 1, "escalate_ticks": 3},
        )
        with pytest.raises(ValueError, match="monotone"):
            load_sla_policy("bad-skill", skills_dir=tmp_path)

    def test_max_nudges_not_2_rejected(self, tmp_path):
        """max_nudges != 2 is rejected."""
        self._write_bad_policy(tmp_path, {"max_nudges": 3})
        with pytest.raises(ValueError, match="max_nudges must be 2"):
            load_sla_policy("bad-skill", skills_dir=tmp_path)

    def test_unknown_ladder_step_rejected(self, tmp_path):
        """Unknown ladder step name is rejected."""
        self._write_bad_policy(tmp_path, {"ladder": ["overdue", "reminder", "unknown"]})
        with pytest.raises(ValueError, match="unknown ladder step"):
            load_sla_policy("bad-skill", skills_dir=tmp_path)

    def _write_bad_policy(self, tmp_path: Path, overrides: dict):
        """Write a minimal bad policy skill for testing."""
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()

        # Write SKILL.md with frontmatter
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: bad-skill
description: Test skill
metadata:
  bridge-policy: assets/policy.yaml
---
Test skill body.
"""
        )

        # Write a base policy.yaml with only the overridden fields changed
        assets_dir = skill_dir / "assets"
        assets_dir.mkdir()

        base_policy = {
            "sla": {
                "deadline": 3,
                "cadence": 2,
                "max_nudges": 2,
                "overdue_ticks": 1,
                "reminder_ticks": 2,
                "escalate_ticks": 3,
                "ladder": ["overdue", "reminder", "escalated"],
            }
        }
        base_policy["sla"].update(overrides)

        import yaml

        (assets_dir / "policy.yaml").write_text(yaml.dump(base_policy))


class TestFrontmatterPointer:
    """Test the bridge-policy frontmatter pointer is honored."""

    def test_custom_policy_path_honored(self, tmp_path):
        """A custom bridge-policy path loads successfully."""
        skill_dir = tmp_path / "custom-policy-skill"
        skill_dir.mkdir()

        # Write SKILL.md pointing to a differently-named file
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: custom-policy-skill
description: Test skill
metadata:
  bridge-policy: assets/sla.yaml
---
Test skill body.
"""
        )

        # Write the policy file at the custom path
        assets_dir = skill_dir / "assets"
        assets_dir.mkdir()
        import yaml

        (assets_dir / "sla.yaml").write_text(
            yaml.dump(
                {
                    "sla": {
                        "deadline": 3,
                        "cadence": 2,
                        "max_nudges": 2,
                        "overdue_ticks": 1,
                        "reminder_ticks": 2,
                        "escalate_ticks": 3,
                        "ladder": ["overdue", "reminder", "escalated"],
                    }
                }
            )
        )

        # Load should succeed
        p = load_sla_policy("custom-policy-skill", skills_dir=tmp_path)
        assert p.escalate_ticks == 3


class TestComposeIntegration:
    """Test VirtualClock + SlaPolicy composition (the P5-1 proof).

    This is the port-level shape of the CUJ-6 follow-up ladder — arm the schedule
    into a clock, advance, and observe overdue→reminder→escalated fire in order.
    P5-2 adds the re-arming state machine + real send.
    """

    def test_compose_schedule_into_virtualclock(self):
        """Arm SlaPolicy.schedule() into VirtualClock and fire in order."""
        p = load_sla_policy("address-proof")
        clock = VirtualClock()

        # Arm each ladder step as an alarm
        for tick, step in p.schedule():
            clock.schedule(step.value, tick, payload=step)

        # Verify all three are pending
        assert len(clock.pending()) == 3

        # advance_to(1) fires overdue
        fired = clock.advance_to(1)
        assert len(fired) == 1
        assert fired[0].payload == LifecycleStatus.overdue

        # advance_to(2) fires reminder
        fired = clock.advance_to(2)
        assert len(fired) == 1
        assert fired[0].payload == LifecycleStatus.reminder

        # advance_to(3) fires escalated
        fired = clock.advance_to(3)
        assert len(fired) == 1
        assert fired[0].payload == LifecycleStatus.escalated

        # No more alarms
        assert clock.pending() == []
