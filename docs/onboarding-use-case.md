# Onboarding a New Use-Case Agent & Creating Schemas from Sample Documents

DocuBridge separates the **core document-exchange engine** (`bridge/`) from **domain-specific use-case agents** (`agents/<name>/`).

By default, a fresh DocuBridge deployment starts **empty**:
- Terraform sets `seed_skills = false` and `empty_catalog_on_start = true` (`BRIDGE_EMPTY_CATALOG_ON_START=1`).
- `gs://<project>-bridge-skills` starts with zero skill objects.
- Zone 6 `KnowledgeCatalog` starts at ontology revision `ont-rev-v0-empty` (`0` doctype entities, `0` ontology edges).
- `GET /.well-known/agent-card.json` returns `skills: []`.

Each use-case agent brings its own **sample documents** and **onboarding manifest**, induces or registers its doctype schemas (`.okf.md` + `SKILL.md` + `assets/schema.json` + `assets/validation.yaml`), and installs its process skill onto DocuBridge.

---

## 1. End-to-End Architecture

```
Use-Case Agent Repo / Folder (e.g. agents/address/)
 ├── onboarding/
 │    ├── manifest.yaml          <-- Process skill + doctypes + sample_files + validation rules
 │    └── samples/               <-- 1-5 sample documents (.pdf, .svg, .docx, .xlsx, .json)
 │
 ▼  scripts/create-schema-from-docs.py  OR  scripts/onboard-use-case.py
┌──────────────────────────────────────────────────────────────────────────┐
│ DocuBridge Zone 2 Schema Refinery + Zone 6 Knowledge Catalog             │
│  1. Extract text/fields from sample documents                            │
│  2. Induce contrastive OKF bundle (.okf.md) with Where-to-read &         │
│     Distinguish-from clauses + LINKS_TO / SUPERSEDES spine edges         │
│  3. Run 3-Tier Schema Assay Gate (structural + extraction + gold recall) │
│  4. Promote OntologyRevision to KnowledgeCatalog (BigQuery / local)      │
│  5. Install doctype + process skills to SkillRegistry (GCS / local)      │
└──────────────────────────────────────────────────────────────────────────┘
 ▼
GET /.well-known/agent-card.json  -->  advertises the new process skill over A2A
```

---

## 2. Helper Script 1: Create a Doctype Schema from Sample Documents

Use [`scripts/create-schema-from-docs.py`](../scripts/create-schema-from-docs.py) whenever you have **1–5 sample documents** (`.pdf`, `.svg`, `.docx`, `.xlsx`, `.json`, `.yaml`, or `.txt`) and want to generate:
1. `catalog/doctypes/<doctype>.okf.md` — the canonical OKF ontology bundle with contrastive `Where to read:` and `Distinguish from:` field guidance.
2. `skills/<doctype>/SKILL.md` — the doctype skill manifest.
3. `skills/<doctype>/assets/schema.json` — the JSON Schema consumed by `gemini-3.8-flash` structured extraction.
4. `skills/<doctype>/assets/validation.yaml` — field-level validation rules and disposition signal ownership.

### Generate Schema Files on Disk

```bash
uv run python scripts/create-schema-from-docs.py \
    --doctype utility-bill \
    --doc wiki/evals/address/images/bill-powerco-clean.svg \
    --doc wiki/evals/address/images/bill-aquautil-clean.svg \
    --spine spine.party \
    --domain kyc \
    --write-dir /tmp/my-use-case
```

### Generate and Immediately Install into Active Registry + Catalog

Add `--install` to promote the induced `OntologyEntitySpec` into Zone 6 `KnowledgeCatalog` and install the doctype skill into `SkillRegistry` (local directory or GCS bucket depending on `BRIDGE_PORTS`):

```bash
uv run python scripts/create-schema-from-docs.py \
    --doctype gov-id \
    --doc wiki/evals/address/images/gov-id-clean.svg \
    --spine spine.party \
    --domain kyc \
    --install
```

Add `--live-llm` to run contrastive field-description synthesis through live `gemini-4-argon` instead of the deterministic offline synthesizer.

---

## 3. Helper Script 2: Onboard a Complete Use-Case Agent

Each use-case agent in the companion `docubridge-examples` repository carries an onboarding manifest under `agents/<agent>/onboarding/manifest.yaml`:
- **Address Proof Service Agent:** `agents/address/onboarding/manifest.yaml` (in `docubridge-examples`) — induces `gov-id` and `utility-bill` from SVG samples and installs the `address-proof` (`collect`) process skill.
- **Benefits Procurement Broker Agent:** `agents/benefits/onboarding/manifest.yaml` (in `docubridge-examples`) — induces `benefits-quote` (including `rates` and `design` tables) from `agents/benefits/onboarding/samples/benefits_quote_sample.json` and installs the `benefits-procurement` (`negotiate`) process skill.

### Manifest Structure (`manifest.yaml`)

```yaml
process_name: address-proof
process_description: Collect proof of address for a party.
pattern: collect          # collect | negotiate
hitl: park
tags: [address, collect, kyc]
context_set: enterprise-document-intake
domain: kyc
satisfaction: >-
  Satisfied by one accepted government-issued ID, OR two accepted utility bills
  from two different companies (distinct issuers).
policy:
  disposition:
    auto_approve_at: 0.85
    resubmit_below: 0.55
    max_resubmissions: 3
doctypes:
  - entity_id: gov-id
    spine_entity_id: spine.party
    source_priority: 10
    supersedes_spine: true
    sample_files:
      - ../../../images/gov-id-clean.svg
    paired_target_examples:
      - full_name: JANE M. DOE
        address: 742 EVERGREEN TERRACE, SPRINGFIELD
        id_number: S998-4421-8802
        expiry: "2029-08-15"
    validation:
      rules:
        - id: not_expired
          field: key_fields.expiry
          check: date_in_future
        - id: complete_fields
          check: all_required_present
```

### Run Onboarding Against a Local or GCP DocuBridge Instance

```bash
# In-process (writes to local SkillRegistry/KnowledgeCatalog or GCP when BRIDGE_PORTS=gcp):
uv run python scripts/onboard-use-case.py \
    --manifest agents/address/onboarding/manifest.yaml

# Remote HTTP onboarding against a deployed Cloud Run DocuBridge service:
uv run python scripts/onboard-use-case.py \
    --manifest agents/address/onboarding/manifest.yaml \
    --bridge-url https://bridge-<project-number>.<region>.run.app
```

---

## 4. REST API Endpoint (`POST /v1/refinery/onboard-use-case`)

You can also onboard a use-case agent directly via the Zone 5/2 Refinery HTTP API:

- **`POST /v1/refinery/onboard-use-case`** — accepts a `UseCaseOnboardingManifest` JSON payload, induces all doctypes through the 3-Tier Assay Gate, promotes them to Zone 6 `KnowledgeCatalog`, installs all doctype skills + the process skill into `SkillRegistry`, and returns:
  ```json
  {
    "process_name": "address-proof",
    "installed_doctypes": ["gov-id", "utility-bill"],
    "installed_skills": ["gov-id", "utility-bill", "address-proof"],
    "active_revision_id": "ont-rev-v0.0.2-utility-bill",
    "agent_card_skill_ids": ["address-proof"],
    "doctypes": [...]
  }
  ```
- Immediately after `POST /v1/refinery/onboard-use-case` completes, `GET /.well-known/agent-card.json` advertises `address-proof` with tags `["doctype:gov-id", "doctype:utility-bill"]`, ready for the use-case agent to consume over A2A (see [`docs/consume-over-a2a.md`](consume-over-a2a.md)).
