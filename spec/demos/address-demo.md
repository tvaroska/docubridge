---
type: atom
related:
  - "demo-suite.md"
  - "../../design/dual-path.md"
  - "../../design/collect.md"
  - "../../design/proactive.md"
tags: [bridge]
---

# Address Demo — scenario

> The bounded-Collect warm-up: a servicer's agent needs **proof of address** and asks the Bridge once. Address proof is satisfied by **either one government ID or two bills from different companies** — a [bounded Collect](../../design/collect.md) with **agent-owned completeness** and **no typed slots**. The Bridge classifies, dispositions, and records each doc in a **classified ledger**, best-efforts an advisory assessment of what's still outstanding, and chases it; the agent reasons over the ledger and holds final authority.

## The satisfaction function (agent-side, sense B)

The agent holds final authority here; the Bridge best-efforts an advisory assessment of it by reading the ledger against the skill's **prose satisfaction description**, and chases the delta. The agent's own function:

```
satisfied(ledger) =
     any(d.doctype == "gov-id"      and d.accepted)
  or count(distinct d.issuer for d in ledger
           where d.doctype == "utility-bill" and d.accepted) >= 2
```

This runs as the [multi-turn Collect loop](../../design/collect.md) with a *fixed* agent policy: each turn the Bridge reports the ledger, the agent runs this function and replies with the outstanding requirements (or `done`). [RFP](rfp-demo.md) is the **identical loop** with a smarter, mutating reply — same Bridge code, no DSL.

## Scenario — KYC onboarding

A fintech servicer onboards a new customer, *Jordan Lee* (synthetic identity). The servicer's internal agent requests proof of address — `request address-proof for party:jordan-lee` — and never touches the chase again. Run as **two exchanges on the one deployed core**, so both fulfillment paths and the interesting branches all show.

### Exchange 1 — Path A, the fast case (~30s)
A counterparty that *has* an agent returns a **structured government-ID assertion** on the A2A edge (name + address + expiry, carrying the exchange context token). Bridge does **validate-only** — schema + [disposition](../../design/disposition.md), no extraction call — appends one `gov-id` entry to the ledger. Agent's function is satisfied on the first document; delivered instantly.
**Proves:** pull spine · [dual-path](../../design/dual-path.md) (A) · alternative satisfaction resolved by the ID branch · no extraction on Path A.

### Exchange 2 — Path B, the meaty case (the real demo)
Jordan has no passport handy, so goes the **two-bills** branch via the [A2UI portal](../../design/a2ui-edge.md):

| Beat | What happens | Ledger after | Proves |
|---|---|---|---|
| 1 | Uploads an **electricity bill** → classified `utility-bill`, issuer `PowerCo`, extracted, auto-approved. Agent's function: 1 distinct issuer < 2 → not done → asks the Bridge to chase "one more from a different company." | `[bill:PowerCo]` | classification vs the **label space** · issuer extraction · simple-gate disposition · **agent** reasoning over the ledger |
| 2 | **Silence.** Virtual clock advances past the SLA window → `overdue` → reminder → `escalated`. | `[bill:PowerCo]` | [proactive follow-up](../../design/proactive.md) with real content (the missing second bill), not a generic stall |
| 3 | Uploads a **second PowerCo bill** (same issuer). Classified + issuer extracted. Agent's function: still 1 *distinct* issuer → not done → re-requests "a bill from a *different* company." | `[bill:PowerCo, bill:PowerCo]` | **agent-owned completeness (sense B)** distinct from Bridge disposition (sense A) — the Bridge accepted a valid bill and best-efforts the set assessment, but the *agent* makes the final call and rejects the set |
| 4 | Uploads a **water bill** (`AquaUtil`) but the photo is blurry → quality gate → **resubmission** request (capped at 3). Resubmits a clear copy → mid-confidence → **HITL review** → reviewer approves. | `[bill:PowerCo, bill:PowerCo, bill:AquaUtil]` | quality gate · resubmission loop · confidence gate · HITL suspend/resume from the persisted session |
| 5 | Agent's function: 2 distinct issuers → **done** → finalize → one **clean normalized artifact** delivered to the servicer agent. | — | the payoff: "asks once, receives ready-to-act documentation" |

That one narrative hits every beat: pull spine, both edges, dual-path, bounded Collect, classification + issuer extraction, agent-owned satisfaction (OR + count + distinct-issuer), all three disposition gates, resubmission, proactive chase/escalate, and delivery.

## Live doctype-add (config, no redeploy)

> A capability, shown at small scale on the one deployed core: *new capability = config, no redeploy*. Two runs with a skill uploaded **between them, live** — a small-scale form of the [RFP reusability headline](rfp-demo.md).

- **Run 1 — simple.** The core knows only the `utility-bill` doctype; proof of address goes the Path-B portal route (a single bill under a lightweight agent policy). Solid but modest — establishes the baseline the audience will watch change.
- **Live moment — the platform proof (config-only).** Upload the `gov-id` **doctype skill** to the *running* core. The [Agent Card](../../design/a2a-edge.md) regenerates and now advertises gov-ID — **no redeploy, no restart, no agent code touched.** The agent can accept a document type it could not 20 seconds ago.
- **Run 2 — full.** A counterparty returns a **structured gov-ID on Path A** → instant validate-only satisfaction (the ~30s fast case) — a fulfillment path that *did not exist* in Run 1 now lights up. The two-bills branch remains available for depth.

