"""Document-to-Schema Induction, Skill Package Materialization & Use-Case Onboarding.

Allows DocuBridge to start empty (`BRIDGE_EMPTY_CATALOG_ON_START=1`, `seed_skills=false`)
and lets each use-case agent onboard its own doctypes and process skill from sample
documents (`create_schema_from_documents` and `onboard_use_case`).
"""

from __future__ import annotations

import json
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bridge.doctypes import entity_spec_to_json_schema
from bridge.refinery.engine import SchemaRefineryEngine
from bridge.refinery.okf_loader import (
    parse_okf_doctype_bundle,
    render_okf_markdown,
    render_skill_schema_json,
)
from bridge.refinery.onboarding import (
    DoctypeOnboardingResult,
    PairedTargetExample,
    SampleDocumentExample,
    _slugify_entity_id,
    build_onboarding_proposal,
)
from bridge.refinery.sample_loader import (
    build_onboarding_cli_parser,
)
from bridge.refinery.sample_loader import (
    load_sample_from_file as load_sample_from_file,
)
from bridge.refinery.sql_store import schema_refinery_from_env
from contract.refinery import (
    OntologyEdge,
    OntologyEntitySpec,
    OntologyRevisionState,
)

DEFAULT_PROCESS_POLICY: dict[str, Any] = {
    "disposition": {
        "resubmit_below": 0.55,
        "auto_approve_at": 0.85,
        "max_resubmissions": 3,
    },
    "sla": {
        "deadline": 3,
        "cadence": 2,
        "max_nudges": 2,
    },
}

DEFAULT_DOCTYPE_VALIDATION: dict[str, Any] = {
    "rules": [
        {
            "id": "required_fields_present",
            "check": "all_required_present",
        }
    ],
    "signals": {
        "legibility": "bridge",
        "type_match": "bridge",
        "confidence": "bridge",
        "completeness": "agent",
    },
}


with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)

    class DoctypeSchemaPackage(BaseModel):
        """Complete generated OKF + Skill package for one doctype."""

        model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

        entity_id: str
        okf_markdown: str
        skill_md: str
        schema_json: str  # type: ignore[assignment]
        validation_yaml: str
        onboarding_result: DoctypeOnboardingResult
        promoted_revision_id: str | None = None
        installed_in_registry: bool = False
        written_files: tuple[str, ...] = ()


class UseCaseDoctypeRequest(BaseModel):
    """HTTP-safe doctype onboarding item (inline samples only; no server file paths)."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    description: str = ""
    samples: list[dict[str, Any]] = Field(default_factory=list)
    paired_target_examples: list[dict[str, Any]] = Field(default_factory=list)
    okf_markdown: str | None = None
    spine_entity_id: str | None = None
    context_set: str = "enterprise-document-intake"
    domain: str = "general"
    source_priority: int = 20
    supersedes_spine: bool = True
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    field_enums: dict[str, list[str]] = Field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = Field(default_factory=dict)
    notes: str = ""


class UseCaseOnboardingRequest(BaseModel):
    """HTTP-safe request body for ``POST /v1/refinery/onboard-use-case`` (no ``sample_files``)."""

    model_config = ConfigDict(extra="forbid")

    process_name: str
    process_description: str
    pattern: Literal["collect", "request", "negotiate", "deliver"] = "collect"
    hitl: Literal["park", "report"] = "report"
    satisfaction: str = ""
    tags: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    context_set: str = "enterprise-document-intake"
    domain: str = "general"
    doctypes: list[UseCaseDoctypeRequest] = Field(default_factory=list)
    auto_promote: bool = True
    install_skills: bool = True


class DoctypeOnboardingItem(UseCaseDoctypeRequest):
    """CLI/manifest doctype onboarding specification supporting local ``sample_files``."""

    sample_files: list[str] = Field(default_factory=list)


class UseCaseOnboardingManifest(BaseModel):
    """Declarative CLI manifest for onboarding a use-case agent onto DocuBridge."""

    model_config = ConfigDict(extra="forbid")

    process_name: str
    process_description: str
    pattern: Literal["collect", "request", "negotiate", "deliver"] = "collect"
    hitl: Literal["park", "report"] = "report"
    satisfaction: str = ""
    tags: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    context_set: str = "enterprise-document-intake"
    domain: str = "general"
    doctypes: list[DoctypeOnboardingItem] = Field(default_factory=list)
    auto_promote: bool = True
    install_skills: bool = True


class UseCaseOnboardingResult(BaseModel):
    """Summary returned after onboarding a use-case agent onto DocuBridge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    process_name: str
    installed_doctypes: tuple[str, ...]
    installed_skills: tuple[str, ...]
    active_revision_id: str
    agent_card_skill_ids: tuple[str, ...]
    doctype_packages: tuple[DoctypeSchemaPackage, ...]


