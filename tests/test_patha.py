"""Path-A validate-only tests (P6-1, PATH-A; schema-driven validation S0-arch-8).

A structured extraction-assertion (party-with-agent) is schema-validated + disposed
with the extraction seam **never called**; a malformed assertion (missing a required
key field) is rejected at Pydantic validation before disposition; and the edge Path-A
branch returns a terminal turn without any extraction (proven with a seam spy).

The typed key-field model is built from the asserted doctype's installed skill schema
(``bridge/doctypes.py``), so the coverage here is every installed doctype — including
``benefits-quote``, which the retired two-entry ``_KEY_FIELD_MODELS`` dict never
validated — and a doctype **installed at runtime** validates with no code change.
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from bridge import patha
from bridge.collect import Collector, project_ledger_entry
from bridge.doctypes import doctype_schemas, key_field_model
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from bridge.seams import Seam, SeamMode, get_adapter
from contract.enums import Disposition, Gate
from contract.extraction import (
    ExtractionResult,
    GovIdKeyFields,
    UtilityBillKeyFields,
)
from contract.wire import CollectRequest, ExchangeTurn

DOCTYPES = ["gov-id", "utility-bill"]

BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)

BASE = "http://test"
URL = "http://test/"


def _gov_id_clean_assertion() -> dict:
    """A well-formed gov-id-clean extraction assertion (all required key fields)."""
    return {
        "fields": {
            "doctype": "gov-id",
            "key_fields": {
                "name": "Jordan Lee",
                "address": "14 Elm Row, Springfield",
                "doc_number": "DL-8392047",
                "issuing_authority": "State DMV",
                "expiry": "2030-01-01",
            },
        },
        "overall_confidence": 0.96,
        "legible": True,
        "flagged_fields": [],
    }


def _malformed_gov_id_assertion() -> dict:
    """A gov-id assertion missing the required 'expiry' key field."""
    a = _gov_id_clean_assertion()
    del a["fields"]["key_fields"]["expiry"]
    return a


def _benefits_quote_assertion() -> dict:
    """The corpus benefits-quote extraction block (the oracle, never hand-authored)."""
    corpus = json.loads(BENEFITS_CORPUS.read_text())
    return json.loads(json.dumps(corpus["documents"][0]["extraction"]))


def _pay_stub_schema() -> str:
    """A brand-new doctype's schema: employer + net_pay, both required."""
    return json.dumps(
        {
            "type": "object",
            "required": ["doctype", "key_fields"],
            "properties": {
                "doctype": {"type": "string"},
                "key_fields": {
                    "type": "object",
                    "required": ["employer", "net_pay"],
                    "properties": {
                        "employer": {"type": "string"},
                        "net_pay": {"type": "string"},
                    },
                },
            },
        }
    )


def _install_pay_stub(assets: dict[str, str] | None) -> None:
    """Install a pay-stub doctype skill through the skill_registry seam."""
    registry = get_adapter(Seam.skill_registry, SeamMode.local)
    registry.install_skill(
        "pay-stub",
        "---\nname: pay-stub\ndescription: A pay stub.\n"
        "metadata:\n  bridge-kind: doctype\n  bridge-schema: assets/schema.json\n---\n\nx\n",
        assets=assets,
    )


def _pay_stub_assertion(key_fields: dict[str, str]) -> dict:
    return {
        "fields": {"doctype": "pay-stub", "key_fields": key_fields},
        "overall_confidence": 0.9,
        "legible": True,
        "flagged_fields": [],
    }


class SpyExtractor:
    """Fails the test if extract() is ever called (Path A must not touch the seam)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, artifact_name: str):
        self.calls.append(artifact_name)
        raise AssertionError("Path A must not call the extraction seam")


async def test_validate_only_accepts_without_extraction():
    """Gate #5: a well-formed assertion disposes accepted; no seam is consulted."""
    verdict = await patha.validate_only(
        _gov_id_clean_assertion(), candidate_doctypes=DOCTYPES
    )
    assert verdict.disposition is Disposition.accepted


async def test_malformed_assertion_rejected_at_validation():
    """A missing required key field raises ValidationError before disposition."""
    with pytest.raises(ValidationError):
        await patha.validate_only(
            _malformed_gov_id_assertion(), candidate_doctypes=DOCTYPES
        )
    # validate_only_entry raises the same way (before minting a verdict).
    with pytest.raises(ValidationError):
        patha.validate_only_entry(
            _malformed_gov_id_assertion(), doc_id="d", candidate_doctypes=DOCTYPES
        )


