# DocuBridge Architecture Overview, Surfaces & Glossary

Detailed reference moved out of the root [README.md](../README.md) so the front page stays concise while keeping every walkthrough, surface URL, and contract snippet one click away.

## 6-Zone Architecture Diagram

The full 6-zone architecture (A2A edge, dual-path intake, Rule 0 ledger and WORM vault, role views and BigQuery sync, click-to-source proof UI, and Schema Refinery / Agent Gym calibration) is documented in [design/bridge.md](../design/bridge.md) and [BANKING.md](../BANKING.md).

## Consume Over A2A in 10 Lines

Downstream teams do not fork this repository. A calling service agent discovers DocuBridge at `/.well-known/agent-card.json`, sends one `CollectRequest` over A2A JSON-RPC, and receives page-anchored, `Decimal`-consolidated facts back:

```python
import httpx
from contract.wire import CONTRACT_VERSION, CollectRequest

req = CollectRequest(
    process="commercial-loan-underwriting",
    party="LOAN-MERIDIAN-001",
    context_id="ctx-meridian-001",
)
resp = httpx.post(
    "http://localhost:8000/",
    headers={"A2A-Version": "1.0"},
    json={
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m-1",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": req.model_dump(mode="json"),
                        "metadata": {
                            "kind": "collect_request",
                            "contract_version": CONTRACT_VERSION,
                        },
                    }
                ],
            }
        },
    },
).json()
print(resp["result"]["task"]["status"]["state"], resp["result"]["task"]["artifacts"])
```

See [docs/consume-over-a2a.md](consume-over-a2a.md) for supplied folders (`skills/`, `catalog/doctypes/`, `scenarios/`), environment knobs, and the wire-only consumer example (`examples/a2a_consumer/consume_bridge.py`).

## Four Canonical A2A Patterns

1. **Address Verification (Bounded Collect):** Onboards synthetic applicant **Jordan Lee** (`1` gov ID or `2` distinct-issuer utility bills); DocuBridge dispositions documents and parks ambiguous scans while the calling agent owns completeness.
2. **Employee Benefits Procurement (Fan-Out Negotiate):** Fans out `program:acme-2026` across four carriers (ADK A2A, LangGraph A2A, French/EUR PDF, German/GBP PDF), normalizes currency and language, flags gaps against `standard.yaml`, and negotiates revisions over A2A.
3. **Emergent RFP Collection (Shared Board & Email Ingress):** Opens multi-carrier RFP matrices (`acme-vb-2027`) over A2A, correlates inbound M365/MIME carrier emails by HMAC token (`bridge/email_ingress/`), and hot-reloads uploaded skills without restart.
4. **Hybrid A2A Edge (`BRIDGE_EDGE_MODE=deterministic|hybrid`):** Routes structured `CollectRequest` and Path-A assertions through the deterministic zero-LLM Collector while routing off-script intents and HITL pauses through the resumable `LlmAgent` with pinned gate inputs (`bridge/gatepin.py`).

## Four Platform Pillars

| Pillar | Capabilities Demonstrated in DocuBridge | Key Modules |
| :--- | :--- | :--- |
| **1. BUILD** | Single-turn and resumable ADK agents, `SKILL.md` runtime registry, A2A JSON-RPC + SSE edge, A2UI v0.9 surfaces, cross-framework ADK and LangGraph interop | `bridge/agent.py`, `bridge/edge/`, `skills/`, `examples/` |
| **2. GOVERN** | Agent Registry (`urn:agent`, `urn:mcp`, `urn:endpoint`, `urn:skill`, content-addressed revisions), Agent Gateway (Model Armor + Cloud DLP + IAM v3 CEL), SPIFFE JWT-SVID + Human OBO intersection (`Agent ∩ User`) | `bridge/identity.py`, `bridge/agent_gym/registry_gateway.py`, `bridge/knowledge_catalog/` |
| **3. SCALE** | 6-Zone Enterprise Data Architecture: Edge PII redaction, 7-year WORM Audit Vault (`if_generation_match=0`), deterministic `Decimal` Consolidation Engine, bi-temporal SQL Operational Store, Role-Based Views, and BigQuery Open Iceberg sync | `bridge/vault.py`, `bridge/operational_store/`, `bridge/sql_runtime.py`, `terraform/` |
| **4. OPTIMIZE** | Schema Refinery + Agent Gym (`RSR >= 0.80` Two-Sided Oversight, 3-Tier evaluation gate, `SKILL.md` compilation, Programmable Trust) and 3-layer BigQuery `Rule 0` SQL Trace Evaluation (`AI.GENERATE_TABLE` + Rogan-Gladen debiasing) | `bridge/agent_gym/`, `bridge/telemetry/`, `terraform/modules/telemetry/` |

## Run All Local Surfaces & CI/CD

