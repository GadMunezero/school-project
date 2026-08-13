"""Strategy interface.

A strategy is a plain Python class implementing lifecycle hooks. It never touches the broker
directly: it emits :class:`~tradeloom.engine.events.SignalEvent` objects through the context, and
the risk manager decides size and whether the trade is allowed at all. That separation is what
makes position sizing a *portfolio* decision rather than something each strategy reinvents.

Safety
------
Strategies are selected by key from :mod:`tradeloom.engine.registry`. User input never becomes
code: an unknown key is rejected before a job is queued, and there is no ``eval``, ``exec``,
import-by-name, or pickle-loading anywhere in this package. Supporting user-authored strategies
later would require a separate sandboxed worker — the interface is ready for it, the capability
is deliberately absent. See ``docs/SECURITY.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from tradeloom.core.enums import Direction, ParameterType
from tradeloom.core.money import ZERO, to_decimal
from tradeloom.engine.bars import Bar, BarSeries
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.events import SignalDirection, SignalEvent
from tradeloom.engine.portfolio import Portfolio, SimPosition


class StrategyParameterError(ValueError):
    """Raised when a supplied parameter violates its declared schema."""


@dataclass(frozen=True, slots=True)
class StrategyParameter:
    """Declared parameter with bounds the server enforces before a run is queued."""

    name: str
    param_type: ParameterType
    default: Any
    label: str | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    step: Decimal | None = None
    choices: tuple[str, ...] = ()
    description: str = ""

    def coerce(self, value: Any) -> Any:
        """Validate and convert a supplied value, or raise :class:`StrategyParameterError`."""
        if value is None:
            return self.default

        if self.param_type is ParameterType.INTEGER:
            try:
                parsed: Any = int(value)
            except (TypeError, ValueError) as exc:
                raise StrategyParameterError(f"{self.name} must be a whole number") from exc
        elif self.param_type is ParameterType.DECIMAL:
            try:
                parsed = to_decimal(value)
            except Exception as exc:
                raise StrategyParameterError(f"{self.name} must be a number") from exc
        elif self.param_type is ParameterType.BOOLEAN:
            if isinstance(value, bool):
                parsed = value
            elif str(value).lower() in {"true", "1", "yes"}:
                parsed = True
            elif str(value).lower() in {"false", "0", "no"}:
                parsed = False
            else:
                raise StrategyParameterError(f"{self.name} must be true or false")
        elif self.param_type is ParameterType.CHOICE:
            parsed = str(value)
            if self.choices and parsed not in self.choices:
                raise StrategyParameterError(
                    f"{self.name} must be one of: {', '.join(self.choices)}"
                )
        else:
            parsed = str(value)

        if self.param_type in (ParameterType.INTEGER, ParameterType.DECIMAL):
            numeric = to_decimal(parsed)
            if self.minimum is not None and numeric < self.minimum:
                raise StrategyParameterError(f"{self.name} must be at least {self.minimum}")
            if self.maximum is not None and numeric > self.maximum:
                raise StrategyParameterError(f"{self.name} must be at most {self.maximum}")
        return parsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label or self.name.replace("_", " ").title(),
            "param_type": self.param_type.value,
            "default_value": str(self.default),
            "minimum": str(self.minimum) if self.minimum is not None else None,
            "maximum": str(self.maximum) if self.maximum is not None else None,
            "step": str(self.step) if self.step is not None else None,
            "choices": list(self.choices),
            "description": self.description,
        }


@dataclass(slots=True)
class StrategyContext:
    """What a strategy can see and do on the current bar.

    Deliberately narrow: the context exposes the current bar, the bars *already seen*, the open
    position and the portfolio. There is no accessor for future bars, because the runner only
    ever hands over a series truncated at the current index.
    """

    config: BacktestConfig
    bar: Bar
    index: int
    history: BarSeries
    portfolio: Portfolio
    #: Signals raised on this bar, drained by the runner after ``on_bar`` returns.
    signals: list[SignalEvent] = field(default_factory=list)
    #: Non-fatal notes surfaced to the user alongside the results.
    warnings: list[str] = field(default_factory=list)

    @property
    def timestamp(self) -> datetime:
        return self.bar.opened_at

    @property
    def position(self) -> SimPosition | None:
        return self.portfolio.position

    @property
    def is_flat(self) -> bool:
        return self.portfolio.is_flat

    @property
    def is_long(self) -> bool:
        return self.position is not None and self.position.direction is Direction.LONG

    @property
    def is_short(self) -> bool:
        return self.position is not None and self.position.direction is Direction.SHORT

    @property
    def equity(self) -> Decimal:
        return self.portfolio.equity(self.bar.close)

    def bars_since_entry(self) -> int | None:
        if self.position is None:
            return None
        entry = self.position.opened_at
        return sum(1 for candle in self.history if candle.opened_at >= entry)

    # -- signal helpers ------------------------------------------------------

    def enter_long(
        self,
        *,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        quantity: Decimal | None = None,
        strength: Decimal = Decimal(1),
        reason: str | None = None,
    ) -> None:
        self.signals.append(
            SignalEvent(
                timestamp=self.timestamp,
                direction=SignalDirection.ENTER_LONG,
                strength=strength,
                stop_loss=stop_loss,
                take_profit=take_profit,
                quantity=quantity,
                reason=reason,
            )
        )

    def enter_short(
        self,
        *,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        quantity: Decimal | None = None,
        strength: Decimal = Decimal(1),
        reason: str | None = None,
    ) -> None:
        self.signals.append(
            SignalEvent(
                timestamp=self.timestamp,
                direction=SignalDirection.ENTER_SHORT,
                strength=strength,
                stop_loss=stop_loss,
                take_profit=take_profit,
                quantity=quantity,
                reason=reason,
            )
        )

    def close_position(self, reason: str | None = None) -> None:
        if self.position is None:
            return
        direction = (
            SignalDirection.EXIT_LONG
            if self.position.direction is Direction.LONG
            else SignalDirection.EXIT_SHORT
        )
        self.signals.append(
            SignalEvent(timestamp=self.timestamp, direction=direction, reason=reason)
        )

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


class Strategy(ABC):
    """Base class for every built-in strategy.

    Only :meth:`on_bar` is required. The remaining hooks exist so a strategy can react to
    execution events without polling, and all have no-op defaults.
    """

    #: Registry key. Must be unique and stable — stored runs reference it.
    key: str = ""
    name: str = ""
    description: str = ""
    category: str = "general"
    parameters: tuple[StrategyParameter, ...] = ()

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = self.resolve_parameters(params or {})

    @classmethod
    def resolve_parameters(cls, supplied: dict[str, Any]) -> dict[str, Any]:
        """Validate supplied values against the declared schema, filling in defaults.

        Unknown keys are rejected rather than ignored: silently dropping a misspelled parameter
        would run a different strategy than the user configured.
        """
        declared = {spec.name: spec for spec in cls.parameters}
        unknown = set(supplied) - set(declared)
        if unknown:
            raise StrategyParameterError(
                f"Unknown parameter{'s' if len(unknown) > 1 else ''}: {', '.join(sorted(unknown))}"
            )
        return {name: spec.coerce(supplied.get(name)) for name, spec in declared.items()}

    @classmethod
    def parameter_defaults(cls) -> dict[str, Any]:
        return {spec.name: spec.default for spec in cls.parameters}

    # -- lifecycle -----------------------------------------------------------

    def initialize(self, config: BacktestConfig) -> None:
        """Called once before the first bar. Build indicators here."""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> None:
        """Called once per bar, after the bar is complete and after resting orders were matched."""

    def generate_signal(self, ctx: StrategyContext) -> SignalEvent | None:
        """Optional pure-function entry point. Returning a signal queues it."""
        return None

    def calculate_position_size(self, ctx: StrategyContext, signal: SignalEvent) -> Decimal | None:
        """Override to size a specific signal. ``None`` defers to the risk manager."""
        return signal.quantity

    def risk_management(self, ctx: StrategyContext) -> None:
        """Called every bar while a position is open — trail stops, scale out, time-stop."""

    def on_order_fill(self, ctx: StrategyContext, order_id: int, price: Decimal) -> None:
        """Called after each fill."""

    def on_position_open(self, ctx: StrategyContext, position: SimPosition) -> None:
        """Called when a flat account becomes non-flat."""

    def on_position_update(self, ctx: StrategyContext, position: SimPosition) -> None:
        """Called when an existing position's size changes."""

    def on_position_close(self, ctx: StrategyContext) -> None:
        """Called when a position is fully closed."""

    def finalize(self, ctx: StrategyContext | None) -> None:
        """Called once after the last bar."""


def parameter_value(params: dict[str, Any], name: str, default: Decimal = ZERO) -> Decimal:
    """Read a numeric parameter as a Decimal."""
    return to_decimal(params.get(name, default), default=default)


__all__ = [
    "Strategy",
    "StrategyContext",
    "StrategyParameter",
    "StrategyParameterError",
    "parameter_value",
]
