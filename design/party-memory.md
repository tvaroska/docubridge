---
type: atom
related:
  - "gcp-substrate.md"
  - "aggregate-model.md"
tags: [bridge]
---

# Party Memory

> The same partner recurs across many exchanges. How they usually send things, what they got wrong last time, their preferred channel — memory keyed by the counterparty reference could feed advisory hints. But the Bridge does **not** own the party as an aggregate.

**Intent.** The internal agent already knows its counterparties, so it supplies a stable [counterparty reference](aggregate-model.md) (falling back to the exchange context). Party memory is the cross-exchange record keyed by that reference; its intended shape is an append-only log of observations (corrections, disposition outcomes, channel/format/latency) with a derived per-party profile.

**The interface as built.** `Seam.memory_bank` / `MemoryBankSeam` declares `build_memory_service(db_path: str | None = None) -> BaseMemoryService` and is wired into `LlmLeg` (`bridge/edge/llm_leg.py`):
- **Local (`LocalMemoryBank`)**: returns ADK `InMemoryMemoryService()` by default, or `SqliteMemoryService(db_path=...)` when `db_path` or `BRIDGE_MEMORY_DB_PATH` is set.
- **GCP (`GcpMemoryBank`)**: returns ADK `VertexAiMemoryBankService(project=..., location=..., agent_engine_id=...)` backed by Vertex AI Agent Engine (`BRIDGE_AGENT_ENGINE_ID`), refusing loudly if `BRIDGE_AGENT_ENGINE_ID` is unset.

Higher-level domain consumers (disposition pre-arming, extraction hints, outbound channel defaults) remain advisory-only and are not wired into the deterministic disposition gate (`Outbound` still uses `Nudge.channel = "email"` and default chase copy).

Three intended consumers:

- **Disposition pre-arming** — recurring issues pre-flag matching rules and raise scrutiny; never auto-rejects.
- **Extraction hints** — correction hotspots injected as few-shot examples to lift accuracy on a party's quirks.
- **Outbound defaults** — preferred channel/format/language pre-select the outbound template, overridable.

**Advisory only.** Any method the seam gains is advisory: it never changes thresholds, never auto-routes disposition, never sends outbound outside the normal path, and never sits on the critical path of a single exchange. Its outputs only ever pre-arm or hint.

## Related
- [GCP substrate](gcp-substrate.md), [aggregate model](aggregate-model.md)
