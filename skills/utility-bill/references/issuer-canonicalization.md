# Issuer Canonicalization

The issuer canonicalization algorithm converts raw company names to stable keys for consistent identity tracking across variations in naming.

## Algorithm

1. **Split camelCase**: Insert spaces at camelCase boundaries (e.g., `PowerCo` → `Power Co`)
2. **Lowercase**: Convert all text to lowercase
3. **Non-alphanumeric to hyphens**: Replace non-alphanumeric characters with spaces
4. **Drop corporate suffixes**: Remove corporate suffixes like `ltd`, `inc`, `llc`, `corp`, `corporation`, `incorporated`, `limited`, `plc`, `gmbh`, etc. **Note: `co` is deliberately kept** (it's not treated as a suffix)
5. **Join with hyphens**: Join remaining tokens with hyphens

## Examples

The following examples demonstrate the canonicalization behavior and match the code implementation in `bridge.classification.canonicalize_issuer`:

- `PowerCo` → `power-co`
- `Power Co.` → `power-co`
- `PowerCo Ltd` → `power-co`
- `AquaUtil` → `aqua-util`

## Authority

The authoritative implementation is `bridge.classification.canonicalize_issuer`.