def render_doctype_skill_package(  # noqa: PLR0913 - keyword-only doctype skill package renderer
    spec: OntologyEntitySpec,
    *,
    field_enums: dict[str, tuple[str, ...]] | None = None,
    tables: dict[str, dict[str, Any]] | None = None,
    schema_version: str | None = "1.0",
    validation_rules: Mapping[str, Any] | None = None,
    tags: Sequence[str] = (),
    notes: str = "",
) -> tuple[str, dict[str, str]]:
    """Render `(skill_md, assets)` for a doctype skill ready for `Port.skill_registry.install_skill`."""
    schema_dict = entity_spec_to_json_schema(
        spec,
        field_enums=field_enums,
        tables=tables,
        schema_version=schema_version,
    )
    schema_json_str = render_skill_schema_json(schema_dict)
    val_doc = (
        dict(validation_rules) if validation_rules else dict(DEFAULT_DOCTYPE_VALIDATION)
    )
    validation_yaml_str = yaml.safe_dump(val_doc, sort_keys=False, allow_unicode=True)

    tag_list = [
        "doctype",
        spec.entity_id,
        *(t for t in tags if t not in ("doctype", spec.entity_id)),
    ]
    desc_text = (
        spec.entity_description.what_it_is.strip()
        or f"Structured {spec.entity_id} document."
    )
    frontmatter = {
        "name": spec.entity_id,
        "description": desc_text[:1024],
        "license": "Apache-2.0",
        "metadata": {
            "bridge-kind": "doctype",
            "bridge-tags": " ".join(tag_list),
            "bridge-extraction-engine": "gemini",
            "bridge-docai-processor": "",
            "bridge-schema": "assets/schema.json",
            "version": str(schema_version or "1.0"),
        },
    }
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body_text = (
        notes.strip()
        or f"Extract the fields defined in `assets/schema.json` for `{spec.entity_id}`. "
        f"Validation rules live in `assets/validation.yaml`."
    )
    skill_md = f"---\n{fm_yaml}\n---\n\n{body_text}\n"
    assets = {
        "assets/schema.json": schema_json_str,
        "assets/validation.yaml": validation_yaml_str,
    }
    return skill_md, assets


