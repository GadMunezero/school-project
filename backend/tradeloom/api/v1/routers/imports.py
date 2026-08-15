"""CSV import endpoints.

The uploaded file is stored in object storage and re-read at each stage, so a browser refresh
between mapping and commit loses nothing. Validation and commit run synchronously for files small
enough to be safe (the vast majority) and are also available as background jobs for large ones.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from tradeloom.api.deps import Paging, Tenant, WritableTenant
from tradeloom.core.errors import NotFoundError, UnprocessableStateError
from tradeloom.models.imports import Import
from tradeloom.schemas.common import (
    DataResponse,
    ListResponse,
    MessageResponse,
    PageMeta,
    TradeloomModel,
)
from tradeloom.services.files import FileService
from tradeloom.services.imports.pipeline import ImportPipeline

router = APIRouter(prefix="/imports", tags=["imports"])

#: Files under this size are validated inline; larger ones must go through the worker.
INLINE_LIMIT_BYTES = 2 * 1024 * 1024


class MappingRequest(TradeloomModel):
    column_mapping: dict[str, str]
    options: dict[str, Any] = {}


class ImportRead(TradeloomModel):
    id: Any
    account_id: Any
    status: str
    filename: str
    row_kind: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    imported_rows: int
    created_order_count: int
    created_trade_count: int
    column_mapping: dict[str, Any]
    options: dict[str, Any]
    inspection: dict[str, Any]
    error_summary: dict[str, Any]
    committed_at: Any | None
    reverted_at: Any | None
    created_at: Any
    can_revert: bool = False


def _read(record: Import) -> ImportRead:
    model = ImportRead.model_validate(record)
    model.can_revert = record.can_revert
    return model


def _pipeline(tenant: Tenant) -> ImportPipeline:
    return ImportPipeline(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)


async def _load_file(tenant: Tenant, record: Import) -> bytes:
    if record.file_object_id is None:
        raise UnprocessableStateError(
            "The uploaded file is no longer available. Upload it again to continue."
        )
    data, _ = await FileService(tenant.session, tenant.organization_id).download(
        record.file_object_id
    )
    return data


@router.get("", response_model=ListResponse[ImportRead], summary="List imports")
async def list_imports(
    tenant: Tenant,
    paging: Paging,
    account_id: uuid.UUID | None = Query(default=None),
) -> ListResponse[ImportRead]:
    pipeline = _pipeline(tenant)
    filters = [Import.account_id == account_id] if account_id else []
    page = await pipeline.imports.paginate(paging, *filters, order_by=[Import.created_at.desc()])
    return ListResponse(data=[_read(record) for record in page.items], meta=PageMeta(**page.meta()))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[ImportRead],
    summary="Upload a CSV and inspect its columns",
)
async def upload(
    tenant: WritableTenant,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[uuid.UUID, Form()],
) -> DataResponse[ImportRead]:
    data = await file.read()

    files = FileService(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)
    stored = await files.upload(
        data=data,
        declared_content_type=file.content_type or "text/csv",
        original_filename=file.filename,
        purpose="import",
    )

    pipeline = _pipeline(tenant)
    record, _inspection = await pipeline.create(
        account_id=account_id,
        filename=file.filename or "upload.csv",
        data=data,
        file_object_id=stored.id,
    )
    await tenant.session.commit()
    return DataResponse(data=_read(record))


@router.get("/{import_id}", response_model=DataResponse[ImportRead], summary="Import detail")
async def get_import(import_id: uuid.UUID, tenant: Tenant) -> DataResponse[ImportRead]:
    record = await _pipeline(tenant).get(import_id)
    return DataResponse(data=_read(record))


@router.put(
    "/{import_id}/mapping",
    response_model=DataResponse[ImportRead],
    summary="Set the column mapping",
)
async def set_mapping(
    import_id: uuid.UUID, payload: MappingRequest, tenant: WritableTenant
) -> DataResponse[ImportRead]:
    record = await _pipeline(tenant).set_mapping(import_id, payload.column_mapping, payload.options)
    await tenant.session.commit()
    return DataResponse(data=_read(record))


@router.post(
    "/{import_id}/validate",
    response_model=DataResponse[ImportRead],
    summary="Validate every row",
)
async def validate(import_id: uuid.UUID, tenant: WritableTenant) -> DataResponse[ImportRead]:
    pipeline = _pipeline(tenant)
    record = await pipeline.get(import_id)
    data = await _load_file(tenant, record)
    record = await pipeline.validate(import_id, data)
    await tenant.session.commit()
    return DataResponse(data=_read(record))


@router.get("/{import_id}/preview", response_model=DataResponse[dict], summary="Preview rows")
async def preview(
    import_id: uuid.UUID,
    tenant: Tenant,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DataResponse[dict]:
    return DataResponse(data=await _pipeline(tenant).preview(import_id, limit))


@router.post(
    "/{import_id}/commit",
    response_model=DataResponse[ImportRead],
    summary="Commit the valid rows",
)
async def commit(import_id: uuid.UUID, tenant: WritableTenant) -> DataResponse[ImportRead]:
    record = await _pipeline(tenant).commit(import_id)
    await tenant.session.commit()
    return DataResponse(data=_read(record))


@router.post(
    "/{import_id}/revert",
    response_model=DataResponse[ImportRead],
    summary="Undo a committed import",
)
async def revert(import_id: uuid.UUID, tenant: WritableTenant) -> DataResponse[ImportRead]:
    record = await _pipeline(tenant).revert(import_id)
    await tenant.session.commit()
    return DataResponse(data=_read(record))


@router.get(
    "/templates/available",
    response_model=DataResponse[list[dict]],
    summary="Broker column templates",
)
async def templates(tenant: Tenant) -> DataResponse[list[dict]]:
    from sqlalchemy import select

    from tradeloom.models.imports import ImportTemplate

    result = await tenant.session.execute(
        select(ImportTemplate)
        .where(
            (ImportTemplate.organization_id == tenant.organization_id)
            | (ImportTemplate.organization_id.is_(None))
        )
        .order_by(ImportTemplate.name.asc())
    )
    return DataResponse(
        data=[
            {
                "id": str(template.id),
                "key": template.key,
                "name": template.name,
                "broker": template.broker,
                "description": template.description,
                "column_mapping": template.column_mapping,
                "options": template.options,
                "is_system": template.is_system,
            }
            for template in result.scalars().all()
        ]
    )


@router.delete("/{import_id}", response_model=MessageResponse, summary="Delete an import record")
async def delete_import(import_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    pipeline = _pipeline(tenant)
    record = await pipeline.get(import_id)
    if record.status.value == "completed":
        raise UnprocessableStateError(
            "Revert this import before deleting its record, so the trades it created are removed "
            "too."
        )
    deleted = await pipeline.imports.hard_delete(record.id)
    if not deleted:
        raise NotFoundError("Import not found.")
    await tenant.session.commit()
    return MessageResponse(message="Import record deleted.")


__all__ = ["router"]
