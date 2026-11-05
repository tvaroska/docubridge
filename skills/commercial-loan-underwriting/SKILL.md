---
name: commercial-loan-underwriting
description: Collect and validate commercial real estate loan, residential TRID mortgage closing, and commercial property insurance packages. A bounded Collect over trid-closing-disclosure, cre-credit-agreement, mai-appraisal, and commercial-property-quote with human-in-the-loop review parking and SLA follow-up.
license: Apache-2.0
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "trid-closing-disclosure cre-credit-agreement mai-appraisal commercial-property-quote"
  bridge-tags: "banking mortgage insurance lending underwriting collect fsi"
  bridge-policy: assets/policy.yaml
  bridge-hitl: park
  bridge-satisfaction: >-
    Satisfied by (a) an accepted Closing Disclosure or Schedule B fee addendum
    (trid-closing-disclosure) with verified loan amount, appraised collateral value,
    and origination fee, or (b) both an accepted CRE Credit Agreement (cre-credit-agreement)
    and an independent MAI Appraisal (mai-appraisal) from a distinct issuer, plus an optional
    Commercial Property Quote (commercial-property-quote). Completeness across the credit
    facility package is the underwriting agent's sense-B decision.
  version: "1.0"
---

Bounded Collect for Financial Services commercial loan, residential TRID mortgage closing, and commercial property insurance packages. The Bridge classifies each arriving credit agreement, appraisal, closing disclosure, or property insurance quote against the candidate doctypes, dispositions it (sense A), and chases missing or low-confidence items per `assets/policy.yaml`.

## Satisfaction (prose, advisory)
Either (a) one accepted Closing Disclosure (`trid-closing-disclosure`) carrying verified loan amount, appraised value, and origination fee, or (b) one accepted CRE Credit Agreement (`cre-credit-agreement`) paired with an independent MAI Appraisal (`mai-appraisal`) from a distinct issuer.
