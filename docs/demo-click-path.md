# DocuBridge — 5-Minute Live Demo Click-Path & Gemini Platform Architecture Guide

- **Live Demo Console:** `https://documents001-bridge-rfp-board-321488252989.us-central1.run.app/demo`
- **A2A Edge Agent Card:** `https://documents001-bridge-321488252989.us-central1.run.app/.well-known/agent-card.json`
- **3-Slide Google Slides Presentation (`20261022 DocuBridge Observability & Agent Improvement Loop`):** `https://docs.google.com/presentation/d/1BMUfz5_kWUgPHCgO-cFcZOyAD2_YU4UjO14Af_wragI/edit`
- **Multi-Model Split:** `gemini-3.8-flash` (Runtime Workhorse) + `gemini-4-argon` (Teacher / Judge / Escalation)

---

## 1. Gemini Platform 4-Pillar Architecture Map

1. **BUILD (ADK 2.11 + Multi-Model + Skills):**
   - `gemini-3.8-flash` handles high-volume multimodal document extraction.
   - `gemini-4-argon` handles cross-document conflict adjudication, Agent Gym rule distillation, 3-Tier evaluation judging, and `SKILL.md` compilation.
   - Skills live in `gs://documents001-bridge-skills` and register in the Gemini platform Skill Registry (`Skill` container + immutable `SkillRevision` chain).
2. **GOVERN (Agent Registry + Agent Gateway + Identity):**
   - **`CLIENT_TO_AGENT` Ingress Gateway:** Runs Model Armor (`PI_AND_JAILBREAK_FILTER`) and Cloud DLP inline SSN redaction (`[REDACTED-SSN]`) before requests reach Agent Runtime.
   - **Agent Registry + Skill Registry (`agentregistry.googleapis.com`):** Tracks `Agents`, `MCP Servers`, `Endpoints`, `Skills & Revisions`, and `Bindings`.
   - **`AGENT_TO_ANYWHERE` Egress Gateway:** Enforces Agent Registry membership, rejects URN misuse (`HTTP 404`), and evaluates IAM v3 Unified Access Policy (`iap.googleapis.com/resources.egressViaIAP`) CEL rules where `DENY` overrides `ALLOW`.
3. **SCALE (Agent Engine Runtime + Bi-Temporal Store + A2UI):**
   - Split topology between Cloud Run (`documents001-bridge` & `documents001-bridge-rfp-board`, `min_instances = 1`) as the HTTP/SSE/A2UI/REST edge and Agent Engine (`ReasoningEngine` `5963861295097708544`) hosting ADK `Sessions`, `Memory Bank`, and `BridgeA2aAgentEngine`.
   - Append-only bi-temporal Operational Store (`recorded_at` vs. `effective_from`) + 7-year write-once Cloud Storage Audit Vault.
4. **OPTIMIZE (Agent Observability + 3-Tier Eval + Agent Gym):**
   - OpenTelemetry `gen_ai.*` spans exported to Cloud Trace v2 and BigQuery (`documents001.docubridge_telemetry.otel_spans`) with FinOps traffic attribution (`production_inference`, `conflict_escalation`, `agent_gym_eval`).
   - Closed-loop Agent Gym with Two-Sided Oversight (`RSR >= 0.50`), 3-Tier Evaluation Gate, `SKILL.md` revision compilation (`v1.0.0 -> v1.1.0`), 1-click rollback, and Process-Instance Skill Lineage.

---

## 2. Minute-by-Minute Live Demo Click-Path (5 Minutes)

### Minute 0:00–1:00 — Scene 1 & Scene 2: Dual-Path Intake & Bi-Temporal Conflict Detection
1. Open `https://documents001-bridge-rfp-board-321488252989.us-central1.run.app/demo` and select **`🏦 Banking`** (`Meridian Logistics Center LLC — $45M CRE Term Loan`, or switch to **`🏠 Mortgage · TRID Closing`** / **`🛡️ Insurance`**).
2. In **Scene 1 (Dual-Path Intake)**:
   - Contrast **Path A (Structured MISMO / ACORD / JSON)** bypassing OCR at zero token cost against **Path B (Documents)** running Document AI bounding-polygon extraction + `gemini-3.8-flash`.
   - Click any highlighted value (`$100,000.00` Schedule B origination fee addendum) to open the **Click-to-Source Document Proof Modal** showing the PDF page, normalized polygon, and 7-year write-once Cloud Storage Audit Vault lock.
