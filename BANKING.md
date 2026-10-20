# DocuBridge Neutralization & Banking Use Case Specification (`BANKING.md`)

**Status:** Ready for Implementation  
**Target Repository:** `/usr/local/google/home/tvaroska/projects/docubridge`  
**Baseline Commit:** `v1.8.0-agent-engine-a2a` (`e39e4e4`)  
**Primary Goal:** Transition `docubridge` from its Aon-branded visual theme and insurance-only defaults to a **vendor-neutral Enterprise / Google Cloud Blue-Slate design**, and add an interactive **Commercial & Mortgage Loan Underwriting (Banking)** use case alongside the existing **Commercial Insurance Placement** use case—with full **Observability, Tracing, Governance, and Evaluation (Agent Gym)** test coverage.

---

## 1. Architectural Objectives & Guardrails

1. **Vendor & Customer Neutrality:**
   - Remove all Aon-specific visual branding (`--aon-red: #e11b22`, `--aon-navy: #1b1c27`, `AON` header wordmark, `Aon Document Bridge`, `Aon Provider Portal`) and replace with a neutral **Enterprise / Google Cloud Blue-Slate** palette (`#0f172a` slate header, `#2563eb` / `#1a73e8` primary blue accent, `DOCUBRIDGE` wordmark).
   - Replace Aon-specific default URIs and trust domains (`agents.global.aon.net`, `https://aon.com/a2ui/...`, `gs://aon-docubridge-audit-vault`, `alloydb://aon-enterprise-data/...`, `aon_lakehouse.bq_policy_facts_iceberg`, `@aon.com`) with neutral `docubridge.io` / `docubridge-audit-vault` / `docubridge_lakehouse` identifiers.
   - Keep all demo entities 100% synthetic (`Meridian Logistics Center LLC` for Banking; `Terrepower Coastal LLC` for Insurance). Never include real customer names, logos, or PII/MNPI.
2. **Dual-Domain Interactive Experience (Banking Default + Insurance Toggle):**
   - Make **Banking: Commercial & Mortgage Loan Underwriting** the default scenario in the 4-stage interactive walkthrough console (`demo/enterprise-demo.html`, served live at `GET /demo` and `GET /console`), with a **1-click domain toggle** in the top navigation bar:
     - `🏦 Banking · Loan Underwriting` *(Default)*
     - `🛡️ Insurance · Commercial Placement`
   - Switching the toggle dynamically updates all 4 stages (Dual-Path Intake & Vault, Consolidation & Bi-Temporal Views, SPIFFE OBO & A2UI v0.9 Inbox, and Agent Gym & Programmable Trust Dial) and executes live API calls against the FastAPI backend.
3. **Zero Regression on Core Isolation & Conformance Guards:**
   - **CRITICAL (`tests/test_isolation_guard.py`):**
     - `bridge/` must NEVER import `agents/`, and `agents/` must NEVER import `bridge/`.
     - `test_core_names_no_skill` scans every Python file in `bridge/` (outside `bridge/demos/`) and fails if any non-docstring string literal equals an installed folder name in `skills/`. Do **not** hardcode `skills/` folder names inside `bridge/` core modules.
   - All existing unit, conformance, and frontend tests (`uv run pytest`, `uv run ruff check`, `pnpm test`) must pass with zero warnings or regressions.

---

## 2. File-by-File Implementation Specification

### 2.1 Backend Identity & SPIFFE Registry (`bridge/identity.py`)

Update default constants and registered SPIFFE agent policies (lines 51–185, 239–302):

1. **Trust Domain, Signing Secret & Audience:**
   - `DEFAULT_SPIFFE_TRUST_DOMAIN = "agents.docubridge.io"` (was `"agents.global.aon.net"`)
   - `DEFAULT_DEV_SVID_SECRET = "docubridge-spiffe-svid-signing-key-v1"` (was `"aon-docubridge-spiffe-svid-signing-key-v1"`)
   - In `mint_jwt_svid()` and `verify_jwt_svid()`, set default `audience: str = "docubridge"` and `expected_audience: str | None = "docubridge"` (was `"aon-docubridge"`).
