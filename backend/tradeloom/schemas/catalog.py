"""Instrument, strategy, setup and tag contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from tradeloom.core.enums import (
    AssetType,
    ParameterType,
    StrategyKind,
    StrategyStatus,
)
from tradeloom.schemas.common import TradeloomModel

# --- instruments -----------------------------------------------------------


class InstrumentCreate(TradeloomModel):
    symbol: str = Field(min_length=1, max_length=40)
    name: str | None = Field(default=None, max_length=160)
    asset_type: AssetType
    exchange: str | None = Field(default=None, max_length=40)
    currency: str = Field(default="USD", max_length=8)
    tick_size: Decimal = Field(default=Decimal("0.01"), gt=0)
    contract_multiplier: Decimal = Field(default=Decimal(1), gt=0)
    lot_size: Decimal = Field(default=Decimal(1), gt=0)
    price_precision: int = Field(default=2, ge=0, le=10)
    expires_on: date | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return value.strip().upper()


class InstrumentUpdate(TradeloomModel):
    name: str | None = Field(default=None, max_length=160)
    exchange: str | None = Field(default=None, max_length=40)
    tick_size: Decimal | None = Field(default=None, gt=0)
    contract_multiplier: Decimal | None = Field(default=None, gt=0)
    lot_size: Decimal | None = Field(default=None, gt=0)
    price_precision: int | None = Field(default=None, ge=0, le=10)
    is_active: bool | None = None
    expires_on: date | None = None
    metadata_json: dict[str, Any] | None = None


class InstrumentRead(TradeloomModel):
    id: Any
    organization_id: Any | None
    symbol: str
    name: str | None
    asset_type: AssetType
    exchange: str | None
    currency: str
    tick_size: Decimal
    contract_multiplier: Decimal
    lot_size: Decimal
    price_precision: int
    is_active: bool
    expires_on: date | None
    #: True when the row comes from the shared catalogue rather than this workspace.
    is_global: bool = False


class InstrumentAliasCreate(TradeloomModel):
    alias: str = Field(min_length=1, max_length=60)
    source: str = Field(default="*", max_length=40)


# --- tags ------------------------------------------------------------------


class TagCreate(TradeloomModel):
    name: str = Field(min_length=1, max_length=60)
    category: str = Field(default="custom", max_length=32)
    color: str | None = Field(default=None, max_length=16)
    description: str | None = Field(default=None, max_length=255)


class TagUpdate(TradeloomModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    category: str | None = Field(default=None, max_length=32)
    color: str | None = Field(default=None, max_length=16)
    description: str | None = Field(default=None, max_length=255)


class TagRead(TradeloomModel):
    id: Any
    name: str
    slug: str
    category: str
    color: str | None
    description: str | None
    created_at: datetime
    #: Populated by the list endpoint so the tag manager can show usage without an N+1.
    trade_count: int = 0


# --- setups ----------------------------------------------------------------


class SetupCreate(TradeloomModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    strategy_id: Any | None = None
    color: str | None = Field(default=None, max_length=16)
    checklist: dict[str, Any] = Field(default_factory=dict)


class SetupUpdate(TradeloomModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    strategy_id: Any | None = None
    color: str | None = Field(default=None, max_length=16)
    checklist: dict[str, Any] | None = None
    is_active: bool | None = None


class SetupRead(TradeloomModel):
    id: Any
    name: str
    description: str | None
    strategy_id: Any | None
    color: str | None
    checklist: dict[str, Any]
    is_active: bool
    created_at: datetime
    trade_count: int = 0


# --- strategies ------------------------------------------------------------


class ParameterSpec(TradeloomModel):
    """Declared bounds for a strategy parameter. Enforced server-side before a run is queued."""

    name: str = Field(min_length=1, max_length=60)
    label: str | None = Field(default=None, max_length=120)
    param_type: ParameterType
    default_value: str | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    step: Decimal | None = None
    choices: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=500)
    display_order: int = 0


class StrategyCreate(TradeloomModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    kind: StrategyKind = StrategyKind.JOURNAL_ONLY
    #: Must be a key registered in the engine. Arbitrary values are rejected — never executed.
    engine_key: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=16)
    playbook: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)


class StrategyUpdate(TradeloomModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    status: StrategyStatus | None = None
    color: str | None = Field(default=None, max_length=16)
    playbook: dict[str, Any] | None = None


class StrategyVersionCreate(TradeloomModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class StrategyVersionRead(TradeloomModel):
    id: Any
    strategy_id: Any
    version: int
    engine_key: str | None
    parameters: dict[str, Any]
    notes: str | None
    is_published: bool
    created_at: datetime


class StrategyRead(TradeloomModel):
    id: Any
    name: str
    description: str | None
    kind: StrategyKind
    engine_key: str | None
    status: StrategyStatus
    color: str | None
    playbook: dict[str, Any]
    current_version_id: Any | None
    created_at: datetime
    updated_at: datetime
    trade_count: int = 0
    #: Realised performance of journal trades tagged with this strategy.
    net_pnl: Decimal | None = None
    win_rate: Decimal | None = None


class StrategyDetail(TradeloomModel):
    strategy: StrategyRead
    versions: list[StrategyVersionRead] = Field(default_factory=list)
    parameter_specs: list[ParameterSpec] = Field(default_factory=list)


class EngineStrategyInfo(TradeloomModel):
    """A built-in strategy available to the backtester."""

    key: str
    name: str
    description: str
    category: str
    parameters: list[ParameterSpec]


__all__ = [
    "EngineStrategyInfo",
    "InstrumentAliasCreate",
    "InstrumentCreate",
    "InstrumentRead",
    "InstrumentUpdate",
    "ParameterSpec",
    "SetupCreate",
    "SetupRead",
    "SetupUpdate",
    "StrategyCreate",
    "StrategyDetail",
    "StrategyRead",
    "StrategyUpdate",
    "StrategyVersionCreate",
    "StrategyVersionRead",
    "TagCreate",
    "TagRead",
    "TagUpdate",
]
