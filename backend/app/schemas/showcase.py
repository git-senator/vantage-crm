"""What the public showcase is allowed to say about a listing.

A separate read model from `PropertyRead`, and deliberately so. The internal
schema carries things a stranger has no business seeing — which agent holds the
listing, which client it is filed against, its pipeline status, the notes. A
public page that reuses the internal schema leaks all of that the moment
somebody adds a field, and nobody notices because the page still renders.

So this model lists what a buyer needs and nothing else. Adding a field here is
a deliberate act with the word "public" written next to it.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.lead import Name


class ShowcasePhoto(BaseModel):
    url: str
    is_cover: bool = False


class ShowcaseListing(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    features: list[str] = Field(default_factory=list)

    price: Decimal | None = None
    currency: str | None = None
    property_type: str | None = None
    bedrooms: int | None = None
    bathrooms: Decimal | None = None
    #: The column is `square_feet` for historical reasons; this catalogue is
    #: Brazilian and the numbers in it are square metres. Named for what it
    #: holds, so the page does not have to know that story.
    area_m2: int | None = None

    city: str | None = None
    state: str | None = None
    country: str | None = None

    cover_url: str | None = None
    photo_count: int = 0
    #: Populated on the detail view only; the list view would mint hundreds of
    #: signed URLs to render thumbnails nobody scrolled to.
    photos: list[ShowcasePhoto] = Field(default_factory=list)


class EnquiryCreate(BaseModel):
    """What a visitor fills in. Kept to the minimum that makes a lead useful.

    No honeypot or captcha here: the endpoint is rate-limited by IP, and a form
    this small is cheap to re-key. Adding friction to a buyer who is trying to
    give us money is the wrong trade.
    """

    first_name: Name
    last_name: Name | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    message: str | None = Field(default=None, max_length=2000)
    #: The listing the visitor was looking at, if any. This is the whole point:
    #: an agent opening the lead sees what the person was reading, not just
    #: that "someone from the website" wrote in.
    listing_id: UUID | None = None
    #: The page's language, so the first reply is in the language they used.
    locale: str | None = Field(default=None, max_length=10)
