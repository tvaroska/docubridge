# DocuBridge — 5-Minute Live Demo Click-Path & Gemini Platform Architecture Guide

- **Live Demo Console:** `https://docubridge-showcase-bridge-rfp-board-100000000000.us-central1.run.app/demo`
- **A2A Edge Agent Card:** `https://docubridge-showcase-bridge-100000000000.us-central1.run.app/.well-known/agent-card.json`
- **Multi-Model Split:** `gemini-3.8-flash` (Runtime Workhorse) + `gemini-4-argon` (Teacher / Judge / Escalation)

---

## 1. Gemini Platform 4-Pillar Architecture Map

1. **BUILD (ADK 2.11 + Multi-Model + Skills):**
   - `gemini-3.8-flash` handles high-volume multimodal document extraction.
   - `gemini-4-argon` handles cross-document conflict adjudication, Agent Gym rule distillation, 3-Tier evaluation judging, and `SKILL.md` compilation.
   - Skills live in `gs://docubridge-showcase-bridge-skills` and register in the Gemini platform Skill Registry (`Skill` container + immutable `SkillRevision` chain).
2. **GOVERN (Agent Registry + Agent Gateway + Identity):**
   - **`CLIENT_TO_AGENT` Ingress Gateway:** Runs Model Armor (`PI_AND_JAILBREAK_FILTER`) and Cloud DLP inline SSN redaction (`[REDACTED-SSN]`) before requests reach Agent Runtime.
   - **Agent Registry + Skill Registry (`agentregistry.googleapis.com`):** Tracks `Agents`, `MCP Servers`, `Endpoints`, `Skills & Revisions`, and `Bindings`.
   - **`AGENT_TO_ANYWHERE` Egress Gateway:** Enforces Agent Registry membership, rejects URN misuse (`HTTP 404`), and evaluates IAM v3 Unified Access Policy (`iap.googleapis.com/resources.egressViaIAP`) CEL rules where `DENY` overrides `ALLOW`.
3. **SCALE (Agent Engine Runtime + Bi-Temporal Store + A2UI):**
   - Split topology between Cloud Run (`docubridge-showcase-bridge` & `docubridge-showcase-bridge-rfp-board`, `min_instances = 1`) as the HTTP/SSE/A2UI/REST edge and Agent Engine (`ReasoningEngine`, `BRIDGE_AGENT_ENGINE_ID`) hosting ADK `Sessions`, `Memory Bank`, and `BridgeA2aAgentEngine`.
   - Append-only bi-temporal Operational Store (`recorded_at` vs. `effective_from`) + 7-year write-once Cloud Storage Audit Vault.
4. **OPTIMIZE (Agent Observability + 3-Tier Eval + Agent Gym):**
   - OpenTelemetry `gen_ai.*` spans exported to Cloud Trace v2 and BigQuery (`docubridge-showcase.docubridge_telemetry.otel_spans`) with FinOps traffic attribution (`production_inference`, `conflict_escalation`, `agent_gym_eval`).
   - Calibration (closed-loop Agent Gym) with Two-Sided Oversight (`RSR >= 0.50`), 3-Tier Evaluation Gate, `SKILL.md` revision compilation (`v1.0.0 -> v1.1.0`), 1-click rollback, and Process-Instance Skill Lineage.

---

## 2. Minute-by-Minute Live Demo Click-Path (5 Minutes)

