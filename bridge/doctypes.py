"""Doctype schema knowledge — the ONE place a doctype skill's ``schema.json`` is read.

A **doctype** skill folder declares its field shape in a ``bridge-schema`` asset
(default ``assets/schema.json``). When the doctype also has a
``catalog/doctypes/<name>.okf.md`` bundle, that bundle is canonical and the asset is
a generated copy of it (``bridge/refinery/okf_loader.py::regenerate_skill_schemas``);
:func:`doctype_schemas` then renders the schema from the bundle and never reads the
asset. Either way the schema has two consumers, and they must not learn to disagree:

1. ``bridge/extraction/gemini.py`` (Path B) renders the declared fields into the
   extraction prompt's installed-doctype catalog and pins their UNION as the
   request's structured-output ``response_schema`` (S0-arch-5).
2. ``bridge/patha.py`` (Path A) builds the **typed key-field model** an asserted
   extraction must satisfy (S0-arch-8) — so a party-with-agent asserting a malformed
   document is rejected at validation, for *every* installed doctype rather than the
   two a hand-maintained dict used to name.

Both read the same asset through the ``skill_registry`` port, so installing a doctype
skill folder gives that doctype extraction **and** validation with no code change and
no redeploy — the no-redeploy property the registry already promises.

**Pydantic, not ``jsonschema``.** The typed model is built with
``pydantic.create_model`` from ``properties.key_fields.{properties,required}``:
required props are mandatory, declared-but-optional props default to ``None``, and
``extra="forbid"`` rejects an undeclared key. That keeps the raised type
``pydantic.ValidationError`` (the edge maps exactly that to a ``schema_invalid``
reject) and adds no dependency.

**Every declared property is typed ``str``**, whatever JSON-Schema ``type`` it
declares: the wire shape is ``ExtractionFields.key_fields: dict[str, str]``, so a
schema declaring ``"type": "number"`` still arrives as a string. Coercion is a
values concern (``assets/validation.yaml`` rules), not a shape concern. A property
declaring an ``enum`` of strings is typed as that closed ``Literal`` — a closed
vocabulary (a rate ``frequency``) is shape, not value.

**Tables (S0-arch-10).** A doctype may also declare ``properties.tables``: each
property an ``array`` whose ``items`` is an object of text columns (the
``benefits-quote`` rate sheet). :func:`tables_model` builds the matching typed model
for ``ExtractionFields.tables`` — declared tables optional, each row an
``extra="forbid"`` model with the ``items.required`` columns mandatory — by the same
rules as the key-field model.

Module posture: stdlib + ``pydantic`` at module level only. The ``bridge.ports``
import inside :func:`doctype_schemas` is **lazy on purpose** —
``bridge/ports/local/__init__.py`` imports ``bridge.extraction.fixture`` at module
level, so a module-level port import here would close an import cycle (and
``bridge/patha.py``, which imports this module, is itself imported from
``bridge/__init__.py``). Same precedent as ``bridge/edge/skills.py::loaded_skills``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model

__all__ = [
    "DEFAULT_SCHEMA_ASSET",
    "declares_issuer",
    "doctype_description",
    "doctype_fields",
    "doctype_schemas",
    "doctype_tables",
    "entity_spec_to_json_schema",
    "issuer_description",
    "key_field_model",
    "key_field_properties",
    "required_key_fields",
    "slice_ontology_for_extraction",
    "table_properties",
    "table_spec",
    "tables_model",
]

# The skill-relative schema pointer a doctype skill declares as ``bridge-schema``;
# also the default when a doctype skill declares none (mirrors ``bridge/sla.py``'s
# pointer-or-default policy for ``bridge-policy``).
DEFAULT_SCHEMA_ASSET = "assets/schema.json"


def key_field_properties(schema: dict) -> dict:
    """The ``key_fields.properties`` block of a doctype schema (``{}`` when absent)."""
    if not isinstance(schema, dict):
        return {}
    key_fields = (schema.get("properties") or {}).get("key_fields") or {}
    props = key_fields.get("properties") or {}
    return props if isinstance(props, dict) else {}


def required_key_fields(schema: dict) -> tuple[str, ...]:
    """The ``key_fields.required`` list of a doctype schema (``()`` when absent).

    Defensive about authoring shape: a non-list ``required`` yields ``()`` rather than
    a crash — an absent/garbled required list means "nothing is mandatory", never
    "reject everything".
    """
    if not isinstance(schema, dict):
        return ()
    key_fields = (schema.get("properties") or {}).get("key_fields") or {}
    required = key_fields.get("required") if isinstance(key_fields, dict) else None
    if not isinstance(required, list):
        return ()
    return tuple(str(name) for name in required)


def table_properties(schema: dict) -> dict[str, dict]:
    """(table name -> ``items.properties`` column block) of a doctype schema.

    Only well-formed tables count: an ``array`` property whose ``items`` declares at
    least one column. Anything else is ignored (``{}`` when the schema declares none).
    """
    if not isinstance(schema, dict):
        return {}
    tables = (schema.get("properties") or {}).get("tables") or {}
    props = tables.get("properties") if isinstance(tables, dict) else None
    if not isinstance(props, dict):
        return {}
    out: dict[str, dict] = {}
    for name, spec in props.items():
        items = (spec or {}).get("items") if isinstance(spec, dict) else None
        columns = (items or {}).get("properties") if isinstance(items, dict) else None
        if isinstance(name, str) and isinstance(columns, dict) and columns:
            out[name] = columns
    return out


def table_spec(schema: dict, table: str) -> dict:
    """The raw ``properties.tables.properties.{table}`` block (``{}`` when absent)."""
    if not isinstance(schema, dict):
        return {}
    tables = (schema.get("properties") or {}).get("tables") or {}
    props = tables.get("properties") if isinstance(tables, dict) else None
    spec = props.get(table) if isinstance(props, dict) else None
    return spec if isinstance(spec, dict) else {}


def _table_required(schema: dict, table: str) -> set[str]:
    required = (table_spec(schema, table).get("items") or {}).get("required")
    return {str(c) for c in required} if isinstance(required, list) else set()


def doctype_tables(schema: dict) -> dict[str, dict[str, str]]:
    """(table name -> (column -> description)) — the prompt-catalog view of tables."""
    return {
        table: {
            col: str((spec or {}).get("description", "") or "")
            for col, spec in columns.items()
        }
        for table, columns in table_properties(schema).items()
    }


def doctype_fields(schema: dict) -> dict[str, str]:
    """(field name -> description) from a doctype schema's ``key_fields`` properties.

    The description is the schema property's own ``description`` (``""`` when the
    property declares none) — authoring order is preserved so the rendered catalog
    reads like the authored schema.
    """
    return {
        name: str((spec or {}).get("description", "") or "")
        for name, spec in key_field_properties(schema).items()
    }


def declares_issuer(schema: dict) -> bool:
    """Whether a doctype schema declares a top-level ``issuer`` (utility-bill does)."""
    if not isinstance(schema, dict):
        return False
    return "issuer" in (schema.get("properties") or {})


def issuer_description(schema: dict) -> str:
    """Return a human-readable description of the expected document issuer."""
    return str(
        ((schema.get("properties") or {}).get("issuer") or {}).get("description", "")
        or ""
    )


def doctype_description(schema: dict) -> str:
    """Return the top-level contrastive entity description of a doctype schema."""
    if not isinstance(schema, dict):
        return ""
    return str(schema.get("description", "") or "").strip()


def entity_spec_to_json_schema(
    spec: Any,
    *,
    field_enums: dict[str, tuple[str, ...]] | None = None,
    tables: dict[str, dict[str, Any]] | None = None,
    schema_version: str | None = None,
) -> dict[str, Any]:
    """Convert an ``OntologyEntitySpec`` into a doctype JSON Schema dictionary.

    This is the ONE rendering of an OKF bundle as ``assets/schema.json``: the loader
    uses it for every doctype whose bundle exists, and the generator writes its
    output to disk, so both sides agree by construction. ``field_enums`` /
    ``tables`` / ``schema_version`` are the bundle's JSON-Schema-only extras (see
    ``bridge/refinery/okf_loader.py``); key order is fixed so the serialized file
    is deterministic.
    """
    enums = field_enums or {}
    props: dict[str, Any] = {
        "doctype": {"type": "string", "enum": [spec.entity_id]},
    }
    required_root: list[str] = ["doctype", "key_fields"]
    if spec.declares_issuer:
        props["issuer"] = {
            "type": "string",
            "description": spec.issuer_description or "canonicalized issuer key",
        }
        required_root.insert(1, "issuer")
    field_props: dict[str, Any] = {}
    required_fields: list[str] = []
    for fname, fspec in spec.fields.items():
        entry: dict[str, Any] = {"type": fspec.field_type}
        if enums.get(fname):
            entry["enum"] = list(enums[fname])
        entry["description"] = fspec.description.render()
        field_props[fname] = entry
        if fspec.required:
            required_fields.append(fname)
    props["key_fields"] = {
        "type": "object",
        "required": required_fields,
        "properties": field_props,
    }
    if tables:
        props["tables"] = {
            "type": "object",
            "properties": {
                tname: {
                    "type": "array",
                    "description": str(tspec.get("description") or ""),
                    "items": {
                        "type": "object",
                        "required": list(tspec.get("required") or []),
                        "properties": dict(tspec.get("columns") or {}),
                    },
                }
                for tname, tspec in tables.items()
            },
        }
    return {
        **({"version": schema_version} if schema_version else {}),
        "type": "object",
        "description": spec.entity_description.render(),
        "x_okf_revision_id": spec.revision_id,
        "required": required_root,
        "properties": props,
    }


def _enrich_schema_with_okf_spec(
    schema: dict[str, Any],
    okf_spec: Any | None,
    fallback_skill_description: str = "",
) -> dict[str, Any]:
    """Enrich a parsed ``assets/schema.json`` dict with OKF contrastive descriptions."""
    if not schema:
        return schema
    enriched = dict(schema)
    if okf_spec is not None:
        enriched["description"] = okf_spec.entity_description.render()
        enriched["x_okf_revision_id"] = okf_spec.revision_id
        root_props = dict(enriched.get("properties") or {})
        kf_block = dict(root_props.get("key_fields") or {})
        kf_props = dict(kf_block.get("properties") or {})
        for fname, fspec in okf_spec.fields.items():
            if fname in kf_props and isinstance(kf_props[fname], dict):
                field_entry = dict(kf_props[fname])
                field_entry["description"] = fspec.description.render()
                kf_props[fname] = field_entry
            elif fname not in kf_props:
                kf_props[fname] = {
                    "type": fspec.field_type,
                    "description": fspec.description.render(),
                }
        if kf_props:
            kf_block["properties"] = kf_props
            root_props["key_fields"] = kf_block
            enriched["properties"] = root_props
    elif not enriched.get("description") and fallback_skill_description.strip():
        enriched["description"] = fallback_skill_description.strip()
    return enriched


def _model_name(doctype: str) -> str:
    """``gov-id`` -> ``GovIdKeyFields`` — a readable name in the ValidationError repr."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", doctype) if p]
    return "".join(p.title() for p in parts) + "KeyFields" if parts else "KeyFields"


