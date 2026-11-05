---
type: atom
up: "model.md"
tags: [bridge, outbound]
---

# Outbound direction (the Bridge as client)

> The core motion is the **solicited pull spine** — a party responds *inbound* to an exchange the servicer opened ([dual-path](a2a-edge.md#dual-path-fulfillment)). But an assistant that only ever waits is half an assistant: it also **chases**, **delivers**, and **dials counterparties**. Outbound is therefore a **first-class direction**, not a "someday" edge. This note is the design home for everything the Bridge *sends*.

## Three outbound activities

The word "outbound" covers three distinct things; keep them apart.

| Activity | What it is | Pattern / trigger | Who it talks to |
|---|---|---|---|
| **Party-channel nudge** | the real send behind a [proactive chase](proactive.md) — a reminder to a slow party (email / portal link / that party's agent) | Scheduler alarm → chase | an existing leg's party |
| **Delivery** | pushing a finished document/result outward | the [Deliver](patterns.md) pattern (CUJ-3) | a party or downstream system |
| **Bridge-as-client (dial-out)** | the Bridge *originating* an A2A exchange against a counterparty — Agent-Card discovery, then `message/send`/`message/stream` | a servicer asks the Bridge to *reach* a party that has an agent | a counterparty agent |

The pull spine still needs no client to *receive* a response; these are the paths where the Bridge **initiates** traffic.

## It rides the standard port — nothing new at the wire

Outbound is **not** a new protocol or a Ring-1 deviation. It reuses the existing **A2A client (outbound) [port](ports.md)** (Ring 2): local adapter = an injected fake transport; GCP adapter = an authenticated transport carrying **[Agent Identity](zones.md)**. Dialing a counterparty is canonical A2A in the other direction — resolve the peer's Agent Card, then speak the same `message/send` / `message/stream` / `tasks/*` verbs the [A2A edge](a2a-edge.md) serves. A counterparty's `input-required` park is consumed with the **same** native construct a servicer uses on the Bridge ([`RemoteA2aAgent`](a2a-consumer.md)) — the Bridge becomes, for that leg, a consumer of *its* counterparty.

- **Egress trust boundary.** All dial-out leaves through the [two-zone](zones.md) boundary authenticated as a workload (Agent Identity); the outbound transport never carries a long-lived party bearer token.
- **Idempotency & retries** live in the port adapter (a send is crash-safe and re-drivable), consistent with the [Scheduler](proactive.md) port's idempotent alarms.
- **Channel and copy.** A nudge carries a fixed channel (`email`) and the Bridge's default chase copy. [Party memory](zones.md#party-memory--a-reserved-advisory-only-port) is the intended source of per-party defaults (preferred channel/format/language, advisory and overridable), but its port declares no methods, so nothing pre-selects them.

## One architecture for all three activities

The architecture is whole: all three activities ride the single **A2A client (outbound) port** through the one two-zone trust boundary, over the shared wire contracts. Nothing about the port, the boundary, or the contracts is per-activity — a party-channel nudge, a Deliver push, and a Bridge-as-client dial-out are the same egress motion with different targets.

As built: `Nudge` (`bridge/outbound.py:68`), `DialOut` (:92), `Delivery` (:112) map to their idempotency keys: `nudge:<ctx>:<step>` for ladder steps, `nudge:<ctx>:chase-<seq>` for the manual chase (where `seq = leg.chases`), `dial:<ctx>`, and `deliver:<ctx>:<recipient>`.

## Chasing: the ladder and the manual chase

Ladder nudges come from `build_nudge_notifier` (`bridge/outbound.py:293`) on `overdue` and `reminder` steps — one send per step. `escalated` is internal only and sends nothing.

The operator's manual chase is `POST /rfps/{id}/chase` (body `{"party": <carrier>|null}`, `null` = every chaseable carrier; `bridge/dashboard/rfp_api.py`). It sends one `Nudge` per targeted carrier over the `a2a_client` port, with `seq = leg.chases`. A second chase is a new send. A crash re-drive dedupes. `bridge.rfp.record_chase` (`rfp.py:685`) is **audit-only**: `chases += 1`, `last_chased_at`, plus one `chased` audit event. No cell status and no `last_activity` moves. Chaseable means tracked, outstanding and not `declined`. Responses: 409 when nothing is chaseable, 404 for an unknown RFP or carrier, 503 for a misconfigured adapter, 502 on a partial send failure (whatever was sent is still recorded). The route is mounted on the dashboard host, the RFP board host and the RFP demo host.

See [proactive](proactive.md) for ladder timing and [rfp-views](rfp-views.md#the-manual-chase) for the chase from the RFP-views angle.

## Not outbound: cold unsolicited push

**Cold, unsolicited inbound push** (the [Extract](patterns.md) pattern — a party pushing a document with no open exchange) is a **secondary edge**, distinct from outbound: it is traffic the Bridge *receives* unsolicited, not traffic it *sends*. No edge accepts it without an open exchange. It is not part of this note's scope.

## Related
- [A2A edge](a2a-edge.md) · [A2A consumer](a2a-consumer.md) · [ports](ports.md) · [proactive follow-up](proactive.md) · [party memory](zones.md#party-memory--a-reserved-advisory-only-port) · [patterns](patterns.md)
