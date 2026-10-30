"""OKF v0.2 Semantic-Entity Markdown Loader & Serializer for the Schema Refinery.

Loads declarative doctype specifications from ``catalog/doctypes/*.okf.md`` into
typed ``OntologyEntitySpec`` and ``OntologyRevision`` contracts without hardcoding
skill or doctype names in Python code.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from contract.refinery import (
    ContrastiveDescription,
    OntologyEdge,
    OntologyEntitySpec,
    OntologyFieldSpec,
    OntologyRevision,
    OntologyRevisionState,
)

DEFAULT_OKF_DOCTYPES_DIR = Path(__file__).resolve().parents[2] / "catalog" / "doctypes"
OKF_DOCTYPES_DIR_ENV = "BRIDGE_OKF_DOCTYPES_DIR"
BASELINE_REVISION_ID = "ont-rev-v1-1-baseline"
BASELINE_VERSION = "1.1.0"
_BASELINE_TIMESTAMP = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)


def resolve_okf_doctypes_dir(catalog_dir: Path | None = None) -> Path:
    """Resolve the directory containing ``*.okf.md`` doctype bundles."""
    if catalog_dir is not None:
        return catalog_dir
    env_override = os.environ.get(OKF_DOCTYPES_DIR_ENV, "").strip()
    if env_override:
        return Path(env_override)
    return DEFAULT_OKF_DOCTYPES_DIR


def _split_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and Markdown body from an ``.okf.md`` file."""
    stripped = raw_text.lstrip()
    if not stripped.startswith("---"):
        return {}, raw_text
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, raw_text
    loaded = yaml.safe_load(parts[1])
    if not isinstance(loaded, dict):
        return {}, parts[2].strip()
    return loaded, parts[2].strip()


def _parse_contrastive_description(
    raw: Any, fallback: str = ""
) -> ContrastiveDescription:
    """Parse either a 3-part mapping or a plain string into ``ContrastiveDescription``."""
    if isinstance(raw, ContrastiveDescription):
        return raw
    if isinstance(raw, dict):
        return ContrastiveDescription(
            what_it_is=str(raw.get("what_it_is") or fallback).strip(),
            where_to_read=str(raw.get("where_to_read") or "").strip(),
            distinguish_from=str(raw.get("distinguish_from") or "").strip(),
        )
    if isinstance(raw, str) and raw.strip():
        return ContrastiveDescription(what_it_is=raw.strip())
    return ContrastiveDescription(what_it_is=fallback or "Declared document attribute.")


def parse_okf_entity_markdown(
    raw_text: str,
    *,
    default_entity_id: str = "",
) -> OntologyEntitySpec:
    """Parse a single ``.okf.md`` bundle into an ``OntologyEntitySpec``."""
    meta, _ = _split_frontmatter(raw_text)
    entity_id = str(meta.get("entity_id") or default_entity_id).strip()
    if not entity_id:
        raise ValueError(
            "OKF doctype bundle is missing required 'entity_id' frontmatter."
        )

    revision_id = str(meta.get("revision_id") or BASELINE_REVISION_ID).strip()
    entity_desc = _parse_contrastive_description(
        meta.get("entity_description"),
        fallback=f"Structured document entity {entity_id}.",
    )

    raw_fields = meta.get("fields") or {}
    parsed_fields: dict[str, OntologyFieldSpec] = {}
    if isinstance(raw_fields, dict):
        for fname, fspec in raw_fields.items():
            if not isinstance(fspec, dict):
                continue
            fdesc = _parse_contrastive_description(
                fspec.get("description"),
                fallback=f"Extracted {fname} attribute.",
            )
            synonyms_raw = fspec.get("synonyms") or []
            synonyms = tuple(str(s).strip() for s in synonyms_raw if str(s).strip())
            feeds_metric = fspec.get("feeds_metric")
            parsed_fields[str(fname)] = OntologyFieldSpec(
                name=str(fname),
                field_type=str(fspec.get("type") or "string"),
                required=bool(fspec.get("required", False)),
                synonyms=synonyms,
                description=fdesc,
                feeds_metric=str(feeds_metric).strip() if feeds_metric else None,
                monetary=bool(fspec.get("monetary", False)),
            )

    raw_edges = meta.get("edges") or []
    parsed_edges: list[OntologyEdge] = []
    if isinstance(raw_edges, list):
        for idx, edge_item in enumerate(raw_edges):
            if not isinstance(edge_item, dict):
                continue
            relation = str(edge_item.get("relation") or "").strip()
            target_id = str(edge_item.get("target_id") or "").strip()
            if not relation or not target_id:
                continue
            edge_id = str(
                edge_item.get("edge_id")
                or f"edge-{entity_id}-{relation.lower()}-{idx + 1}"
            )
            on_fields_raw = edge_item.get("on_fields") or []
            on_fields = tuple(str(f).strip() for f in on_fields_raw if str(f).strip())
            prio_raw = edge_item.get("priority_rank")
            prio_rank = int(prio_raw) if prio_raw is not None else None
            parsed_edges.append(
                OntologyEdge(
                    edge_id=edge_id,
                    source_id=str(edge_item.get("source_id") or entity_id),
                    relation=relation,  # type: ignore[arg-type]
                    target_id=target_id,
                    priority_rank=prio_rank,
                    on_fields=on_fields,
                    metric_operation=(
                        str(edge_item["metric_operation"])
                        if edge_item.get("metric_operation")
                        else None
                    ),
                    condition_expr=(
                        str(edge_item["condition_expr"])
                        if edge_item.get("condition_expr")
                        else None
                    ),
                    effective_from_revision=str(
                        edge_item.get("effective_from_revision") or revision_id
                    ),
                )
            )

    aliases_raw = meta.get("aliases") or []
    aliases = tuple(str(a).strip() for a in aliases_raw if str(a).strip())

    return OntologyEntitySpec(
        entity_id=entity_id,
        okf_version=str(meta.get("okf_version") or "0.2"),
        entry_type=str(meta.get("entry_type") or "semantic-entity"),
        context_set=str(meta.get("context_set") or "enterprise-document-intake"),
        revision_id=revision_id,
        source_priority=int(meta.get("source_priority", 20)),
        aliases=aliases,
        declares_issuer=bool(meta.get("declares_issuer", False)),
        issuer_description=str(
            meta.get("issuer_description") or "canonicalized issuer key"
        ),
        entity_description=entity_desc,
        fields=parsed_fields,
        edges=tuple(parsed_edges),
    )


