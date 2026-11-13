"""Cross-repo consumer conformance run (S0-arch-9).

A carrier PDF enters over the A2A edge **by reference**, is extracted against the
installed ``benefits-quote`` skill schema, lands on the ``ExchangeTurn`` with per-field
confidence and review flags, and maps onto the consumer's ``BenefitsQuote`` — for every
record of ``wiki/evals/benefits/conformance.json`` x {fixture, gemini-replay} x
{loopback ``http://``, ``gs://``}, at terminal-outcome parity.

Always-run and credential-free, except two gated legs: the live Gemini read
(``BRIDGE_TEST_GEMINI=1``) and the real-consumer drift check
(``BRIDGE_TEST_CONSUMER_REPO=<path to the consumer checkout>``).
"""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import subprocess
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from contract.enums import ReviewReason
from contract.extraction import FieldReview
from contract.wire import CONTRACT_VERSION, LedgerEntry
from tests import corpus_pdf
from tests._scripted_llm import ScriptedLlm
from tests.conformance import consumer
from tests.conformance.consumer import ConformanceGap, map_entry_to_quote
from tests.conformance.journey import (
    BASE,
    BENEFITS_ROOT,
    LEGS,
    RECORDS,
    REFS,
    ReplayClient,
    build_app,
    gap_payload,
    intake_for,
    reference_for,
    send_by_reference,
    serve_corpus,
    terminal_outcome,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORD_IDS = tuple(RECORDS)
RECORD_A, RECORD_B = RECORD_IDS
_GEMINI = os.environ.get("BRIDGE_TEST_GEMINI") == "1"
_CONSUMER_REPO = os.environ.get("BRIDGE_TEST_CONSUMER_REPO")

# The consumer's vocabulary (``~/benefits-consumer/contract/enums.py``), re-declared locally as in
# ``tests/test_structured_quote.py`` — test modules do not import each other.
AON_COVERAGE_TIERS = {"EE", "EE_SPOUSE", "EE_CHILDREN", "FAMILY"}
AON_TOBACCO = {"UNI", "NON_TOBACCO", "TOBACCO"}
AON_FREQUENCIES = {"MONTHLY", "BI_WEEKLY", "ANNUAL"}
AON_PRODUCT_LINES = {"AD_AND_D", "CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY", "LIFE"}


@pytest.fixture
def corpus_server(monkeypatch, edge_mode):
    """The loopback corpus server, its ``host:port`` allowlisted for the egress guard.

    Depends on ``edge_mode`` (``conftest.py``) so every journey runs on both the
    deterministic and the hybrid edge (S3-edge-3), the LLM leg called 0 times.
    """
    with serve_corpus() as server:
        monkeypatch.setenv("BRIDGE_FETCH_ALLOWLIST", server.host_port)
        yield server


def _pdf_bytes(record_id: str) -> bytes:
    return corpus_pdf.build_conformance_pdf(RECORDS[record_id])


async def _journey(record_id: str, leg: str, ref: str, server, *, client=None):
    app = build_app(leg, record_id=record_id, client=client)
    uri = reference_for(record_id, ref, server)
    return app, uri, await send_by_reference(app, record_id=record_id, uri=uri)


journeys = pytest.mark.parametrize(
    ("record_id", "leg", "ref"),
    [(r, leg, ref) for r in RECORD_IDS for leg in LEGS for ref in REFS],
)


# --------------------------------------------------------------------------- #
# The journey, per record x leg x reference shape                             #
# --------------------------------------------------------------------------- #
@journeys
async def test_journey_lands_expected_terminal_outcome(
    record_id, leg, ref, corpus_server
):
    record = RECORDS[record_id]
    _, _, run = await _journey(record_id, leg, ref, corpus_server)

    assert run.task.status.state == TaskState.TASK_STATE_COMPLETED
    assert run.part_contract_version == CONTRACT_VERSION
    assert run.turn.contract_version == CONTRACT_VERSION
    entry = run.entry
    assert entry.doc_id == record_id
    assert entry.doctype == "benefits-quote"
    expected = record["expected_terminal"]
    assert entry.disposition.value == expected["disposition"]
    assert entry.gate.value == expected["gate"]
    assert entry.reason_code == expected["reason_code"]
    assert [r.path for r in entry.field_reviews] == expected["fields_requiring_review"]
    # Cells verbatim, still text.
    assert entry.tables == record["extraction"]["fields"]["tables"]
    # Both legs replay the oracle, so exact here — the parity key still excludes them.
    assert entry.overall_confidence == record["extraction"]["overall_confidence"]
    assert entry.field_confidence == record["extraction"]["field_confidence"]


@journeys
async def test_journey_maps_onto_consumer_quote(record_id, leg, ref, corpus_server):
    _, _, run = await _journey(record_id, leg, ref, corpus_server)

    quote = map_entry_to_quote(run.entry, intake_for(record_id))

    assert (
        quote.model_dump(mode="json", exclude={"source"})
        == (RECORDS[record_id]["expected_consumer_quote"])
    )
    assert quote.source.path is consumer.SourcePath.a2a
    assert quote.source.artifact.uri == run.entry.artifact.uri
    assert quote.rates
    assert all(isinstance(rate.amount, Decimal) for rate in quote.rates)


@pytest.mark.parametrize("record_id", RECORD_IDS)
async def test_terminal_outcome_parity_across_legs_and_reference_shapes(
    record_id, corpus_server
):
    outcomes = []
    for leg in LEGS:
        for ref in REFS:
            _, _, run = await _journey(record_id, leg, ref, corpus_server)
            quote = map_entry_to_quote(run.entry, intake_for(record_id))
            outcomes.append(terminal_outcome(run.entry, quote))
    assert len(outcomes) == 4
    assert all(outcome == outcomes[0] for outcome in outcomes)


def _exchange_turn_data(task) -> dict:
    """The raw exchange_turn data dict off the task (selected by metadata.kind)."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if part.HasField("data") and meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return MessageToDict(part.data)
    raise AssertionError("no exchange_turn artifact part on the task")


PARITY_CONTEXT_ID = "conformance-parity"


@journeys
async def test_hybrid_exchange_turn_byte_identical_to_deterministic(
    record_id, leg, ref, monkeypatch
):
    """Benefits Consumer parity (S3-edge-3): the hybrid edge's ExchangeTurn is byte-for-byte the
    deterministic edge's, and the LLM leg is called 0 times.

    Not on the ``edge_mode``-parametrized ``corpus_server`` (that would run it twice
    for nothing): both modes are built here, as fresh apps (artifact versions match),
    with a FIXED ``context_id`` so the rewritten scoped artifact URIs are identical.
    """
    with serve_corpus() as server:
        monkeypatch.setenv("BRIDGE_FETCH_ALLOWLIST", server.host_port)
        uri = reference_for(record_id, ref, server)
        det_app = build_app(leg, record_id=record_id, edge_mode="deterministic")
        det = await send_by_reference(
            det_app, record_id=record_id, uri=uri, context_id=PARITY_CONTEXT_ID
        )
        llm = ScriptedLlm(max_calls=0)
        hyb_app = build_app(leg, record_id=record_id, edge_mode="hybrid", llm=llm)
        hyb = await send_by_reference(
            hyb_app, record_id=record_id, uri=uri, context_id=PARITY_CONTEXT_ID
        )

    assert det.task.status.state == TaskState.TASK_STATE_COMPLETED
    assert hyb.task.status.state == TaskState.TASK_STATE_COMPLETED
    assert hyb.turn.context_id == PARITY_CONTEXT_ID
    assert hyb.turn.model_dump_json() == det.turn.model_dump_json()
    assert _exchange_turn_data(hyb.task) == _exchange_turn_data(det.task)
    assert hyb.part_contract_version == det.part_contract_version
    assert llm.call_count == 0


# --------------------------------------------------------------------------- #
# By reference, really                                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("record_id", RECORD_IDS)
async def test_gemini_leg_read_the_pdf_by_reference(record_id, corpus_server):
    """http: the engine fetched the committed PDF and sent it inline as application/pdf."""
    from google.genai import types as genai_types

    client = ReplayClient(RECORDS[record_id])
    await _journey(record_id, "gemini", "http", corpus_server, client=client)

    assert len(client.calls) == 1
    call = client.calls[0]
    prompt, part = call["contents"]
    assert isinstance(part, genai_types.Part), type(part)
    assert part.inline_data is not None
    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == _pdf_bytes(record_id)
    # Extracted against the installed skill schema (no `schemas=` injected).
    rates = call["config"]["response_schema"]["properties"]["fields"]["properties"][
        "tables"
    ]["properties"]["rates"]
    assert {"WEEKLY", "BI_WEEKLY"} <= set(
        rates["items"]["properties"]["frequency"]["enum"]
    )
    assert "benefits-quote" in prompt
    # The edge fetches twice (extraction + persistence): assert presence, not a count.
    assert f"/{RECORDS[record_id]['artifact']}" in corpus_server.seen


@pytest.mark.parametrize("record_id", RECORD_IDS)
async def test_gemini_leg_gs_uri_rides_by_reference(record_id, corpus_server):
    """gs://: the model reads the object; the Bridge downloads and persists nothing."""
    client = ReplayClient(RECORDS[record_id])
    _, uri, run = await _journey(
        record_id, "gemini", "gs", corpus_server, client=client
    )

    (call,) = client.calls
    part = call["contents"][1]
    assert part.file_data is not None
    assert part.file_data.file_uri == uri
    assert part.file_data.mime_type == "application/pdf"
    assert part.inline_data is None
    assert corpus_server.seen == []
    assert run.entry.artifact.uri == uri  # by reference: no persisted bytes


@pytest.mark.parametrize("leg", LEGS)
async def test_http_reference_is_rewritten_to_the_scoped_fetch_endpoint(
    leg, corpus_server
):
    app, _, run = await _journey(RECORD_A, leg, "http", corpus_server)

    uri = run.entry.artifact.uri
    assert uri.startswith(f"{BASE}/")
    assert run.turn.context_id in uri
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        response = await hc.get(uri)
    assert response.status_code == 200
    assert response.content == _pdf_bytes(RECORD_A)


# --------------------------------------------------------------------------- #
# Refuse loud, never coerce                                                    #
# --------------------------------------------------------------------------- #
async def test_conformance_gap_is_reported_not_coerced(corpus_server):
    """The Bridge carries WEEKLY / PER_1000 verbatim; the consumer mapping refuses them."""
    payload = gap_payload(RECORD_A)
    client = ReplayClient(RECORDS[RECORD_A], payload=payload)
    _, _, run = await _journey(RECORD_A, "gemini", "http", corpus_server, client=client)

    assert run.entry.disposition.value == "accepted"
    assert run.entry.tables == payload["fields"]["tables"]
    with pytest.raises(ConformanceGap) as exc:
        map_entry_to_quote(run.entry, intake_for(RECORD_A))
    gaps = exc.value.gaps
    assert any(g.startswith("rates[0].frequency=WEEKLY") for g in gaps), gaps
    assert any(g.startswith("rates[1].rate_basis=PER_1000") for g in gaps), gaps
    assert len(gaps) == 2, gaps


def _entry(record_id: str, **update) -> LedgerEntry:
    """A ledger entry projected straight from a record's oracle (no edge)."""
    extraction = RECORDS[record_id]["extraction"]
    fields = extraction["fields"]
    entry = LedgerEntry(
        doc_id=record_id,
        doctype=fields["doctype"],
        disposition="accepted",
        gate="auto_approve",
        issuer=fields["issuer"],
        key_fields=fields["key_fields"],
        tables=copy.deepcopy(fields["tables"]),
        artifact={"uri": f"gs://b/{record_id}.pdf", "mime_type": "application/pdf"},
        overall_confidence=extraction["overall_confidence"],
        field_confidence=extraction["field_confidence"],
    )
    return entry.model_copy(update=update)


def test_mapping_requires_wire_confidence():
    with pytest.raises(ConformanceGap) as exc:
        map_entry_to_quote(
            _entry(RECORD_A, overall_confidence=None), intake_for(RECORD_A)
        )
    assert [g for g in exc.value.gaps if g.startswith("extraction_confidence")]


def test_unmappable_review_path_is_a_gap():
    """A review flag the consumer cannot carry is never silently dropped."""
    review = FieldReview(
        path="fields.key_fields.premium", reason=ReviewReason.model_flagged
    )
    with pytest.raises(ConformanceGap) as exc:
        map_entry_to_quote(
            _entry(RECORD_A, field_reviews=[review]), intake_for(RECORD_A)
        )
    assert any("fields.key_fields.premium" in g for g in exc.value.gaps)


@pytest.mark.parametrize(
    ("bridge_path", "consumer_path"),
    [
        ("fields.key_fields.currency", "currency"),
        ("fields.key_fields.plan_year", "plan_year"),
        ("fields.key_fields.expiry_date", "expiration_date"),
        ("fields.key_fields.carrier_product_name", "carrier_term_original"),
        ("fields.key_fields.commission_rate_pct", "commission.rate_pct"),
        ("fields.issuer", "carrier"),
        ("fields.tables.rates[3].amount", "rates[3].amount"),
        ("fields.tables.rates[0].rate_basis", "rates[0]"),
        ("fields.tables.rates", "rates"),
        ("fields.tables.design[1].category_original", "design[1].category_original"),
    ],
)
def test_consumer_review_path_table(bridge_path, consumer_path):
    assert consumer.consumer_review_path(bridge_path) == consumer_path


@pytest.mark.parametrize(
    "bridge_path",
    [
        "fields.doctype",
        "fields.key_fields.deductible",
        "fields.tables.riders[0].name",
        "fields.tables.rates[0].premium_cents",
    ],
)
def test_consumer_review_path_refuses_the_unmappable(bridge_path):
    with pytest.raises(ConformanceGap):
        consumer.consumer_review_path(bridge_path)


def test_consumer_money_mirrors_the_consumer_parser():
    assert consumer.consumer_money(" $1,234.50 ") == Decimal("1234.50")
    for bad in ("", "USD 12.40", "12,40 EUR"):
        with pytest.raises(ValueError):
            consumer.consumer_money(bad)
    with pytest.raises(ValueError, match="text"):
        consumer.consumer_money(12.4)


# --------------------------------------------------------------------------- #
# The committed corpus                                                         #
# --------------------------------------------------------------------------- #
def _printed_cells(record: dict) -> list[str]:
    """Every oracle value the page must print, after ``pdf_overrides``."""
    overrides = record.get("pdf_overrides", {})
    fields = record["extraction"]["fields"]
    values = list(fields["key_fields"].values())
    for table, rows in fields["tables"].items():
        for i, row in enumerate(rows):
            values += [
                overrides.get(f"{table}[{i}].{col}", v) for col, v in row.items()
            ]
    return values


@pytest.mark.parametrize("record_id", RECORD_IDS)
def test_conformance_pdfs_are_regenerable_and_carry_the_oracle(record_id, tmp_path):
    record = RECORDS[record_id]
    assert record_id in corpus_pdf.CONFORMANCE_DOC_IDS
    assert record["artifact"] == f"images/{record_id}.pdf"  # the parity pivot
    committed = BENEFITS_ROOT / record["artifact"]
    assert not committed.exists()
    regenerated = corpus_pdf.write_conformance_pdf(record, tmp_path / "regen.pdf")
    expected_bytes = corpus_pdf.build_conformance_pdf(record)
    assert regenerated.read_bytes() == expected_bytes

    page = expected_bytes.decode("cp1252")
    for value in _printed_cells(record):
        assert value in page, f"{value!r} is not printed on {record_id}'s page"
    assert record["party"] in page
    if record_id == RECORD_B:
        assert "per pay period" in page


def test_consumer_mirror_vocabulary():
    def values(enum_cls):
        return {m.value for m in enum_cls}

    assert values(consumer.CoverageTier) == AON_COVERAGE_TIERS
    assert values(consumer.TobaccoClass) == AON_TOBACCO
    assert values(consumer.RateFrequency) == AON_FREQUENCIES
    assert values(consumer.ProductLine) == AON_PRODUCT_LINES


def _imported_roots(py_path: Path) -> set[str]:
    """Top-level package names a file imports absolutely (``tests.x`` -> ``tests``)."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_isolation():
    """``bridge/`` never imports the tests or the consumer; the mapper never imports bridge."""
    offenders = [
        (str(path.relative_to(REPO_ROOT)), root)
        for path in sorted((REPO_ROOT / "bridge").rglob("*.py"))
        for root in _imported_roots(path) & {"tests", "benefits_consumer", "agents"}
    ]
    assert not offenders, offenders
    mapper = REPO_ROOT / "tests/conformance/consumer.py"
    assert "bridge" not in _imported_roots(mapper)


# --------------------------------------------------------------------------- #
# Gated: the live Gemini read                                                  #
# --------------------------------------------------------------------------- #
def _canonical_cell(column: str, raw: str) -> str:
    """Absorb live echo variance (case, a stray ``USD``/``$``, an ``Age`` prefix)."""
    if column in (
        "coverage_tier",
        "tobacco",
        "frequency",
        "rate_basis",
        "product_line",
    ):
        return raw.strip().upper().replace(" ", "_").replace("-", "_")
    if column in ("amount", "benefit_amount"):
        return raw.upper().replace("USD", "").replace("$", "").strip()
    if column == "age_band":
        return re.sub(r"(?i)^\s*ages?\s+", "", raw).strip()
    return raw


def _unprinted_unmodelled(review: FieldReview) -> bool:
    """A live model flags the schema's Path-A-required key fields a rate sheet does not
    print (``premium``, ``deductible``, …) as ``absent_from_document``. The consumer has
    no field for them, so there is nothing to carry. Every OTHER review must still map."""
    if review.reason is not ReviewReason.absent_from_document:
        return False
    try:
        consumer.consumer_review_path(review.path)
    except ConformanceGap:
        return True
    return False


@pytest.mark.skipif(
    not _GEMINI, reason="set BRIDGE_TEST_GEMINI=1 to run the live Gemini engine"
)
async def test_live_gemini_conformance(corpus_server):
    """The REAL engine reads record A's PDF over loopback and it maps onto the quote.

    Record A only: record B's review flag (a frequency printed ``per pay period``) is a
    judgement a live model may or may not make, so asserting it would test the model's
    mood, not the Bridge. A test-local canonicalizer absorbs echo variance and drops
    only the "absent, and the consumer has no such field" reviews
    (:func:`_unprinted_unmodelled`); ``bridge/`` and the oracle are never loosened, and
    confidences are not asserted.
    """
    record = RECORDS[RECORD_A]
    _, _, run = await _journey(RECORD_A, "live", "http", corpus_server)
    entry = run.entry
    assert entry.doctype == "benefits-quote"
    assert entry.gate.value in {"auto_approve", "hitl_review"}

    kf = {k: _canonical_cell(k, v) for k, v in entry.key_fields.items()}
    tables = {
        t: [{c: _canonical_cell(c, v) for c, v in row.items()} for row in rows]
        for t, rows in entry.tables.items()
    }
    canonical = entry.model_copy(
        update={
            "key_fields": kf,
            "tables": tables,
            "field_reviews": [
                r for r in entry.field_reviews if not _unprinted_unmodelled(r)
            ],
        }
    )
    quote = map_entry_to_quote(canonical, intake_for(RECORD_A))
    expected = record["expected_consumer_quote"]

    assert quote.product_line.value == expected["product_line"]
    assert (quote.currency, quote.language[:2]) == ("USD", "en")
    assert quote.plan_year == expected["plan_year"]
    assert quote.effective_date.isoformat() == expected["effective_date"]
    assert quote.expiration_date.isoformat() == expected["expiration_date"]

    def rows(dumped):
        return sorted(
            (
                r["coverage_tier"],
                r["tobacco"],
                r["age_band"],
                Decimal(r["amount"]),
                r["frequency"],
                Decimal(r["benefit_amount"]),
            )
            for r in dumped
        )

    assert rows(quote.model_dump(mode="json")["rates"]) == rows(expected["rates"])
    assert quote.commission.rate_pct == Decimal(expected["commission"]["rate_pct"])
    assert [(d.category, d.value) for d in quote.design] == [
        (d["category"], d["value"]) for d in expected["design"]
    ]


# --------------------------------------------------------------------------- #
# Gated: the real consumer's own model accepts what the journey produced       #
# --------------------------------------------------------------------------- #
_CONSUMER_SCRIPT = """
import json, sys
sys.path.insert(0, ".")
from contract import enums, quote, wire
q = quote.BenefitsQuote.model_validate_json(sys.stdin.read())
models = {m.__name__: sorted(m.model_fields) for m in (
    quote.BenefitsQuote, quote.RateTier, quote.DesignField, quote.CommissionTerms,
    wire.SourceRef, wire.ArtifactRef)}
vocab = {e.__name__: sorted(m.value for m in e) for e in (
    enums.RateFrequency, enums.ProductLine, enums.CoverageTier, enums.TobaccoClass,
    enums.SourcePath)}
print(json.dumps({"quote": q.model_dump(mode="json"), "models": models, "vocab": vocab}))
"""


def _consumer_python() -> tuple[Path, Path]:
    if not _CONSUMER_REPO:
        pytest.skip(
            "set BRIDGE_TEST_CONSUMER_REPO=<consumer checkout, e.g. ~/benefits-consumer> to validate "
            "the mapped quote against the consumer's own BenefitsQuote"
        )
    repo = Path(_CONSUMER_REPO).expanduser()
    python = repo / ".venv/bin/python"
    if not python.is_file():
        pytest.skip(f"no consumer interpreter at {python}; run `uv sync` in {repo}")
    return repo, python


@pytest.mark.parametrize("record_id", RECORD_IDS)
async def test_real_consumer_accepts_mapped_quote(record_id, corpus_server):
    repo, python = _consumer_python()
    _, _, run = await _journey(record_id, "fixture", "http", corpus_server)
    quote = map_entry_to_quote(run.entry, intake_for(record_id))

    proc = subprocess.run(
        [str(python), "-c", _CONSUMER_SCRIPT],
        input=quote.model_dump_json(),
        capture_output=True,
        text=True,
        cwd=repo,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    real = json.loads(proc.stdout)

    ours = quote.model_dump(mode="json")
    assert real["quote"] == {**ours, "fx": None}
    mirror = {
        "BenefitsQuote": consumer.BenefitsQuote,
        "RateTier": consumer.RateTier,
        "DesignField": consumer.DesignField,
        "CommissionTerms": consumer.CommissionTerms,
        "SourceRef": consumer.SourceRef,
        "ArtifactRef": consumer.ArtifactRef,
    }
    for name, model in mirror.items():
        extra = {"fx"} if name == "BenefitsQuote" else set()
        assert set(real["models"][name]) == set(model.model_fields) | extra, name
    for enum_cls in (
        consumer.RateFrequency,
        consumer.ProductLine,
        consumer.CoverageTier,
        consumer.TobaccoClass,
        consumer.SourcePath,
    ):
        assert real["vocab"][enum_cls.__name__] == sorted(m.value for m in enum_cls)