2. **`DEFAULT_SPIFFE_AGENT_POLICIES`:**
   - Replace the `spiffe://agents.global.aon.net/...` keys and `"Aon ..."` display names with neutral `spiffe://agents.docubridge.io/...` policies supporting both Banking and Insurance workflows:
     - `spiffe://agents.docubridge.io/internal/credit-officer-agent` — `"Internal Credit & Loan Underwriting Agent"` (`security_zone="internal"`, `allowed_channels=("gemini_app",)`, `allowed_roles=("underwriter", "broker", "executive")`, `allowed_views=("v_underwriter_exposure", "v_broker_placement", "v_claims_coverage")`, `allow_vault_promotion=True`)
     - `spiffe://agents.docubridge.io/internal/broker-agent` — `"Internal Commercial Placement & Deal Desk Agent"` (`security_zone="internal"`, `allowed_channels=("gemini_app",)`, `allowed_roles=("broker", "executive")`, `allowed_views=("v_broker_placement", "v_claims_coverage", "v_underwriter_exposure")`, `allow_vault_promotion=True`)
     - `spiffe://agents.docubridge.io/internal/claims-agent` — `"Internal Covenant & Claims Review Agent"` (`security_zone="internal"`, `allowed_channels=("gemini_app",)`, `allowed_roles=("claims", "broker", "executive")`, `allowed_views=("v_claims_coverage", "v_broker_placement")`, `allow_vault_promotion=False`)
     - `spiffe://agents.docubridge.io/internal/underwriter-agent` — `"Internal Technical Underwriting & Risk Agent"` (`security_zone="internal"`, `allowed_channels=("gemini_app",)`, `allowed_roles=("underwriter", "broker", "executive")`, `allowed_views=("v_underwriter_exposure", "v_broker_placement", "v_claims_coverage")`, `allow_vault_promotion=True`)
     - `spiffe://agents.docubridge.io/portal/external-portal-agent` — `"External Client, Borrower & Partner Portal Agent"` (`security_zone="external"`, `allowed_channels=("custom_web",)`, `allowed_roles=("client", "carrier")`, `allowed_views=("v_broker_placement", "v_claims_coverage")`, `allow_vault_promotion=False`)
     - `spiffe://agents.docubridge.io/ingest/docubridge-collector` — `"DocuBridge Intake & Consolidation Collector"` (`security_zone="internal"`, `allowed_channels=("gemini_app",)`, `allowed_roles=("broker", "claims", "underwriter", "executive")`, `allowed_views=("v_broker_placement", "v_claims_coverage", "v_underwriter_exposure")`, `allow_vault_promotion=True`)

---

### 2.2 Governed A2UI v0.9 Catalogs (`bridge/a2ui_inbox.py`)

Update catalog URIs and schema `$id` values (lines 51–56, 90–145):

- `INBOX_CATALOG_ID = "https://docubridge.io/a2ui/v0_9/catalogs/enterprise_inbox_catalog.json"` (was `"https://aon.com/a2ui/v0_9/catalogs/enterprise_inbox_catalog.json"`)
- `PORTAL_CATALOG_ID = "https://docubridge.io/a2ui/v0_9/catalogs/portal_customer_catalog.json"` (was `"https://aon.com/a2ui/v0_9/catalogs/portal_customer_catalog.json"`)
- Update docstrings and comments to refer to "internal enterprise teams" instead of "internal Aon teams".

---

### 2.3 Zone 3 & Zone 4 Operational Store & Banking Consolidation Rules (`bridge/operational_store.py`)

1. **Expand `_MONEY_KEYS` (lines 68–81) for Banking Loan Underwriting:**
   Add canonical banking monetary fields so `consolidate_entry()` and `compute_financial_summary()` deterministically parse and sum banking amounts in fixed-point `Decimal(0.01)`:
   ```python
   _MONEY_KEYS = frozenset(
       {
           # Insurance & Benefits
           "premium",
           "deductible",
           "coverage_limit",
           "coverage_limits",
           "insured_value",
           "total_insured_value",
           "claim_amount",
           "reserve_amount",
           "sublimit",
           "net_pay",
           # Banking & Commercial Loan Underwriting
           "loan_amount",
           "principal_amount",
           "facility_limit",
           "appraised_value",
           "collateral_value",
           "origination_fee",
           "closing_fee",
           "rate_lock_fee",
           "annual_debt_service",
           "net_operating_income",
       }
   )
   ```
2. **Expand `source_priority_for_doctype()` (lines 109–126) for Banking Doctypes:**
   Support banking document authority hierarchy alongside insurance doctypes:
   - Priority `40` (Highest — Overrides base terms): `"endorsement"`, `"amendment"`, `"addendum"`, `"closing-disclosure"`, `"fee-override"`
   - Priority `30` (Executed / Bound agreements): `"policy"`, `"binder"`, `"credit-agreement"`, `"commitment-letter"`, `"loan-agreement"`, `"promissory-note"`
   - Priority `20` (Quotes, Appraisals & Term Sheets): `"quote"`, `"cert"`, `"term-sheet"`, `"appraisal"`, `"rent-roll"`
   - Priority `10` (Applications, Statements & Default): `"application"`, `"bill"`, `"statement"`, default
3. **Neutralize BigQuery Open Iceberg Table Name (lines 307, 330, 370–389):**
   - Change `"aon_lakehouse.bq_policy_facts_iceberg"` $\rightarrow$ `"docubridge_lakehouse.bq_operational_facts_iceberg"` on `BigQuerySyncRecord.iceberg_table`, `BigQuerySyncBatch.iceberg_table`, and `BIGQUERY_ICEBERG_DDL`.