def render_process_skill_package(  # noqa: PLR0913 - keyword-only process skill package renderer
    process_name: str,
    *,
    description: str,
    candidate_doctypes: Sequence[str],
    pattern: str = "collect",
    hitl: str = "report",
    satisfaction: str = "",
    tags: Sequence[str] = (),
    policy: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """Render `(skill_md, assets)` for a process skill ready for `Port.skill_registry.install_skill`."""
    slug = _slugify_entity_id(process_name)
    candidates_clean = [
        _slugify_entity_id(c) for c in candidate_doctypes if str(c).strip()
    ]
    if not candidates_clean:
        raise ValueError(
            f"Process skill '{slug}' requires at least one candidate doctype"
        )

    policy_doc = dict(policy) if policy else dict(DEFAULT_PROCESS_POLICY)
    policy_yaml_str = yaml.safe_dump(policy_doc, sort_keys=False, allow_unicode=True)

    tag_list = [slug, pattern, *(t for t in tags if t not in (slug, pattern))]
    meta: dict[str, str] = {
        "bridge-kind": "process",
        "bridge-pattern": pattern,
        "bridge-candidate-doctypes": " ".join(candidates_clean),
        "bridge-tags": " ".join(tag_list),
        "bridge-policy": "assets/policy.yaml",
        "bridge-hitl": hitl,
        "version": "1.0",
    }
    if satisfaction.strip():
        meta["bridge-satisfaction"] = satisfaction.strip()

    frontmatter = {
        "name": slug,
        "description": (description.strip() or f"Process exchange skill {slug}.")[
            :1024
        ],
        "license": "Apache-2.0",
        "metadata": meta,
    }
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = (
        f"Process skill `{slug}` (`{pattern}`) over candidate doctypes "
        f"`{', '.join(candidates_clean)}`. Policy lives in `assets/policy.yaml`."
    )
    if satisfaction.strip():
        body += f"\n\n## Satisfaction (advisory)\n{satisfaction.strip()}"
    skill_md = f"---\n{fm_yaml}\n---\n\n{body}\n"
    return skill_md, {"assets/policy.yaml": policy_yaml_str}


def _merge_okf_bundle_override(
    okf_markdown: str | None,
    entity_id: str,
    spec: OntologyEntitySpec,
    enums_tuple: dict[str, tuple[str, ...]],
    tables_dict: dict[str, dict[str, Any]],
) -> tuple[
    OntologyEntitySpec, dict[str, tuple[str, ...]], dict[str, dict[str, Any]], str
]:
    if not okf_markdown:
        return (
            spec,
            enums_tuple,
            tables_dict,
            render_okf_markdown(spec, field_enums=enums_tuple, tables=tables_dict),
        )
    bundle = parse_okf_doctype_bundle(okf_markdown, default_entity_id=entity_id)
    safe_id = _slugify_entity_id(bundle.spec.entity_id or entity_id)
    normalized_spec = (
        bundle.spec
        if bundle.spec.entity_id == safe_id
        else bundle.spec.model_copy(update={"entity_id": safe_id})
    )
    merged_enums = enums_tuple or dict(bundle.field_enums)
    merged_tables = tables_dict or dict(bundle.tables)
    rendered = render_okf_markdown(
        normalized_spec,
        field_enums=merged_enums,
        tables=merged_tables,
        schema_version=bundle.schema_version,
    )
    return normalized_spec, merged_enums, merged_tables, rendered


def _write_doctype_package_files(
    write_dir: Path,
    entity_id: str,
    final_okf_md: str,
    skill_md: str,
    assets: Mapping[str, str],
) -> list[str]:
    base = Path(write_dir).resolve()
    written: list[str] = []
    okf_path = base / "catalog" / "doctypes" / f"{entity_id}.okf.md"
    okf_path.parent.mkdir(parents=True, exist_ok=True)
    okf_path.write_text(final_okf_md, encoding="utf-8")
    written.append(str(okf_path))

    s_dir = base / "skills" / entity_id
    (s_dir / "assets").mkdir(parents=True, exist_ok=True)
    skill_md_path = s_dir / "SKILL.md"
    skill_md_path.write_text(skill_md, encoding="utf-8")
    written.append(str(skill_md_path))
    for rel, content in assets.items():
        target = s_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target))
    return written


