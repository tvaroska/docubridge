---
type: root
down:
  - "model.md"
  - "edges.md"
  - "knowledge-model.md"
  - "runtime.md"
  - "architecture.md"
tags: [bridge]
---

# A2A Document Bridge

> Collecting documents from the outside world is slow, messy, and human-paced. The Bridge **manages that process** so a servicer's agent never touches it — it asks once and receives documentation already gathered, chased, normalized, and ready to act on.

**Why it exists.** An expert doesn't chase paperwork — an assistant does. The assistant emails the five carriers, re-reads the one reply that came back as a photo, notices two are in different currencies, nudges the one who's late, and only walks in when there's **one clean view** to decide on. The Bridge is that assistant for a servicer's agent: the internal agent stays precise and impatient; the Bridge absorbs a world that is neither. Extraction (PDF → JSON) is already covered well by dedicated services — the durable value is **mediation**: owning the multi-turn, multi-party, multi-format relationship end to end. Extraction itself sits behind a [port](ports.md) with swappable engines — Gemini or Document AI, chosen by config — because work a purpose-built service already does well is exactly the thing you should delegate and be able to swap.

**How it works.** [A2A](patterns.md) is the protocol the exchanges run on — a servicer's agent and the Bridge (and agent counterparties) speak it natively, so a solicitation, a chase, and a delivery are all just tracked tasks in one shared vocabulary. Parties who *aren't* agents still get folded into the same model through the [human-facing edge](human-edge.md). The servicer's agent sees one uniform interface; who's on the other end is the Bridge's problem.

**Who it's for.** Two audiences, in order. First, teams **evaluating the Gemini platform** — the Bridge is the flagship showcase of what the platform makes possible end to end: Agent Runtime, Skill Registry, Memory Bank, Agent Gateway, Agent Identity, A2A and A2UI wired into one real, deployed system rather than a toy. Second, servicers who could **adopt it as a product** — a managed mediation service their agent calls once and gets clean, ready-to-act documentation back. The ports, the managed-GCP wiring, and the trust boundary *are* the demonstration; the extraction inside is delegated to dedicated services.

**Core vs. demos.** The **Bridge core** is the independent, reusable project — the platform showcase itself. Each demo is a **self-contained implementation** — skills plus a thin driver that consume the core, with **no change to the core**. That separation *is* the reusability claim (where demo-host glue lives: [core vs. demo hosts](architecture.md#core-vs-demo-hosts)). The demos are separate implementations, but they run against **one deployed core at once** — a single Agent Card and one dashboard serve them all together (see [one core, many demos](../spec/demos/demo-suite.md)).

**The demos.** Three demo implementations run on the one deployed core: **Address** (the pull spine carrying a *bounded* [Collect](collect.md): prove an address with either **one government ID** or **two bills from different companies**), **Benefits** (the full fan-out → normalize → compare → negotiate → bind arc), and **RFP** (a different industry and the *full, emergent* Collect at scale, added *live* with no redeploy). See [the demo implementations](../spec/demos/demo-suite.md).

**Scope.** Core motion is **solicited/pull** — the servicer asks first and the Bridge mints the exchange's context identifier at request time. **Outbound is first-class** — the Bridge also chases, delivers, and dials counterparties ([outbound direction](outbound.md)) — but the pull spine is the shape the system is built around. Cold, *unsolicited* push (the [Extract](patterns.md) pattern) is out of scope: no edge accepts a document without an open exchange. A solicited reply by **email** is not cold push: it arrives through [email intake](email-intake.md), correlated to its open exchange by token.

## Read next

Five areas, each one page, each answering one question before it links deeper:

- [model](model.md) — **what the Bridge is**: exchange / task / session / party, the closed pattern set, disposition, artifacts, the outbound direction.
- [edges](edges.md) — **how you talk to it**: the A2A server and its consumer, the service-agent shape, the human edge, email as a transport, the RFP read API.
- [knowledge model](knowledge-model.md) — **what things are vs. how we collect them vs. how it learns**: ontology, skills, extraction, Schema Refinery, Agent Gym, Harness Evolve.
- [runtime](runtime.md) — **how it runs**: ADK, the 13 ports, the two-zone trust boundary, proactive follow-up, deploying the resumable loop.
- [architecture](architecture.md) — **where things live**: the repo map and the boundaries between its parts.

Requirements live in [`spec/`](../spec/cujs.md) (the CUJ catalog, the [standardization rings](../spec/standards.md), the [demo suite](../spec/demos/demo-suite.md), [open questions](../spec/open-questions.md)).

> Planning artifacts live **outside `design/`** (design is `design/`'s job; sequencing is not): the feature catalog at `docs/features.md` and the milestones + live status at `docs/roadmap.md` (an ephemeral root `TODO.md` may be created during an active sprint). Forkable requirements live in `spec/`.