async def test_validate_only_entry_accepts():
    """validate_only_entry projects a Path-A accepted ledger entry (no extraction)."""
    entry = patha.validate_only_entry(
        _gov_id_clean_assertion(), doc_id="gov-id", candidate_doctypes=DOCTYPES
    )
    assert entry.disposition is Disposition.accepted
    assert entry.doctype == "gov-id"


# --------------------------------------------------------------------------- #
# Edge Path-A branch (#8): structured assertion → terminal turn, no extraction #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(URL)
        yield hc, client


def _assertion_message() -> Message:
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_data_part(_gov_id_clean_assertion()))
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


def _turn_from_task(task) -> ExchangeTurn:
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return ExchangeTurn.model_validate(MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact")


async def test_edge_path_a_branch_skips_extraction():
    """Gate #8: a structured-assertion message routes to Path A — terminal turn with the
    accepted entry, and the injected seam spy is NEVER called."""
    spy = SpyExtractor()
    executor = CollectExecutor(collector=Collector(spy))
    app = build_a2a_app(url=URL, executor=executor)

    async with _client(app) as (_hc, client):
        task_id = None
        async for ev in client.send_message(
            SendMessageRequest(message=_assertion_message())
        ):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id
        assert task_id is not None

        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        turn = _turn_from_task(task)
        assert turn.terminal is True
        assert len(turn.status.entries) == 1
        assert turn.status.entries[0].disposition is Disposition.accepted
        # The seam was never called (Path A is validate-only).
        assert spy.calls == []


async def test_edge_path_a_malformed_assertion_rejected():
    """A malformed assertion routes to Path A and yields a schema_invalid reject."""
    spy = SpyExtractor()
    executor = CollectExecutor(collector=Collector(spy))
    app = build_a2a_app(url=URL, executor=executor)

    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_data_part(_malformed_gov_id_assertion()))
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

    async with _client(app) as (_hc, client):
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id

        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = _turn_from_task(task)
        entry = turn.status.entries[0]
        assert entry.disposition is Disposition.rejected
        assert entry.reason_code == "schema_invalid"
        assert spy.calls == []


# --------------------------------------------------------------------------- #
# Schema-driven validation (S0-arch-8): every installed doctype, not two       #
# --------------------------------------------------------------------------- #
def test_benefits_quote_assertion_missing_key_field_rejected():
    """THE regression: a benefits-quote missing ``premium`` used to validate clean."""
    bad = _benefits_quote_assertion()
    del bad["fields"]["key_fields"]["premium"]

    with pytest.raises(ValidationError) as exc:
        patha.validate_assertion(bad)

    err = exc.value.errors()[0]
    assert err["loc"] == ("premium",)
    assert err["type"] == "missing"


def test_benefits_quote_assertion_extra_key_field_rejected():
    """extra="forbid" parity with the deleted hand-written models."""
    extra = _benefits_quote_assertion()
    extra["fields"]["key_fields"]["bogus"] = "x"

    with pytest.raises(ValidationError) as exc:
        patha.validate_assertion(extra)

    assert exc.value.errors()[0]["type"] == "extra_forbidden"


async def test_benefits_quote_assertion_accepts_corpus_record():
    """The untouched corpus block still disposes accepted (no false positive)."""
    verdict = await patha.validate_only(
        _benefits_quote_assertion(), candidate_doctypes=["benefits-quote"]
    )
    assert verdict.disposition is Disposition.accepted


async def test_unknown_doctype_skips_typed_check_and_gates_unsupported():
    """A doctype with NO installed skill stays a free string → label-space gate.

    The S0-arch-5 invariant: an out-of-label-space document (the corpus's passport)
    must remain *reportable* and reach the ``unsupported`` terminal outcome — never
    become a validation raise.
    """
    verdict = await patha.validate_only(
        {
            "fields": {"doctype": "passport", "key_fields": {}},
            "overall_confidence": 0.9,
            "legible": True,
            "flagged_fields": [],
        },
        candidate_doctypes=["gov-id"],
    )
    assert verdict.disposition is Disposition.rejected
    assert verdict.gate is Gate.unsupported
    assert verdict.reason_code == "unsupported_doctype"


