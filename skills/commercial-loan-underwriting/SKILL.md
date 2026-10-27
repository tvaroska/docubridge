---
name: commercial-loan-underwriting
description: Collect and validate commercial real estate loan and residential TRID mortgage closing packages. A bounded Collect over trid-closing-disclosure with human-in-the-loop review parking and SLA follow-up.
license: Proprietary. LICENSE.txt has complete terms
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "trid-closing-disclosure"
  bridge-tags: "banking mortgage lending underwriting collect fsi"
  bridge-policy: assets/policy.yaml
  bridge-hitl: park
  bridge-satisfaction: >-
    Satisfied by an accepted Closing Disclosure or Schedule B fee addendum with verified
    loan amount, appraised collateral value, and origination fee. Completeness across the
    credit facility package is the underwriting agent's sense-B decision.
  version: "1.0"
---

Bounded Collect for Financial Services commercial loan and residential TRID mortgage closing packages. The Bridge classifies each arriving closing disclosure or fee addendum against the candidate doctypes, dispositions it (sense A), and chases missing or low-confidence items per `assets/policy.yaml`.

## Satisfaction (prose, advisory)
One accepted Closing Disclosure (`trid-closing-disclosure`) with verified loan amount, appraised value, and origination fee.
