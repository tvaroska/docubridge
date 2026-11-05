"""Tests for P8-1: Contrastive Descriptions & OKF Schema Package Loader."""

from __future__ import annotations

from collections.abc import Sequence

from bridge.doctypes import (
    doctype_description,
    doctype_fields,
    doctype_schemas,
)
from bridge.extraction.fixture import FixtureExtractor, _normalize_key
from bridge.extraction.gemini_schema import (
    build_extraction_prompt,
    build_response_schema,
)
from bridge.refinery import (
    BASELINE_REVISION_ID,
    build_baseline_ontology_revision,
    build_catalog_permutations,
    compute_perturbation_stability,
    load_okf_entity_specs,
    parse_okf_entity_markdown,
    render_okf_markdown,
)
from contract.extraction import ExtractionResult


def test_okf_doctype_bundles_load_all_seven_canonical_entities() -> None:
    """All 7 catalog/doctypes/*.okf.md bundles parse into OntologyEntitySpec objects."""
    specs = load_okf_entity_specs()
    expected = {
        "benefits-quote",
        "commercial-property-quote",
        "cre-credit-agreement",
        "gov-id",
        "mai-appraisal",
        "trid-closing-disclosure",
        "utility-bill",
    }
    assert expected <= set(specs.keys())

    for entity_id, spec in specs.items():
        assert spec.entity_id == entity_id
        assert spec.okf_version == "0.2"
        assert spec.revision_id == BASELINE_REVISION_ID
        rendered_entity = spec.entity_description.render()
        assert len(rendered_entity) >= 30
        assert "Where to read:" in rendered_entity
        assert "Distinguish from:" in rendered_entity
        assert spec.fields, f"{entity_id} must declare at least one field"
        for fname, fspec in spec.fields.items():
            rendered_field = fspec.description.render()
            assert fspec.description.what_it_is, (
                f"{entity_id}.{fname} missing what_it_is"
            )
            assert "Where to read:" in rendered_field
            assert "Distinguish from:" in rendered_field


def test_okf_markdown_roundtrip_preserves_entity_fields_and_edges() -> None:
    """render_okf_markdown -> parse_okf_entity_markdown is lossless across fields and edges."""
    specs = load_okf_entity_specs()
    original = specs["trid-closing-disclosure"]
    serialized = render_okf_markdown(original)
    reparsed = parse_okf_entity_markdown(serialized)

    assert reparsed.entity_id == original.entity_id
    assert reparsed.source_priority == original.source_priority
    assert reparsed.entity_description == original.entity_description
    assert set(reparsed.fields.keys()) == set(original.fields.keys())
    assert len(reparsed.edges) == len(original.edges)


def test_doctype_schemas_and_response_schema_have_100_percent_contrastive_descriptions() -> (
    None
):
    """100% of installed doctypes and fields in build_response_schema carry descriptions."""
    schemas = doctype_schemas()
    assert len(schemas) >= 7
    for dt, schema in schemas.items():
        desc = doctype_description(schema)
        assert desc, f"{dt} top-level description must be non-empty"
        fields = doctype_fields(schema)
        assert fields, f"{dt} must declare fields"
        for fname, fdesc in fields.items():
            assert fdesc.strip(), f"{dt}.{fname} description must be non-empty"

    prompt = build_extraction_prompt(schemas)
    assert "Where to read:" in prompt
    assert "Distinguish from:" in prompt

    resp_schema = build_response_schema(schemas)
    assert resp_schema is not None
    assert resp_schema.get("description")
    fields_obj = resp_schema["properties"]["fields"]
    assert fields_obj.get("description")
    doctype_prop = fields_obj["properties"]["doctype"]
    assert "enum" not in doctype_prop
    assert "Contrastive doctype guide:" in doctype_prop["description"]

    kf_obj = fields_obj["properties"]["key_fields"]
    assert kf_obj.get("description")
    for fname, fspec in kf_obj["properties"].items():
        assert fspec.get("description"), (
            f"response_schema key_fields.{fname} missing description"
        )


