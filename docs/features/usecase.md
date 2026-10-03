# Live use-case-add

Feature area for live use-case-add (CUJ-9): a whole new demo's skills — a process skill plus its doctype skills — uploaded to the running core while exchanges of other processes are in flight.

## Overview

CUJ-8 (live doctype-add) is built. CUJ-9 is the whole-use-case version; its Driver and companions are built, and the spec's Driver line is a proposal awaiting the spec owner (DECISIONS.md 2026-10-03, S4-usecase-5).

**Supports CUJs:** CUJ-9 (Driver proposed), CUJ-8 (shared live-read path), CUJ-13 / CUJ-15 (the board companion opens and files RFPs of the live-added process).

## Completed Work

### 2026-10-03 — S4-usecase-1 Read-time audit of skill consumers

**What shipped:** two captures fixed so a skill dropped into `BRIDGE_SKILLS_DIR` after start is seen on the next request: the policy loaders (SLA, disposition, RFP, negotiate, program standard) and the Gemini extraction catalog. The consumer → read-time table is in `design/skills.md`.

**Approach:** `bridge.sla.skills_root()` resolves the registry root per call (explicit argument, else `BRIDGE_SKILLS_DIR`, else the repo `skills/`). The loaders' `skills_dir` default changed from the import-time constant to `None`. `GeminiExtractor._catalog` re-reads the doctype catalog on every `extract` and rebuilds the prompt and response schema only when it changed.

**Verification:** T1: `uv run ruff check` clean, `uv run pytest -q` 1750 passed / 97 skipped. T2: `tests/test_live_skill_reads.py` (a skill created after import resolves through all five loaders; red before, as `skills_root` and the live default did not exist) and `test_doctype_added_after_first_extract_reaches_the_next_prompt` (one extractor, a doctype installed between two extracts, appears in the second request).

**Decisions & gotchas:** see DECISIONS.md 2026-10-03. The earlier once-per-extractor catalog read was reversed on purpose. Mandatory CRITICAL (`bridge/`) self-review of the diff done before commit.

### 2026-10-03 — S4-usecase-2 CUJ-9 Driver: a whole use case added live

**What shipped:** `tests/test_live_use_case_add.py`, one Driver test on a single hybrid-edge `build_a2a_app` over a tmp registry seeded with Address only, plus the shared synthetic skill fixture `tests/_skill_fixtures.py` (`supplier-onboarding` process, `w9-form` / `insurance-cert` doctypes, `install_supplier_onboarding(registry)`). `tests/test_rfp_second_use_case.py` and `tests/test_live_skill_reads.py` now import the fixture. No `bridge/` change was needed.

**Approach:** park an Address exchange (POWER + AQUA bills, AquaUtil parks on the R3 handoff to `ScriptedLlm`), run an identical control core to completion, install the use case through the `skill_registry` seam (doctypes first, process last), then assert: the parked task is proto-equal to its snapshot and no model call was made, the next card advertises `supplier-onboarding` with both doctype tags, the resumed exchange equals the control's (status reason, text, full turn), and the new process is usable on the same app (Path-B accepted with fetchable scoped artifacts; Path-A schema enforced). Before the upload the same supplier request is `unsupported_doctype`.

**Verification:** T1: `uv run ruff check` clean, `uv run pytest -q` 1751 passed / 97 skipped; the Driver also passes under `BRIDGE_EDGE_MODE=deterministic` and `hybrid`. T2: trap mutations (rebuild, skipped install, process-only install, control after upload) each turn the Driver red.

**Decisions & gotchas:** see DECISIONS.md 2026-10-03. The A2A RFP opener is bound once at build, so a live-added process has no RFP opener without a rebuild (a finding for S4-usecase-4/5).

### 2026-10-03 — S4-usecase-3 A bad live upload is refused, not propagated

**What shipped:** both `skill_registry` adapters skip a malformed skill folder with a logged reason (WARNING on `bridge.edge.skills`) instead of raising out of `list_skills`; `loaded_skills()` narrows each process's label space to its installed candidate doctypes and drops a process with none installed; `install_skill` (local + gcp) refuses a malformed upload before any write. Before this, one truncated `SKILL.md` made the card GET 500 and crashed in-flight resumes, and one corrupt `schema.json` broke Path-A for every doctype.

**Approach:** `skill_meta_from_dir` became the strict validator (no fence; bad / non-mapping frontmatter; missing `name` / `description` / `bridge-kind`; unknown kind; name ≠ folder; corrupt present policy / schema; escaping pointer → `ValueError`). Shared helpers in `bridge/edge/skills.py`: `listed_skill_meta` (tolerant listing), `resolve_label_spaces` (cross-skill), `validate_skill_upload` (staged strict parse). `tests/test_live_use_case_add.py::test_bad_live_upload_is_refused_not_propagated` drops five bad folders raw next to a parked Address exchange and walks discovery, S0-arch-18, Path-A, the resume, a refused seam re-upload, and the partial / full repairs.

