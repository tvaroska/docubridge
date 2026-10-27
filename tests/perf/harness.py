"""The RFP-scale harness (S0-perf-1): ``uv run python -m tests.perf.harness``.

Options: ``--json PATH`` (write the measurements dict), ``--rows-per-entry N`` (rows on
each of the RFP's quotes, default 1000), ``--carriers N`` / ``--products N`` (default
10 x 5), ``--live`` (extraction through the REAL Gemini client — Vertex credentials
required; the unchunked "before" leg is skipped there to save a 32k-token call).

Sections (one table; exit code 0 only if every CORRECTNESS check passes — timings and
sizes are reported, never asserted):

1. **extraction** — ``rate-sheet-1000`` (26 pages) and ``quote-50p`` (50 pages) through
   ``GeminiExtractor``: wall time, calls, windows, max in-flight, event-loop ticks
   observed while extracting, rows vs the oracle (exact + ordered); plus the
   **unchunked leg** (``chunk_pages=0``) — the reproduced "before": the single
   1000-row output is cut at the budget and raises ``ExtractionTruncated`` after ONE
   call (no futile retries).
2. **ledger** — ``signals_from_result`` → ``dispose_signals`` → ``project_ledger_entry``;
   ``LedgerEntry`` JSON size/time.
3. **rfp** — ``open_rfp`` over carriers x products, one ``receive`` per cell (each quote
   carrying ``--rows-per-entry`` rows), ``rfp_status`` (all received, ``waiting == 0``),
   and the ``ExchangeTurn`` (every entry) round-tripped through ``decode_wire``.
4. **portal** — ``start_session`` + ``upload`` of the merged 1000-row result →
   ``current_view`` → ``encode_a2ui_view``; ``GET /sessions/{sid}/view`` over ASGI.
5. **dashboard** — ``build_dashboard_app(rfps=board)`` over ASGI: ``GET /exchanges``,
   ``readmodel_snapshot``, ``GET /events`` time-to-first-frame + frame bytes; the first
   frame must carry the RFP row with every cell.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bridge.collect import project_ledger_entry
from bridge.dashboard import build_dashboard_app
from bridge.dashboard.board import RfpBoard
from bridge.dashboard.readmodel import readmodel_snapshot
from bridge.edge.a2ui import encode_a2ui_view
from bridge.extraction.gemini import ExtractionTruncated, GeminiExtractor
from bridge.portal import session as portal
from bridge.portal.server import build_portal_app
from bridge.portal.store import InMemoryPortalStore
from bridge.rfp import (
    load_rfp_policy,
    open_rfp,
    receive,
    rfp_status,
    to_exchange_turn,
)
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import RequirementStatus, RfpItemStatus
from contract.extraction import ExtractionResult
from contract.wire import (
    CollectRequest,
    ExchangeTurn,
    Requirement,
    RequirementsList,
    decode_wire,
)
from tests.perf.fake_gemini import PageAwareFakeClient
from tests.perf.scale_corpus import (
    ScaleDoc,
    build_scale_pdf,
    key_fields,
    quote_50_pages,
    rate_rows,
    rate_sheet_1000,
)

PROCESS = "benefits-rfp"  # the process skill under test (named here, never in bridge/)
CANDIDATES = ["benefits-quote"]
# The schema's product_line enum has 4 values; ACCIDENT is the 5th line an RFP asks for,
# so ``receive`` is ALWAYS told the product line explicitly.
PRODUCT_LINES = (
    "AD_AND_D",
    "CRITICAL_ILLNESS",
    "HOSPITAL_INDEMNITY",
    "LIFE",
    "ACCIDENT",
)
OPENED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
NOW = OPENED + timedelta(days=3)
FAKE_LATENCY_S = 0.05
TICK_S = 0.005


def canonical_cell(value: str) -> str:
    """TEST-LOCAL canonicalizer for the live leg (precedent S0-arch-9): strip ``$``/``USD``,
    casefold, drop an ``Age `` / ``Tier `` / ``Tobacco `` / ``Benefit `` label echo."""
    text = value.replace("$", "").replace("USD", "").replace(",", "").strip().casefold()
    for label in ("age ", "tier ", "tobacco ", "benefit "):
        if text.startswith(label):
            text = text[len(label) :]
    return text.strip()


def rows_match(got: list[dict], want: list[dict], *, canonical: bool) -> int:
    """How many rows equal the oracle row at the same index (exact, or canonicalized)."""

    def norm(row: dict) -> dict:
        return {k: canonical_cell(v) for k, v in row.items()} if canonical else row

    return sum(1 for g, w in zip(got, want, strict=False) if norm(g) == norm(w))


class _Static:
    """An extraction-port double returning one fixed result (the portal leg)."""

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result

    async def extract(self, artifact_name: str) -> ExtractionResult:
        return self.result


def quote_result(rows: int, product_line: str) -> ExtractionResult:
    """An oracle-built, gate-clean quote result carrying ``rows`` rate rows."""
    return ExtractionResult.model_validate(
        {
            "fields": {
                "doctype": "benefits-quote",
                "issuer": "Synthetic Mutual",
                "key_fields": key_fields(product_line),
                "tables": {"rates": rate_rows(rows)},
            },
            "overall_confidence": 0.93,
            "field_confidence": {"currency": 0.99},
            "legible": True,
            "flagged_fields": [],
        }
    )


async def _ticking(coro) -> tuple[Any, int, float]:
    """Run ``coro`` while a ticker counts event-loop turns; (result, ticks, seconds)."""
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(TICK_S)
            ticks += 1

    task = asyncio.create_task(ticker())
    start = time.perf_counter()
    try:
        result = await coro
    finally:
        elapsed = time.perf_counter() - start
        stop.set()
        await task
    return result, ticks, elapsed


async def extract_doc(
    doc: ScaleDoc, *, live: bool, chunk_pages: int | None = None
) -> dict:
    """Extract one scale document; the measurement row + correctness verdict."""
    pdf = build_scale_pdf(doc)
    client = None if live else PageAwareFakeClient(doc, latency_s=FAKE_LATENCY_S)
    extractor = GeminiExtractor(
        client=client,
        resolver=lambda name: (pdf, "application/pdf"),
        chunk_pages=chunk_pages,
    )
    out: dict = {"doc": doc.name, "pages": doc.total_pages, "pdf_bytes": len(pdf)}
    try:
        result, ticks, elapsed = await _ticking(extractor.extract(f"{doc.name}.pdf"))
    except ExtractionTruncated as exc:
        out.update(error="ExtractionTruncated", message=str(exc))
        result, ticks, elapsed = None, 0, 0.0
    out.update(seconds=round(elapsed, 3), loop_ticks=ticks)
    if client is not None:
        out.update(
            calls=len(client.calls),
            window_calls=len(client.window_calls),
            survey_calls=len(client.survey_calls),
            truncated_calls=sum(1 for c in client.calls if c["truncated"]),
            max_in_flight=client.max_in_flight,
        )
    if result is not None:
        want = doc.tables
        out["result"] = result
        for table, rows in want.items():
            got = result.fields.tables.get(table, [])
            out[f"{table}_rows"] = len(got)
            out[f"{table}_expected"] = len(rows)
            out[f"{table}_exact"] = rows_match(got, rows, canonical=False)
            if live:
                out[f"{table}_canonical"] = rows_match(got, rows, canonical=True)
        out["key_fields_exact"] = result.fields.key_fields == doc.key_fields
    return out


def _timed(fn, *args, **kwargs) -> tuple[Any, float]:
    start = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, time.perf_counter() - start


async def run(args: argparse.Namespace) -> tuple[dict, list[tuple[str, bool]]]:
    m: dict = {}
    checks: list[tuple[str, bool]] = []

    # 1. extraction ------------------------------------------------------------------
    sheet = await extract_doc(rate_sheet_1000(), live=args.live)
    big = sheet.pop("result", None)
    m["rate_sheet_1000"] = sheet
    quote = await extract_doc(quote_50_pages(), live=args.live)
    quote.pop("result", None)
    m["quote_50p"] = quote
    if args.live:
        for name, row in (("rate-sheet-1000", sheet), ("quote-50p", quote)):
            checks.append(
                (
                    f"{name}: rate row count exact",
                    row.get("rates_rows") == row.get("rates_expected"),
                )
            )
            checks.append(
                (
                    f"{name}: >= 99% rate rows match (canonical)",
                    row.get("rates_canonical", 0)
                    >= 0.99 * row.get("rates_expected", 1),
                )
            )
    else:
        checks += [
            ("rate-sheet-1000: 26 pages", sheet["pages"] == 26),
            ("rate-sheet-1000: 4 window calls", sheet.get("window_calls") == 4),
            (
                "rate-sheet-1000: 1000/1000 rows exact & ordered",
                sheet.get("rates_exact") == 1000 == sheet.get("rates_rows"),
            ),
            (
                "rate-sheet-1000: key fields exact",
                sheet.get("key_fields_exact") is True,
            ),
            (
                "rate-sheet-1000: 2 <= max in-flight <= 4",
                2 <= sheet.get("max_in_flight", 0) <= 4,
            ),
            ("rate-sheet-1000: loop not blocked (ticks > 0)", sheet["loop_ticks"] > 0),
            (
                "quote-50p: 50 pages, 7 windows",
                quote["pages"] == 50 and quote.get("window_calls") == 7,
            ),
            (
                "quote-50p: 1380 rate rows exact",
                quote.get("rates_exact") == 1380 == quote.get("rates_rows"),
            ),
            (
                "quote-50p: 60 design rows exact",
                quote.get("design_exact") == 60 == quote.get("design_rows"),
            ),
        ]
        before = await extract_doc(rate_sheet_1000(), live=False, chunk_pages=0)
        m["rate_sheet_1000_unchunked"] = before
        checks.append(
            (
                "unchunked (chunk_pages=0): ExtractionTruncated after exactly 1 call",
                before.get("error") == "ExtractionTruncated"
                and before.get("calls") == 1,
            )
        )

    # 2. ledger ------------------------------------------------------------------------
    if big is None:
        big = quote_result(args.rows_per_entry, "CRITICAL_ILLNESS")
    signals = signals_from_result(big, candidate_doctypes=CANDIDATES)
    verdict = dispose_signals(signals)
    entry, t_project = _timed(
        project_ledger_entry, doc_id="rate-sheet-1000", result=big, verdict=verdict
    )
    entry_json, t_dump = _timed(entry.model_dump_json)
    m["ledger"] = {
        "rows": len(entry.tables.get("rates", [])),
        "disposition": verdict.disposition.value,
        "project_seconds": round(t_project, 4),
        "dump_seconds": round(t_dump, 4),
        "entry_bytes": len(entry_json),
    }
    checks.append(
        (
            "ledger: 1000-row quote dispositions accepted",
            verdict.disposition.value == "accepted",
        )
    )

    # 3. rfp ---------------------------------------------------------------------------
    policy = load_rfp_policy(PROCESS)
    carriers = [f"carrier-{i:02d}" for i in range(1, args.carriers + 1)]
    lines = list(PRODUCT_LINES[: args.products])
    request = CollectRequest(
        process=PROCESS,
        party="acme-broker",
        context_id="rfp-scale",
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item=line,
                    party=c,
                    status=RequirementStatus.required,
                    doctype_hint="benefits-quote",
                )
                for c in carriers
                for line in lines
            ]
        ),
        due_at=DUE,
    )
    rfp = open_rfp(request, now=OPENED, rfp_id="scale-rfp")
    start = time.perf_counter()
    for c in carriers:
        for line in lines:
            result = quote_result(args.rows_per_entry, line)
            v = dispose_signals(
                signals_from_result(result, candidate_doctypes=CANDIDATES)
            )
            e = project_ledger_entry(doc_id=f"{c}-{line}", result=result, verdict=v)
            rfp = receive(rfp, c, e, now=NOW, policy=policy, product_line=line)
    t_receive = time.perf_counter() - start
    view, t_status = _timed(rfp_status, rfp, now=NOW, policy=policy)
    received = view.counts.get(RfpItemStatus.received, 0)
    turn = to_exchange_turn(rfp, now=NOW, policy=policy)
    turn_json, t_turn_dump = _timed(turn.model_dump_json)
    decoded, t_decode = _timed(decode_wire, ExchangeTurn, json.loads(turn_json))
    cells = len(carriers) * len(lines)
    m["rfp"] = {
        "carriers": len(carriers),
        "products": len(lines),
        "cells": cells,
        "rows_per_entry": args.rows_per_entry,
        "received": received,
        "waiting": view.waiting,
        "receive_all_seconds": round(t_receive, 3),
        "status_seconds": round(t_status, 4),
        "exchange_turn_bytes": len(turn_json),
        "exchange_turn_dump_seconds": round(t_turn_dump, 3),
        "decode_wire_seconds": round(t_decode, 3),
    }
    checks += [
        (
            f"rfp {len(carriers)}x{len(lines)}: {cells}/{cells} cells received",
            received == cells,
        ),
        (f"rfp {len(carriers)}x{len(lines)}: waiting == 0", view.waiting == 0),
        (
            "rfp: ExchangeTurn round-trips through decode_wire",
            len(decoded.status.entries) == cells
            and all(
                len(x.tables.get("rates", [])) == args.rows_per_entry
                for x in decoded.status.entries
            ),
        ),
    ]

    # 4. portal ------------------------------------------------------------------------
    store = InMemoryPortalStore()
    rec = portal.start_session(store, process=PROCESS)
    start = time.perf_counter()
    rec = await portal.upload(
        store,
        rec.session_id,
        doc_id="rate-sheet-1000",
        artifact_name="rate-sheet-1000.pdf",
        extractor=_Static(big),
    )
    t_upload = time.perf_counter() - start
    payload, t_view = _timed(lambda: encode_a2ui_view(portal.current_view(rec)))
    view_bytes = len(json.dumps(payload))
    app = build_portal_app(store=store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://portal"
    ) as hc:
        start = time.perf_counter()
        resp = await hc.get(f"/sessions/{rec.session_id}/view")
        t_http = time.perf_counter() - start
    m["portal"] = {
        "upload_seconds": round(t_upload, 3),
        "view_encode_seconds": round(t_view, 4),
        "view_bytes": view_bytes,
        "http_view_seconds": round(t_http, 3),
        "http_view_bytes": len(resp.content),
        "view_kind": payload.get("kind"),
    }
    checks.append(
        (
            "portal: view encodes and GET /view answers 200",
            resp.status_code == 200 and view_bytes > 0,
        )
    )

    # 5. dashboard / SSE ---------------------------------------------------------------
    board = RfpBoard(policy=policy, clock=lambda: NOW)
    board.put(rfp)
    snap, t_snap = _timed(readmodel_snapshot, store, rfps=board)
    snap_json, t_snap_dump = _timed(json.dumps, snap)
    app = build_dashboard_app(store=store, rfps=board, max_ticks=3)
    first_frame: str | None = None
    frames = 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dash"
    ) as hc:
        start = time.perf_counter()
        resp = await hc.get("/exchanges")
        t_exchanges = time.perf_counter() - start
        exchanges_bytes = len(resp.content)
        start = time.perf_counter()
        t_first = None
        async with hc.stream("GET", "/events") as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    frames += 1
                    if first_frame is None:
                        t_first = time.perf_counter() - start
                        first_frame = line[6:]
        t_stream = time.perf_counter() - start
    rfp_row = None
    if first_frame is not None:
        frame = json.loads(first_frame)
        rfp_row = next((x for x in frame["exchanges"] if "rfp" in x), None)
    m["dashboard"] = {
        "snapshot_seconds": round(t_snap, 3),
        "snapshot_json_seconds": round(t_snap_dump, 3),
        "snapshot_bytes": len(snap_json),
        "get_exchanges_seconds": round(t_exchanges, 3),
        "get_exchanges_bytes": exchanges_bytes,
        "sse_first_frame_seconds": round(t_first or 0.0, 3),
        "sse_first_frame_bytes": len(first_frame or "") + len("data: \n\n"),
        "sse_frames": frames,
        "sse_stream_seconds": round(t_stream, 3),
        "exchanges_in_snapshot": len(snap["exchanges"]),
    }
    checks.append(
        (
            f"dashboard: first SSE frame carries the RFP row with {cells} cells",
            rfp_row is not None and len(rfp_row["rfp"]["cells"]) == cells,
        )
    )
    return m, checks


def _print(m: dict, checks: list[tuple[str, bool]]) -> None:
    print("S0-perf-1 RFP-scale harness")
    print(f"{'section':<28} {'metric':<30} value")
    print("-" * 80)
    for section, values in m.items():
        for key, value in values.items():
            print(f"{section:<28} {key:<30} {value}")
    print("-" * 80)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    failed = sum(1 for _, ok in checks if not ok)
    print(f"{len(checks) - failed}/{len(checks)} correctness checks passed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.perf.harness")
    parser.add_argument("--json", metavar="PATH")
    parser.add_argument("--rows-per-entry", type=int, default=1000)
    parser.add_argument("--carriers", type=int, default=10)
    parser.add_argument("--products", type=int, default=5, choices=range(1, 6))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    m, checks = asyncio.run(run(args))
    _print(m, checks)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(
                {"measurements": m, "checks": dict(checks)}, fh, indent=2, default=str
            )
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
