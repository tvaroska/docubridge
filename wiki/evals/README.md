---
type: eval-index
related:
  - "../../spec/demos/demo-suite.md"
tags: [bridge, eval]
---

# Evals

> **Evals are spec, not runtime config.** Each eval captures, per use-case, the **use-case description**, the **example inputs** (documents / images), and the **expected outputs** (disposition + gate per input). They define *what correct looks like* and are the source of truth for the golden-run suite.
>
> **Evals ≠ skills.** Skills (`skills/`) are the Agent Skills folders the Bridge loads at runtime (doctype extraction specs, process policy). Evals (`wiki/evals/`) are the test/spec corpus that grades behaviour. Keep them separate: a skill is *how the Bridge behaves*; an eval is *how we check it behaved correctly*.

## Layout

```
wiki/evals/<use-case>/
  description.md   — use-case description + example inputs + expected-outputs table (a wiki atom)
  expected.json    — machine-readable eval corpus: inputs + expected disposition/gate
  timeline.json    — scripted arrival order (where a use-case has a virtual-clock scenario)
  images/          — example input documents (synthetic, no PII)
  conformance.json — (Benefits) consumer-conformance records, same {"documents": [...]} shape
```

## Use-cases

| Use-case | Eval |
|---|---|
| **Address** (proof of address) | [address](address/description.md) — 8 documents, full expected outputs, golden scenarios |
| **Benefits** | [benefits](benefits/description.md) — 5 carrier-quote records (`expected.json`: normalize, standard-gap and revise oracles, incl. Carrier B's revised `-v2`), 2 consumer-conformance records (`conformance.json`), 3 native-digital PDFs |
| **RFP** | [rfp](rfp/description.md) — scaffold (structure only) — the RFP demo host and RFP tests read the Benefits conformance corpus |

Address and Benefits carry full corpora + expected outputs. RFP provides the eval structure only; its demo inputs come from the Benefits conformance corpus. See [demo-suite](../../spec/demos/demo-suite.md) for the demo set.
