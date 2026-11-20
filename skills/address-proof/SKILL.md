---
name: address-proof
description: Collect proof of address for a party. Use when a servicer's agent needs address verification. A bounded Collect over candidate doctypes gov-id and utility-bill; the agent owns the completeness decision. Carries a prose satisfaction description (never a formal rule) the Bridge best-efforts an advisory assessment from.
license: Apache-2.0
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "gov-id utility-bill"
  bridge-tags: "address collect kyc"
  bridge-policy: assets/policy.yaml
  bridge-hitl: park
  bridge-satisfaction: >-
    Satisfied by one accepted government-issued ID, OR two accepted utility bills from
    two different companies (distinct issuers). Two bills from the same company do not
    satisfy. The completeness decision is the agent's (sense B); this description is
    advisory only.
  version: "1.0"
---

Bounded Collect. The Bridge classifies each arriving doc against the candidate doctypes, dispositions it (sense A), evaluates `assets/strategy.yaml` into `ExchangeTurn.sufficiency` (sense A′), and chases the delta per `assets/policy.yaml`. Completeness — the final `done` decision and any waiver — remains the **calling agent's** sense-B call (e.g. `is_satisfied` in the companion `docubridge-examples` reference consumer), asserted per turn, never enforced here.

## Satisfaction (prose, advisory)
One government-issued ID **or** two utility bills from **different** companies.