def _assay_okf_override_proposal(  # noqa: PLR0913 - keyword-only OKF override assay inputs
    *,
    active_refinery: SchemaRefineryEngine,
    entity_id: str,
    okf_markdown: str,
    combined_samples: Sequence[SampleDocumentExample | Mapping[str, Any]],
    paired_target_examples: Sequence[PairedTargetExample | Mapping[str, Any]],
    spine_entity_id: str | None,
    context_set: str,
    domain: str,
    source_priority: int,
    supersedes_spine: bool,
    aliases: Sequence[str],
    enums_tuple: dict[str, tuple[str, ...]],
    tables_dict: dict[str, dict[str, Any]],
    client: object,
    use_live_llm: bool | None,
) -> tuple[
    DoctypeOnboardingResult,
    OntologyEntitySpec,
    dict[str, tuple[str, ...]],
    dict[str, dict[str, Any]],
    str,
]:
    baseline = active_refinery.get_active_revision()
    raw_proposal, induced_spec, _induced_md, proj_hints, induced_by = (
        build_onboarding_proposal(
            entity_id=entity_id,
            samples=combined_samples,
            baseline_revision=baseline,
            paired_target_examples=paired_target_examples,
            spine_entity_id=spine_entity_id,
            context_set=context_set,
            domain=domain,
            source_priority=source_priority,
            supersedes_spine=supersedes_spine,
            aliases=aliases,
            client=client,
            use_live_llm=use_live_llm,
        )
    )
    spec, merged_enums, merged_tables, final_okf_md = _merge_okf_bundle_override(
        okf_markdown, entity_id, induced_spec, enums_tuple, tables_dict
    )
    merged_edges: list[OntologyEdge] = list(spec.edges)
    seen_keys = {(e.source_id, e.relation, e.target_id) for e in merged_edges}
    for extra_edge in raw_proposal.added_edges:
        key = (extra_edge.source_id, extra_edge.relation, extra_edge.target_id)
        if extra_edge.relation == "SUPERSEDES" and key not in seen_keys:
            rebound = extra_edge.model_copy(update={"source_id": spec.entity_id})
            merged_edges.append(rebound)
            seen_keys.add((rebound.source_id, rebound.relation, rebound.target_id))
    spec_with_edges = spec.model_copy(update={"edges": tuple(merged_edges)})
    override_proposal = raw_proposal.model_copy(
        update={
            "target_entity_id": spine_entity_id or spec.entity_id,
            "new_entity_id": None,
            "proposed_entity": spec_with_edges,
            "added_edges": tuple(merged_edges),
        }
    )
    updated_prop, report, cand_rev = active_refinery.register_proposal(
        override_proposal
    )
    onboard_res = DoctypeOnboardingResult(
        proposal=updated_prop,
        assay_report=report,
        draft_revision=cand_rev.model_copy(
            update={"state": OntologyRevisionState.DRAFT}
        ),
        entity_spec=spec_with_edges,
        okf_markdown=final_okf_md,
        target_projection_hints=proj_hints,
        induced_by_model=induced_by,
    )
    return onboard_res, spec_with_edges, merged_enums, merged_tables, final_okf_md


