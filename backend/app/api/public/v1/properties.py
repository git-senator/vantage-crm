"""Public property endpoints. Reuses `PropertyService`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import CREATED_SORT, PUBLIC_V1_PREFIX, is_ascending
from app.api.v1.properties import _to_read
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.property import (
    PropertyCreate,
    PropertyFilters,
    PropertyRead,
    PropertyUpdate,
)
from app.services.property import PropertyService

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=Page[PropertyRead], summary="List properties")
async def list_properties(
    principal: Annotated[MachinePrincipal, Depends(require_scope("properties.view"))],
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    property_type: str | None = None,
    listing_agent_id: UUID | None = None,
    client_id: UUID | None = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    min_price: Annotated[str | None, Query()] = None,
    max_price: Annotated[str | None, Query()] = None,
    min_bedrooms: Annotated[int | None, Query(ge=0, le=100)] = None,
    feature: Annotated[str | None, Query(max_length=60)] = None,
    sort: CREATED_SORT = "-created_at",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[PropertyRead]:
    filters = PropertyFilters.model_validate(
        {
            "search": search,
            "status": status_filter,
            "property_type": property_type,
            "listing_agent_id": listing_agent_id,
            "client_id": client_id,
            "city": city,
            "min_price": min_price,
            "max_price": max_price,
            "min_bedrooms": min_bedrooms,
            "feature": feature,
        }
    )
    rows, has_more = await PropertyService(
        principal.session, principal.auth
    ).list_properties(
        filters=filters,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        ascending=is_ascending(sort),
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[PropertyRead](
        data=[_to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get(
    "/{property_id}", response_model=PropertyRead, summary="Retrieve a property"
)
async def get_property(
    property_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("properties.view"))],
) -> PropertyRead:
    return _to_read(
        await PropertyService(principal.session, principal.auth).get_property(property_id)
    )


@router.post(
    "",
    response_model=PropertyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a property",
)
async def create_property(
    payload: PropertyCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope("properties.manage"))],
) -> PropertyRead:
    actor = await actor_for(principal)
    listing = await PropertyService(principal.session, principal.auth).create_property(
        payload, actor
    )
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/properties/{listing.id}"
    return _to_read(listing)


@router.patch(
    "/{property_id}", response_model=PropertyRead, summary="Update a property"
)
async def update_property(
    property_id: UUID,
    payload: PropertyUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope("properties.manage"))],
) -> PropertyRead:
    actor = await actor_for(principal)
    listing = await PropertyService(principal.session, principal.auth).update_property(
        property_id, payload, actor
    )
    return _to_read(listing)


@router.delete(
    "/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a property",
)
async def delete_property(
    property_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("properties.manage"))],
) -> None:
    actor = await actor_for(principal)
    await PropertyService(principal.session, principal.auth).delete_property(
        property_id, actor
    )
