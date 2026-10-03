---
name: benefits-procurement
description: Source and compare group-benefits quotes from N carriers over a program. Use when a broker's agent needs one normalized, apples-to-apples comparison across carriers. A Negotiate exchange over a program; the agent owns the bind decision (sense B). Carries a prose satisfaction description and program-standard rules the comparison flags against.
license: Proprietary. LICENSE.txt has complete terms
metadata:
  bridge-kind: process
  bridge-pattern: negotiate
  bridge-candidate-doctypes: "benefits-quote"
  bridge-tags: "benefits negotiate quote program"
  bridge-policy: assets/policy.yaml
  bridge-program-standard: assets/standard.yaml
  bridge-satisfaction: >-
    Satisfied when each program leg holds an accepted, standard-conformant benefits quote
    normalized to one schema/currency/language, and the agent has bound a winning leg. The
    bind decision is the agent's (sense B); this description is advisory only.
  version: "1.0"
---

Negotiate over a program. The Bridge fans out one leg per carrier (each its own context+party, isolated), classifies + dispositions each arriving quote (sense A), flags it against the program-standard rules in `assets/standard.yaml`, and chases per `assets/policy.yaml`. A flagged gap routes to negotiation; a revised quote lands as a new artifact version on the same leg. Completeness/bind is the agent's sense-B call, asserted per turn, never enforced here.