class _PermutationAwareFixtureExtractor:
    """Corpus-backed extractor that honours ``catalog_order`` and records it."""

    def __init__(self) -> None:
        self._inner = FixtureExtractor()
        self.seen_orders: list[tuple[str, ...]] = []

    async def extract(
        self, artifact_name: str, *, catalog_order: Sequence[str] | None = None
    ) -> ExtractionResult:
        self.seen_orders.append(tuple(catalog_order or ()))
        return await self._inner.extract(artifact_name)


class _FlipOnceExtractor(_PermutationAwareFixtureExtractor):
    """Same corpus, but one document's field flips under the reversed catalog."""

    def __init__(self, *, document: str, field: str) -> None:
        super().__init__()
        self._document = document
        self._field = field

    async def extract(
        self, artifact_name: str, *, catalog_order: Sequence[str] | None = None
    ) -> ExtractionResult:
        result = await super().extract(artifact_name, catalog_order=catalog_order)
        order = tuple(catalog_order or ())
        if _normalize_key(artifact_name) != self._document or order != tuple(
            reversed(sorted(order))
        ):
            return result
        key_fields = {**result.fields.key_fields, self._field: " Perturbed "}
        return result.model_copy(
            update={
                "fields": result.fields.model_copy(update={"key_fields": key_fields})
            }
        )


_CORPUS_DOCS = ("gov-id-clean", "bill-powerco-clean", "bill-aquautil-clean")


def test_catalog_order_and_layout_perturbation_stability_exceeds_alpha_threshold() -> (
    None
):
    """Alpha is measured over real permutation runs: identical outputs → 1.0, a flip → < 1.0."""
    schemas = doctype_schemas()
    reversed_schemas = {k: schemas[k] for k in reversed(list(schemas.keys()))}
    # Default ordering is deterministic regardless of the input dict order ...
    assert build_extraction_prompt(schemas) == build_extraction_prompt(reversed_schemas)
    assert build_response_schema(schemas) == build_response_schema(reversed_schemas)
    # ... and an explicit catalog_order genuinely changes what the model is shown.
    orders = build_catalog_permutations(sorted(schemas), permutations=3)
    assert len({tuple(o) for o in orders}) == 3
    prompts = {build_extraction_prompt(schemas, catalog_order=o) for o in orders}
    assert len(prompts) == 3

    stable = _PermutationAwareFixtureExtractor()
    result = compute_perturbation_stability(
        stable, _CORPUS_DOCS, permutations=3, doctypes=sorted(schemas)
    )
    assert result.evaluated is True
    assert result.n_permutations == 3
    assert result.n_documents == len(_CORPUS_DOCS)
    assert result.n_items >= len(_CORPUS_DOCS) * 2
    assert result.alpha == 1.0
    assert result.ac2 == 1.0
    assert result.disagreements == ()
    assert result.passes is True
    assert len(stable.seen_orders) == len(_CORPUS_DOCS) * 3
    assert len(set(stable.seen_orders)) == 3

    flipped = _FlipOnceExtractor(document="gov-id-clean", field="expiry")
    unstable = compute_perturbation_stability(
        flipped, _CORPUS_DOCS, permutations=3, doctypes=sorted(schemas)
    )
    assert unstable.evaluated is True
    assert unstable.alpha is not None and unstable.alpha < 1.0
    assert unstable.ac2 is not None and unstable.ac2 < 1.0
    assert [(d.document, d.field) for d in unstable.disagreements] == [
        ("gov-id-clean", "key_fields.expiry")
    ]
    assert "perturbed" in unstable.disagreements[0].values

    assert compute_perturbation_stability(None, _CORPUS_DOCS).evaluated is False
    assert compute_perturbation_stability(stable, ()).evaluated is False

    baseline_rev = build_baseline_ontology_revision()
    assert baseline_rev.revision_id == BASELINE_REVISION_ID
    assert len(baseline_rev.edges) >= 13
