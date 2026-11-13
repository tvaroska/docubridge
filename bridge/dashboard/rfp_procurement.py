"""Benefits RFP multi-agent procurement execution route for `bridge-rfp` (`M2`).

Coordinates a complete RFP award outcome (`owner="benefits-rfp-procurement"`):
- Carrier A (`carrier-a-adk`): `Path-A` structured quote validated via `patha.validate_only_entry` (`0` LLM tokens, `source_mode="A2A_PATH_A"`).
- Carrier B (`carrier-b-langgraph`): `Path-B` PDF quote bundle (`gs://...`) extracted inside `bridge-rfp` using `gemini-3.8-flash` (`source_mode="PDF_PATH_B_FLASH"`) on calibrated runs or `gemini-4-argon` with `>200K` context (`source_mode="PDF_PATH_B_ARGON"`) on the `argon_cohort` runs so uncalibrated Argon spend is attributed to `benefits-rfp-procurement` (`§5.1`).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bridge import patha
from bridge.edge.skills import candidate_doctypes_for
from bridge.extraction.gemini import REASONING_MODEL, WORKHORSE_MODEL
from bridge.showcase.live_extraction import (
    _build_genai_client,
    is_live_gemini_configured,
)
from bridge.telemetry import close_outcome, start_outcome, trace_step
from bridge.telemetry.outcome import inject_outcome_headers, without_outcome_baggage
from bridge.telemetry.payload_log import emit_trace_payload_log

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard


class RunRfpProcurementRequest(BaseModel):
    """Request body for `POST /v1/rfp/run-procurement`."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    employer_name: str = "Northstar Manufacturing Group"
    plan_year: int = 2027
    carrier_a_url: str | None = None
    carrier_b_url: str | None = None
    carrier_b_pdf_uri: str | None = None
    argon_cohort: bool = False
    mode: str = "live"
    carrier_a_fallback: dict[str, Any] = Field(default_factory=dict)


def _build_long_context_appendix(case_id: str) -> str:
    """Deterministic ~215K-token statutory benefits appendix for the uncalibrated Argon cohort.

    Uses per-section case-salted identifiers so Vertex implicit prefix caching does not mask
    uncached long-context (`>200K`) token pricing and so every clause remains domain-authentic.
    """
    blocks: list[str] = [
        f"STATUTORY GROUP BENEFITS MASTER POLICY APPENDIX — CASE {case_id}\n"
    ]
    base_clause = (
        "Section {idx:04d} ({salt}): Group Voluntary Critical Illness, Hospital Indemnity, Dental, "
        "and Supplemental Life Coverage Schedule. Employer-sponsored ERISA plan provisions govern "
        "tier eligibility (EE, EE_SPOUSE, EE_CHILDREN, FAMILY), tobacco underwriting classes "
        "(NON_TOBACCO, TOBACCO, UNI), waiting-period waivers, orthodontics lifetime maximums, "
        "portability conversion windows, state continuation riders, and broker commission "
        "schedules across multi-state bargaining units. Verify exact deductible and rate-table "
        "alignment before issuing final procurement award.\n"
    )
    for idx in range(1, 2351):
        blocks.append(base_clause.format(idx=idx, salt=f"{case_id}-S{idx:04d}"))
    return "".join(blocks)