**Honesty rule for the stage.** Two things change between runs and they live in **different places** — narrate them separately, never as one "skill update":
- **Label space `[utility-bill]` → `[gov-id, utility-bill]`** — this *is* the process skill / the new doctype skill. **Config. The platform's win.**
- **Satisfaction `count>=1` → `gov-id OR distinct>=2`** — the **decision** stays the **agent's** (sense B), agent policy/code; the skill may now carry this as a **prose satisfaction description** (never a formal rule/DSL) that the Bridge best-efforts an advisory assessment from. Present the tightening as *"the servicer tightens its own policy,"* distinct from the no-redeploy platform claim — the agent owns the completeness *decision*; the Bridge best-efforts (advisory), and the skill may carry a prose satisfaction description, never a formal rule.

## What we need to make it work

**Skills (config, uploaded at runtime — [Agent Skills format](../../design/skills.md), one `SKILL.md` folder each):**
- **Process skill `address-proof`** — a [lean catalog](../../design/skills.md): pattern `Collect (bounded)`, candidate doctypes `[gov-id, utility-bill]` (the label space), policy (SLA cadence, max nudges, retry cap, thresholds) in `assets/policy.yaml`. **No** *formal* satisfaction rule inside it — a prose satisfaction *description* is allowed, which the Bridge best-efforts an advisory assessment from.
- **Doctype skill `gov-id`** — schema (name, address, doc number, issuing authority, expiry) in `assets/schema.json`, extraction prompt in the `SKILL.md` body, validation (unexpired, address present, legible).
- **Doctype skill `utility-bill`** — schema with **issuer/company name** extracted *and canonicalized* to a stable key (`PowerCo` / `Power Co.` → `power-co`) so the agent compares clean values; account-holder name, service address, statement date; validation (recent within N months, address present).

**Capabilities the demo forces (the step up from a plain single-doc pull):**
- **Classification** — route an unlabeled upload to `gov-id` vs `utility-bill` against the label space, or flag wrong/unrequested. Gemini classifies against the candidate doctypes.
- **Classified ledger** — the Bridge's append-only "what we have": `{doctype, key fields (issuer, address, expiry), disposition}`, derivable as a view over the exchange's tasks (one doc per task).
- **Driver (two initiation surfaces)** — a thin stand-in for the servicer's internal agent that requests proof and starts the [`Workflow` graph](../../design/service-agent-architecture.md) (which reads the ledger, runs the satisfaction function via the gate, and re-requests or finalizes). Realized as (1) a **CLI** (`python -m agents.address …`, scriptable, drives the golden/eval runs) and (2) the **[Agent Console](../../design/frontend.md)** (Vite+React: new-session-from-prepared-request button + session list with live status). Both are `App`/`Runner` hosts, not ADK agents. Sense B lives in the graph's **gate node**, not the driver.

**Core machinery the demo relies on:**
- Extraction seam — **fixture** adapter (deterministic, for a reliable stage run) + **Gemini** adapter (impressive on real sample docs).
- [Disposition](../../design/disposition.md) — simple quality/confidence gates + HITL suspend/resume.
- Both [edges](../../design/edges.md) — A2A server (Path A) + A2UI portal & reference renderer (Path B).
- [Proactive](../../design/proactive.md) — Scheduler seam with the **virtual clock** to fast-forward the SLA on stage.
- Read-model/dashboard — exchange view showing the ledger fill in live.
- Deploy path — runtime + Gateway/Identity (deployed for the real showcase; local adapters for rehearsal).

**Demo data (don't underestimate this):**
- **Synthetic** documents only — no real PII: a driver-license image, two same-issuer bills, two distinct-issuer bills, one expired ID, one blurry/illegible bill. These double as the extraction **fixtures** and the test corpus. The eval spec lives in `wiki/evals/address/`.
- A scripted **timeline** for the virtual clock (when to fire overdue/escalated).

**Validation:** the same golden-run suite runs against fixture and Gemini adapters — Path A instant, distinct-issuer accept, same-issuer reject, resubmission, HITL, escalation.

## Decisions & design parameters

**"Same company" is the agent's call.** Two bills from the *same* issuer are each individually valid, so the Bridge **accepts both** and records them; it never inspects the pair. The agent, on its turn, sees `count(distinct issuer) = 1 < 2` → not satisfied → re-requests one from a different company (beat 3). The split: **per-document issuer normalization is the Bridge (sense A)** — it canonicalizes `PowerCo`/`Power Co.` → `power-co`; the **cross-document distinct-issuer decision is the agent (sense B)**. Full entity resolution (fuzzy match, parent-company graphs) is out of scope — canonical-string equality is enough.

**Address-match check.** Whether the bill's address matches the ID's / the account sits on the **agent** side (sense B) here, consistent with our model. Having the Bridge *surface* an address-mismatch flag as a pack-declared rule (sense A) would enrich beat 4 but nudges toward "declared rules in the skill"; the design keeps this on the agent side.

## Related
- [demo implementations](demo-suite.md), [dual-path fulfillment](../../design/dual-path.md), [Collect](../../design/collect.md), [proactive follow-up](../../design/proactive.md), [frontend](../../design/frontend.md)
