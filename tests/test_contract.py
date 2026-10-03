"""Tests for contract/ wire types and extraction models."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contract import (
    CONTRACT_VERSION,
    ArtifactRef,
    CollectionStatus,
    CollectRequest,
    Disposition,
    ExchangeTurn,
    ExtractionFields,
    ExtractionResult,
    FieldReview,
    Gate,
    GovIdKeyFields,
    HumanReviewResolution,
    LedgerEntry,
    LifecycleStatus,
    Requirement,
    RequirementsList,
    RequirementStatus,
    ReviewReason,
    UtilityBillKeyFields,
    field_reviews_for,
    fields_requiring_review,
)
from contract.wire import _strip_to_known, decode_wire


class TestEnumValues:
    """Enums are plain strings on the wire."""

    def test_disposition_values(self):
        assert Disposition.accepted.value == "accepted"
        assert Disposition.rejected.value == "rejected"
        assert Disposition.pending.value == "pending"

    def test_gate_values(self):
        assert Gate.auto_approve.value == "auto_approve"
        assert Gate.hitl_review.value == "hitl_review"
        assert Gate.resubmit.value == "resubmit"
        assert Gate.unsupported.value == "unsupported"

    def test_requirement_status_values(self):
        assert RequirementStatus.required.value == "required"
        assert RequirementStatus.optional.value == "optional"
        assert RequirementStatus.satisfied.value == "satisfied"
        assert RequirementStatus.waived.value == "waived"

    def test_lifecycle_status_values(self):
        """P4-5: LifecycleStatus enum has the SLA ladder values."""
        assert LifecycleStatus.on_track.value == "on_track"
        assert LifecycleStatus.overdue.value == "overdue"
        assert LifecycleStatus.reminder.value == "reminder"
        assert LifecycleStatus.escalated.value == "escalated"
        # Verify complete set
        assert set(LifecycleStatus) == {
            LifecycleStatus.on_track,
            LifecycleStatus.overdue,
            LifecycleStatus.reminder,
            LifecycleStatus.escalated,
        }

    def test_enum_serializes_to_string(self):
        """Enums dump to plain strings, not enum repr."""
        entry = LedgerEntry(
            doc_id="test",
            doctype="gov-id",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
        )
        dumped = entry.model_dump(mode="json")
        assert dumped["disposition"] == "accepted"
        assert dumped["gate"] == "auto_approve"


class TestRoundTripStability:
    """Every model survives JSON round-trip (model_dump → model_validate)."""

    def test_artifact_ref(self):
        inst = ArtifactRef(uri="gs://bucket/obj", mime_type="image/png", name="doc.png")
        assert ArtifactRef.model_validate(inst.model_dump(mode="json")) == inst

    def test_ledger_entry(self):
        inst = LedgerEntry(
            doc_id="test-1",
            doctype="gov-id",
            disposition=Disposition.accepted,
            gate=Gate.auto_approve,
            issuer="power-co",
            key_fields={"name": "Test User"},
            artifact=ArtifactRef(uri="gs://bucket/doc"),
        )
        assert LedgerEntry.model_validate(inst.model_dump(mode="json")) == inst

    def test_requirement(self):
        inst = Requirement(
            item="Government ID",
            status=RequirementStatus.required,
            doctype_hint="gov-id",
        )
        assert Requirement.model_validate(inst.model_dump(mode="json")) == inst

    def test_requirements_list(self):
        inst = RequirementsList(
            requirements=[
                Requirement(item="Gov ID", status=RequirementStatus.satisfied),
            ],
            done=True,
        )
        assert RequirementsList.model_validate(inst.model_dump(mode="json")) == inst

    def test_collection_status(self):
        inst = CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="test",
                    doctype="gov-id",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                )
            ],
            outstanding=RequirementsList(done=False),
        )
        assert CollectionStatus.model_validate(inst.model_dump(mode="json")) == inst

    def test_collect_request(self):
        inst = CollectRequest(
            process="address-proof",
            party="jordan-lee",
            context_id="ctx-123",
            message="Upload proof",
        )
        assert CollectRequest.model_validate(inst.model_dump(mode="json")) == inst

    def test_exchange_turn(self):
        inst = ExchangeTurn(
            context_id="ctx-123",
            status=CollectionStatus(),
            requirements=RequirementsList(done=False),
            terminal=False,
        )
        assert ExchangeTurn.model_validate(inst.model_dump(mode="json")) == inst

    def test_exchange_turn_with_lifecycle(self):
        """P4-5: ExchangeTurn.lifecycle round-trips and defaults to on_track."""
        # Explicit lifecycle value
        inst = ExchangeTurn(
            context_id="ctx-123",
            status=CollectionStatus(),
            requirements=RequirementsList(done=False),
            terminal=False,
            lifecycle=LifecycleStatus.escalated,
        )
        assert ExchangeTurn.model_validate(inst.model_dump(mode="json")) == inst
        assert inst.lifecycle == LifecycleStatus.escalated

        # Default lifecycle when omitted (old-payload compatibility)
        inst_default = ExchangeTurn(
            context_id="ctx-123",
            status=CollectionStatus(),
        )
        assert inst_default.lifecycle == LifecycleStatus.on_track

        # Old payload (no lifecycle field) validates with default
        old_payload = {
            "context_id": "ctx-123",
            "status": {"entries": []},
        }
        turn = ExchangeTurn.model_validate(old_payload)
        assert turn.lifecycle == LifecycleStatus.on_track

    def test_extraction_fields(self):
        inst = ExtractionFields(
            doctype="gov-id",
            issuer="power-co",
            key_fields={"name": "Test", "address": "123 Main"},
        )
        assert ExtractionFields.model_validate(inst.model_dump(mode="json")) == inst

    def test_extraction_result(self):
        inst = ExtractionResult(
            fields=ExtractionFields(doctype="gov-id"),
            overall_confidence=0.95,
            field_confidence={"name": 0.96},
            legible=True,
            flagged_fields=["expiry"],
        )
        assert ExtractionResult.model_validate(inst.model_dump(mode="json")) == inst

    def test_gov_id_key_fields(self):
        inst = GovIdKeyFields(
            name="Jordan Lee",
            address="14 Elm Row",
            doc_number="DL-123",
            issuing_authority="DMV",
            expiry="2030-01-01",
        )
        assert GovIdKeyFields.model_validate(inst.model_dump(mode="json")) == inst

    def test_utility_bill_key_fields(self):
        inst = UtilityBillKeyFields(
            name="Jordan Lee",
            service_address="14 Elm Row",
            statement_date="2026-07-01",
        )
        assert UtilityBillKeyFields.model_validate(inst.model_dump(mode="json")) == inst


class TestExtraForbid:
    """extra='forbid' rejects unknown wire fields."""

    def test_collect_request_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            CollectRequest.model_validate({"process": "address-proof", "bogus": 1})

    def test_ledger_entry_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            LedgerEntry.model_validate({
                "doc_id": "test",
                "doctype": "gov-id",
                "disposition": "accepted",
                "gate": "auto_approve",
                "unknown_key": "should fail",
            })

    def test_extraction_result_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate({
                "fields": {"doctype": "gov-id"},
                "overall_confidence": 0.9,
                "legible": True,
                "extra_field": "bad",
            })


class TestRequiredFields:
    """Omitting required fields raises ValidationError."""

    def test_collect_request_requires_process(self):
        with pytest.raises(ValidationError):
            CollectRequest.model_validate({})

    def test_ledger_entry_requires_disposition(self):
        with pytest.raises(ValidationError):
            LedgerEntry.model_validate({
                "doc_id": "test",
                "doctype": "gov-id",
                "gate": "auto_approve",
            })

    def test_exchange_turn_requires_context_id(self):
        with pytest.raises(ValidationError):
            ExchangeTurn.model_validate({
                "status": {"entries": []},
            })


class TestContractVersionDefault:
    """contract_version defaults to CONTRACT_VERSION on top-level types."""

    def test_collect_request_has_default(self):
        req = CollectRequest(process="address-proof")
        assert req.contract_version == CONTRACT_VERSION

    def test_exchange_turn_has_default(self):
        turn = ExchangeTurn(context_id="ctx-123", status=CollectionStatus())
        assert turn.contract_version == CONTRACT_VERSION


class TestVersionGatedTolerantReader:
    """decode_wire (D2): strict within our contract major, tolerant across a bump.

    Covers S0-bug-1 — contract_version is now inspected, and a newer-major
    producer's added fields degrade gracefully instead of hard-failing the
    consumer's model_validate, while same-major decode stays strict (extra=forbid).
    """

    def _turn_payload(self, **extra):
        payload = {
            "context_id": "ctx-1",
            "status": {"entries": []},
        }
        payload.update(extra)
        return payload

    def test_same_major_still_rejects_unknown_field(self):
        """Within our major, an added field is still a hard error (strict edge)."""
        payload = self._turn_payload(contract_version=CONTRACT_VERSION, bogus="x")
        with pytest.raises(ValidationError):
            decode_wire(ExchangeTurn, payload)

    def test_no_version_declared_is_treated_as_our_major(self):
        """A payload omitting contract_version defaults to our major → strict."""
        payload = self._turn_payload(bogus="x")  # no contract_version
        with pytest.raises(ValidationError):
            decode_wire(ExchangeTurn, payload)

    def test_newer_major_tolerates_unknown_top_level_field(self):
        """A newer major (bumped contract_version) degrades: unknown field ignored."""
        payload = self._turn_payload(contract_version="2", brand_new_field="future")
        turn = decode_wire(ExchangeTurn, payload)
        assert turn.context_id == "ctx-1"
        assert turn.contract_version == "2"
        assert not hasattr(turn, "brand_new_field")

    def test_newer_major_tolerates_unknown_nested_field(self):
        """Tolerance is recursive: an added field on a nested LedgerEntry is stripped."""
        payload = self._turn_payload(
            contract_version="2",
            status={
                "entries": [
                    {
                        "doc_id": "d1",
                        "doctype": "gov-id",
                        "disposition": "accepted",
                        "gate": "auto_approve",
                        "future_confidence": 0.99,  # field a v2 producer added
                    }
                ]
            },
        )
        turn = decode_wire(ExchangeTurn, payload)
        assert len(turn.status.entries) == 1
        assert turn.status.entries[0].doc_id == "d1"

    def test_newer_major_still_requires_required_fields(self):
        """Tolerance adds fields only — a missing required field still fails."""
        with pytest.raises(ValidationError):
            decode_wire(ExchangeTurn, {"contract_version": "2"})  # no context_id/status

    def test_collect_request_wrong_shape_never_decodes_as_turn(self):
        """Version-gated decode preserves select-by-shape: a CollectRequest (even v2)
        blob is not a valid ExchangeTurn (missing required status)."""
        blob = {"contract_version": "2", "process": "address-proof", "context_id": "c"}
        with pytest.raises(ValidationError):
            decode_wire(ExchangeTurn, blob)

    def test_non_dict_passthrough(self):
        """A non-dict payload defers to plain model_validate (its own error path)."""
        with pytest.raises(ValidationError):
            decode_wire(ExchangeTurn, ["not", "a", "dict"])


class TestCorpusParity:
    """Extraction models validate against the eval corpus oracle."""

    @pytest.fixture
    def corpus(self):
        """Load wiki/evals/address/expected.json."""
        corpus_path = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
        with open(corpus_path) as f:
            return json.load(f)

    def test_all_extraction_blocks_validate(self, corpus):
        """All 8 corpus extraction blocks parse as ExtractionResult."""
        for doc in corpus["documents"]:
            extraction = doc["extraction"]
            # Should not raise
            result = ExtractionResult.model_validate(extraction)
            assert isinstance(result, ExtractionResult)

    def test_extraction_round_trip_stability(self, corpus):
        """Model equality after re-validation (not byte-equality with source)."""
        for doc in corpus["documents"]:
            extraction = doc["extraction"]
            parsed = ExtractionResult.model_validate(extraction)
            # Round-trip through model_dump → model_validate
            round_tripped = ExtractionResult.model_validate(parsed.model_dump(mode="json"))
            assert parsed == round_tripped

    def test_corpus_disposition_enum_coverage(self, corpus):
        """Every corpus expected_disposition constructs the enum."""
        for doc in corpus["documents"]:
            disp = doc["expected_disposition"]
            # Should not raise
            enum_val = Disposition(disp)
            assert isinstance(enum_val, Disposition)

    def test_corpus_gate_enum_coverage(self, corpus):
        """Every corpus expected_gate constructs the enum."""
        for doc in corpus["documents"]:
            gate = doc["expected_gate"]
            # Should not raise
            enum_val = Gate(gate)
            assert isinstance(enum_val, Gate)

    def test_passport_without_field_confidence(self, corpus):
        """Passport doc omits field_confidence; model fills default."""
        passport_doc = next(d for d in corpus["documents"] if d["id"] == "passport-unsupported")
        extraction = passport_doc["extraction"]
        assert "field_confidence" not in extraction

        result = ExtractionResult.model_validate(extraction)
        assert result.field_confidence == {}

    def test_blurry_without_key_fields(self, corpus):
        """Blurry doc omits key_fields; model fills default."""
        blurry_doc = next(d for d in corpus["documents"] if d["id"] == "bill-aquautil-blurry")
        extraction = blurry_doc["extraction"]
        assert "key_fields" not in extraction["fields"]

        result = ExtractionResult.model_validate(extraction)
        assert result.fields.key_fields == {}


class TestReviewVocabulary:
    """S0-debt-2: the consumer's review vocabulary, mapped (not renamed).

    Consumer speaks `extraction_confidence` + `fields_requiring_review` as dotted
    paths; the wire keeps `overall_confidence`/`flagged_fields`. The mapping is
    executable (`fields_requiring_review` / `field_reviews_for`) and inbound-tolerant.
    """

    def _result(self, **extra):
        payload = {
            "fields": {"doctype": "gov-id", "key_fields": {"name": "Jordan Lee"}},
            "overall_confidence": 0.8,
            "legible": True,
        }
        payload.update(extra)
        return ExtractionResult.model_validate(payload)

    def test_field_review_round_trip(self):
        inst = FieldReview(
            path="fields.key_fields.expiry",
            reason=ReviewReason.absent_from_document,
            confidence=0.4,
            detail="not printed on this copy",
        )
        assert FieldReview.model_validate(inst.model_dump(mode="json")) == inst

    def test_field_review_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            FieldReview.model_validate({
                "path": "fields.key_fields.name",
                "reason": "derived",
                "bogus": 1,
            })

    def test_field_review_rejects_unknown_reason(self):
        with pytest.raises(ValidationError):
            FieldReview.model_validate({"path": "x", "reason": "because"})

    def test_bare_key_field_maps_to_dotted_path(self):
        result = self._result(flagged_fields=["name"], field_confidence={"name": 0.62})
        assert fields_requiring_review(result) == ["fields.key_fields.name"]
        review = field_reviews_for(result)[0]
        assert review.reason is ReviewReason.model_flagged
        assert review.confidence == 0.62

    def test_top_level_names_map_to_their_own_paths(self):
        result = self._result(flagged_fields=["issuer", "doctype"])
        assert fields_requiring_review(result) == ["fields.issuer", "fields.doctype"]
        assert {r.reason for r in field_reviews_for(result)} == {ReviewReason.model_flagged}

    def test_already_dotted_name_passes_through(self):
        result = self._result(flagged_fields=["commission.rate_pct", "rates[0].amount"])
        assert fields_requiring_review(result) == ["commission.rate_pct", "rates[0].amount"]

    def test_flag_for_a_field_the_document_lacks_is_absent_from_document(self):
        result = self._result(flagged_fields=["expiry"])
        review = field_reviews_for(result)[0]
        assert review.path == "fields.key_fields.expiry"
        assert review.reason is ReviewReason.absent_from_document
        assert review.confidence is None

    def test_engine_review_wins_over_the_derived_entry(self):
        result = self._result(
            flagged_fields=["name"],
            field_reviews=[{"path": "fields.key_fields.name", "reason": "derived"}],
        )
        reviews = field_reviews_for(result)
        assert len(reviews) == 1
        assert reviews[0].reason is ReviewReason.derived

    def test_engine_bare_path_is_canonicalized_and_deduped(self):
        """An engine answering with the bare key name collapses onto its own flag."""
        result = self._result(
            flagged_fields=["name"],
            field_reviews=[{"path": "name", "reason": "low_confidence", "confidence": 0.3}],
        )
        reviews = field_reviews_for(result)
        assert [r.path for r in reviews] == ["fields.key_fields.name"]
        assert reviews[0].reason is ReviewReason.low_confidence

    def test_order_is_stable_engine_first_then_flags(self):
        result = self._result(
            flagged_fields=["expiry", "name"],
            field_reviews=[{"path": "fields.issuer", "reason": "derived"}],
        )
        assert fields_requiring_review(result) == [
            "fields.issuer",
            "fields.key_fields.expiry",
            "fields.key_fields.name",
        ]

    def test_duplicate_flags_dedupe(self):
        result = self._result(flagged_fields=["name", "name"])
        assert fields_requiring_review(result) == ["fields.key_fields.name"]

    def test_consumer_alias_accepted_inbound(self):
        result = self._result(fields_requiring_review=["commission.rate_pct"])
        assert result.flagged_fields == ["commission.rate_pct"]

    def test_we_dump_our_own_name_not_the_consumers(self):
        dumped = self._result(fields_requiring_review=["x.y"]).model_dump(mode="json")
        assert dumped["flagged_fields"] == ["x.y"]
        assert "fields_requiring_review" not in dumped

    def test_both_review_keys_with_different_values_fails_loud(self):
        with pytest.raises(ValidationError):
            self._result(flagged_fields=["name"], fields_requiring_review=["expiry"])

    def test_both_review_keys_agreeing_is_accepted(self):
        result = self._result(flagged_fields=["name"], fields_requiring_review=["name"])
        assert result.flagged_fields == ["name"]

    def test_field_reviews_is_optional_and_defaulted(self):
        """Path A's shape selector relies on this: never a required field."""
        assert self._result().field_reviews == []

    def test_result_round_trips_with_reviews(self):
        inst = ExtractionResult(
            fields=ExtractionFields(doctype="gov-id"),
            overall_confidence=0.9,
            legible=True,
            field_reviews=[FieldReview(path="fields.doctype", reason=ReviewReason.derived)],
        )
        assert ExtractionResult.model_validate(inst.model_dump(mode="json")) == inst

    def test_strip_to_known_keeps_the_validation_alias(self):
        """A foreign-major payload using the consumer's spelling must survive the strip.

        `_strip_to_known` reads `field.alias`, which an `AliasChoices`
        validation_alias is invisible to — stripping the accepted name back out
        would defeat the tolerance for exactly the caller it exists for.
        """
        payload = {
            "fields": {"doctype": "gov-id", "key_fields": {}},
            "overall_confidence": 0.9,
            "legible": True,
            "fields_requiring_review": ["commission.rate_pct"],
            "some_v2_field": "future",
        }
        stripped = _strip_to_known(ExtractionResult, payload)
        assert stripped["fields_requiring_review"] == ["commission.rate_pct"]
        assert "some_v2_field" not in stripped
        assert ExtractionResult.model_validate(stripped).flagged_fields == ["commission.rate_pct"]

    def test_ledger_entry_carries_field_reviews_across_a_major_bump(self):
        """LedgerEntry.field_reviews is additive: decode_wire recurses into it."""
        payload = {
            "contract_version": "2",
            "context_id": "ctx-1",
            "status": {"entries": [{
                "doc_id": "d1",
                "doctype": "gov-id",
                "disposition": "pending",
                "gate": "hitl_review",
                "field_reviews": [{
                    "path": "fields.key_fields.expiry",
                    "reason": "absent_from_document",
                    "v2_extra": "future",
                }],
            }]},
        }
        turn = decode_wire(ExchangeTurn, payload)
        review = turn.status.entries[0].field_reviews[0]
        assert review.path == "fields.key_fields.expiry"
        assert review.reason is ReviewReason.absent_from_document

    def test_corpus_blocks_need_no_reviews(self):
        """The oracle is untouched by this vocabulary: derivation covers it."""
        corpus_path = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
        with open(corpus_path) as f:
            corpus = json.load(f)
        for doc in corpus["documents"]:
            result = ExtractionResult.model_validate(doc["extraction"])
            assert result.field_reviews == []
            assert len(fields_requiring_review(result)) == len(result.flagged_fields)


