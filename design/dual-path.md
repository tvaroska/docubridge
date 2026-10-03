---
type: atom
related:
  - "edges.md"
  - "fulfillment-graph.md"
  - "disposition.md"
tags: [bridge]
---

# Dual-Path Fulfillment

> When a servicer requests a doc from party X, X responds — **indistinguishably to the requester** — in one of two modes, decided only by **what kind of response arrives**.

| Mode | Party | What arrives | Bridge does |
|---|---|---|---|
| **Path A — structured** | has an agent | a structured data response on the A2A edge (carrying the exchange context token) | **validate-only** — schema validation + [disposition](disposition.md), **no extraction call** |
| **Path B — portal** | no agent | PDF / free-text / form via the A2UI portal | **extract / normalize** → disposition → HITL if flagged (the [extraction graph](fulfillment-graph.md)) |
| **Operator-fulfilled** | internal human | upload on X's behalf | as Path B; lower priority |

**Path-A schema validation is skill-driven.** The typed key-field model an assertion must satisfy is built from the asserted doctype's **skill schema** — the doctype skill's `bridge-schema` asset (default `assets/schema.json`), read through the `skill_registry` seam by `bridge/doctypes.py`, the same reader the Path-B extraction prompt is built from. Installing a doctype skill folder therefore gives that doctype Path-A validation too, with no code change and no redeploy; the schema's `required` key fields are mandatory and an undeclared key is refused (`extra="forbid"`), raising at validation before any disposition. Skip policy: a doctype with **no installed skill** skips the typed check and stays a free string, so the [disposition](disposition.md) label-space gate rejects it `unsupported` (an out-of-label-space document must remain reportable); an installed skill whose asset is **absent** skips the check with a warning (a capability gap is never silent); an installed asset that is **unparsable** refuses loud.

**Party-first, inbound.** Both modes are the party *responding* to an already-open exchange — the Bridge minted the context at request time. That is why the **pull path** itself needs no A2A client. (Outbound — chases, delivery, dialing counterparties — is a first-class direction handled separately: [outbound direction](outbound.md).)

**The migration payoff.** When a Path-B party later builds its own A2A agent, it flips to Path A by **config only** — history preserved, and the servicer's code never changes. Both modes coexist on the same exchange (e.g. a party agent returns a structured ID assertion on Path A; a human uploads an ID photo or two bill PDFs on Path B).

**Escalation is not rejection (Path B).** When extraction keeps failing to *read* a document, Path B routes it to a **human** (escalated, disposition `PENDING`) rather than rejecting it — the document was never judged wrong, the Bridge just could not read it. A resubmission awaits a *fresh document*, not a human decision, so it is handled **inside a single exchange task** (the Bridge chases the resubmission and returns one terminal ledger carrying the rejected doc then the accepted one), not as a new fulfillment round.

This is the concrete meaning of "uniform internal interface regardless of party maturity": the messy variety lives at the edge, and everything past intake is one normalized artifact stream.

## Related
- [two edges](edges.md), [extraction graph](fulfillment-graph.md), [disposition](disposition.md)
