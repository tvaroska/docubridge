---
name: cre-credit-agreement
description: A Commercial Real Estate (CRE) credit agreement, syndicated commitment letter, or Schedule B fee addendum. Extracts loan amount, origination fee, net operating income, currency, and effective date with source page anchors.
license: Proprietary. LICENSE.txt has complete terms
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype banking cre lending credit-agreement addendum"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the commercial loan facility and fee schedule fields defined in `assets/schema.json` from the supplied credit agreement, commitment letter, or closing addendum. Validation rules (`complete_fields`) live in `assets/validation.yaml`. The four disposition signals (legibility / type_match / confidence) are Bridge-authoritative (sense A); completeness across the credit facility package is the underwriting agent's sense-B call.
