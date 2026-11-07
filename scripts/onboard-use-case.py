#!/usr/bin/env python3
"""Onboard a use-case agent (sample documents + doctype schemas + process skill) onto DocuBridge.

Examples:
    # Onboard the Address Proof use-case agent locally (or against GCP when BRIDGE_PORTS=gcp):
    uv run python scripts/onboard-use-case.py \\
        --manifest agents/address/onboarding/manifest.yaml

    # Onboard the Address Proof use-case agent against a remote Cloud Run DocuBridge service:
    uv run python scripts/onboard-use-case.py \\
        --manifest agents/address/onboarding/manifest.yaml \\
        --bridge-url https://bridge-883686070419.us-central1.run.app
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bridge.refinery.use_case_onboard import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["onboard", *sys.argv[1:]]))
