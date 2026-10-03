"""RFP-scale harness (S0-perf-1) — "will it work with 1000 lines".

Measures the Bridge at the size an RFP actually arrives at: a 1000-row carrier rate
sheet (26 pages), a 50-page quote (1380 rate rows + 60 design rows), and one exchange of
10 carriers x 5 product lines. It drives extraction (the real ``GeminiExtractor`` over a
page-aware fake Gemini client, or the real client under ``--live``), the ledger
projection + wire round trip, the RFP collection, the dashboard read-model / SSE frame
and the portal view, and prints one table of timings, byte sizes and correctness checks.

- ``scale_corpus``  — deterministic synthetic generators (no PII; every page says
  "synthetic"); the per-page content is also the ORACLE.
- ``fake_gemini``   — ``PageAwareFakeClient``: answers a page window with exactly that
  window's rows and truncates (``finish_reason=MAX_TOKENS``) past ``max_output_tokens``.
- ``harness``       — ``uv run python -m tests.perf.harness [--json PATH] [--live]``;
  exit code 0 only when every CORRECTNESS check passes (timings are reported, never
  asserted).

Generated PDFs go to a caller-given directory
(``uv run python -m tests.perf.scale_corpus /tmp/scale``) — never into ``wiki/evals/``.
"""
