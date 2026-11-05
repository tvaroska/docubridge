---
type: area
up: "bridge.md"
down:
  - "ontology.md"
  - "skills.md"
  - "extraction.md"
  - "schema-refinery.md"
  - "agent-gym.md"
  - "harness-evolve.md"
tags: [bridge, knowledge, ontology, skills, learning]
---

# Knowledge model — what things are, how we collect them, how Calibration improves both

> Three layers carry everything the Bridge knows about documents. Keeping them apart is what lets a doctype be added without code, a process be tuned without touching the doctype, and a learning loop change exactly one layer at a time.

**Layer 1 — the ontology: what a document *is*.** One OKF v0.2 bundle per doctype in `catalog/doctypes/*.okf.md`, parsed by `bridge/refinery/okf_loader.py` into the wire types in `contract/refinery.py` (`OntologyEntitySpec`, `OntologyFieldSpec`, `OntologyEdge`, grouped into an immutable `OntologyRevision`). The bundle is canonical for field names, types, `required`, closed `enum` vocabularies, row-shaped `tables`, the three-part contrastive descriptions and the graph edges between doctypes. A doctype skill's `assets/schema.json` is a **generated copy** of its bundle (`python -m bridge.refinery.okf_loader --write-schemas`); `bridge/doctypes.py::doctype_schemas` renders from the bundle and never reads the asset when a bundle exists. **Layer 2 — skills: how the Bridge *collects* it and behaves.** A process skill (`skills/<process>/`) selects the exchange pattern, names its candidate doctypes (the label space), carries `assets/policy.yaml` (disposition thresholds, SLA ladder, retry cap), `bridge-hitl` (who owns a review) and an advisory `bridge-satisfaction` prose description. A doctype skill (`skills/<doctype>/`) is the installable unit the registry ships: `SKILL.md` discovery metadata whose `bridge-schema` points at the generated `assets/schema.json`, plus `assets/validation.yaml` (per-document rules and which disposition signals the Bridge owns). **Layer 3 — the application agent's sense-B decision.** Completeness of the set, ordering, and cross-document constraints such as *two bills from distinct issuers* are decided by the calling agent, today as Python in that agent (`agents/address/gate.py::is_satisfied`); the Bridge proposes from the layer-2 prose but never enforces.

The invariants: a fact lives in exactly one layer — fields and types in the bundle, thresholds and policy in the process skill, completeness in the agent; `bridge/` names no installed skill or doctype (the loaders discover them); the extraction prompt and the Path-A validator read the **same** schema, so they cannot disagree; `fields.doctype` stays a free string so an out-of-label-space document remains reportable; and every learning loop writes a **new revision** with lineage (an `OntologyRevision` or a `SkillRevision`) rather than mutating in place. Which layer each loop changes:

| Loop | Changes | Through |
|---|---|---|
| Schema Refinery | layer 1 — entity taxonomy, contrastive descriptions, edges, field cardinality | taxonomy / description assays, the 3-tier gate, promoted `OntologyRevision`s with `[:SPLIT_FROM]` / `[:MERGED_INTO]` lineage |
| Agent Gym | layer 2 — thresholds, trust policies, `EXTRACTION_HINT`s folded into a doctype skill's `SKILL.md` | `TribalKnowledgeRule` distillation, `ProgrammableTrustPolicy`, `compile_rules_to_skill` → a `SkillRevision` |
| two-sided oversight | layer 3 is **measured**, not learned by the Bridge | `oversight_rsr_score`, spot-checks, reviewer calibration |

Where the boundary between a declared strategy and the layer-2 prose should sit is an open design question: see `docs/roadmap.md`.

## Read next

- [ontology](ontology.md) — what the OKF grammar is, how a bundle is parsed and consumed, and how the consistency test holds the generated schema to it.
- [skills](skills.md) — what a process skill and a doctype skill declare, how they are packaged, read live and refused when malformed.
- [extraction](extraction.md) — which engines sit behind the extraction port, how the prompt is built from the ontology slice, and how large and packaged documents are handled.
- [Schema Refinery](schema-refinery.md) — how the ontology is evaluated and evolved: assays, the 3-tier gate, snapshot isolation and lineage.
- [Agent Gym & Programmable Trust](agent-gym.md) — how single-example expert fixes become rules, how autonomy is dialled per slice, and how oversight is kept honest.
- [Agent Gym × Harness Evolve](harness-evolve.md) — how accumulated rules are compiled into a skill revision and how extraction tiers evolve.
