"""Benefits skills load + advertise correctly (S1-benefits-1).

Proves the two authored skills (process ``benefits-procurement`` + doctype
``benefits-quote``) load, that the Agent Card regenerates to advertise the process
skill with a ``doctype:benefits-quote`` tag (and NOT a standalone ``benefits-quote``
skill — doctype = label space), and that the process skill's policy + assets parse.
Also guards that the three address skills still load (no regression from the second
process skill).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from bridge.edge import build_agent_card
from bridge.edge.skills import loaded_skills
from bridge.policy import load_disposition_policy
from bridge.sla import load_sla_policy

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _by_name():
    return {s.name: s for s in loaded_skills()}


class TestSkillsLoad:
    def test_process_skill_loads(self):
        p = _by_name()["benefits-procurement"]
        assert p.kind == "process"
        assert p.candidate_doctypes == ("benefits-quote",)

    def test_doctype_skill_loads(self):
        q = _by_name()["benefits-quote"]
        assert q.kind == "doctype"

    def test_address_skills_still_load(self):
        names = set(_by_name())
        assert {"address-proof", "gov-id", "utility-bill"} <= names


class TestAgentCard:
    def test_process_skill_advertised_with_doctype_tag(self):
        card = build_agent_card("http://localhost:8000/")
        skill = next(s for s in card.skills if s.id == "benefits-procurement")
        assert "doctype:benefits-quote" in skill.tags

    def test_doctype_not_advertised_standalone(self):
        card = build_agent_card("http://localhost:8000/")
        assert not any(s.id == "benefits-quote" for s in card.skills)


class TestPolicy:
    def test_sla_policy_parses(self):
        p = load_sla_policy("benefits-procurement")
        assert p.max_nudges == 2
        schedule = p.schedule()
        ticks = [tick for tick, _ in schedule]
        assert ticks == sorted(ticks)  # monotone

    def test_disposition_policy_parses(self):
        d = load_disposition_policy("benefits-procurement")
        assert d.auto_approve_at > d.resubmit_below
        assert d.max_resubmissions >= 0

    def test_negotiate_revise_cap_present(self):
        doc = yaml.safe_load(
            (SKILLS_DIR / "benefits-procurement/assets/policy.yaml").read_text()
        )
        assert doc["negotiate"]["revise_cap"] >= 1


class TestAssets:
    def test_schema_is_valid_json_with_required_fields(self):
        schema = json.loads(
            (SKILLS_DIR / "benefits-quote/assets/schema.json").read_text()
        )
        assert schema["properties"]["doctype"]["enum"] == ["benefits-quote"]
        key_fields = schema["properties"]["key_fields"]["required"]
        assert {
            "premium",
            "deductible",
            "coverage_limits",
            "exclusions",
            "effective_date",
            "expiry_date",
        } <= set(key_fields)

    def test_validation_yaml_valid(self):
        doc = yaml.safe_load(
            (SKILLS_DIR / "benefits-quote/assets/validation.yaml").read_text()
        )
        assert doc["rules"]
        assert doc["signals"]["completeness"] == "agent"

    def test_standard_yaml_valid_with_rules(self):
        doc = yaml.safe_load(
            (SKILLS_DIR / "benefits-procurement/assets/standard.yaml").read_text()
        )
        rule_ids = {r["id"] for r in doc["rules"]}
        assert rule_ids  # at least one declared rule
        assert doc["overridable"] is True
