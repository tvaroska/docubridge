"""Consolidation-stage beat operations (vault lock, checklist patches, waiver, Agent Gym).

Every string these operations write into the stage state comes from the manifest
params; the Python here only knows *how* to ingest, patch, approve and distill.
"""

from __future__ import annotations

import contextlib
import copy
import uuid
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from bridge.a2ui_inbox import (
    A2uiClientActionRequest,
    ActionCitation,
    ActionProgressStep,
)
from bridge.agent_gym import TribalRuleCategory
from bridge.showcase.context import OperationContext
from bridge.showcase.operations import operation, view_builder
from bridge.telemetry import trace_step
from bridge.user_interaction.surfaces import ingest_accepted_submission
from contract.extraction import SourceAnchor
from contract.wire import Disposition, Gate, LedgerEntry


def recompute_counts(state: dict[str, Any]) -> None:
    items = state.get("borrower_checklist", [])
    state["counts"] = {
        "total": len(items),
        "satisfied": sum(1 for i in items if i.get("status") == "satisfied"),
        "waived": sum(1 for i in items if i.get("status") == "waived"),
        "required": sum(1 for i in items if i.get("status") == "required"),
    }


@contextlib.contextmanager
def _optional_trace(
    ctx: OperationContext, trace: dict[str, Any] | None
) -> Iterator[None]:
    if not trace:
        yield
        return
    rendered = ctx.render(trace)
    with trace_step(rendered["name"], rendered.get("attributes", {})):
        yield


def _anchor(spec: dict[str, Any]) -> SourceAnchor:
    return SourceAnchor(
        page_number=int(spec["page_number"]),
        bounding_box=tuple(spec["bounding_box"]),
        snippet=spec["snippet"],
        artifact_uri=spec.get("artifact_uri"),
    )


def _ledger_entry(
    doc: dict[str, Any], named_anchors: dict[str, SourceAnchor]
) -> LedgerEntry:
    anchors: dict[str, SourceAnchor] = {}
    for fld, ref in (doc.get("source_anchors") or {}).items():
        anchors[fld] = named_anchors[ref] if isinstance(ref, str) else _anchor(ref)
    return LedgerEntry(
        doc_id=doc["doc_id"],
        doctype=doc["doctype"],
        issuer=doc["issuer"],
        disposition=Disposition(doc.get("disposition", "accepted")),
        gate=Gate(doc.get("gate", "auto_approve")),
        overall_confidence=float(doc.get("overall_confidence", 0.99)),
        message=doc["message"],
        key_fields=dict(doc.get("key_fields") or {}),
        source_anchors=anchors,
    )


def _ingest_one(
    ctx: OperationContext,
    doc: dict[str, Any],
    *,
    named_anchors: dict[str, SourceAnchor],
    context_id: str,
) -> dict[str, Any]:
    entry = _ledger_entry(doc, named_anchors)
    asset_id = doc.get("pdf_asset")
    raw = (
        ctx.pdf_bytes(asset_id)
        if asset_id
        else doc.get("raw_document_marker", "%PDF-1.7").encode()
    )
    res = ingest_accepted_submission(
        entry,
        entity_id=ctx.entity_for(doc.get("entity")),
        context_id=context_id,
        raw_document_bytes=raw,
        filename=doc.get("filename"),
        operational_store=ctx.store,
        knowledge_catalog=ctx.catalog,
        agent_gym=ctx.gym,
    )
    info = {
        "doc_id": entry.doc_id,
        "filename": doc.get("filename"),
        "vault_uri": res.vault_record.vault_uri,
        "sha256": res.vault_record.sha256,
        "pdf_url": ctx.pdf_url(asset_id) if asset_id else None,
        "disposition": res.entry.disposition.value,
        "gate": res.entry.gate.value,
        "applied_extraction_hints": list(res.applied_extraction_hints),
    }
    ctx.docs[doc["alias"]] = info
    return info


