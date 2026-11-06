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

# Knowledge model — what things are, how we collect them, and how Calibration improves fact extraction

> Three layers carry everything the Bridge knows about documents. Keeping them apart is what lets a doctype be added without code, a process be tuned without touching the doctype, and Calibration improve **fact extraction** without ever mutating which documents a case requires (`ADR-15`).

**Layer 1 — the ontology: what a document *is* and how facts are extracted from it.** One OKF v0.2 bundle per doctype in `catalog/doctypes/*.okf.md`, parsed by `bridge/refinery/okf_loader.py` into the wire types in `contract/refinery.py` (`OntologyEntitySpec`, `OntologyFieldSpec`, `OntologyEdge`, grouped into an immutable `OntologyRevision` persisted via `Port.knowledge_catalog`). The bundle is canonical for field names, types, `required`, closed `enum` vocabularies, row-shaped `tables`, the three-part contrastive descriptions (`what_it_is`, `where_to_read`, `distinguish_from`), and the graph edges between doctypes (`[:SUPERSEDES]`, `[:REQUIRES_CORROBORATION]`, `[:SPLIT_FROM]`). A doctype skill's `assets/schema.json` is a **generated copy** of its bundle (`python -m bridge.refinery.okf_loader --write-schemas`); `bridge/doctypes.py::doctype_schemas` renders from the bundle and never reads the asset when a bundle exists. **Layer 2 — skills: how the Bridge *collects* it and behaves.** A process skill (`skills/<process>/`) selects the exchange pattern, names its candidate doctypes (the label space), carries `assets/policy.yaml` (disposition thresholds, SLA ladder, retry cap), static `assets/strategy.yaml` (declared collection strategy), `bridge-hitl` (who owns a review), and `bridge-satisfaction` prose. A doctype skill (`skills/<doctype>/`) is the installable unit the registry ships: `SKILL.md` discovery metadata whose `bridge-schema` points at the generated `assets/schema.json`, plus `assets/validation.yaml` (per-document rules and which disposition signals the Bridge owns). **Layer 3 — the application agent's sense-B decision.** Which documents are needed, acceptable substitutes, or waivable is decided by the calling agent + static `strategy.yaml`; **DocuBridge never learns collection sufficiency or document waivers (`ADR-15`)**.

The invariants: a fact lives in exactly one layer — fields, contrastive descriptions, and fact-precedence edges in the bundle, thresholds and static collection strategy in the process skill, waivers and business judgment in the calling agent; `bridge/` names no installed skill or doctype; the extraction prompt and the Path-A validator read the **same** schema; `fields.doctype` stays a free string so an out-of-label-space document remains reportable; and every learning loop writes a **new revision** with lineage (`OntologyRevision` or `SkillRevision`) that improves **fact extraction, Stage-B deterministic derivations (`FieldDerivation`), and cross-document fact reconciliation**, never document-set requirements:

| Loop | Changes | Never Changes | Through |
|---|---|---|---|
| Schema Refinery | layer 1 — subtype granularity (`[:SPLIT_FROM]`), contrastive field descriptions, `[:SUPERSEDES]` / `[:REQUIRES_CORROBORATION]` edges | required document checklist, acceptable substitutes, or waivers | taxonomy / description assays, 3-tier gate, promoted `OntologyRevision`s in `Port.knowledge_catalog` |
| Agent Gym | layer 1 & 2 — `EXTRACTION_HINT`s, `ENTITY_ALIAS` rules, Stage-B `FieldDerivation` triggers, per-slice extraction confidence thresholds | `strategy.yaml` sufficiency rules or waiver authority | `TribalKnowledgeRule` distillation, `ProgrammableTrustPolicy`, `compile_rules_to_skill` → `SkillRevision` |
| two-sided oversight | layer 3 is **measured**, not learned by the Bridge | collection requirements | `oversight_rsr_score`, spot-checks, reviewer calibration |

## Read next

- [ontology](ontology.md) — what the OKF grammar is, how a bundle is parsed and consumed, and how the consistency test holds the generated schema to it.
- [skills](skills.md) — what a process skill and a doctype skill declare, how they are packaged, read live and refused when malformed.
- [extraction](extraction.md) — which engines sit behind the extraction port, how the prompt is built from the ontology slice, and how large and packaged documents are handled.
- [Schema Refinery](schema-refinery.md) — how the ontology is evaluated and evolved: assays, the 3-tier gate, snapshot isolation and lineage.
- [Agent Gym & Programmable Trust](agent-gym.md) — how single-example expert fixes become rules, how autonomy is dialled per slice, and how oversight is kept honest.
- [Agent Gym × Harness Evolve](harness-evolve.md) — how accumulated rules are compiled into a skill revision and how extraction tiers evolve.