def create_schema_from_documents(  # noqa: PLR0913 - keyword-only public schema induction API
    *,
    entity_id: str,
    samples: Sequence[SampleDocumentExample | Mapping[str, Any]] = (),
    sample_files: Sequence[str | Path] = (),
    paired_target_examples: Sequence[PairedTargetExample | Mapping[str, Any]] = (),
    okf_markdown: str | None = None,
    spine_entity_id: str | None = None,
    context_set: str = "enterprise-document-intake",
    domain: str = "general",
    source_priority: int = 20,
    supersedes_spine: bool = True,
    aliases: Sequence[str] = (),
    tags: Sequence[str] = (),
    validation_rules: Mapping[str, Any] | None = None,
    field_enums: Mapping[str, Sequence[str]] | None = None,
    tables: Mapping[str, Mapping[str, Any]] | None = None,
    notes: str = "",
    refinery: SchemaRefineryEngine | None = None,
    auto_promote: bool = False,
    install_skill: bool = False,
    write_dir: Path | None = None,
    client: object = None,
    use_live_llm: bool | None = None,
) -> DoctypeSchemaPackage:
    """Create an OKF `.okf.md` bundle + `SKILL.md` + `schema.json` + `validation.yaml` from documents."""
    active_refinery = refinery or schema_refinery_from_env()
    combined_samples: list[SampleDocumentExample | Mapping[str, Any]] = list(samples)
    for idx, fpath in enumerate(sample_files, start=len(combined_samples) + 1):
        combined_samples.append(
            load_sample_from_file(Path(fpath), sample_id=f"sample-{idx}")
        )

    enums_tuple: dict[str, tuple[str, ...]] = {
        str(k): tuple(str(v) for v in vals) for k, vals in (field_enums or {}).items()
    }
    tables_dict: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in (tables or {}).items()
    }

    if okf_markdown and not combined_samples:
        bundle = parse_okf_doctype_bundle(okf_markdown, default_entity_id=entity_id)
        enums_tuple = enums_tuple or dict(bundle.field_enums)
        tables_dict = tables_dict or dict(bundle.tables)
        seed_fields = {fname: f"sample_{fname}" for fname in bundle.spec.fields} or {
            "document_id": "sample_id"
        }
        combined_samples.append(
            SampleDocumentExample(
                sample_id="sample-from-okf",
                extracted_fields=seed_fields,
            )
        )

    if okf_markdown:
        onboard_res, spec, enums_tuple, tables_dict, final_okf_md = (
            _assay_okf_override_proposal(
                active_refinery=active_refinery,
                entity_id=entity_id,
                okf_markdown=okf_markdown,
                combined_samples=combined_samples,
                paired_target_examples=paired_target_examples,
                spine_entity_id=spine_entity_id,
                context_set=context_set,
                domain=domain,
                source_priority=source_priority,
                supersedes_spine=supersedes_spine,
                aliases=aliases,
                enums_tuple=enums_tuple,
                tables_dict=tables_dict,
                client=client,
                use_live_llm=use_live_llm,
            )
        )
    else:
        onboard_res = active_refinery.onboard_doctype_from_examples(
            entity_id=entity_id,
            samples=combined_samples,
            paired_target_examples=paired_target_examples,
            spine_entity_id=spine_entity_id,
            context_set=context_set,
            domain=domain,
            source_priority=source_priority,
            supersedes_spine=supersedes_spine,
            aliases=aliases,
            client=client,
            use_live_llm=use_live_llm,
        )
        spec, enums_tuple, tables_dict, final_okf_md = _merge_okf_bundle_override(
            None, entity_id, onboard_res.entity_spec, enums_tuple, tables_dict
        )

    promoted_rev_id: str | None = None
    if auto_promote and onboard_res.assay_report.passed:
        promoted = active_refinery.promote_proposal(
            onboard_res.proposal.proposal_id,
            revision_id=f"ont-rev-onboard-{spec.entity_id[:24]}",
        )
        promoted_rev_id = promoted.revision_id

    skill_md, assets = render_doctype_skill_package(
        spec,
        field_enums=enums_tuple,
        tables=tables_dict,
        validation_rules=validation_rules,
        tags=tags,
        notes=notes,
    )

    installed = False
    if install_skill and onboard_res.assay_report.passed:
        from bridge.ports import Port, get_adapter, resolve_mode

        reg = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
        reg.install_skill(spec.entity_id, skill_md, assets)
        installed = True

    written = (
        _write_doctype_package_files(
            write_dir, spec.entity_id, final_okf_md, skill_md, assets
        )
        if write_dir is not None and onboard_res.assay_report.passed
        else []
    )

    return DoctypeSchemaPackage(
        entity_id=spec.entity_id,
        okf_markdown=final_okf_md,
        skill_md=skill_md,
        schema_json=assets["assets/schema.json"],
        validation_yaml=assets["assets/validation.yaml"],
        onboarding_result=onboard_res,
        promoted_revision_id=promoted_rev_id,
        installed_in_registry=installed,
        written_files=tuple(written),
    )