@operation("ingest_documents")
def ingest_documents(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Vault-lock + ingest the listed documents; results become `${doc:<alias>.*}`."""
    named = {k: _anchor(v) for k, v in (params.get("anchors") or {}).items()}
    context_id = f"{params.get('context_prefix', 'ctx')}-{uuid.uuid4().hex[:8]}"
    with _optional_trace(ctx, params.get("trace")):
        return {
            doc["alias"]: _ingest_one(
                ctx, ctx.render(doc), named_anchors=named, context_id=context_id
            )
            for doc in params["documents"]
        }


def _set_path(state: dict[str, Any], dotted: str, value: Any, *, append: bool) -> None:
    keys = dotted.split(".")
    node: Any = state
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    if append:
        node.setdefault(keys[-1], []).append(value)
    else:
        node[keys[-1]] = value


@operation("patch_state")
def patch_state(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Apply `set` / `update_items` / `append` patches, then optionally recount."""
    with _optional_trace(ctx, params.get("trace")):
        for dotted, value in (params.get("set") or {}).items():
            _set_path(ctx.state, dotted, ctx.render(value), append=False)
        for list_key, patches in (params.get("update_items") or {}).items():
            for item in ctx.state.get(list_key, []):
                patch = patches.get(item.get("id"))
                if patch:
                    item.update(ctx.render(patch))
        for dotted, value in (params.get("append") or {}).items():
            _set_path(ctx.state, dotted, ctx.render(value), append=True)
        if "beat" in params:
            ctx.state["beat"] = int(params["beat"])
        if "done" in params:
            ctx.state["done"] = bool(params["done"])
        if params.get("recount_checklist"):
            recompute_counts(ctx.state)
    return {"beat": ctx.state.get("beat"), "done": ctx.state.get("done")}


@operation("inbox_waiver")
def inbox_waiver(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create + resolve the 1-click A2UI inbox action; exposes `${inbox_item_id}` etc."""
    with _optional_trace(ctx, params.get("trace")):
        citation = ActionCitation(**ctx.render(params["citation"]))
        steps = []
        for raw_step in params.get("steps", []):
            step = ctx.render(raw_step)
            if step.pop("with_citation", False):
                step["citations"] = [citation]
            steps.append(ActionProgressStep(**step))
        action = ctx.render(params["action"])
        item = ctx.inbox.create_action(
            entity_id=ctx.entity_for(params.get("entity")), steps=steps, **action
        )
        resolve = ctx.render(params["resolve"])
        result = ctx.inbox.resolve_action(
            item.item_id,
            action_request=A2uiClientActionRequest(action=resolve["action"]),
            actor_principal=resolve["actor_principal"],
            actor_role=resolve["actor_role"],
            channel=resolve.get("channel", "gemini_app"),
            agent_spiffe_id=resolve.get("agent_spiffe_id"),
        )
    ctx.vars["inbox_item_id"] = item.item_id
    ctx.vars["audit_id"] = result["audit_id"]
    ctx.vars["decision"] = result["decision"]
    return {"inbox_item_id": item.item_id, "audit_id": result["audit_id"]}


@operation("gym_distill_and_compile")
def gym_distill_and_compile(
    ctx: OperationContext, params: dict[str, Any]
) -> dict[str, Any]:
    """Distill the expert correction, pass the stress gate, compile the skill, verify Day-2."""
    distill = ctx.render(params["distill"])
    anchor = _anchor(distill.pop("source_anchor"))
    entity_alias = distill.pop("entity", None)
    category = TribalRuleCategory[distill.pop("category", "EXTRACTION_HINT")]
    signal, rule = ctx.gym.distill_from_quarantine_correction(
        entity_id=ctx.entity_for(entity_alias),
        source_anchor=anchor,
        category=category,
        **distill,
    )
    report = ctx.gym.evaluate_candidate_rule(rule.rule_id, auto_promote=True)
    compiled = ctx.gym.compile_rules_to_skill(**ctx.render(params["compile"]))

    day2 = params["day2"]
    day2_doc = ctx.render(day2["document"])
    day2_info = _ingest_one(
        ctx,
        day2_doc,
        named_anchors={},
        context_id=f"{day2.get('context_prefix', 'ctx-day2')}-{uuid.uuid4().hex[:8]}",
    )

    ctx.vars.update(
        {
            "rule_id": rule.rule_id,
            "signal_id": signal.signal_id,
            "skill_name": compiled["doctype"],
            "skill_version": compiled["version"],
            "skill_revision_id": compiled["revision_id"],
            "skill_urn": compiled["skill_urn"],
            "day2_entity_id": ctx.entity_for(day2_doc.get("entity")),
        }
    )
    payoff: dict[str, Any] = {
        "rule_id": rule.rule_id,
        "signal_id": signal.signal_id,
        "skill_name": compiled["doctype"],
        "skill_version": compiled["version"],
        "skill_revision_id": compiled["revision_id"],
        "skill_urn": compiled["skill_urn"],
        "skill_markdown": compiled.get("skill_markdown", ""),
        "stress_cases_passed": report.stress_cases_passed,
        "stress_cases_total": report.stress_cases_total,
        "tier1_deterministic_passed": report.tier1_rule0_passed,
        "tier2_autorater_score": report.tier2_autorater_score,
        "tier3_corridor_feasible": report.tier3_corridor_fa_zero,
        "day2_entity_id": ctx.vars["day2_entity_id"],
        "day2_status": day2_info["disposition"],
        "day2_gate": day2_info["gate"],
        "day2_pdf_url": day2_info["pdf_url"],
        "day2_vault_uri": day2_info["vault_uri"],
        "day2_sha256": day2_info["sha256"],
        "applied_extraction_hints": list(day2_info["applied_extraction_hints"]),
    }
    payoff.update(copy.deepcopy(ctx.render(params.get("payoff") or {})))
    ctx.state["agent_gym_payoff"] = payoff
    return {"rule_id": rule.rule_id, "skill_version": compiled["version"]}


@operation("financial_summary")
def financial_summary(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Exact Decimal LTV / DSCR / liability schedule (`Rule 0`: never computed by a model)."""
    spec = ctx.render(params)
    liabilities = spec["liabilities"]
    total_loan = sum((Decimal(row["balance"]) for row in liabilities), Decimal("0"))
    appraised = Decimal(spec["appraised_value"])
    noi = Decimal(spec["annual_noi"])
    debt_service = Decimal(spec["annual_debt_service"])
    ltv_cap = Decimal(spec["ltv_policy_cap_pct"])
    dscr_min = Decimal(spec["dscr_policy_min"])
    cents = Decimal("0.01")
    ltv_pct = ((total_loan / appraised) * Decimal("100")).quantize(cents, ROUND_HALF_UP)
    dscr = (noi / debt_service).quantize(cents, ROUND_HALF_UP)
    summary = {
        "consolidated_loan_amount": f"${total_loan:,.2f}",
        "appraised_collateral_value": f"${appraised:,.2f}",
        "ltv_pct": float(ltv_pct),
        "ltv_formatted": f"{ltv_pct}%",
        "ltv_policy_cap": f"{ltv_cap:.2f}%",
        "ltv_compliant": ltv_pct <= ltv_cap,
        "annual_cfads_noi": f"${noi:,.2f}",
        "annual_debt_service": f"${debt_service:,.2f}",
        "dscr_ratio": float(dscr),
        "dscr_formatted": f"{dscr}x",
        "dscr_policy_min": f"{dscr_min:.2f}x",
        "dscr_compliant": dscr >= dscr_min,
        "existing_liabilities_schedule": [
            {
                "creditor": row["creditor"],
                "account_ref": row["account_ref"],
                "facility_type": row["facility_type"],
                "payoff_amount": f"${Decimal(row['balance']):,.2f}",
                "handling": row["handling"],
            }
            for row in liabilities
        ],
    }
    ctx.state[spec.get("state_key", "financial_summary")] = summary
    return summary


@view_builder("consolidation")
def consolidation_view(ctx: OperationContext) -> dict[str, Any]:
    return copy.deepcopy(ctx.state)


__all__ = ["recompute_counts"]
