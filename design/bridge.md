---
type: root
down:
  - "aggregate-model.md"
  - "edges.md"
  - "patterns.md"
  - "adk.md"
  - "../spec/standards.md"
  - "artifacts.md"
  - "skills.md"
  - "gcp-substrate.md"
  - "outbound.md"
  - "agent-gym.md"
  - "harness-evolve.md"
  - "schema-refinery.md"
  - "../spec/demos/demo-suite.md"
  - "../spec/cujs.md"
  - "architecture.md"
tags: [bridge]
---

# A2A Document Bridge

> Collecting documents from the outside world is slow, messy, and human-paced. The Bridge **manages that process** so a servicer's agent never touches it — it asks once and receives documentation already gathered, chased, normalized, and ready to act on.

**Why it exists.** An expert doesn't chase paperwork — an assistant does. The assistant emails the five carriers, re-reads the one reply that came back as a photo, notices two are in different currencies, nudges the one who's late, and only walks in when there's **one clean view** to decide on. The Bridge is that assistant for a servicer's agent: the internal agent stays precise and impatient; the Bridge absorbs a world that is neither. Extraction (PDF → JSON) is already covered well by dedicated services — the durable value is **mediation**: owning the multi-turn, multi-party, multi-format relationship end to end. Extraction itself sits behind a [port](ports.md) with swappable engines — Gemini or Document AI, chosen by config — because work a purpose-built service already does well is exactly the thing you should delegate and be able to swap.

**How it works.** [A2A](patterns.md) is the protocol the exchanges run on — a servicer's agent and the Bridge (and agent counterparties) speak it natively, so a solicitation, a chase, and a delivery are all just tracked tasks in one shared vocabulary. Parties who *aren't* agents still get folded into the same model through the human-facing edge. The servicer's agent sees one uniform interface; who's on the other end is the Bridge's problem.

**Who it's for.** Two audiences, in order. First, teams **evaluating the Gemini platform** — the Bridge is the flagship showcase of what the platform makes possible end to end: Agent Runtime, Skill Registry, Memory Bank, Agent Gateway, Agent Identity, A2A and A2UI wired into one real, deployed system rather than a toy. Second, servicers who could **adopt it as a product** — a managed mediation service their agent calls once and gets clean, ready-to-act documentation back. The ports, the managed-GCP wiring, and the trust boundary *are* the demonstration; the extraction inside is delegated to dedicated services.

**Core vs. demos.** The **Bridge core** is the independent, reusable project — the platform showcase itself. Each demo is a **self-contained implementation** — skills plus a thin driver that consume the core, with **no change to the core**. That separation *is* the reusability claim (where demo-host glue lives: [core vs. demo hosts](architecture.md#core-vs-demo-hosts)). The demos are separate implementations, but they run against **one deployed core at once** — a single Agent Card and one dashboard serve them all together (see [one core, many demos](../spec/demos/demo-suite.md)).

**The demos.** Three demo implementations run on the one deployed core: **Address** (the pull spine carrying a *bounded* [Collect](collect.md): prove an address with either **one government ID** or **two bills from different companies**), **Benefits** (the full fan-out → normalize → compare → negotiate → bind arc), and **RFP** (a different industry and the *full, emergent* Collect at scale, added *live* with no redeploy). See [the demo implementations](../spec/demos/demo-suite.md).

**Scope.** Core motion is **solicited/pull** — the servicer asks first and the Bridge mints the exchange's context identifier at request time. **Outbound is first-class** — the Bridge also chases, delivers, and dials counterparties ([outbound direction](outbound.md)) — but the pull spine is the shape the system is built around. Cold, *unsolicited* push (the [Extract](patterns.md) pattern) is out of scope: no edge accepts a document without an open exchange. A solicited reply by **email** is not cold push: it arrives through [email intake](email-intake.md), correlated to its open exchange by token.

## Read next

**Start here — the model**
- [aggregate model](aggregate-model.md) — exchange / task / session / party; read this first

**How it's built** (dive as you like)
- [running on Google ADK](adk.md) — the platform principle: native constructs, as standard as possible
- [standardization rings](../spec/standards.md) — which layer bends when standard and custom conflict; the **Ring 1** wire standard (D1–D5)
- [documents as artifacts](artifacts.md) — versioned ADK artifacts, moved across the wire by reference
- [two edges](edges.md) — A2A for agents, A2UI for humans
- [exchange patterns](patterns.md) — Request / Negotiate / Deliver / Collect
- [long-running collection](long-running.md) — the durable A2A task; days/weeks with no held connection
- [A2A consumer](a2a-consumer.md) — how an agent calls the Bridge: `RemoteA2aAgent`, `input-required` pause, status updates
- [service-agent reference architecture](service-agent-architecture.md) — the reusable whole-agent shape: `Workflow` graph (collect · gate · present) on one durable session
- [skills](skills.md) — demos as configuration
- [GCP substrate](gcp-substrate.md) — ports, network zones, deploy, proactive engine
- [Agent Gym & Programmable Trust](agent-gym.md) — continuous learning, graduated autonomy, and two-sided human-oversight calibration literature
- [Agent Gym × Harness Evolve & Multi-Tier Extraction](harness-evolve.md) — combining daytime SME capture with nightly `SKILL.md` compilation and Document AI Standard/Custom Extractor evolution
- [Schema Refinery & Ontology-Driven Document Types](schema-refinery.md) — replacing flat `doctype` skills with OKF-backed Knowledge Catalog entries, an AlloyDB + BigQuery Knowledge Graph, automated taxonomy/description evaluation, snapshot isolation, and cross-revision lineage

**Context**
- [demo implementations](../spec/demos/demo-suite.md) — the three demos on one deployed core
- [CUJ catalog](../spec/cujs.md) — the canonical user journeys, mapped to patterns and demos
- [outbound direction](outbound.md) — chases, delivery, and dialing counterparties

**Layout**
- [project layout](architecture.md) — the repo map and the boundaries between its parts
- [open questions](../spec/open-questions.md) — what's still genuinely undecided

> Planning artifacts live **outside `design/`** (design is `design/`'s job; sequencing is not): the feature catalog at `docs/features.md` and the milestones + live status at `docs/roadmap.md` (an ephemeral root `TODO.md` may be created during an active sprint). Forkable requirements live in `spec/`.
