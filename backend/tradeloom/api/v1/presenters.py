"""Model -> schema conversion.

Presentation only: no calculation happens here. If a value needs computing it is computed in a
service and stored or returned by that service, so the frontend and the API can never disagree
about a number.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from tradeloom.models.strategy import Tag
from tradeloom.models.trading import Trade
from tradeloom.schemas.trade import TagRef, TradeRead


def tag_ref(tag: Tag) -> TagRef:
    return TagRef(id=tag.id, name=tag.name, slug=tag.slug, color=tag.color, category=tag.category)


def trade_read(
    trade: Trade,
    *,
    tags: Sequence[Tag] = (),
    labels: dict[str, dict[uuid.UUID, str]] | None = None,
) -> TradeRead:
    lookup = labels or {}
    model = TradeRead.model_validate(trade)
    model.tags = [tag_ref(tag) for tag in tags]
    model.account_name = lookup.get("account", {}).get(trade.account_id)
    if trade.strategy_id:
        model.strategy_name = lookup.get("strategy", {}).get(trade.strategy_id)
    if trade.setup_id:
        model.setup_name = lookup.get("setup", {}).get(trade.setup_id)
    return model


def trade_list(
    trades: Sequence[Trade],
    tag_map: dict[uuid.UUID, list[Tag]],
    labels: dict[str, dict[uuid.UUID, str]] | None = None,
) -> list[TradeRead]:
    return [trade_read(trade, tags=tag_map.get(trade.id, []), labels=labels) for trade in trades]


__all__ = ["tag_ref", "trade_list", "trade_read"]
