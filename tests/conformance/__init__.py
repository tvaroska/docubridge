"""Cross-repo consumer conformance (S0-arch-9).

The whole journey, end to end: a carrier PDF enters over the A2A edge **by reference**
(a loopback ``http(s)`` URL or a ``gs://`` object), is extracted against the installed
``benefits-quote`` skill schema, lands on the ``ExchangeTurn`` with per-field confidence
and review flags, and is mapped onto the consumer's own ``BenefitsQuote`` shape — on the
fixture and gemini legs, at terminal-outcome parity, against the committed synthetic
oracle ``wiki/evals/benefits/conformance.json``.

The rule this package exists under: ``bridge/`` never imports it, and never imports the
consumer. The consumer shape lives here as a **mirror** (``consumer.py``) that reads only
the wire (``contract/``); the real consumer is reached only through a gated subprocess
drift check, never in-process.
"""
