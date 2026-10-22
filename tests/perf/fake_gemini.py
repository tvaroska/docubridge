"""``PageAwareFakeClient`` — a genai ``Client``-shaped double that knows the document's pages.

It implements only ``.models.generate_content(model=, contents=, config=)`` (sync, like
the real client and every other fake in the suite) and answers from a
:class:`tests.perf.scale_corpus.ScaleDoc` oracle:

- the page-count **survey** prompt → ``{"page_count": N}``;
- a request carrying the ``[bridge-page-window a-b/N]`` sentinel → the rows printed on
  pages a-b (whole-document scalars as usual);
- anything else → the whole document.

**Output budget.** The answer's estimated size is ``rows x tokens_per_row +
base_tokens``; past ``config["max_output_tokens"]`` the JSON text is cut to the budget
fraction and the response carries ``candidates=[finish_reason="MAX_TOKENS"]`` — exactly
the live failure mode (the cut body never closes its braces). Otherwise the full JSON
with ``finish_reason="STOP"``.

Thread-safe (the engine calls it via ``asyncio.to_thread``): calls are recorded under a
lock and the max number of concurrent calls is tracked (``max_in_flight``).
``flag_rows`` (GLOBAL rate-row indices) are reported as WINDOW-LOCAL
``fields.tables.rates[i].frequency`` flags + reviews + confidences, which is what proves
row-path rebasing end to end. ``faults`` maps a window ``(a, b)`` (or ``None`` for the
whole document) to ``"bad_then_good"`` / ``"always_bad"`` / ``"error"``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from types import SimpleNamespace

from bridge.extraction.gemini import SURVEY_PROMPT
from tests.perf.scale_corpus import ScaleDoc

_SENTINEL = re.compile(r"^\[bridge-page-window (\d+)-(\d+)/(\d+)\]")
_CORRECTION_MARK = "was REJECTED by schema validation"


class _Models:
    def __init__(self, owner: PageAwareFakeClient) -> None:
        self._owner = owner

    def generate_content(self, *, model, contents, config):
        return self._owner._generate(model=model, contents=contents, config=config)


class PageAwareFakeClient:
    """A page-aware fake Gemini client over a ``ScaleDoc`` oracle (see module docstring)."""

    def __init__(
        self,
        doc: ScaleDoc,
        *,
        tokens_per_row: int = 60,
        base_tokens: int = 400,
        latency_s: float = 0.0,
        flag_rows: tuple[int, ...] = (),
        faults: dict[tuple[int, int] | None, str] | None = None,
        overall_confidence: float = 0.93,
    ) -> None:
        self.doc = doc
        self.tokens_per_row = tokens_per_row
        self.base_tokens = base_tokens
        self.latency_s = latency_s
        self.flag_rows = tuple(flag_rows)
        self.faults = dict(faults or {})
        self.overall_confidence = overall_confidence
        self.models = _Models(self)
        self.calls: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    # -- introspection helpers ------------------------------------------------------
    @property
    def window_calls(self) -> list[dict]:
        return [c for c in self.calls if c["kind"] == "window"]

    @property
    def survey_calls(self) -> list[dict]:
        return [c for c in self.calls if c["kind"] == "survey"]

    # -- the call -----------------------------------------------------------------
    def _generate(self, *, model, contents, config):
        kind, window = self._classify(contents)
        with self._lock:
            index = len(self.calls)
            record = {
                "index": index,
                "model": model,
                "contents": list(contents),
                "config": config,
                "kind": kind,
                "window": window,
                "truncated": False,
            }
            self.calls.append(record)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.latency_s:
                time.sleep(self.latency_s)
            self._check_document(contents)
            if kind == "survey":
                return _response(
                    json.dumps({"page_count": self.doc.total_pages}), "STOP"
                )
            first, last = window or (1, self.doc.total_pages)
            retry = any(isinstance(c, str) and _CORRECTION_MARK in c for c in contents)
            fault = self.faults.get(window)
            if fault == "error":
                raise RuntimeError("503 backend unavailable")
            payload = self._payload(first, last)
            if fault == "always_bad" or (fault == "bad_then_good" and not retry):
                payload["surprise"] = "unexpected"  # extra="forbid" rejects it
            text = json.dumps(payload)
            rows = sum(len(r) for r in payload["fields"].get("tables", {}).values())
            estimate = rows * self.tokens_per_row + self.base_tokens
            budget = (
                config.get("max_output_tokens") if isinstance(config, dict) else None
            )
            if budget is not None and estimate > budget:
                record["truncated"] = True
                cut = max(1, int(len(text) * budget / estimate))
                return _response(text[:cut], "MAX_TOKENS")
            return _response(text, "STOP")
        finally:
            with self._lock:
                self.in_flight -= 1

    def _classify(self, contents) -> tuple[str, tuple[int, int] | None]:
        if contents and contents[0] == SURVEY_PROMPT:
            return "survey", None
        for part in contents:
            if isinstance(part, str):
                match = _SENTINEL.match(part)
                if match:
                    return "window", (int(match[1]), int(match[2]))
        return "single", None

    @staticmethod
    def _check_document(contents) -> None:
        part = contents[1]
        inline = getattr(part, "inline_data", None)
        by_ref = getattr(part, "file_data", None)
        if inline is not None:
            assert inline.mime_type == "application/pdf", inline.mime_type
        elif by_ref is not None:
            assert str(by_ref.file_uri).startswith("gs://"), by_ref.file_uri
        else:  # pragma: no cover - a misuse of the double
            raise AssertionError(
                f"expected a PDF document part, got {type(part).__name__}"
            )

    def _payload(self, first: int, last: int) -> dict:
        doc = self.doc
        tables = doc.tables_for(first, last)
        offset = len(doc.tables_for(1, first - 1).get("rates", [])) if first > 1 else 0
        local_rows = len(tables.get("rates", []))
        flagged: list[str] = []
        reviews: list[dict] = []
        confidence: dict[str, float] = {name: 0.98 for name in doc.key_fields}
        for global_index in self.flag_rows:
            local = global_index - offset
            if 0 <= local < local_rows:
                path = f"fields.tables.rates[{local}].frequency"
                flagged.append(path)
                reviews.append(
                    {
                        "path": path,
                        "reason": "low_confidence",
                        "confidence": 0.41,
                        "detail": "frequency printed ambiguously",
                    }
                )
                confidence[path] = 0.41
        fields: dict = {
            "doctype": doc.doctype,
            "issuer": doc.issuer,
            "key_fields": dict(doc.key_fields),
        }
        if tables:
            fields["tables"] = tables
        return {
            "fields": fields,
            "overall_confidence": self.overall_confidence,
            "field_confidence": confidence,
            "legible": True,
            "flagged_fields": flagged,
            "field_reviews": reviews,
        }


def _response(text: str, finish_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text, candidates=[SimpleNamespace(finish_reason=finish_reason)]
    )