### Minute 0:00–1:00 — Scene 1 & Scene 2: Dual-Path Intake & Bi-Temporal Conflict Detection
1. Open `https://docubridge-showcase-bridge-rfp-board-100000000000.us-central1.run.app/demo` and select **`🏦 Banking`** (`Meridian Logistics Center LLC — $45M CRE Term Loan`, `$45,000,000` / `72.00% LTV` on `$62,500,000` appraisal; or switch to **`🏠 Mortgage · TRID Closing`** `$1.85M` / `$1,850,000` / `77.08% LTV` with `$4,250.00 -> $8,500.00` rate-lock fee override, or **`🛡️ Insurance`** `$142,500,000` TIV / `$418,500.00` premium).
2. In **Scene 1 (Dual-Path Intake)**:
   - Contrast **Path A (Structured MISMO / ACORD / JSON)** bypassing OCR at zero token cost against **Path B (Documents)** running Document AI bounding-polygon extraction + `gemini-3.8-flash`.
   - Click any highlighted value (`$100,000.00` Schedule B origination fee addendum) to open the **Click-to-Source Document Proof Modal** showing the PDF page, normalized polygon, and 7-year write-once Cloud Storage Audit Vault lock.
3. Click **`2. Operational Store & Views`**:
   - Show the **Bi-Temporal Ledger** (`recorded_at` vs. `effective_from`) flagging the `$50,000.00` vs. `$100,000.00` origination fee conflict without overwriting prior history.

### Minute 1:00–2:30 — Scene 3: Knowledge Catalog, Agent Registry, Skill Registry & Agent Gateway
1. Click **`3. Catalog & Lineage`** and walk through the **6-Hop End-to-End Lineage**.
2. In the **Gemini Platform Agent Registry, Skill Registry & Gateway Explorer**, click through the **6 tabs**:
   - **`Agents`**, **`MCP Servers`**, and **`Endpoints`**: Show registered resources (`projects/100000000000/locations/us-central1/...`) and model assignments (`gemini-3.8-flash` + `gemini-4-argon`).
   - **`Skills & Revs`**: Show the 2-level hierarchy (`Skill` container + immutable `SkillRevision` chain `rev-v1-0-baseline` with `.zip` SHA-256 digest in `gs://docubridge-showcase-bridge-skills`).
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

### Minute 4:15–5:00 — Scene 5: OpenTelemetry `gen_ai.*` Waterfall, Multi-Model FinOps & BigQuery SQL Trace Evaluation
1. Click **`🔭 5. Trace & BQ X-Ray`**.
2. Show the **Multi-Model FinOps Split Cards** (`gemini-3.8-flash` workhorse vs. `gemini-4-argon` reasoning/eval) and the **Live W3C OpenTelemetry Waterfall** streaming to Cloud Trace and BigQuery (`docubridge-showcase.docubridge_telemetry.otel_spans`).
3. Click **`🧮 Run BQ SQL Trace Eval`** in the header and walk through the **BigQuery SQL Trace Evaluation (`v_trace_sql_evaluation` + `AI.GENERATE_TABLE`)** panel:
   - **5-Gate Deterministic SQL Trajectory Scorecard (`Rule 0`)**: Evaluates 100% of traces in SQL at **`$0` LLM token cost** (`Gate 1: PII & WORM Lock Before Extract`, `Gate 2: SPIFFE JWT-SVID Verified`, `Gate 3: Decimal Math & Bi-Temporal Ledger`, `Gate 4: Two-Sided Oversight RSR >= 0.50`, `Gate 5: FinOps Model Right-Sizing`).
   - **3-Tab Interactive BigQuery Standard SQL Query Inspector**: Click through **`1. Rule 0 Trajectory SQL`** (`v_trace_sql_evaluation` pre-filtering `needs_llm_judge_sampling`), **`2. AI.GENERATE_TABLE Judge`** (invoking `gemini-4-argon` only on anomalous/escalated/canary traces for `95%` judge token savings), and **`3. Rogan-Gladen & SME SQL`** (joining SME annotations to compute bias-corrected pass rate $\hat{\theta} = 99.1\%$).
4. Click **`↺ Reset Demo`** in the top-right header to restore a clean baseline.

---

## 3. Insurance & Schema Evolution Showcase (insurance-first deployment, `BRIDGE_DEFAULT_DEMO_DOMAIN=insurance`)

