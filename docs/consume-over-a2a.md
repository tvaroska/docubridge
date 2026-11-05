# Consuming DocuBridge over A2A

DocuBridge is the base other industry showcases build on. A downstream showcase does
**not** fork this repo or import `bridge/` as a Python package: it deploys the core
unchanged, points it at its own content via environment variables, and talks to it as
an A2A agent through `/.well-known/agent-card.json`. This page is the contract that
makes that work. The conformance test is
[`tests/test_a2a_consumer_contract.py`](../tests/test_a2a_consumer_contract.py).

## What a downstream showcase supplies

| Folder | Contents | How the core finds it |
|---|---|---|
| `skills/` | One folder per Agent Skill: `SKILL.md` (+ `assets/`). **Process** skills (`bridge-kind: process`) name their `bridge-candidate-doctypes`; **doctype** skills carry `assets/schema.json`, `validation.yaml`, `policy.yaml`. See [`design/skills.md`](../design/skills.md). | `BRIDGE_SKILLS_DIR` (local) or `BRIDGE_SKILLS_BUCKET` (GCS, `BRIDGE_PORT_MODE=gcp`). Read at call time — a folder added after start is seen on the next card fetch. |
| `catalog/doctypes/` | One `<doctype>.okf.md` bundle per doctype: entity scope, contrastive field descriptions, ontology edges, enums and tables. The bundle is canonical; `schema.json` is generated from it (`python -m bridge.refinery.okf_loader --write-schemas`). | `BRIDGE_OKF_DOCTYPES_DIR` |
| `scenarios/` | One `scenarios/<id>/scenario.yaml` per showcase scenario (`contract/scenario.py::ScenarioManifest`): display copy, entity ids, seed facts, optional staged beats and declarative `pdf/*.yaml` assets. The folder name must equal `id`. | `BRIDGE_SCENARIOS_DIR` |
| console files (optional) | `enterprise-demo.html/.js/.css`, `ebc-live.*`, `ebc-favicon.svg` if the showcase ships its own `/demo` and `/ebc` consoles. | `BRIDGE_DEMO_STATIC_DIR` |

A process skill with none of its candidate doctypes installed is not advertised; a
malformed skill folder is skipped, never fatal.

## Per-deployment knobs

All are listed with defaults in [`.env.example`](../.env.example); none is required for
a local run of this repo.

| Variable | Meaning | Default |
|---|---|---|
| `BRIDGE_SKILLS_DIR` | Skill registry root (local adapter). | `<repo>/skills` |
| `BRIDGE_SKILLS_BUCKET` | Skill registry bucket (GCP adapter). | unset |
| `BRIDGE_OKF_DOCTYPES_DIR` | OKF doctype bundles. | `<repo>/catalog/doctypes` |
| `BRIDGE_SCENARIOS_DIR` | Scenario manifests root. | `<repo>/scenarios` |
| `BRIDGE_DEMO_STATIC_DIR` | Static root for `/demo*` and `/ebc*` (`bridge.showcase.loader.demo_static_root`). | `<repo>/demo` |
| `BRIDGE_DEFAULT_DEMO_DOMAIN` | Domain `/demo` opens in when no `?domain=` is given (`banking`, `mortgage`, `insurance`). | unset → `banking` |
| `BRIDGE_PUBLIC_URL` | Origin the Agent Card advertises; must equal the origin callers fetch the card from (ADK same-origin check). | `http://localhost:8000/` |

None of these is baked in at import time: skills, doctype bundles and the static root
are read per call, scenarios when the dashboard host builds its app — so one container
image serves any showcase.

## The Agent Card

Served at `GET /.well-known/agent-card.json`, built by
[`bridge/edge/card.py`](../bridge/edge/card.py) (`build_agent_card` →
`agent_card_from_skills`). A caller may rely on:

