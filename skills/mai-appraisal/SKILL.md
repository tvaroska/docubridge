---
name: mai-appraisal
description: An Interagency (12 CFR Part 34) / USPAP MAI commercial real estate appraisal or Fannie Mae Form 1004 residential appraisal report. Extracts appraised collateral value, property address, net operating income, currency, and valuation date.
license: Proprietary. LICENSE.txt has complete terms
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype banking mortgage lending appraisal collateral"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the collateral valuation fields defined in `assets/schema.json` from the supplied MAI commercial appraisal or Form 1004 residential appraisal report. Validation rules (`complete_fields`) live in `assets/validation.yaml`. The four disposition signals (legibility / type_match / confidence) are Bridge-authoritative (sense A); collateral adequacy and LTV compliance are the underwriting agent's sense-B call.