@pytest.mark.parametrize(
    ("doctype", "reference"),
    [("gov-id", GovIdKeyFields), ("utility-bill", UtilityBillKeyFields)],
)
def test_dynamic_key_field_model_matches_contract_models(doctype, reference):
    """No coverage regression: the built model == the model the dict used to name."""
    model = key_field_model(doctype, doctype_schemas()[doctype])

    assert set(model.model_fields) == set(reference.model_fields)
    required = {n for n, f in model.model_fields.items() if f.is_required()}
    assert required == {n for n, f in reference.model_fields.items() if f.is_required()}


def test_live_doctype_add_validates_new_doctype(tmp_path, monkeypatch):
    """The no-redeploy property, extended to validation: install a folder, get a check.

    ``get_adapter(Seam.skill_registry, …)`` reads ``BRIDGE_SKILLS_DIR`` per call and
    Path A reads the catalog per assertion, so the new doctype validates immediately —
    with zero change to ``bridge/``.
    """
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    _install_pay_stub({"assets/schema.json": _pay_stub_schema()})

    with pytest.raises(ValidationError) as exc:
        patha.validate_assertion(_pay_stub_assertion({"employer": "Acme"}))
    assert exc.value.errors()[0]["loc"] == ("net_pay",)

    complete = patha.validate_assertion(
        _pay_stub_assertion({"employer": "Acme", "net_pay": "100.00"})
    )
    assert complete.fields.doctype == "pay-stub"


def test_half_installed_doctype_skips_typed_check_loudly(tmp_path, monkeypatch, caplog):
    """Declared-but-absent schema asset: skip the check, but never silently."""
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    _install_pay_stub(None)  # SKILL.md declares bridge-schema; the asset is absent

    with caplog.at_level(logging.WARNING, logger="bridge.patha"):
        result = patha.validate_assertion(_pay_stub_assertion({}))

    assert result.fields.doctype == "pay-stub"
    assert any(
        rec.levelno == logging.WARNING and "pay-stub" in rec.getMessage()
        for rec in caplog.records
    )


def test_injected_schemas_drive_validation_without_a_registry():
    """The injectable catalog (mirrors ``GeminiExtractor(schemas=…)``) is honoured."""
    schemas = {"pay-stub": json.loads(_pay_stub_schema())}

    with pytest.raises(ValidationError):
        patha.validate_assertion(
            _pay_stub_assertion({"employer": "Acme"}), schemas=schemas
        )
    # An empty injected catalog means "no installed doctype" → skip, never reject.
    assert (
        patha.validate_assertion(
            _pay_stub_assertion({"employer": "Acme"}), schemas={}
        ).fields.doctype
        == "pay-stub"
    )


async def test_edge_path_a_malformed_benefits_assertion_rejected():
    """End-to-end: a malformed benefits-quote assertion → schema_invalid, no extraction.

    The process skill is ``benefits-procurement`` (``bridge-candidate-doctypes:
    benefits-quote``), the doctype the old two-entry dict never checked.
    """
    spy = SpyExtractor()
    executor = CollectExecutor(collector=Collector(spy))
    app = build_a2a_app(url=URL, executor=executor)

    bad = _benefits_quote_assertion()
    del bad["fields"]["key_fields"]["premium"]
    parts = encode_collect_request(CollectRequest(process="benefits-procurement"))
    parts.append(ph.new_data_part(bad))
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

    async with _client(app) as (_hc, client):
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id

        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = _turn_from_task(task)
        entry = turn.status.entries[0]
        assert entry.disposition is Disposition.rejected
        assert entry.reason_code == "schema_invalid"
        assert spy.calls == []


async def test_consumer_vocabulary_assertion_disposes_hitl_review():
    """S0-debt-2: `fields_requiring_review` on an assertion reaches the gate (and the
    ledger carries the dotted paths + why)."""
    assertion = _gov_id_clean_assertion()
    del assertion["flagged_fields"]
    assertion["fields_requiring_review"] = ["expiry"]

    verdict = await patha.validate_only(assertion, candidate_doctypes=DOCTYPES)
    assert verdict.disposition is Disposition.pending
    assert verdict.gate is Gate.hitl_review
    assert verdict.reason_code == "flagged_fields"

    entry = project_ledger_entry(
        doc_id="doc-a",
        result=ExtractionResult.model_validate(assertion),
        verdict=verdict,
    )
    assert [(r.path, r.reason.value) for r in entry.field_reviews] == [
        ("fields.key_fields.expiry", "model_flagged")
    ]
