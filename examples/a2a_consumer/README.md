# Minimal A2A consumer

`consume_bridge.py` is the smallest thing that talks to a deployed Bridge: it resolves
`/.well-known/agent-card.json`, checks the requested process is advertised, sends one
`CollectRequest` (built from `contract.wire`) plus by-reference document parts, and prints
each ledger entry's disposition. It imports only `a2a-sdk` (with its `httpx`), `contract`
and the stdlib — never `bridge/` or `agents/` (enforced by `tests/test_isolation_guard.py`).

```bash
scripts/run-core.sh &   # or any deployed Bridge origin
uv run python examples/a2a_consumer/consume_bridge.py http://localhost:8000/ \
    --process address-proof --doc gov-id-clean=images/gov-id-clean.svg
```

The contract it relies on is spelled out in [`docs/consume-over-a2a.md`](../../docs/consume-over-a2a.md);
`tests/test_a2a_consumer_contract.py` runs this script in-process against the core.