- **Insurance & Schema Evolution Live Demo Console (a second deployment of the same stack):** `https://<service>-rfp-board-<PROJECT_NUMBER>.<region>.run.app/demo` (defaults directly to **`🛡️ Insurance — Commercial Property · terrepower-coastal-2027`** via `BRIDGE_DEFAULT_DEMO_DOMAIN=insurance`)
- **A2A Edge Agent Card (insurance-first deployment):** `https://<service>-<PROJECT_NUMBER>.<region>.run.app/.well-known/agent-card.json`

### 4-Beat Live Schema Evolution & Bi-Temporal Ontology Graph Walkthrough (Scene 1 `#schema-evolution-stage` + Scene 4 `#refinery-card`)
At the top of `#schema-evolution-stage`, use the 2-button **SCENARIO** switcher to toggle between:
- **`🏥 Employee Benefits · HDHP (HSA vs. FSA) & $125K Rate Addendum`** (`BENEFITS-APEX-HDHP-2027` → `BENEFITS-NORTHSTAR-DAY2`, **default**):
  1. **Beat 0 — Baseline Ontology (`ont-rev-v1-1-baseline`, 7 Entities):** `150-Page Carrier Base Quote PDF ($140,000.00, Prio 20)` overrides `Manual Workbook Entry ($145,000.00, Prio 10)`, and the 4-part field guide maps `'Elimination Period'` / `'Qualifying Period'` to `90-Day Benefit Waiting Period` (Page 14) while ignoring the `30-Day New-Hire Enrollment Wait` (Page 2).
  2. **Beat 1 — Click `1. Upload Page 4 HDHP Rider & $125K Rate Addendum (Guesses "FSA: $1,000" ✕)`:** Page 4 prints `"Plan: HDHP · Employer Account Seed: $1,000.00"` without writing `HSA` or `FSA`; prompt-only extraction (`~60%`) guesses **`"FSA: $1,000.00" ✕`**, and the 2-page `$125,000.00` Signed Rate Addendum is quarantined under `benefits-quote` ($J(A,B) = 0.0714 \le 0.10$).
  3. **Beat 2 — Click `2. Challenge Fix ("FSA" → "HSA") & Run 3-Tier Assay (0% Backsliding)`:** Broker changes `"FSA"` $\to$ `"HSA"` on Page 4. Agent Gym & Schema Refinery ask the Socratic clarifying question: **`"Is this only for this quote — or can ANY HDHP plan never have a standard FSA?"`** and compile `HDHP [:INCLUDES] HSA`, `HDHP [:EXCLUDES] FSA`, and `rate-guarantee-addendum` (`Prio 40`, $\text{NFR} = 0.0\%$).
  4. **Beat 3 — Click `3. Promote Ontology v1.2.0 ("HSA: $1,000" ✓ + $125K Addendum Wins)`:** Promotes `ont-rev-v1-2-benefits` (`v1.2.0`). The Knowledge Graph rules out `FSA ✕`, extracts **`"HSA: $1,000.00" ✓`** (`95%+`), and promotes the **`$125,000.00` Signed Rate Addendum** over the `$140,000.00` Base Quote PDF and `$145,000.00` Manual Entry across all 8 specialty teams.
  5. **Beat 4 — Click `4. Verify Day-2 Payoff (Next HDHP Renewal · 134ms)`:** Next employer renewal (`BENEFITS-NORTHSTAR-DAY2` — `NorthStar Precision Systems`, `"Plan: HDHP · Employer Account Seed: $1,500.00"` + `$118,000.00` 2-page addendum) extracts **`"HSA: $1,500.00" ✓`** in `134 ms` with `0` quarantine parks and `0` escalation tokens.
