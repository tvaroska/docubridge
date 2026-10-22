# Architecture Decision Records (ADR)

This document summarizes the foundational architectural decisions that shape **DocuBridge**, an enterprise Agent-to-Agent (A2A) document collection, verification, and governance platform built on Google ADK (`google-adk`), the A2A v0.3 wire protocol (`a2a-sdk`), and the Gemini platform (`gemini-3.8-flash` and `gemini-4-argon`).

---

## ADR-01: Eight-Seam Dual-Mode Infrastructure (`local` vs. `gcp`)

- **Context:** Enterprise document workflows require cloud-native durability and security in production (Cloud Run, Firestore, Cloud Storage, AlloyDB/Cloud SQL, Document AI, Cloud Tasks, and Agent Engine Sessions/Memory), but developers and CI pipelines need sub-second, deterministic execution without cloud credentials.
- **Decision:** Factor all external infrastructure dependencies behind eight explicit seam protocols (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, plus `email_ingress` and `vault`). Every seam provides a deterministic `local` implementation (`bridge/seams/local/`) and a production `gcp` implementation (`bridge/seams/gcp/`).
- **Consequence:** Every scenario in the test suite runs identically against `local` seams in milliseconds and against `gcp` seams in staging/production via `@pytest.mark.seam(...)`.

---

## ADR-02: Hybrid Deterministic + LLM Disposition Gate

- **Context:** Pure LLM judgment on document acceptability can introduce non-determinism on regulatory thresholds (such as document age, legibility scores, or required fields), whereas purely rigid rule engines struggle with layout variations and cross-document corroboration.
- **Decision:** Implement a two-stage disposition gate (`bridge/disposition.py` and `bridge/agent.py`):
  1. **Deterministic Signal Evaluation:** Compute objective quality, freshness, issuer match, and schema completeness signals (`bridge/signals.py`) against declarative thresholds.
  2. **Agentic Synthesis & Remediation Guidance:** Invoke the ADK `LlmAgent` (`gemini-3.8-flash`) with bounded tools to synthesize cross-slot corroboration, explain rejections in plain language, and issue structured follow-up instructions.
- **Consequence:** Regulatory hard stops are enforced deterministically while edge-case reasoning and user-facing remediation remain conversational and context-aware.

---

## ADR-03: Strict Package Boundary Isolation (`bridge/` vs. `agents/`)

- **Context:** In multi-agent ecosystems, co-mingling the document verification platform (`bridge/`) with sample caller or carrier agents (`agents/`) leads to leaky abstractions and hidden state sharing.
- **Decision:** Enforce a strict AST-verified import firewall (`tests/test_isolation_guard.py`): `bridge/` and `agents/` may only share types through `contract/` (`contract/wire.py`, `contract/rfp.py`, `contract/quote.py`). Neither package may import from the other.
- **Consequence:** Caller and responder agents communicate exclusively over the A2A v0.3 protocol or HTTP APIs, proving that external counterparties can integrate using only the published wire contract.

---

## ADR-04: Declarative Skill Packages with Code-Free Onboarding

- **Context:** Adding a new document type (e.g., utility bill, government ID, carrier benefits quote) or multi-slot collection process should not require modifying Python code in `bridge/`.
- **Decision:** Package domain knowledge as self-contained skill directories under `skills/` (or `gs://<project>-bridge-skills/` in GCP) containing a `SKILL.md` manifest with YAML frontmatter (`quality_threshold`, `max_age_days`, `corroboration_fields`) and JSON Schema extraction contracts. An AST guard (`tests/test_isolation_guard.py`) verifies that no non-docstring string literal in `bridge/` hardcodes an installed skill folder name.
- **Consequence:** New document types and collection processes are onboarded or evolved at runtime without redeploying the core bridge service.

---

## ADR-05: Two-Stage Document AI Pre-Pass + Gemini Structured Extraction

- **Context:** Scanned PDFs, multi-page financial statements, and complex benefit schedule tables benefit from optical layout analysis alongside semantic LLM schema mapping.
- **Decision:** Combine an optional Google Cloud Document AI layout/OCR pre-pass (`bridge/extraction/docai.py`) with Gemini structured schema extraction (`bridge/extraction/gemini.py`). Every extracted field carries provenance anchors (page number, bounding box, confidence score, and verbatim snippet).
- **Consequence:** Downstream reviewers and automated rules can inspect exact page-and-coordinate provenance for every extracted value.

---

## ADR-06: Dual-Write WORM Audit Vault & Operational Fact Store

