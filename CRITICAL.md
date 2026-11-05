# Critical / protected paths

Tasks touching any path below auto-escalate: stronger model + mandatory review before
commit. `spec/` is protected — propose changes, never edit it during `/implement`.

| Path | Why it's protected |
|------|--------------------|
| `spec/` | THE WHAT — forkable requirements. Status-free, downstream-consumed; changes are proposals only. |
| `wiki/evals/` | The eval corpus (Address + Benefits oracles). A runtime fixture hardcoded into `bridge/` + `tests/`; the "done" gate for Address and Benefits behavior. Never edit to make a test pass. |
| `contract/` | Shared Pydantic wire types — the ONLY package both `bridge/` and `agents/` import. A change ripples across every consumer. |
| `bridge/` | The reusable Bridge CORE. Load-bearing invariant: **never imports `agents/`** (isolation-guard test enforces it). |
| `terraform/` | Infrastructure-as-code. Provisioning/IAM/trust-boundary changes. |
| `scripts/deploy.sh` | Live GCP apply: image build + two-phase Terraform apply against a real project (needs live auth). A mistake mutates real infrastructure. |
| `scripts/parity.sh` | The deployed-path gate; runs against live GCP resources. |
| `skills/` | Runtime policy the Bridge loads. Thresholds, SLA ladders, satisfaction and standard rules change verdicts without a code change. Skill frontmatter is asserted by tests (`tests/address/test_skills.py`, `tests/test_benefits_skills.py`). |
| `catalog/doctypes/` | OKF doctype bundles (`*.okf.md`). Entity scope, contrastive field descriptions, and ontology edges drive consolidation priority and the extraction schema (ADR-13); an edit changes which fact wins and what Gemini is asked to extract. |
| `bridge/agent_gym/` | Promotion gates (3-Tier evaluation, Two-Sided Oversight `RSR`, Programmable Trust ladder). Promoted rules rewrite extraction prompts and flip `pending` → `accepted` dispositions. |
| `bridge/refinery/` | Schema Refinery promotion gate (3-Tier Counterfactual Assay, `NFR = 0.0%`). A promoted ontology revision changes doctype splits, priorities, and historical lineage. |
| `bridge/user_interaction/auth.py` | Caller identity and role resolution for every A2UI / REST surface (IAP header, `X-Bridge-Role`, SPIFFE SVID, `BRIDGE_A2UI_REQUIRE_AUTH`). An error here is an authorization bypass or a lock-out. |
| `bridge/identity.py` | SPIFFE JWT-SVID signing/verification, OBO compound identity, tenant isolation (`BRIDGE_SPIFFE_*`, `BRIDGE_USER_JWT_SECRET`). Key material names only; never a value. |
| `bridge/email_ingress/` | Secret-bearing: the HMAC correlation token (`BRIDGE_EMAIL_TOKEN_SECRET`) and the Graph app credential (`BRIDGE_EMAIL_GRAPH_CLIENT_SECRET`). Never log or commit a value. |
| `bridge/edge/email_ingress.py` | The public (`allUsers`) email webhook. It checks the Graph subscription `BRIDGE_EMAIL_CLIENT_STATE` (secret). It is the one Gateway exception in `design/zones.md`. |
| `.env.example` | The index of every env var, including the SECRET ones (`BRIDGE_EMAIL_TOKEN_SECRET`, `BRIDGE_EMAIL_GRAPH_CLIENT_SECRET`, `BRIDGE_EMAIL_CLIENT_STATE`, `DATABASE_URL`). Names + blank defaults only, never a value. |
