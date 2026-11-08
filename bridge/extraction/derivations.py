"""Deterministic Stage-B field derivations and extraction-hint execution (`Rule 0`).

Executes inside ``ExtractionPort`` (after Stage-A LLM transcription or fixture lookup) so
models only transcribe raw printed strings and anchors while pure-Python code computes
derived fields, normalizes polarity, and applies promoted ``EXTRACTION_HINT`` rules with
SHA-256 ``FieldDerivation`` receipts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from contract.extraction import (
    ExtractionFields,
    ExtractionResult,
    FieldDerivation,
)

_PCT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")


def _receipt_hash(rule_id: str, inputs: dict[str, Any], output: str) -> str:
    payload = json.dumps(
        {"rule": rule_id, "inputs": inputs, "output": output},
        sort_keys=True,
    ).encode("utf-8")
    return f"rcpt-{hashlib.sha256(payload).hexdigest()[:16]}"


def apply_field_derivations(
    fields: ExtractionFields,
    *,
    active_hints: Sequence[Any] = (),
    requested_fields: Sequence[str] | None = None,
) -> tuple[ExtractionFields, tuple[str, ...]]:
    """Run Stage-B deterministic derivations and promoted ``EXTRACTION_HINT`` rules over ``fields``."""
    updated_key_fields = dict(fields.key_fields)
    updated_derivations = dict(fields.derivations)
    applied_rule_ids: list[str] = []

    # 1. Apply promoted Agent Gym EXTRACTION_HINT rules matching (issuer, doctype)
    for hint in active_hints:
        field_path = getattr(hint, "field_path", "") or ""
        canonical_target = getattr(hint, "canonical_target", None)
        rule_id = getattr(hint, "rule_id", "") or ""
        if field_path and canonical_target:
            raw_before = updated_key_fields.get(field_path, "")
            if raw_before != canonical_target:
                updated_key_fields[field_path] = canonical_target
                rcpt = _receipt_hash(
                    rule_id or "extraction-hint",
                    {"field_path": field_path, "raw_value": raw_before},
                    canonical_target,
                )
                updated_derivations[field_path] = FieldDerivation(
                    raw_value=raw_before,
                    polarity="EXTRACTION_HINT_CANONICAL",
                    normalized_value=canonical_target,
                    derivation_rule=rule_id or "extraction-hint",
                    derived_from=[field_path],
                    receipt_id=rcpt,
                )
            if rule_id and rule_id not in applied_rule_ids:
                applied_rule_ids.append(rule_id)

    # 2. Deterministic Coinsurance Polarity Inversion (100% - Member% -> Employer%)
    raw_member_pct = updated_key_fields.get("member_coinsurance_pct")
    if raw_member_pct and "employer_coinsurance_pct" not in updated_key_fields:
        m = _PCT_RE.match(raw_member_pct)
        if m:
            try:
                member_dec = Decimal(m.group(1))
                if Decimal("0") <= member_dec <= Decimal("100"):
                    emp_dec = Decimal("100") - member_dec
                    emp_str = (
                        f"{int(emp_dec)}%"
                        if emp_dec == emp_dec.to_integral_value()
                        else f"{emp_dec.normalize()}%"
                    )
                    rule_name = "coinsurance-polarity-inversion"
                    rcpt = _receipt_hash(
                        rule_name,
                        {"member_coinsurance_pct": raw_member_pct},
                        emp_str,
                    )
                    updated_key_fields["employer_coinsurance_pct"] = emp_str
                    updated_derivations["employer_coinsurance_pct"] = FieldDerivation(
                        raw_value=raw_member_pct,
                        polarity="MEMBER_PAYS",
                        normalized_value=emp_str,
                        derivation_rule=rule_name,
                        derived_from=["member_coinsurance_pct"],
                        parameters={"formula": "100% - member_coinsurance_pct"},
                        receipt_id=rcpt,
                    )
            except InvalidOperation:
                pass

    # 3. Deterministic Paid-in-Full Wire -> Remaining Liability Balance ($0.00)
    wire_ref = (
        updated_key_fields.get("wire_reference", "")
        or updated_key_fields.get("payoff_waiver_status", "")
        or ""
    )
    if (
        ("PAID IN FULL" in wire_ref.upper() or "paid_in_full" in wire_ref.lower())
        and "remaining_liability_balance" not in updated_key_fields
        and (
            "wire_reference" in updated_key_fields
            or "payoff_waiver_status" in updated_key_fields
        )
    ):
        source_key = (
            "wire_reference"
            if "wire_reference" in updated_key_fields
            else "payoff_waiver_status"
        )
        rule_name = "paid-in-full-wire-zero-balance"
        rcpt = _receipt_hash(rule_name, {source_key: wire_ref}, "$0.00")
        updated_key_fields["remaining_liability_balance"] = "$0.00"
        updated_derivations["remaining_liability_balance"] = FieldDerivation(
            raw_value=wire_ref,
            polarity="CONTRACTUAL_EXACT",
            normalized_value="$0.00",
            derivation_rule=rule_name,
            derived_from=[source_key],
            parameters={"settlement_status": "PAID_IN_FULL"},
            receipt_id=rcpt,
        )

    # 4. Narrow to requested_fields when caller explicitly scopes datapoint targets
    if requested_fields:
        allowed = {f.strip() for f in requested_fields if f and f.strip()}
        if allowed:
            updated_key_fields = {
                k: v for k, v in updated_key_fields.items() if k in allowed
            }
            updated_derivations = {
                k: v for k, v in updated_derivations.items() if k in allowed
            }

    # 5. Apply Stage-B derivations across 1 -> N decomposed ExtractedRecord items
    updated_records = []
    records_changed = False
    for rec in fields.records:
        rec_fields = ExtractionFields(
            doctype=fields.doctype,
            issuer=fields.issuer,
            key_fields=dict(rec.key_fields),
            derivations=dict(rec.derivations),
        )
        derived_rec, rec_rules = apply_field_derivations(
            rec_fields,
            active_hints=active_hints,
            requested_fields=requested_fields,
        )
        for rid in rec_rules:
            if rid not in applied_rule_ids:
                applied_rule_ids.append(rid)
        if (
            derived_rec.key_fields != rec.key_fields
            or derived_rec.derivations != rec.derivations
        ):
            records_changed = True
            updated_records.append(
                rec.model_copy(
                    update={
                        "key_fields": derived_rec.key_fields,
                        "derivations": derived_rec.derivations,
                    }
                )
            )
        else:
            updated_records.append(rec)

    if (
        updated_key_fields == fields.key_fields
        and updated_derivations == fields.derivations
        and not records_changed
    ):
        return fields, tuple(applied_rule_ids)

    update_payload: dict[str, Any] = {
        "key_fields": updated_key_fields,
        "derivations": updated_derivations,
    }
    if records_changed:
        update_payload["records"] = updated_records

    return (
        fields.model_copy(update=update_payload),
        tuple(applied_rule_ids),
    )


def apply_result_derivations(
    result: ExtractionResult,
    *,
    active_hints: Sequence[Any] = (),
    requested_fields: Sequence[str] | None = None,
    ontology_revision_id: str | None = None,
) -> ExtractionResult:
    """Apply Stage-B derivations to ``ExtractionResult`` and stamp lineage when active."""
    new_fields, applied_rules = apply_field_derivations(
        result.fields,
        active_hints=active_hints,
        requested_fields=requested_fields,
    )
    updates: dict[str, Any] = {}
    if new_fields is not result.fields:
        updates["fields"] = new_fields
    if applied_rules:
        merged_rules = list(dict.fromkeys([*result.applied_rule_ids, *applied_rules]))
        updates["applied_rule_ids"] = merged_rules
    if ontology_revision_id is not None:
        updates["ontology_revision_id"] = ontology_revision_id
    if not updates:
        return result
    return result.model_copy(update=updates)
