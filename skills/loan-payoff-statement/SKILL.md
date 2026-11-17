---
name: loan-payoff-statement
description: Official lender payoff demand statement or cryptographically signed A2A payoff assertion establishing exact principal, accrued interest, per-diem accrual, good-through date, and closing settlement instructions for an existing facility scheduled for retirement.
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype banking lending payoff consolidation"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0.0"
---

Extract the facility payoff and settlement fields defined in `assets/schema.json` from the supplied lender payoff statement or structured A2A payoff assertion. Validation rules (`complete_fields`) live in `assets/validation.yaml`. Three of the four disposition signals (legibility / type_match / confidence) are Bridge-authoritative (sense A); the fourth, completeness, is the sense-B call — cross-document payoff reconciliation and facility retirement eligibility are the underwriting agent's sense-B call.
