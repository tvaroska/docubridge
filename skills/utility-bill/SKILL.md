---
name: utility-bill
description: A utility bill (electricity, water, gas) from a service provider. Use as proof of address. Extracts and canonicalizes the issuer/company, account-holder name, service address, and statement date; validates the bill is recent and shows an address.
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype address proof-of-address"
  bridge-extraction-engine: gemini
  bridge-docai-processor: ""
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the fields in `assets/schema.json` from the supplied bill. Canonicalize the issuer to a stable key (`PowerCo` / `Power Co.` / `PowerCo Ltd` -> `power-co`) — see `references/issuer-canonicalization.md`. Validation rules live in `assets/validation.yaml`.