- **`🏭 Commercial Property · $100K Coastal Wind Endorsement`** (`terrepower-coastal-2027` → `PLACEMENT-HARBORVIEW-DAY2`):
1. **Beat 0 — Baseline Ontology (`ont-rev-v1-1-baseline`, 7 Doctypes):**
   - Open `https://<service>-rfp-board-<PROJECT_NUMBER>.<region>.run.app/demo` (or append `?domain=insurance` to the banking console URL above). Notice the **Live Schema Evolution & Bi-Temporal Ontology Graph (`terrepower-coastal-2027` → `PLACEMENT-HARBORVIEW-DAY2`)** stage directly below the Carrier Broker Collaboration Engine in Scene 1.
   - Initially, 7 baseline OKF doctype bundles are active (`ont-rev-v1-1-baseline`, `v1.1.0`), and all 3 baseline TerrePower documents (`$142.5M` TIV, `$418,500.00` Lead Quote, `$50,000.00` base wind deductible) are reconciled.
2. **Beat 1 — Click `1. Upload 1-Page Wind Endorsement (Expose Sparsity)`:**
   - Chubb underwriter uploads `Chubb_Wind_Deductible_Endorsement_ScheduleB.pdf` (`$100,000.00` coastal windstorm deductible).
   - Because `ont-rev-v1-1-baseline` lacks a dedicated endorsement entity, the 1-page schedule is forced into `commercial-property-quote` (`Prio 20`), leaving `4/5` core quote fields `NULL` (**Bimodal Jaccard $J(A,B) = 0.0667 \le 0.10$**) and causing a **`Prio 20 == Prio 20` tie** (`$50,000.00` vs. `$100,000.00`).
3. **Beat 2 — Click `2. Run Taxonomy Assay & 3-Tier Gate`:**
   - `SchemaRefineryEngine` runs the Bimodal Null Split Detector and compiles candidate `wind-deductible-endorsement.okf.md` (`Prio 40`), 3 Knowledge Graph edges (`[:SUPERSEDES]`, `[:SPLIT_FROM]`, `[:FEEDS_METRIC]`), and contrastive field descriptions (`what_it_is`, `where_to_read`, `distinguish_from`).
   - The **3-Tier Counterfactual Assay Gate** verifies **$\text{NFR} = 0.0\%$ (`0` regressions)**, Krippendorff's $\alpha = 0.94 \ge 0.80$, Gwet's $\text{AC}_2 = 0.93 \ge 0.80$, and Rogan-Gladen $\hat{\theta} = 98.8\%$.
4. **Beat 3 — Click `3. Promote Ontology v1.2.0 & Reconcile $100k`:**
   - Atomically promotes `ont-rev-v1-2-insurance` (`v1.2.0`, 8 doctypes) to `ACTIVE`, closes `ont-rev-v1-1-baseline` `effective_to`, and re-evaluates Zone 3 `consolidation_rules.py` via `[:SUPERSEDES]` (`Prio 40 > Prio 20`).
   - All `4/4` fields reconcile deterministically (`$100,000.00` wind deductible wins cleanly, `done=True`) while Turn-1 historical queries remain reproducible via `[:SPLIT_FROM]` lineage.
5. **Beat 4 — Click `4. Verify Day-2 Payoff (Next Placement)`:**
   - Re-ingests a new 1-page coastal wind endorsement on the next placement (`PLACEMENT-HARBORVIEW-DAY2` — `Harborview Marine Terminal LLC`, `$75,000.00` wind deductible).
   - `gemini-3.8-flash` classifies it directly as `wind-deductible-endorsement` (`Prio 40`) in `138 ms` with **`0` false-positive missing-field quarantine parks**, **`0` human touchpoints**, and **`0` `gemini-4-argon` escalation tokens**.
6. *(Optional Deep-Dive)* Switch to **`4. Agent Gym (Learning & Trust)`** (`#refinery-card`) to inspect or toggle between **Insurance (`commercial-property-quote` → `wind-deductible-endorsement`)** and **Banking/Mortgage (`trid-closing-disclosure` → `fee-schedule-addendum`)** taxonomy assays.
