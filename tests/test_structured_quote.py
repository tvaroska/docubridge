"""Structured benefits-quote schema — the rate table (S0-arch-10).

The ``benefits-quote`` skill schema (v2) declares row-shaped data in
``properties.tables`` (``rates``, ``design``); the extraction wire carries it in the
additive ``ExtractionFields.tables`` / ``LedgerEntry.tables`` (contract minor 1.1),
money staying **text**. Covered here: field-for-field alignment with the consumer's
``BenefitsQuote`` vocabulary, Path-A typed validation of rows (accept + every reject
shape, end-to-end over the edge), Path-B prompt/response-schema/parse, the ledger →
``ExchangeTurn`` round trip, and the newer-minor tolerant decode.
"""

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from bridge import patha
from bridge.collect import Collector, project_ledger_entry
from bridge.doctypes import doctype_schemas, tables_model
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.wire import (
    KIND_EXCHANGE_TURN,
    META_KIND,
    decode_exchange_turn,
    encode_exchange_turn,
)
from bridge.extraction.gemini import (
    GeminiExtractor,
    _parse_extraction,
    build_extraction_prompt,
    build_response_schema,
)
from contract.enums import Disposition
from contract.extraction import ExtractionResult, fields_requiring_review
from contract.wire import (
    CONTRACT_MAJOR,
    CONTRACT_VERSION,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    _minor,
    decode_wire,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "skills/benefits-quote/assets/schema.json").read_text())
BENEFITS_CORPUS = ROOT / "wiki/evals/benefits/expected.json"

BASE = "http://test"
URL = "http://test/"

# The consumer's normalized quote vocabulary (``~/benefits-consumer/contract/quote.py`` +
# ``~/benefits-consumer/contract/enums.py``), mirrored here because ``tests/`` must not import the
# consumer. Our schema must carry every field and every enum value of theirs.
AON_RATE_TIER_FIELDS = {
    "coverage_tier",
    "tobacco",
    "age_band",
    "amount",
    "frequency",
    "benefit_amount",
}
AON_DESIGN_FIELDS = {
    "category",
    "category_original",
    "value",
}  # requested_value: intake-side
AON_COVERAGE_TIERS = {"EE", "EE_SPOUSE", "EE_CHILDREN", "FAMILY"}
AON_TOBACCO = {"UNI", "NON_TOBACCO", "TOBACCO"}
AON_FREQUENCIES = {"MONTHLY", "BI_WEEKLY", "ANNUAL"}
AON_PRODUCT_LINES = {"AD_AND_D", "CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY", "LIFE"}


def _columns(table: str) -> dict:
    return SCHEMA["properties"]["tables"]["properties"][table]["items"]["properties"]


def _corpus_block() -> dict:
    """The corpus benefits-quote extraction (oracle) — flat key_fields, no tables."""
    corpus = json.loads(BENEFITS_CORPUS.read_text())
    return json.loads(json.dumps(corpus["documents"][0]["extraction"]))


def _rate_sheet_assertion() -> dict:
    """A synthetic VB rate sheet: mixed MONTHLY/WEEKLY carriers, a per-$1,000 basis,
    design rows and commission — the shapes the Benefits Consumer exec asked about."""
    block = _corpus_block()
    block["fields"]["key_fields"].update(
        {
            "product_line": "CRITICAL_ILLNESS",
            "carrier_product_name": "CritCare Plus",
            "plan_year": "2027",
            "commission_basis": "level",
            "commission_rate_pct": "20",
        }
    )
    block["fields"]["tables"] = {
        "rates": [
            {
                "coverage_tier": "EE",
                "tobacco": "NON_TOBACCO",
                "age_band": "30-34",
                "amount": "$1.10",
                "frequency": "MONTHLY",
                "rate_basis": "PER_1000",
                "benefit_amount": "$10,000",
            },
            {
                "coverage_tier": "EE_SPOUSE",
                "tobacco": "TOBACCO",
                "age_band": "30-34",
                "amount": "$0.62",
                "frequency": "WEEKLY",
                "rate_basis": "PER_1000",
                "benefit_amount": "$10,000",
            },
            {"coverage_tier": "FAMILY", "amount": "4.25", "frequency": "SEMI_MONTHLY"},
        ],
        "design": [
            {
                "category": "Heart Attack",
                "category_original": "Myocardial Infarction",
                "value": "100%",
            },
            {"category": "Pre-existing Condition Limitation", "value": "6 months"},
        ],
    }
    return block


