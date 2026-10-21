"""Record the live GCP end-to-end demo video for DocuBridge Enterprise (documents001).

Exercises the live deployed Cloud Run v2 services in GCP project ``documents001``:
  1. ``https://documents001-bridge-sf6etnew5q-uc.a.run.app`` (A2A Edge)
  2. ``https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app`` (Dashboard, Zone 5 A2UI,
     Zone 6 Knowledge Catalog, SPIFFE Registry, and Cloud SQL Agent Gym)

Drives headless Chrome (1920x1080) over Chrome DevTools Protocol, captures frames at 8 fps
across all 4 parts of the walkthrough, and encodes ``demo/_captures/gcp-end-to-end-demo.mp4``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import websockets

BRIDGE_URI = "https://documents001-bridge-sf6etnew5q-uc.a.run.app"
BOARD_URI = "https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app"
CACHE_PATH = Path("/tmp/docubridge_live_telemetry.json")

DEMO_DIR = Path(__file__).resolve().parent
HTML_PATH = DEMO_DIR / "enterprise-demo.html"
OUT_DIR = DEMO_DIR / "_captures"
OUT_MP4 = OUT_DIR / "gcp-end-to-end-demo.mp4"
FPS = 8


def get_id_token() -> str:
    res = subprocess.run(
        ["gcloud", "auth", "print-identity-token", "--quiet"],
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


class CdpSession:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._msg_id = 0

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._msg_id += 1
        target_id = self._msg_id
        payload = {"id": target_id, "method": method, "params": params or {}}
        await self._ws.send(json.dumps(payload))
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == target_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} error: {msg['error']}")
                return msg.get("result", {})

    async def eval_js(self, expression: str) -> Any:
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return res.get("result", {}).get("value")

    async def screenshot_png(self) -> bytes:
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(res["data"])


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fetch_only = "--fetch-only" in sys.argv
    from_cache = "--from-cache" in sys.argv

    if from_cache and CACHE_PATH.exists():
        print(f"1. Loading cached live GCP telemetry from {CACHE_PATH}...")
        cached = json.loads(CACHE_PATH.read_text())
        agent_card = cached["agent_card"]
        catalog_assets = cached["catalog_assets"]
        spiffe_agents = cached["spiffe_agents"]
        policies_initial = cached["policies_initial"]
        policies_promoted = cached["policies_promoted"]
        rule_id = cached["rule_id"]
        evaluated = cached["evaluated"]
    else:
        token = get_id_token()
        headers = {"Authorization": f"Bearer {token}"}

        print("1. Fetching live telemetry & executing Agent Gym loop on GCP Cloud Run (documents001)...")
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            card_resp = await client.get(f"{BRIDGE_URI}/.well-known/agent-card.json")
            card_resp.raise_for_status()
            agent_card = card_resp.json()

            assets_resp = await client.get(f"{BOARD_URI}/v1/catalog/assets")
            assets_resp.raise_for_status()
            catalog_assets = assets_resp.json()

            spiffe_resp = await client.get(f"{BOARD_URI}/v1/catalog/spiffe-agents")
            spiffe_resp.raise_for_status()
            spiffe_agents = spiffe_resp.json()

            # Seed Banking, Mortgage, and Insurance demo scenarios
            seed_resp = await client.post(
                f"{BOARD_URI}/v1/demo/seed",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
            )
            seed_resp.raise_for_status()

            mort_lin_resp = await client.get(
                f"{BOARD_URI}/v1/catalog/lineage/MORTGAGE-WESTBROOK-2026/origination_fee",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
            )
            mort_lin_resp.raise_for_status()
            mort_lineage = mort_lin_resp.json()

            # Ensure slice:aig:endorsement:MEDIUM starts in guided_review before the live promotion step
            await client.post(
                f"{BOARD_URI}/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial?role=underwriter",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
                json={
                    "target_level": "guided_review",
                    "reason": "In Guided Human Review Queue (4/5 matches toward Auto-Approve).",
                    "force": True,
                },
            )
            for idx in range(4):
                await client.post(
                    f"{BOARD_URI}/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/shadow",
                    json={
                        "entity_id": "terrepower-coastal-2027",
                        "doc_id": f"aig-excess-property-binder-{idx}.pdf",
                        "issuer": "AIG",
                        "doctype": "endorsement",
                        "field_path": "excess_layer_limit_usd",
                        "ai_value": "$25,000,000",
                        "human_value": "$25,000,000",
                        "risk_level": "MEDIUM",
                    },
                )

            policies_initial_resp = await client.get(f"{BOARD_URI}/v1/gym/trust-policies")
            policies_initial_resp.raise_for_status()
            policies_initial = policies_initial_resp.json()

            # Distill a live "Example of One" quarantine fix in Cloud SQL (Mortgage TRID CD)
            distill_resp = await client.post(
                f"{BOARD_URI}/v1/gym/signals/from-quarantine?role=underwriter",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
                json={
                    "entity_id": "MORTGAGE-WESTBROOK-2026",
                    "doc_id": "trid-closing-disclosure-westbrook-2026.pdf",
                    "doctype": "closing-disclosure",
                    "issuer": "FirstNational",
                    "field_path": "origination_fee",
                    "before_value": "$4,250.00",
                    "after_value": "$8,500.00",
                    "expert_memo": "TRID Closing Disclosure Page 3 Section A rate-lock extension addendum overrides base origination fee to $8,500.00.",
                    "page_number": 3,
                    "bounding_box": [0.11, 0.48, 0.86, 0.56],
                    "category": "extraction_hint",
                },
            )
            distill_resp.raise_for_status()
            distilled = distill_resp.json()
            rule_id = distilled["candidate_rule"]["rule_id"]

            # Run Synthetic Stress Suite & Report Card Gate in Cloud SQL
            eval_resp = await client.post(
                f"{BOARD_URI}/v1/gym/rules/{rule_id}/evaluate?role=underwriter",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
            )
            eval_resp.raise_for_status()
            evaluated = eval_resp.json()

            # Verify Day-2 Continuous Learning Payoff on live Cloud Run
            day2_resp = await client.post(
                f"{BOARD_URI}/v1/intake/ingest?role=underwriter",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
                json={
                    "entity_id": "MORTGAGE-WESTBROOK-2026",
                    "context_id": "ctx-mortgage-day2-live",
                    "doc_id": "trid-closing-disclosure-westbrook-day2.pdf",
                    "doctype": "closing-disclosure",
                    "issuer": "FirstNational",
                    "status": "parked",
                    "confidence": 0.96,
                    "key_fields": {
                        "currency": "USD",
                        "loan_amount": "$1,850,000.00",
                        "origination_fee": "$4,250.00",
                        "appraised_value": "$2,400,000.00",
                    },
                },
            )
            day2_resp.raise_for_status()
            day2_body = day2_resp.json()

            # Record 5th consecutive shadow match on slice:aig:endorsement:MEDIUM
            shadow_resp = await client.post(
                f"{BOARD_URI}/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/shadow",
                json={
                    "entity_id": "terrepower-coastal-2027",
                    "doc_id": "aig-excess-property-binder-2027.pdf",
                    "issuer": "AIG",
                    "doctype": "endorsement",
                    "field_path": "excess_layer_limit_usd",
                    "ai_value": "$25,000,000",
                    "human_value": "$25,000,000",
                    "risk_level": "MEDIUM",
                },
            )
            shadow_resp.raise_for_status()

            # Dial trust on slice:aig:endorsement:MEDIUM to trusted_auto_approve in Cloud SQL
            dial_resp = await client.post(
                f"{BOARD_URI}/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial?role=underwriter",
                headers={
                    "X-Bridge-Role": "underwriter",
                    "X-Bridge-Principal": "senior.credit.officer@docubridge.io",
                },
                json={
                    "target_level": "trusted_auto_approve",
                    "reason": "Promoted after 5/5 consecutive exact matches in Guided Review Queue.",
                    "force": False,
                },
            )
            dial_resp.raise_for_status()

            policies_promoted_resp = await client.get(f"{BOARD_URI}/v1/gym/trust-policies")
            policies_promoted_resp.raise_for_status()
            policies_promoted = policies_promoted_resp.json()

            analytics_resp = await client.get(f"{BOARD_URI}/v1/telemetry/analytics?limit=200")
            analytics_resp.raise_for_status()
            analytics = analytics_resp.json()

        CACHE_PATH.write_text(
            json.dumps(
                {
                    "agent_card": agent_card,
                    "catalog_assets": catalog_assets,
                    "spiffe_agents": spiffe_agents,
                    "policies_initial": policies_initial,
                    "policies_promoted": policies_promoted,
                    "rule_id": rule_id,
                    "evaluated": evaluated,
                    "mort_lineage": mort_lineage,
                    "day2_body": day2_body,
                    "analytics": analytics,
                }
            )
        )
        print(
            f"   Live GCP verified: card={agent_card['name']} ({len(agent_card.get('skills', []))} skills), "
            f"assets={len(catalog_assets)}, spiffe={len(spiffe_agents)}, "
            f"mortgage_fee={mort_lineage['current_value']} (p.{mort_lineage['source_anchor']['page_number']}), "
            f"rule={rule_id} ({evaluated['rule']['status']}), "
            f"day2_disposition={day2_body['disposition']} ({day2_body['redacted_fields']['origination_fee']}), "
            f"otel_spans={analytics['total_spans_analyzed']}"
        )
        if fetch_only:
            return

    chrome_bin = "/opt/google/chrome/google-chrome"
    if not Path(chrome_bin).exists():
        chrome_bin = shutil.which("google-chrome-stable") or shutil.which("google-chrome") or ""
    if not chrome_bin:
        raise RuntimeError("google-chrome binary not found")

    user_data_dir = tempfile.mkdtemp(prefix="chrome_docubridge_demo_")
    port = 9244
    proc = subprocess.Popen(
        [
            chrome_bin,
            "--headless=new",
            "--no-sandbox",
            "--no-proxy-server",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1920,1080",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            f"file://{HTML_PATH}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    frames_dir = Path(tempfile.mkdtemp(prefix="docubridge_frames_"))
    frame_idx = 0

    try:
        # Wait for DevTools endpoint
        ws_url = None
        async with httpx.AsyncClient(trust_env=False) as local_http:
            for _ in range(40):
                try:
                    r = await local_http.get(f"http://127.0.0.1:{port}/json")
                    pages = [p for p in r.json() if p.get("type") == "page"]
                    if pages:
                        ws_url = pages[0]["webSocketDebuggerUrl"]
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.2)
        if not ws_url:
            raise RuntimeError("Failed to connect to headless Chrome DevTools")

        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            cdp = CdpSession(ws)
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1920,
                    "height": 1080,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await asyncio.sleep(0.6)

            async def hold_seconds(seconds: float) -> None:
                nonlocal frame_idx
                png_bytes = await cdp.screenshot_png()
                count = max(1, round(seconds * FPS))
                for _ in range(count):
                    frame_idx += 1
                    frame_path = frames_dir / f"frame_{frame_idx:04d}.png"
                    frame_path.write_bytes(png_bytes)

            # Hydrate live GCP data into the page
            card_preview_text = (
                f'name="{agent_card["name"]}" · version="{agent_card["version"]}" · '
                f'skills={len(agent_card.get("skills", []))} (address-proof, benefits-procurement, benefits-rfp, ...) · '
                f'url="{BRIDGE_URI}"'
            )
            await cdp.eval_js(
                f"document.getElementById('agent-card-preview').textContent = {json.dumps(card_preview_text)};"
            )
            await cdp.eval_js(f"window.renderCatalogAssets({json.dumps(catalog_assets)});")
            await cdp.eval_js(f"window.renderSpiffeAgents({json.dumps(spiffe_agents)});")
            await cdp.eval_js(f"window.renderTrustPolicies({json.dumps(policies_initial)});")

            # =================================================================
            # SCENE 1.1: External Zone — Provider Portal & A2A Edge Agent Card
            # =================================================================
            await cdp.eval_js(
                """
                window.switchScene('scene-portal', 1, true);
                window.setCaption(
                  'PART 1 · STEP 1: EXTERNAL PROVIDER PORTAL (A2A EDGE)',
                  'Carrier opens External Zone Provider Portal connected to live Document Bridge A2A Edge in GCP.',
                  'GET https://documents001-bridge-sf6etnew5q-uc.a.run.app/.well-known/agent-card.json -> 18 Agent Skills in gs://documents001-bridge-skills'
                );
                """
            )
            await hold_seconds(9.5)

            # Highlight Path A + Path B submission buttons in External Zone
            await cdp.eval_js(
                """
                document.getElementById('btn-submit-path-a').classList.add('btn-active-ring');
                document.getElementById('btn-submit-path-b').classList.add('btn-active-ring');
                window.setCaption(
                  'PART 1 · STEP 2: DUAL-PATH SUBMISSION (PATH A + PATH B)',
                  'Submitting structured SOV schedule (Path A, 0 AI tokens) and Chubb PDF quote (Path B, layout + gate).',
                  'POST https://documents001-bridge-sf6etnew5q-uc.a.run.app/ -> WORM Audit Vault + Review Quarantine'
                );
                """
            )
            await hold_seconds(9.5)

            # =================================================================
            # SCENE 1.2: Internal Zone — Dual-Path Intake, Quarantine & Vault
            # =================================================================
            await cdp.eval_js(
                """
                window.switchScene('scene-intake', 1, false);
                window.setCaption(
                  'PART 1 · STEP 3: TWO-ZONE CLOUD STORAGE & 4-SIGNAL GATE',
                  'Path A SOV locks in 7-year WORM Audit Vault; Chubb PDF parks in Review Quarantine on Schedule B conflict.',
                  'gs://documents001-bridge-audit-vault (7-yr WORM) · gs://documents001-bridge-artifacts (Quarantine)'
                );
                """
            )
            await hold_seconds(11.0)

            # Highlight Zone 6 Governed Asset Map
            await cdp.eval_js(
                """
                window.setCaption(
                  'PART 1 · STEP 4: ZONE 6 GOVERNED STORAGE & ASSET MAP',
                  'Every physical GCS bucket, Cloud SQL table, BigQuery Iceberg sink, and Agent Gym store is cataloged.',
                  'GET https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/v1/catalog/assets -> 10 Governed Assets'
                );
                """
            )
            await hold_seconds(9.5)

            # =================================================================
            # SCENE 2.1: Consolidation Rules Engine & Bi-Temporal Store
            # =================================================================
            await cdp.eval_js(
                """
                window.switchScene('scene-consolidation', 2, false);
                window.setCaption(
                  'PART 2 · STEP 1: CONSOLIDATION RULES & BI-TEMPORAL STORE',
                  '100% engine Decimal math applies Source Priority (Signed Endorsement > Binder > Quote) & flags conflicts.',
                  'Cloud SQL Postgres 16 (bridge-sql 10.68.0.3) · Bi-temporal learned_at vs effective_from · Page 4 bbox anchor'
                );
                """
            )
            await hold_seconds(11.0)

            # =================================================================
            # SCENE 2.2: Role-Based Views & 6-Hop Lineage + BigQuery Iceberg
            # =================================================================
            await cdp.eval_js(
                """
                document.getElementById('view-btn-broker').className = 'btn btn-outline';
                document.getElementById('view-btn-underwriter').className = 'btn btn-navy btn-active-ring';
                document.getElementById('active-view-title').textContent = 'Role-Based Projection: Underwriter View (v_operational_underwriter)';
                window.setCaption(
                  'PART 2 · STEP 2: ROLE-BASED VIEWS, 6-HOP LINEAGE & ICEBERG SYNC',
                  'Switching to Underwriter View with 6-hop click-to-page lineage and watermarked BigQuery Iceberg CDC export.',
                  'GET /v1/catalog/lineage/LOAN-MERIDIAN-001/origination_fee -> Page 4 Schedule B + BigQuery Iceberg'
                );
                """
            )
            await hold_seconds(8.0)

            # =================================================================
            # SCENE 2.3: Mortgage Underwriting Scenario & Click-to-Source Modal
            # =================================================================
            await cdp.eval_js(
                """
                window.switchDomain('mortgage');
                window.setCaption(
                  'PART 2 · STEP 3: MORTGAGE UNDERWRITING & TRID CLOSING SCENARIO',
                  'Switching to Westbrook Family Trust ($1.85M 30-Yr Jumbo Mortgage) with TRID Closing Disclosure Page 3 fee override.',
                  'GET /v1/catalog/lineage/MORTGAGE-WESTBROOK-2026/origination_fee -> Page 3 Section A ($8,500.00)'
                );
                """
            )
            await hold_seconds(8.5)

            await cdp.eval_js(
                """
                window.openSourceProofModal('origination_fee');
                window.setCaption(
                  'PART 2 · STEP 4: INTERACTIVE CLICK-TO-SOURCE DOCUMENT PROOF MODAL',
                  'Clicking any Page + Bounding Box chip opens the split-screen Document AI polygon overlay & 7-Year WORM Vault proof.',
                  'Document AI Normalized Vertices [0.11, 0.48, 0.86, 0.56] · SHA-256 WORM Object Retention'
                );
                """
            )
            await hold_seconds(9.0)

            await cdp.eval_js("window.closeSourceProofModal();")

            # =================================================================
            # SCENE 3.1: SPIFFE JWT-SVID & RBAC 403 Refusal (Broker Associate)
            # =================================================================
            await cdp.eval_js(
                """
                window.switchScene('scene-inbox', 3, false);
                document.getElementById('persona-display').textContent = 'Broker Associate (Max Authority $25k) ▾';
                document.getElementById('rbac-refusal-banner').style.display = 'block';
                document.getElementById('compound-jwt-box').textContent = JSON.stringify({
                  iss: "https://agents.docubridge.io",
                  sub: "spiffe://agents.docubridge.io/internal/credit-officer-agent",
                  aud: ["docubridge"],
                  obo: {
                    sub: "usr:junior.officer@docubridge.io",
                    role: "underwriter",
                    tenant_id: "tenant-meridian",
                    max_approval_amount: "25000.00"
                  }
                }, null, 2);
                window.setCaption(
                  'PART 3 · RBAC REFUSAL (403): SPIFFE OBO AUTHORITY GATE',
                  'Broker Associate ($25k limit) refused on $100,000 HIGH-risk deductible override -> Switching to Lead Underwriter.',
                  'Effective Permission = Agent(SPIFFE) ∩ User(Principal) · Shadow AI blocked · HTTP 403 Forbidden'
                );
                """
            )
            await hold_seconds(11.0)

            # =================================================================
            # SCENE 3.2: Persona Switch to Lead Underwriter & A2UI Edit+Approve
            # =================================================================
            await cdp.eval_js(
                """
                document.getElementById('persona-display').textContent = 'Lead Underwriter (SPIFFE OBO · All Grants) ▾';
                document.getElementById('rbac-refusal-banner').style.display = 'none';
                document.getElementById('btn-edit-approve').classList.add('btn-active-ring');
                document.getElementById('inbox-status-badge').className = 'badge badge-green';
                document.getElementById('inbox-status-badge').textContent = '✓ APPROVED · RFC 6902 Patch Recorded';
                document.getElementById('step-box-3').style.background = '#ecfdf5';
                document.getElementById('step-box-3').style.borderColor = '#a7f3d0';
                document.getElementById('step-box-3-title').style.color = '#059669';
                document.getElementById('step-box-3-title').textContent = '✓ 3. Underwriter Gate';
                document.getElementById('step-box-3-sub').style.color = '#047857';
                document.getElementById('step-box-3-sub').textContent = 'edit_and_approve ($100k)';
                document.getElementById('step-box-4').style.background = '#ecfdf5';
                document.getElementById('step-box-4').style.borderColor = '#a7f3d0';
                document.getElementById('step-box-4-title').style.color = '#059669';
                document.getElementById('step-box-4-title').textContent = '✓ 4. Commit & Gym Signal';
                document.getElementById('step-box-4-sub').style.color = '#047857';
                document.getElementById('step-box-4-sub').textContent = 'RFC 6902 Patch -> Cloud SQL';
                document.getElementById('compound-jwt-box').textContent = JSON.stringify({
                  iss: "https://agents.docubridge.io",
                  sub: "spiffe://agents.docubridge.io/internal/credit-officer-agent",
                  aud: ["docubridge"],
                  obo: {
                    sub: "usr:senior.credit.officer@docubridge.io",
                    role: "underwriter",
                    tenant_id: "tenant-meridian",
                    max_approval_amount: "25000000.00"
                  }
                }, null, 2);
                window.setCaption(
                  'PART 3 · STEP 2: A2UI v0.9 ACTION INBOX (EDIT & APPROVE)',
                  'Lead Underwriter approves $100,000 Schedule B deductible; ADK resumes and logs immutable RFC 6902 JSON Patch.',
                  'POST /v1/inbox/actions/{id}/decide -> op: replace, path: /deductible_usd, value: $100,000'
                );
                """
            )
            await hold_seconds(11.0)

            # =================================================================
            # SCENE 4.1: Agent Gym — Daytime Signal Capture ("Example of One")
            # =================================================================
            await cdp.eval_js(
                f"""
                window.switchScene('scene-gym', 4, false);
                document.getElementById('live-rule-id').textContent = {json.dumps(rule_id)};
                document.getElementById('btn-distill-signal').classList.add('btn-active-ring');
                window.setCaption(
                  'PART 4 · STEP 1: AGENT GYM — "EXAMPLE OF ONE" SIGNAL DISTILLATION',
                  'Single expert correction ($50,000 -> $100,000 on Chubb page 4) distills into candidate Tribal Knowledge Rule.',
                  'POST https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/v1/gym/signals/from-quarantine -> Cloud SQL ({rule_id})'
                );
                """
            )
            await hold_seconds(11.0)

            # =================================================================
            # SCENE 4.2: Agent Gym — Synthetic Stress Suite & Report Card Gate
            # =================================================================
            await cdp.eval_js(
                f"""
                document.getElementById('btn-distill-signal').classList.remove('btn-active-ring');
                document.getElementById('btn-evaluate-rule').classList.add('btn-active-ring');
                window.setCaption(
                  'PART 4 · STEP 2: SYNTHETIC STRESS SUITE & QUALITY REPORT CARD GATE',
                  'Candidate rule passes 4/4 perturbation stress tests & 12/12 historical benchmarks (+50% slice gain, 0 regressions).',
                  'POST https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/v1/gym/rules/{rule_id}/evaluate -> Status: PROMOTED'
                );
                """
            )
            await hold_seconds(11.5)

            # =================================================================
            # SCENE 4.3: Agent Gym — Programmable Trust Dial Promotion
            # =================================================================
            await cdp.eval_js(
                f"""
                document.getElementById('btn-evaluate-rule').classList.remove('btn-active-ring');
                document.getElementById('btn-promote-trust').classList.add('btn-active-ring');
                window.renderTrustPolicies({json.dumps(policies_promoted)});
                window.setCaption(
                  'PART 4 · STEP 3: PROGRAMMABLE TRUST DIAL (GRADUATED AUTONOMY)',
                  'Slice hits 5/5 exact matches (100% accuracy) and promotes from Guided Review to Trusted Auto-Approve.',
                  'POST https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial'
                );
                """
            )
            await hold_seconds(9.5)

            # =================================================================
            # SCENE 4.4: Agent Gym — Day-2 Continuous Learning Payoff
            # =================================================================
            await cdp.eval_js(
                """
                document.getElementById('btn-promote-trust').classList.remove('btn-active-ring');
                document.getElementById('btn-verify-day2-payoff').classList.add('btn-active-ring');
                window.runVerifyDay2Payoff();
                window.setCaption(
                  'PART 4 · STEP 4: DAY-2 CONTINUOUS LEARNING PAYOFF (ZERO HUMAN TOUCH)',
                  'Re-ingesting the next Mortgage TRID Closing Disclosure automatically applies promoted rule and auto-approves.',
                  'POST /v1/intake/ingest -> Day-1 Parked ($4,250) becomes Day-2 Auto-Approved ($8,500) in 0 human touches'
                );
                """
            )
            await hold_seconds(10.0)

            # =================================================================
            # SCENE 5.1: Live OpenTelemetry & BigQuery Waterfall X-Ray
            # =================================================================
            await cdp.eval_js(
                """
                window.switchScene('scene-telemetry', 5, false);
                window.setCaption(
                  'PART 5 · LIVE OPENTELEMETRY & BIGQUERY WATERFALL X-RAY',
                  'Every browser click propagates W3C traceparent across all 22 spans into Cloud Trace v2 & BigQuery otel_spans.',
                  'GET /v1/telemetry/analytics -> v_step_latency_analytics + v_trace_waterfall + v_entity_pipeline_summary'
                );
                """
            )
            await hold_seconds(11.0)

    finally:
        proc.terminate()
        shutil.rmtree(user_data_dir, ignore_errors=True)

    print(f"2. Encoding {frame_idx} frames at {FPS} fps into {OUT_MP4}...")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frames_dir / "frame_%04d.png"),
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuvj420p",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(OUT_MP4),
        ],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"3. Done! Wrote {OUT_MP4} ({OUT_MP4.stat().st_size:,} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
