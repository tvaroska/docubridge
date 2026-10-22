# Security Policy

## Architectural Security Controls

**A2A Document Bridge** enforces defense-in-depth across all six Enterprise Data Architecture zones:

1. **Privacy at the Edge (`bridge/vault.py::redact_personal_data`):**
   - Sensitive personal identifiers (SSNs, tax IDs, phone numbers, email addresses, bank routing/account numbers) are scrubbed at intake before records enter operational storage.
2. **Write-Once WORM Audit Vault (`bridge/vault.py::GcpAuditVaultStore`):**
   - Accepted documents are locked in Cloud Storage with `if_generation_match=0`, SHA-256 content digests, and retention locks (`BRIDGE_VAULT_BUCKET`). Overwrites and deletions raise `VaultImmutabilityError`.
3. **Compound Identity (`bridge/identity.py` & `bridge/user_interaction/auth.py`):**
   - Every sensitive operation evaluates the intersection of the workload's **SPIFFE ID** (`spiffe://agents.docubridge.io/...` verified via `X-Bridge-Spiffe-Svid`) and the human caller's **Principal Role & Approval Limit** (`Effective Access = Agent(SPIFFE) ∩ Human(Principal) ∩ Tenant Scope`).
   - When `BRIDGE_SEAM_MODE=gcp`, hardcoded development signing secrets are rejected (`SpiffeAuthenticationError`).
4. **Agent Gateway & Model Armor (`bridge/agent_gym/registry_gateway.py`):**
   - `CLIENT_TO_AGENT` ingress enforces inline prompt-injection detection and DLP scrubbing; `AGENT_TO_ANYWHERE` egress enforces Agent Registry membership and IAM v3 Unified Access Policy CEL rules (`DENY` overrides `ALLOW`).
5. **Private-IP Data Plane & Secret Management (`terraform/`):**
   - Cloud SQL PostgreSQL 16 runs exclusively on VPC Private Services Access (`ipv4_enabled = false`) with `cloudsql.iam_authentication = "on"`.
   - No secrets or plaintext DSNs are ever committed to HCL, `.env.example`, or container images; credentials are injected at runtime via Google Cloud Secret Manager (`secret_key_ref`).

## Reporting a Vulnerability

Please do not report security vulnerabilities in public issues. Report suspected vulnerabilities privately to the repository maintainers with a minimal reproduction and affected component path.
