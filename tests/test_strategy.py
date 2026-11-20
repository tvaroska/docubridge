"""Tests for declarative `assets/strategy.yaml`, `bridge/strategy/`, and `ExchangeTurn.sufficiency` (P11-1)."""

from __future__ import annotations

import json
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import TransportProtocol
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
from pydantic import ValidationError

from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.llm_leg import LlmLeg
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from bridge.extraction.containers import parse_html_reply_advisories
from bridge.extraction.fixture import FixtureExtractor
from bridge.satisfaction import evaluate_turn_sufficiency, sufficiency_to_outstanding
from bridge.strategy import (
    StrategyLoadError,
    evaluate_strategy,
    load_strategy,
    load_strategy_from_yaml,
    render_satisfaction_prose,
    strategy_for,
    waiver_recommendations_from_advisories,
)
from contract.enums import Disposition, Gate, RequirementStatus
from contract.wire import (
    CONTRACT_MAJOR,
    CONTRACT_VERSION,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
    SufficiencyEvidence,
    SufficiencyVerdict,
    WaiverRecommendation,
    _minor,
    decode_wire,
)
from tests._scripted_llm import ScriptedLlm

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"


def _entry(
    doc_id: str,
    doctype: str,
    *,
    disposition: Disposition = Disposition.accepted,
    issuer: str | None = None,
    key_fields: dict[str, str] | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        doc_id=doc_id,
        doctype=doctype,
        disposition=disposition,
        gate=Gate.auto_approve
        if disposition is Disposition.accepted
        else Gate.resubmit,
        issuer=issuer,
        key_fields=key_fields or {},
    )


def _gov_id(doc_id: str = "gov-1", *, expiry: str = "2030-01-01") -> LedgerEntry:
    return _entry(
        doc_id,
        "gov-id",
        key_fields={
            "name": "Jordan Lee",
            "address": "14 Elm Row, Springfield",
            "doc_number": "DL-8392047",
            "issuing_authority": "State DMV",
            "expiry": expiry,
        },
    )


def _bill(
    doc_id: str,
    issuer: str,
    *,
    statement_date: str = "2026-07-05",
    service_address: str = "14 Elm Row, Springfield",
    disposition: Disposition = Disposition.accepted,
) -> LedgerEntry:
    return _entry(
        doc_id,
        "utility-bill",
        disposition=disposition,
        issuer=issuer,
        key_fields={
            "name": "Jordan Lee",
            "service_address": service_address,
            "statement_date": statement_date,
        },
    )


def _loan_entries(
    *,
    appraisal_issuer: str = "Cushman Valuation Partners",
    valuation_date: str = "2026-07-15",
    good_through_date: str = "2026-09-30",
) -> list[LedgerEntry]:
    return [
        _entry(
            "doc-credit-1",
            "cre-credit-agreement",
            issuer="Meridian Syndicated Bank",
            key_fields={
                "loan_amount": "$1,800,000.00",
                "origination_fee": "$18,000.00",
                "currency": "USD",
                "effective_date": "2026-07-01",
            },
        ),
        _entry(
            "doc-appraisal-1",
            "mai-appraisal",
            issuer=appraisal_issuer,
            key_fields={
                "appraised_value": "$2,600,000.00",
                "property_address": "400 Logistics Pkwy, Austin, TX",
                "currency": "USD",
                "valuation_date": valuation_date,
            },
        ),
        _entry(
            "doc-cd-1",
            "trid-closing-disclosure",
            issuer="Meridian Settlement Services",
            key_fields={
                "loan_amount": "$1,800,000.00",
                "origination_fee": "$18,000.00",
                "currency": "USD",
                "effective_date": "2026-07-20",
            },
        ),
        _entry(
            "doc-payoff-1",
            "loan-payoff-statement",
            issuer="First National Bank",
            key_fields={
                "loan_account": "#FNB-4402",
                "payoff_amount": "$412,000.00",
                "good_through_date": good_through_date,
                "currency": "USD",
            },
        ),
    ]


