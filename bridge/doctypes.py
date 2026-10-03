"""Doctype schema knowledge — the ONE place a doctype skill's ``schema.json`` is read.

A **doctype** skill folder declares its field shape in a ``bridge-schema`` asset
(default ``assets/schema.json``). That asset has two consumers, and they must not
learn to disagree:

1. ``bridge/extraction/gemini.py`` (Path B) renders the declared fields into the
   extraction prompt's installed-doctype catalog and pins their UNION as the
   request's structured-output ``response_schema`` (S0-arch-5).
2. ``bridge/patha.py`` (Path A) builds the **typed key-field model** an asserted
   extraction must satisfy (S0-arch-8) — so a party-with-agent asserting a malformed
   document is rejected at validation, for *every* installed doctype rather than the
   two a hand-maintained dict used to name.

Both read the same asset through the ``skill_registry`` seam, so installing a doctype
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

Module posture: stdlib + ``pydantic`` at module level only. The ``bridge.seams``
import inside :func:`doctype_schemas` is **lazy on purpose** —
``bridge/seams/local/__init__.py`` imports ``bridge.extraction.fixture`` at module
level, so a module-level seam import here would close an import cycle (and
``bridge/patha.py``, which imports this module, is itself imported from
``bridge/__init__.py``). Same precedent as ``bridge/edge/skills.py::loaded_skills``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

__all__ = [
    "DEFAULT_SCHEMA_ASSET",
    "declares_issuer",
    "doctype_fields",
    "doctype_schemas",
    "doctype_tables",
    "issuer_description",
    "key_field_model",
    "key_field_properties",
    "required_key_fields",
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
    return str(((schema.get("properties") or {}).get("issuer") or {}).get(
        "description", ""
    ) or "")


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
    """Read every installed doctype skill's ``bridge-schema`` asset through the seam.

    Half-installed policy (deliberately asymmetric): a declared-but-ABSENT asset is a
    legitimate registry state (a skill installed with no assets), so that doctype
    contributes its name only — one half-installed unrelated doctype must not kill all
    extraction. A PRESENT but unparsable asset is an unambiguous authoring bug and gets
    the repo's refuse-loud treatment (``ValueError`` naming the skill and the asset).
    """
    # Lazy seam import (mirrors ``bridge/edge/skills.py::loaded_skills``):
    # ``bridge.seams.local`` imports ``bridge.extraction.fixture`` at module level, so
    # a module-level import here would close an import cycle.
    from bridge.seams import Seam, get_adapter, resolve_mode

    registry = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
    schemas: dict[str, dict] = {}
    for meta in registry.list_skills():
        if meta.kind != "doctype":
            continue  # process skills carry no field schema
        rel = meta.schema_asset or DEFAULT_SCHEMA_ASSET
        try:
            raw = registry.read_asset(meta.name, rel)
        except KeyError:
            schemas[meta.name] = {}  # declared-but-absent asset: name-only, no raise
            continue
        try:
            schemas[meta.name] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"doctype skill {meta.name!r}: {rel} is not valid JSON"
            ) from exc
    return schemas
