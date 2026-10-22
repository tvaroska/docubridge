# Financial Services & Regulated Industry Showcase Guide

**DocuBridge** ships with three pre-seeded, multi-document enterprise workflows spanning **Institutional Banking (KYC/AML)**, **Residential Mortgage Origination (TRID / Fannie Mae)**, and **Commercial & Employee Benefits Insurance (RFP Placement)**.

Each domain exercises the full 6-zone architecture:
1. **Zone 1 — A2A v0.3 Edge & Intake:** Multi-channel document ingestion (A2A wire turns, provider upload portal, and correlated MIME email ingress).
2. **Zone 2 — Hybrid Extraction & Disposition Gate:** Document AI layout pre-pass + `gemini-3.8-flash` structured extraction with bounding-box provenance and deterministic rule checks.
3. **Zone 3 — Dual-Write Audit Vault & Operational Fact Store:** WORM SHA-256 evidence locking (`bridge/vault.py`) paired with relational entity and fact normalization (`bridge/operational_store.py`).
4. **Zone 4 — Enterprise Knowledge Catalog & W3C PROV-O Lineage:** Business glossary mapping, sensitivity tagging (`CONFIDENTIAL`, `RESTRICTED_PII`), and end-to-end lineage graphs (`bridge/knowledge_catalog.py`).
5. **Zone 5 — Declarative A2UI v0.9 Action Inbox:** Role-aware human-in-the-loop review surfaces with compound SPIFFE + human authority enforcement and cryptographic `HMAC-SHA256` resolution signatures (`bridge/a2ui_inbox.py`).
6. **Zone 6 — Agent Gym & Closed-Loop Skill Evolution:** Golden and adversarial stress testing (`gemini-4-argon` judge), candidate skill mutation, and shadow-traffic promotion (`bridge/agent_gym/`).

---

## 1. Institutional Banking — Corporate KYC & Beneficial Ownership (`banking`)

### Regulatory Context
- **Frameworks:** FinCEN CDD Rule (31 CFR 1010.230), FATF Recommendation 24 (Beneficial Ownership Transparency), and BSA/AML Customer Due Diligence.
- **Collection Process:** Multi-slot corroboration across Government ID (`gov-id`), Proof of Residential Address (`address-proof`), and Corporate Utility / Operating Address (`utility-bill`).

### Seeded Showcase Entities
| Entity ID | Legal Name | Jurisdiction | Scenario Highlights |
| :--- | :--- | :--- | :--- |
| `ent-bank-apex-001` | **Apex Global Trade Ltd.** | `US-DE` | Cross-document beneficial ownership verification (`28.5%` UBO stake), LEI validation, and address corroboration across passport and utility statement. |
| `ent-bank-meridian-002` | **Meridian Treasury Services Corp.** | `GB-LON` | High-value institutional treasury onboarding requiring `kyc_analyst` / `compliance_officer` sign-off in the A2UI Action Inbox. |

### Key Canonical Facts & Glossary Terms
- `ubo_ownership_pct` (`gls-bank-ubo`): Ultimate Beneficial Owner percentage stake (`>= 25%` regulatory threshold).
- `legal_entity_identifier` (`gls-bank-lei`): 20-character ISO 17442 LEI code.
- `kyc_risk_rating` (`gls-bank-risk`): Computed AML risk tier (`LOW`, `MEDIUM`, `HIGH`).

---

## 2. Residential Mortgage — TRID Closing & Underwriting (`mortgage`)

### Regulatory Context
- **Frameworks:** CFPB TILA-RESPA Integrated Disclosure (TRID / Regulation Z) and Fannie Mae Selling Guide B3-3.1 (Income & Asset Assessment).
- **Collection Process:** Corroborates borrower identity, occupancy address, debt-to-income (DTI) ratio, and Loan Estimate vs. Closing Disclosure fee tolerances.

### Seeded Showcase Entities
| Entity ID | Borrower / File Name | Jurisdiction | Scenario Highlights |
| :--- | :--- | :--- | :--- |
| `ent-mort-harborview-001` | **Harborview Estates 2026-A (Elena Rostova)** | `US-CA` | Conforming residential mortgage file with DTI check (`36.4%`), appraisal LTV corroboration (`78.0%`), and TRID zero-tolerance fee verification. |

### Key Canonical Facts & Glossary Terms
- `back_end_dti_pct` (`gls-mort-dti`): Total monthly debt obligation divided by gross monthly income.
- `loan_to_value_pct` (`gls-mort-ltv`): Loan amount relative to appraised property value.
- `trid_apr_variance_bps` (`gls-mort-trid`): Basis-point delta between disclosed APR and recalculated actuarial APR.

---

## 3. Commercial & Benefits Insurance — Multi-Carrier RFP Placement (`insurance`)

### Regulatory Context
- **Frameworks:** NAIC Model Laws, ERISA Plan Sponsor Fiduciary Disclosure, and State Department of Insurance (DOI) rate/schedule filing standards.
- **Collection Process:** Solicits, normalizes, and compares multi-line carrier quotes (`benefits-rfp`, `benefits-quote`, `benefits-procurement`) across Life, AD&D, LTD, Dental, and Vision lines.

### Seeded Showcase Entities
| Entity ID | Account / Placement Name | Jurisdiction | Scenario Highlights |
| :--- | :--- | :--- | :--- |
| `ent-ins-northstar-001` | **Northstar Manufacturing Group RFP** | `US-IL` | Multi-carrier benefits placement comparing Carrier A (ADK agent) and Carrier B (LangGraph agent) rate tables, guarantee windows, and plan design deviations. |

### Key Canonical Facts & Glossary Terms
- `total_annual_premium_usd` (`gls-ins-premium`): Aggregated annual premium across quoted coverage lines.
- `rate_guarantee_months` (`gls-ins-guarantee`): Duration of locked carrier rate guarantee (`12`, `24`, or `36` months).
- `commission_schedule_pct` (`gls-ins-commission`): Disclosed broker commission percentage.

---

## Running the Interactive 3-Domain Showcase

```bash
# Start the unified demo server locally
.venv/bin/uvicorn bridge.dashboard.board:build_board_app --factory --port 8080

# Open the interactive 6-Zone Enterprise Console
open http://localhost:8080/demo
```

### API Endpoints for Domain Exploration
- `GET /api/v1/catalog/summary` — Inspect registered SPIFFE agents, business glossary terms, and catalog assets across `banking`, `mortgage`, and `insurance`.
- `GET /api/v1/operational/entities?domain=banking` — Query canonical entities, submissions, and extracted facts by industry domain.
- `GET /api/v1/inbox/actions?domain=banking` — Retrieve pending human-in-the-loop A2UI v0.9 action items and cryptographic audit records.
- `GET /api/v1/gym/overview` — View golden evaluation scores, adversarial stress-test results, and skill evolution candidates across all domains.