# --------------------------------------------------------------------------- #
# Strategy YAML assets & loader fail-closed invariants                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("process", ["address-proof", "commercial-loan-underwriting"])
def test_declared_strategies_load_and_reference_valid_ontology_fields(
    process: str,
) -> None:
    """Every declared `assets/strategy.yaml` loads cleanly and names real schema fields."""
    strategy = load_strategy(process)
    assert strategy is not None
    assert strategy.process == process
    assert strategy.revision.startswith(f"{strategy.version}@")
    assert len(strategy.revision.split("@", 1)[1]) == 12
    assert strategy_for(process) == strategy
    assert render_satisfaction_prose(strategy)

    for rule in strategy.all_rules():
        schema_path = SKILLS_DIR / rule.doctype / "assets" / "schema.json"
        assert schema_path.is_file(), f"missing schema for {rule.doctype}"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        known_fields = set(
            schema.get("properties", {}).get("key_fields", {}).get("properties", {})
        )
        for field_name in rule.referenced_fields():
            assert field_name in known_fields, (
                f"{process}/{rule.id}: unknown ontology field {field_name!r} for {rule.doctype}"
            )


def test_loader_returns_none_when_absent_and_fails_closed_when_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent strategy -> None; corrupt/unknown-key/policy-leak strategy -> StrategyLoadError."""
    assert load_strategy("benefits-procurement") is None
    assert load_strategy("nonexistent-process") is None
    assert load_strategy("../escape") is None

    for bad_yaml in (
        ":::not-yaml:::[",
        "- just-a-list",
        "schema_version: '1'\nversion: '1.0'\nprocess: p\n",
        "schema_version: '1'\nversion: '1.0'\nprocess: p\nltv_cap: 75\nrules: [{id: r1, item: x, doctype: gov-id}]\n",
        "schema_version: '1'\nversion: '1.0'\nprocess: p\nrules: [{id: r1, item: x, doctype: gov-id, recency_days: 90}]\n",
        "schema_version: '1'\nversion: '1.0'\nprocess: p\nrules: [{id: dup, item: a, doctype: gov-id}, {id: dup, item: b, doctype: utility-bill}]\n",
    ):
        with pytest.raises(StrategyLoadError):
            load_strategy_from_yaml(bad_yaml)

    # Live read-time resolution without caching via BRIDGE_SKILLS_DIR
    skills_copy = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, skills_copy)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(skills_copy))
    before = load_strategy("address-proof")
    assert before is not None
    strat_file = skills_copy / "address-proof" / "assets" / "strategy.yaml"
    strat_file.write_text(
        strat_file.read_text(encoding="utf-8").replace(
            'version: "1.0.0"', 'version: "1.0.1"'
        ),
        encoding="utf-8",
    )
    after = load_strategy("address-proof")
    assert after is not None and after.version == "1.0.1"
    assert after.revision != before.revision

    # Corrupt file on disk fails closed
    strat_file.write_text("schema_version: '1'\nauto_waive: true\n", encoding="utf-8")
    with pytest.raises(StrategyLoadError):
        load_strategy("address-proof")


# --------------------------------------------------------------------------- #
# Address-proof strategy: ordering, alternatives, distinct issuers, recency   #
# --------------------------------------------------------------------------- #
def test_address_proof_ordering_alternatives_distinct_issuers_and_recency() -> None:
    strategy = load_strategy("address-proof")
    assert strategy is not None
    as_of = date(2026, 8, 1)

    # Turn 0 (empty ledger): ordering asks for gov-id BEFORE utility-bill
    v0 = evaluate_strategy(strategy, [], as_of=as_of)
    assert v0.satisfied is False
    assert [r.doctype_hint for r in v0.outstanding] == ["gov-id", "utility-bill"]
    assert all(r.status is RequirementStatus.required for r in v0.outstanding)
    assert sufficiency_to_outstanding(v0).done is False

    # Branch 1: clean unexpired gov-id satisfies immediately
    v_id = evaluate_strategy(strategy, [_gov_id()], as_of=as_of)
    assert v_id.satisfied is True
    assert v_id.outstanding == []
    ev_id = next(e for e in v_id.evidence if e.rule_id == "gov-id-primary")
    assert ev_id.satisfied is True and ev_id.doc_ids == ["gov-1"]

    # Expired gov-id fails even if accepted
    v_exp = evaluate_strategy(strategy, [_gov_id(expiry="2020-01-01")], as_of=as_of)
    assert v_exp.satisfied is False
    assert "expired" in next(
        e.reason for e in v_exp.evidence if e.rule_id == "gov-id-primary"
    )

    # Non-accepted gov-id never satisfies
    v_pending = evaluate_strategy(
        strategy,
        [_entry("gov-p", "gov-id", disposition=Disposition.pending)],
        as_of=as_of,
    )
    assert v_pending.satisfied is False

    # Two bills from same issuer (canonicalized 'PowerCo' == 'Power Co.') -> NOT satisfied
    v_same = evaluate_strategy(
        strategy,
        [_bill("b1", "PowerCo"), _bill("b2", "Power Co.")],
        as_of=as_of,
    )
    assert v_same.satisfied is False
    ev_bills = next(
        e for e in v_same.evidence if e.rule_id == "utility-bill-corroborated"
    )
    assert ev_bills.satisfied is False
    assert ev_bills.issuers == ["power-co"]

    # Two distinct-issuer bills within 90 days + matching service_address -> satisfied
    v_distinct = evaluate_strategy(
        strategy,
        [_bill("b1", "PowerCo"), _bill("b2", "AquaUtil")],
        as_of=as_of,
    )
    assert v_distinct.satisfied is True
    assert v_distinct.outstanding == []
    ev_ok = next(
        e for e in v_distinct.evidence if e.rule_id == "utility-bill-corroborated"
    )
    assert ev_ok.issuers == ["aqua-util", "power-co"]
    assert set(ev_ok.doc_ids) == {"b1", "b2"}

    # Stale bill (>90 days old) disqualified
    v_stale = evaluate_strategy(
        strategy,
        [_bill("b1", "PowerCo", statement_date="2026-01-01"), _bill("b2", "AquaUtil")],
        as_of=as_of,
    )
    assert v_stale.satisfied is False
    assert "stale" in next(
        e.reason for e in v_stale.evidence if e.rule_id == "utility-bill-corroborated"
    )

    # Mismatched service_address disqualified
    v_mismatch = evaluate_strategy(
        strategy,
        [
            _bill("b1", "PowerCo", service_address="14 Elm Row, Springfield"),
            _bill("b2", "AquaUtil", service_address="99 Oak Ave, Shelbyville"),
        ],
        as_of=as_of,
    )
    assert v_mismatch.satisfied is False
    assert "service_address" in next(
        e.reason
        for e in v_mismatch.evidence
        if e.rule_id == "utility-bill-corroborated"
    )


# --------------------------------------------------------------------------- #
# Commercial loan underwriting: all_of, cross-doc issuer, optional, overrides #
# --------------------------------------------------------------------------- #
def test_commercial_loan_underwriting_strategy_and_caller_overrides() -> None:
    strategy = load_strategy("commercial-loan-underwriting")
    assert strategy is not None
    as_of = date(2026, 8, 1)

    # Empty ledger lists items in declared ordering
    v0 = evaluate_strategy(strategy, [], as_of=as_of)
    assert v0.satisfied is False
    assert [r.doctype_hint for r in v0.outstanding] == [
        "cre-credit-agreement",
        "mai-appraisal",
        "trid-closing-disclosure",
        "loan-payoff-statement",
        "commercial-property-quote",
    ]
    assert v0.outstanding[-1].status is RequirementStatus.optional

    # Complete 4-doc package satisfies (optional property quote does not block)
    v_ok = evaluate_strategy(strategy, _loan_entries(), as_of=as_of)
    assert v_ok.satisfied is True
    assert [r.doctype_hint for r in v_ok.outstanding] == ["commercial-property-quote"]
    assert v_ok.outstanding[0].status is RequirementStatus.optional

    # Appraisal from same issuer as credit agreement fails independence check
    v_same_issuer = evaluate_strategy(
        strategy,
        _loan_entries(appraisal_issuer="Meridian Syndicated Bank"),
        as_of=as_of,
    )
    assert v_same_issuer.satisfied is False
    assert "collides with cre-credit-agreement" in next(
        e.reason
        for e in v_same_issuer.evidence
        if e.rule_id == "independent-mai-appraisal"
    )

    # Expired payoff statement fails good_through_date check
    v_exp_payoff = evaluate_strategy(
        strategy,
        _loan_entries(good_through_date="2026-07-01"),
        as_of=as_of,
    )
    assert v_exp_payoff.satisfied is False

    # Caller override waiving loan-payoff-statement wins over base strategy
    entries_without_payoff = _loan_entries()[:-1]
    v_missing_payoff = evaluate_strategy(strategy, entries_without_payoff, as_of=as_of)
    assert v_missing_payoff.satisfied is False

    caller_waiver = RequirementsList(
        requirements=[
            Requirement(
                item="loan-payoff-statement",
                status=RequirementStatus.waived,
                doctype_hint="loan-payoff-statement",
                reason_code="caller_accepted_wire_proof",
            )
        ],
        done=False,
    )
    v_waived_by_caller = evaluate_strategy(
        strategy,
        entries_without_payoff,
        caller_requirements=caller_waiver,
        as_of=as_of,
    )
    assert v_waived_by_caller.satisfied is True
    payoff_ev = next(
        e for e in v_waived_by_caller.evidence if e.rule_id == "loan-payoff-statements"
    )
    assert payoff_ev.satisfied is True
    assert "Waived by calling agent override" in payoff_ev.reason

    # Caller override adding party-scoped payoff requirement enforces it
    caller_extra = RequirementsList(
        requirements=[
            Requirement(
                item="Apex Capital Payoff #7712",
                party="Apex Capital Commercial Funding",
                doctype_hint="loan-payoff-statement",
                status=RequirementStatus.required,
            )
        ],
        done=False,
    )
    v_extra = evaluate_strategy(
        strategy, _loan_entries(), caller_requirements=caller_extra, as_of=as_of
    )
    assert v_extra.satisfied is False
    assert any(
        r.item == "Apex Capital Payoff #7712" and r.status is RequirementStatus.required
        for r in v_extra.outstanding
    )


# --------------------------------------------------------------------------- #
# Recommendation-only waivers & SufficiencyVerdict wire invariants            #
# --------------------------------------------------------------------------- #
def test_waiver_recommendations_never_waive_on_bridge_authority() -> None:
    """ADR-14 D4: WaiverRecommendation never flips satisfied=True or status='waived'."""
    strategy = load_strategy("commercial-loan-underwriting")
    assert strategy is not None
    as_of = date(2026, 8, 1)

    rec = WaiverRecommendation(
        item="loan-payoff-statement",
        doctype_hint="loan-payoff-statement",
        reason_code="paid_in_full_wire_proof",
        reason="Matched outgoing Fedwire PAID IN FULL on Page 14.",
        source_doc_id="doc-bank-stmt",
        citation="Page 14",
        recommended_by="reviewer@bank.example.com",
    )
    verdict = evaluate_strategy(
        strategy,
        _loan_entries()[:-1],
        waiver_recommendations=[rec],
        as_of=as_of,
    )
    assert verdict.satisfied is False
    assert verdict.waiver_recommendations == [rec]
    payoff_ev = next(
        e for e in verdict.evidence if e.rule_id == "loan-payoff-statements"
    )
    assert payoff_ev.satisfied is False
    assert payoff_ev.waiver_recommended is True
    payoff_out = next(
        r for r in verdict.outstanding if r.doctype_hint == "loan-payoff-statement"
    )
    assert payoff_out.status is RequirementStatus.required
    assert payoff_out.reason_code == "waiver_recommended"

    # Converting HTML reply advisories yields recommendations, never waived statuses
    advisories = parse_html_reply_advisories(
        "<table><tr><th>Item</th><th>Status</th></tr>"
        "<tr><td>loan-payoff-statement</td><td>Attested unchanged — carry forward</td></tr></table>",
        source_envelope_id="env-1",
    )
    converted = waiver_recommendations_from_advisories(advisories)
    assert len(converted) == 1
    assert converted[0].item == "loan-payoff-statement"
    assert converted[0].reason_code == "attested_carry_forward"

    # Wire validator actively refuses status='waived' inside SufficiencyVerdict.outstanding
    with pytest.raises(ValidationError, match="must never contain status='waived'"):
        SufficiencyVerdict(
            satisfied=False,
            strategy_revision="1.0.0@000000000000",
            evidence=[
                SufficiencyEvidence(rule_id="r1", satisfied=False, reason="missing doc")
            ],
            outstanding=[
                Requirement(
                    item="loan-payoff-statement", status=RequirementStatus.waived
                )
            ],
        )


def test_exchange_turn_sufficiency_wire_roundtrip_and_tolerant_decode() -> None:
    """CONTRACT_VERSION 1.5 round-trips sufficiency; 1.4 decodes with None; 1.6 strips extra."""
    verdict = evaluate_turn_sufficiency(
        "address-proof", [_gov_id()], as_of=date(2026, 8, 1)
    )
    assert verdict is not None
    turn = ExchangeTurn(
        context_id="ctx-strat-1",
        status=CollectionStatus(entries=[_gov_id()]),
        sufficiency=verdict,
        terminal=True,
    )
    dumped = turn.model_dump(mode="json")
    assert dumped["contract_version"] == CONTRACT_VERSION
    decoded = decode_wire(ExchangeTurn, dumped)
    assert decoded.sufficiency == verdict

    # Pre-1.5 wire payload without sufficiency decodes cleanly to None
    legacy_payload = {
        "contract_version": "1.4",
        "context_id": "ctx-legacy",
        "status": {"entries": []},
    }
    legacy_turn = decode_wire(ExchangeTurn, legacy_payload)
    assert legacy_turn.sufficiency is None

    # Future minor version with extra field inside SufficiencyVerdict is tolerantly stripped
    newer = f"{CONTRACT_MAJOR}.{_minor(CONTRACT_VERSION) + 1}"
    future_payload = dict(dumped)
    future_payload["contract_version"] = newer
    future_payload["sufficiency"] = dict(dumped["sufficiency"], future_flag=True)
    future_turn = decode_wire(ExchangeTurn, future_payload)
    assert future_turn.sufficiency is not None
    assert future_turn.sufficiency.satisfied is True


# --------------------------------------------------------------------------- #
# End-to-end A2A edge: Collector + Path-A populate ExchangeTurn.sufficiency   #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def _a2a_client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        yield await ClientFactory(config).create_from_url("http://test/")


def _extract_turn(task) -> ExchangeTurn:
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return ExchangeTurn.model_validate(MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact")


async def test_a2a_collector_and_path_a_populate_sufficiency_with_zero_llm_calls() -> (
    None
):
    """Both Collector and Path-A A2A turns populate ExchangeTurn.sufficiency with 0 LLM calls."""
    llm = ScriptedLlm(max_calls=0)
    app = build_a2a_app(
        url="http://test/",
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        edge_mode="hybrid",
        llm_leg=LlmLeg(model=llm),
    )

    async def _send(message: Message) -> ExchangeTurn:
        async with _a2a_client(app) as client:
            task_id = None
            async for ev in client.send_message(SendMessageRequest(message=message)):
                if ev.HasField("task"):
                    task_id = ev.task.id
                elif ev.HasField("status_update"):
                    task_id = ev.status_update.task_id
            assert task_id is not None
            task = await client.get_task(GetTaskRequest(id=task_id))
            assert task.status.state == TaskState.TASK_STATE_COMPLETED
            return _extract_turn(task)

    # Collector turn (by-reference gov-id)
    doc_part = ph.new_url_part("images/gov-id-clean.svg", media_type="image/svg+xml")
    doc_part.metadata.CopyFrom(
        ParseDict({"doc_id": "gov-id-clean"}, struct_pb2.Struct())
    )
    collector_msg = Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=[
            *encode_collect_request(CollectRequest(process="address-proof")),
            doc_part,
        ],
    )
    collector_turn = await _send(collector_msg)
    assert collector_turn.sufficiency is not None
    assert collector_turn.sufficiency.satisfied is True
    assert collector_turn.sufficiency.outstanding == []

    # Path-A turn (structured gov-id assertion)
    path_a_msg = Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        parts=[
            *encode_collect_request(CollectRequest(process="address-proof")),
            ph.new_data_part(
                {
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
            ),
        ],
    )
    path_a_turn = await _send(path_a_msg)
    assert path_a_turn.sufficiency is not None
    assert path_a_turn.sufficiency.satisfied is True
    assert llm.call_count == 0


# --------------------------------------------------------------------------- #
# Regression tests for multi-override collision, all-optional, rec isolation  #
# --------------------------------------------------------------------------- #
def test_multi_caller_overrides_sharing_doctype_hint_never_drop_or_double_count() -> (
    None
):
    """Multiple caller requirements sharing doctype_hint are evaluated independently."""
    strategy = load_strategy("commercial-loan-underwriting")
    assert strategy is not None
    as_of = date(2026, 8, 1)

    # 3 base docs + 0 payoffs + 1 required payoff + 1 waived payoff sharing doctype_hint
    v_mixed = evaluate_strategy(
        strategy,
        _loan_entries()[:-1],
        caller_requirements=RequirementsList(
            requirements=[
                Requirement(
                    item="Apex Capital Payoff #7712",
                    status=RequirementStatus.required,
                    doctype_hint="loan-payoff-statement",
                ),
                Requirement(
                    item="Internal LOC Payoff #LOC-9012",
                    status=RequirementStatus.waived,
                    doctype_hint="loan-payoff-statement",
                ),
            ],
            done=False,
        ),
        as_of=as_of,
    )
    assert v_mixed.satisfied is False
    assert any(
        r.item == "Apex Capital Payoff #7712" and r.status is RequirementStatus.required
        for r in v_mixed.outstanding
    )
    assert any(
        e.rule_id == "caller:apexcapitalpayoff7712" and e.satisfied is False
        for e in v_mixed.evidence
    )
    assert any(
        e.rule_id == "caller:internallocpayoffloc9012" and e.satisfied is True
        for e in v_mixed.evidence
    )

    # Two required payoffs sharing doctype_hint with only 1 payoff doc submitted -> False
    v_one_for_two = evaluate_strategy(
        strategy,
        _loan_entries(),
        caller_requirements=RequirementsList(
            requirements=[
                Requirement(
                    item="First National Payoff #FNB-4402",
                    status=RequirementStatus.required,
                    doctype_hint="loan-payoff-statement",
                ),
                Requirement(
                    item="Apex Capital Payoff #7712",
                    status=RequirementStatus.required,
                    doctype_hint="loan-payoff-statement",
                ),
            ],
            done=False,
        ),
        as_of=as_of,
    )
    assert v_one_for_two.satisfied is False
    assert any(r.item == "Apex Capital Payoff #7712" for r in v_one_for_two.outstanding)

    # Supplying the second distinct payoff satisfies both caller items
    second_payoff = _entry(
        "doc-payoff-2",
        "loan-payoff-statement",
        issuer="Apex Capital Commercial Funding",
        key_fields={
            "loan_account": "#7712",
            "payoff_amount": "$1,089,790.00",
            "good_through_date": "2026-09-30",
            "currency": "USD",
        },
    )
    v_two_for_two = evaluate_strategy(
        strategy,
        [*_loan_entries(), second_payoff],
        caller_requirements=RequirementsList(
            requirements=[
                Requirement(
                    item="First National Payoff #FNB-4402",
                    status=RequirementStatus.required,
                    doctype_hint="loan-payoff-statement",
                ),
                Requirement(
                    item="Apex Capital Payoff #7712",
                    status=RequirementStatus.required,
                    doctype_hint="loan-payoff-statement",
                ),
            ],
            done=False,
        ),
        as_of=as_of,
    )
    assert v_two_for_two.satisfied is True


def test_optional_override_never_causes_vacuous_zero_doc_satisfaction() -> None:
    """Overriding a branch's only rule to optional must not satisfy with 0 documents."""
    strategy = load_strategy("address-proof")
    assert strategy is not None
    as_of = date(2026, 8, 1)

    v_empty_opt = evaluate_strategy(
        strategy,
        [],
        caller_requirements=RequirementsList(
            requirements=[
                Requirement(item="gov-id", status=RequirementStatus.optional)
            ],
            done=False,
        ),
        as_of=as_of,
    )
    assert v_empty_opt.satisfied is False
    assert all(e.satisfied is False for e in v_empty_opt.evidence)

    # Explicit caller waiver still satisfies the branch
    v_waived = evaluate_strategy(
        strategy,
        [],
        caller_requirements=RequirementsList(
            requirements=[Requirement(item="gov-id", status=RequirementStatus.waived)],
            done=False,
        ),
        as_of=as_of,
    )
    assert v_waived.satisfied is True


