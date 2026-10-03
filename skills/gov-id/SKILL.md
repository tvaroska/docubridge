---
name: gov-id
description: A government-issued ID (passport, driver's license, national ID) used as proof of identity and address. Extracts name, address, document number, issuing authority, and expiry date; validates the ID is unexpired and complete.
license: Proprietary. LICENSE.txt has complete terms
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype identity address"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the fields defined in `assets/schema.json` from the supplied ID. Validation rules (`not_expired`, `complete_fields`) live in `assets/validation.yaml`. The four disposition signals (legibility / type_match / confidence) are Bridge-authoritative (sense A); completeness is the app's sense-B call.