- `skills[]` — exactly one entry per **installed process skill**; `id` is the skill
  folder name and is the value to send as `CollectRequest.process`. Doctype skills
  are not separate entries; they appear as `doctype:<name>` tags on their process.
- `skills[].inputModes` / `defaultInputModes` — `application/json` (a structured
  `CollectRequest`) and `text/plain` (free-text intake, routed at the edge).
- `defaultOutputModes` — `application/json` only.
- `capabilities.streaming` and `capabilities.pushNotifications` — both `true`.
- `supportedInterfaces[0]` — JSON-RPC at the card `url` (A2A protocol 1.0).

`name`, `description` and `version` are informational and may change.

## Wire types for one Collect round-trip

Everything on the wire is defined in [`contract/wire.py`](../contract/wire.py) and
[`contract/enums.py`](../contract/enums.py); `contract/` is the only package a
consumer imports from this repo.

1. **Send** an A2A `Message` (`message/send` or `message/stream`) whose parts are:
   - one data part = `CollectRequest.model_dump(mode="json")` with part metadata
     `{"kind": "collect_request", "contract_version": CONTRACT_VERSION}`. Required:
     `process`; optional: `party`, `context_id` (reuse to continue an exchange),
     `message`, `requirements`, `due_at`.
   - one **by-reference** file part per document (`url`, with metadata `doc_id`).
     Inline bytes are rejected.
2. **Read the task.** `COMPLETED` means the turn is terminal; `INPUT_REQUIRED` means
   the Bridge parked (human review, escalation, open RFP) and the same task resumes
   later — treat it as a pause, not a failure. Status updates carry a human-readable
   `status.message`.
3. **Decode** the artifact data part tagged `metadata.kind == "exchange_turn"` as
   `ExchangeTurn` (select by tag, never by index). `status.entries[]` is a
   `LedgerEntry` per document: `doc_id`, `doctype`, `disposition`
   (`accepted` / `rejected` / `pending`), `gate`, `reason_code`, `key_fields`
   (money and dates stay text), `field_reviews`, provenance (`source_anchors`,
   `vault_uri`). `status.outstanding` lists what is still missing. Completeness
   ("is the set done") is the caller's decision, not the Bridge's.

## Minimal external consumer

[`examples/a2a_consumer/consume_bridge.py`](../examples/a2a_consumer/consume_bridge.py)
does exactly the three steps above with `a2a-sdk` and `contract` only:

```bash
scripts/run-core.sh &    # or any deployed Bridge origin
uv run python examples/a2a_consumer/consume_bridge.py http://localhost:8000/ \
    --process address-proof --doc gov-id-clean=images/gov-id-clean.svg
```

An ADK agent uses `RemoteA2aAgent` pointed at the same card instead of a hand-rolled
client; see [`design/a2a-consumer.md`](../design/a2a-consumer.md).

## The `/demo` surface

The Ops Dashboard host (`bridge.dashboard.server:build_dashboard_app`) serves:

- `GET /v1/demo/scenarios` — `{"scenarios": [...]}`, the manifests found under
  `BRIDGE_SCENARIOS_DIR` in `(order, id)` order, each as `ScenarioManifest.summary()`.
- `GET|POST /v1/demo/scenarios/{id}/{state,advance,reset}`, `POST /v1/demo/scenarios/seed`.
- `GET /demo` (alias `/console`) and `GET /ebc` — the console files from
  `BRIDGE_DEMO_STATIC_DIR`; `404` when a file is absent.

## What the conformance test proves

[`tests/test_a2a_consumer_contract.py`](../tests/test_a2a_consumer_contract.py) builds
a throwaway tree from one scenario folder plus only the skills and bundles it
references, points all five knobs at it, and checks: the card lists exactly that
process skill, one Collect over the fixture extraction engine reaches a terminal
`accepted` entry, `GET /v1/demo/scenarios` lists exactly that scenario, `/demo` serves,
and the example consumer prints the disposition against the same app.
