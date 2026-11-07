#!/usr/bin/env python3
"""Create an OKF (.okf.md) ontology bundle and doctype skill folder from 1-5 sample documents.

Examples:
    # Induce a new doctype schema from sample SVG/PDF/JSON documents and write to ./out:
    uv run python scripts/create-schema-from-docs.py \\
        --doctype utility-bill \\
        --doc wiki/evals/address/images/bill-powerco-clean.svg \\
        --doc wiki/evals/address/images/bill-aquautil-clean.svg \\
        --spine spine.party \\
        --domain kyc \\
        --write-dir /tmp/generated-schemas

    # Induce and install directly into the active SkillRegistry + KnowledgeCatalog:
    uv run python scripts/create-schema-from-docs.py \\
        --doctype gov-id \\
        --doc wiki/evals/address/images/gov-id-clean.svg \\
        --spine spine.party \\
        --domain kyc \\
        --install
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bridge.refinery.use_case_onboard import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["create-schema", *sys.argv[1:]]))