class TestHumanReviewResolution:
    """S3-edge-6 (contract 1.4): the reviewer's resolution of a parked
    ``request_human_review`` call (S3-edge-1 D10) — additive, strict within 1.x at or
    below our minor, tolerant of a newer minor's added fields."""

    def _payload(self, **extra):
        payload = {
            "call_id": "adk-call-1",
            "resolution": "accepted",
            "message": "Reviewed by a person.",
        }
        payload.update(extra)
        return payload

    def test_contract_version_bumped_for_human_review(self):
        assert CONTRACT_VERSION == "1.4"
        assert HumanReviewResolution(**self._payload()).contract_version == "1.4"

    def test_human_review_round_trip(self):
        res = HumanReviewResolution(**self._payload(reviewer="alice"))
        again = decode_wire(HumanReviewResolution, json.loads(res.model_dump_json()))
        assert again == res
        assert again.resolution == "accepted"
        assert again.reviewer == "alice"

    def test_human_review_reviewer_optional(self):
        res = HumanReviewResolution(**self._payload())
        assert res.reviewer is None

    def test_human_review_newer_minor_extra_stripped(self):
        res = decode_wire(
            HumanReviewResolution,
            self._payload(contract_version="1.5", review_tool_build="2027.1"),
        )
        assert res.call_id == "adk-call-1"
        assert res.contract_version == "1.5"
        assert not hasattr(res, "review_tool_build")

    def test_human_review_same_minor_extra_rejected(self):
        with pytest.raises(ValidationError):
            decode_wire(
                HumanReviewResolution,
                self._payload(contract_version="1.4", review_tool_build="x"),
            )

    def test_human_review_unknown_resolution_rejected(self):
        with pytest.raises(ValidationError):
            HumanReviewResolution(**self._payload(resolution="maybe"))

    def test_human_review_requires_call_id_and_message(self):
        with pytest.raises(ValidationError):
            HumanReviewResolution(resolution="accepted", message="m")
        with pytest.raises(ValidationError):
            HumanReviewResolution(call_id="c", resolution="accepted")
