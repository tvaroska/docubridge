"""P9-2: ``catalog/doctypes/*.okf.md`` is the single source of truth for doctype schemas.

Every doctype skill with an OKF bundle must carry an ``assets/schema.json`` that is
exactly what the bundle renders to (names, types, required flags, enums, tables,
descriptions). Regenerate with ``python -m bridge.refinery.okf_loader --write-schemas``.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest

from bridge.doctypes import (
    DEFAULT_SCHEMA_ASSET,
    doctype_schemas,
    key_field_properties,
    required_key_fields,
)
from bridge.edge.skills import skill_meta_from_dir
from bridge.refinery.okf_loader import (
    OKF_BUNDLE_SUFFIX,
    bundle_to_skill_schema,
    load_okf_doctype_bundles,
    parse_okf_doctype_bundle,
    regenerate_skill_schemas,
    render_okf_markdown,
    render_skill_schema_json,
)
from bridge.sla import SKILLS_DIR

REPO = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO / "catalog" / "doctypes"

# Doctype skills that declare a schema asset but are deliberately not OKF-governed.
# Empty on purpose: every installed doctype skill is expected to have a bundle. Add
# an entry only with a reason string.
OKF_EXEMPT_DOCTYPE_SKILLS: dict[str, str] = {}


def _bundle_names() -> list[str]:
    return sorted(
        p.name[: -len(OKF_BUNDLE_SUFFIX)] for p in CATALOG_DIR.glob("*.okf.md")
    )


def _doctype_skills_with_schema() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        meta = skill_meta_from_dir(skill_dir)
        if meta.kind != "doctype":
            continue
        asset = skill_dir / (meta.schema_asset or DEFAULT_SCHEMA_ASSET)
        if asset.is_file():
            out[meta.name] = asset
    return out


def _pretty(doc: dict) -> list[str]:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False).splitlines()


def _shape(schema: dict) -> dict:
    """The structural projection the extraction + Path-A consumers depend on."""
    props = key_field_properties(schema)
    return {
        "required": list(schema.get("required") or []),
        "issuer": "issuer" in (schema.get("properties") or {}),
        "key_fields": {
            name: {
                "type": spec.get("type"),
                "enum": spec.get("enum"),
                "required": name in required_key_fields(schema),
            }
            for name, spec in props.items()
        },
        "tables": (schema.get("properties") or {}).get("tables"),
    }


@pytest.mark.parametrize("doctype", _bundle_names())
def test_schema_asset_equals_okf_rendering(doctype: str) -> None:
    skill_dir = SKILLS_DIR / doctype
    if not skill_dir.is_dir():
        pytest.skip(f"{doctype}: OKF bundle without an installed skill folder")
    bundle = load_okf_doctype_bundles(CATALOG_DIR)[doctype]
    expected = bundle_to_skill_schema(bundle)
    asset = skill_dir / DEFAULT_SCHEMA_ASSET
    actual = json.loads(asset.read_text(encoding="utf-8"))

    shape_diff = "\n".join(
        difflib.unified_diff(
            _pretty(_shape(expected)),
            _pretty(_shape(actual)),
            fromfile=f"catalog/doctypes/{doctype}{OKF_BUNDLE_SUFFIX} (rendered)",
            tofile=f"skills/{doctype}/{DEFAULT_SCHEMA_ASSET}",
            lineterm="",
        )
    )
    assert _shape(actual) == _shape(expected), (
        f"{doctype}: schema.json drifted from its OKF bundle "
        f"(fields / types / required / enums / tables):\n{shape_diff}"
    )

    full_diff = "\n".join(
        difflib.unified_diff(
            _pretty(expected),
            _pretty(actual),
            fromfile=f"catalog/doctypes/{doctype}{OKF_BUNDLE_SUFFIX} (rendered)",
            tofile=f"skills/{doctype}/{DEFAULT_SCHEMA_ASSET}",
            lineterm="",
        )
    )
    assert actual == expected, (
        f"{doctype}: schema.json is not the OKF rendering — run "
        f"`python -m bridge.refinery.okf_loader --write-schemas`:\n{full_diff}"
    )
    assert asset.read_text(encoding="utf-8") == render_skill_schema_json(expected), (
        f"{doctype}: schema.json bytes differ from the canonical serialization — run "
        "`python -m bridge.refinery.okf_loader --write-schemas`"
    )


def test_every_doctype_skill_with_a_schema_has_an_okf_bundle() -> None:
    bundles = set(_bundle_names())
    missing = {
        name
        for name in _doctype_skills_with_schema()
        if name not in bundles and name not in OKF_EXEMPT_DOCTYPE_SKILLS
    }
    assert not missing, (
        f"doctype skills declaring a schema without catalog/doctypes/<name>"
        f"{OKF_BUNDLE_SUFFIX}: {sorted(missing)} (add a bundle, or allow-list with a "
        "reason in OKF_EXEMPT_DOCTYPE_SKILLS)"
    )
    stale_exemptions = set(OKF_EXEMPT_DOCTYPE_SKILLS) & bundles
    assert not stale_exemptions, f"exempt skills now have bundles: {stale_exemptions}"


def test_regenerate_is_a_no_op_on_the_committed_tree() -> None:
    outcome = regenerate_skill_schemas(
        skills_dir=SKILLS_DIR, catalog_dir=CATALOG_DIR, write=False
    )
    assert outcome, "no OKF bundle matched an installed skill folder"
    assert not [eid for eid, changed in outcome.items() if changed]


def test_regenerate_writes_only_matching_skill_folders(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "alpha-doc").mkdir(parents=True)
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    bundle_text = (
        "---\n"
        "entity_id: alpha-doc\n"
        "declares_issuer: false\n"
        'entity_description: {what_it_is: "Alpha.", where_to_read: "P1", '
        'distinguish_from: "Beta."}\n'
        "fields:\n"
        "  amount:\n"
        "    type: string\n"
        "    required: true\n"
        "    enum: [LOW, HIGH]\n"
        '    description: {what_it_is: "Amt.", where_to_read: "P1", '
        'distinguish_from: "Fee."}\n'
        "---\n# alpha\n"
    )
    (catalog / "alpha-doc.okf.md").write_text(bundle_text)
    (catalog / "orphan-doc.okf.md").write_text(
        bundle_text.replace("alpha-doc", "orphan-doc")
    )

    first = regenerate_skill_schemas(skills_dir=skills, catalog_dir=catalog)
    assert first == {"alpha-doc": True}
    assert not (skills / "orphan-doc").exists()
    written = json.loads((skills / "alpha-doc" / DEFAULT_SCHEMA_ASSET).read_text())
    assert written["properties"]["key_fields"]["properties"]["amount"]["enum"] == [
        "LOW",
        "HIGH",
    ]
    assert written["properties"]["key_fields"]["required"] == ["amount"]
    assert "issuer" not in written["properties"]

    second = regenerate_skill_schemas(skills_dir=skills, catalog_dir=catalog)
    assert second == {"alpha-doc": False}


def test_okf_roundtrip_preserves_enums_tables_and_schema_version() -> None:
    bundles = load_okf_doctype_bundles(CATALOG_DIR)
    with_extras = [b for b in bundles.values() if b.field_enums or b.tables]
    assert with_extras, "expected at least one bundle declaring enum or tables"
    for bundle in with_extras:
        rendered = render_okf_markdown(
            bundle.spec,
            field_enums=bundle.field_enums,
            tables=bundle.tables,
            schema_version=bundle.schema_version,
        )
        reparsed = parse_okf_doctype_bundle(rendered)
        assert reparsed.field_enums == bundle.field_enums
        assert reparsed.tables == bundle.tables
        assert reparsed.schema_version == bundle.schema_version
        assert bundle_to_skill_schema(reparsed) == bundle_to_skill_schema(bundle)


def test_loader_serves_the_okf_rendering_for_bundled_doctypes() -> None:
    schemas = doctype_schemas()
    bundles = load_okf_doctype_bundles(CATALOG_DIR)
    for doctype in _bundle_names():
        if doctype not in schemas:
            continue
        assert schemas[doctype] == bundle_to_skill_schema(bundles[doctype]), doctype
