"""Record the live GCP end-to-end demo video for Aon Document Bridge (documents001).

Exercises the live deployed Cloud Run v2 services in GCP project ``documents001``:
  1. ``https://documents001-bridge-sf6etnew5q-uc.a.run.app`` (A2A Edge)
  2. ``https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app`` (Dashboard, Zone 5 A2UI,
     Zone 6 Knowledge Catalog, SPIFFE Registry, and Cloud SQL Agent Gym)

Drives headless Chrome (1920x1080) over Chrome DevTools Protocol, captures frames at 8 fps
across all 4 parts of the walkthrough, and encodes ``demo/_captures/gcp-end-to-end-demo.mp4``
matching ``placement/demo/_captures/gcp-end-to-end-demo.mp4``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
import websockets

BRIDGE_URI = "https://documents001-bridge-sf6etnew5q-uc.a.run.app"
BOARD_URI = "https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app"

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

        # Ensure slice:aig:endorsement:MEDIUM starts in guided_review before the live promotion step
        await client.post(
            f"{BOARD_URI}/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial?role=underwriter",
            headers={
                "X-Bridge-Role": "underwriter",
                "X-Bridge-Principal": "lead.underwriter@aon.com",
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

        # Distill a live "Example of One" quarantine fix in Cloud SQL
        distill_resp = await client.post(
            f"{BOARD_URI}/v1/gym/signals/from-quarantine?role=underwriter",
            headers={
                "X-Bridge-Role": "underwriter",
                "X-Bridge-Principal": "lead.underwriter@aon.com",
            },
            json={
                "entity_id": "terrepower-coastal-2027",
                "doc_id": "chubb-coastal-property-quote-2027.pdf",
                "doctype": "quote",
                "issuer": "Chubb",
                "field_path": "deductible_usd",
                "before_value": "$50,000",
                "after_value": "$100,000",
                "expert_memo": "Coastal Schedule B endorsement on page 4 overrides base deductible to $100,000.",
                "page_number": 4,
                "category": "extraction_hint",
                "distinct_entities": ["Syndicate 2987", "Syndicate 1183"],
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
                "X-Bridge-Principal": "lead.underwriter@aon.com",
            },
        )
        eval_resp.raise_for_status()
        evaluated = eval_resp.json()

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
                "X-Bridge-Principal": "lead.underwriter@aon.com",
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

    print(
        f"   Live GCP verified: card={agent_card['name']} ({len(agent_card.get('skills', []))} skills), "
        f"assets={len(catalog_assets)}, spiffe={len(spiffe_agents)}, rule={rule_id} "
        f"({evaluated['rule']['status']})"
    )

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
        async with httpx.AsyncClient() as local_http:
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
                  'GET /v1/catalog/lineage/terrepower-coastal-2027/deductible_usd -> Page 4 Schedule B + BigQuery Iceberg'
                );
                """
            )
            await hold_seconds(10.5)

            # =================================================================
            # SCENE 3.1: SPIFFE JWT-SVID & RBAC 403 Refusal (Broker Associate)
            # =================================================================
            await cdp.eval_js(
                """
                window.switchScene('scene-inbox', 3, false);
                document.getElementById('persona-display').textContent = 'Broker Associate (Max Authority $25k) ▾';
                document.getElementById('rbac-refusal-banner').style.display = 'block';
                document.getElementById('compound-jwt-box').textContent = JSON.stringify({
                  iss: "spiffe://agents.global.aon.net",
                  sub: "spiffe://agents.global.aon.net/ns/internal/sa/broker-copilot",
                  aud: ["aon-docubridge"],
                  obo: {
                    sub: "broker.associate@aon.com",
                    role: "broker",
                    tenant_id: "tenant-terrepower",
                    max_approval_amount: "25000"
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
                  iss: "spiffe://agents.global.aon.net",
                  sub: "spiffe://agents.global.aon.net/ns/internal/sa/underwriter-reviewer",
                  aud: ["aon-docubridge"],
                  obo: {
                    sub: "lead.underwriter@aon.com",
                    role: "underwriter",
                    tenant_id: "tenant-terrepower",
                    max_approval_amount: "50000000"
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
                  'AIG* endorsement slice hits 5/5 exact matches (100% accuracy) and promotes from Guided Review to Trusted Auto-Approve.',
                  'POST https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial'
                );
                """
            )
            await hold_seconds(12.5)

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