def _field_type(spec: object) -> object:
    """``str``, or the closed ``Literal`` a string ``enum`` declares."""
    values = spec.get("enum") if isinstance(spec, dict) else None
    if isinstance(values, list) and values and all(isinstance(v, str) for v in values):
        return Literal[tuple(values)]
    return str


def _text_model(name: str, props: dict, required: set[str]) -> type[BaseModel] | None:
    """An ``extra="forbid"`` model of text (or closed-enum) fields; ``None`` if fieldless."""
    # A leading underscore is a pydantic private attribute, not a field — a schema
    # declaring one gets it ignored rather than crashing model construction.
    names = [n for n in props if isinstance(n, str) and n and not n.startswith("_")]
    if not names:
        return None
    definitions: dict[str, object] = {}
    for field in names:
        typ = _field_type(props[field])
        definitions[field] = (typ, ...) if field in required else (typ | None, None)
    return create_model(
        name,
        # extra="forbid": an undeclared key is an ``extra_forbidden`` error — the same
        # posture the hand-written contract models carried. Empty protected_namespaces
        # so a legitimately-named ``model_*`` field does not warn.
        __config__=ConfigDict(extra="forbid", protected_namespaces=()),
        **definitions,
    )


@lru_cache(maxsize=64)
def _key_field_model_cached(doctype: str, schema_json: str) -> type[BaseModel] | None:
    """Build (once per distinct schema CONTENT) the typed key-field model.

    Caching is keyed by the schema's serialized content, **not** by the registry read:
    S0-arch-5 forbids caching the read itself (``BRIDGE_SKILLS_DIR`` changes at
    runtime and ``get_adapter`` builds a fresh adapter per call, so a cached read
    would break live doctype-add). A pure function of ``(doctype, schema content)``
    is safe — an installed or edited schema simply produces a different key. Do not
    "fix" this back into a cache of :func:`doctype_schemas`.
    """
    schema = json.loads(schema_json)
    return _text_model(
        _model_name(doctype),
        key_field_properties(schema),
        set(required_key_fields(schema)),
    )