4. **Financial Summary Mapping (`compute_financial_summary`, lines 556–593):**
   - Allow banking equivalents to roll up into `FinancialSummary` when insurance fields are absent (or alongside them):
     - `origination_fee` / `closing_fee` / `rate_lock_fee` / `annual_debt_service` $\rightarrow$ rolls up into `total_premium` (or `total_deductible` for fee/retention exposure: map `origination_fee` / `closing_fee` / `rate_lock_fee` into `total_deductible` and `annual_debt_service` into `total_premium`, `loan_amount` / `principal_amount` / `facility_limit` into `total_coverage_limit`, and `appraised_value` / `collateral_value` into `total_insured_value`).
     - This ensures `UnderwriterView` and `BrokerView` automatically compute deterministic `Decimal` ratios for banking submissions without breaking any existing schema fields!

---

### 2.4 Zone 6 Knowledge Catalog (`bridge/knowledge_catalog.py`)

1. **Neutralize Physical URIs in `DEFAULT_SYSTEM_ASSETS` (lines 294–450) and `_build_trace_from_fact` (lines 750–780):**
   - `gs://aon-docubridge-audit-vault/vault/` $\rightarrow$ `gs://docubridge-audit-vault/vault/`
   - `alloydb://aon-enterprise-data/operational/bridge_operational_facts` $\rightarrow$ `alloydb://docubridge-enterprise-data/operational/bridge_operational_facts`
   - `alloydb://aon-enterprise-data/views/v_broker_placement` $\rightarrow$ `alloydb://docubridge-enterprise-data/views/v_broker_placement`
   - `alloydb://aon-enterprise-data/views/v_claims_coverage` $\rightarrow$ `alloydb://docubridge-enterprise-data/views/v_claims_coverage`
   - `alloydb://aon-enterprise-data/views/v_underwriter_exposure` $\rightarrow$ `alloydb://docubridge-enterprise-data/views/v_underwriter_exposure`
   - `bigquery://aon_lakehouse.bq_policy_facts_iceberg` $\rightarrow$ `bigquery://docubridge_lakehouse.bq_operational_facts_iceberg`
   - `alloydb://aon-enterprise-data/gym/...` $\rightarrow$ `alloydb://docubridge-enterprise-data/gym/...`
2. **Add Banking Business Glossary Terms to `DEFAULT_GLOSSARY_TERMS` (lines 219–292):**
   Keep the 4 existing terms and append 2 banking underwriting terms:
   - `term.loan_to_value_ratio` (`"Commercial Loan-to-Value (LTV) & Fee Exposure"`, `domain="underwriting"`, `canonical_fields=("loan_amount", "appraised_value", "origination_fee")`, `calculation_formula="(loan_amount / appraised_value) * 100"`)
   - `term.addendum_priority_hierarchy` (`"Credit Addendum & Closing Disclosure Authority"`, `domain="governance"`, `canonical_fields=("origination_fee", "loan_amount")`, `calculation_formula="Signed Addendum / Closing Disclosure (40) > Credit Agreement (30) > Term Sheet / Appraisal (20) > Application (10)"`)

---

### 2.5 Zone 6 Agent Gym & Programmable Trust Policies (`bridge/agent_gym.py`)

Update `DEFAULT_TRUST_POLICIES` (lines 269–310) to use `@docubridge.io` principals and include **both** the 3 Banking slices and the 3 Insurance slices so both modes in the interactive UI are backed by live `/v1/gym/trust-policies` state:

| Slice ID | Domain | `issuer_pattern` | `doctype` | `risk_level` | Initial `autonomy_level` | `consecutive_matches` | `last_updated_by` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `slice:closing-disclosure:fee-schedule:LOW` | Banking | `Meridian*` | `closing-disclosure` | `LOW` | `trusted_auto_approve` | `14 / 5` | `senior.credit.officer@docubridge.io` |
| `slice:credit-agreement:addendum:MEDIUM` | Banking | `Syndicated*` | `addendum` | `MEDIUM` | `guided_review` | `4 / 5` | `senior.credit.officer@docubridge.io` |
| `slice:appraisal-report:commercial:HIGH` | Banking | `CBRE*` | `appraisal` | `HIGH` | `shadow_mode` | `2 / 5` | `system:bootstrap` |
| `slice:chubb:quote:LOW` | Insurance | `Chubb*` | `quote` | `LOW` | `trusted_auto_approve` | `12 / 5` | `lead.broker@docubridge.io` |
| `slice:aig:endorsement:MEDIUM` | Insurance | `AIG*` | `endorsement` | `MEDIUM` | `guided_review` | `4 / 5` | `lead.underwriter@docubridge.io` |
| `slice:lloyds:quote:HIGH` | Insurance | `Lloyd's*` | `quote` | `HIGH` | `shadow_mode` | `2 / 5` | `system:bootstrap` |

