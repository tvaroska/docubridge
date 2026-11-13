"""Skill markdown rendering and 1-click DRAFT -> ACTIVE promotion helpers for Agent Gym."""

from __future__ import annotations

import json
from typing import Any

from bridge.agent_gym.models import (
    REASONING_MODEL,
    WORKHORSE_MODEL,
    SkillRevisionRecord,
)
from bridge.telemetry import trace_step


def resolve_compiled_schema_json(
    doctype: str,
    *,
    semver: str,
    sorted_rule_ids: tuple[str, ...],
) -> str:
    """Return canonical installed `assets/schema.json` if present, else a fallback schema."""
    try:
        from bridge.ports import Port, get_adapter, resolve_mode

        registry = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
        return registry.read_asset(doctype, "assets/schema.json")
    except (KeyError, ValueError, OSError):
        pass
    schema_payload = {
        "doctype": doctype,
        "version": semver,
        "compiled_by": REASONING_MODEL,
        "runtime_workhorse": WORKHORSE_MODEL,
        "fields": {
            "issuer": {
                "type": "string",
                "description": "Canonical issuer or lender name",
            },
            "effective_date": {
                "type": "string",
                "description": "ISO-8601 effective date",
            },
            "origination_fee": {
                "type": "string",
                "description": "Origination fee (Schedule B / Page 3 Section A)",
            },
            "loan_amount": {
                "type": "string",
                "description": "Total principal loan amount",
            },
        },
        "compiled_rule_ids": list(sorted_rule_ids),
    }
    return json.dumps(schema_payload, indent=2, sort_keys=True)


def render_compiled_skill_markdown(
    *,
    doctype: str,
    semver: str,
    skill_version: str,
    revision_id: str,
    parent_revision_id: str,
    sorted_rule_ids: tuple[str, ...],
    rule_bullets: list[str],
    policy_ceiling_usd: str,
    skill_urn: str,
    target_state: str,
) -> str:
    """Render the governed `SKILL.md` document with explicit policy guardrails (`Decision 20`)."""
    rules_block = "\n".join(rule_bullets)
    return (
        f"---\n"
        f"name: {doctype}\n"
        f"description: Distilled extraction and consolidation skill for {doctype} "
        f"(compiled by {REASONING_MODEL} for {WORKHORSE_MODEL} runtime execution).\n"
        f"license: Apache-2.0\n"
        f"metadata:\n"
        f"  bridge-kind: doctype\n"
        f'  bridge-tags: "doctype {doctype} harness-evolve"\n'
        f"  bridge-extraction-engine: gemini\n"
        f"  bridge-schema: assets/schema.json\n"
        f'  version: "{semver}"\n'
        f'  revision-id: "{revision_id}"\n'
        f'  parent-revision-id: "{parent_revision_id}"\n'
        f'  compiled-by-model: "{REASONING_MODEL}"\n'
        f'  runtime-workhorse-model: "{WORKHORSE_MODEL}"\n'
        f'  compiled-rule-ids: "{",".join(sorted_rule_ids)}"\n'
        f"---\n\n"
        f"# {doctype.upper()} Extraction & Consolidation Skill ({skill_version} · `{revision_id}`)\n\n"
        f"## Distilled Domain Precedence Rules (Compiled via Harness Evolve)\n"
        f"{rules_block}\n\n"
        f"## Policy Guardrails & Auto-Approval Boundaries\n"
        f"- **Amount Ceiling:** `<= ${policy_ceiling_usd}` USD (any wire above ceiling escalates to human review)\n"
        f"- **Exact Match Condition:** Wire memo must name the exact facility number + `'PAID IN FULL'` / `'FINAL PAYOFF'` and match balance to the cent\n"
        f"- **Out-of-Policy Fallback:** Route all non-matching or over-ceiling items to `/overwatch` human triage\n"
        f"- **Continuous Verification:** Sampled Step-2 `AI.GENERATE_TABLE` audit over skill-approved outcomes\n\n"
        f"## Runtime Execution Contract & Lineage\n"
        f"- **Skill URN:** `{skill_urn}`\n"
        f"- **Active Revision:** `{revision_id}` (parent: `{parent_revision_id}`, state: `{target_state}`)\n"
        f"- **Workhorse Model:** `{WORKHORSE_MODEL}` (`thinking_budget=0`)\n"
        f"- **Deterministic Gate:** `Rule 0` (`pin_gate_args` + `threshold_corridor` FA=0)\n"
        f"- **Prompt Compaction:** Daytime SQL rules folded into canonical schema instructions (0 prompt bloat)\n"
    )


def execute_skill_promotion(
    engine: Any,
    skill_name: str,
    revision_id: str,
    *,
    install_to_port: bool = True,
) -> SkillRevisionRecord:
    """Promote a compiled `DRAFT` `SkillRevision` to `ACTIVE` (`defaultRevision`)."""
    with trace_step(
        "docubridge.agent_gym.promote_skill_revision",
        {
            "docubridge.zone": "6_knowledge_catalog_agent_gym",
            "docubridge.skill_name": skill_name,
            "skill_id": skill_name,
            "docubridge.revision_id": revision_id,
            "hitl.review": "true",
        },
    ) as span:
        revs = engine.list_skill_revisions(skill_name)
        promoted: SkillRevisionRecord | None = None
        for rev in revs:
            if rev.revision_id == revision_id:
                updated_md = rev.skill_markdown.replace(
                    "state: `DRAFT`", "state: `ACTIVE`"
                )
                promoted = rev.model_copy(
                    update={
                        "is_default_revision": True,
                        "target_state": "ACTIVE",
                        "skill_markdown": updated_md,
                    }
                )
                engine._save_skill_revision(promoted)
            elif rev.is_default_revision:
                engine._save_skill_revision(
                    rev.model_copy(update={"is_default_revision": False})
                )
        if promoted is None:
            raise KeyError(
                f"Unknown SkillRevision {revision_id!r} for skill {skill_name!r}"
            )
        if install_to_port:
            from bridge.ports import Port, get_adapter, resolve_mode

            registry = get_adapter(
                Port.skill_registry, resolve_mode(Port.skill_registry)
            )
            assets: dict[str, str] = {}
            if promoted.assets_schema_json:
                assets["assets/schema.json"] = promoted.assets_schema_json
            registry.install_skill(skill_name, promoted.skill_markdown, assets)
        span.set_attribute("skill_version", promoted.version)
        span.set_attribute("docubridge.active_revision_id", promoted.revision_id)
        return promoted