def test_waiver_recommendation_does_not_cross_contaminate_sibling_caller_items() -> (
    None
):
    """A waiver recommendation for LOC #LOC-9012 must not tag unrelated Apex #7712."""
    strategy = load_strategy("commercial-loan-underwriting")
    assert strategy is not None
    as_of = date(2026, 8, 1)

    loc_rec = WaiverRecommendation(
        item="Internal LOC #LOC-9012",
        doctype_hint="loan-payoff-statement",
        reason_code="on_us_internal_facility",
        reason="Verified on internal core ledger.",
    )
    verdict = evaluate_strategy(
        strategy,
        _loan_entries()[:-1],
        caller_requirements=RequirementsList(
            requirements=[
                Requirement(
                    item="Apex Capital Payoff #7712",
                    party="Apex Capital",
                    doctype_hint="loan-payoff-statement",
                    status=RequirementStatus.required,
                ),
                Requirement(
                    item="Internal LOC #LOC-9012",
                    doctype_hint="loan-payoff-statement",
                    status=RequirementStatus.required,
                ),
            ],
            done=False,
        ),
        waiver_recommendations=[loc_rec],
        as_of=as_of,
    )
    assert verdict.satisfied is False
    apex_ev = next(
        e for e in verdict.evidence if e.rule_id == "caller:apexcapitalpayoff7712"
    )
    apex_out = next(
        r for r in verdict.outstanding if r.item == "Apex Capital Payoff #7712"
    )
    assert apex_ev.waiver_recommended is False
    assert apex_out.reason_code != "waiver_recommended"

    loc_ev = next(
        e for e in verdict.evidence if e.rule_id == "caller:internallocloc9012"
    )
    loc_out = next(r for r in verdict.outstanding if r.item == "Internal LOC #LOC-9012")
    assert loc_ev.waiver_recommended is True
    assert loc_out.reason_code == "waiver_recommended"
