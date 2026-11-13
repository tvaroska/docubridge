---
name: loan-payoff-statement
description: Distilled extraction and consolidation skill for loan-payoff-statement (compiled by gemini-4-argon for gemini-3.8-flash runtime execution).
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype loan-payoff-statement harness-evolve"
  bridge-extraction-engine: gemini
  bridge-schema: assets/schema.json
  version: "1.1.0"
  revision-id: "rev-v1-1-43debe2b"
  parent-revision-id: "rev-v1-0-baseline"
  compiled-by-model: "gemini-4-argon"
  runtime-workhorse-model: "gemini-3.8-flash"
  compiled-rule-ids: "rule-3585d9fb"
---

# LOAN-PAYOFF-STATEMENT Extraction & Consolidation Skill (v1.1.0 · `rev-v1-1-43debe2b`)

## Distilled Domain Precedence Rules (Compiled via Harness Evolve)
- **[rule-3585d9fb · extraction_hint]** (EquipLease Financial* / `payoff_waiver_status`): When extracting 'payoff_waiver_status' from EquipLease Financial* loan-payoff-statement documents (see page 14), map 'require_external_payoff_letter' -> 'waived_via_bank_statement_paid_in_full_wire'. Reason: Within policy limits (amount <= $100,000.00 USD), when a debt schedule lists an equipment facility, extract any outgoing wire on the recent 90-day Operating Bank Statement matching the creditor + exact facility number with memo 'FINAL PAYOFF' or 'PAID IN FULL' to the cent and emit payoff_waiver_status='waived_via_bank_statement_paid_in_full_wire'; escalate recurring monthly installments or wires above $100,000.00 to Overwatch.

## Policy Guardrails & Auto-Approval Boundaries
- **Amount Ceiling:** `<= $100000.00` USD (any wire above ceiling escalates to human review)
- **Exact Match Condition:** Wire memo must name the exact facility number + `'PAID IN FULL'` / `'FINAL PAYOFF'` and match balance to the cent
- **Out-of-Policy Fallback:** Route all non-matching or over-ceiling items to `/overwatch` human triage
- **Continuous Verification:** Sampled Step-2 `AI.GENERATE_TABLE` audit over skill-approved outcomes

## Runtime Execution Contract & Lineage
- **Skill URN:** `urn:skill:projects-docubridge-showcase:locations:global:private-loan-payoff-statement`
- **Active Revision:** `rev-v1-1-43debe2b` (parent: `rev-v1-0-baseline`, state: `ACTIVE`)
- **Workhorse Model:** `gemini-3.8-flash` (`thinking_budget=0`)
- **Deterministic Gate:** `Rule 0` (`pin_gate_args` + `threshold_corridor` FA=0)
- **Prompt Compaction:** Daytime SQL rules folded into canonical schema instructions (0 prompt bloat)