def onboard_use_case(  # noqa: PLR0913 - keyword-only public use-case onboarding API
    manifest: UseCaseOnboardingRequest | UseCaseOnboardingManifest | Mapping[str, Any],
    *,
    base_dir: Path | None = None,
    refinery: SchemaRefineryEngine | None = None,
    write_dir: Path | None = None,
    client: object = None,
    use_live_llm: bool | None = None,
) -> UseCaseOnboardingResult:
    """Onboard a complete use-case agent (all its doctypes + its process skill) onto DocuBridge."""
    parsed: UseCaseOnboardingRequest | UseCaseOnboardingManifest = (
        manifest
        if isinstance(manifest, (UseCaseOnboardingRequest, UseCaseOnboardingManifest))
        else UseCaseOnboardingManifest.model_validate(manifest)
    )
    active_refinery = refinery or schema_refinery_from_env()
    root_dir = Path(base_dir).resolve() if base_dir is not None else Path.cwd()

    staged_packages: list[DoctypeSchemaPackage] = []
    for item in parsed.doctypes:
        raw_sample_files: Sequence[str] = getattr(item, "sample_files", ())
        resolved_files = [
            str((root_dir / rel).resolve()) if not Path(rel).is_absolute() else rel
            for rel in raw_sample_files
        ]
        pkg = create_schema_from_documents(
            entity_id=item.entity_id,
            samples=item.samples,
            sample_files=resolved_files,
            paired_target_examples=item.paired_target_examples,
            okf_markdown=item.okf_markdown,
            spine_entity_id=item.spine_entity_id,
            context_set=item.context_set or parsed.context_set,
            domain=item.domain if item.domain != "general" else parsed.domain,
            source_priority=item.source_priority,
            supersedes_spine=item.supersedes_spine,
            aliases=item.aliases,
            tags=item.tags,
            validation_rules=item.validation or None,
            field_enums=item.field_enums or None,
            tables=item.tables or None,
            notes=item.notes or item.description,
            refinery=active_refinery,
            auto_promote=False,
            install_skill=False,
            write_dir=None,
            client=client,
            use_live_llm=use_live_llm,
        )
        staged_packages.append(pkg)

    all_assays_passed = bool(staged_packages) and all(
        pkg.onboarding_result.assay_report.passed for pkg in staged_packages
    )
    proc_slug = _slugify_entity_id(parsed.process_name)
    doctype_packages: list[DoctypeSchemaPackage] = []
    installed_doctypes: list[str] = []
    installed_skills: list[str] = []

    if all_assays_passed:
        promoted_packages: list[DoctypeSchemaPackage] = []
        for pkg in staged_packages:
            updates: dict[str, Any] = {}
            if parsed.auto_promote:
                promoted = active_refinery.promote_proposal(
                    pkg.onboarding_result.proposal.proposal_id,
                    revision_id=f"ont-rev-onboard-{pkg.entity_id[:24]}",
                )
                updates["promoted_revision_id"] = promoted.revision_id
            if write_dir is not None:
                updates["written_files"] = tuple(
                    _write_doctype_package_files(
                        write_dir,
                        pkg.entity_id,
                        pkg.okf_markdown,
                        pkg.skill_md,
                        {
                            "assets/schema.json": pkg.schema_json,
                            "assets/validation.yaml": pkg.validation_yaml,
                        },
                    )
                )
            promoted_packages.append(pkg.model_copy(update=updates) if updates else pkg)
        staged_packages = promoted_packages
        candidate_doctypes = [pkg.entity_id for pkg in staged_packages]
        proc_skill_md, proc_assets = render_process_skill_package(
            proc_slug,
            description=parsed.process_description,
            candidate_doctypes=candidate_doctypes,
            pattern=parsed.pattern,
            hitl=parsed.hitl,
            satisfaction=parsed.satisfaction,
            tags=parsed.tags,
            policy=parsed.policy or None,
        )
        if parsed.install_skills:
            from bridge.ports import Port, get_adapter, resolve_mode

            reg = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
            for pkg in staged_packages:
                reg.install_skill(
                    pkg.entity_id,
                    pkg.skill_md,
                    {
                        "assets/schema.json": pkg.schema_json,
                        "assets/validation.yaml": pkg.validation_yaml,
                    },
                )
                doctype_packages.append(
                    pkg.model_copy(update={"installed_in_registry": True})
                )
                installed_doctypes.append(pkg.entity_id)
                installed_skills.append(pkg.entity_id)
            reg.install_skill(proc_slug, proc_skill_md, proc_assets)
            installed_skills.append(proc_slug)
        else:
            doctype_packages.extend(staged_packages)
            installed_doctypes.extend(candidate_doctypes)

        if write_dir is not None:
            p_dir = Path(write_dir).resolve() / "skills" / proc_slug
            (p_dir / "assets").mkdir(parents=True, exist_ok=True)
            (p_dir / "SKILL.md").write_text(proc_skill_md, encoding="utf-8")
            for rel, content in proc_assets.items():
                dest = p_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
    else:
        doctype_packages.extend(staged_packages)

    from bridge.edge.skills import loaded_skills

    card_processes = tuple(s.name for s in loaded_skills() if s.kind == "process")
    active_rev = active_refinery.get_active_revision()
    return UseCaseOnboardingResult(
        process_name=proc_slug,
        installed_doctypes=tuple(installed_doctypes),
        installed_skills=tuple(installed_skills),
        active_revision_id=active_rev.revision_id,
        agent_card_skill_ids=card_processes,
        doctype_packages=tuple(doctype_packages),
    )


