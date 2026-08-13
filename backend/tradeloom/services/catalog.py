"""Instruments, tags, setups and strategies.

Grouped because they share one shape: small tenant-owned reference entities that trades point at.
Each service enforces uniqueness inside the workspace and refuses to hard-delete anything a trade
still references — deleting a strategy must not orphan the trades that used it, so the row is
soft-deleted and existing references keep resolving.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import (
    AuditAction,
    StrategyKind,
    StrategyStatus,
    TradeStatus,
)
from tradeloom.core.errors import ConflictError, NotFoundError, ValidationError
from tradeloom.core.money import HUNDRED, quantize_money, quantize_percent, safe_div
from tradeloom.core.pagination import Page, PageParams
from tradeloom.models.instrument import Instrument, InstrumentAlias
from tradeloom.models.strategy import Setup, Strategy, StrategyParameter, StrategyVersion, Tag
from tradeloom.models.trading import Trade
from tradeloom.repositories.trading import (
    InstrumentRepository,
    SetupRepository,
    StrategyRepository,
    StrategyVersionRepository,
    TagRepository,
)
from tradeloom.schemas.catalog import (
    InstrumentCreate,
    InstrumentUpdate,
    SetupCreate,
    SetupUpdate,
    StrategyCreate,
    StrategyUpdate,
    StrategyVersionCreate,
    TagCreate,
    TagUpdate,
)
from tradeloom.services.audit import AuditService

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    return _SLUG_CLEAN.sub("-", value.strip().lower()).strip("-")[:60] or "tag"


def normalize_alias(alias: str) -> str:
    return "".join(ch for ch in alias.strip().upper() if ch.isalnum())


class InstrumentService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id
        self.repo = InstrumentRepository(session, organization_id)

    async def list(self, params: PageParams, search: str | None = None) -> Page[Instrument]:
        filters = []
        if search:
            needle = f"%{search.strip().lower()}%"
            filters.append(
                func.lower(Instrument.symbol).like(needle)
                | func.lower(func.coalesce(Instrument.name, "")).like(needle)
            )
        return await self.repo.paginate(params, *filters, order_by=[Instrument.symbol.asc()])

    async def get(self, instrument_id: uuid.UUID) -> Instrument:
        instrument = await self.repo.get(instrument_id)
        if instrument is None:
            raise NotFoundError("Instrument not found.")
        return instrument

    async def create(self, payload: InstrumentCreate) -> Instrument:
        existing = await self.repo.by_symbol(payload.symbol)
        if existing is not None and existing.organization_id == self.organization_id:
            raise ConflictError(f"{payload.symbol} already exists in this workspace.")
        instrument = Instrument(
            organization_id=self.organization_id,
            **payload.model_dump(),
        )
        self.session.add(instrument)
        await self.session.flush()
        return instrument

    async def update(self, instrument_id: uuid.UUID, payload: InstrumentUpdate) -> Instrument:
        instrument = await self.get(instrument_id)
        if instrument.organization_id is None:
            # Shared catalogue rows are read-only; a workspace override must be created instead.
            raise ConflictError(
                "This instrument is part of the shared catalogue. "
                "Create a workspace-specific instrument to change its contract details."
            )
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(instrument, field, value)
        await self.session.flush()
        return instrument

    async def resolve(self, symbol: str, source: str = "*") -> Instrument | None:
        """Symbol -> instrument, trying the exact symbol then the alias table."""
        direct = await self.repo.by_symbol(symbol)
        if direct is not None:
            return direct
        return await self.repo.resolve_alias(symbol, source)

    async def add_alias(
        self, instrument_id: uuid.UUID, alias: str, source: str = "*"
    ) -> InstrumentAlias:
        instrument = await self.get(instrument_id)
        normalized = normalize_alias(alias)
        if not normalized:
            raise ValidationError("Alias must contain at least one alphanumeric character.")
        record = InstrumentAlias(
            organization_id=self.organization_id,
            instrument_id=instrument.id,
            alias=alias.strip(),
            alias_normalized=normalized,
            source=source,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def aliases(self, instrument_id: uuid.UUID) -> list[InstrumentAlias]:
        result = await self.session.execute(
            select(InstrumentAlias)
            .where(InstrumentAlias.instrument_id == instrument_id)
            .order_by(InstrumentAlias.alias.asc())
        )
        return list(result.scalars().all())


class TagService:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.repo = TagRepository(session, organization_id)

    async def list_with_counts(self) -> list[tuple[Tag, int]]:
        from tradeloom.models.trading import TradeTag

        tags = await self.repo.list(order_by=[Tag.category.asc(), Tag.name.asc()])
        counts = await self.session.execute(
            select(TradeTag.tag_id, func.count())
            .where(TradeTag.organization_id == self.organization_id)
            .group_by(TradeTag.tag_id)
        )
        lookup = dict(counts.all())
        return [(tag, int(lookup.get(tag.id, 0))) for tag in tags]

    async def create(self, payload: TagCreate) -> Tag:
        slug = slugify(payload.name)
        if await self.repo.by_slug(slug) is not None:
            raise ConflictError("A tag with that name already exists.")
        tag = Tag(
            organization_id=self.organization_id,
            name=payload.name.strip(),
            slug=slug,
            category=payload.category,
            color=payload.color,
            description=payload.description,
        )
        self.session.add(tag)
        await self.session.flush()
        return tag

    async def update(self, tag_id: uuid.UUID, payload: TagUpdate) -> Tag:
        tag = await self.repo.get(tag_id)
        if tag is None:
            raise NotFoundError("Tag not found.")
        data = payload.model_dump(exclude_unset=True)
        if data.get("name"):
            slug = slugify(data["name"])
            clash = await self.repo.by_slug(slug)
            if clash is not None and clash.id != tag.id:
                raise ConflictError("A tag with that name already exists.")
            tag.slug = slug
        for field, value in data.items():
            setattr(tag, field, value)
        await self.session.flush()
        return tag

    async def delete(self, tag_id: uuid.UUID) -> None:
        tag = await self.repo.get(tag_id)
        if tag is None:
            raise NotFoundError("Tag not found.")
        # Soft delete: trade_tags rows stay, so historical trades keep their labels.
        await self.repo.soft_delete(tag.id)


class SetupService:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.repo = SetupRepository(session, organization_id)

    async def list_with_counts(self) -> list[tuple[Setup, int]]:
        setups = await self.repo.list(order_by=[Setup.name.asc()])
        counts = await self.session.execute(
            select(Trade.setup_id, func.count())
            .where(
                Trade.organization_id == self.organization_id,
                Trade.deleted_at.is_(None),
                Trade.setup_id.isnot(None),
            )
            .group_by(Trade.setup_id)
        )
        lookup = dict(counts.all())
        return [(setup, int(lookup.get(setup.id, 0))) for setup in setups]

    async def get(self, setup_id: uuid.UUID) -> Setup:
        setup = await self.repo.get(setup_id)
        if setup is None:
            raise NotFoundError("Setup not found.")
        return setup

    async def create(self, payload: SetupCreate) -> Setup:
        existing = await self.repo.list(func.lower(Setup.name) == payload.name.strip().lower())
        if existing:
            raise ConflictError("A setup with that name already exists.")
        if payload.strategy_id is not None:
            strategies = StrategyRepository(self.session, self.organization_id)
            if not await strategies.exists(payload.strategy_id):
                raise NotFoundError("Strategy not found.")
        setup = Setup(organization_id=self.organization_id, **payload.model_dump())
        self.session.add(setup)
        await self.session.flush()
        return setup

    async def update(self, setup_id: uuid.UUID, payload: SetupUpdate) -> Setup:
        setup = await self.get(setup_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(setup, field, value)
        await self.session.flush()
        return setup

    async def delete(self, setup_id: uuid.UUID) -> None:
        setup = await self.get(setup_id)
        await self.repo.soft_delete(setup.id)


class StrategyService:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.repo = StrategyRepository(session, organization_id)
        self.versions = StrategyVersionRepository(session, organization_id)
        self.audit = AuditService(session)

    async def get(self, strategy_id: uuid.UUID) -> Strategy:
        strategy = await self.repo.get(strategy_id)
        if strategy is None:
            raise NotFoundError("Strategy not found.")
        return strategy

    async def list_with_stats(self, params: PageParams) -> tuple[Page[Strategy], dict]:
        page = await self.repo.paginate(params, order_by=[Strategy.name.asc()])
        stats = await self._performance_by_strategy()
        return page, stats

    async def _performance_by_strategy(self) -> dict[uuid.UUID, dict[str, Decimal | int | None]]:
        rows = await self.session.execute(
            select(
                Trade.strategy_id,
                func.count(),
                func.coalesce(func.sum(Trade.net_pnl), 0),
            )
            .where(
                Trade.organization_id == self.organization_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
                Trade.strategy_id.isnot(None),
            )
            .group_by(Trade.strategy_id)
        )
        totals = {
            row[0]: {"count": int(row[1]), "net_pnl": quantize_money(row[2])} for row in rows.all()
        }

        wins = await self.session.execute(
            select(Trade.strategy_id, func.count())
            .where(
                Trade.organization_id == self.organization_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
                Trade.net_pnl > 0,
                Trade.strategy_id.isnot(None),
            )
            .group_by(Trade.strategy_id)
        )
        win_counts = {row[0]: int(row[1]) for row in wins.all()}

        for strategy_id, payload in totals.items():
            count = payload["count"]
            rate = safe_div(Decimal(win_counts.get(strategy_id, 0)) * HUNDRED, Decimal(count))
            payload["win_rate"] = quantize_percent(rate) if rate is not None else None
        return totals

    async def create(self, payload: StrategyCreate) -> Strategy:
        from tradeloom.engine.registry import STRATEGY_REGISTRY, is_registered

        existing = await self.repo.list(func.lower(Strategy.name) == payload.name.strip().lower())
        if existing:
            raise ConflictError("A strategy with that name already exists.")

        if payload.kind is StrategyKind.BUILTIN and not is_registered(payload.engine_key):
            # Only registry keys are accepted. Arbitrary text never reaches the engine.
            raise ValidationError(
                "Unknown strategy engine. Choose one of: " + ", ".join(sorted(STRATEGY_REGISTRY))
            )

        strategy = Strategy(
            organization_id=self.organization_id,
            created_by_user_id=self.actor_user_id,
            name=payload.name.strip(),
            description=payload.description,
            kind=payload.kind,
            engine_key=payload.engine_key if payload.kind is StrategyKind.BUILTIN else None,
            status=StrategyStatus.ACTIVE,
            color=payload.color,
            playbook=payload.playbook,
        )
        self.session.add(strategy)
        await self.session.flush()

        await self.create_version(
            strategy, StrategyVersionCreate(parameters=payload.parameters, notes="Initial version")
        )
        await self.audit.record(
            AuditAction.CREATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="strategy",
            entity_id=strategy.id,
            summary=f"Created strategy {strategy.name}",
        )
        return strategy

    async def create_version(
        self, strategy: Strategy, payload: StrategyVersionCreate
    ) -> StrategyVersion:
        """Snapshot the parameters into a new immutable version.

        Parameters are validated against the engine strategy's declared schema *now*, so an
        out-of-range value can never reach a worker.
        """
        resolved = dict(payload.parameters)
        if strategy.kind is StrategyKind.BUILTIN and strategy.engine_key:
            from tradeloom.engine.registry import get_strategy
            from tradeloom.engine.strategy import StrategyParameterError

            engine_class = get_strategy(strategy.engine_key)
            try:
                coerced = engine_class.resolve_parameters(payload.parameters)
            except StrategyParameterError as exc:
                raise ValidationError(str(exc)) from exc
            resolved = {key: str(value) for key, value in coerced.items()}

        existing = await self.repo.versions(strategy.id)
        next_version = (existing[0].version + 1) if existing else 1

        version = StrategyVersion(
            organization_id=self.organization_id,
            strategy_id=strategy.id,
            version=next_version,
            engine_key=strategy.engine_key,
            parameters=resolved,
            notes=payload.notes,
            created_by_user_id=self.actor_user_id,
        )
        self.session.add(version)
        await self.session.flush()

        if strategy.kind is StrategyKind.BUILTIN and strategy.engine_key:
            await self._persist_parameter_specs(version, strategy.engine_key)

        strategy.current_version_id = version.id
        await self.session.flush()
        return version

    async def _persist_parameter_specs(self, version: StrategyVersion, engine_key: str) -> None:
        from tradeloom.engine.registry import get_strategy

        engine_class = get_strategy(engine_key)
        for order, spec in enumerate(engine_class.parameters):
            self.session.add(
                StrategyParameter(
                    organization_id=self.organization_id,
                    strategy_version_id=version.id,
                    name=spec.name,
                    label=spec.label or spec.name.replace("_", " ").title(),
                    param_type=spec.param_type,
                    default_value=str(spec.default),
                    minimum=spec.minimum,
                    maximum=spec.maximum,
                    step=spec.step,
                    choices={"values": list(spec.choices)} if spec.choices else {},
                    description=spec.description,
                    display_order=order,
                )
            )
        await self.session.flush()

    async def parameter_specs(self, version_id: uuid.UUID) -> list[StrategyParameter]:
        result = await self.session.execute(
            select(StrategyParameter)
            .where(
                StrategyParameter.strategy_version_id == version_id,
                StrategyParameter.organization_id == self.organization_id,
            )
            .order_by(StrategyParameter.display_order.asc())
        )
        return list(result.scalars().all())

    async def update(self, strategy_id: uuid.UUID, payload: StrategyUpdate) -> Strategy:
        strategy = await self.get(strategy_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(strategy, field, value)
        await self.session.flush()
        return strategy

    async def delete(self, strategy_id: uuid.UUID) -> None:
        strategy = await self.get(strategy_id)
        await self.repo.soft_delete(strategy.id)
        await self.audit.record(
            AuditAction.DELETED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="strategy",
            entity_id=strategy.id,
            summary=f"Deleted strategy {strategy.name}",
        )


__all__ = [
    "InstrumentService",
    "SetupService",
    "StrategyService",
    "TagService",
    "normalize_alias",
    "slugify",
]
