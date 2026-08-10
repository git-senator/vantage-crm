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

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.models.property_translation import (
    DEFAULT_LOCALE,
    LOCALES,
    PropertyTranslation,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.property import (
    PropertyAssign,
    PropertyCreate,
    PropertyFilters,
    PropertyPhoto,
    PropertyRead,
    PropertyTranslationRead,
    PropertyTranslationUpdate,
    PropertyUpdate,
)
from app.services.property import PropertyService
from app.services.property_photo import PropertyPhotoService
from app.services.property_translation import PropertyTranslationService

router = APIRouter()


def requested_locale(
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
) -> str:
    """The language to answer in, from the standard header.

    Deliberately not a query parameter: every client already sends this, it
    caches correctly with `Vary`, and it keeps the API ignorant of the web
    app's cookie names. The parse is the simple one — the first tag that is a
    locale we ship — because there are three of them and the elaborate
    q-value negotiation would be more code than the feature.
    """
    if not accept_language:
        return DEFAULT_LOCALE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip()
        if tag in LOCALES:
            return tag
        # `pt-br` and `PT-BR` are the same language as `pt-BR`.
        for known in LOCALES:
            if tag.lower() == known.lower():
                return known
    return DEFAULT_LOCALE


LocaleDep = Annotated[str, Depends(requested_locale)]


def _to_read(  # type: ignore[no-untyped-def]
    listing,
    cover_url: str | None = None,
    translation: PropertyTranslation | None = None,
) -> PropertyRead:
    """Project an ORM row onto the response contract.

    Explicit rather than `from_attributes` alone, because `full_address` and
    `days_on_market` are Python properties and `listing_agent` needs
    flattening.

    `cover_url` is passed in rather than read off the row: it is a freshly
    signed URL, not a stored field, and minting it needs the storage adapter
    this function has no business holding.

    `translation` swaps the three translated fields in place, so every client
    keeps reading `title` and `description` and simply receives them in the
    language it asked for. When there is none the source text is returned
    rather than nothing: a Portuguese page showing a Russian description is
    imperfect, and a Portuguese page showing an empty one is broken.
    """
    text = translation
    return PropertyRead(
        id=listing.id,
        title=text.title if text else listing.title,
        mls_number=listing.mls_number,
        status=listing.status,
        property_type=listing.property_type,
        listing_kind=listing.listing_kind,
        rent_period=listing.rent_period,
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
        description=text.description if text else listing.description,
        features=list((text.features if text else listing.features) or []),
        custom_fields=listing.custom_fields or {},
        source_locale=listing.source_locale,
        content_locale=text.locale if text else listing.source_locale,
        content_is_machine=bool(text and text.is_machine),
        content_is_stale=bool(text and text.is_stale),
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
        cover_attachment_id=listing.cover_attachment_id,
        cover_url=cover_url,
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
    listing_kind: str | None = None,
    listing_agent_id: UUID | None = None,
    client_id: UUID | None = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    min_price: Annotated[str | None, Query()] = None,
    max_price: Annotated[str | None, Query()] = None,
    min_bedrooms: Annotated[int | None, Query(ge=0, le=100)] = None,
    feature: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    locale: LocaleDep = DEFAULT_LOCALE,
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
            "listing_kind": listing_kind,
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
    # One extra query for the whole page, then local signing — see
    # `PropertyPhotoService.covers_for`.
    covers = await PropertyPhotoService(session, auth).covers_for(rows)
    # One query for the page's translations, same shape as the covers above.
    texts = await _translations_for(session, rows, locale)
    return Page[PropertyRead](
        data=[
            _to_read(row, covers.get(row.id), texts.get(row.id)) for row in rows
        ],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


async def _translations_for(  # type: ignore[no-untyped-def]
    session, rows, locale: str
) -> dict[UUID, PropertyTranslation]:
    """The requested locale's text for a set of listings, or nothing.

    Listings already written in the requested language are skipped rather than
    looked up: there is no translation row for a listing's own locale, and
    asking for one would be a guaranteed miss on every Russian listing viewed
    in Russian.
    """
    wanted = [row.id for row in rows if row.source_locale != locale]
    if not wanted:
        return {}
    return await PropertyTranslationService(session).for_listings(wanted, locale)


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
    locale: LocaleDep = DEFAULT_LOCALE,
) -> PropertyRead:
    """One listing. 404 when outside the caller's view scope."""
    listing = await PropertyService(session, auth).get_property(property_id)
    covers = await PropertyPhotoService(session, auth).covers_for([listing])
    texts = await _translations_for(session, [listing], locale)
    return _to_read(listing, covers.get(listing.id), texts.get(listing.id))


@router.get("/{property_id}/photos", response_model=list[PropertyPhoto])
async def list_property_photos(
    property_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.view"))],
    _user: CurrentUser,
) -> list[PropertyPhoto]:
    """The listing's gallery, cover first.

    Gated on `properties.view` and nothing else: a photo is part of the listing
    a caller has already been shown, not a document they went looking for.
    `get_property` runs first so an unreadable listing 404s here exactly as it
    does everywhere else, before any URL is minted.
    """
    listing = await PropertyService(session, auth).get_property(property_id)
    photos = PropertyPhotoService(session, auth)

    out: list[PropertyPhoto] = []
    for photo in await photos.list_photos(listing.id):
        url = photos.view_url(photo)
        if url is None:  # unrenderable — skip rather than break the gallery
            continue
        out.append(
            PropertyPhoto(
                id=photo.id,
                filename=photo.filename,
                content_type=photo.content_type,
                url=url,
                is_cover=photo.id == listing.cover_attachment_id,
            )
        )
    return out


@router.get(
    "/{property_id}/translations", response_model=list[PropertyTranslationRead]
)
async def list_property_translations(
    property_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.view"))],
    _user: CurrentUser,
) -> list[PropertyTranslationRead]:
    """Every language this listing exists in, and how each was produced.

    Gated on view rather than manage: knowing what a client would be shown in
    Portuguese is part of reading the listing.
    """
    listing = await PropertyService(session, auth).get_property(property_id)
    stored = await PropertyTranslationService(session).for_property(listing.id)
    return [
        PropertyTranslationRead.model_validate(stored[locale])
        for locale in LOCALES
        if locale in stored
    ]


@router.put(
    "/{property_id}/translations/{locale}",
    response_model=PropertyTranslationRead,
    dependencies=[Depends(verify_csrf)],
)
async def edit_property_translation(
    property_id: UUID,
    locale: str,
    payload: PropertyTranslationUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.manage"))],
    user: CurrentUser,
) -> PropertyTranslationRead:
    """Correct one language by hand.

    PUT rather than PATCH: the body is the whole translation, and the write is
    idempotent. From here the text is the agent's — regeneration will not
    overwrite it, and a later change to the source flags it for review instead
    of discarding the correction.

    Editing a listing's own language is refused: that text lives on the
    listing, and writing it here would create a second, divergent copy of the
    source with no rule for which one wins.
    """
    row = await PropertyService(session, auth).edit_translation(
        property_id,
        locale,
        title=payload.title,
        description=payload.description,
        features=payload.features,
        actor=user,
    )
    return PropertyTranslationRead.model_validate(row)


@router.put(
    "/{property_id}/photos/{attachment_id}/cover",
    response_model=PropertyRead,
    dependencies=[Depends(verify_csrf)],
)
async def set_property_cover(
    property_id: UUID,
    attachment_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("properties.manage"))],
    user: CurrentUser,
) -> PropertyRead:
    """Choose which photo leads the listing.

    PUT, not POST: naming the cover twice leaves the same listing in the same
    state. Write authorisation comes from the service — a listing agent may set
    the cover on their own listings only, the same rule as any other edit.
    """
    service = PropertyService(session, auth)
    listing = await service.set_cover(property_id, attachment_id, user)
    covers = await PropertyPhotoService(session, auth).covers_for([listing])
    return _to_read(listing, covers.get(listing.id))


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