# --------------------------------------------------------------------------- #
# Alignment with the consumer's BenefitsQuote                                 #
# --------------------------------------------------------------------------- #
def test_rate_columns_cover_consumer_rate_tier_field_for_field():
    assert set(_columns("rates")) >= AON_RATE_TIER_FIELDS
    assert set(_columns("design")) >= AON_DESIGN_FIELDS


def test_enums_are_supersets_of_the_consumer_vocabulary():
    rates = _columns("rates")
    assert set(rates["coverage_tier"]["enum"]) == AON_COVERAGE_TIERS
    assert set(rates["tobacco"]["enum"]) == AON_TOBACCO
    # The exec's mixed monthly/weekly question: WEEKLY + SEMI_MONTHLY on top.
    assert AON_FREQUENCIES | {"WEEKLY", "SEMI_MONTHLY"} == set(
        rates["frequency"]["enum"]
    )
    assert set(rates["rate_basis"]["enum"]) == {"FLAT", "PER_1000", "PER_UNIT"}
    key_fields = SCHEMA["properties"]["key_fields"]["properties"]
    assert set(key_fields["product_line"]["enum"]) == AON_PRODUCT_LINES


def test_money_stays_text_everywhere():
    """No numeric type anywhere in the schema: the consumer code-parses money."""

    def types(node):
        if isinstance(node, dict):
            if "type" in node:
                yield node["type"]
            for v in node.values():
                yield from types(v)

    assert set(types(SCHEMA)) == {"object", "string", "array"}


def test_schema_is_versioned_v2():
    assert SCHEMA["version"] == "2.0"
    skill = (ROOT / "skills/benefits-quote/SKILL.md").read_text()
    assert 'version: "2.0"' in skill


# --------------------------------------------------------------------------- #
# Path A — typed row validation                                               #
# --------------------------------------------------------------------------- #
async def test_rate_sheet_assertion_validates_and_disposes_accepted():
    verdict = await patha.validate_only(
        _rate_sheet_assertion(), candidate_doctypes=["benefits-quote"]
    )
    assert verdict.disposition is Disposition.accepted


async def test_corpus_block_without_tables_still_accepted():
    """v1-shaped payloads (the whole existing corpus) validate unchanged."""
    verdict = await patha.validate_only(
        _corpus_block(), candidate_doctypes=["benefits-quote"]
    )
    assert verdict.disposition is Disposition.accepted


@pytest.mark.parametrize(
    ("mutate", "loc", "err_type"),
    [
        (
            lambda t: t["rates"][0].update(frequency="Monthly"),
            ("rates", 0, "frequency"),
            "literal_error",
        ),
        (lambda t: t["rates"][1].pop("amount"), ("rates", 1, "amount"), "missing"),
        (
            lambda t: t["rates"][2].update(premium_cents="425"),
            ("rates", 2, "premium_cents"),
            "extra_forbidden",
        ),
        (lambda t: t.update(riders=[{"name": "x"}]), ("riders",), "extra_forbidden"),
        (lambda t: t["design"][0].pop("value"), ("design", 0, "value"), "missing"),
    ],
    ids=[
        "bad-frequency",
        "missing-amount",
        "extra-column",
        "undeclared-table",
        "design-missing-value",
    ],
)
def test_malformed_rows_rejected_at_validation(mutate, loc, err_type):
    bad = _rate_sheet_assertion()
    mutate(bad["fields"]["tables"])
    with pytest.raises(ValidationError) as exc:
        patha.validate_assertion(bad)
    err = exc.value.errors()[0]
    assert (err["loc"], err["type"]) == (loc, err_type)


