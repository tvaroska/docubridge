# Testing Features

Test infrastructure and evaluation corpus management for the Document Bridge.

---

## Address Evaluation Corpus Parity

The Address evaluation corpus (`wiki/evals/address/`) serves as both the deterministic extraction table and the golden-run corpus. Test infrastructure ensures no drift between the corpus and its consumers.

### Parity Anchors

**Fixture Extractor.** `bridge.extraction.fixture.FixtureExtractor` reads `expected.json` directly via `DEFAULT_CORPUS`; there is no separate fixture table. The corpus itself is the fixture table, eliminating one source of drift. Tests enforce that every record resolves by both artifact path and bare id to its exact `extraction` block, and reproduces its disposition/gate/issuer deterministically through `Collector`.

**Provider-Portal Samples.** The local demo's upload picker (`frontend/provider-portal/src/components/UploadPrompt.tsx`) defines `CORPUS_IDS` as the sample documents offered to users. Test infrastructure reads this TypeScript source via regex and validates that every id is a unique corpus id that resolves through `FixtureExtractor` to the corresponding extraction block. The parse is strict: a renamed or missing const fails the test loudly rather than passing vacuously.

**Timeline Validity.** `timeline.json` references are validated: every document exists in `expected.json`, and beat dispositions match corpus expectations.

### Cross-Language Testing

Portal sample parity is checked from Python (`tests/corpus_and_skills/test_corpus.py::test_portal_samples_resolve_to_corpus`) rather than vitest because T1 (the commit gate) runs `uv run pytest`. The test regex-reads the TypeScript const and fails if the pattern is not found or yields an empty list, ensuring test failures are loud rather than vacuous.

This establishes a pattern for frontend-to-backend parity checks that run in the T1 gate: Python tests can read and validate frontend source files when necessary to enforce cross-cutting invariants.

### Benefits RFP Sample Parity (Deferred)

Similar drift risk exists for `RfpChecklist.tsx::QUOTE_ARTIFACTS` (benefits conformance ids), but coverage is not yet implemented. The TypeScript const lists corpus document ids that should resolve through the benefits fixture extractor. A future parity test would follow the same regex-read-from-Python pattern established for Address portal samples.