3. Click **`2. Operational Store & Views`**:
   - Show the **Bi-Temporal Ledger** (`recorded_at` vs. `effective_from`) flagging the `$50,000.00` vs. `$100,000.00` origination fee conflict without overwriting prior history.

### Minute 1:00–2:30 — Scene 3: Knowledge Catalog, Agent Registry, Skill Registry & Agent Gateway
1. Click **`3. Catalog & Lineage`** and walk through the **6-Hop End-to-End Lineage**.
2. In the **Gemini Platform Agent Registry, Skill Registry & Gateway Explorer**, click through the **6 tabs**:
   - **`Agents`**, **`MCP Servers`**, and **`Endpoints`**: Show registered resources (`projects/321488252989/locations/us-central1/...`) and model assignments (`gemini-3.8-flash` + `gemini-4-argon`).
   - **`Skills & Revs`**: Show the 2-level hierarchy (`Skill` container + immutable `SkillRevision` chain `rev-v1-0-baseline` with `.zip` SHA-256 digest in `gs://documents001-bridge-skills`).
   - **`Bindings`**: Click **`+ Register Custom MCP Service`** and **`+ Create Binding`** (demonstrating that URN misuse is rejected with `HTTP 404 NOT_FOUND` while hierarchical Resource Names succeed with `HTTP 200`).
   - **`Gateway & CEL (2)`**: Click the **4 live simulation buttons**:
     - **`🛡️ Test Ingress (Cloud DLP SSN Redact)`** -> `ALLOW_CLIENT_TO_AGENT_INGRESS (200)` with inline SSN scrubbed to `[REDACTED-SSN]`.
     - **`🚫 Test Ingress (Model Armor Block PI)`** -> `DENY_MODEL_ARMOR_PROMPT_INJECTION (403)`.
     - **`✅ Test Egress (Allowed MCP Tool)`** -> `ALLOW_EGRESS_VIA_IAP (200)`.
     - **`🔒 Test Egress (CEL Deny drop_table)`** -> `DENY_UAP_CEL_POLICY (403)`.

### Minute 2:30–4:15 — Scene 4: Agent Gym Loop, Two-Sided Oversight (`RSR`), 3-Tier Eval & Skill Lineage
1. Click **`4. Agent Gym (Learning & Trust)`**.
2. Click **`1. Capture Expert Signal & Distill Rule`** (`gemini-4-argon` Teacher) and highlight the **Two-Sided Oversight Badge (`RSR: 1.00 — Genuine Review`)**.
3. Click **`2. Run Synthetic Stress Test & 3-Tier Gate`** and show the **3-Tier Evaluation Pills** (Tier 1 `Rule 0` Deterministic `Decimal` Math `1.00` + Tier 2 `gemini-4-argon` Judge `0.97` + Tier 3 `FA=0` Corridor `PASS`).
4. Click **`🧬 6. Compile to SKILL.md (gemini-4-argon)`** to mint `SkillRevision` `v1.1.0` (`rev-v1-1-...`, with 1-click **`⏪ Rollback to v1.0.0`**).
5. Click **`⚡ 5. Verify Day-2 Payoff (Re-Ingest Next Deal)`** and inspect the **Process-Instance Skill Lineage Card** showing Turn 1 (`addendum@v1.0.0`) vs. Turn 2 (`addendum@v1.1.0`) in the same deal.

### Minute 4:15–5:00 — Scene 5: OpenTelemetry `gen_ai.*` Waterfall & Multi-Model FinOps X-Ray
1. Click **`🔭 5. Trace & BQ X-Ray`**.
2. Show the **Multi-Model FinOps Split Cards** (`gemini-3.8-flash` workhorse vs. `gemini-4-argon` reasoning/eval) and the **Live W3C OpenTelemetry Waterfall** streaming to Cloud Trace and BigQuery (`documents001.docubridge_telemetry.otel_spans`).
3. Click **`↺ Reset Demo`** in the top-right header to restore a clean baseline.