> **Note:** Both `slice:credit-agreement:addendum:MEDIUM` (Banking) and `slice:aig:endorsement:MEDIUM` (Insurance) start at `4 / 5` consecutive matches in `guided_review`. During an interactive session or automated test, recording the 5th shadow match (`POST /v1/gym/trust-policies/{slice_id}/shadow`) makes `ready_for_promotion=True` and unlocks live promotion to `trusted_auto_approve` (`POST /v1/gym/trust-policies/{slice_id}/dial`).

---

### 2.6 Zone 5 User Interaction & Live Console Route (`bridge/user_interaction.py`, `bridge/dashboard/server.py`, `Dockerfile`)

1. **`bridge/user_interaction.py` (line 250–256):**
   - In `resolve_caller_role()`, support `"credit"` / `"officer"` mapping to `"underwriter"`, and recognize `@docubridge.io` (while keeping `@aon.com` as a backward-compatible fallback):
     ```python
     if verified_role is None and iap_email:
         lowered = iap_email.lower()
         if "credit" in lowered:
             verified_role = "underwriter"
         else:
             for r in ("underwriter", "claims", "broker", "carrier", "client"):
                 if r in lowered:
                     verified_role = cast(CatalogRole, r)
                     break
         if verified_role is None and lowered.endswith(("@docubridge.io", "@aon.com")):
             verified_role = "broker"
     ```
2. **Serve the Interactive Console (`GET /demo` and `GET /console`) directly from FastAPI:**
   - Mount `GET /demo` and `GET /console` (returning `HTMLResponse` with `demo/enterprise-demo.html`) in `build_user_interaction_router()` (`bridge/user_interaction.py`), which is automatically included by both `build_dashboard_app()` (`bridge/dashboard/server.py`) and `build_user_interaction_app()`.
   - In `Dockerfile`, add `COPY demo/ ./demo/` alongside `COPY contract/ bridge/ skills/ wiki/` so Cloud Run services (`documents001-bridge-rfp-board`) can serve `/demo` and `/console` directly in the browser.

---

### 2.7 React Ops Dashboard Theme (`frontend/ops-dashboard/src/theme.ts`)

- In `frontend/ops-dashboard/src/theme.ts` (line 10) and `frontend/shared/src/theme.ts` (comment on line 5):
  - Change `export const theme = makeTheme("#d93025");` $\rightarrow$ `export const theme = makeTheme("#1a73e8");` (Google Cloud Blue, replacing the red accent).

---

### 2.8 Interactive 4-Stage Console Redesign & Domain Toggle (`demo/enterprise-demo.html` & `demo/record_gcp_demo.py`)

1. **Neutral Enterprise / Google Cloud Blue-Slate Theme (`demo/enterprise-demo.html`):**
   - Replace CSS variables:
     - `--brand-primary: #1a73e8` (Google Cloud Blue)
     - `--brand-primary-dark: #1557b0`
     - `--brand-slate: #0f172a` (Deep Slate Header)
     - `--brand-bg: #f8fafc`
   - Replace `<span class="aon-wordmark">AON</span>` with `<span class="brand-wordmark">DOCUBRIDGE</span>` and subtitle `Enterprise Data Architecture · Observability, Governance & Agent Gym`.
