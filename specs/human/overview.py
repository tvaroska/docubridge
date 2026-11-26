"""Plain-English Overview tab data for the DocuBridge /human specification."""

from __future__ import annotations

from specs.human.contract import (
    ActorStageSpec,
    KpiSpec,
    PitchLayerSpec,
    PrincipleSpec,
    ThemeDiffSpec,
)

PITCH_LAYERS: tuple[PitchLayerSpec, ...] = (
    PitchLayerSpec(
        label="Edges & Intake Gates",
        text=(
            "Hybrid A2A JSON-RPC server + Path-B A2UI portal validate structured JSON "
            "at $0 (Path A) or extract PDFs via Gemini + DocAI OCR (Path B), then enforce "
            "a deterministic 4-signal code gate."
        ),
    ),
    PitchLayerSpec(
        label="Code-Only Core",
        text=(
            "7-year WORM Cloud Storage vault, Decimal override math (ADR-16 effective-date "
            "precedence), bi-temporal SQL ledger, and OKF knowledge graph."
        ),
    ),
    PitchLayerSpec(
        label="Human Review & Calibration",
        text=(
            "A2UI Action Inbox captures RFC 6902 expert fixes (RSR >= 0.80); Agent Gym "
            "& Schema Refinery improve fact extraction only (never document requirements)."
        ),
    ),
    PitchLayerSpec(
        label="14 Swappable Ports",
        text=(
            "All 14 infrastructure boundaries swap cleanly between credential-free local "
            "adapters and GCP services with shared parity tests."
        ),
    ),
)

PRINCIPLES: tuple[PrincipleSpec, ...] = (
    PrincipleSpec(
        title="1. Evidence Left (DocuBridge), Judgment Right (Callers)",
        body=(
            "DocuBridge decides per-document validity (Sense A) and evaluates static "
            "strategy sufficiency with evidence (Sense A'), while calling agents own what "
            "is needed, waivers, and 'done' (Sense B)."
        ),
    ),
    PrincipleSpec(
        title="2. Models Route, Deterministic Code Decides",
        body=(
            "before_tool_callback (pin_gate_args) pins extraction signals so LlmAgent "
            "never mints acceptance. Financial sums run in Decimal SQL ordered by "
            "effective_from DESC then source_priority."
        ),
    ),
    PrincipleSpec(
        title="3. Calibration Evolves Fact Extraction Only (ADR-15)",
        body=(
            "Schema Refinery and Agent Gym refine doctype descriptions, Stage-B derivations, "
            "and precedence edges—gated against negative flips—without ever mutating "
            "document checklists."
        ),
    ),
)

KPIS: tuple[KpiSpec, ...] = (
    KpiSpec(
        label="Recorded Decisions",
        value="16 ADRs",
        caption="ADR-01 to ADR-16 · Hexagonal ports, WORM vault, OKF graph & precedence",
    ),
    KpiSpec(
        label="Abstraction Layers",
        value="5 -> 16 -> 14 Ports",
        caption="5 top-level actors unpack into 16 subsystems backed by 14 local/GCP ports",
    ),
    KpiSpec(
        label="Verified Journeys",
        value="15 CUJs · 3+ FSI",
        caption="2,017 offline tests pass, 57 skipped · 0 live noisy-PDF benchmarks (N=0)",
    ),
    KpiSpec(
        label="2026-11 Reviews",
        value="5.0-6.2 / 10",
        caption="P13 (15) & P14 (12) fixed locally on main; P15 (8) & P16 (10) open",
    ),
)

