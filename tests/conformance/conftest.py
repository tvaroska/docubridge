"""Conformance fixtures: both edge routing modes (S3-edge-3).

Every journey runs under ``edge_mode`` ∈ {deterministic, hybrid}. The benefits
conformance traffic is all structured (a ``CollectRequest`` + a by-reference file), so
the hybrid edge routes it to the model-free Collector: the LLM double fails loud on its
first call (``max_calls=0`` → the task FAILs) and teardown asserts it was called 0
times. Not a port (no ``adapter`` fixture): the edge mode is a routing mode (D2).
"""

from __future__ import annotations

import pytest

from tests._scripted_llm import ScriptedLlm
from tests.conformance import journey


@pytest.fixture(params=["deterministic", "hybrid"])
def edge_mode(request, monkeypatch):
    llm = ScriptedLlm(max_calls=0)
    monkeypatch.setattr(
        journey, "EDGE", journey.EdgeConfig(mode=request.param, llm=llm)
    )
    yield request.param
    assert llm.call_count == 0, "structured conformance traffic reached the LLM leg"