2. **Interactive Domain Switcher (`Banking · Loan Underwriting` $\leftrightarrow$ `Insurance · Commercial Placement`):**
   - Add a prominent domain toggle in the top header (`window.switchDomain('banking' | 'insurance')`) that defaults to `'banking'`.
   - **Banking Scenario (`Meridian Logistics Center LLC — $45,000,000 Commercial Real Estate Term Loan`):**
     - **Scene 1 (External Borrower/Lender Portal & Internal Dual-Path Intake):**
       - *Path A (Structured Excel, 0 AI Tokens):* `meridian-rent-roll-dscr-2026.xlsx` (38 commercial tenants, Net Operating Income `$4,185,000.00`, DSCR `1.42x`, 2 guarantor SSNs masked at edge `[REDACTED-SSN]`, locked in 7-year WORM Audit Vault `gs://docubridge-audit-vault/...`).
       - *Path B Card 1 (Quarantined PDF):* `syndicated-credit-agreement-meridian-2026.pdf` — Cover Term Sheet states `$50,000` Origination & Rate-Lock Fee (`Page 1 · Region [0.14, 0.31]`), whereas Schedule B Fee & Covenant Addendum specifies `$100,000` (`Page 4 · Region [0.12, 0.44]`). Parked by the **4-Signal Disposition Gate** in Review Quarantine (`awaiting_human_review`).
       - *Path B Card 2 (Accepted PDF):* `cbre-mai-commercial-appraisal-2026.pdf` — Multi-window table merge across pages 1–24, Appraised Collateral Value `$62,500,000.00` (`Page 11`), Implied LTV `72.00%`, auto-approved (`Confidence 99%`).
       - *Zone 6 Asset Map Table:* Dynamically populated from live `GET /v1/catalog/assets`.
     - **Scene 2 (Consolidation Rules Engine, Bi-Temporal Store & 6-Hop Lineage):**
       - Role-Based Views switcher: **Credit Officer View (Deal & Conflicts)**, **Underwriter View (LTV, DSCR & Fee Exposure)**, **Covenant / Audit View (Effective-Date Terms)**.
       - Shows deterministic `Decimal` reconciliation: Schedule B Fee Addendum (`$100,000.00`, Priority 40) supersedes Cover Term Sheet (`$50,000.00`, Priority 20) while flagging the cross-document conflict (`FactConflict`).
       - Shows Borrower Entity Resolution: Agent Gym rule normalizes `"Meridian Logistics Holdings Ltd."` $\rightarrow$ `"Meridian Logistics"` while keeping `"Meridian SPV II"` distinct.
       - Right column displays the **6-Hop End-to-End Lineage Trace** (`GET /v1/catalog/lineage/...`) and **BigQuery Open Iceberg Watermark Sync** (`docubridge_lakehouse.bq_operational_facts_iceberg`).
     - **Scene 3 (SPIFFE Workload Identity, OBO Authority Gate & A2UI v0.9 Action Inbox):**
       - Interactive persona switcher between:
         1. `Junior Loan Officer (Max Authority $25k)` (`obo.sub = "junior.officer@docubridge.io"`, `max_approval_amount = "25000"`): Clicking **Approve** triggers a live or simulated **HTTP 403 / 422 Authority Refusal Banner** because the `$100,000` Schedule B fee override exceeds the officer's `$25,000` authority limit.
         2. `Senior Credit Officer (SPIFFE OBO · $25M Authority)` (`act.sub = "spiffe://agents.docubridge.io/internal/credit-officer-agent"`, `obo.sub = "senior.credit.officer@docubridge.io"`): Clicking **✓ Edit & Approve** resolves the A2UI v0.9 Action Card, emits the RFC 6902 JSON Patch `[{"op": "replace", "path": "/origination_fee_usd", "value": "$100,000"}]`, and advances the 4-step progress tracker to `100% Completed`.
     - **Scene 4 (Agent Gym Continuous Learning & Programmable Trust Dial):**
       - Interactive buttons wired to real `fetch()` calls against the backend (`window.location.origin` when served from FastAPI, with automatic fallback if opened via `file://`):
         1. **`1. Distill "Example of One" Signal`** $\rightarrow$ `POST /v1/gym/signals/from-quarantine` (distills the single `$50,000` $\rightarrow$ `$100,000` Schedule B addendum correction into a `CANDIDATE` `TribalKnowledgeRule`).
         2. **`2. Run Stress Suite & Report Card Gate`** $\rightarrow$ `POST /v1/gym/rules/{rule_id}/evaluate` (runs the 4 synthetic perturbation stress cases + 12 historical benchmarks + Decimal math firewall check, updating the Report Card UI to `100.0%` and status `PROMOTED`).
         3. **`3. Promote Credit Addendum Slice to Trusted Auto-Approve`** $\rightarrow$ `POST /v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/shadow` (records 5th consecutive match) followed by `POST /v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/dial` (promotes slice to `3. Trusted Auto-Approve ✓` and refreshes the table).
         4. **`4. Simulate Format Drift (Safety Dial-Back)`** $\rightarrow$ `POST /v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/shadow` with `ai_value != human_value` (demonstrates **Instant Safety Dial-Back** demoting the slice from `Trusted Auto-Approve` back to `Guided Human Review` with `0/5` streak and `1 drift incident`!).

---

## 3. Observability, Tracing, Governance & Evaluation Test Specification

All changes must be verified by automated tests in `tests/test_enterprise_data_architecture.py` (plus existing test suites) and interactive verification.

### 3.1 Automated Test Suite Requirements (`tests/test_enterprise_data_architecture.py`)

1. **Update Existing Assertions for Neutral URIs:**
   - Update `spiffe://agents.global.aon.net/...` $\rightarrow$ `spiffe://agents.docubridge.io/...`
   - Update `gs://aon-docubridge-audit-vault/...` $\rightarrow$ `gs://docubridge-audit-vault/...`
   - Update `aon_lakehouse.bq_policy_facts_iceberg` $\rightarrow$ `docubridge_lakehouse.bq_operational_facts_iceberg`
   - Keep `@docubridge.io` (and verify `@aon.com` backward compatibility) in IAP / OBO principal tests.
