"""Trading account endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from tradeloom.api.deps import Entitlements, Paging, Tenant, WritableTenant
from tradeloom.schemas.account import (
    AccountCreate,
    AccountDetail,
    AccountRead,
    AccountSnapshotRead,
    AccountStats,
    AccountUpdate,
    CashTransactionCreate,
    CashTransactionRead,
)
from tradeloom.schemas.common import (
    DataResponse,
    ListResponse,
    MessageResponse,
    PageMeta,
)
from tradeloom.services.accounts import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _service(tenant: Tenant) -> AccountService:
    return AccountService(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)


@router.get("", response_model=ListResponse[AccountRead], summary="List accounts")
async def list_accounts(
    tenant: Tenant,
    paging: Paging,
    include_archived: bool = Query(default=False),
) -> ListResponse[AccountRead]:
    page = await _service(tenant).list(paging, include_archived=include_archived)
    return ListResponse(
        data=[AccountRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[AccountRead],
    summary="Create an account",
)
async def create_account(
    payload: AccountCreate,
    tenant: WritableTenant,
    entitlements: Entitlements,
) -> DataResponse[AccountRead]:
    service = _service(tenant)
    account = await service.create(payload, entitlements=entitlements)
    await tenant.session.commit()
    return DataResponse(data=AccountRead.model_validate(account))


@router.get("/{account_id}", response_model=DataResponse[AccountDetail], summary="Account detail")
async def get_account(account_id: uuid.UUID, tenant: Tenant) -> DataResponse[AccountDetail]:
    service = _service(tenant)
    account = await service.get(account_id)
    stats = await service.stats(account_id)
    return DataResponse(
        data=AccountDetail(account=AccountRead.model_validate(account), stats=stats)
    )


@router.patch(
    "/{account_id}", response_model=DataResponse[AccountRead], summary="Update an account"
)
async def update_account(
    account_id: uuid.UUID, payload: AccountUpdate, tenant: WritableTenant
) -> DataResponse[AccountRead]:
    account = await _service(tenant).update(account_id, payload)
    await tenant.session.commit()
    return DataResponse(data=AccountRead.model_validate(account))


@router.post(
    "/{account_id}/archive",
    response_model=DataResponse[AccountRead],
    summary="Archive an account",
)
async def archive_account(
    account_id: uuid.UUID, tenant: WritableTenant
) -> DataResponse[AccountRead]:
    account = await _service(tenant).archive(account_id)
    await tenant.session.commit()
    return DataResponse(data=AccountRead.model_validate(account))


@router.delete("/{account_id}", response_model=MessageResponse, summary="Delete an account")
async def delete_account(account_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await _service(tenant).delete(account_id)
    await tenant.session.commit()
    return MessageResponse(message="Account deleted.")


@router.post(
    "/{account_id}/recalculate",
    response_model=DataResponse[AccountRead],
    summary="Rebuild cached balances from the ledger",
)
async def recalculate_account(
    account_id: uuid.UUID, tenant: WritableTenant
) -> DataResponse[AccountRead]:
    account = await _service(tenant).recalculate(account_id)
    await tenant.session.commit()
    return DataResponse(data=AccountRead.model_validate(account))


@router.get(
    "/{account_id}/stats", response_model=DataResponse[AccountStats], summary="Account statistics"
)
async def account_stats(account_id: uuid.UUID, tenant: Tenant) -> DataResponse[AccountStats]:
    return DataResponse(data=await _service(tenant).stats(account_id))


@router.get(
    "/{account_id}/transactions",
    response_model=ListResponse[CashTransactionRead],
    summary="List cash transactions",
)
async def list_transactions(
    account_id: uuid.UUID, tenant: Tenant, paging: Paging
) -> ListResponse[CashTransactionRead]:
    page = await _service(tenant).list_cash_transactions(account_id, paging)
    return ListResponse(
        data=[CashTransactionRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.post(
    "/{account_id}/transactions",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[CashTransactionRead],
    summary="Record a deposit, withdrawal or adjustment",
)
async def create_transaction(
    account_id: uuid.UUID, payload: CashTransactionCreate, tenant: WritableTenant
) -> DataResponse[CashTransactionRead]:
    transaction = await _service(tenant).add_cash_transaction(
        account_id,
        kind=payload.kind,
        amount=payload.amount,
        occurred_at=payload.occurred_at,
        description=payload.description,
    )
    await tenant.session.commit()
    return DataResponse(data=CashTransactionRead.model_validate(transaction))


@router.delete(
    "/{account_id}/transactions/{transaction_id}",
    response_model=MessageResponse,
    summary="Delete a cash transaction",
)
async def delete_transaction(
    account_id: uuid.UUID, transaction_id: uuid.UUID, tenant: WritableTenant
) -> MessageResponse:
    await _service(tenant).delete_cash_transaction(account_id, transaction_id)
    await tenant.session.commit()
    return MessageResponse(message="Transaction deleted.")


@router.get(
    "/{account_id}/snapshots",
    response_model=ListResponse[AccountSnapshotRead],
    summary="Daily balance snapshots",
)
async def list_snapshots(
    account_id: uuid.UUID,
    tenant: Tenant,
    limit: int = Query(default=365, ge=1, le=2000),
) -> ListResponse[AccountSnapshotRead]:
    from tradeloom.models.account import AccountSnapshot
    from tradeloom.repositories.trading import AccountSnapshotRepository

    service = _service(tenant)
    await service.get(account_id)
    repo = AccountSnapshotRepository(tenant.session, tenant.organization_id)
    rows = await repo.list(
        AccountSnapshot.account_id == account_id,
        order_by=[AccountSnapshot.as_of_date.desc()],
        limit=limit,
    )
    rows.reverse()
    return ListResponse(
        data=[AccountSnapshotRead.model_validate(row) for row in rows],
        meta=PageMeta(page=1, page_size=len(rows), total=len(rows), total_pages=1, has_next=False),
    )


__all__ = ["router"]
