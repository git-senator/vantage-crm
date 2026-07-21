"""Property endpoints.

HTTP concerns only — every authorization decision and business rule lives in
the service, exactly as in `app/api/v1/leads.py`.

One deliberate difference from the other CRM routers: a listing the caller can
see but not edit returns **403**, not 404. Listings are shared inventory, so
the record's existence is not a secret and pretending otherwise would just be
confusing. See `PropertyService._load_for_write`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.property import (
    PropertyAssign,
    PropertyCreate,
    PropertyFilters,
    PropertyRead,
    PropertyUpdate,
)
from app.services.property import PropertyService

router = APIRouter()


def _to_read(listing) -> PropertyRead:  # type: ignore[no-untyped-def]
    """Project an ORM row onto the response contract.

    Explicit rather than `from_attributes` alone, because `full_address` and
    `days_on_market` are Python properties and `listing_agent` needs
    flattening.
    """
    return PropertyRead(
        id=listing.id,
        title=listing.title,
        mls_number=listing.mls_number,
        status=listing.status,
        property_type=listing.property_type,
        address_line1=listing.address_line1,
        address_line2=listing.address_line2,
        city=listing.city,
        state=listing.state,
        postal_code=listing.postal_code,
        country=listing.country,
        full_address=listing.full_address,
        latitude=listing.latitude,
        longitude=listing.longitude,
        price=listing.price,
        currency=listing.currency,
        bedrooms=listing.bedrooms,
        bathrooms=listing.bathrooms,
        square_feet=listing.square_feet,
        lot_size_sqft=listing.lot_size_sqft,
        year_built=listing.year_built,
        listed_at=listing.listed_at,
        days_on_market=listing.days_on_market,
        view_count=listing.view_count,
        save_count=listing.save_count,
        description=listing.description,
        features=list(listing.features or []),
        custom_fields=listing.custom_fields or {},
        client_id=listing.client_id,
        listing_agent=(
            {
                "id": listing.listing_agent.id,
                "full_name": listing.listing_agent.full_name,
                "initials": listing.listing_agent.initials,
                "avatar_hue": listing.listing_agent.avatar_hue,
            }
            if listing.listing_agent
            else None
        ),
        created_at=listing.created_at,
        updated_at=listing.updated_at,
    )


@router.get("", response_model=Page[PropertyRead])
async def list_properties(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.view"))],
    _user: CurrentUser,
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
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[PropertyRead]:
    """Cursor-paginated listings, newest first, scoped to the caller.

    Note that for an `agent` the scope is ALL — listings are shared inventory,
    so this returns the whole brokerage book. A `manager` filtering to their
    team does so with `listing_agent_id`, not by a narrower scope.
    """
    # Built through Pydantic so an unknown status or a malformed price is a 422
    # with a field-level message rather than a filter that silently matches
    # nothing. Prices arrive as strings so Decimal parsing stays exact.
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

    service = PropertyService(session, auth)
    rows, has_more = await service.list_properties(
        filters=filters,
        limit=limit,
        # A malformed cursor yields the first page rather than a 400 — cursors
        # end up in bookmarked URLs.
        cursor=Cursor.decode(cursor) if cursor else None,
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


@router.get("/stats/statuses", response_model=dict[str, int])
async def status_counts(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.view"))],
    _user: CurrentUser,
) -> dict[str, int]:
    """Listing counts per status, within the caller's scope."""
    return await PropertyService(session, auth).status_counts()


@router.get("/{property_id}", response_model=PropertyRead)
async def get_property(
    property_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.view"))],
    _user: CurrentUser,
) -> PropertyRead:
    """One listing. 404 when outside the caller's view scope."""
    return _to_read(await PropertyService(session, auth).get_property(property_id))


@router.post(
    "",
    response_model=PropertyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_property(
    payload: PropertyCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.manage"))],
    user: CurrentUser,
) -> PropertyRead:
    listing = await PropertyService(session, auth).create_property(payload, user)
    response.headers["Location"] = f"/api/v1/properties/{listing.id}"
    return _to_read(listing)


@router.patch(
    "/{property_id}",
    response_model=PropertyRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_property(
    property_id: UUID,
    payload: PropertyUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.manage"))],
    user: CurrentUser,
) -> PropertyRead:
    """Partial update. Only fields present in the body are touched.

    403 when the listing belongs to another agent — visible, but not yours.
    """
    listing = await PropertyService(session, auth).update_property(
        property_id, payload, user
    )
    return _to_read(listing)


@router.delete(
    "/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_property(
    property_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. The row and its audit trail survive."""
    await PropertyService(session, auth).delete_property(property_id, user)


@router.post(
    "/{property_id}/assign",
    response_model=PropertyRead,
    dependencies=[Depends(verify_csrf)],
)
async def assign_property(
    property_id: UUID,
    payload: PropertyAssign,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.assign"))],
    user: CurrentUser,
) -> PropertyRead:
    """Reassign the listing agent.

    Separate from update because it needs `properties.assign` — an agent may
    edit their own listings without being able to move one onto a colleague.
    """
    listing = await PropertyService(session, auth).assign_property(
        property_id, payload.listing_agent_id, user
    )
    return _to_read(listing)
