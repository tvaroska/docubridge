"""Bridge ADK integration core (experimental-surface register + Ring-3 discipline artifacts)."""
from __future__ import annotations

from bridge.adk.experimental import (
    ADK_PIN,
    EXPERIMENTAL_SURFACES,
    KNOWN_EXPERIMENTAL_ADK_SYMBOLS,
    ExperimentalSurface,
    registered_symbols,
)

__all__ = [
    "ADK_PIN",
    "EXPERIMENTAL_SURFACES",
    "KNOWN_EXPERIMENTAL_ADK_SYMBOLS",
    "ExperimentalSurface",
    "registered_symbols",
]