@lru_cache(maxsize=64)
def _tables_model_cached(doctype: str, schema_json: str) -> type[BaseModel]:
    """Build (once per schema CONTENT) the typed ``tables`` model — same cache rule."""
    schema = json.loads(schema_json)
    base = _model_name(doctype).removesuffix("KeyFields")
    definitions: dict[str, object] = {}
    for table, columns in table_properties(schema).items():
        row = _text_model(
            f"{base}{_model_name(table).removesuffix('KeyFields')}Row",
            columns,
            _table_required(schema, table),
        )
        if row is not None:
            definitions[table] = (list[row] | None, None)
    return create_model(
        f"{base}Tables",
        # A doctype declaring no tables gets a field-less forbid model: an asserted
        # table it never declared is rejected, an empty ``tables`` passes.
        __config__=ConfigDict(extra="forbid", protected_namespaces=()),
        **definitions,
    )


def key_field_model(doctype: str, schema: dict) -> type[BaseModel] | None:
    """The typed ``key_fields`` model for ``doctype``, or ``None`` when it declares none.

    ``None`` means "nothing to enforce" (a half-installed skill whose schema asset is
    absent, or a field-less doctype) — the caller skips the typed check; it is never a
    reason to reject.
    """
    if not isinstance(schema, dict):
        return None
    return _key_field_model_cached(doctype, json.dumps(schema, sort_keys=True))


