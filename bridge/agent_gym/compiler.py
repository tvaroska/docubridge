"""Harness Evolve Skill Compiler, SkillRevision lineage & Document AI dataset export mixin."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any, Literal

from bridge.a2ui_inbox import RiskTier
from bridge.agent_gym.models import (
    DEFAULT_TRUST_POLICIES,
    REASONING_MODEL,
    WORKHORSE_MODEL,
    ExpertFeedbackSignal,
    ProcessSkillLineageRecord,
    ProgrammableTrustPolicy,
    ShadowComparisonRecord,
    SkillRevisionRecord,
    TribalKnowledgeRule,
    TribalRuleStatus,
    TrustAutonomyLevel,
    _gcp_project_id,
    _skills_bucket,
    _strip_legal_suffix,
    _vault_bucket,
)
from bridge.knowledge_catalog import CatalogRole
from bridge.telemetry import trace_step


class SkillCompilerMixin:
    """Mixin providing Harness Evolve skill compilation, SkillRevision rollback,
    process-instance skill lineage, and Document AI uptraining dataset export."""

    def _invoke_reasoning_model_if_enabled(
        self,
        *,
        prompt: str,
        default_in_tokens: int,
        default_out_tokens: int,
    ) -> tuple[str | None, int, int]:
        """Invoke the teacher/reasoning model when live Gemini calls are enabled (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _list_all_rules(self) -> list[TribalKnowledgeRule]:
        """Return all persisted TribalKnowledgeRule records (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _list_all_signals(self) -> list[ExpertFeedbackSignal]:
        """Return all persisted ExpertFeedbackSignal records (implemented by InMemory and Sql stores)."""
        return []

    def _save_skill_revision(self, revision: SkillRevisionRecord) -> None:
        """Persist an immutable SkillRevisionRecord (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _list_all_skill_revisions(self) -> list[SkillRevisionRecord]:
        """Return all persisted SkillRevisionRecord snapshots (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _append_process_skill_lineage(self, record: ProcessSkillLineageRecord) -> None:
        """Append a ProcessSkillLineageRecord (implemented by AgentGymEngine)."""
        raise NotImplementedError

    def _list_all_process_skill_lineage(self) -> list[ProcessSkillLineageRecord]:
        """Return all ProcessSkillLineageRecord entries (implemented by AgentGymEngine)."""
        raise NotImplementedError

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

    def compile_rules_to_skill(
        self,
        *,
        doctype: str = "closing-disclosure",
        issuer_pattern: str = "*",
        install_to_port: bool = False,
    ) -> dict[str, Any]:
        """Harness Evolve Nightly Compiler: fold daytime SQL tribal rules into a versioned
        `SKILL.md` package via `gemini-4-argon` and register an immutable `SkillRevision`
        in Gemini platform Agent Registry / Skill Registry so `gemini-3.8-flash` executes
        without prompt bloat."""
        _, in_tok, out_tok = self._invoke_reasoning_model_if_enabled(
            prompt=f"Compile promoted TribalKnowledgeRules for doctype='{doctype}' issuer='{issuer_pattern}' into SKILL.md.",
            default_in_tokens=2400,
            default_out_tokens=510,
        )
        with trace_step(
            "docubridge.agent_gym.compile_skill",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doctype": doctype,
                "docubridge.issuer_pattern": issuer_pattern,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "compile_skill",
                "gen_ai.agent.name": "harness_evolve_compiler",
                "gen_ai.request.model": REASONING_MODEL,
                "docubridge.finops.traffic_type": "agent_gym_eval",
                "gen_ai.usage.input_tokens": in_tok,
                "gen_ai.usage.output_tokens": out_tok,
            },
        ) as span:
            from bridge.edge.skills import validate_skill_upload

            all_rules = self._list_all_rules()
            matched = [
                r
                for r in all_rules
                if r.status
                in (
                    TribalRuleStatus.PROMOTED,
                    TribalRuleStatus.VERIFIED,
                    TribalRuleStatus.CANDIDATE,
                )
                and (r.doctype in ("*", doctype) or doctype == "*")
            ]
            if not matched and all_rules:
                matched = list(all_rules)

            verified_or_promoted = [
                r
                for r in matched
                if r.status in (TribalRuleStatus.PROMOTED, TribalRuleStatus.VERIFIED)
            ]
            target_state: Literal["DRAFT", "ACTIVE"] = (
                "ACTIVE" if (verified_or_promoted or not matched) else "DRAFT"
            )

            rule_bullets = [
                f"- **[{r.rule_id} · {r.category.value}]** ({r.issuer_pattern} / `{r.field_path}`): {r.instruction}"
                for r in matched
            ]
            if not rule_bullets:
                rule_bullets = [
                    f"- **[rule-default-precedence · extraction_hint]** ({issuer_pattern} / `origination_fee`): "
                    "Prioritize executed Schedule B / Page 3 Section A addendum tables over cover-page estimates."
                ]

            existing_revs = self.list_skill_revisions(doctype)
            current_default = next(
                (rev for rev in existing_revs if rev.is_default_revision),
                None,
            )
            parent_revision_id = (
                current_default.revision_id
                if current_default is not None
                else "rev-v1-0-baseline"
            )
            minor_num = len(existing_revs) + 1
            semver = f"1.{minor_num}.0"
            skill_version = f"v{semver}"
            skill_urn = f"urn:skill:projects-{_gcp_project_id()}:locations:global:private-{doctype}"
            sorted_rule_ids = tuple(sorted(r.rule_id for r in matched))

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
            schema_json_str = json.dumps(schema_payload, indent=2, sort_keys=True)
            digest_input = f"{doctype}|{semver}|{','.join(sorted_rule_ids)}|{'\n'.join(rule_bullets)}".encode()
            short_hash = hashlib.sha256(digest_input).hexdigest()[:8]
            revision_id = f"rev-v1-{minor_num}-{short_hash}"
            resource_name = f"projects/{_gcp_project_id()}/locations/global/skills/private-{doctype}/revisions/{revision_id}"
            gcs_source_uri = (
                f"gs://{_skills_bucket()}/revisions/{doctype}/{revision_id}.zip"
            )
            skill_uri = f"gs://{_skills_bucket()}/{doctype}/SKILL.md"

            skill_markdown = (
                f"---\n"
                f"name: {doctype}\n"
                f"description: Distilled extraction and consolidation skill for {doctype} "
                f"(compiled by {REASONING_MODEL} for {WORKHORSE_MODEL} runtime execution).\n"
                f"license: Proprietary. LICENSE.txt has complete terms\n"
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
                + "\n".join(rule_bullets)
                + "\n\n## Runtime Execution Contract & Lineage\n"
                f"- **Skill URN:** `{skill_urn}`\n"
                f"- **Active Revision:** `{revision_id}` (parent: `{parent_revision_id}`, state: `{target_state}`)\n"
                f"- **Workhorse Model:** `{WORKHORSE_MODEL}` (`thinking_budget=0`)\n"
                "- **Deterministic Gate:** `Rule 0` (`pin_gate_args` + `threshold_corridor` FA=0)\n"
                "- **Prompt Compaction:** Daytime SQL rules folded into canonical schema instructions (0 prompt bloat)\n"
            )

            # Validate against ADK + DocuBridge SkillMeta validator before creating revision
            validate_skill_upload(
                doctype,
                skill_markdown,
                {"assets/schema.json": schema_json_str},
            )

            # Build deterministic in-memory ZIP package for Gemini platform Skill Registry
            zip_buf = io.BytesIO()
            lineage_manifest = json.dumps(
                {
                    "skill_name": doctype,
                    "skill_urn": skill_urn,
                    "revision_id": revision_id,
                    "parent_revision_id": parent_revision_id,
                    "version": semver,
                    "target_state": target_state,
                    "compiled_by_model": REASONING_MODEL,
                    "workhorse_model": WORKHORSE_MODEL,
                    "compiled_rule_ids": list(sorted_rule_ids),
                },
                indent=2,
                sort_keys=True,
            )
            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("SKILL.md", skill_markdown)
                zf.writestr("assets/schema.json", schema_json_str)
                zf.writestr("assets/lineage.json", lineage_manifest)
            zip_bytes = zip_buf.getvalue()
            archive_sha256 = hashlib.sha256(zip_bytes).hexdigest()

            is_default = target_state == "ACTIVE"
            if is_default:
                for prev in existing_revs:
                    if prev.is_default_revision:
                        prev_copy = prev.model_copy(
                            update={"is_default_revision": False}
                        )
                        self._save_skill_revision(prev_copy)

            rev_record = SkillRevisionRecord(
                revision_id=revision_id,
                skill_name=doctype,
                skill_urn=skill_urn,
                resource_name=resource_name,
                version=semver,
                parent_revision_id=parent_revision_id,
                target_state=target_state,
                is_default_revision=is_default,
                compiled_by_model=REASONING_MODEL,
                workhorse_model=WORKHORSE_MODEL,
                compiled_rule_ids=sorted_rule_ids,
                compiled_rules_count=len(rule_bullets),
                gcs_source_uri=gcs_source_uri,
                archive_sha256=archive_sha256,
                archive_size_bytes=len(zip_bytes),
                skill_markdown=skill_markdown,
                assets_schema_json=schema_json_str,
            )
            self._save_skill_revision(rev_record)

            if install_to_port:
                from bridge.ports import Port, get_adapter, resolve_mode

                registry = get_adapter(
                    Port.skill_registry, resolve_mode(Port.skill_registry)
                )
                registry.install_skill(
                    doctype,
                    skill_markdown,
                    {
                        "assets/schema.json": schema_json_str,
                        "assets/lineage.json": lineage_manifest,
                    },
                )

            span.set_attribute("docubridge.compiled_rule_count", len(rule_bullets))
            span.set_attribute("docubridge.skill_version", skill_version)
            span.set_attribute("docubridge.skill_revision_id", revision_id)
            span.set_attribute("docubridge.skill_urn", skill_urn)
            span.set_attribute("docubridge.skill_target_state", target_state)
            return {
                "doctype": doctype,
                "skill_version": skill_version,
                "version": semver,
                "semver": semver,
                "revision_id": revision_id,
                "parent_revision_id": parent_revision_id,
                "skill_urn": skill_urn,
                "resource_name": resource_name,
                "target_state": target_state,
                "is_default_revision": is_default,
                "skill_uri": skill_uri,
                "gcs_source_uri": gcs_source_uri,
                "archive_sha256": archive_sha256,
                "archive_size_bytes": len(zip_bytes),
                "teacher_model": REASONING_MODEL,
                "workhorse_model": WORKHORSE_MODEL,
                "compiled_rule_ids": list(sorted_rule_ids),
                "compiled_rules_count": len(rule_bullets),
                "skill_markdown": skill_markdown,
            }

    def list_skill_revisions(
        self, skill_name: str | None = None
    ) -> list[SkillRevisionRecord]:
        """List immutable SkillRevision snapshots (newest last), optionally filtered by skill_name."""
        revs = self._list_all_skill_revisions()
        if skill_name is not None:
            revs = [r for r in revs if r.skill_name == skill_name]
        return sorted(revs, key=lambda r: (r.created_at, r.revision_id))

    def get_active_skill_revision(self, skill_name: str) -> SkillRevisionRecord:
        """Return the active (`defaultRevision`) SkillRevision for `skill_name`,
        or synthesize the baseline `v1.0.0` (`rev-v1-0-baseline`) record."""
        revs = self.list_skill_revisions(skill_name)
        for rev in reversed(revs):
            if rev.is_default_revision and rev.target_state == "ACTIVE":
                return rev
        skill_urn = f"urn:skill:projects-{_gcp_project_id()}:locations:global:private-{skill_name}"
        return SkillRevisionRecord(
            revision_id="rev-v1-0-baseline",
            skill_name=skill_name,
            skill_urn=skill_urn,
            resource_name=(
                f"projects/{_gcp_project_id()}/locations/global/skills/private-{skill_name}/revisions/rev-v1-0-baseline"
            ),
            version="1.0.0",
            parent_revision_id=None,
            target_state="ACTIVE",
            is_default_revision=True,
            compiled_by_model="system:baseline",
            workhorse_model=WORKHORSE_MODEL,
            compiled_rule_ids=(),
            compiled_rules_count=0,
            gcs_source_uri=f"gs://{_skills_bucket()}/revisions/{skill_name}/rev-v1-0-baseline.zip",
            archive_sha256=hashlib.sha256(skill_name.encode()).hexdigest(),
            archive_size_bytes=1024,
            skill_markdown=f"---\nname: {skill_name}\ndescription: Baseline v1.0.0 skill\n---\n",
        )

    def rollback_skill_revision(
        self,
        skill_name: str,
        *,
        target_revision_id: str | None = None,
        reason: str = "Operator or drift-triggered rollback to parent SkillRevision.",
    ) -> SkillRevisionRecord:
        """Repoint `defaultRevision` of `skill_name` back to `target_revision_id` (or `parent_revision_id`
        of the current default revision) without deleting immutable revision history."""
        with trace_step(
            "docubridge.agent_gym.rollback_skill_revision",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.skill_name": skill_name,
                "docubridge.target_revision_id": target_revision_id or "parent",
                "docubridge.reason": reason,
            },
        ) as span:
            revs = self.list_skill_revisions(skill_name)
            current_default = next(
                (r for r in reversed(revs) if r.is_default_revision), None
            )
            resolved_target_id = target_revision_id or (
                current_default.parent_revision_id
                if current_default
                else "rev-v1-0-baseline"
            )
            if not resolved_target_id:
                resolved_target_id = "rev-v1-0-baseline"

            target_rev: SkillRevisionRecord | None = None
            for r in revs:
                if r.revision_id == resolved_target_id:
                    updated = r.model_copy(
                        update={"is_default_revision": True, "target_state": "ACTIVE"}
                    )
                    self._save_skill_revision(updated)
                    target_rev = updated
                elif r.is_default_revision:
                    demoted = r.model_copy(
                        update={
                            "is_default_revision": False,
                            "target_state": "DEPRECATED",
                        }
                    )
                    self._save_skill_revision(demoted)

            if target_rev is None:
                # Rolling back to baseline v1.0.0 when only 1 compiled revision existed
                baseline = self.get_active_skill_revision(skill_name).model_copy(
                    update={
                        "revision_id": "rev-v1-0-baseline",
                        "version": "1.0.0",
                        "parent_revision_id": None,
                        "is_default_revision": True,
                        "target_state": "ACTIVE",
                        "compiled_rule_ids": (),
                        "compiled_rules_count": 0,
                    }
                )
                self._save_skill_revision(baseline)
                target_rev = baseline

            span.set_attribute("docubridge.active_revision_id", target_rev.revision_id)
            span.set_attribute("docubridge.active_version", target_rev.version)
            return target_rev

    def record_process_skill_lineage(
        self,
        *,
        context_id: str,
        entity_id: str,
        doc_id: str,
        doctype: str,
        document_version: int = 1,
        process_skill_name: str = "commercial-loan-closing",
        applied_rule_ids: tuple[str, ...] = (),
        recorded_at: datetime | None = None,
        effective_from: datetime | None = None,
    ) -> ProcessSkillLineageRecord:
        """Record which exact process skill revision and doctype skill revision executed
        on `(context_id, entity_id, doc_id, document_version)`."""
        rec_ts = recorded_at or datetime.now(UTC)
        eff_ts = effective_from or rec_ts
        proc_rev = self.get_active_skill_revision(process_skill_name)
        doc_rev = self.get_active_skill_revision(doctype)
        combined_rules = tuple(
            dict.fromkeys([*doc_rev.compiled_rule_ids, *applied_rule_ids])
        )
        record = ProcessSkillLineageRecord(
            lineage_id=f"psl-{uuid.uuid4().hex[:8]}",
            context_id=context_id,
            entity_id=entity_id,
            doc_id=doc_id,
            document_version=document_version,
            process_skill_name=process_skill_name,
            process_skill_urn=proc_rev.skill_urn,
            process_skill_version=proc_rev.version,
            process_skill_revision_id=proc_rev.revision_id,
            doctype_skill_name=doctype,
            doctype_skill_urn=doc_rev.skill_urn,
            doctype_skill_version=doc_rev.version,
            doctype_skill_revision_id=doc_rev.revision_id,
            compiled_rule_ids=combined_rules,
            workhorse_model=WORKHORSE_MODEL,
            recorded_at=rec_ts,
            effective_from=eff_ts,
        )
        self._append_process_skill_lineage(record)
        return record

    def list_process_skill_lineage(
        self,
        *,
        context_id: str | None = None,
        entity_id: str | None = None,
    ) -> list[ProcessSkillLineageRecord]:
        """Return chronological process-instance skill lineage records."""
        records = self._list_all_process_skill_lineage()
        if context_id is not None:
            records = [r for r in records if r.context_id == context_id]
        if entity_id is not None:
            records = [r for r in records if r.entity_id == entity_id]
        return sorted(records, key=lambda r: (r.recorded_at, r.document_version))

    def list_signals(
        self,
        *,
        doctype: str | None = None,
        verified_only: bool = False,
    ) -> list[ExpertFeedbackSignal]:
        """List ExpertFeedbackSignal records optionally filtered by doctype and oversight verification."""
        signals = self._list_all_signals()
        if doctype is not None:
            norm_doc = doctype.strip().lower()
            signals = [s for s in signals if s.doctype.strip().lower() == norm_doc]
        if verified_only:
            signals = [
                s
                for s in signals
                if s.citation_verified and s.oversight_rsr_score >= 0.50
            ]
        return sorted(signals, key=lambda s: s.recorded_at)

    def export_docai_uptraining_dataset(
        self,
        *,
        doctype: str | None = None,
        verified_only: bool = True,
    ) -> dict[str, Any]:
        """Export verified ExpertFeedbackSignal + SourceAnchor records in Document AI ImportDocuments format.

        Translates human-verified field corrections and normalized bounding boxes `(x0, y0, x1, y1)`
        into Document AI `Document` JSON records (`entities[].page_anchor.page_refs[].bounding_poly.normalized_vertices`)
        ready for Document AI Custom Extractor uptraining (`ImportDocuments`).
        """
        with trace_step(
            "docubridge.agent_gym.export_docai_dataset",
            {
                "docubridge.zone": "6_knowledge_catalog_agent_gym",
                "docubridge.doctype": doctype or "*",
                "docubridge.verified_only": verified_only,
            },
        ) as span:
            signals = self.list_signals(doctype=doctype, verified_only=verified_only)
            documents: list[dict[str, Any]] = []
            for sig in signals:
                anchor = sig.source_anchor
                page_index = (
                    max(0, anchor.page_number - 1)
                    if anchor is not None and anchor.page_number >= 1
                    else 0
                )
                if anchor is not None and anchor.bounding_box is not None:
                    x0, y0, x1, y1 = anchor.bounding_box
                else:
                    x0, y0, x1, y1 = (0.10, 0.20, 0.85, 0.28)
                normalized_vertices = [
                    {"x": round(float(x0), 4), "y": round(float(y0), 4)},
                    {"x": round(float(x1), 4), "y": round(float(y0), 4)},
                    {"x": round(float(x1), 4), "y": round(float(y1), 4)},
                    {"x": round(float(x0), 4), "y": round(float(y1), 4)},
                ]
                entity_record = {
                    "id": sig.signal_id,
                    "type": sig.field_path,
                    "mention_text": sig.after_value,
                    "confidence": 1.0,
                    "normalized_value": {"text": sig.after_value},
                    "page_anchor": {
                        "page_refs": [
                            {
                                "page": str(page_index),
                                "layout_type": "VISUAL_ELEMENT",
                                "bounding_poly": {
                                    "normalized_vertices": normalized_vertices,
                                },
                            }
                        ]
                    },
                }
                documents.append(
                    {
                        "uri": f"gs://{_vault_bucket()}/vault/{sig.entity_id}/{sig.doc_id}.pdf",
                        "mime_type": "application/pdf",
                        "doc_id": sig.doc_id,
                        "entity_id": sig.entity_id,
                        "doctype": sig.doctype,
                        "issuer": sig.issuer,
                        "signal_id": sig.signal_id,
                        "oversight_rsr_score": sig.oversight_rsr_score,
                        "citation_verified": sig.citation_verified,
                        "document": {
                            "uri": f"gs://{_vault_bucket()}/vault/{sig.entity_id}/{sig.doc_id}.pdf",
                            "mime_type": "application/pdf",
                            "text": f"{sig.field_path}: {sig.after_value}",
                            "entities": [entity_record],
                        },
                    }
                )
            span.set_attribute("docubridge.exported_document_count", len(documents))
            return {
                "format": "google.cloud.documentai.v1.ImportDocumentsRequest",
                "doctype_filter": doctype or "*",
                "verified_only": verified_only,
                "document_count": len(documents),
                "documents": documents,
            }

    # ------------------------------------------------------------------ #
    # Programmable Trust & Shadow Mode Comparisons                       #
    # ------------------------------------------------------------------ #

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

    def record_shadow_comparison(
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
        citation_verified: bool = True,
        review_dwell_ms: int = 4200,
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

    def dial_trust_policy(
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
