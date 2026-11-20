"""Declarative collection-strategy loading and deterministic evaluation (ADR-14)."""

from bridge.strategy.evaluator import (
    evaluate_process_sufficiency,
    evaluate_strategy,
    waiver_recommendations_from_advisories,
)
from bridge.strategy.loader import (
    DEFAULT_STRATEGY_ASSET,
    load_strategy,
    load_strategy_from_yaml,
    render_satisfaction_prose,
    strategy_for,
)
from bridge.strategy.models import (
    CollectionStrategy,
    StrategyAlternative,
    StrategyLoadError,
    StrategyRule,
)

__all__ = [
    "DEFAULT_STRATEGY_ASSET",
    "CollectionStrategy",
    "StrategyAlternative",
    "StrategyLoadError",
    "StrategyRule",
    "evaluate_process_sufficiency",
    "evaluate_strategy",
    "load_strategy",
    "load_strategy_from_yaml",
    "render_satisfaction_prose",
    "strategy_for",
    "waiver_recommendations_from_advisories",
]