2. **Add New Test: `test_banking_loan_underwriting_observability_lineage_and_evaluation()`:**
   Must explicitly test and assert all 4 session pillars on the **Banking Loan Underwriting** scenario (`entity_id="LOAN-MERIDIAN-001"`):
   - **Pillar 1 — Tracing, Provenance & Bi-Temporal Observability:**
     - Ingest `term-sheet` (`doc-meridian-ts-v1`, priority `20`, `origination_fee="$50,000.00"`, `loan_amount="$45,000,000.00"`, `appraised_value="$62,500,000.00"` at `t0`) and `closing-disclosure` / `addendum` (`doc-meridian-addendum-v2`, priority `40`, `origination_fee="$100,000.00"` with `SourceAnchor(page_number=4, bounding_box=(0.12, 0.44, 0.58, 0.51))` at `t1`).
     - Verify `catalog.trace_lineage("LOAN-MERIDIAN-001", "origination_fee")` returns all **6 lineage hops** (`INBOUND` $\rightarrow$ `AUDIT_VAULT` $\rightarrow$ `OPERATIONAL_STORE` $\rightarrow$ `BUSINESS_VIEW` $\rightarrow$ `BIGQUERY_ICEBERG` $\rightarrow$ `USER_INTERACTION_A2UI`), with `current_value == "$100,000.00"`, `source_anchor.page_number == 4`, and `consolidation_rule` reflecting `priority=40`.
     - Verify bi-temporal point-in-time query at `learned_as_of=t0` returns `$50,000.00` while `learned_as_of=t1` returns `$100,000.00` and flags a `FactConflict` between `doc-meridian-addendum-v2` and `doc-meridian-ts-v1`.
     - Verify watermarked BigQuery Iceberg CDC export (`store.export_bigquery_sync_batch("LOAN-MERIDIAN-001", since_recorded_at=t0)`) exports only the delta from `t1` into `docubridge_lakehouse.bq_operational_facts_iceberg`.
   - **Pillar 2 — Runtime Governance, SPIFFE OBO & A2UI v0.9 SSE Telemetry:**
     - Mint a SPIFFE JWT-SVID for `spiffe://agents.docubridge.io/internal/credit-officer-agent` acting on behalf of `usr:junior.officer@docubridge.io` with `max_approval_amount="25000.00"`.
     - Create an A2UI v0.9 Inbox action for `LOAN-MERIDIAN-001` with `approvedAmount: 100000.0` and verify that the junior officer's `edit_and_approve` call is **rejected with HTTP 422** (exceeds `$25,000` principal authority limit).
     - Mint a SPIFFE JWT-SVID for `spiffe://agents.docubridge.io/internal/credit-officer-agent` acting on behalf of `usr:senior.credit.officer@docubridge.io` (`max_approval_amount="25000000.00"`), execute `edit_and_approve`, and verify:
       - RFC 6902 patch `/transfer/approvedAmount` and `/resolution/selectedValue` is recorded in `inbox_store.list_audit_records()`.
       - Both `actor_principal == "usr:senior.credit.officer@docubridge.io"` and `agent_spiffe_id == "spiffe://agents.docubridge.io/internal/credit-officer-agent"` are logged in the immutable audit record.
       - Live SSE stream `GET /v1/a2ui/inbox/items/{item_id}/events` emits `event: a2ui.surface` and `event: a2ui.delta`.
   - **Pillar 3 — Continuous Evaluation (Agent Gym Stress Suite + Decimal Math Firewall):**
     - Post the single Schedule B addendum correction (`$50,000.00` $\rightarrow$ `$100,000.00` on `Syndicated Credit Agreement`, page 4) to `POST /v1/gym/signals/from-quarantine`.
     - Evaluate the candidate rule via `POST /v1/gym/rules/{rule_id}/evaluate` and assert:
       - `passed_gate is True`
       - `math_firewall_intact is True`
       - `stress_cases_total == 4` and `stress_cases_passed == 4`
       - `len(historical_regressions) == 0`
       - `baseline_slice_score == 0.5` $\rightarrow$ `candidate_slice_score == 1.0` (`+0.5` improvement delta)
     - Distill and evaluate a borrower entity alias rule (`"Meridian Logistics Holdings Ltd."` $\rightarrow$ `"Meridian Logistics"`, `distinct_entities=["Meridian SPV II"]`) and verify `ingest_accepted_submission()` normalizes `"Meridian Logistics Holdings Ltd."` to `"Meridian Logistics"` while preserving `"Meridian SPV II"`.
     - Verify the **Hallucination / Math Firewall Gate** blocks a rule containing `override_decimal_math` / `llm_calculate_total` (`math_firewall_intact is False`, `status == "rejected"`).
   - **Pillar 4 — Graduated Autonomy (Programmable Trust Dial & Instant Safety Dial-Back):**
     - Verify `GET /v1/gym/trust-policies` includes `slice:credit-agreement:addendum:MEDIUM` at `guided_review` with `4/5` consecutive matches.
     - Record a 5th exact match (`POST /v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/shadow` with `ai_value="$100,000.00"`, `human_value="$100,000.00"`), verify `ready_for_promotion is True`, and promote to `trusted_auto_approve` via `POST /v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/dial`.
     - Inject a format drift mismatch (`ai_value="$50,000.00"`, `human_value="$100,000.00"`) on `POST /v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/shadow` and assert **Instant Safety Dial-Back**:
       - `autonomy_level == "guided_review"`
       - `consecutive_matches == 0`
       - `drift_incidents == 1`