Credential-free across all 14 hexagonal **Ports, Adapters, and Contracts** (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`, `knowledge_catalog`).

```bash
uv sync && pnpm install
scripts/run-all.sh          # four Bridge Python hosts + four Vite apps; Ctrl-C tears down
```

| Surface | URL | Role |
| :--- | :--- | :--- |
| Enterprise Showcase Console (`/demo`) | http://localhost:8003/demo | 5-scene interactive walkthrough (Banking, Mortgage & Insurance) |
| Live Multimodal Consolidation (`/ebc`) | http://localhost:8003/ebc | Split-screen live PDF extraction + WORM + Agent Gym stage |
| Provider portal | http://localhost:5173 | External party upload & A2UI Path-B host |
| Agent console | http://localhost:5174 | Calling service agent session & turn viewer (backend in `docubridge-examples`) |
| Ops dashboard | http://localhost:5175 | Operator read-model, HITL/escalation queues & RFP board |
| Time-warp | http://localhost:5176 | Virtual SLA clock control (`overdue`, `reminder`, `escalated`) |
| A2A edge | http://localhost:8000 | Agent Card at `/.well-known/agent-card.json` |

Full verification commands and Google Cloud Build CI/CD (`cloudbuild.yaml`):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict contract bridge
uv run pytest
pnpm --filter "!@docubridge/e2e" -r test
terraform -chdir=terraform init -backend=false && terraform -chdir=terraform test
```

## Repository Map

```text
spec/       THE WHAT: requirements (CUJs, standards, demos). Forkable, status-free, protected.
design/     THE HOW: architecture, ports, zones, patterns, agent-gym, harness-evolve. Status-free.
DECISIONS.md architectural decision records (ADR-01 through ADR-15) & milestone history
BANKING.md  5-scene Enterprise Showcase architecture & walkthrough (Banking, Mortgage, Insurance)
docs/       feature catalog, demo click-path, roadmap, local-dev, upstream ADK issues
wiki/evals/ the eval corpus (Address + Benefits oracles), a runtime fixture
contract/   shared Pydantic wire types, the ONLY package consumers import from this repo
bridge/     the reusable core (NEVER imports examples/ or external consumer agents)
examples/   minimal wire-only A2A consumer (consume_bridge.py)
skills/     Agent Skills the Bridge loads at runtime
catalog/    OKF doctype bundles (single source of truth for field schemas and [:SUPERSEDES])
demo/       5-scene Enterprise Showcase Console + /ebc live stage
frontend/   four React/Vite apps (portal, console, dashboard, timewarp) + shared lib + e2e
terraform/  IaC by substrate primitive (standalone or co-located single-bucket mode)
tests/      shared port suite (local + gcp) + enterprise architecture + conformance + perf
scripts/    local run-*.sh hosts + deploy.sh / parity.sh
```

## Documentation Index

| Start here | Why |
| :--- | :--- |
| [BANKING.md](../BANKING.md) | 5-scene Enterprise Showcase (Banking, Mortgage & Insurance), 6-Zone Data Architecture, Agent Gym & BigQuery SQL Eval |
| [demo-click-path.md](demo-click-path.md) | 5-minute live demo click-path, Agent Registry & Gateway walkthrough, and BigQuery SQL evaluation guide |
| [DECISIONS.md](../DECISIONS.md) | Architecture Decision Records (`ADR-01` through `ADR-15`) |
| [design/bridge.md](../design/bridge.md) | One-page mediation root leading to `model.md`, `edges.md`, `knowledge-model.md`, `runtime.md`, and `architecture.md` |
| [spec/cujs.md](../spec/cujs.md) | Critical User Journeys (`CUJ-1` through `CUJ-15`) |
| [features.md](features.md) | Feature catalog across all phases and sprints |
| [consume-over-a2a.md](consume-over-a2a.md) | Downstream A2A consumer contract, supplied folders, and wire types |
| [local-dev.md](local-dev.md) | Port map, showcase runner (`scripts/run-showcase.sh`), and RFP demo host |
| [CLAUDE.md](../CLAUDE.md) | Engineering conventions and hard-won operational lessons |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Contribution conventions, invariants, and verification ladder |

## Core Glossary

- **Exchange:** One A2A context representing a durable multi-turn interaction about a set of documents.
- **Sense A / Sense B:** DocuBridge judges single-document admissibility and reconciles cross-document facts against declared rules (**Sense A**). The calling service agent decides whether the packet is complete or which quote to bind (**Sense B**).
- **Actors:** Five roles take part in an exchange: the **calling agent**, **DocuBridge**, the **document provider**, the **reviewer**, and the **domain expert**; see [design/actors.md](../design/actors.md).
- **Port & Adapter (`Port`):** A managed-service boundary (`bridge/ports/`) with a local adapter and a GCP adapter; shared `@pytest.mark.port` tests verify terminal-outcome parity across both.
- **Calibration:** Schema Refinery evolves the ontology and Agent Gym evolves the skills, promoting only through statistical gates in `bridge/calibration.py`.
- **Ontology vs. Skill:** A bundle in `catalog/doctypes/` defines what a document is and its `[:SUPERSEDES]` precedence; a skill in `skills/` defines how the Bridge collects it.
