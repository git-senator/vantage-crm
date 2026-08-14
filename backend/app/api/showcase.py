"""The public showcase — the only part of this API a stranger may call.

Everything else on this server authenticates: a person with a cookie, or a
machine with a key. This surface authenticates nobody, which is the point: it
feeds a public catalogue page that anonymous visitors read, and the enquiry
form they write back through.

Three rules hold it in place.

**One organization, named explicitly.** `SHOWCASE_ORGANIZATION_ID` decides
whose listings are public. Unset — the default — and the whole surface answers
404, so a deployment that never opted in cannot leak by accident. RLS is bound
to that one id per transaction, so even a bug in a filter cannot reach another
tenant's rows.

**A separate read model.** `ShowcaseListing` says what a buyer needs and
nothing else. Reusing `PropertyRead` would publish the listing agent, the
client it is filed against and its pipeline status the moment someone adds a
field upstream — and the page would keep rendering, so nobody would notice.

**Only what is on the market.** Sold and off-market listings are excluded here
rather than in the frontend, because a filter in a template is a filter someone
removes while restyling.

The enquiry endpoint writes a lead, and a write from an anonymous caller needs
an actor to attribute it to. It borrows the organization's owner, exactly as an
API key borrows the person who minted it: the audit trail then reads "created
by the owner, source website", which is true, rather than pretending a stranger
had an account.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SettingsDep
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.rate_limit import RateLimit, check
from app.db.session import get_session_factory, set_tenant_context
from app.models.attachment import Attachment
from app.models.property import Property
from app.models.property_translation import LOCALES
from app.models.user import User
from app.schemas.lead import LeadCreate
from app.schemas.showcase import EnquiryCreate, ShowcaseListing, ShowcasePhoto
from app.services.lead import LeadService
from app.services.property_photo import PropertyPhotoService
from app.services.property_translation import PropertyTranslationService
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

router = APIRouter(prefix="/showcase/v1", tags=["showcase"])

#: Listing statuses a visitor may see. `pending` is in: a deal in progress is
#: still worth showing, and hiding it makes a busy agency look empty.
VISIBLE_STATUSES = ("active", "pending", "coming_soon")

#: Enquiries per IP. Generous for a person, useless for a script: a buyer sends
#: one form and maybe corrects it twice; a bot wants thousands.
ENQUIRY_LIMIT = RateLimit("showcase_enquiry", limit=5, window_seconds=3600)


def _organization_id(settings: Settings) -> UUID:
    raw = settings.SHOWCASE_ORGANIZATION_ID
    if not raw:
        # Not "misconfigured" — not enabled. A deployment that never asked for
        # a public catalogue should not have one.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No public showcase here.")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No public showcase here."
        ) from exc


async def showcase_session(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    """A transaction pinned to the showcase organization, with no actor."""
    organization = _organization_id(settings)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_tenant_context(session, organization)
        yield session


ShowcaseSession = Annotated[AsyncSession, Depends(showcase_session)]


@dataclass(frozen=True, slots=True)
class ShowcaseContext:
    """A session, and the organization owner whose authority reads borrow.

    The services below all take an `AuthorizationContext` — they are the same
    services the internal API uses, not a laxer parallel path, and that is the
    property worth keeping. So an anonymous request borrows the owner's context
    for the duration, and the endpoints above it decide what may be shown.
    """

    session: AsyncSession
    auth: AuthorizationContext
    owner: User


async def showcase_context(
    session: ShowcaseSession, settings: SettingsDep
) -> ShowcaseContext:
    organization = _organization_id(settings)
    owner = (
        await session.execute(
            select(User)
            .where(User.organization_id == organization, User.deleted_at.is_(None))
            .order_by(User.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if owner is None:  # pragma: no cover — an organization always has its creator
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No public showcase here.")
    auth = await RbacService(session).resolve(owner.id, organization, use_cache=False)
    return ShowcaseContext(session=session, auth=auth, owner=owner)


ShowcaseCtx = Annotated[ShowcaseContext, Depends(showcase_context)]


def requested_locale(
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
) -> str:
    """The page's language, or English.

    English rather than the source language: this catalogue is written in
    Russian and read by buyers in North America. Falling back to the source
    would show them Cyrillic.
    """
    if not accept_language:
        return "en"
    head = accept_language.split(",")[0].strip()
    for locale in LOCALES:
        if head.lower() == locale.lower():
            return locale
    # `pt-BR;q=0.9` and bare `pt` both mean Portuguese to us.
    prefix = head.split("-")[0].lower()
    for locale in LOCALES:
        if locale.split("-")[0].lower() == prefix:
            return locale
    return "en"


LocaleDep = Annotated[str, Depends(requested_locale)]


def _to_showcase(
    listing: Property,
    translation: object | None,
    cover_url: str | None,
    photo_count: int,
    photos: list[ShowcasePhoto] | None = None,
) -> ShowcaseListing:
    title = getattr(translation, "title", None) or listing.title
    description = getattr(translation, "description", None) or listing.description
    features = getattr(translation, "features", None) or (listing.features or [])
    return ShowcaseListing(
        id=listing.id,
        title=title,
        description=description,
        features=list(features),
        price=listing.price,
        currency=listing.currency,
        property_type=listing.property_type,
        bedrooms=listing.bedrooms,
        bathrooms=listing.bathrooms,
        area_m2=listing.square_feet,
        city=listing.city,
        state=listing.state,
        country=listing.country,
        cover_url=cover_url,
        photo_count=photo_count,
        photos=photos or [],
    )


@router.get("/listings", response_model=list[ShowcaseListing])
async def list_listings(
    ctx: ShowcaseCtx,
    locale: LocaleDep,
    limit: int = 60,
) -> list[ShowcaseListing]:
    rows = list(
        (
            await ctx.session.execute(
                select(Property)
                .where(
                    Property.deleted_at.is_(None),
                    Property.status.in_(VISIBLE_STATUSES),
                )
                .order_by(Property.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    ids = [row.id for row in rows]
    translations = await PropertyTranslationService(ctx.session).for_listings(
        ids, locale
    )
    covers = await PropertyPhotoService(ctx.session, ctx.auth).covers_for(rows)

    # One aggregate rather than a query per card: forty listings on the page
    # would otherwise be forty round trips to print "12 photos".
    counts = dict(
        (
            await ctx.session.execute(
                select(Attachment.entity_id, func.count(Attachment.id))
                .where(
                    Attachment.organization_id == ctx.auth.organization_id,
                    Attachment.entity_type == "property",
                    Attachment.entity_id.in_(ids),
                    Attachment.deleted_at.is_(None),
                    Attachment.status == "available",
                )
                .group_by(Attachment.entity_id)
            )
        ).all()
    )

    return [
        _to_showcase(
            row,
            translations.get(row.id),
            covers.get(row.id),
            int(counts.get(row.id, 0)),
        )
        for row in rows
    ]


@router.get("/listings/{listing_id}", response_model=ShowcaseListing)
async def get_listing(
    listing_id: UUID, ctx: ShowcaseCtx, locale: LocaleDep
) -> ShowcaseListing:
    listing = (
        await ctx.session.execute(
            select(Property).where(
                Property.id == listing_id,
                Property.deleted_at.is_(None),
                Property.status.in_(VISIBLE_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such listing.")

    translations = await PropertyTranslationService(ctx.session).for_listings(
        [listing.id], locale
    )
    photos = PropertyPhotoService(ctx.session, ctx.auth)
    gallery = await photos.list_photos(listing.id)

    rendered: list[ShowcasePhoto] = []
    for photo in gallery:
        url = photos.view_url(photo)
        if url is None:
            continue
        rendered.append(
            ShowcasePhoto(url=url, is_cover=photo.id == listing.cover_attachment_id)
        )

    cover = next((p.url for p in rendered if p.is_cover), None) or (
        rendered[0].url if rendered else None
    )
    return _to_showcase(
        listing, translations.get(listing.id), cover, len(rendered), rendered
    )


@router.post("/enquiries", status_code=status.HTTP_202_ACCEPTED)
async def submit_enquiry(
    payload: EnquiryCreate,
    request: Request,
    ctx: ShowcaseCtx,
) -> dict[str, str]:
    client_ip = (request.client.host if request.client else "unknown") or "unknown"
    allowed = await check(ENQUIRY_LIMIT, f"showcase:enquiry:{client_ip}")
    if not allowed.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many enquiries. Try again later."
        )

    # What the visitor was reading, recorded where an agent will see it. The
    # listing id alone would make them go look it up.
    notes: list[str] = []
    if payload.message:
        notes.append(payload.message)
    if payload.listing_id:
        listing = (
            await ctx.session.execute(
                select(Property).where(Property.id == payload.listing_id)
            )
        ).scalar_one_or_none()
        if listing is not None:
            notes.append(f"Interested in: {listing.title}")
    if payload.locale:
        notes.append(f"Page language: {payload.locale}")

    lead = await LeadService(ctx.session, ctx.auth).create_lead(
        LeadCreate(
            first_name=payload.first_name,
            last_name=payload.last_name or "—",
            email=payload.email,
            phone=payload.phone,
            source="website",
            notes="\n".join(notes) or None,
        ),
        ctx.owner,
    )
    logger.info("showcase_enquiry", extra={"lead_id": str(lead.id)})
    return {"detail": "Thank you — we will be in touch shortly."}
