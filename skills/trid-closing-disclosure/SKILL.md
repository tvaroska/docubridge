---
name: trid-closing-disclosure
description: A CFPB TILA-RESPA Integrated Disclosure (TRID) Closing Disclosure or commercial loan closing fee schedule addendum. Extracts loan amount, appraised value, origination fee, currency, and effective date with source page anchors.
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype banking mortgage lending trid"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the closing disclosure and fee schedule fields defined in `assets/schema.json` from the supplied loan or mortgage closing document. Validation rules (`complete_fields`) live in `assets/validation.yaml`. Three of the four disposition signals (legibility / type_match / confidence) are Bridge-authoritative (sense A); the fourth, completeness, is the sense-B call — completeness across the credit package is the underwriting agent's sense-B call.
