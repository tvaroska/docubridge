"""Live multimodal PDF extraction for consolidation-kind scenarios.

Runs real `gemini-3.8-flash` extraction (`thinking_budget=0`) per uploaded PDF, counts
PII tokens for the edge-redaction telemetry, WORM-locks each upload through the
normal ingestion path, drives the staged beats from what the documents contain, and
asks the reasoning model for the underwriter rationale — with a deterministic
`mode="cached"` fallback so the stage never stalls offline. All document roles,
prompts, fallback payloads and checklist bindings come from the manifest's
`stage.live_extraction` block.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from bridge.agent_gym.models import _gcp_project_id
from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.showcase.context import OperationContext
from bridge.showcase.operations.consolidation import recompute_counts
from bridge.telemetry import (
    resume_outcome,
    start_outcome,
    trace_step,
)
from bridge.user_interaction.surfaces import ingest_accepted_submission
from contract.enums import Disposition, Gate
from contract.extraction import SourceAnchor
from contract.wire import LedgerEntry


class _GenaiModelsLike(Protocol):
    def generate_content(
        self,
        *,
        model: str,
        contents: object,
        config: object = None,
    ) -> object: ...


class GenaiClientLike(Protocol):
    """Minimal structural contract for a Gemini GenAI client (live or test fake)."""

    @property
    def models(self) -> _GenaiModelsLike: ...


@dataclass(frozen=True)
class _ExtractOptions:
    use_live: bool
    skill_active: bool
    genai_client: GenaiClientLike | None = None


@dataclass(frozen=True)
class _ReasoningSpanSpec:
    span_name: str
    operation_name: str
    traffic_type: str


_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")

_LIVE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "doc_role": {
            "type": "STRING",
            "enum": [
                "audited_financials",
                "appraisal",
                "payoff_letter",
                "bank_statement",
                "other",
            ],
        },
        "borrower_name": {"type": "STRING"},
        "issuer": {"type": "STRING"},
        "loan_amount": {"type": "STRING"},
        "appraised_value": {"type": "STRING"},
        "annual_noi": {"type": "STRING"},
        "citation_page": {"type": "INTEGER"},
        "citation_snippet": {"type": "STRING"},
        "recommend_payoff_waiver": {"type": "BOOLEAN"},
        "liabilities": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "creditor": {"type": "STRING"},
                    "account_ref": {"type": "STRING"},
                    "balance": {"type": "STRING"},
                    "is_internal_on_us": {"type": "BOOLEAN"},
                },
                "required": ["creditor", "account_ref", "balance", "is_internal_on_us"],
            },
        },
        "payoff_wires": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "wire_date": {"type": "STRING"},
                    "beneficiary": {"type": "STRING"},
                    "loan_account": {"type": "STRING"},
                    "amount": {"type": "STRING"},
                    "memo": {"type": "STRING"},
                    "is_paid_in_full": {"type": "BOOLEAN"},
                    "page_number": {"type": "INTEGER"},
                },
                "required": [
                    "wire_date",
                    "beneficiary",
                    "loan_account",
                    "amount",
                    "memo",
                    "is_paid_in_full",
                    "page_number",
                ],
            },
        },
    },
    "required": [
        "doc_role",
        "borrower_name",
        "issuer",
        "citation_page",
        "citation_snippet",
        "recommend_payoff_waiver",
        "liabilities",
        "payoff_wires",
    ],
}


def build_initial_live_telemetry() -> dict[str, Any]:
    return {
        "execution_mode": "live_gcp",
        "last_action_latency_ms": 0,
        "flash_calls": 0,
        "flash_input_tokens": 0,
        "flash_output_tokens": 0,
        "flash_last_latency_ms": 0,
        "argon_calls": 0,
        "argon_input_tokens": 0,
        "argon_output_tokens": 0,
        "argon_last_latency_ms": 0,
        "pii_tokens_redacted": 0,
        "worm_objects_locked": 0,
        "argon_live_rationale": None,
        "recent_extractions": [],
    }


def _count_pii_tokens_in_pdf(pdf_bytes: bytes) -> int:
    decoded = pdf_bytes.decode("latin-1", errors="ignore")
    return len(_SSN_RE.findall(decoded)) + len(_PHONE_RE.findall(decoded))


def is_live_gemini_configured(genai_client: object = None) -> bool:
    if genai_client is not None:
        return True
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().upper() in {
        "1",
        "TRUE",
    } or bool(os.environ.get("GEMINI_API_KEY", "").strip())


def _build_genai_client(
    injected_client: GenaiClientLike | None = None,
) -> GenaiClientLike:
    if injected_client is not None:
        return injected_client
    from google import genai

    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().upper() in {"1", "TRUE"}:
        loc = os.environ.get("BRIDGE_VERTEX_LOCATION", "").strip() or "global"
        return cast(
            GenaiClientLike,
            genai.Client(vertexai=True, project=_gcp_project_id(), location=loc),
        )
    return cast(GenaiClientLike, genai.Client())


def _fallback_structured_extraction(
    config: dict[str, Any], filename: str, *, skill_active: bool
) -> dict[str, Any]:
    """Pick the manifest fallback whose markers match the filename; `${skill_active}` resolves."""
    low = filename.lower()
    chosen: dict[str, Any] | None = None
    for candidate in config.get("fallbacks", []):
        if any(marker.lower() in low for marker in candidate.get("match_any", [])):
            chosen = dict(candidate["payload"])
            break
    if chosen is None:
        chosen = dict(config.get("default_payload", {}))
    rendered: dict[str, Any] = {}
    for key, value in chosen.items():
        if value == "${skill_active}":
            rendered[key] = skill_active
        elif isinstance(value, str):
            rendered[key] = value.replace("${filename}", filename)
        else:
            rendered[key] = value
    return rendered


def _extract_single_pdf(
    config: dict[str, Any],
    filename: str,
    pdf_bytes: bytes,
    *,
    options: _ExtractOptions,
) -> dict[str, Any]:
    t0 = time.monotonic()
    used_live = False
    in_tok, out_tok, thoughts_tok = 0, 0, 0
    parsed: dict[str, Any] | None = None
    prompts = config.get("prompts", {})

    if options.use_live:
        try:
            from google.genai import types

            client = _build_genai_client(options.genai_client)
            clause = prompts.get(
                "skill_clause_active"
                if options.skill_active
                else "skill_clause_inactive",
                "",
            )
            resp = client.models.generate_content(
                model=WORKHORSE_MODEL,
                contents=[
                    prompts.get("extract", "") + clause,
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_LIVE_EXTRACTION_SCHEMA,
                    temperature=0.0,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            raw_text = str(getattr(resp, "text", "") or "").strip()
            if raw_text:
                parsed = json.loads(raw_text)
                usage = getattr(resp, "usage_metadata", None)
                in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
                out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
                thoughts_tok = int(getattr(usage, "thoughts_token_count", 0) or 0)
                used_live = True
        except Exception as exc:  # noqa: BLE001 - intentional fallback to cached extraction if live Vertex/Gemini fails
            logging.getLogger(__name__).debug("live extraction fallback: %s", exc)
            parsed = None

    if parsed is None:
        parsed = _fallback_structured_extraction(
            config, filename, skill_active=options.skill_active
        )

    elapsed_ms = max(12, int((time.monotonic() - t0) * 1000))
    return {
        "filename": filename,
        "pdf_bytes": pdf_bytes,
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "pii_redacted": _count_pii_tokens_in_pdf(pdf_bytes),
        "parsed": parsed,
        "used_live": used_live,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "thinking_tokens": thoughts_tok,
        "latency_ms": elapsed_ms if used_live else 95,
    }


def _invoke_reasoning_summary(
    prompt: str,
    *,
    use_live: bool,
    fallback_summary: str,
    genai_client: GenaiClientLike | None = None,
) -> tuple[str, int, int, int, int, bool]:
    if not use_live:
        return fallback_summary, 0, 0, 0, 85, False
    t0 = time.monotonic()
    try:
        from google.genai import types

        client = _build_genai_client(genai_client)
        resp = client.models.generate_content(
            model=REASONING_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
            ),
        )
        text = str(getattr(resp, "text", "") or "").strip()
        usage = getattr(resp, "usage_metadata", None)
        in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
        out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        thoughts_tok = int(getattr(usage, "thoughts_token_count", 0) or 0)
        elapsed_ms = max(25, int((time.monotonic() - t0) * 1000))
        if text:
            return text, in_tok, out_tok, thoughts_tok, elapsed_ms, True
    except Exception as exc:  # noqa: BLE001 - intentional fallback to cached summary if live Vertex/Gemini fails
        logging.getLogger(__name__).debug("live reasoning summary fallback: %s", exc)
    return fallback_summary, 0, 0, 0, 85, False


def _is_day2(config: dict[str, Any], filename: str, parsed: dict[str, Any]) -> bool:
    markers = config.get("day2_markers", {})
    low = filename.lower()
    borrower = str(parsed.get("borrower_name", "")).lower()
    wires = parsed.get("payoff_wires") or []
    return (
        any(m.lower() in low for m in markers.get("filename", []))
        or any(m.lower() in borrower for m in markers.get("borrower", []))
        or any(
            acct in str(w.get("loan_account", ""))
            for w in wires
            for acct in markers.get("loan_accounts", [])
        )
    )


def _build_live_ledger_entry(
    config: dict[str, Any],
    doc_id: str,
    resolved_doctype: str,
    res: dict[str, Any],
    anchor: SourceAnchor,
) -> LedgerEntry:
    parsed = res["parsed"]
    wires = parsed.get("payoff_wires") or []
    key_fields: dict[str, str] = {"currency": "USD"}
    for src, dst in (
        ("loan_amount", "loan_amount"),
        ("appraised_value", "appraised_value"),
        ("annual_noi", "net_operating_income"),
    ):
        if parsed.get(src) and parsed[src] != "N/A":
            key_fields[dst] = str(parsed[src])
    if wires:
        key_fields["closing_fee"] = str(
            wires[0].get("amount") or config.get("default_wire_amount", "")
        )
    return LedgerEntry(
        doc_id=doc_id,
        doctype=resolved_doctype,
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        overall_confidence=0.99,
        message=f"Extracted {res['filename']} via {WORKHORSE_MODEL}",
        issuer=str(parsed.get("issuer") or "Counterparty"),
        key_fields=key_fields,
        source_anchors=dict.fromkeys(key_fields, anchor),
    )


def _record_live_extraction_telemetry(
    telem: dict[str, Any],
    res: dict[str, Any],
    *,
    role: str,
    cite_page: int,
) -> None:
    parsed = res["parsed"]
    wires = parsed.get("payoff_wires") or []
    telem["flash_calls"] += 1
    telem["flash_input_tokens"] += res["input_tokens"]
    telem["flash_output_tokens"] += res["output_tokens"]
    telem["flash_last_latency_ms"] = res["latency_ms"]
    telem["pii_tokens_redacted"] += res["pii_redacted"]
    telem["worm_objects_locked"] += 1
    telem["recent_extractions"].insert(
        0,
        {
            "filename": res["filename"],
            "doc_role": role,
            "model": WORKHORSE_MODEL,
            "mode": "live_gcp" if res["used_live"] else "instant_cached",
            "latency_ms": res["latency_ms"],
            "input_tokens": res["input_tokens"],
            "output_tokens": res["output_tokens"],
            "pii_redacted": res["pii_redacted"],
            "sha256": res["sha256"],
            "vault_uri": res["vault_uri"],
            "pdf_url": res["pdf_url"],
            "highlight_page": cite_page,
            "liabilities_count": len(parsed.get("liabilities") or []),
            "payoff_wires_count": len(wires),
            "waiver_recommended": bool(parsed.get("recommend_payoff_waiver", False)),
            "summary": str(parsed.get("citation_snippet") or "")[:180],
        },
    )
    telem["recent_extractions"] = telem["recent_extractions"][:8]


def _ingest_live_document(
    ctx: OperationContext,
    config: dict[str, Any],
    res: dict[str, Any],
    *,
    telem: dict[str, Any],
) -> None:
    fname = res["filename"]
    parsed = res["parsed"]
    role = str(parsed.get("doc_role", "other"))
    wires = parsed.get("payoff_wires") or []
    cite_page = max(1, int(parsed.get("citation_page") or 1))
    page_hints = config.get("citation_page_by_role", {})
    if role == "audited_financials" and parsed.get("liabilities"):
        cite_page = int(page_hints.get(role, cite_page))
    elif role == "bank_statement" and wires:
        cite_page = int(wires[0].get("page_number") or page_hints.get(role, cite_page))

    doc_slug = re.sub(r"[^a-z0-9-]+", "-", fname.lower().removesuffix(".pdf")).strip(
        "-"
    )
    doc_id = f"doc-live-{doc_slug[:36]}"
    entity_id = ctx.entity_for("day2") if res["is_day2"] else ctx.entity_id
    anchor = SourceAnchor(
        page_number=cite_page,
        bounding_box=(0.14, 0.36, 0.86, 0.58),
        snippet=str(parsed.get("citation_snippet") or fname)[:240],
    )
    conv_ctx_map = ctx.state.setdefault("_conversation_context_ids", {})
    conv_key = "day2" if res["is_day2"] else "primary"
    stable_context_id = conv_ctx_map.setdefault(
        conv_key,
        f"{config.get('context_prefix', 'ctx-live')}-{ctx.manifest.id}-{conv_key}",
    )
    doc_ver_map = ctx.state.setdefault("_doc_versions", {})
    ver_key = f"{stable_context_id}:{doc_id}"
    doc_version = int(doc_ver_map.get(ver_key, 0)) + 1
    doc_ver_map[ver_key] = doc_version
    role_doctypes = config.get("role_doctypes", {})
    resolved_doctype = role_doctypes.get(
        role, role_doctypes.get("default", "credit-agreement")
    )
    with trace_step(
        "docubridge.collab.live_pdf_extract",
        {
            "docubridge.zone": "2_mediation_quarantine",
            "docubridge.entity_id": entity_id,
            "entity_id": entity_id,
            "docubridge.context_id": stable_context_id,
            "docubridge.doc_id": doc_id,
            "docubridge.filename": fname,
            "skill_id": resolved_doctype,
            "source_mode": "PDF_PATH_B_FLASH",
            "docubridge.source_mode": "PDF_PATH_B_FLASH",
            "docubridge.agent_spiffe_id": ctx.manifest.agents.get("bridge", ""),
            "docubridge.execution_mode": "live_gcp"
            if res["used_live"]
            else "instant_cached",
            "docubridge.pii_redacted_count": res["pii_redacted"],
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": "extract_document",
            "gen_ai.agent.name": "document_bridge_sense_a",
            "gen_ai.request.model": WORKHORSE_MODEL,
            "gen_ai.response.model": WORKHORSE_MODEL,
            "gen_ai.usage.input_tokens": res["input_tokens"],
            "gen_ai.usage.output_tokens": res["output_tokens"],
            "gen_ai.usage.reasoning.output_tokens": res.get("thinking_tokens", 0),
            "docubridge.thinking_tokens": res.get("thinking_tokens", 0),
            "docubridge.finops.traffic_type": "production_inference",
            "finops.traffic_type": "production_inference",
        },
    ):
        entry = _build_live_ledger_entry(config, doc_id, resolved_doctype, res, anchor)
        from bridge.vault import VaultImmutabilityError

        while True:
            try:
                ingest_res = ingest_accepted_submission(
                    entry,
                    entity_id=entity_id,
                    context_id=stable_context_id,
                    version=doc_version,
                    raw_document_bytes=res["pdf_bytes"],
                    filename=fname,
                    operational_store=ctx.store,
                    knowledge_catalog=ctx.catalog,
                    agent_gym=ctx.gym,
                    input_tokens=res["input_tokens"] if res["used_live"] else None,
                    output_tokens=res["output_tokens"] if res["used_live"] else None,
                    thinking_tokens=res.get("thinking_tokens")
                    if res["used_live"]
                    else None,
                    source_mode="PDF_PATH_B_FLASH",
                )
                break
            except VaultImmutabilityError:
                if doc_version >= 100:
                    raise
                doc_version += 1
                doc_ver_map[ver_key] = doc_version

    res["vault_uri"] = ingest_res.vault_record.vault_uri
    res["cite_page"] = cite_page
    res["pdf_url"] = f"{config['pdf_url_base']}/{fname}"
    _record_live_extraction_telemetry(telem, res, role=role, cite_page=cite_page)


def _bind_upload(
    item: dict[str, Any], res: dict[str, Any], extra: dict[str, Any]
) -> None:
    item.update(extra)
    item["pdf_url"] = res["pdf_url"]
    item["vault_uri"] = res["vault_uri"]
    item["sha256"] = res["sha256"]


def _reasoning_step(
    ctx: OperationContext,
    telem: dict[str, Any],
    prompt_block: dict[str, Any],
    span_spec: _ReasoningSpanSpec,
    options: _ExtractOptions,
) -> bool:
    rationale, a_in, a_out, a_thoughts, a_ms, a_live = _invoke_reasoning_summary(
        ctx.render(prompt_block.get("prompt", "")),
        use_live=options.use_live,
        fallback_summary=ctx.render(prompt_block.get("fallback", "")),
        genai_client=options.genai_client,
    )
    with trace_step(
        span_spec.span_name,
        {
            "docubridge.zone": "3_consolidation_operational",
            "docubridge.entity_id": ctx.entity_id,
            "entity_id": ctx.entity_id,
            "source_mode": "PDF_PATH_B_ARGON",
            "docubridge.source_mode": "PDF_PATH_B_ARGON",
            "docubridge.agent_spiffe_id": ctx.manifest.agents.get("underwriter", ""),
            "gen_ai.system": "gemini_platform",
            "gen_ai.operation.name": span_spec.operation_name,
            "gen_ai.agent.name": "credit_underwriting_sense_b",
            "gen_ai.request.model": REASONING_MODEL,
            "gen_ai.response.model": REASONING_MODEL,
            "gen_ai.usage.input_tokens": a_in,
            "gen_ai.usage.output_tokens": a_out,
            "gen_ai.usage.reasoning.output_tokens": a_thoughts,
            "docubridge.thinking_tokens": a_thoughts,
            "docubridge.finops.traffic_type": span_spec.traffic_type,
            "finops.traffic_type": span_spec.traffic_type,
        },
    ):
        telem["argon_calls"] += 1
        telem["argon_input_tokens"] += a_in
        telem["argon_output_tokens"] += a_out
        telem["argon_last_latency_ms"] = a_ms
        telem["argon_live_rationale"] = rationale
        ctx.state["agent2_policy_status"]["detail"] = rationale
    return a_live


def _advance_day2_uploads(
    ctx: OperationContext,
    config: dict[str, Any],
    results: list[dict[str, Any]],
    run_beat: Callable[[int], None],
    telem: dict[str, Any],
) -> None:
    beats = config.get("beats", {})
    state = ctx.state
    for pre_beat in (
        int(beats.get("expand", 1)),
        int(beats.get("reconcile", 2)),
        int(beats.get("waiver", 3)),
        int(beats.get("distill", 4)),
    ):
        if state.get("beat", 0) < pre_beat:
            run_beat(pre_beat)
    day2_res = next((r for r in results if r["is_day2"]), results[-1])
    payoff = state.get("agent_gym_payoff")
    if payoff is not None:
        payoff["day2_latency_ms"] = day2_res["latency_ms"]
        payoff["day2_pdf_url"] = day2_res["pdf_url"]
        payoff["day2_sha256"] = day2_res["sha256"]
        payoff["day2_vault_uri"] = day2_res["vault_uri"]
        payoff["day2_flash_tokens"] = (
            day2_res["input_tokens"] + day2_res["output_tokens"]
        )
    telem["argon_live_rationale"] = ctx.render(config.get("day2_rationale", ""))


def _reconcile_bank_statement_uploads(
    ctx: OperationContext,
    config: dict[str, Any],
    results: list[dict[str, Any]],
    telem: dict[str, Any],
    options: _ExtractOptions,
) -> None:
    state = ctx.state
    bank_res = next(
        (r for r in results if r["parsed"].get("doc_role") == "bank_statement"),
        results[-1],
    )
    payoff_res = next(
        (r for r in results if r["parsed"].get("doc_role") == "payoff_letter"),
        None,
    )
    reconcile = config.get("reconcile_bindings", {})
    for item in state.get("borrower_checklist", []):
        if item["id"] in reconcile.get("bank_statement_items", []):
            _bind_upload(item, bank_res, {})
        elif item["id"] in reconcile.get("payoff_letter_items", []) and payoff_res:
            _bind_upload(item, payoff_res, {})
    if state.get("cross_doc_discovery"):
        state["cross_doc_discovery"]["pdf_url"] = bank_res["pdf_url"]
        state["cross_doc_discovery"]["discovered_in_filename"] = bank_res["filename"]
    if state.get("underwriter_overwatch"):
        state["underwriter_overwatch"]["pdf_url"] = bank_res["pdf_url"]
        state["underwriter_overwatch"]["doc_title"] = bank_res["filename"]
    _reasoning_step(
        ctx,
        telem,
        config.get("reconcile_summary", {}),
        _ReasoningSpanSpec(
            span_name="docubridge.collab.live_argon_reconcile",
            operation_name="cross_document_reconcile",
            traffic_type="conflict_escalation",
        ),
        options,
    )


def _advance_day1_uploads(
    ctx: OperationContext,
    config: dict[str, Any],
    results: list[dict[str, Any]],
    run_beat: Callable[[int], None],
    options: _ExtractOptions,
) -> None:
    state = ctx.state
    telem = state["live_telemetry"]
    beats = config.get("beats", {})
    beat_expand = int(beats.get("expand", 1))
    beat_reconcile = int(beats.get("reconcile", 2))
    roles_seen = {str(r["parsed"].get("doc_role", "other")) for r in results}
    expand_roles = set(config.get("expand_roles", ["audited_financials", "appraisal"]))
    if roles_seen & expand_roles and state.get("beat", 0) < beat_expand:
        run_beat(beat_expand)
        if _reasoning_step(
            ctx,
            telem,
            config.get("policy_summary", {}),
            _ReasoningSpanSpec(
                span_name="docubridge.collab.live_argon_policy",
                operation_name="evaluate_underwriting_policy",
                traffic_type="production_inference",
            ),
            options,
        ):
            options = _ExtractOptions(
                use_live=True,
                skill_active=options.skill_active,
                genai_client=options.genai_client,
            )

    bindings = config.get("checklist_bindings", {})
    for res in results:
        binding = bindings.get(str(res["parsed"].get("doc_role")))
        if not binding:
            continue
        for item in state.get("borrower_checklist", []):
            if item["id"] in binding.get("items", []):
                _bind_upload(item, res, binding.get("set", {}))

    if "bank_statement" in roles_seen:
        if state.get("beat", 0) < beat_expand:
            run_beat(beat_expand)
        run_beat(beat_reconcile)
        _reconcile_bank_statement_uploads(ctx, config, results, telem, options)


def process_uploaded_pdfs(
    ctx: OperationContext,
    *,
    resolved_files: list[tuple[str, bytes]],
    mode: Literal["live", "cached"],
    run_beat: Callable[[int], None],
    genai_client: GenaiClientLike | None = None,
) -> dict[str, Any]:
    """Extract, WORM-lock and reconcile uploads, advancing the staged beats they unlock."""
    stage = ctx.manifest.stage
    config = dict(stage.live_extraction if stage else {})
    config.setdefault("pdf_url_base", stage.pdf_url_base if stage else "")
    state = ctx.state
    owner = os.environ.get(
        "BRIDGE_APPHUB_APPLICATION_ID", "meridian-loan-consolidation"
    )
    if state.get("outcome_id"):
        resume_outcome(str(state["outcome_id"]), owner=owner)
    else:
        state["outcome_id"] = start_outcome(owner=owner)
    overall_t0 = time.monotonic()
    options = _ExtractOptions(
        use_live=mode == "live" and is_live_gemini_configured(genai_client),
        skill_active=state.get("agent_gym_payoff") is not None
        or bool(ctx.gym.list_skill_revisions(config["gym_skill_doctype"])),
        genai_client=genai_client,
    )
    telem = state.setdefault("live_telemetry", build_initial_live_telemetry())

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_extract_single_pdf, config, fname, raw, options=options)
            for fname, raw in resolved_files
        ]
        results = [f.result() for f in futures]

    for res in results:
        res["is_day2"] = _is_day2(config, res["filename"], res["parsed"])
        _ingest_live_document(ctx, config, res, telem=telem)

    if any(r["is_day2"] for r in results):
        _advance_day2_uploads(ctx, config, results, run_beat, telem)
    else:
        _advance_day1_uploads(ctx, config, results, run_beat, options)

    recompute_counts(state)
    state["agent1_bridge_status"]["documents_processed"] = telem["worm_objects_locked"]
    telem["execution_mode"] = (
        "live_gcp" if any(r["used_live"] for r in results) else "instant_cached"
    )
    telem["last_action_latency_ms"] = max(
        15, int((time.monotonic() - overall_t0) * 1000)
    )
    return state


__all__ = [
    "GenaiClientLike",
    "build_initial_live_telemetry",
    "is_live_gemini_configured",
    "process_uploaded_pdfs",
]
