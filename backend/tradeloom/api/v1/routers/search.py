"""Global search endpoint. Debounced client-side; bounded and tenant-scoped server-side."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from tradeloom.api.deps import Tenant
from tradeloom.schemas.common import DataResponse
from tradeloom.services.search import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=DataResponse[list[dict]], summary="Search across the workspace")
async def search(
    tenant: Tenant,
    q: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> DataResponse[list[dict]]:
    results = await SearchService(tenant.session, tenant.organization_id).search(q, limit)
    return DataResponse(data=results)


__all__ = ["router"]