def tables_model(doctype: str, schema: dict) -> type[BaseModel]:
    """The typed ``ExtractionFields.tables`` model for ``doctype``.

    Declared tables are optional (a quote may print no rate grid); within a present
    table every row must carry the ``items.required`` columns and nothing undeclared,
    and an enum column accepts only its declared values. A doctype that declares no
    tables yields a field-less model, so any asserted table is ``extra_forbidden``.
    """
    return _tables_model_cached(
        doctype, json.dumps(schema if isinstance(schema, dict) else {}, sort_keys=True)
    )


def doctype_schemas() -> dict[str, dict]:
    """Read every installed doctype skill's schema — OKF bundle first, asset second.

    A doctype whose ``catalog/doctypes/<name>.okf.md`` bundle exists gets its schema
    rendered from that bundle (``entity_spec_to_json_schema``), never from the
    skill's ``assets/schema.json`` — the asset is a generated copy of the same
    bundle (``python -m bridge.refinery.okf_loader --write-schemas``) and
    ``tests/test_doctype_consistency.py`` holds the two byte-identical. When an
    ontology revision beyond baseline is active its entity spec replaces the file's
    field set; the bundle's enum/table extras still apply.

    Doctypes without a bundle keep the asset path. Half-installed policy
    (deliberately asymmetric): a declared-but-ABSENT asset is a legitimate registry
    state (a skill installed with no assets), so that doctype contributes its name
    only — one half-installed unrelated doctype must not kill all extraction. A
    PRESENT but unparsable asset is an unambiguous authoring bug and gets the repo's
    refuse-loud treatment (``ValueError`` naming the skill and the asset).
    """
    # Lazy port import (mirrors ``bridge/edge/skills.py::loaded_skills``):
    # ``bridge.ports.local`` imports ``bridge.extraction.fixture`` at module level, so
    # a module-level import here would close an import cycle.
    import os

    from bridge.ports import Port, get_adapter, resolve_mode
    from bridge.refinery.okf_loader import (
        BASELINE_REVISION_ID,
        load_okf_doctype_bundles,
    )

    bundles = load_okf_doctype_bundles()
    okf_specs: dict[str, Any] = {eid: b.spec for eid, b in bundles.items()}
    active_revision_id = BASELINE_REVISION_ID
    try:
        from bridge.knowledge_catalog import knowledge_catalog_from_env

        active_rev = knowledge_catalog_from_env().get_active_ontology_revision()
        if active_rev is not None and active_rev.entities:
            okf_specs = dict(active_rev.entities)
            active_revision_id = active_rev.revision_id
    except Exception:
        pass

    registry = get_adapter(Port.skill_registry, resolve_mode(Port.skill_registry))
    schemas: dict[str, dict] = {}
    for meta in registry.list_skills():
        if meta.kind != "doctype":
            continue  # process skills carry no field schema
        bundle = bundles.get(meta.name)
        if bundle is not None:
            schemas[meta.name] = entity_spec_to_json_schema(
                okf_specs.get(meta.name, bundle.spec),
                field_enums=bundle.field_enums,
                tables=bundle.tables,
                schema_version=bundle.schema_version,
            )
            continue
        rel = meta.schema_asset or DEFAULT_SCHEMA_ASSET
        try:
            raw = registry.read_asset(meta.name, rel)
        except KeyError:
            schemas[meta.name] = {}  # declared-but-absent asset: name-only, no raise
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"doctype skill {meta.name!r}: {rel} is not valid JSON"
            ) from exc
        if isinstance(parsed, dict) and parsed:
            schemas[meta.name] = _enrich_schema_with_okf_spec(
                parsed,
                okf_specs.get(meta.name),
                fallback_skill_description=getattr(meta, "description", "") or "",
            )
        else:
            schemas[meta.name] = parsed

    # Include newly split/promoted child entities when an ontology revision beyond
    # baseline is active and BRIDGE_SKILLS_DIR is not isolated to a test tempdir.
    if (
        active_revision_id != BASELINE_REVISION_ID
        and not os.environ.get("BRIDGE_SKILLS_DIR", "").strip()
    ):
        for entity_id, spec in sorted(okf_specs.items()):
            if entity_id not in schemas:
                schemas[entity_id] = entity_spec_to_json_schema(spec)
    return schemas


