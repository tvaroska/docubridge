---
type: atom
up: "model.md"
tags: [bridge]
---

# Disposition

> Decide whether a submission is **acceptable** — right type, complete, legible, rule-valid — and route it: accept / clarify / reject / request-more. Not merely legible.

**The mediator boundary — two senses of "judgment."** Neither is forbidden to the Bridge outright; the split is *what authorizes the call*:

- **Sense A — evaluate one artifact against *declared* rules → flags.** Legible? Right type? Meets declared coverage? Unexpired? This *is* disposition. The rules are pack-declared (agent-overridable), never Bridge-invented; the Bridge **surfaces**. → **Bridge**.
- **Sense B — decide requirements, completeness of a *set*, or acceptance of a *deal*.** Is the casefile done? Do we bind this quote? → **the app decides; the Bridge best-efforts.** The Bridge is no longer completeness-blind: it interprets a satisfaction rule the skill carries **as prose** (the Address `OR`/count/distinct-issuer is exactly this — a description, not a formal language), *proposes* what's still outstanding (the satisfaction assessor — [Collect](collect.md)), and chases it. But it has **no deterministic rule and no final word on enforcement** — the app makes the call and must always be ready to. Binding a *deal* is squarely the app's. → **app-decides, Bridge-best-effort.**

**Gates + classification.** A quality gate (below the resubmit threshold → resubmit) and a confidence gate (at or above the auto-approve threshold with no flags → auto-approve; else HITL); the exact order, numbers and boundary semantics are [the review contract](#the-review-contract). Because a bounded [Collect](collect.md) draws on a candidate-doctype **label space**, disposition also does **doctype classification** — classify each arriving doc against the label space (gov ID vs utility bill) or flag it wrong/unrequested, and extract **plus canonicalize** the bill's **issuer** (e.g. `PowerCo` / `Power Co.` / `PowerCo Ltd` → `power-co`) so the agent gets clean, comparable values. That split is the boundary in miniature: **per-document issuer normalization is the Bridge (sense A)**; the **cross-document distinct-issuer decision is sense B** — the **app decides** it, while the Bridge, reading the skill's prose rule, makes a **best-effort** assessment of it (the OR, count-to-two, distinct-issuer check over the classified ledger) and chases the gap. The Bridge classifies, dispositions, records each doc in a **classified ledger**, and best-efforts the set assessment — advisory, never enforced — and pre-allocates no typed slots. Where a pattern flags each normalized item against a program standard, that too is a declared-rule check (sense A) — rules declared in the pack, routing to negotiation.

**The four-signal model.** One confidence scalar can't separate *unreadable* from *readable-but-wrong* from *readable-right-but-expired*, so disposition reads four signals behind a single **`SignalProvider`** interface — one contract, pluggable implementations (fixture / model-reported / computed):

| Signal | Question | Routes to |
|---|---|---|
| Legibility | Can we read it? | resubmission |
| Type match | Right kind? | reject / clarify |
| Extraction confidence + fields requiring review | Read correctly? | HITL vs auto-approve |
| Completeness + failed rules | Complete + rule-valid? | request-more / reject |

Signal 3 carries not just *which* fields need review but **why**: alongside `flagged_fields` an extraction result may carry `field_reviews` — one `{path, reason, confidence, detail}` entry per field, `reason` drawn from `low_confidence` / `derived` / `absent_from_document` / `model_flagged`, `path` a dotted path into the extraction result. The gate itself is unchanged: it fires on "any field requires review" (union of the flags and the reviews) and still emits `reason_code="flagged_fields"`; the provenance rides the ledger for the reviewer and the consumer ([A2A consumer](a2a-consumer.md)).

The first three signals are Bridge-authoritative (sense A). **Completeness is advisory**: the Bridge makes a best-effort assessment of whether the set is complete, but the app decides (sense B) and must always be ready to. The `SignalProvider` contract carries all four regardless.

## The review contract

The gate's order, thresholds and boundary semantics are a published contract: they answer "what level of human oversight, and when is manual review invoked?" for every consumer, and the consumer builds its review UI on them rather than on a threshold of its own.

**Gate order** (`dispose`; first match wins):

| # | Condition | `disposition` | `gate` | `reason_code` | A2A task status |
|---|---|---|---|---|---|
| 1 | doctype not in the process's candidate label space | `rejected` | `unsupported` | `unsupported_doctype` | `COMPLETED` |
| 2a | not legible | `rejected` | `resubmit` | `illegible` | `COMPLETED` |
| 2b | `overall_confidence < resubmit_below` | `rejected` | `resubmit` | `low_quality` | `COMPLETED` |
| 3a | any field requires review (flags ∪ `field_reviews`) | `pending` | `hitl_review` | `flagged_fields` | `COMPLETED` (`report`) · `INPUT_REQUIRED` (`park`, hybrid edge) |
| 3b | `overall_confidence < auto_approve_at` | `pending` | `hitl_review` | `needs_review` | `COMPLETED` (`report`) · `INPUT_REQUIRED` (`park`, hybrid edge) |
| 4 | everything else | `accepted` | `auto_approve` | `auto_approved` | `COMPLETED` |

- **Comparisons are strict `<`.** A confidence of exactly `0.55` is HITL (not resubmit); exactly `0.85` is auto-approve (not HITL).
- **A flag beats the auto-approve comparison but not the resubmit comparison.** A flagged document at `0.93` is HITL; a flagged document at `0.40` is `low_quality` resubmit.
- The task status follows the turn, not the item: the edge maps `ExchangeTurn.terminal` — terminal → `COMPLETED`, non-terminal → `INPUT_REQUIRED` ([A2A consumer](a2a-consumer.md#waiting-is-input-required--and-the-edge-emits-it)). A Collector or Path-A turn is always terminal, so a `pending` item rides a `COMPLETED` task and the consumer owns the review (`bridge-hitl: report`, the default). A `pending` item parks the task as `INPUT_REQUIRED` only when the process declares `bridge-hitl: park` and the edge is hybrid: the LLM leg holds it on `request_human_review` ([A2A edge](a2a-edge.md) R3). The other non-terminal turns are an LLM-leg escalate and an RFP's turn, which waits on its carriers.

**Thresholds.** `auto_approve_at = 0.85`, `resubmit_below = 0.55`.

- Each process declares them in its skill's `policy.yaml` `disposition:` block; the code defaults equal them, and equality across every process skill is test-enforced.
- Validity invariant: `0 ≤ resubmit_below ≤ auto_approve_at ≤ 1`. A pair that breaks it (inverted, out of range, NaN) is refused at construction, and a policy declaring one is refused at load. Equal values are legal: there is then no confidence HITL band, and flags still route to HITL.

**When a human is invoked.**

- The two `hitl_review` rows (3a, 3b).
- **Escalation**: a document that keeps failing the quality gate is routed to a human after `max_resubmissions` (policy) resubmits — `pending`, `escalated_for_review`, never `rejected` (the Bridge could not read it; it never judged it wrong).
- The HITL park is the native long-running pause ([long-running](long-running.md), [ADK](adk.md)): nothing polls, and the leg resumes on the reviewer's resolution. That resolution is the only path from `pending` to a final verdict — a model never mints one ([the inversion](adk.md#the-one-inversion-we-keep--and-why-its-still-standard)).

**What the consumer reads** (`LedgerEntry`): `disposition`, `gate`, `reason_code`, `message`, `field_reviews`, `overall_confidence`, `field_confidence`.

- `field_reviews[].reason` (`low_confidence` / `derived` / `absent_from_document` / `model_flagged`) is provenance for the reviewer; the gate does not branch on it.
- Codes a consumer also sees outside the gate table: `schema_invalid` (a typed payload that fails the doctype schema, rejected at the edge), `awaiting_human_review` (a path's HITL park), `escalated_for_review` (the escalation above).

**Consumer obligations.**

- Render the Bridge's `gate` / `reason_code` / `field_reviews`. Do not re-threshold `overall_confidence` into a second gate.
- A confidence highlight, if shown, uses `auto_approve_at`, so "highlighted" means exactly "not auto-approved on confidence".
- Treat `INPUT_REQUIRED` as a resumable park, not a failure.

**Calibration.** The thresholds are calibrated against the eval corpus, not chosen in isolation.

- Each gate-bearing corpus record bounds the thresholds through the gate's own precedence: a legible `resubmit` record forces `resubmit_below` above its confidence; a legible `hitl_review` or `auto_approve` record forces it at or below; an unflagged `hitl_review` record forces `auto_approve_at` above its confidence; an `auto_approve` record forces it at or below. The legal region is the intersection of these bounds — the *corridor*.
- Illegible and out-of-label-space records do not constrain the corridor (an earlier gate catches them at any threshold), and flagged records bound only `resubmit_below`.
- A threshold change is legal only if it stays inside the corridor **and** the live-engine terminal-outcome parity leg stays green. Narrowing the corridor means adding corpus records near its edges — a corpus change, not a code change.
- The procedure is `python -m bridge.calibration`: it prints the corridor with the records that bind each edge, checks the defaults and every process policy against it, and evaluates a candidate pair (`--auto-approve-at`, `--resubmit-below`). It exits non-zero on any violation.

## The ledger side — agent-authored explanations

Rejected ledger entries and outstanding requirements carry a machine `reason_code` **and** a human `message`, so both the routing decision and its explanation travel with the record. Outstanding work is modelled with `Requirement`, `RequirementsList`, and `RequirementStatus` types: the Bridge proposes what is still outstanding as a `RequirementsList` (each `Requirement` bearing its `reason_code` + `message` and a `RequirementStatus`), which the app reads to decide the sense-B call.

## Related
- [patterns](patterns.md), [extraction graph](extraction.md), [skills](skills.md), [Agent Gym & Programmable Trust](agent-gym.md)