def load_okf_entity_specs(
    catalog_dir: Path | None = None,
) -> dict[str, OntologyEntitySpec]:
    """Load all ``*.okf.md`` doctype specifications from the catalog directory."""
    target_dir = resolve_okf_doctypes_dir(catalog_dir)
    if not target_dir.is_dir():
        return {}
    specs: dict[str, OntologyEntitySpec] = {}
    for path in sorted(target_dir.glob("*.okf.md")):
        stem_name = path.name[: -len(".okf.md")]
        raw_text = path.read_text(encoding="utf-8")
        spec = parse_okf_entity_markdown(raw_text, default_entity_id=stem_name)
        specs[spec.entity_id] = spec
    return specs


def compute_ontology_hash(
    entities: dict[str, OntologyEntitySpec],
    edges: tuple[OntologyEdge, ...],
) -> str:
    """Compute a deterministic SHA-256 content digest across entities and graph edges."""
    canonical_payload = {
        "entities": {
            k: entities[k].model_dump(mode="json") for k in sorted(entities.keys())
        },
        "edges": [e.model_dump(mode="json") for e in edges],
    }
    encoded = json.dumps(
        canonical_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_baseline_ontology_revision(
    catalog_dir: Path | None = None,
) -> OntologyRevision:
    """Construct the baseline active ``OntologyRevision`` from ``catalog/doctypes/*.okf.md``."""
    entities = load_okf_entity_specs(catalog_dir)
    all_edges: list[OntologyEdge] = []
    seen_edge_ids: set[str] = set()
    for entity_id in sorted(entities.keys()):
        for edge in entities[entity_id].edges:
            if edge.edge_id not in seen_edge_ids:
                seen_edge_ids.add(edge.edge_id)
                all_edges.append(edge)
    edges_tuple = tuple(all_edges)
    digest = compute_ontology_hash(entities, edges_tuple)
    return OntologyRevision(
        revision_id=BASELINE_REVISION_ID,
        version=BASELINE_VERSION,
        parent_revision_id=None,
        state=OntologyRevisionState.ACTIVE,
        content_sha256=digest,
        summary=(
            "Baseline OKF v0.2 ontology graph across lending, appraisal, insurance, "
            "and KYC doctypes"
        ),
        entities=entities,
        edges=edges_tuple,
        promoted_proposal_ids=(),
        effective_from=_BASELINE_TIMESTAMP,
        effective_to=None,
        recorded_at=_BASELINE_TIMESTAMP,
    )


def render_okf_markdown(
    spec: OntologyEntitySpec,
    *,
    body: str | None = None,
) -> str:
    """Serialize an ``OntologyEntitySpec`` into an ``.okf.md`` bundle with YAML frontmatter."""
    frontmatter: dict[str, Any] = {
        "okf_version": spec.okf_version,
        "entry_type": spec.entry_type,
        "entity_id": spec.entity_id,
        "context_set": spec.context_set,
        "revision_id": spec.revision_id,
        "source_priority": spec.source_priority,
        "aliases": list(spec.aliases),
        "declares_issuer": spec.declares_issuer,
        "issuer_description": spec.issuer_description,
        "entity_description": {
            "what_it_is": spec.entity_description.what_it_is,
            "where_to_read": spec.entity_description.where_to_read,
            "distinguish_from": spec.entity_description.distinguish_from,
        },
        "fields": {
            fname: {
                "type": fspec.field_type,
                "required": fspec.required,
                "monetary": fspec.monetary,
                **({"feeds_metric": fspec.feeds_metric} if fspec.feeds_metric else {}),
                "synonyms": list(fspec.synonyms),
                "description": {
                    "what_it_is": fspec.description.what_it_is,
                    "where_to_read": fspec.description.where_to_read,
                    "distinguish_from": fspec.description.distinguish_from,
                },
            }
            for fname, fspec in spec.fields.items()
        },
        "edges": [
            {
                "edge_id": edge.edge_id,
                "relation": edge.relation,
                "target_id": edge.target_id,
                **(
                    {"priority_rank": edge.priority_rank}
                    if edge.priority_rank is not None
                    else {}
                ),
                "on_fields": list(edge.on_fields),
                **(
                    {"metric_operation": edge.metric_operation}
                    if edge.metric_operation
                    else {}
                ),
                **(
                    {"condition_expr": edge.condition_expr}
                    if edge.condition_expr
                    else {}
                ),
            }
            for edge in spec.edges
        ],
    }
    yaml_block = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True
    ).strip()
    markdown_body = (
        body.strip()
        if body
        else f"# {spec.entity_id}\n\n{spec.entity_description.render()}"
    )
    return f"---\n{yaml_block}\n---\n{markdown_body}\n"