3. **Verify Interactive Console Endpoint (`GET /demo` and `GET /console`):**
   - Assert `client.get("/demo").status_code == 200` and `"DOCUBRIDGE"` in `client.get("/demo").text` and `"Meridian Logistics Center LLC"` in `client.get("/demo").text`.

---

## 4. How to Run & Test in Interactive Mode

### 4.1 Automated Verification Commands (Run First)

From `/usr/local/google/home/tvaroska/projects/docubridge`:

```bash
# 1. Lint & Format Check
uv run ruff check .

# 2. Architectural Isolation & Skill-Name Guard
uv run pytest tests/test_isolation_guard.py -v

# 3. 6-Zone Enterprise Data Architecture, Observability, Lineage, SPIFFE & Agent Gym Suite
uv run pytest tests/test_enterprise_data_architecture.py -v

# 4. Disposition Gate Threshold Calibration Corridor (Corpus Evaluation Check)
uv run python -m bridge.calibration

# 5. Full Backend + Frontend Test Suite
uv run pytest
pnpm --dir frontend test -- --run
```

### 4.2 Launching Interactive Mode Locally

You can run the interactive demo in two ways depending on which surfaces you want to show live:

#### Mode A: Single-Command Interactive Enterprise Console + Live APIs (Fastest for Live Demo)

Starts the Ops Dashboard + Zone 5 A2UI Inbox + Zone 6 Knowledge Catalog + Agent Gym + Interactive 4-Stage Console on port `8003`:

```bash
cd /usr/local/google/home/tvaroska/projects/docubridge
uv run uvicorn --factory bridge.dashboard.server:build_dashboard_app --host 127.0.0.1 --port 8003
```

Open in browser:
- **Interactive 4-Stage Console (Banking + Insurance Toggle):** `http://127.0.0.1:8003/demo` (or `http://127.0.0.1:8003/console`)
- **Live OpenAPI / Swagger UI for Direct API Inspection:** `http://127.0.0.1:8003/docs`

#### Mode B: Full Unified Showcase Runner (A2A Edge + Ops Dashboard + Provider Portal + React Apps)

Starts the composed ASGI host on `:8011` (`/` A2A Edge, `/ops` Ops Dashboard + `/ops/demo` Interactive Console, `/portal` Provider Portal) plus the Vite React apps on `:5173` and `:5175`:

```bash
cd /usr/local/google/home/tvaroska/projects/docubridge
bash scripts/run-showcase.sh
```

Open in browser:
- **Interactive 4-Stage Enterprise Console:** `http://localhost:8011/ops/demo`
- **A2A Agent Card (Discovery & Skills):** `http://localhost:8011/.well-known/agent-card.json`
- **Provider Portal React App (KYC Address & Document Intake):** `http://localhost:5173`
- **Ops Dashboard React App (Live SSE Queues, HITL & Escalations):** `http://localhost:5175`

---

### 4.3 Interactive Demo & Evaluation Walkthrough Script (Banking Use Case)

Once `http://127.0.0.1:8003/demo` (or the Cloud Run URL `https://documents001-bridge-rfp-board-sf6etnew5q-uc.a.run.app/demo`) is open in your browser:

1. **Header Domain Switcher Check:**
   - Verify the page loads in the neutral **Blue-Slate `DOCUBRIDGE`** theme with **`🏦 Banking · Loan Underwriting`** active by default (`Meridian Logistics Center LLC · $45M CRE Term Loan`).
   - Click **`🛡️ Insurance · Commercial Placement`** in the header to verify instant switching to the insurance scenario (`Terrepower Coastal LLC`), then click back to **`🏦 Banking · Loan Underwriting`**.
2. **Stage 1 — Dual-Path Intake, PII Redaction & 4-Signal Gate (`1. Dual-Path Intake & Vault`):**
   - Inspect **Path A (`meridian-rent-roll-dscr-2026.xlsx`)**: parsed deterministically in code (`0 AI tokens`), 2 guarantor SSNs masked at the edge (`[REDACTED-SSN]`), locked in the 7-year WORM Audit Vault.
   - Inspect **Path B (`syndicated-credit-agreement-meridian-2026.pdf`)**: Document AI extracts Page 1 Cover Term Sheet (`$50,000` Origination Fee) and Page 4 Schedule B Fee Addendum (`$100,000` Origination Fee) with bounding-box coordinates `[0.12, 0.44]`, parking the conflict in **Zone 2 Review Quarantine**.
   - Inspect the **Zone 6 Governed Asset Map** table at the bottom (populated live from `GET /v1/catalog/assets` with neutral `docubridge-audit-vault` and `docubridge_lakehouse` URIs).
3. **Stage 2 — Bi-Temporal Consolidation & 6-Hop Traceability (`2. Consolidation & Views`):**
   - Click between **Credit Officer View**, **Underwriter View (LTV & DSCR)**, and **Covenant / Audit View**.
   - Inspect the **6-Hop End-to-End Field Lineage** panel showing the unbroken trace from `syndicated-credit-agreement-meridian-2026.pdf (Page 4)` $\rightarrow$ `Document AI + Agent Gym Hint` $\rightarrow$ `WORM Audit Vault` $\rightarrow$ `Consolidation Rule (Addendum Prio 40 > Term Sheet Prio 20)` $\rightarrow$ `Bi-Temporal Operational Store` $\rightarrow$ `A2UI Citation Chip`.