def load_manifest_file(manifest_path: Path) -> tuple[UseCaseOnboardingManifest, Path]:
    """Load a `UseCaseOnboardingManifest` from a YAML or JSON file and inline any `sample_files`."""
    resolved = manifest_path.resolve()
    raw = resolved.read_text(encoding="utf-8")
    data = (
        json.loads(raw) if resolved.suffix.lower() == ".json" else yaml.safe_load(raw)
    )
    manifest = UseCaseOnboardingManifest.model_validate(data)
    base_dir = resolved.parent
    hydrated_items: list[DoctypeOnboardingItem] = []
    for item in manifest.doctypes:
        samples_list = list(item.samples)
        for idx, rel in enumerate(item.sample_files, start=len(samples_list) + 1):
            fpath = (
                (base_dir / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
            )
            sample = load_sample_from_file(fpath, sample_id=f"{item.entity_id}-{idx}")
            samples_list.append(sample.model_dump(mode="json"))
        hydrated_items.append(
            item.model_copy(update={"samples": samples_list, "sample_files": []})
        )
    return manifest.model_copy(update={"doctypes": hydrated_items}), base_dir


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `create-schema` and `onboard` commands."""
    parser = build_onboarding_cli_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "create-schema":
        paired_list: list[dict[str, Any]] = []
        if args.paired_target:
            pt_path = Path(args.paired_target).resolve()
            raw_pt = pt_path.read_text(encoding="utf-8")
            loaded_pt = (
                json.loads(raw_pt)
                if pt_path.suffix.lower() == ".json"
                else yaml.safe_load(raw_pt)
            )
            if isinstance(loaded_pt, list):
                paired_list = [dict(x) for x in loaded_pt]
            elif isinstance(loaded_pt, Mapping):
                paired_list = [dict(loaded_pt)]
        pkg = create_schema_from_documents(
            entity_id=args.doctype,
            sample_files=args.doc,
            paired_target_examples=paired_list,
            spine_entity_id=args.spine,
            context_set=args.context_set,
            domain=args.domain,
            source_priority=args.priority,
            auto_promote=args.install,
            install_skill=args.install,
            write_dir=args.write_dir,
            use_live_llm=True if args.live_llm else None,
        )
        print(
            json.dumps(
                {
                    "entity_id": pkg.entity_id,
                    "assay_passed": pkg.onboarding_result.assay_report.passed,
                    "promoted_revision_id": pkg.promoted_revision_id,
                    "installed_in_registry": pkg.installed_in_registry,
                    "written_files": list(pkg.written_files),
                    "fields": list(pkg.onboarding_result.entity_spec.fields.keys()),
                },
                indent=2,
            )
        )
        return 0

    manifest_obj, base_dir = load_manifest_file(args.manifest)
    if args.bridge_url:
        import httpx

        http_payload = UseCaseOnboardingRequest.model_validate(
            manifest_obj.model_dump(
                mode="json", exclude={"doctypes": {"__all__": {"sample_files"}}}
            )
        )
        url = args.bridge_url.rstrip("/") + "/v1/refinery/onboard-use-case"
        resp = httpx.post(
            url,
            json=http_payload.model_dump(mode="json"),
            timeout=120.0,
        )
        resp.raise_for_status()
        print(json.dumps(resp.json(), indent=2))
        return 0

    res = onboard_use_case(
        manifest_obj,
        base_dir=base_dir,
        write_dir=args.write_dir,
        use_live_llm=True if args.live_llm else None,
    )
    print(
        json.dumps(
            {
                "process_name": res.process_name,
                "installed_doctypes": list(res.installed_doctypes),
                "installed_skills": list(res.installed_skills),
                "active_revision_id": res.active_revision_id,
                "agent_card_skill_ids": list(res.agent_card_skill_ids),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