- **Context:** Regulated financial workflows (G-SIB KYC, TRID mortgage disclosures, commercial insurance placement) require both immutable binary evidence retention and queryable relational facts for analytics and reconciliation.
- **Decision:** When a submission is accepted (`bridge/user_interaction/surfaces.py`), execute a dual write:
  1. **Immutable Audit Vault (`bridge/vault.py`):** Lock the raw bytes with SHA-256 content hashes and retention metadata in Cloud Storage / Firestore (`RetentionPolicy.SEVEN_YEARS`).
  2. **Operational Fact Store (`bridge/operational_store.py`):** Normalize extracted fields into canonical entity records, document submissions, and typed fact rows (`fact_value_text`, `fact_value_num`, `fact_value_Date`) in SQL (`SqlOperationalStore` on Cloud SQL / AlloyDB / SQLite).
- **Consequence:** Auditors get cryptographic proof of the original artifact while downstream systems query structured SQL facts with full lineage back to the vault record.

---

## ADR-07: W3C PROV-O Lineage & Enterprise Knowledge Catalog

- **Context:** Enterprise data governance requires tracing every operational fact back to the raw document, the extraction model, the skill revision, and the authorizing agent.
- **Decision:** Maintain an Enterprise Knowledge Catalog (`bridge/knowledge_catalog.py`) tracking business glossary terms, sensitivity classifications (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED_PII`), and directed W3C PROV-O lineage edges (`wasDerivedFrom`, `wasGeneratedBy`, `wasAttributedTo`).
- **Consequence:** Any fact in the operational store can be traced upstream to its source PDF and downstream to the workflow decisions it informed.

---

## ADR-08: Compound Identity (SPIFFE Workload ID + Human Principal)

- **Context:** An agent acting on behalf of a human operator must never exceed either the agent's workload permissions or the human user's role and monetary approval authority.
- **Decision:** Enforce compound identity propagation (`bridge/identity.py`, `bridge/user_interaction/auth.py`) combining the caller's SPIFFE ID (`spiffe://<trust-domain>/ns/<ns>/sa/<sa>`) with the authenticated human principal's role and `effective_approval_limit`.
- **Consequence:** High-value approvals and sensitive PII mutations require both a trusted agent identity in the Agent Registry and an authorized human operator role.

---

## ADR-09: Declarative A2UI v0.9 Human-in-the-Loop Action Inbox

- **Context:** When automated verification encounters low-confidence extractions, cross-document mismatches, or high-value monetary thresholds, the workflow must park cleanly for human review without losing conversational state.
- **Decision:** Render declarative **A2UI v0.9** widget trees (`bridge/a2ui_inbox.py`, `bridge/user_interaction/surfaces.py`) that adapt dynamically to the caller's surface (`custom_web` vs. `gemini_app`) and role (`kyc_analyst`, `mortgage_underwriter`, `insurance_broker`, `compliance_officer`, `client_contributor`). Every human resolution is cryptographically signed (`HMAC-SHA256`) and appended to an immutable audit log.
- **Consequence:** Human operators review side-by-side provenance, override or approve exceptions within their authority limits, and resume the A2A exchange with a verifiable audit trail.

---

## ADR-10: Closed-Loop Agent Gym & Skill Evolution (`gemini-4-argon` Judge)

- **Context:** Static prompts and extraction rules degrade when exposed to adversarial inputs, noisy scans, or evolving regulatory edge cases.
- **Decision:** Embed a closed-loop **Agent Gym** (`bridge/agent_gym/`) that evaluates skills against golden and stress-test suites (`PromptInjection`, `BoundaryMismatch`, `CorruptedPayload`, `AuthorityEscalation`), scores trajectories with a high-reasoning teacher/judge model (`gemini-4-argon`), compiles mutated candidate skill revisions, and gates promotion behind shadow-traffic verification and human sign-off.
- **Consequence:** Runtime workhorse execution stays fast and cost-effective on `gemini-3.8-flash`, while `gemini-4-argon` continuously hardens skill instructions and deterministic rule sets offline.

---

## ADR-11: Production Wiring Guard Against Unconditional In-Memory Fallbacks

- **Context:** A common failure mode in dual-mode architectures is accidentally shipping an unconditional `InMemory*()` store constructor in a production code path, causing silent state loss across container restarts.
- **Decision:** Enforce an AST-level service wiring guard (`tests/test_service_wiring_guard.py` — test `2026-09-07 — "Remove test-only InMemory fallback"`) verifying that every `InMemory*()` instantiation in `bridge/` occurs inside an explicit configuration branch or fallback guard after SQL/GCP store resolution.
- **Consequence:** Production deployments fail fast or bind to durable SQL/Firestore stores rather than silently dropping data into ephemeral memory.