4. **Stage 3 — SPIFFE Compound Identity & A2UI v0.9 Action Inbox (`3. A2UI Inbox & SPIFFE`):**
   - Select persona **`Junior Loan Officer (Max Authority $25k)`** (`obo.sub = "junior.officer@docubridge.io"`): observe the **HTTP 403 / 422 Authority Check Failed** banner blocking approval of the `$100,000` HIGH-risk fee override.
   - Switch persona to **`Senior Credit Officer (SPIFFE OBO · $25M Authority)`** (`spiffe://agents.docubridge.io/internal/credit-officer-agent`): click **`✓ Edit & Approve`** and observe the 4-step A2UI progress bar advance to `100%` and log the RFC 6902 JSON Patch `[{"op":"replace","path":"/origination_fee_usd","value":"$100,000"}]`.
5. **Stage 4 — Agent Gym Continuous Evaluation & Programmable Trust Dial (`4. Agent Gym & Trust Dial`):**
   - Click **`1. Distill "Example of One" Signal`**: triggers a live `POST /v1/gym/signals/from-quarantine` request, creating a new candidate `TribalKnowledgeRule` for the Schedule B fee addendum.
   - Click **`2. Run Stress Suite & Report Card Gate`**: triggers a live `POST /v1/gym/rules/{rule_id}/evaluate` request, executing the 4 synthetic stress cases (`Layout Shift`, `Legal Suffix`, `Entity Collision Guard`, `Decimal Math Firewall`) and 12 historical benchmarks (`+50.0%` slice accuracy gain, `0` regressions, status `PROMOTED`).
   - Click **`3. Promote Credit Addendum Slice to Trusted Auto-Approve`**: records the 5th consecutive exact match on `slice:credit-agreement:addendum:MEDIUM` and promotes it from `2. Guided Human Review` (`4/5`) to `3. Trusted Auto-Approve ✓` (`5/5`).
   - Click **`4. Simulate Format Drift (Safety Dial-Back)`**: sends a mismatched shadow comparison (`ai_value="$50,000"` vs `human_value="$100,000"`) and shows **Instant Safety Dial-Back** automatically demoting `slice:credit-agreement:addendum:MEDIUM` back to `2. Guided Human Review` (`0/5 matches`, `1 drift`).

---

## 5. Acceptance Checklist (For Post-Implementation Evaluation)

When the implementation is ready for review, we will evaluate completeness against these 8 criteria:

- [ ] **C1. Zero Aon Branding in UI & Default Metadata:** No `AON` wordmark, `#e11b22` red header, `agents.global.aon.net`, `gs://aon-docubridge-audit-vault`, or `aon_lakehouse` in default responses from `/v1/catalog/*`, `/v1/a2ui/*`, or `/v1/gym/*`.
- [ ] **C2. Interactive Console Served on FastAPI (`/demo` & `/console`):** Opening `GET /demo` or `GET /console` returns the neutral Blue-Slate 4-stage console with both Banking and Insurance modes.
- [ ] **C3. Banking Default + Live 1-Click Toggle:** Defaults to `Banking · Loan Underwriting` (`Meridian Logistics Center LLC`) and cleanly toggles all 4 stages between Banking and Insurance.
- [ ] **C4. Banking Consolidation & Lineage Math:** `_MONEY_KEYS` and `source_priority_for_doctype` in `bridge/operational_store.py` deterministically handle banking fields (`loan_amount`, `appraised_value`, `origination_fee`, `net_operating_income`) and banking doctypes (`addendum` / `closing-disclosure` `[40]` > `credit-agreement` `[30]` > `term-sheet` / `appraisal` `[20]`).
- [ ] **C5. Banking Programmable Trust Slices:** `GET /v1/gym/trust-policies` returns the 3 banking slices (`slice:closing-disclosure:fee-schedule:LOW`, `slice:credit-agreement:addendum:MEDIUM`, `slice:appraisal-report:commercial:HIGH`) alongside the 3 insurance slices.
- [ ] **C6. Interactive Agent Gym Buttons Work Live:** Clicking Distill, Evaluate, Promote, and Simulate Drift in Scene 4 executes real `POST /v1/gym/*` requests and updates the DOM from the live JSON responses.
- [ ] **C7. Isolation & Calibration Guards Green:** `uv run pytest tests/test_isolation_guard.py` and `uv run python -m bridge.calibration` exit `0`.
- [ ] **C8. Full Test & Lint Suite Green:** `uv run ruff check .`, `uv run pytest`, and `pnpm --dir frontend test -- --run` pass with 100% green results, including `test_banking_loan_underwriting_observability_lineage_and_evaluation`.
