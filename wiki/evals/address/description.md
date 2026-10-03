---
type: eval
related:
  - "../../../spec/demos/address-demo.md"
  - "../../../design/collect.md"
  - "../../../design/disposition.md"
tags: [bridge, eval]
---

# Eval — Address (proof of address)

> **This is an eval, not a skill.** It is the spec of *what correct looks like* for the Address use-case: the use-case description, the example input documents (images), and the **expected outputs** for each. The runtime config the Bridge loads lives separately in `skills/` (`address-proof`, `gov-id`, `utility-bill`) — skills are not evals.

## Use-case

Prove a person's address. The satisfaction rule (owned by the app, sense B) is **one government ID _or_ two utility bills from different companies**. A party submits documents over one or more turns; the Bridge classifies each against the candidate label space (`gov-id`, `utility-bill`), dispositions it (sense A — legible? right type? unexpired? confident?), maintains the classified ledger, and chases the delta until the app judges the set complete. See [Address demo](../../../spec/demos/address-demo.md) for the demo narrative and [Collect](../../../design/collect.md) for the loop.

**No real PII.** All documents use the synthetic identity **Jordan Lee** and are marked `synthetic: true`.

## Example inputs + expected outputs

Each row is one eval case: an input document (`images/*.svg`) with fixed extraction signals → the expected disposition/gate. The machine-readable form is `expected.json`; the scripted arrival order is `timeline.json`.

| id (image) | doctype | issuer | signals | **expected outcome** | what it proves |
|---|---|---|---|---|---|
| `gov-id-clean` | gov-id | — | conf=0.96, legible, unflagged, expiry=2030 | **accepted (auto_approve)** | Clean ID satisfies address-proof alone (Path A instant) |
| `gov-id-expired` | gov-id | — | conf=0.93, legible, flagged=expiry, expiry=2019 | **pending (hitl_review)** | Flagged-field gate → human review |
| `bill-powerco-clean` | utility-bill | power-co | conf=0.97, legible, unflagged | **accepted (auto_approve)** | First PowerCo bill; same-issuer scenario |
| `bill-powerco-clean-2` | utility-bill | power-co | conf=0.95, legible, unflagged | **accepted (auto_approve)** | issuer_raw="Power Co." → canonicalization variant |
| `bill-aquautil-clean` | utility-bill | aqua-util | conf=0.96, legible, unflagged | **accepted (auto_approve)** | Pairs with PowerCo to satisfy 2-distinct-issuer |
| `bill-aquautil-clear` | utility-bill | aqua-util | conf=0.72, legible, unflagged | **pending (hitl_review)** | Mid-confidence gate (resubmitted after blurry) |
| `bill-aquautil-blurry` | utility-bill | aqua-util | conf=0.30, illegible | **rejected (resubmit)** | Quality gate + resubmission flow |
| `passport-unsupported` | passport | gov | outside label space | **rejected (unsupported)** | Label-space rejection |

**Key eval scenarios** (the golden runs, from `timeline.json`): Path-A instant accept (`gov-id-clean`); distinct-issuer accept (`bill-powerco-clean` + `bill-aquautil-clean`); same-issuer reject (two PowerCo → not two *distinct*); resubmission (`bill-aquautil-blurry` → `bill-aquautil-clear`); HITL (`gov-id-expired`); escalation (SLA breach on the virtual clock).

## Issuer canonicalization

Bills carry both `issuer_raw` (as extracted) and `issuer` (canonical). The eval asserts `issuer == canonicalize(issuer_raw)` via `bridge.classification.canonicalize_issuer`:
- `PowerCo` → `power-co` · `Power Co.` → `power-co` (suffix "co" kept; only Ltd/Inc/etc. dropped) · `AquaUtil` → `aqua-util`

## Files

- **`expected.json`** — the eval corpus: 8 records (party `jordan-lee`), each with input signals + `expected_disposition` + `expected_gate` + `artifact` (image path).
- **`timeline.json`** — scripted virtual-clock exchanges (Exchange 1: Path A instant; Exchange 2: Path B full ladder).
- **`images/*.svg`** — synthetic SVG placeholders (deterministic, no external refs, no PII; the blurry one visually conveys blur).

## Consumers (parity — tests enforce no-drift)

The eval doubles as the deterministic extraction table and the golden-run corpus. Loaded by `tests/address/_corpus.py` (`load_corpus()`, `build_extraction_table()`, `load_timeline()`). Parity anchors held by tests:
1. **Fixture extractor**: `bridge.extraction.fixture.FixtureExtractor` reads `expected.json` directly (`DEFAULT_CORPUS`); there is no second table to drift. Every record resolves by artifact path and by bare id to its exact `extraction` block, and reproduces its `expected_disposition` / `expected_gate` / `issuer` through `Collector`, deterministically on repeat.
2. **Provider-portal samples**: every id in `CORPUS_IDS` (`frontend/provider-portal/src/components/UploadPrompt.tsx`, the local demo's upload picker) is a corpus id and resolves through the fixture extractor to its exact `extraction` block.
3. **`timeline.json`**: every referenced document exists in `expected.json`, and any beat `expected_disposition` matches the corpus.

`tests/address/test_corpus.py` enforces well-formedness, gate parity + determinism, collect-path parity, fixture resolution, portal-sample resolution, canonicalization parity, category coverage, no-PII, and timeline validity.

## Related
- [Address demo](../../../spec/demos/address-demo.md), [Collect](../../../design/collect.md), [disposition](../../../design/disposition.md), [evals index](../README.md)
