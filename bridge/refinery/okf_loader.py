"""OKF v0.2 Semantic-Entity Markdown Loader & Serializer for the Schema Refinery.

Loads declarative doctype specifications from ``catalog/doctypes/*.okf.md`` into
typed ``OntologyEntitySpec`` and ``OntologyRevision`` contracts without hardcoding
skill or doctype names in Python code.

The OKF bundle is the **single source of truth** for a doctype's field names, types,
required flags, closed vocabularies and descriptions. A doctype skill's
``assets/schema.json`` is a *generated* artifact rendered from the bundle by
:func:`regenerate_skill_schemas` (``python -m bridge.refinery.okf_loader
--write-schemas``) and kept on disk only because other tooling (the GCS skill
registry sync) ships the skill folder as-is.

Three bundle keys carry JSON-Schema-only shape the frozen ``contract.refinery``
models do not model, so they ride next to the typed spec in :class:`OkfDoctypeBundle`
rather than inside it: ``fields.<name>.enum`` (closed string vocabulary),
``tables`` (row-shaped extraction blocks, S0-arch-10) and ``schema_version`` (the
asset's ``version`` marker).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bridge.doctypes import DEFAULT_SCHEMA_ASSET, entity_spec_to_json_schema
from bridge.sla import skills_root
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
EMPTY_CATALOG_ENV = "BRIDGE_EMPTY_CATALOG_ON_START"
OKF_BUNDLE_SUFFIX = ".okf.md"
BASELINE_REVISION_ID = "ont-rev-v1-1-baseline"
BASELINE_VERSION = "1.1.0"
EMPTY_REVISION_ID = "ont-rev-v0-empty"
EMPTY_VERSION = "0.0.0"
_BASELINE_TIMESTAMP = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)


def is_empty_catalog_mode(catalog_dir: Path | None = None) -> bool:
    """Return True when the catalog should start empty (zero pre-seeded doctypes)."""
    if catalog_dir is not None:
        return False
    if os.environ.get(OKF_DOCTYPES_DIR_ENV, "").strip():
        return False
    return os.environ.get(EMPTY_CATALOG_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


@dataclass(frozen=True)
class OkfDoctypeBundle:
    """A parsed ``.okf.md`` bundle: the typed spec plus its JSON-Schema-only extras."""

    spec: OntologyEntitySpec
    field_enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    schema_version: str | None = None

    @property
    def entity_id(self) -> str:
        return self.spec.entity_id


def _parse_enum(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(v) for v in raw if str(v).strip())


def _parse_tables(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize the ``tables`` frontmatter block.

    Authoring shape per table: ``description``, ``required`` (column names) and
    ``columns`` (column -> ``{type, enum?, description}``). Malformed entries are
    dropped rather than raised — a table without columns has no extraction value.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for tname, tspec in raw.items():
        if not isinstance(tspec, dict):
            continue
        columns_raw = tspec.get("columns") or {}
        if not isinstance(columns_raw, dict) or not columns_raw:
            continue
        columns: dict[str, dict[str, Any]] = {}
        for cname, cspec in columns_raw.items():
            cspec = cspec if isinstance(cspec, dict) else {}
            col: dict[str, Any] = {"type": str(cspec.get("type") or "string")}
            enum = _parse_enum(cspec.get("enum"))
            if enum:
                col["enum"] = list(enum)
            col["description"] = str(cspec.get("description") or "").strip()
            columns[str(cname)] = col
        required_raw = tspec.get("required") or []
        out[str(tname)] = {
            "description": str(tspec.get("description") or "").strip(),
            "required": [str(c) for c in required_raw if str(c) in columns]
            if isinstance(required_raw, list)
            else [],
            "columns": columns,
        }
    return out


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


def parse_okf_doctype_bundle(
    raw_text: str,
    *,
    default_entity_id: str = "",
) -> OkfDoctypeBundle:
    """Parse a single ``.okf.md`` bundle into an :class:`OkfDoctypeBundle`."""
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
    field_enums: dict[str, tuple[str, ...]] = {}
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
            enum = _parse_enum(fspec.get("enum"))
            if enum:
                field_enums[str(fname)] = enum

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

    spec = OntologyEntitySpec(
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
    schema_version = meta.get("schema_version")
    return OkfDoctypeBundle(
        spec=spec,
        field_enums=field_enums,
        tables=_parse_tables(meta.get("tables")),
        schema_version=str(schema_version).strip() if schema_version else None,
    )


def parse_okf_entity_markdown(
    raw_text: str,
    *,
    default_entity_id: str = "",
) -> OntologyEntitySpec:
    """Parse a single ``.okf.md`` bundle into an ``OntologyEntitySpec``."""
    return parse_okf_doctype_bundle(raw_text, default_entity_id=default_entity_id).spec


def load_okf_doctype_bundles(
    catalog_dir: Path | None = None,
) -> dict[str, OkfDoctypeBundle]:
    """Load all ``*.okf.md`` bundles (spec + schema extras) keyed by entity id."""
    if is_empty_catalog_mode(catalog_dir):
        return {}
    target_dir = resolve_okf_doctypes_dir(catalog_dir)
    if not target_dir.is_dir():
        return {}
    bundles: dict[str, OkfDoctypeBundle] = {}
    for path in sorted(target_dir.glob(f"*{OKF_BUNDLE_SUFFIX}")):
        stem_name = path.name[: -len(OKF_BUNDLE_SUFFIX)]
        raw_text = path.read_text(encoding="utf-8")
        bundle = parse_okf_doctype_bundle(raw_text, default_entity_id=stem_name)
        bundles[bundle.entity_id] = bundle
    return bundles


def load_okf_entity_specs(
    catalog_dir: Path | None = None,
) -> dict[str, OntologyEntitySpec]:
    """Load all ``*.okf.md`` doctype specifications from the catalog directory."""
    return {
        eid: bundle.spec
        for eid, bundle in load_okf_doctype_bundles(catalog_dir).items()
    }


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


def build_empty_ontology_revision() -> OntologyRevision:
    """Construct an empty initial ``OntologyRevision`` (0 doctype entities, 0 edges)."""
    digest = compute_ontology_hash({}, ())
    return OntologyRevision(
        revision_id=EMPTY_REVISION_ID,
        version=EMPTY_VERSION,
        parent_revision_id=None,
        state=OntologyRevisionState.ACTIVE,
        content_sha256=digest,
        summary="Empty initial ontology graph (awaiting use-case agent onboarding)",
        entities={},
        edges=(),
        promoted_proposal_ids=(),
        effective_from=_BASELINE_TIMESTAMP,
        effective_to=None,
        recorded_at=_BASELINE_TIMESTAMP,
    )


def build_baseline_ontology_revision(
    catalog_dir: Path | None = None,
) -> OntologyRevision:
    """Construct the baseline active ``OntologyRevision`` from ``catalog/doctypes/*.okf.md``."""
    if is_empty_catalog_mode(catalog_dir):
        return build_empty_ontology_revision()
    entities = load_okf_entity_specs(catalog_dir)
    if not entities:
        return build_empty_ontology_revision()
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
    field_enums: dict[str, tuple[str, ...]] | None = None,
    tables: dict[str, dict[str, Any]] | None = None,
    schema_version: str | None = None,
) -> str:
    """Serialize an ``OntologyEntitySpec`` into an ``.okf.md`` bundle with YAML frontmatter."""
    enums = field_enums or {}
    frontmatter: dict[str, Any] = {
        "okf_version": spec.okf_version,
        "entry_type": spec.entry_type,
        "entity_id": spec.entity_id,
        "context_set": spec.context_set,
        "revision_id": spec.revision_id,
        "source_priority": spec.source_priority,
        **({"schema_version": schema_version} if schema_version else {}),
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
                **({"enum": list(enums[fname])} if enums.get(fname) else {}),
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
        **({"tables": tables} if tables else {}),
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


def bundle_to_skill_schema(bundle: OkfDoctypeBundle) -> dict[str, Any]:
    """Render the ``assets/schema.json`` document a doctype skill derives from its bundle."""
    return entity_spec_to_json_schema(
        bundle.spec,
        field_enums=bundle.field_enums,
        tables=bundle.tables,
        schema_version=bundle.schema_version,
    )


def render_skill_schema_json(schema: dict[str, Any]) -> str:
    """The canonical on-disk serialization of a generated ``assets/schema.json``."""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def regenerate_skill_schemas(
    *,
    skills_dir: Path | None = None,
    catalog_dir: Path | None = None,
    schema_asset: str = DEFAULT_SCHEMA_ASSET,
    write: bool = True,
) -> dict[str, bool]:
    """Regenerate every ``skills/<entity_id>/<schema_asset>`` from its OKF bundle.

    Returns ``{entity_id: changed}`` for each bundle whose skill folder exists; a
    bundle without an installed skill folder is skipped (a catalog may describe
    doctypes a deployment does not extract). With ``write=False`` nothing is touched
    and ``changed`` reports whether the on-disk bytes differ (the ``--check`` mode).
    """
    root = skills_root(skills_dir)
    outcome: dict[str, bool] = {}
    for entity_id, bundle in sorted(load_okf_doctype_bundles(catalog_dir).items()):
        skill_dir = root / entity_id
        if not skill_dir.is_dir():
            continue
        target = skill_dir / schema_asset
        rendered = render_skill_schema_json(bundle_to_skill_schema(bundle))
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        changed = current != rendered
        outcome[entity_id] = changed
        if changed and write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
    return outcome


def main(argv: list[str] | None = None) -> int:
    """``python -m bridge.refinery.okf_loader --write-schemas [--check]``."""
    parser = argparse.ArgumentParser(
        prog="python -m bridge.refinery.okf_loader",
        description="Regenerate doctype skill schema.json assets from OKF bundles.",
    )
    parser.add_argument(
        "--write-schemas",
        action="store_true",
        help="render skills/<doctype>/assets/schema.json from catalog/doctypes",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when any on-disk schema.json is stale",
    )
    parser.add_argument("--skills-dir", type=Path, default=None)
    parser.add_argument("--catalog-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.write_schemas and not args.check:
        parser.print_help()
        return 2
    outcome = regenerate_skill_schemas(
        skills_dir=args.skills_dir,
        catalog_dir=args.catalog_dir,
        write=not args.check,
    )
    stale = sorted(eid for eid, changed in outcome.items() if changed)
    verb = "stale" if args.check else "rewrote"
    for eid in stale:
        print(f"{verb}: {eid}", file=sys.stderr)
    print(f"{len(outcome)} doctype schema(s) checked, {len(stale)} {verb}")
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    raise SystemExit(main())