**Verification:** T1: `uv run ruff check` clean; `uv run pytest -q` 1781 passed / 98 skipped (baseline 1751 / 97; +30 tests; the one extra skip is the `BRIDGE_TEST_GCP`-gated gcp param of the new seam test). Driver green under `BRIDGE_EDGE_MODE=deterministic` and `hybrid`. T2: red before (`bridge/` stashed: the Driver raises from `list_skills`); mutations M1–M7 (no tolerant helper, no kind check, no policy check, unresolved `loaded_skills`, no narrowing, no schema check, no install validation) each turn the Driver red.

**Decisions & gotchas:** see DECISIONS.md 2026-10-03 (S4-usecase-3). It supersedes S0-arch-5's "corrupt schema raises at read": the refusal is now at install, and listing skips the folder. Residuals: a raw overwrite of a good skill removes it (fail closed); the RFP policy's candidates are un-narrowed (S4-usecase-4).

### 2026-10-03 — S4-usecase-4 A live-added RFP use case opens on the shared board

**What shipped:** the RFP board host and the A2A opener serve emergent-Collect processes installed live in the skill registry, next to their home process (`BRIDGE_RFP_PROCESS`), each RFP with its own process's policy. Before this, the board refused any RFP of a process other than its build-time one, so a live-added use case could not open an RFP without a rebuild (the S4-usecase-2 finding).

**Approach:** `RfpBoard(live_policy=...)` with `policy_for` / `serves`; every per-RFP consumer (board projection, opener, email intake, carrier checklist) resolves `policy_for(rfp.process)`. The resolver `bridge/edge/skills.py::rfp_policy_for` reads `SKILL.md` + the policy asset through the `skill_registry` seam on every call and returns the narrowed label space; the filesystem loader and the seam reader share pure validators split out of `bridge/sla.py`, `bridge/policy.py` and `bridge/rfp.py`. An RFP whose skill is removed is skipped on the listing, 404 on a single read, and its mail is quarantined `process_unavailable` before extraction.

**Verification:** T1: `uv run ruff check` clean; `uv run pytest -q` 1797 passed / 99 skipped (baseline 1781 / 98; the extra skip is the `BRIDGE_TEST_GCP`-gated gcp param of the new seam test); the Drivers pass under `BRIDGE_EDGE_MODE=deterministic` and `hybrid`. T2: `tests/test_live_rfp_use_case_add.py` (an Address leg parks, `supplier-onboarding` is installed through the seam, an A2A Requirements request opens `onboard-live-2027` 4 × invited model-free, `/rfps` lists it, the checklist accepts `w9-form` / `insurance-cert`, an emailed W-9 files as `W9`, the Address leg resumes to the control's outcome), also through the gcp adapter over a fake bucket; red before (`bridge/` stashed); mutations M1–M6 each red; a cross-process smoke against a running `uvicorn` board host listed a live-opened RFP without a restart.

**Decisions & gotchas:** see DECISIONS.md 2026-10-03 (S4-usecase-4). Residuals: the home policy is fixed at build; an edge with no home process has no opener; the showcase board stays single-process; the live check is a sync registry read on the loop.

### 2026-10-03 — S4-usecase-5 CUJ-9 spec proposal and Sprint 4 close-out

**What shipped:** a proposal-only `DECISIONS.md` entry giving the spec owner the exact replacement for the CUJ-9 line in `spec/cujs.md`: Driver `tests/test_live_use_case_add.py::test_whole_use_case_added_live_while_an_exchange_is_in_flight`, companions `tests/test_live_use_case_add.py::test_bad_live_upload_is_refused_not_propagated` and `tests/test_live_rfp_use_case_add.py::test_live_rfp_use_case_opens_on_the_board_next_to_a_running_exchange`; the Judge is kept verbatim and the trap gains the bad-upload alternative. Catalog (SKILL-5, SEAM-4) and roadmap updated. No code or spec change.

**Approach:** CUJ-9 was run by hand against the proposed line, since `/verify` reads `spec/` where the Driver is still a dash.

**Verification:** T1: `uv run ruff check` clean; `uv run pytest -q` 1797 passed / 99 skipped (unchanged). T3 deterministic: the 3 proposed ids plus 3 supporting ids under `BRIDGE_EDGE_MODE=deterministic` and `hybrid` (credentials stripped) each 6 passed / 2 skipped (the two `BRIDGE_TEST_GCP` gates); the Drivers pin the hybrid edge, so they are env-insensitive (bogus mode fails only the CUJ-8 driver). T3 LLM (jeep): score 1.0, pass true.

**Decisions & gotchas:** see DECISIONS.md 2026-10-03 (S4-usecase-5). The S4-usecase-3/4 residuals are carried there for /replan.

**Sprint 4 complete.** 0 open S4-usecase tasks. Run `/replan` to close the sprint.

## In Progress

(none)

## Planned Work

### Live use-case-add (Priority: P1)
- **Problem:** A new industry must arrive as skills only. Nothing yet proves a process skill dropped into a running core is advertised, usable, and leaves in-flight exchanges of other processes undisturbed. CUJ-9 has no Driver, so the claim is unverified and the T3 gate cannot cover it.
- **Status:** Done (2026-10-03); spec Driver line proposed
- **Added:** 2026-10-02
- **Tasks:** `S4-usecase-1…5` in `TODO.md`.
