"""Terms and privacy documents.

Public and unauthenticated, because someone has to be able to read what they are agreeing to
before they have an account.

The response carries ``is_placeholder`` rather than hiding it. A deployment that has not written
its policies yet should say so on the page, loudly, instead of presenting repository boilerplate
as an agreement — and production refuses to boot in that state anyway.
"""

from __future__ import annotations

from fastapi import APIRouter

from tradeloom.core import legal
from tradeloom.core.errors import NotFoundError
from tradeloom.schemas.common import DataResponse

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("", response_model=DataResponse[list[dict]], summary="Published policy documents")
async def index() -> DataResponse[list[dict]]:
    return DataResponse(
        data=[
            {
                "slug": slug,
                "title": legal.load(slug).title,
                "version": legal.load(slug).version,
                "is_placeholder": legal.load(slug).is_placeholder,
            }
            for slug in legal.DOCUMENTS
        ]
    )


@router.get("/{slug}", response_model=DataResponse[dict], summary="Read a policy document")
async def read(slug: str) -> DataResponse[dict]:
    try:
        document = legal.load(slug)
    except KeyError as error:
        raise NotFoundError("No such document.") from error
    return DataResponse(data=dict(document.to_dict))


__all__ = ["router"]