_SLICE_NEIGHBORHOOD_RELATIONS: frozenset[str] = frozenset(
    {
        "SUPERSEDES",
        "SPLIT_FROM",
        "DISTINCT_FROM",
        "ALIASED_FROM",
        "MERGED_INTO",
        "REQUIRES_CORROBORATION",
    }
)


def _resolve_seed_doctype(
    raw_name: str,
    base_schemas: dict[str, dict],
    entities: dict[str, Any],
) -> str:
    """Resolve a target or candidate doctype name or OKF alias to its canonical entity ID."""
    cleaned = raw_name.strip()
    if cleaned in base_schemas or cleaned in entities:
        return cleaned
    lowered = cleaned.lower()
    for eid, spec in entities.items():
        if eid.lower() == lowered or lowered in (a.lower() for a in spec.aliases):
            return eid
    return cleaned


def slice_ontology_for_extraction(
    target_doctype: str | None = None,
    candidate_doctypes: Sequence[str] | None = None,
    revision: Any | None = None,
    *,
    context_set: str | None = None,
    schemas: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Return the target-scoped doctype schema slice (spine + 1-hop ontology neighborhood).

    When ``target_doctype``, ``candidate_doctypes``, and ``context_set`` are all empty
    or ``None``, returns the full ``schemas`` catalog unchanged. Otherwise selects the
    seed doctypes plus all 1-hop ontology neighbors connected by ``[:SUPERSEDES]``,
    ``[:SPLIT_FROM]``, ``[:DISTINCT_FROM]``, ``[:ALIASED_FROM]``, ``[:MERGED_INTO]``, or
    ``[:REQUIRES_CORROBORATION]`` edges on ``revision``.
    """
    base_schemas = dict(schemas) if schemas is not None else doctype_schemas()
    has_target = bool(target_doctype and target_doctype.strip())
    has_candidates = bool(
        candidate_doctypes and any(str(c).strip() for c in candidate_doctypes)
    )
    has_context = bool(context_set and context_set.strip())
    if not has_target and not has_candidates and not has_context:
        return base_schemas

    active_rev = revision
    if active_rev is None:
        try:
            from bridge.knowledge_catalog import knowledge_catalog_from_env

            active_rev = knowledge_catalog_from_env().get_active_ontology_revision()
        except Exception:
            active_rev = None
    if active_rev is None:
        try:
            from bridge.refinery.okf_loader import build_baseline_ontology_revision

            active_rev = build_baseline_ontology_revision()
        except Exception:
            active_rev = None

    rev_entities: dict[str, Any] = (
        dict(active_rev.entities) if active_rev is not None else {}
    )
    rev_edges: Sequence[Any] = tuple(active_rev.edges) if active_rev is not None else ()

    seeds: set[str] = set()
    if has_target and target_doctype is not None:
        seeds.add(_resolve_seed_doctype(target_doctype, base_schemas, rev_entities))
    if has_candidates and candidate_doctypes is not None:
        for cand in candidate_doctypes:
            if str(cand).strip():
                seeds.add(_resolve_seed_doctype(str(cand), base_schemas, rev_entities))
    if has_context and context_set is not None:
        ctx_clean = context_set.strip()
        for eid, spec in rev_entities.items():
            if getattr(spec, "context_set", "") == ctx_clean:
                seeds.add(eid)

    selected = set(seeds)
    # Expand 1-hop shared spine & spoke neighborhood across ontology graph edges
    for edge in rev_edges:
        if getattr(edge, "relation", "") not in _SLICE_NEIGHBORHOOD_RELATIONS:
            continue
        src = getattr(edge, "source_id", "")
        tgt = getattr(edge, "target_id", "")
        if src in seeds and tgt:
            selected.add(_resolve_seed_doctype(tgt, base_schemas, rev_entities))
        elif tgt in seeds and src:
            selected.add(_resolve_seed_doctype(src, base_schemas, rev_entities))

    sliced: dict[str, dict] = {}
    for eid in sorted(selected):
        if eid in base_schemas:
            if revision is not None and eid in rev_entities:
                sliced[eid] = _enrich_schema_with_okf_spec(
                    base_schemas[eid],
                    rev_entities[eid],
                )
            else:
                sliced[eid] = base_schemas[eid]
        elif eid in rev_entities:
            sliced[eid] = entity_spec_to_json_schema(rev_entities[eid])
    return sliced