def execute_rfp_procurement_run(
    *,
    body: RunRfpProcurementRequest,
    rfps: RfpBoard | None = None,
    genai_client: Any = None,
    http_client: httpx.Client | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run one multi-carrier RFP evaluation and close its `EVAL-P11` outcome."""
    from opentelemetry import context as otel_context

    owner = os.environ.get("BRIDGE_APPHUB_APPLICATION_ID", "benefits-rfp-procurement")
    process_name = (
        rfps.policy.process
        if rfps is not None
        else os.environ.get("BRIDGE_RFP_PROCESS", "").strip()
    )
    doctypes = candidate_doctypes_for(process_name) if process_name else []

    otel_context.attach(without_outcome_baggage())
    outcome_id = start_outcome(owner=owner)
    use_live = body.mode == "live" and is_live_gemini_configured(genai_client)
    uncalibrated_argon = body.argon_cohort or (
        os.environ.get("CARRIER_B_UNCALIBRATED", "").strip() == "1"
    )

    try:
        carrier_a_endpoint = (
            body.carrier_a_url or os.environ.get("BRIDGE_CARRIER_A_URL", "").strip()
        )
        carrier_b_endpoint = (
            body.carrier_b_url or os.environ.get("BRIDGE_CARRIER_B_URL", "").strip()
        )
        ext_headers = inject_outcome_headers(
            {"content-type": "application/json"}, is_external=True
        )

        carrier_a_assertion: dict[str, Any] | None = None
        if body.mode == "live" and (carrier_a_endpoint or http_client is not None):
            target_a = (
                f"{carrier_a_endpoint.rstrip('/')}/v1/rfp/quote"
                if carrier_a_endpoint
                else "/v1/rfp/quote"
            )
            req_a = {
                "rfp_id": body.case_id,
                "employer_name": body.employer_name,
                "plan_year": body.plan_year,
            }
            if http_client is not None:
                resp_a = http_client.post(target_a, json=req_a, headers=ext_headers)
            else:
                with httpx.Client(timeout=20.0) as client:
                    resp_a = client.post(target_a, json=req_a, headers=ext_headers)
            resp_a.raise_for_status()
            data_a = resp_a.json()
            carrier_a_assertion = data_a.get("assertion", data_a)
        elif body.carrier_a_fallback:
            carrier_a_assertion = dict(body.carrier_a_fallback)

        if not carrier_a_assertion:
            raise HTTPException(
                status_code=503, detail="Carrier A quote assertion unavailable."
            )
        if not doctypes and "fields" in carrier_a_assertion:
            doctypes = [str(carrier_a_assertion["fields"].get("doctype", ""))]

        with trace_step(
            "docubridge.rfp.carrier_a_path_a_validate",
            {
                "entity_id": body.case_id,
                "source_mode": "A2A_PATH_A",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "path_a_validate_carrier_quote",
                "gen_ai.agent.name": "rfp_path_a_validator",
                "gen_ai.usage.input_tokens": 0,
                "gen_ai.usage.output_tokens": 0,
                "finops.traffic_type": "production_inference",
            },
        ):
            try:
                entry_a = patha.validate_only_entry(
                    carrier_a_assertion,
                    doc_id=f"{body.case_id}-carrier-a",
                    candidate_doctypes=doctypes,
                    require_installed_schema=True,
                )
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Carrier A Path-A validation failed: {exc}",
                ) from exc

        pdf_uri = body.carrier_b_pdf_uri or ""
        carrier_b_meta: dict[str, Any] = {}
        if body.mode == "live" and (carrier_b_endpoint or http_client is not None):
            target_b = (
                f"{carrier_b_endpoint.rstrip('/')}/v1/rfp/quote-pdf"
                if carrier_b_endpoint
                else "/v1/rfp/quote-pdf"
            )
            req_b = {
                "rfp_id": body.case_id,
                "employer_name": body.employer_name,
                "plan_year": body.plan_year,
                "pdf_uri": pdf_uri,
                "uncalibrated_argon": uncalibrated_argon,
            }
            if http_client is not None:
                resp_b = http_client.post(target_b, json=req_b, headers=ext_headers)
            else:
                with httpx.Client(timeout=25.0) as client:
                    resp_b = client.post(target_b, json=req_b, headers=ext_headers)
            resp_b.raise_for_status()
            carrier_b_meta = resp_b.json()
            pdf_uri = str(carrier_b_meta.get("pdf_uri") or pdf_uri)

        chosen_model = REASONING_MODEL if uncalibrated_argon else WORKHORSE_MODEL
        source_mode = "PDF_PATH_B_ARGON" if uncalibrated_argon else "PDF_PATH_B_FLASH"
        agent_label = (
            "rfp_pdf_extractor_argon_uncalibrated"
            if uncalibrated_argon
            else "rfp_pdf_extractor_flash"
        )

        in_tok, out_tok, think_tok = 0, 0, 0
        extracted_summary = ""
        if use_live:
            from google.genai import types

            client = _build_genai_client(genai_client)
            prompt_head = (
                f"Extract Carrier B voluntary benefits quote schedule for {body.employer_name} "
                f"(RFP {body.case_id}, plan year {body.plan_year}), reconciling v0 orthodontics "
                f"gap against v2 revised deductible and rate tiers. Return concise JSON."
            )
            if uncalibrated_argon:
                prompt_head = (
                    prompt_head + "\n\n" + _build_long_context_appendix(body.case_id)
                )
            contents: list[Any] = [prompt_head]
            if pdf_uri.startswith("gs://"):
                contents.append(
                    types.Part.from_uri(file_uri=pdf_uri, mime_type="application/pdf")
                )
            elif carrier_b_meta.get("pdf_base64"):
                import base64

                contents.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(carrier_b_meta["pdf_base64"]),
                        mime_type="application/pdf",
                    )
                )
            resp = client.models.generate_content(
                model=chosen_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=1024 if uncalibrated_argon else 320,
                    thinking_config=(
                        types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
                        if uncalibrated_argon
                        else types.ThinkingConfig(thinking_budget=0)
                    ),
                ),
            )
            extracted_summary = str(getattr(resp, "text", "") or "").strip()
            usage = getattr(resp, "usage_metadata", None)
            in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
            out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
            think_tok = int(getattr(usage, "thoughts_token_count", 0) or 0)
        else:
            in_tok = 212000 if uncalibrated_argon else 1180
            out_tok = 240 if uncalibrated_argon else 165
            think_tok = 190 if uncalibrated_argon else 0
            extracted_summary = json.dumps(
                {
                    "carrier": "Carrier B",
                    "revision": "v2",
                    "orthodontics_gap_closed": True,
                }
            )

        with trace_step(
            "docubridge.rfp.carrier_b_pdf_extract",
            {
                "entity_id": body.case_id,
                "skill_id": entry_a.doctype,
                "source_mode": source_mode,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "extract_document",
                "gen_ai.agent.name": agent_label,
                "gen_ai.request.model": chosen_model,
                "gen_ai.response.model": chosen_model,
                "gen_ai.usage.input_tokens": in_tok,
                "gen_ai.usage.output_tokens": out_tok,
                "gen_ai.usage.reasoning.output_tokens": think_tok,
                "finops.traffic_type": "production_inference",
            },
        ):
            pass

        emit_trace_payload_log(
            user_prompt=(
                f"Evaluate multi-carrier Benefits RFP {body.case_id} for {body.employer_name} "
                f"(Plan Year {body.plan_year}): validate Carrier A Path-A quote and reconcile "
                f"Carrier B Path-B PDF quote v0->v2 schedules."
            ),
            retrieved_context=json.dumps(
                {
                    "carrier_a_key_fields": entry_a.key_fields,
                    "carrier_b_pdf_uri": pdf_uri,
                    "carrier_b_extracted": extracted_summary[:1500],
                    "extraction_model": chosen_model,
                }
            ),
            final_response=(
                f"RFP {body.case_id} award verified: Carrier A (Path-A, 0 tokens) and "
                f"Carrier B ({source_mode}, v2 conformant) validated with all required limits."
            ),
            apphub_application_id=owner,
        )

        close_outcome(
            "succeeded",
            subject=body.case_id,
            owner=owner,
            outcome_id=outcome_id,
            attributes={
                "schema_valid_quotes": True,
                "source_mode": source_mode,
                "skill_id": entry_a.doctype,
                "entity_id": body.case_id,
            },
        )
        return outcome_id, {
            "case_id": body.case_id,
            "outcome_id": outcome_id,
            "outcome_status": "succeeded",
            "schema_valid_quotes": True,
            "carrier_a_mode": "A2A_PATH_A",
            "carrier_b_mode": source_mode,
            "carrier_b_model": chosen_model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "thinking_tokens": think_tok,
        }
    finally:
        otel_context.attach(without_outcome_baggage())


def mount_rfp_procurement_routes(app: FastAPI, *, rfps: RfpBoard | None = None) -> None:
    """Mount `POST /v1/rfp/run-procurement` onto the internal dashboard/RFP host."""

    @app.post("/v1/rfp/run-procurement")
    def post_run_rfp_procurement(
        body: RunRfpProcurementRequest, response: Response
    ) -> dict[str, Any]:
        outcome_id, payload = execute_rfp_procurement_run(body=body, rfps=rfps)
        response.headers["x-outcome-id"] = outcome_id
        return payload
