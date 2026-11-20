# DocuBridge: Evidence-First A2A Document Mediation

Putting a multi-document loan or insurance packet straight into an agent prompt breaks three ways: late one-page addenda tie with base contracts, token summaries corrupt dollar math, and tired reviewers rubber-stamp bad OCR. DocuBridge isolates collection, PDF polygon evidence, and pure Python `Decimal` conflict overrides behind one A2A boundary so calling agents only own business policy.

![DocuBridge Evidence Left, Policy Right Boundary](docs/assets/architecture_card.svg)

## Why Not Default IDP or Prompt Tools?

If your workflow processes single clean forms with no late amendments, multi-party chasing, or audit retention, a standard OCR pipeline or direct structured extraction tool is simpler and cheaper. Reach for DocuBridge when documents arrive out of order across multiple parties and a silent override costs money or breaks an audit.

| Failure Mode | Default IDP / Prompt Tool | DocuBridge Mechanism | Verified Receipt |
| :--- | :--- | :--- | :--- |
| **Feed waste & model self-approval** | Sends structured feeds to prompts; lets an LLM approve blurry or expired scans | Path A validates JSON/CSV at `$0` LLM cost; Path B screens PDFs on quality, freshness, completeness, and corroboration before locking bytes in a 7-year SHA-256 WORM vault | `0` LLM calls on Path A; `0%` model self-approval ([test_edge_gate_inversion.py](tests/test_edge_gate_inversion.py)) |
| **Late addendum ties & float drift** | Monolithic schemas tie at equal priority and LLM token math drops overrides | Pure Python `Decimal` consolidation resolves declared ontology `[:SUPERSEDES]` precedence across arrival time and effective date | Overrides `$50,000.00` to `$100,000.00` on `$45,000,000` CRE (`72.00% LTV`), `$4,250.00` to `$8,500.00` on `$1,850,000` mortgage (`77.08% LTV`), and `$50,000.00` to `$100,000.00` on `$142,500,000` property (`$418,500.00` premium) with `0` float drift ([test_enterprise_data_architecture.py](tests/test_enterprise_data_architecture.py)) |
| **Rubber-stamp & prompt regression** | Unchecked clicks poison prompts; fixing one carrier breaks others | Requires Reviewer Scrutiny `RSR >= 0.80` (polygon citation plus dwell time) and promotes compiled `SKILL.md` rules only at `0.0%` regression | `FA = 0`, `NFR = 0.0%`, `Krippendorff alpha >= 0.80`, `Gwet AC2 >= 0.80`; `1,912` passing local Python tests + `56/56` Terraform plan checks ([test_calibration_honesty.py](tests/test_calibration_honesty.py)) |
| **100% judge spend & bias** | Frontier judge on every trace is expensive and skewed by imperfect sensitivity/specificity | Layer 1 runs 5 deterministic SQL gates on `100%` of OTel traces at `$0`; Layer 2 samples anomalies (`~5%`); Layer 3 applies Rogan-Gladen debiasing | `100%` SQL trace coverage at `$0` LLM cost, `~95%` judge token reduction ([test_sql_eval_bq_parity.py](tests/test_sql_eval_bq_parity.py)) |

## Quick Start

Run the 5-scene Enterprise Showcase Console locally with zero cloud credentials or API keys (`14/14` **Ports, Adapters, and Contracts** use local adapters over `wiki/evals/`):

```bash
uv sync
uv run uvicorn --factory bridge.dashboard.server:build_dashboard_app --port 8003
```

Open `http://localhost:8003/demo` (or `/ebc` for the live consolidation stage) to switch between Banking (`LOAN-MERIDIAN-001`), Mortgage (`MORTGAGE-WESTBROOK-2026`), and Insurance (`terrepower-coastal-2027`). Downstream agents consume DocuBridge over A2A without forking this repo: run `uv run python examples/a2a_consumer/consume_bridge.py http://localhost:8000/ --process address-proof --doc gov-id-clean=images/gov-id-clean.svg` against `scripts/run-core.sh`, and see [docs/consume-over-a2a.md](docs/consume-over-a2a.md) for the full wire contract.

## Where This Breaks

1. **Offline Fixtures vs. Live Optical Perturbation:** Local zero-credential mode (`BRIDGE_PORT_MODE=local`) replays deterministic oracle fixtures (`wiki/evals/`) and reports `perturbation_evaluated=False` unless a live extractor (`BRIDGE_PORT_EXTRACTION=gcp`) is attached.
2. **Declared Precedence Prerequisite:** Because `Rule 0` forbids LLM math and ad-hoc priority guessing, any new financial rollup or override must be declared in `catalog/doctypes/*.okf.md` (`[:SUPERSEDES]` / `[:FEEDS_METRIC]`) or promoted via Schema Refinery before consolidation.
3. **Strict High-Risk Autonomy Gate:** Slices marked `risk_level="HIGH"` require `5/5` consecutive human-verified shadow matches (`citation_verified=True` and `RSR >= 0.80`) before `trusted_auto_approve`; `force=True` overrides are rejected.
4. **Local Single-Node SQLite vs. Cloud SQL:** Local runs multiplex all 5 SQL stores onto one SQLite runtime; multi-replica production scaling requires `BRIDGE_PORT_MODE=gcp` with Cloud SQL for PostgreSQL 16 or AlloyDB.

## Deep Dives

- **Architecture, 10-line A2A client, surfaces, repo map & glossary:** [docs/architecture-overview.md](docs/architecture-overview.md)
- **Downstream A2A adoption & wire types (`contract/`):** [docs/consume-over-a2a.md](docs/consume-over-a2a.md)
- **Executive showcase walkthrough & click-path:** [BANKING.md](BANKING.md) and [docs/demo-click-path.md](docs/demo-click-path.md)
- **Design tree, ADRs & roadmap:** [design/bridge.md](design/bridge.md), [DECISIONS.md](DECISIONS.md), [docs/roadmap.md](docs/roadmap.md), and [CONTRIBUTING.md](CONTRIBUTING.md)
