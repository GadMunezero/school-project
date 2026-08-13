"""Tradeloom backtesting engine.

A deterministic, event-driven simulator with no dependency on the rest of the application: it
imports nothing from ``tradeloom.models``, ``tradeloom.services`` or any database module. That
isolation is what makes it testable in milliseconds and reusable by both the batch backtester and
the interactive replay session.

Determinism guarantee
---------------------
Given the same bars, the same configuration and the same engine version, a run produces
byte-identical results. There is no wall-clock access, no randomness, and no dictionary-ordering
dependence anywhere in the execution path. ``BacktestRun.input_digest`` hashes the inputs so two
runs can be compared for genuine equivalence.

See ``docs/BACKTESTING.md`` for the execution model, the look-ahead guarantees and the exact
definition of every timestamp.
"""

from tradeloom.engine.bars import Bar, BarSeries
from tradeloom.engine.config import (
    BacktestConfig,
    CommissionConfig,
    RiskConfig,
    SessionConfig,
    SlippageConfig,
    SpreadConfig,
)
from tradeloom.engine.events import (
    FillEvent,
    MarketDataEvent,
    OrderEvent,
    SignalEvent,
)
from tradeloom.engine.performance import PerformanceAnalyzer, PerformanceReport
from tradeloom.engine.registry import STRATEGY_REGISTRY, get_strategy, list_strategies
from tradeloom.engine.runner import BacktestResult, BacktestRunner
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameter
from tradeloom.engine.version import ENGINE_VERSION

__all__ = [
    "ENGINE_VERSION",
    "STRATEGY_REGISTRY",
    "BacktestConfig",
    "BacktestResult",
    "BacktestRunner",
    "Bar",
    "BarSeries",
    "CommissionConfig",
    "FillEvent",
    "MarketDataEvent",
    "OrderEvent",
    "PerformanceAnalyzer",
    "PerformanceReport",
    "RiskConfig",
    "SessionConfig",
    "SignalEvent",
    "SlippageConfig",
    "SpreadConfig",
    "Strategy",
    "StrategyContext",
    "StrategyParameter",
    "get_strategy",
    "list_strategies",
]
