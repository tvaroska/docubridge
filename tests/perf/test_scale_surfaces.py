"""RFP-scale surfaces (S0-perf-1): the harness end to end at a CI-friendly size.

``--rows-per-entry 200`` keeps the 10 x 5 RFP's ExchangeTurn / dashboard snapshot in the
low megabytes; the extraction legs always run at full scale (they are cheap with the
fake). Every correctness check must pass; timings are reported, never asserted.
"""

from __future__ import annotations

import json

from tests.perf import harness


async def test_surfaces_at_200_rows_per_entry():
    args = harness.argparse.Namespace(rows_per_entry=200, carriers=10, products=5,
                                      live=False)
    measurements, checks = await harness.run(args)
    failed = [name for name, ok in checks if not ok]
    assert failed == []
    assert measurements["rfp"]["received"] == 50
    assert measurements["rfp"]["waiting"] == 0
    assert measurements["dashboard"]["sse_frames"] >= 1
    assert measurements["portal"]["http_view_bytes"] > 0


def test_cli_smoke_writes_json(tmp_path, capsys):
    out = tmp_path / "perf.json"
    assert harness.main(["--rows-per-entry", "50", "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "correctness checks passed" in printed
    data = json.loads(out.read_text())
    assert all(data["checks"].values())
    assert data["measurements"]["rate_sheet_1000"]["rates_exact"] == 1000


def test_canonical_cell():
    assert harness.canonical_cell("$1,250.00 USD") == "1250.00"
    assert harness.canonical_cell("Age 30-34") == "30-34"
    assert harness.canonical_cell("monthly") == harness.canonical_cell("MONTHLY")