def test_key_field_enum_enforced():
    bad = _rate_sheet_assertion()
    bad["fields"]["key_fields"]["product_line"] = "DENTAL"
    with pytest.raises(ValidationError) as exc:
        patha.validate_assertion(bad)
    assert exc.value.errors()[0]["loc"] == ("product_line",)


def test_doctype_without_tables_rejects_any_table():
    gov_id = doctype_schemas()["gov-id"]
    model = tables_model("gov-id", gov_id)
    model.model_validate({})
    with pytest.raises(ValidationError):
        model.model_validate({"rates": []})


@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        yield await ClientFactory(config).create_from_url(URL)


class _NoExtraction:
    async def extract(self, artifact_name: str):
        raise AssertionError("Path A must not call the extraction seam")


async def _send_assertion(assertion: dict) -> ExchangeTurn:
    executor = CollectExecutor(collector=Collector(_NoExtraction()))
    app = build_a2a_app(url=URL, executor=executor)
    parts = encode_collect_request(CollectRequest(process="benefits-procurement"))
    parts.append(ph.new_data_part(assertion))
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)
    async with _client(app) as client:
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id
        task = await client.get_task(GetTaskRequest(id=task_id))
    for artifact in task.artifacts:
        for part in artifact.parts:
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if part.HasField("data") and meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return decode_wire(ExchangeTurn, MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact")


async def test_edge_carries_rate_rows_to_the_consumer():
    """End-to-end over A2A: the rows reach the consumer's ExchangeTurn verbatim."""
    assertion = _rate_sheet_assertion()
    turn = await _send_assertion(assertion)
    entry = turn.status.entries[0]
    assert entry.disposition is Disposition.accepted
    assert entry.tables == assertion["fields"]["tables"]
    assert turn.contract_version == CONTRACT_VERSION


async def test_edge_rejects_bad_frequency_schema_invalid():
    bad = _rate_sheet_assertion()
    bad["fields"]["tables"]["rates"][0]["frequency"] = "FORTNIGHTLY"
    entry = (await _send_assertion(bad)).status.entries[0]
    assert entry.disposition is Disposition.rejected
    assert entry.reason_code == "schema_invalid"


# --------------------------------------------------------------------------- #
# Path B — prompt, pinned response schema, parse                              #
# --------------------------------------------------------------------------- #
def test_prompt_catalog_renders_tables():
    prompt = build_extraction_prompt({"benefits-quote": SCHEMA})
    assert "table rates (one row each)" in prompt
    assert "table design (one row each)" in prompt
    assert "frequency (billing frequency" in prompt


def test_response_schema_pins_tables_with_enums():
    schema = build_response_schema({"benefits-quote": SCHEMA})
    fields = schema["properties"]["fields"]
    assert "tables" not in fields["required"]
    rates = fields["properties"]["tables"]["properties"]["rates"]
    assert rates["type"] == "array"
    freq = rates["items"]["properties"]["frequency"]
    assert freq["nullable"] is True
    assert "WEEKLY" in freq["enum"]
    assert fields["properties"]["key_fields"]["properties"]["product_line"]["enum"]


def test_response_schema_accepted_by_genai():
    """The pinned schema (arrays of enum'd nullable cells) passes genai's transformer."""
    from google.genai import _transformers, types

    class _Client:
        vertexai = True

    out = _transformers.t_schema(_Client(), build_response_schema(doctype_schemas()))
    assert isinstance(out, types.Schema)


def test_response_schema_without_tables_is_unchanged():
    gov_id = {"gov-id": doctype_schemas()["gov-id"]}
    fields = build_response_schema(gov_id)["properties"]["fields"]["properties"]
    assert "tables" not in fields


def _model_answer() -> dict:
    """What a model answering under the union schema emits: null cells, null tables."""
    answer = _rate_sheet_assertion()
    answer["fields"]["tables"]["rates"][2].update(tobacco=None, age_band=None)
    answer["fields"]["tables"]["other"] = None
    answer["flagged_fields"] = ["rates"]
    answer["field_reviews"] = [
        {"path": "fields.tables.rates[1].frequency", "reason": "low_confidence"}
    ]
    return answer


def test_parse_prunes_null_cells_and_tables():
    result = _parse_extraction(json.dumps(_model_answer()))
    assert "other" not in result.fields.tables
    assert result.fields.tables["rates"][2] == {
        "coverage_tier": "FAMILY",
        "amount": "4.25",
        "frequency": "SEMI_MONTHLY",
    }


def test_review_paths_address_tables():
    result = _parse_extraction(json.dumps(_model_answer()))
    assert fields_requiring_review(result) == [
        "fields.tables.rates[1].frequency",
        "fields.tables.rates",
    ]


class _Models:
    def __init__(self, text: str) -> None:
        self.text = text

    def generate_content(self, *, model, contents, config):
        return type("R", (), {"text": self.text})()


async def test_gemini_extract_to_ledger_round_trip():
    """Path B end-to-end (fake client): rows → ExtractionResult → LedgerEntry →
    encoded ExchangeTurn → decoded, cells intact and still text."""
    client = type("C", (), {"models": _Models(json.dumps(_model_answer()))})()
    extractor = GeminiExtractor(
        client=client,
        resolver=lambda name: (b"%PDF-1.4", "application/pdf"),
        schemas={"benefits-quote": SCHEMA},
    )
    result = await extractor.extract("carrier-rate-sheet.pdf")
    verdict = await patha.validate_only(result, candidate_doctypes=["benefits-quote"])
    entry = project_ledger_entry(doc_id="d1", result=result, verdict=verdict)
    turn = ExchangeTurn(context_id="ctx", status=CollectionStatus(entries=[entry]))
    restored = decode_exchange_turn(encode_exchange_turn(turn))
    rows = restored.status.entries[0].tables["rates"]
    assert [r["frequency"] for r in rows] == ["MONTHLY", "WEEKLY", "SEMI_MONTHLY"]
    assert rows[1]["amount"] == "$0.62"


# --------------------------------------------------------------------------- #
# contract_version semantics                                                  #
# --------------------------------------------------------------------------- #
def test_contract_minor_bumped_for_additive_tables():
    assert (
        _minor(CONTRACT_VERSION) >= 1
    )  # "1.1" introduced tables; later minors keep it
    assert LedgerEntry.model_fields["tables"].default == {}


def _turn_payload(version: str) -> dict:
    turn = ExchangeTurn(
        context_id="c",
        status=CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="d",
                    doctype="benefits-quote",
                    disposition="accepted",
                    gate="auto_approve",
                    tables={"rates": [{"amount": "1"}]},
                )
            ]
        ),
    ).model_dump(mode="json")
    turn["contract_version"] = version
    turn["status"]["entries"][0]["added_in_a_later_minor"] = "x"
    return turn


def test_newer_minor_decodes_tolerantly():
    newer = f"{CONTRACT_MAJOR}.{_minor(CONTRACT_VERSION) + 1}"
    turn = decode_wire(ExchangeTurn, _turn_payload(newer))
    assert turn.status.entries[0].tables == {"rates": [{"amount": "1"}]}


@pytest.mark.parametrize("version", ["1", "1.1", CONTRACT_VERSION])
def test_same_or_older_version_stays_strict(version):
    with pytest.raises(ValidationError):
        decode_wire(ExchangeTurn, _turn_payload(version))


def test_v1_payload_without_tables_still_decodes():
    payload = _turn_payload("1")
    del payload["status"]["entries"][0]["added_in_a_later_minor"]
    del payload["status"]["entries"][0]["tables"]
    assert decode_wire(ExchangeTurn, payload).status.entries[0].tables == {}


def test_corpus_extraction_round_trips_byte_stable():
    """ExtractionResult of a v1 block re-dumps with an empty ``tables`` only."""
    block = _corpus_block()
    dumped = ExtractionResult.model_validate(block).model_dump(exclude_defaults=True)
    assert "tables" not in dumped["fields"]
