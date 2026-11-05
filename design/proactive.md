---
type: atom
up: "runtime.md"
tags: [bridge]
---

# Proactive Follow-Up

> *"Remind them"* has **no inbound event to react to** — it needs a clock. That single requirement is the deepest structural change the vision implies, and it is **core** to the design: it is the concrete proof of "mediation, not extraction."

The current design is otherwise entirely **reactive** — something arrives, we react. Follow-up is behavior with no trigger, so it introduces a new runtime pillar:

- **A durable Scheduler/Timer port** — a clock behind a [port](ports.md) like every other managed service: local adapter (in-process, with an **injectable virtual clock** for tests) + GCP adapter (**Cloud Tasks**). Idempotent, crash-safe — timer state persists and recovers on restart.
- **An SLA / policy engine** — cadence, deadlines, max nudges, escalation ladder — read from the [process-skill policy](skills.md), so **policy lives in the skill**; `pattern` stays a [selector over built-in flows](patterns.md), not skill-defined.
- **Egress via the visibility read-model *and* a real send** — a stalled request always stamps internal overdue / escalated events and party status on the [dashboard](human-edge.md), so a stall is visible even with no send. The real party-channel nudge (the actual send) rides the [outbound path](outbound.md) — ladder nudges on `overdue`/`reminder` via `build_nudge_notifier`; `escalated` stamps the stall and sends nothing.

## Per-skill ladders

Each process skill's `assets/policy.yaml` carries an `sla:` block (`bridge.sla.load_sla_policy`). The loader enforces a monotone ladder, `escalate_ticks == deadline` and `max_nudges == 2`.

**Address** (`skills/address-proof/assets/policy.yaml`): ticks 1/2/3. The ladder runs from when it is armed (`bridge/followup.py` `FollowupLadder`). It holds one Scheduler alarm per exchange and re-arms step by step. The time-warp host arms it from tick 0.

**RFP** (`skills/benefits-rfp/assets/policy.yaml:10-21` + the `rfp:` block): `overdue_ticks: 1`, `reminder_ticks: 3`, `escalate_ticks: 5`, with `rfp.tick_seconds: 86400`. It is **anchored on the RFP due date**: `bridge.rfp.lifecycle_at` puts `overdue` at `due_at` and each later step at `due_at + (step_ticks − overdue_ticks) × tick`. That gives reminder at +2d and escalated at +4d. The RFP lifecycle is **derived on read** (no alarm is armed), so it sends nothing itself. A carrier is nudged only by the operator's manual chase.

**Benefits** (`skills/benefits-procurement/assets/policy.yaml`) carries a ladder with the same invariants.

## Manual chase

The operator's "Chase now" (`POST /rfps/{id}/chase`, `Nudge.seq`, `bridge.rfp.record_chase`) is the RFP send path. See [outbound](outbound.md) for detail.

**The demo beat.** Let a request go unfulfilled past its SLA window (virtual clock) and watch it move `overdue` → `escalated` with a reminder fired — never silently stalling.

## Related
- [runtime](runtime.md), [skills](skills.md), [human edge](human-edge.md)
