"""Shared pytest fixtures and hooks.

The `adapter` fixture yields SeamMode (local/gcp), for use with get_adapter:
    def test_my_seam(adapter):
        instance = get_adapter(Seam.sessions, adapter)
        ...

The collection guard aborts on invalid @pytest.mark.seam names.
"""

import os

import pytest

from bridge.seams import SEAM_NAMES, SeamMode, is_valid_seam

# Enable pytester for collection-guard tests
pytest_plugins = ["pytester"]

# Ensure loopback test servers (127.0.0.1 / localhost) bypass any ambient HTTP_PROXY.
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

# Read BRIDGE_TEST_GCP at collection time for the gcp gate
_GCP = os.environ.get("BRIDGE_TEST_GCP") == "1"


@pytest.fixture(
    params=[
        pytest.param(SeamMode.local, id="local"),
        pytest.param(
            SeamMode.gcp,
            id="gcp",
            marks=pytest.mark.skipif(
                not _GCP, reason="set BRIDGE_TEST_GCP=1 to run gcp adapters"
            ),
        ),
    ]
)
def adapter(request) -> SeamMode:
    """Parametrized adapter mode fixture (local always, gcp gated on BRIDGE_TEST_GCP=1)."""
    return request.param


def pytest_collection_modifyitems(config, items):
    """Abort collection on invalid @pytest.mark.seam names."""
    bad = []
    for item in items:
        for marker in item.iter_markers(name="seam"):
            name = marker.args[0] if marker.args else None
            if not (isinstance(name, str) and is_valid_seam(name)):
                bad.append((item.nodeid, name))
    if bad:
        lines = "\n".join(f"  {nodeid}: seam={name!r}" for nodeid, name in bad)
        raise pytest.UsageError(
            f"invalid @pytest.mark.seam name(s); valid seams are {sorted(SEAM_NAMES)}:\n{lines}"
        )
