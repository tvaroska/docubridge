"""Agent Gym continuous learning ('Example of One'), 3-tier evaluation, skill compiler, and Programmable Trust routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from bridge.a2ui_inbox import ActionInboxStore
from bridge.agent_gym import (
    AgentGymEngine,
    TribalRuleCategory,
    TribalRuleStatus,
    build_a2ui_trust_readiness_surface,
)
from bridge.knowledge_catalog import CatalogRole, KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.user_interaction.auth import (
    AuthenticationRequiredError,
    resolve_compound_identity,
)
from bridge.user_interaction.consolidation_collab import (
    reset_consolidation_collab_state,
)
from bridge.user_interaction.demo_seed import seed_demo_scenarios
from bridge.user_interaction.schemas import (
    CompileSkillRequest,
    DialTrustPolicyRequest,
    DistillInboxAuditRequest,
    DistillQuarantineFixRequest,
    RecordShadowComparisonRequest,
    RollbackSkillRequest,
)
from contract.extraction import SourceAnchor


def register_gym_routes(
    router: APIRouter,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore,
    gym: AgentGymEngine,
    principal_roles: dict[str, CatalogRole] | None,
    require_auth: bool,
) -> None:
    """Register Agent Gym distillation, 3-tier evaluation, skill compilation, and trust dial routes."""

    @router.post("/v1/gym/signals/from-quarantine")
    def distill_from_quarantine_endpoint(
        request: Request,
        body: DistillQuarantineFixRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Distill a candidate Tribal Knowledge Rule from a single Zone 2 Review Quarantine correction."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot submit Agent Gym quarantine rules."
                )
            anchor = (
                SourceAnchor(
                    page_number=body.page_number,
                    bounding_box=body.bounding_box,
                )
                if body.page_number is not None
                else None
            )
            signal, rule = gym.distill_from_quarantine_correction(
                entity_id=body.entity_id,
                doc_id=body.doc_id,
                doctype=body.doctype,
                issuer=body.issuer,
                field_path=body.field_path,
                before_value=body.before_value,
                after_value=body.after_value,
                expert_memo=body.expert_memo,
                actor_principal=compound.user.subject,
                actor_role=compound.user.role,
                agent_spiffe_id=compound.agent_spiffe_id,
                source_anchor=anchor,
                category=body.category,
                distinct_entities=tuple(body.distinct_entities),
                citation_verified=body.citation_verified,
                review_dwell_ms=body.review_dwell_ms,
            )
            return {
                "signal": signal.model_dump(mode="json"),
                "candidate_rule": rule.model_dump(mode="json"),
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/signals/from-inbox/{item_id}")
    def distill_from_inbox_endpoint(
        request: Request,
        item_id: str,
        body: DistillInboxAuditRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Distill a candidate Tribal Knowledge Rule from a Zone 5 A2UI Inbox RFC 6902 patch audit."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot distill Agent Gym rules."
                )
            audits = inbox.list_audit_records(item_id)
            if not audits:
                raise HTTPException(
                    status_code=404,
                    detail=f"No InboxAuditRecord found for action item '{item_id}'.",
                )
            latest_audit = audits[-1]
            signal, rule = gym.distill_from_inbox_audit(
                latest_audit,
                doc_id=body.doc_id,
                doctype=body.doctype,
                issuer=body.issuer,
                field_path=body.field_path,
                category=body.category,
                distinct_entities=tuple(body.distinct_entities),
                citation_verified=body.citation_verified,
                review_dwell_ms=body.review_dwell_ms,
            )
            return {
                "signal": signal.model_dump(mode="json"),
                "candidate_rule": rule.model_dump(mode="json"),
            }
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/rules/{rule_id}/evaluate")
    def evaluate_gym_rule_endpoint(
        request: Request,
        rule_id: str,
        auto_promote: bool = True,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Run the 3-tier Agent Gym evaluation gate on a candidate rule and promote on zero regression."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot evaluate or promote Agent Gym rules."
                )
            report = gym.evaluate_candidate_rule(rule_id, auto_promote=auto_promote)
            rule = next(r for r in gym.list_rules() if r.rule_id == rule_id)
            return {
                "report_card": report.model_dump(mode="json"),
                "rule": rule.model_dump(mode="json"),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/compile-skill")
    def compile_gym_skill_endpoint(
        request: Request,
        body: CompileSkillRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Compile promoted Tribal Knowledge Rules into an immutable versioned SKILL.md revision via gemini-4-argon."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot compile Agent Gym skills."
                )
            return gym.compile_rules_to_skill(
                doctype=body.doctype,
                issuer_pattern=body.issuer_pattern,
            )
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/skills/{skill_name}/rollback")
    def rollback_gym_skill_endpoint(
        skill_name: str,
        request: Request,
        body: RollbackSkillRequest | None = None,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Roll back a doctype skill defaultRevision pointer to its parent or target revision."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            if compound.user.role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{compound.user.role}' cannot roll back SkillRevisions."
                )
            req = body or RollbackSkillRequest()
            rev = gym.rollback_skill_revision(
                skill_name,
                target_revision_id=req.target_revision_id,
                reason=req.reason,
            )
            return rev.model_dump(mode="json")
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/gym/skills/{skill_name}/revisions")
    def list_gym_skill_revisions_endpoint(skill_name: str) -> dict[str, Any]:
        """List the active defaultRevision and full immutable revision history for a skill."""
        active = gym.get_active_skill_revision(skill_name)
        revs = gym.list_skill_revisions(skill_name)
        return {
            "skill_name": skill_name,
            "active_revision": active.model_dump(mode="json"),
            "revisions": [r.model_dump(mode="json") for r in revs]
            if revs
            else [active.model_dump(mode="json")],
        }

    @router.get("/v1/gym/rules")
    def list_gym_rules_endpoint(
        category: TribalRuleCategory | None = None,
        status: TribalRuleStatus | None = None,
        issuer: str | None = None,
        doctype: str | None = None,
    ) -> list[dict[str, Any]]:
        """List Tribal Knowledge Rules filtered by category, status, issuer, or doctype."""
        return [
            r.model_dump(mode="json")
            for r in gym.list_rules(
                category=category,
                status=status,
                issuer=issuer,
                doctype=doctype,
            )
        ]

    @router.get("/v1/gym/extraction-hints")
    def get_gym_extraction_hints_endpoint(
        issuer: str,
        doctype: str = "quote",
    ) -> list[dict[str, Any]]:
        """Return promoted Stage 1 extraction hint rules for a given issuer and doctype."""
        return [
            r.model_dump(mode="json")
            for r in gym.get_extraction_hints(issuer=issuer, doctype=doctype)
        ]

    @router.get("/v1/gym/export/docai-dataset")
    def export_docai_uptraining_dataset_endpoint(
        doctype: str | None = None,
        verified_only: bool = True,
    ) -> dict[str, Any]:
        """Export verified SME feedback signals and SourceAnchor bounding polygons in Document AI ImportDocuments format."""
        return gym.export_docai_uptraining_dataset(
            doctype=doctype,
            verified_only=verified_only,
        )

    @router.get("/v1/gym/trust-policies")
    def list_gym_trust_policies_endpoint() -> list[dict[str, Any]]:
        """List all Programmable Trust slice policies with readiness status and A2UI v0.9 controls."""
        results: list[dict[str, Any]] = []
        for p in gym.list_trust_policies():
            dumped = p.model_dump(mode="json")
            dumped["accuracy_rate"] = p.accuracy_rate
            dumped["ready_for_promotion"] = p.ready_for_promotion
            dumped["recommended_next_level"] = p.recommended_next_level.value
            dumped["a2ui_messages"] = build_a2ui_trust_readiness_surface(p)
            results.append(dumped)
        return results

    @router.post("/v1/gym/trust-policies/{slice_id}/shadow")
    def record_shadow_comparison_endpoint(
        slice_id: str,
        body: RecordShadowComparisonRequest,
    ) -> dict[str, Any]:
        """Record a Shadow Mode AI vs. human comparison and auto-demote autonomy on drift."""
        record, policy = gym.record_shadow_comparison(
            slice_id=slice_id,
            entity_id=body.entity_id,
            doc_id=body.doc_id,
            issuer=body.issuer,
            doctype=body.doctype,
            field_path=body.field_path,
            ai_value=body.ai_value,
            human_value=body.human_value,
            risk_level=body.risk_level,
        )
        return {
            "comparison": record.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "ready_for_promotion": policy.ready_for_promotion,
            "a2ui_messages": build_a2ui_trust_readiness_surface(policy),
        }

    @router.post("/v1/gym/trust-policies/{slice_id}/dial")
    def dial_trust_policy_endpoint(
        request: Request,
        slice_id: str,
        body: DialTrustPolicyRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Promote or dial back a Programmable Trust slice autonomy level."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role,
                default_role="broker",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            policy = gym.dial_trust_policy(
                slice_id,
                target_level=body.target_level,
                actor_principal=compound.user.subject,
                actor_role=compound.user.role,
                reason=body.reason,
                agent_spiffe_id=compound.agent_spiffe_id,
                force=body.force,
            )
            return {
                "policy": policy.model_dump(mode="json"),
                "ready_for_promotion": policy.ready_for_promotion,
                "a2ui_messages": build_a2ui_trust_readiness_surface(policy),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/v1/gym/trust-policies/reset")
    def reset_gym_trust_policies_endpoint(
        clear_rules: bool = False,
        reseed_demo: bool = False,
    ) -> list[dict[str, Any]]:
        """Reset Programmable Trust policies (and optionally clear rules or reseed demo scenarios)."""
        reset_consolidation_collab_state()
        if clear_rules:
            from bridge.refinery import schema_refinery_from_env

            schema_refinery_from_env(knowledge_catalog=catalog).reset()
        if reseed_demo:
            seed_demo_scenarios(
                operational_store=store,
                knowledge_catalog=catalog,
                agent_gym=gym,
            )
        results: list[dict[str, Any]] = []
        for p in gym.reset_trust_policies(clear_rules=clear_rules):
            dumped = p.model_dump(mode="json")
            dumped["accuracy_rate"] = p.accuracy_rate
            dumped["ready_for_promotion"] = p.ready_for_promotion
            dumped["recommended_next_level"] = p.recommended_next_level.value
            dumped["a2ui_messages"] = build_a2ui_trust_readiness_surface(p)
            results.append(dumped)
        return results
