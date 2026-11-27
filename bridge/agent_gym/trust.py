"""Programmable Trust slice management, shadow comparisons & autonomy dials."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from bridge.a2ui_inbox import RiskTier
from bridge.agent_gym.models import (
    DEFAULT_TRUST_POLICIES,
    ProgrammableTrustPolicy,
    ShadowComparisonRecord,
    SkillRevisionRecord,
    TrustAutonomyLevel,
    _strip_legal_suffix,
)
from bridge.knowledge_catalog import CatalogRole
from bridge.telemetry import trace_step


class TrustPolicyMixin:
    """Mixin providing Programmable Trust slice dials, shadow mode drift tracking, and resets."""

    def _save_trust_policy(self, policy: ProgrammableTrustPolicy) -> None:
        """Persist a ProgrammableTrustPolicy (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        """Look up a ProgrammableTrustPolicy (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _list_all_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        """Return all ProgrammableTrustPolicy entries (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _append_shadow_record(self, record: ShadowComparisonRecord) -> None:
        """Append a ShadowComparisonRecord (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _compute_oversight_rsr(
        self, *, citation_verified: bool, review_dwell_ms: int
    ) -> float:
        """Compute Reviewer Sufficiency Rate (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _clear_rules_and_signals(self) -> None:
        """Clear accumulated demo signals, candidate/promoted rules, and report cards."""

    def get_active_skill_revision(self, skill_name: str) -> SkillRevisionRecord:
        """Return the active SkillRevision for ``skill_name`` (implemented by SkillCompilerMixin)."""
        raise NotImplementedError

    def list_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        """List all registered Programmable Trust slice policies."""
        return self._list_all_trust_policies()

    def get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        """Look up a single Programmable Trust policy by slice_id."""
        return self._get_trust_policy(slice_id)

    def upsert_trust_policy(
        self, policy: ProgrammableTrustPolicy
    ) -> ProgrammableTrustPolicy:
        """Insert or update a Programmable Trust slice policy."""
        self._save_trust_policy(policy)
        return policy

    def record_shadow_comparison(  # noqa: PLR0913 - keyword-only shadow comparison fields
        self,
        *,
        slice_id: str,
        entity_id: str,
        doc_id: str,
        issuer: str,
        doctype: str,
        field_path: str,
        ai_value: str,
        human_value: str,
        risk_level: RiskTier = "MEDIUM",
        citation_verified: bool = False,
        review_dwell_ms: int = 0,
    ) -> tuple[ShadowComparisonRecord, ProgrammableTrustPolicy]:
        """Compare AI output vs. human ground truth; track consecutive matches or auto-demote on drift."""
        oversight_rsr = self._compute_oversight_rsr(
            citation_verified=citation_verified,
            review_dwell_ms=review_dwell_ms,
        )
        with trace_step(
            "docubridge.agent_gym.shadow_comparison",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.slice_id": slice_id,
                "docubridge.entity_id": entity_id,
                "docubridge.doc_id": doc_id,
                "docubridge.issuer": issuer,
                "docubridge.doctype": doctype,
                "docubridge.field_path": field_path,
                "docubridge.citation_verified": citation_verified,
                "docubridge.review_dwell_ms": review_dwell_ms,
                "docubridge.oversight_rsr_score": oversight_rsr,
            },
        ) as span:
            policy = self._get_trust_policy(slice_id)
            if policy is None:
                policy = ProgrammableTrustPolicy(
                    slice_id=slice_id,
                    issuer_pattern=f"{_strip_legal_suffix(issuer)}*",
                    doctype=doctype,
                    risk_level=risk_level,
                    autonomy_level=TrustAutonomyLevel.SHADOW_MODE,
                )

            exact_match = ai_value.strip() == human_value.strip()
            policy.total_evaluated += 1
            drift_reason: str | None = None

            if exact_match:
                policy.total_exact_matches += 1
                if citation_verified and oversight_rsr >= 0.80:
                    policy.consecutive_matches += 1
                    policy.last_updated_reason = (
                        f"Shadow/Review exact match #{policy.consecutive_matches} "
                        f"on '{field_path}' (RSR={oversight_rsr:.2f})."
                    )
                else:
                    policy.last_updated_reason = (
                        f"Unverified/rubber-stamp match on '{field_path}' "
                        f"(RSR={oversight_rsr:.2f} < 0.80); consecutive_matches held at "
                        f"{policy.consecutive_matches}."
                    )
            else:
                policy.consecutive_matches = 0
                policy.drift_incidents += 1
                drift_reason = (
                    f"Format/value drift detected on '{field_path}': "
                    f"AI='{ai_value}' vs Human='{human_value}'."
                )
                if policy.autonomy_level == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE:
                    policy.autonomy_level = TrustAutonomyLevel.GUIDED_REVIEW
                    policy.last_updated_reason = f"Auto-demoted to Guided Review due to carrier drift ({drift_reason})"
                else:
                    policy.last_updated_reason = drift_reason

            policy.updated_at = datetime.now(UTC)
            self._save_trust_policy(policy)

            active_rev = self.get_active_skill_revision(doctype)
            record = ShadowComparisonRecord(
                comparison_id=f"shd-{uuid.uuid4().hex[:8]}",
                slice_id=slice_id,
                entity_id=entity_id,
                doc_id=doc_id,
                issuer=issuer,
                doctype=doctype,
                field_path=field_path,
                ai_value=ai_value,
                human_value=human_value,
                exact_match=exact_match,
                drift_reason=drift_reason,
                active_skill_revision_id=active_rev.revision_id,
                citation_verified=citation_verified,
                oversight_rsr_score=oversight_rsr,
            )
            self._append_shadow_record(record)
            span.set_attribute("docubridge.exact_match", exact_match)
            span.set_attribute(
                "docubridge.consecutive_matches", policy.consecutive_matches
            )
            span.set_attribute("docubridge.autonomy_level", policy.autonomy_level.value)
            span.set_attribute("docubridge.skill.revision_id", active_rev.revision_id)
            return record, policy

    def dial_trust_policy(  # noqa: PLR0913 - keyword-only trust policy audit inputs
        self,
        slice_id: str,
        *,
        target_level: TrustAutonomyLevel,
        actor_principal: str,
        actor_role: CatalogRole,
        reason: str,
        agent_spiffe_id: str | None = None,
        force: bool = False,
    ) -> ProgrammableTrustPolicy:
        """Promote or dial back a slice's autonomy level (enforcing internal lead role)."""
        with trace_step(
            "docubridge.agent_gym.dial_trust_policy",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.slice_id": slice_id,
                "docubridge.target_level": target_level.value,
                "docubridge.actor_principal": actor_principal,
                "docubridge.actor_role": actor_role,
                "docubridge.agent_spiffe_id": agent_spiffe_id,
            },
        ) as span:
            if actor_role in ("client", "carrier"):
                raise PermissionError(
                    f"External role '{actor_role}' is not permitted to modify Programmable Trust policies."
                )
            policy = self._get_trust_policy(slice_id)
            if policy is None:
                raise KeyError(f"ProgrammableTrustPolicy '{slice_id}' not found.")

            if (
                target_level == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE
                and policy.risk_level == "HIGH"
                and policy.consecutive_matches < policy.required_matches_for_promotion
            ):
                raise ValueError(
                    f"Cannot force-promote HIGH-risk slice '{slice_id}' to trusted_auto_approve: "
                    f"only {policy.consecutive_matches}/{policy.required_matches_for_promotion} "
                    "consecutive matches recorded (force override is disabled on HIGH-risk slices)."
                )
            if (
                target_level == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE
                and not force
                and policy.consecutive_matches < policy.required_matches_for_promotion
            ):
                raise ValueError(
                    f"Cannot promote '{slice_id}' to trusted_auto_approve: only "
                    f"{policy.consecutive_matches}/{policy.required_matches_for_promotion} "
                    "consecutive matches recorded."
                )

            policy.autonomy_level = target_level
            policy.last_updated_by = actor_principal
            policy.last_updated_reason = reason
            policy.agent_spiffe_id = agent_spiffe_id
            policy.updated_at = datetime.now(UTC)
            self._save_trust_policy(policy)
            span.set_attribute("docubridge.autonomy_level", policy.autonomy_level.value)
            return policy

    def reset_trust_policies(
        self,
        *,
        policies: tuple[ProgrammableTrustPolicy, ...] = DEFAULT_TRUST_POLICIES,
        clear_rules: bool = False,
    ) -> list[ProgrammableTrustPolicy]:
        """Reset default Programmable Trust policies to baseline state (for interactive demo runs)."""
        with trace_step(
            "docubridge.agent_gym.reset_trust_policies",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.policy_count": len(policies),
                "docubridge.clear_rules": clear_rules,
            },
        ):
            if clear_rules:
                self._clear_rules_and_signals()
            reset_list: list[ProgrammableTrustPolicy] = []
            for p in policies:
                copied = p.model_copy(deep=True)
                self._save_trust_policy(copied)
                reset_list.append(copied)
            return reset_list
