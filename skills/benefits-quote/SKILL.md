---
name: benefits-quote
description: A group-benefits insurance quote from a carrier. Use when comparing carrier quotes for a program. Extracts premium, deductible, coverage limits, exclusions, currency, language, effective dates, product line and commission, plus the rate table and benefit-design rows; validates the quote is complete and dated.
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype benefits quote insurance"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "2.0"
---

Extract the fields in `assets/schema.json` from the supplied carrier quote. Normalize currency to an ISO 4217 code and language to a BCP-47 tag; both ride inside `key_fields` (Option A — the extraction wire carries no first-class currency/language). Leave amounts in the quote's native currency (FX conversion happens downstream in the normalize layer, S1-benefits-2). Validation rules live in `assets/validation.yaml`.

Rows go in `tables` (schema v2): `tables.rates` holds one row per printed premium rate (coverage tier, tobacco class, age band, amount, **per-row** frequency, rate basis, benefit amount) and `tables.design` holds one row per benefit-design provision. Every value is text copied as printed — amounts are never computed or converted here; the consumer code-parses money. A carrier that quotes some rates monthly and others weekly keeps each row's own frequency. Commission terms ride as `commission_*` key fields.
