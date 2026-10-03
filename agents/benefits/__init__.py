"""Simulated benefits carriers as real external-zone A2A agents (S1-benefits-6, CUJ-12).

Two cross-framework counterparties the Bridge dials over A2A: **Carrier A in Google ADK**
(``carrier_a_adk``) and **Carrier B in LangGraph** (``carrier_b_langgraph``). Both speak
the same framework-neutral wire (``wire``) and serve quotes read-only from the benefits
eval oracle (``quotes``), proving one A2A conversation succeeds against both builds.

External zone: never imports ``bridge/`` (the reverse isolation guard).
"""

from agents.benefits.carrier_a_adk import build_carrier_a_app
from agents.benefits.carrier_b_langgraph import build_carrier_b_app
from agents.benefits.server import build_carrier_card

__all__ = ["build_carrier_a_app", "build_carrier_b_app", "build_carrier_card"]
