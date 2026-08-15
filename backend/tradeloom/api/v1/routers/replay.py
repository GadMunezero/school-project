"""Market replay endpoints.

Every response contains only the candles up to the session's cursor. Stepping forward is a
server-side operation that advances the simulator; the client cannot request a bar it has not
reached.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from tradeloom.api.deps import CurrentLimits, Tenant, WritableTenant
from tradeloom.core.errors import EntitlementError
from tradeloom.schemas.backtest import (
    ReplayCreate,
    ReplayOrderRequest,
    ReplayProtectionRequest,
    ReplayStepRequest,
)
from tradeloom.schemas.common import DataResponse, MessageResponse
from tradeloom.services.replay import ReplayService

router = APIRouter(prefix="/replay", tags=["replay"])


def _service(tenant: Tenant) -> ReplayService:
    return ReplayService(tenant.session, tenant.organization_id, tenant.user_id)


def _require_replay(limits: CurrentLimits) -> None:
    if not limits.replay_enabled:
        raise EntitlementError(
            "Market replay is a Pro feature.", feature="replay", required_plan="pro"
        )


@router.get("", response_model=DataResponse[list[dict]], summary="List replay sessions")
async def list_sessions(tenant: Tenant) -> DataResponse[list[dict]]:
    sessions = await _service(tenant).list()
    return DataResponse(
        data=[
            {
                "id": str(item.id),
                "name": item.name,
                "timeframe": item.timeframe.value,
                "cursor_index": item.cursor_index,
                "total_bars": item.total_bars,
                "is_finished": item.is_finished,
                "created_at": item.created_at.isoformat(),
                "last_interacted_at": (
                    item.last_interacted_at.isoformat() if item.last_interacted_at else None
                ),
            }
            for item in sessions
        ]
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[dict],
    summary="Start a replay session",
)
async def create_session(
    payload: ReplayCreate, tenant: WritableTenant, limits: CurrentLimits
) -> DataResponse[dict]:
    _require_replay(limits)
    service = _service(tenant)
    record = await service.create(payload)
    await tenant.session.commit()
    return DataResponse(data=await service.state(record.id))


@router.get("/{replay_id}", response_model=DataResponse[dict], summary="Current replay state")
async def get_state(
    replay_id: uuid.UUID, tenant: Tenant, limits: CurrentLimits
) -> DataResponse[dict]:
    _require_replay(limits)
    return DataResponse(data=await _service(tenant).state(replay_id))


@router.post("/{replay_id}/step", response_model=DataResponse[dict], summary="Advance the replay")
async def step(
    replay_id: uuid.UUID,
    payload: ReplayStepRequest,
    tenant: WritableTenant,
    limits: CurrentLimits,
) -> DataResponse[dict]:
    _require_replay(limits)
    state = await _service(tenant).step(replay_id, payload.steps)
    await tenant.session.commit()
    return DataResponse(data=state)


@router.post(
    "/{replay_id}/orders", response_model=DataResponse[dict], summary="Place a simulated order"
)
async def place_order(
    replay_id: uuid.UUID,
    payload: ReplayOrderRequest,
    tenant: WritableTenant,
    limits: CurrentLimits,
) -> DataResponse[dict]:
    _require_replay(limits)
    state = await _service(tenant).submit_order(replay_id, payload)
    await tenant.session.commit()
    return DataResponse(data=state)


@router.post(
    "/{replay_id}/protection",
    response_model=DataResponse[dict],
    summary="Set or move the stop and target",
)
async def set_protection(
    replay_id: uuid.UUID,
    payload: ReplayProtectionRequest,
    tenant: WritableTenant,
    limits: CurrentLimits,
) -> DataResponse[dict]:
    _require_replay(limits)
    state = await _service(tenant).set_protection(replay_id, payload.stop_loss, payload.take_profit)
    await tenant.session.commit()
    return DataResponse(data=state)


@router.post(
    "/{replay_id}/close", response_model=DataResponse[dict], summary="Close the open position"
)
async def close_position(
    replay_id: uuid.UUID, tenant: WritableTenant, limits: CurrentLimits
) -> DataResponse[dict]:
    _require_replay(limits)
    state = await _service(tenant).close_position(replay_id)
    await tenant.session.commit()
    return DataResponse(data=state)


@router.delete("/{replay_id}", response_model=MessageResponse, summary="Delete a replay session")
async def delete_session(replay_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await _service(tenant).delete(replay_id)
    await tenant.session.commit()
    return MessageResponse(message="Replay session deleted.")


__all__ = ["router"]
