---
type: atom
up: "runtime.md"
tags: [bridge]
---

# Two-Zone Network Model

> The Bridge is a **network-boundary mediator (DMZ)**. Isolation is enforced **at the network layer only** — not by application-level tenant scoping. This is core: a credible platform showcase — and any real production deployment — has to show the trust boundary.

```
External zone (untrusted)  ─ingress→  Bridge (DMZ)  ←Agent Identity→  Internal zone (trusted)
 provider/party agents                Agent Runtime                    backend / servicer agents
 + host portals
```

- **Agent Gateway = external ingress.** All external traffic (both [edges](edges.md)) enters through the Gateway — routing, access-control, per-party auth scoping. A party agent can address **only its own leg's context**. With the Gateway enabled the runtime accepts only load-balancer ingress. **One exception:** the [email intake](email-intake.md) webhook — Microsoft Graph cannot present a Google identity — is a separate public Cloud Run service (`allUsers` invoker) outside the Gateway, authenticated per notification by the subscription's clientState and running as a create-only identity (no project roles; per-resource grants only — read on its two Graph secrets and object-create on the email bucket). The RFP board host defaults to internal-only ingress.
- **Agent Identity = workload identity.** The Bridge↔backend link and the Bridge's calls to managed services authenticate as workloads (mutual TLS). The internal zone never accepts unauthenticated calls.
- **Carrier-vs-carrier confidentiality is addressing, not a wall.** Competing carriers share the external zone; a carrier can't see another's bid because it can't address another leg's context (Gateway scoping + per-leg isolation).
- **No per-customer logical multi-tenancy.** One deployment serves one trust domain; cross-org tenant scoping is out of scope.

**Deployment-only, not a port.** Unlike the storage [ports](ports.md), the Gateway has no local equivalent — local dev runs no gateway. So the deploy path provides **validated deploy-spec builders** for the gateway, workload identity, and network zone that fold into the deployment spec only when a gateway is configured. Provisioning the runtime is separate from provisioning the gateway.

## Party memory — a reserved, advisory-only port

> The same partner recurs across many exchanges. How they usually send things, what they got wrong last time, their preferred channel — memory keyed by the counterparty reference could feed advisory hints. But the Bridge does **not** own the party as an aggregate.

**Intent.** The internal agent already knows its counterparties, so it supplies a stable [counterparty reference](aggregate-model.md) (falling back to the exchange context). Party memory is the cross-exchange record keyed by that reference; its intended shape is an append-only log of observations (corrections, disposition outcomes, channel/format/latency) with a derived per-party profile.

**The interface as built.** `Port.memory_bank` / `MemoryBankPort` declares `build_memory_service(db_path: str | None = None) -> BaseMemoryService` and is wired into `LlmLeg` (`bridge/edge/llm_leg.py`):
- **Local (`LocalMemoryBank`)**: returns ADK `InMemoryMemoryService()` by default, or `SqliteMemoryService(db_path=...)` when `db_path` or `BRIDGE_MEMORY_DB_PATH` is set.
- **GCP (`GcpMemoryBank`)**: returns ADK `VertexAiMemoryBankService(project=..., location=..., agent_engine_id=...)` backed by Agent Engine (`BRIDGE_AGENT_ENGINE_ID`), refusing loudly if `BRIDGE_AGENT_ENGINE_ID` is unset.

Higher-level domain consumers (disposition pre-arming, extraction hints, outbound channel defaults) remain advisory-only and are not wired into the deterministic disposition gate (`Outbound` still uses `Nudge.channel = "email"` and default chase copy).

Three intended consumers:

- **Disposition pre-arming** — recurring issues pre-flag matching rules and raise scrutiny; never auto-rejects.
- **Extraction hints** — correction hotspots injected as few-shot examples to lift accuracy on a party's quirks.
- **Outbound defaults** — preferred channel/format/language pre-select the outbound template, overridable.

**Advisory only.** Any method the port gains is advisory: it never changes thresholds, never auto-routes disposition, never sends outbound outside the normal path, and never sits on the critical path of a single exchange. Its outputs only ever pre-arm or hint.

## Related
- [runtime](runtime.md), [ports](ports.md), [edges](edges.md), [aggregate model](aggregate-model.md)
