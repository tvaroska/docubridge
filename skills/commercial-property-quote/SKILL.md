---
name: commercial-property-quote
description: A commercial or sovereign catastrophe property insurance carrier quote, binder, or deductible endorsement. Extracts total insured value, coverage limit, annual premium, deductible, currency, and effective date.
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype insurance property placement quote endorsement"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the commercial property placement and catastrophe deductible fields defined in `assets/schema.json` from the supplied carrier quote, binder, or deductible endorsement. Validation rules (`complete_fields`) live in `assets/validation.yaml`. Three of the four disposition signals (legibility / type_match / confidence) are Bridge-authoritative (sense A); the fourth, completeness, is the sense-B call — placement binding and layer completeness are the broker/underwriting agent's sense-B call.
