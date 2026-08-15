"""Strategy registry.

The **only** way to obtain executable strategy logic. Lookup is by key against an explicit
mapping, so a request body can never name a module, a class path, or anything else that could be
imported. An unknown key raises before any work is scheduled.
"""

from __future__ import annotations

from typing import Any

from tradeloom.engine.strategies import (
    BreakoutStrategy,
    EmaCrossStrategy,
    RsiMeanReversionStrategy,
    SmaCrossStrategy,
    TrendFollowingStrategy,
)
from tradeloom.engine.strategy import Strategy

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    strategy.key: strategy
    for strategy in (
        EmaCrossStrategy,
        SmaCrossStrategy,
        RsiMeanReversionStrategy,
        BreakoutStrategy,
        TrendFollowingStrategy,
    )
}


class UnknownStrategyError(KeyError):
    """Raised when a key is not in the registry."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        available = ", ".join(sorted(STRATEGY_REGISTRY))
        return f"Unknown strategy '{self.key}'. Available: {available}"


def get_strategy(key: str) -> type[Strategy]:
    try:
        return STRATEGY_REGISTRY[key]
    except KeyError as exc:
        raise UnknownStrategyError(key) from exc


def is_registered(key: str | None) -> bool:
    return bool(key) and key in STRATEGY_REGISTRY


def build_strategy(key: str, params: dict[str, Any] | None = None) -> Strategy:
    return get_strategy(key)(params or {})


def list_strategies() -> list[dict[str, Any]]:
    """Registry contents in a JSON-serialisable form for the backtester UI."""
    return [
        {
            "key": strategy.key,
            "name": strategy.name,
            "description": strategy.description,
            "category": strategy.category,
            "parameters": [spec.to_dict() for spec in strategy.parameters],
        }
        for strategy in sorted(STRATEGY_REGISTRY.values(), key=lambda s: s.name)
    ]


__all__ = [
    "STRATEGY_REGISTRY",
    "UnknownStrategyError",
    "build_strategy",
    "get_strategy",
    "is_registered",
    "list_strategies",
]