STAGES: tuple[ActorStageSpec, ...] = (
    ActorStageSpec(
        stage="1. External Parties",
        role=(
            "Carriers, suppliers, and customers answering chases with structured data, "
            "PDFs, or emails."
        ),
        contents=(
            "Counterparty Agents (ADK & LangGraph), Tracked Email Replies, "
            "Human Upload Portal"
        ),
        boundary="Met at their digital maturity; normalized into one internal exchange model.",
    ),
    ActorStageSpec(
        stage="2. DocuBridge Agent",
        role=(
            "Single mediation core that chases files, enforces code gates, reconciles "
            "versions, and locks bytes."
        ),
        contents=(
            "Zero-Trust Front Door, Intake & 4-Signal Gate, Decimal Override & "
            "Sufficiency, Vault & SQL Ledger, Proactive Chase"
        ),
        boundary=(
            "Models never self-approve; infrastructure swaps across 14 Ports, Adapters "
            "and Contracts."
        ),
    ),
    ActorStageSpec(
        stage="3. Use-Case Agents",
        role=(
            "Internal service agents requesting document packets and deciding business "
            "completion."
        ),
        contents=(
            "Bounded Address Collect, Benefits Fan-Out & Negotiate, "
            "Emergent RFP & Loan Underwriting"
        ),
        boundary="Connect strictly over contract/ wire types; sole owners of waivers and 'done'.",
    ),
    ActorStageSpec(
        stage="4. Human Reviewers",
        role=(
            "Underwriters and ops specialists resolving flagged scans side-by-side "
            "with page proof."
        ),
        contents=(
            "Page-Pinned Action Inbox (A2UI + RFC 6902), Anti-Rubber-Stamp (RSR) Guard"
        ),
        boundary=(
            "Role limits enforced in code; rubber-stamps are blocked from Day-2 training."
        ),
    ),
    ActorStageSpec(
        stage="5. Day-2 Learning Loop",
        role=(
            "Turns verified human fixes and OTel traffic into sharper extraction and "
            "ontologies."
        ),
        contents=(
            "Agent Gym (Skills), Schema Refinery (OKF), 22-Step OTel & BQ SQL Trace Eval"
        ),
        boundary=(
            "Fenced by ADR-15 to improve extraction/derivations only—never caller policy."
        ),
    ),
)

THEME_DIFFS: tuple[ThemeDiffSpec, ...] = (
    ThemeDiffSpec(
        theme="Decision Ownership (Sense A / A' / B)",
        before=(
            "Sufficiency rules buried in per-consumer Python or prose prompts; "
            "inbox reviewers waived missing docs."
        ),
        after=(
            "DocuBridge computes static strategy.yaml verdicts with evidence (Sense A'); "
            "calling agents alone waive and mint done (ADR-14, ADR-15)."
        ),
    ),
    ThemeDiffSpec(
        theme="Calibration Scope & Honesty",
        before=(
            "Agent Gym defaulted auto_promote=True & citation_verified=True; "
            "Refinery returned constant 38/40 alpha/AC2 scores."
        ),
        after=(
            "Calibration edits fact extraction/derivations only; offline perturbation says "
            "'not evaluated' & fail-closed P13 checks pin gate inputs."
        ),
    ),
    ThemeDiffSpec(
        theme="Override & Consolidation Math",
        before=(
            "source_priority beat effective_from (2024 endorsement beat 2026 renewal); "
            "table rows keyed by index; greedy regex money parser."
        ),
        after=(
            "effective_from DESC breaks before source_priority (ADR-16); whole-table "
            "replacement per winner; fullmatch Decimal currency grammar."
        ),
    ),
    ThemeDiffSpec(
        theme="Ontology vs Process Skills",
        before=(
            "Doctypes overloaded flat SKILL.md folders with hardcoded Python priority "
            "branches."
        ),
        after=(
            "OKF v0.2 catalog/doctypes/*.okf.md is canonical (schemas generated); Zone 6 "
            "Port #14 SqlKnowledgeCatalog persists TBox revisions + edges."
        ),
    ),
    ThemeDiffSpec(
        theme="Hexagonal Ports & SQL Runtime",
        before=(
            "Mix of direct env lookups, duplicate async loops per store, and "
            "repo-local example code inside core."
        ),
        after=(
            "14 Ports, Adapters and Contracts (local/gcp) multiplexed over refcounted "
            "DsnSqlRuntime; standalone empty core separated from docubridge-examples."
        ),
    ),
    ThemeDiffSpec(
        theme="Deployed Auth & Supply Chain",
        before=(
            "serve_container lacked AuthBackend; IAP lacked audience/ES256 checks; "
            "default deploy scripts opened ingress."
        ),
        after=(
            "P14 wired EdgeAuthBackend + principal-scoped tasks/artifacts, scoped viewer "
            "cookie, OIDC allowlists, ES256 IAP, gs:// allowlist & Secret Manager."
        ),
    ),
)
