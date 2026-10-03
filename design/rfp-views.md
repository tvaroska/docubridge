---
type: atom
up: "frontend.md"
related:
  - "frontend.md"
  - "zones.md"
  - "outbound.md"
  - "email-intake.md"
  - "a2ui-edge.md"
tags: [bridge, frontend, rfp, collect]
---

# RFP views — the consumer status API, the manual chase, the carrier checklist

> An RFP is one exchange with N isolated carrier legs (the RFP aggregate in `bridge/rfp.py`). Three views read it. The **consumer read API** gives a consumer's own status board the carrier × product-line matrix as content. The operator's **manual chase** sends one reminder to a carrier now. The **carrier checklist** shows one carrier, addressed by its leg, exactly what it still owes, and lets it upload into a row. All three read and write the same `RfpBoard`, which reads and writes the RFP through the [exchange store](seams.md) — so an RFP (its uploads, chases and audit trail) survives a host restart. No view invents state: `overdue` is derived on read, a chase is audit-only, and an upload goes through the same disposition gate as every other channel.

## The consumer read API (internal zone)

Mounted on the Ops Dashboard host over its injected `RfpBoard` — deployed, the internal RFP board host (`bridge.dashboard.board_host`, [email intake](email-intake.md#deployed-topology)), which serves it over the durable exchange store. A write that loses the exchange store's compare-and-set to a concurrent writer is a 409 the client retries (here and on the portal upload):

| Route | Returns |
|---|---|
| `GET /rfps` | every open RFP's status (`[]` with no board) |
| `GET /rfps/{rfp_id}` | one RFP's status; 404 when unknown |
| `POST /rfps/{rfp_id}/chase` | the manual chase (below) |

The status is `RfpStatusView` verbatim — `rfp_id`, `process`, `context_id`, `due_at`, `as_of`, `lifecycle`, `ready` / `waiting`, `counts`, `carriers`, `cells`, `unmatched` — plus two additive fields:
- `api_version` (`"1"`), the shape version a consumer pins;
- per carrier, `reply_address` and `reply_subject_tag`: the leg's [email intake](email-intake.md) address, `null` when the host has no intake mailbox configured (`BRIDGE_EMAIL_MAILBOX` + `BRIDGE_EMAIL_TOKEN_SECRET`).

Each cell carries its effective status (`invited | acknowledged | received | incomplete | declined | overdue`), versions, last activity, and the gate's `reason_code` / `message` / `field_reviews` relayed verbatim. A consumer renders this matrix in its own UI; it does not embed a Bridge page. The dashboard frontend renders the same matrix off its SSE row (which omits `as_of` so an idle board emits no frames).

**Derived on read.** `overdue` and the ladder step are projected from the board clock at read time; reading never re-puts the aggregate. An RFP whose ladder reaches `escalated` joins the dashboard escalation queue.

## The manual chase

`POST /rfps/{rfp_id}/chase` with `{"party": <carrier>}` chases one carrier; `{}`, `{"party": null}` or no body chases every carrier with something chaseable.

- **What is chaseable.** A carrier's tracked (`required` / `optional`), outstanding (not `received`), non-`declined` items. A declined line is the agent's to waive or replace, so it is never chased. Nothing chaseable is a 409; an unknown RFP or carrier is a 404.
- **What is sent.** One [outbound](outbound.md) `Nudge` per targeted carrier over the `a2a_client` seam, addressed to that carrier's leg `context_id`. Its message names only that carrier, its outstanding lines, the due date and — when configured — its reply address and subject tag. The `step` is the carrier's current ladder step.
- **Idempotency.** A chase nudge carries `seq = leg.chases`, so its key is `nudge:<leg ctx>:chase-<seq>`. A second chase is a new send (`chase-1`); a re-drive of the same chase dedupes (`resent`). Ladder nudges (no `seq`) keep their `nudge:<ctx>:<step>` key.
- **What is recorded.** `record_chase`: the leg's `chases` count and `last_chased_at`, plus a `chased` audit event. No cell moves; last activity is the carrier's, not the operator's. A chase is recorded once per `seq`, so two concurrent chases that dedupe onto one send count once.
- **Ordering.** Sends run first, off the event loop. Then one fresh read → record → put runs with no `await` in between. If a send fails, what already went is recorded and the route answers 502. A misconfigured outbound adapter is a 503; the adapter is built on first use, never at app construction.

The manual chase complements the SLA ladder; it does not replace it.

## The carrier checklist (external zone)

Mounted on the Provider Portal host over the same board:

| Route | Returns |
|---|---|
| `GET /rfp/legs/{context_id}` | the carrier's checklist; 404 for an unknown leg |
| `POST /rfp/legs/{context_id}/uploads` | file one quote into a row; the new checklist |

**Addressing is the isolation** ([zones](zones.md)). The leg `context_id` is unguessable and names exactly one carrier. The checklist is filtered to that carrier: its product lines, the due date, its ladder step, its reply address, and each item's status, version count, last activity and verbatim reasons. No other carrier's name, cell, counts, document ids or unmatched list appears in it. There is no new auth scheme.

The checklist is a separate model (`kind: "rfp_checklist"`), not a new A2UI view kind; its upload affordance reuses the A2UI action shape. Content and protocol only — the portal owns the pixels.

**Upload into a row.** The body names the row (`product_line`), a `doc_id` and the artifact. The upload follows the email-intake path: extract with the board untouched, then dispose through the gate and project the ledger entry, then one fresh read → acknowledge the carrier's first response → `receive(product_line=…)` → put. Refusals leave the board untouched:

| Refusal | Status |
|---|---|
| unknown leg, or a line not requested of this carrier | 404 |
| the row is waived / no longer requested, or the `doc_id` is already filed | 409 |
| the engine cannot read the artifact | 422 |

Locally the artifact is an eval-corpus name the fixture extractor resolves; a real byte upload is the deployed swap.

## The portal stays the carrier surface

The provider portal is themeable per deployment: `VITE_PORTAL_ACCENT` sets its accent and `VITE_PORTAL_TITLE` its title. A consumer brands the carrier surface instead of building a parallel one. Opening it with `?leg=<context_id>` shows that carrier's RFP checklist; without it the existing Path-B flow is unchanged.

## Running both views locally

`bridge/demos/rfp_demo.py` (the fenced demo-host package) seeds one synthetic RFP onto one shared board and mounts the dashboard at `/ops` and the portal at `/portal`. Its module docstring has the run recipe.
