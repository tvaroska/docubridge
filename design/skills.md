---
type: atom
up: "knowledge-model.md"
tags: [bridge, skills]
---

# Skills — how the Bridge collects, and demos as configuration

> A bundle in `catalog/doctypes/` says what a document *is* ([ontology](ontology.md)); a skill in `skills/` says how the Bridge *collects* it and how it behaves. Once the primitives are the architecture, every demo is the same machine, configured by **two kinds of skill**. Adding a document type — or a whole demo — is a runtime upload with **no code change and no redeploy**.

Skills are authored in the open **[Agent Skills format](https://agentskills.io)** (the Anthropic-originated standard: a folder with a `SKILL.md` — YAML frontmatter `name` + `description`, plus a Markdown body — optionally bundling `assets/`, `references/`, `scripts/`). The Bridge is a skills host: it installs skill folders and consumes them. Two Bridge-specific *kinds* are distinguished by a `metadata.bridge-kind` field, because one document type is used across many processes and one process consumes many document types:

```
document-type skill               the installable unit for ONE doc kind
    = discovery metadata          name + description on the Agent Card's doctype: tags
    + generated schema            assets/schema.json, rendered from the OKF bundle (never hand-edited)
    + extraction notes            the SKILL.md body + references/ (issuer canonicalization)
    + per-document validation     assets/validation.yaml — rules + which disposition signals the Bridge owns
    + engine keys (declarative)   bridge-extraction-engine / bridge-docai-processor, not read by the Bridge

process skill                     parameterizes an EXCHANGE — a lean catalog
    = exchange pattern            request | negotiate | deliver | collect (a selector, not a definition)
    + candidate doctypes          which doctype skills may appear (the label space)
    + policy                      assets/policy.yaml — thresholds, SLA ladder, retry cap, rfp: block
    + review ownership            bridge-hitl: park | report
    + satisfaction prose          bridge-satisfaction — advisory, read by bridge/satisfaction.py
```

A doctype skill binds to the [extraction port](ports.md) engine-agnostically: the schema rendered from its bundle and the `SKILL.md` notes drive the Gemini request under every engine — including the Document AI engine, whose OCR [pre-pass](extraction.md#document-ai-pre-pass--the-docai-engine) uses the one processor configured for the deployment (`BRIDGE_DOCAI_PROCESSOR`), not a per-doctype key — and the optional per-doctype processor mapping is reserved for a doctype-specific custom extractor; a doctype without one is never refused (its capability envelope). A doctype skill is reusable and pattern-agnostic; a process skill is a **lean catalog** of candidate doctypes plus behaviour (`policy.yaml` thresholds and SLA, `bridge-hitl`) and — per ADR-14 — the **declared collection strategy** (`assets/strategy.yaml`: ordering, counts, cross-document constraints such as distinct issuer or recency, alternatives; built by `P9-4`). The strategy is declarative data evaluated by code, never a workflow DSL and never prose read by a model; it references ontology fields by name. The Bridge evaluates it into a **sufficiency verdict with evidence** on every turn; the completeness *decision* remains the **agent's** (sense B, e.g. `agents/address/gate.py::is_satisfied`), which accepts the verdict or overrides it and alone waives an item. Until `P9-4` lands, the skill carries the strategy as a **prose satisfaction description** (`bridge-satisfaction`) that feeds `bridge/satisfaction.py` for an advisory proposal (see [Collect](collect.md)); afterwards that prose is generated from the strategy. A one-shot extract is a single-doctype process skill. A full **demo implementation** is exactly this configuration plus a thin driver — it consumes the [core Bridge](bridge.md) without changing it, which is why adding a demo is adding configuration, not forking the platform.

## Packaged as Agent Skills

Every skill is one Agent Skills folder. Frontmatter `name` (lowercase, hyphens, ≤64 chars, matches the directory) and `description` (what it does + when to use, ≤1024 chars) are the **discovery surface**; the structured Bridge config lives in `metadata` (string→string) plus bundled `assets/`, and the extraction prompt / process notes are the `SKILL.md` body. `bridge-*` metadata keys are namespaced to avoid collisions per the spec's guidance.

**Doctype skill** — `utility-bill/`:
```
utility-bill/
├── SKILL.md                # frontmatter + extraction notes (body)
├── assets/
│   ├── schema.json         # GENERATED from catalog/doctypes/utility-bill.okf.md (okf_loader --write-schemas)
│   └── validation.yaml     # per-document rules + disposition-signal ownership
└── references/
    └── issuer-canonicalization.md   # optional: issuer-key rules, loaded on demand
```
```markdown
---
name: utility-bill
description: A utility bill (electricity, water, gas) from a service provider. Use as
  proof of address. Extracts and canonicalizes the issuer/company, account-holder name,
  service address, and statement date; validates the bill is recent and shows an address.
license: Apache-2.0
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype address proof-of-address"
  bridge-extraction-engine: gemini  # declarative; the engine is BRIDGE_EXTRACTION_ENGINE
  bridge-docai-processor: ""        # declarative; not read (the OCR pre-pass uses BRIDGE_DOCAI_PROCESSOR)
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the fields defined in `assets/schema.json` from the supplied bill. Canonicalize
the issuer to a stable key (`PowerCo` / `Power Co.` / `PowerCo Ltd` → `power-co`) — see
`references/issuer-canonicalization.md`. Validation rules live in `assets/validation.yaml`.
```

**Process skill** — `address-proof/`:
```
address-proof/
├── SKILL.md                # frontmatter + process notes (body)
└── assets/
    └── policy.yaml         # SLA cadence, max nudges, retry cap, thresholds
```
```markdown
---
name: address-proof
description: Collect proof of address for a party. Use when a servicer's agent needs
  address verification. A bounded Collect over candidate doctypes gov-id and utility-bill;
  the agent owns the completeness decision. The skill carries a prose satisfaction
  description (never a formal rule) the Bridge best-efforts from.
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "gov-id utility-bill"
  bridge-tags: "address collect kyc"
  bridge-policy: assets/policy.yaml
  bridge-hitl: park
  bridge-satisfaction: >-
    Satisfied by one accepted government-issued ID, OR two accepted utility bills from
    two different companies (distinct issuers). Two bills from the same company do not
    satisfy. The completeness decision is the agent's (sense B); this description is
    advisory only.
---

Bounded Collect. The Bridge classifies each arriving doc against the candidate doctypes,
dispositions it, and chases the delta per `assets/policy.yaml`. Completeness (the OR, the
count-to-two, the distinct-issuer check) is the **agent's** — asserted per turn, never here.
```

`bridge-hitl` (`park` | `report`, default `report`; any other value fails the skill load) says who owns a Collector `hitl_review` verdict for the process. `report`: the pending entry rides a terminal turn and the consumer owns review. `park`: the Bridge owns review — on a hybrid edge the turn is handed to the LLM leg, which parks the task on `request_human_review` until an authorized reviewer resolves it ([A2A edge](a2a-edge.md) R3/R6). `address-proof` parks; the benefits processes report.

The remaining optional process keys: `bridge-satisfaction` — the prose satisfaction description (natural language, advisory), read by the Bridge's satisfaction assessor (`bridge/satisfaction.py`, [Collect](collect.md)); `bridge-tags` — space-separated tags advertised on the process's Agent Card skill; `bridge-program-standard` — negotiate processes only: a skill-relative path to the program standard each normalized item is flagged against (e.g. `benefits-procurement`'s `assets/standard.yaml`, read by `bridge/standard.py`).

The `bridge-pattern` value is a **selector over a closed set of built-in flows** (see [patterns](patterns.md)) — the skill *picks* a flow, it never *defines* one; there is no workflow DSL. Industry-agnosticism comes from the skills plus the agent's per-turn decisions, not author-defined flow.

## Doctype field schemas and validation

A doctype's field shape is declared **once**, in its OKF bundle (`catalog/doctypes/<doctype>.okf.md`, [ontology](ontology.md)): field names, types, `required`, closed `enum` vocabularies and row-shaped `tables`. The skill's `assets/schema.json` is a generated rendering of that bundle (`python -m bridge.refinery.okf_loader --write-schemas`), kept in the folder because the registry ships the folder as-is; `tests/test_doctype_consistency.py` holds the two byte-identical, and `bridge/doctypes.py::doctype_schemas` renders from the bundle rather than reading the asset whenever a bundle exists. On the wire the extracted `key_fields` is an open `dict[str, str]`; the schema is what constrains and enforces it — validated on ingest (Path A) and pinned on the extraction request (Path B). Row-shaped data (a carrier rate sheet) is declared under `tables` and rides in the sibling `tables` map (see `design/a2a-consumer.md` § Structured rows); `benefits-quote` (schema v2) declares `rates` and `design`. Two doctype skills:

- **`gov-id`** — required fields `doctype` (enum `gov-id`) plus `key_fields`: `name`, `address`, `doc_number`, `issuing_authority`, `expiry` (ISO date). Validation rules `not_expired` (expiry in the future) and `complete_fields`. (These field names are the ones the eval corpus enforces — see `wiki/evals/address/expected.json`.)
- **`utility-bill`** — required fields `doctype` (enum `utility-bill`), `issuer` (canonicalized), plus `key_fields`: `name` (account holder), `service_address`, `statement_date`. Validation rules `recent_bill` (`window_days: 90`) and `address_present`. Issuer canonicalization maps `PowerCo` / `Power Co.` / `PowerCo Ltd` → `power-co`.

The schema is **read at extraction time** (`doctype_schemas`, through the `skill_registry` port for the installed list), not just at validation time: the Gemini engine renders every installed doctype's declared fields and contrastive descriptions into the prompt and pins their union as the request's structured-output schema ([extraction](extraction.md)). The schema is what the model is constrained by — it is not decorative metadata — and a newly installed doctype skill (with its bundle) changes extraction with no redeploy. What stays in the **skill** rather than the bundle is behaviour: `assets/validation.yaml` carries the per-document rules and which disposition signals the Bridge owns.

Both use the disposition signals `legibility`, `type_match`, and `confidence` as Bridge-authoritative (Sense A); `completeness` is the agent's Sense-B call, best-efforted by the Bridge as advisory (see [disposition](disposition.md)).

## Read-time audit — every skill consumer resolves the registry live

No per-request consumer captures a skill at app build, module import or host start; each reads `BRIDGE_SKILLS_DIR` (through the `skill_registry` port, or `bridge.sla.skills_root()` for the pure loaders) when it is called. `get_adapter` builds a fresh adapter per call, so there is no registry-level cache to invalidate.

| Consumer | Read-time |
|---|---|
| Agent Card (`build_agent_card`, `_live_card`) | every discovery GET |
| `candidate_doctypes_for`, `hitl_mode_for`, `satisfaction_for` (`bridge/edge/skills.py`; the executor and `portal.session`) | every call, via `loaded_skills()` |
| Gemini extraction prompt + pinned response schema (`GeminiExtractor._catalog`) | every `extract`; the prompt is rebuilt only when the catalog content changed. An injected `schemas` catalog is fixed |
| Path-A key-field / tables validation (`bridge/doctypes.py`) | every assertion; the typed-model `lru_cache` is keyed by schema content, never by the registry read |
| `load_sla_policy`, `load_disposition_policy`, `load_rfp_policy`, `load_negotiate_policy`, `load_program_standard` | every call; `skills_dir` defaults to `skills_root()` (explicit argument, else `BRIDGE_SKILLS_DIR`, else the repo `skills/`) |
| `rfp_policy_for` (`bridge/edge/skills.py`) | every call, through the `skill_registry` port (`read_asset` of `SKILL.md` + the policy asset, parsed by the same `rfp_policy_from_docs` validator as `load_rfp_policy`); the candidate doctypes are the narrowed, installed-only label space |
| `RfpBoard` live policy (`live_policy=rfp_policy_for`; the board host and the A2A opener) | per RFP, per read: every `put` / `items` / `get` / `policy_for` of a non-home process re-resolves it; an RFP whose process no longer resolves is skipped on the listing (logged), 404 on a single read, quarantined `process_unavailable` on email intake. The home process's policy is loaded once at host build |
| `bridge.calibration` | operator / test CLI over a `skills_dir` argument, default the repo `skills/`; not a request path |
| Fixture extractor corpus (`bridge/extraction/fixture.py`, `lru_cache`) | the eval corpus file, not a skill |

An exchange already in flight is unaffected by a later install: it holds its own task and ledger, and the new skill only adds a process or a doctype.

## Bad uploads are refused, not propagated

A malformed or half-copied skill folder dropped into the live registry never breaks discovery, never removes an already-advertised skill, and never disturbs an exchange in flight. Two layers of refusal:

- **Structural, per folder.** The shared loader is the strict validator. A folder is refused when its `SKILL.md` has no frontmatter fence; when the frontmatter is not a YAML mapping (or `metadata` is not one); when `name`, `description` or `metadata.bridge-kind` is missing; when `bridge-kind` is neither `process` nor `doctype`; when `name` differs from the folder name; when a process skill's policy file is present but not a YAML mapping; when a doctype skill's schema asset is present but not valid JSON; or when either pointer resolves outside the skill folder. An **absent** policy or schema file stays legitimate (a half-installed skill); only a **present but corrupt** one is refused. At listing, both registry adapters skip a refused folder with a logged reason naming it, and keep listing.
- **Cross-skill, per read.** A process skill's label space is its declared candidate doctypes **that are installed**, in declared order. A missing candidate is omitted from the label space and from the Agent Card's `doctype:` tags until it is installed, so a document of that type is `unsupported_doctype`, never accepted without a schema. A process **none** of whose candidates is installed is not loaded: it is absent from the card, its label space is empty, and it is never dispositioned against another process's label space. A process that declares no candidates is unchanged.

A refused doctype is simply not installed: it cannot take down extraction or Path-A validation for the other doctypes. Repairing a folder (a good re-upload, or fixing the bad file in place) makes it reappear on the next read; nothing is cached.

**Install through the port refuses before writing.** `install_skill` validates the upload in a staging directory with the same loader, after checking every asset path against the real destination, and raises naming the skill if it is malformed. Nothing is written, so a bad re-upload can never overwrite a good skill. Cross-skill state is not checked at install: a process may be installed before its doctypes.

**Residual.** A raw filesystem (or bucket) overwrite of an existing skill with a malformed one bypasses the install check; the loader then refuses the folder and the skill disappears until repaired (fail closed, logged). There is no last-known-good copy.

## Why it matters — progressive disclosure *is* the Agent Card

Skills live in the GCP **Skill Registry** behind a [port](ports.md) (local adapter = a directory of skill folders for dev). The Agent Skills **progressive-disclosure** model maps directly onto the Bridge's discovery surface: an agent host loads only `name` + `description` at startup, and the full `SKILL.md` + assets only when a skill activates. The Bridge's [Agent Card](a2a-edge.md) is exactly that discovery layer — generated from the installed skills' `name`/`description`. So uploading one skill folder regenerates the card and the new capability is live; install several demos' skills and the **one** card advertises them all: a single core speaking many use-cases at once (see [one core, many demos](../spec/demos/demo-suite.md)). This **live skill-add** is the concrete proof of the reusability thesis, and a required demo beat: adding the whole RFP implementation to the running core.

A skill folder is checked by the Bridge's own loader, the same parser on both registry adapters: a malformed folder (no frontmatter fence; a missing `name`, `description` or `metadata.bridge-kind`; an unknown kind; an invalid `bridge-hitl`; a corrupt policy or schema file) is skipped with a logged reason at listing and refused at install ([above](#bad-uploads-are-refused-not-propagated)), and the test suite loads every installed skill. **Policy lives in the skill** (`assets/policy.yaml`), not in code. The disposition thresholds — `resubmit_below: 0.55`, `auto_approve_at: 0.85`, `max_resubmissions: 3` — live in the process skill's `assets/policy.yaml`; code carries built-in defaults used **only** when a key is omitted. The same file carries the SLA cadence (`deadline: 3`, `cadence: 2`, `max_nudges: 2`, in scheduler ticks; ladder overdue → reminder → escalated). A process skill declares **no *formal* satisfaction rule** (no rule language / DSL) — but it **may** carry a **prose satisfaction description** (natural language, `metadata.bridge-satisfaction`) the Bridge best-efforts an advisory assessment from: with `BRIDGE_SATISFACTION=llm` a model reads it on each deterministic collect turn and proposes the outstanding items; off (the default), the outstanding list is per-entry ([Collect](collect.md)). The completeness **decision** is always the app's Sense-B call; the prose description never enforces.

An **emergent-Collect** process skill (`bridge-pattern: collect` with a per-item matrix, e.g. `benefits-rfp`) adds an `rfp:` block to its policy: `tick_seconds` (the wall-clock length of one ladder tick; the ladder anchors on the request's due date) and an optional `item_field` — the extracted `key_fields` key that names an item, default `product_line`. Together with the frontmatter's `bridge-candidate-doctypes` (the label space, never empty) this is everything the core needs to run the process; it names no skill ([core vs. demo hosts](architecture.md#core-vs-demo-hosts)).

## Related
- [the Bridge](bridge.md), [patterns](patterns.md), [ports](ports.md)
