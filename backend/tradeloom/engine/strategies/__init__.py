"""Built-in strategies.

These are reference implementations, not trading advice. Each one is deliberately simple so the
*engine's* behaviour is what a backtest measures, and each declares explicit parameter bounds so
the API can validate a configuration before queuing a job.
"""

from tradeloom.engine.strategies.breakout import BreakoutStrategy
from tradeloom.engine.strategies.ema_cross import EmaCrossStrategy
from tradeloom.engine.strategies.rsi_reversion import RsiMeanReversionStrategy
from tradeloom.engine.strategies.sma_cross import SmaCrossStrategy
from tradeloom.engine.strategies.trend_following import TrendFollowingStrategy

__all__ = [
    "BreakoutStrategy",
    "EmaCrossStrategy",
    "RsiMeanReversionStrategy",
    "SmaCrossStrategy",
    "TrendFollowingStrategy",
]
