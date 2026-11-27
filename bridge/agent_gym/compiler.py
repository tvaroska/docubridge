"""Harness Evolve Skill Compiler, SkillRevision lineage & Document AI dataset export mixin."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any, Literal

from bridge.agent_gym.models import (
    REASONING_MODEL,
    WORKHORSE_MODEL,
    ExpertFeedbackSignal,
    ProcessSkillLineageRecord,
    SkillRevisionRecord,
    TribalKnowledgeRule,
    TribalRuleStatus,
    _gcp_project_id,
    _skills_bucket,
    _vault_bucket,
    issuer_matches_pattern,
)
from bridge.agent_gym.trust import TrustPolicyMixin
from bridge.telemetry import trace_step


class SkillCompilerMixin(TrustPolicyMixin):
    """Mixin providing Harness Evolve skill compilation, SkillRevision rollback,
    process-instance skill lineage, and Document AI uptraining dataset export."""

    def _invoke_reasoning_model_if_enabled(
        self,
        *,
        prompt: str,
    ) -> tuple[str | None, int | None, int | None]:
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

    @staticmethod
    def _build_skill_zip(
        skill_markdown: str, schema_json_str: str, lineage_manifest: str
    ) -> tuple[bytes, str]:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", skill_markdown)
            zf.writestr("assets/schema.json", schema_json_str)
            zf.writestr("assets/lineage.json", lineage_manifest)
        zip_bytes = zip_buf.getvalue()
        return zip_bytes, hashlib.sha256(zip_bytes).hexdigest()

    @staticmethod
    def _install_compiled_skill(
        doctype: str,
        skill_markdown: str,
        schema_json_str: str,
        lineage_manifest: str,
    ) -> None:
        import contextlib

        from bridge.ports import Port, get_adapter, resolve_mode

        registry = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
        install_assets: dict[str, str] = {
            "assets/schema.json": schema_json_str,
            "assets/lineage.json": lineage_manifest,
        }
        with contextlib.suppress(KeyError, ValueError, OSError):
            install_assets["assets/validation.yaml"] = registry.read_asset(
                doctype, "assets/validation.yaml"
            )
        registry.install_skill(doctype, skill_markdown, install_assets)

    @staticmethod
    def _rule_matches_compile_issuer(
        rule: TribalKnowledgeRule, issuer_pattern: str
    ) -> bool:
        cleaned = (issuer_pattern or "*").strip()
        if cleaned in ("", "*"):
            return True
        if rule.issuer_pattern == "*" or rule.issuer_pattern.lower() == cleaned.lower():
            return True
        probe_issuer = cleaned.rstrip("*").strip()
        rule_probe = rule.issuer_pattern.rstrip("*").strip()
        return bool(probe_issuer) and (
            issuer_matches_pattern(probe_issuer, rule.issuer_pattern)
            or (bool(rule_probe) and issuer_matches_pattern(rule_probe, cleaned))
        )

    def _select_compile_rules(
        self, doctype: str, issuer_pattern: str
    ) -> tuple[list[TribalKnowledgeRule], list[str], bool]:
        cleaned_doctype = doctype.strip()
        if not cleaned_doctype or cleaned_doctype == "*":
            raise ValueError(
                "compile_rules_to_skill requires a specific doctype slug, not '*'."
            )
        all_rules = self._list_all_rules()
        matched = [
            r
            for r in all_rules
            if r.status in (TribalRuleStatus.PROMOTED, TribalRuleStatus.VERIFIED)
            and r.doctype in ("*", cleaned_doctype)
            and self._rule_matches_compile_issuer(r, issuer_pattern)
        ]
        has_verified = bool(matched)
        rule_bullets = [
            f"- **[{r.rule_id} · {r.category.value}]** ({r.issuer_pattern} / `{r.field_path}`): {r.instruction}"
            for r in matched
        ] or [
            f"- **[rule-default-precedence · extraction_hint]** ({issuer_pattern} / `origination_fee`): "
            "Prioritize executed Schedule B / Page 3 Section A addendum tables over cover-page estimates."
        ]
        return matched, rule_bullets, has_verified

    def compile_rules_to_skill(  # noqa: PLR0913 - keyword-only Harness Evolve compiler options
        self,
        *,
        doctype: str = "closing-disclosure",
        issuer_pattern: str = "*",
        install_to_port: bool = False,
        auto_promote: bool = False,
        policy_ceiling_usd: str = "100000.00",
        ceiling_note: str | None = None,
        exact_match_condition: str | None = None,
        fallback_target: str | None = None,
        guardrail_bullets: list[str] | None = None,
    ) -> dict[str, Any]:
        """Harness Evolve Nightly Compiler: fold verified/promoted tribal rules into a versioned
        `SKILL.md` package via `gemini-4-argon` and register an immutable `SkillRevision`."""
        llm_text, in_tok, out_tok = self._invoke_reasoning_model_if_enabled(
            prompt=f"Compile promoted TribalKnowledgeRules for doctype='{doctype}' issuer='{issuer_pattern}' into SKILL.md.",
        )
        compile_attrs: dict[str, Any] = {
            "docubridge.zone": "6_knowledge_catalog_agent_gym",
            "docubridge.doctype": doctype,
            "docubridge.issuer_pattern": issuer_pattern,
            "skill_id": doctype,
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "compile_skill",
            "gen_ai.agent.name": "harness_evolve_compiler",
            "docubridge.finops.traffic_type": "agent_gym_eval",
            "finops.traffic_type": "agent_gym_eval",
        }
        if llm_text is not None:
            compile_attrs["gen_ai.request.model"] = REASONING_MODEL
            compile_attrs["gen_ai.response.model"] = REASONING_MODEL
        if in_tok is not None:
            compile_attrs["gen_ai.usage.input_tokens"] = in_tok
        if out_tok is not None:
            compile_attrs["gen_ai.usage.output_tokens"] = out_tok
        with trace_step("docubridge.agent_gym.compile_skill", compile_attrs) as span:
            from bridge.agent_gym.skill_promotion import (
                render_compiled_skill_markdown,
                resolve_compiled_schema_json,
            )
            from bridge.edge.skills import validate_skill_upload

            matched, rule_bullets, has_verified = self._select_compile_rules(
                doctype, issuer_pattern
            )
            target_state: Literal["DRAFT", "ACTIVE"] = (
                "ACTIVE" if auto_promote and has_verified else "DRAFT"
            )
            existing_revs = self.list_skill_revisions(doctype)
            current_default = next(
                (rev for rev in existing_revs if rev.is_default_revision), None
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
            schema_json_str = resolve_compiled_schema_json(
                doctype, semver=semver, sorted_rule_ids=sorted_rule_ids
            )
            digest_input = f"{doctype}|{semver}|{','.join(sorted_rule_ids)}|{'\n'.join(rule_bullets)}".encode()
            revision_id = (
                f"rev-v1-{minor_num}-{hashlib.sha256(digest_input).hexdigest()[:8]}"
            )
            resource_name = f"projects/{_gcp_project_id()}/locations/global/skills/private-{doctype}/revisions/{revision_id}"
            gcs_source_uri = (
                f"gs://{_skills_bucket()}/revisions/{doctype}/{revision_id}.zip"
            )
            skill_markdown = render_compiled_skill_markdown(
                doctype=doctype,
                semver=semver,
                skill_version=skill_version,
                revision_id=revision_id,
                parent_revision_id=parent_revision_id,
                sorted_rule_ids=sorted_rule_ids,
                rule_bullets=rule_bullets,
                policy_ceiling_usd=policy_ceiling_usd,
                skill_urn=skill_urn,
                target_state=target_state,
                ceiling_note=ceiling_note,
                exact_match_condition=exact_match_condition,
                fallback_target=fallback_target,
                guardrail_bullets=guardrail_bullets,
            )
            validate_skill_upload(
                doctype, skill_markdown, {"assets/schema.json": schema_json_str}
            )
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
            zip_bytes, archive_sha256 = self._build_skill_zip(
                skill_markdown, schema_json_str, lineage_manifest
            )
            is_default = target_state == "ACTIVE"
            if is_default:
                for prev in existing_revs:
                    if prev.is_default_revision:
                        self._save_skill_revision(
                            prev.model_copy(update={"is_default_revision": False})
                        )
            self._save_skill_revision(
                SkillRevisionRecord(
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
                    compiled_rules_count=len(matched),
                    gcs_source_uri=gcs_source_uri,
                    archive_sha256=archive_sha256,
                    archive_size_bytes=len(zip_bytes),
                    skill_markdown=skill_markdown,
                    assets_schema_json=schema_json_str,
                )
            )
            if install_to_port and is_default:
                self._install_compiled_skill(
                    doctype, skill_markdown, schema_json_str, lineage_manifest
                )

            span.set_attribute("docubridge.compiled_rule_count", len(matched))
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
                "skill_uri": f"gs://{_skills_bucket()}/{doctype}/SKILL.md",
                "gcs_source_uri": gcs_source_uri,
                "archive_sha256": archive_sha256,
                "archive_size_bytes": len(zip_bytes),
                "teacher_model": REASONING_MODEL,
                "workhorse_model": WORKHORSE_MODEL,
                "compiled_rule_ids": list(sorted_rule_ids),
                "compiled_rules_count": len(matched),
                "skill_markdown": skill_markdown,
            }

    def promote_skill_revision(
        self,
        skill_name: str,
        revision_id: str,
        *,
        install_to_port: bool = True,
    ) -> SkillRevisionRecord:
        """Promote a compiled `DRAFT` `SkillRevision` to `ACTIVE` (`defaultRevision`) after human diff sign-off."""
        from bridge.agent_gym.skill_promotion import execute_skill_promotion

        return execute_skill_promotion(
            self, skill_name, revision_id, install_to_port=install_to_port
        )

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

    def record_process_skill_lineage(  # noqa: PLR0913 - keyword-only lineage record fields
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
                if s.citation_verified and s.oversight_rsr_score >= 0.80
            ]
        return sorted(signals, key=lambda s: s.recorded_at)

    def export_docai_uptraining_dataset(
        self,
        *,
        doctype: str | None = None,
        verified_only: bool = True,
    ) -> dict[str, Any]:
        """Export verified ExpertFeedbackSignal + SourceAnchor records in Document AI ImportDocuments format."""
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
