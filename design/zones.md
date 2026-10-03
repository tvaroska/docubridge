---
type: atom
related:
  - "gcp-substrate.md"
  - "edges.md"
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

**Deployment-only, not a seam.** Unlike the storage [seams](seams.md), the Gateway has no local equivalent — local dev runs no gateway. So the deploy path provides **validated deploy-spec builders** for the gateway, workload identity, and network zone that fold into the deployment spec only when a gateway is configured. Provisioning the runtime is separate from provisioning the gateway.

## Related
- [GCP substrate](gcp-substrate.md), [two edges](edges.md)
