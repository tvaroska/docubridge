# A2A Document Bridge — build conventions

Clean-slate build. Requirements (THE WHAT) live in [`spec/`](spec/cujs.md) (forkable,
status-free, protected); design (THE HOW) is [`design/`](design/bridge.md) (status-free);
live status + sequencing is [`TODO.md`](TODO.md); the feature catalog is
[`docs/features.md`](docs/features.md).

## Toolchain
- **Python 3.12+**, managed with **uv**. Run the test suite with `uv run pytest`.
  All tests must pass before a task is considered done. Never declare success with a
  failing or convenience-skipped test.
- **Lint (T1 gate):** `uv run ruff check` must be clean. Config in `pyproject.toml`
  (`[tool.ruff]`): select `E4/E7/E9,F,I,UP,B,SIM,RUF`, line-length 88. `ruff format`
  owns line width — **E501 is intentionally not lint-enforced** (long prompt strings /
  docstrings). Fix findings or `--fix`; don't blanket-`noqa`. `UP042`/`RUF002` are
  ignored (wire-enum `__str__` semantics; intentional docstring unicode).
- **Frontend**: pnpm workspaces (`frontend/*`), React + TS + Vite + MUI; `frontend/shared`
  is the shared lib, `frontend/e2e` the Playwright suite.
- ADK is pinned `google-adk >= 2.11.0, < 3`; A2A on `a2a-sdk`.

## Repo shape (the load-bearing invariant)
```
spec/       THE WHAT — requirements (cujs, standards, demos/, open-questions); forkable, protected
design/     THE HOW — architecture, seams, zones, patterns + per-atom design notes (source of truth)
contract/   shared Pydantic wire types — the ONLY package both bridge/ and agents/ import
bridge/     the reusable Bridge CORE — NEVER imports agents/
agents/     service-agent consumers + demo drivers (Address, Benefits carriers, console)
skills/     Agent Skills the Bridge loads at runtime
frontend/   the four apps + shared lib + e2e
terraform/  IaC by substrate primitive
tests/      the shared seam suite (local + gcp adapters) + demo golden runs;
            conformance/ (Benefits consumer conformance), perf/ (scale harness)
wiki/evals/ the eval corpus (Address + Benefits oracles) — a runtime fixture, referenced by code + tests
docs/       planning/ops — roadmap, feature catalog, local-dev, upstream ADK issues
scripts/    local run-*.sh hosts + _dev-env.sh; deploy.sh / parity.sh (live GCP apply)
```
- `bridge/` **never imports** `agents/`. An isolation-guard test enforces this.
- Every managed-service boundary is a **seam**: an ABC/Protocol with a **local** adapter
  and a **GCP** adapter, exercised by one shared parametrized test
  (`@pytest.mark.seam`, `adapter` fixture over `("local","gcp")`, GCP gated on
  `BRIDGE_TEST_GCP`). Parity is **terminal-outcome**, never ledger-identical.

## Planning Workflow (gen-3)

This project uses a sprint/phase-based planning workflow managed through slash commands.
Live status lives in ONE place (`TODO.md`); `spec/` and `design/` stay status-free.

### Layout

| Path | Purpose |
|------|---------|
| `TODO.md` | Tasks + live status/counts (the ONLY place status lives) |
| `DECISIONS.md` | Append-only decision/learning log (committed) |
| `CRITICAL.md` | Registry of protected/sensitive paths (auto-escalate) |
| `spec/` | THE WHAT — requirements. Forkable, status-free, **protected**. `cujs.md`, `standards.md`, `demos/*.md`, `open-questions.md` |
| `design/` | THE HOW (as built) — `architecture.md`, `seams.md`, `zones.md`, `patterns.md` + per-atom design notes; the design source of truth |
| `docs/` | Planning/ops — `roadmap.md`, `features.md`, `upstream-adk-issues.md` |
| `wiki/evals/` | The eval corpus (Address oracle) — a runtime fixture, referenced by code + tests |
| `.claude/plans/` | Per-task plans — LOCAL, gitignored, named `{task-id}-{slug}.md` |

Committed: `TODO.md`, `spec/`, `design/`, `docs/`, `wiki/evals/`, `DECISIONS.md`, `CRITICAL.md`.
Gitignored: `.claude/` (including `.claude/plans/`).

### Commands

- `/open` — top 3 open tasks (surfaces `- [!]` failed items)
- `/implement {task-id}` — implement, run the verification ladder, archive
- `/new-task` — add a critical issue to Sprint 0
- `/new-feature` — define a feature; writes requirements to `spec/`
- `/replan` — sprint/phase transition; runs the T3 CUJ gate
- `/verify [cuj-id]` — run CUJ simulation(s) on demand
- `/status` — planning summary + live progress

### Checkbox states

`- [ ]` open · `- [x]` done · `- [!]` attempted-but-failed (awaiting follow-up).

### Task ID format

This repo keeps its native `P<phase>-<seq>` scheme (e.g. `P1-3`), grouped under phase
headings in `TODO.md`; a task is unblocked when every task it "needs" is `- [x]`.
`TODO.md` carries a counter comment `<!-- Counters: p0=N p1=N ... -->` for per-phase seq.
(This is a deliberate deviation from the gen-3 `S<sprint>-<feature>-<seq>` default — see
`DECISIONS.md`.)

### Verification ladder

- **T1 (every /implement — gates the commit):** `uv run pytest` green (plus lint/type-check
  where configured). All tests must pass — never declare success with a failing or
  convenience-skipped test.
- **T2 (every /implement — gates flipping `- [x]`):** exercise the artifact against the
  task's own acceptance criteria. Files existing / build green is NOT done.
- **T3 (phase-close via /replan + on-demand via /verify):** CUJ simulation against the
  eval corpus. The Address eval corpus is the oracle (see Data discipline).

### CRITICAL escalation

Any task touching a path in `CRITICAL.md` auto-escalates: stronger model + mandatory
review before commit. `spec/` is protected — agents PROPOSE changes to spec, never edit
it during `/implement`.

## Task workflow (plan-implement)
- Each task: implement → `uv run pytest` green → flip its `- [ ]` to `- [x]` in `TODO.md`
  → commit path-scoped on the current branch (do not push).
- Match the style, naming, and seam/adapter idioms of the surrounding code.

## Data discipline
- **Synthetic data only, no real PII.** The eval party "Jordan Lee" is synthetic.
- The Address eval corpus (`wiki/evals/address/`) is the oracle — no Address behavior
  is "done" until it reproduces `expected.json` (+ `timeline.json` where relevant).
