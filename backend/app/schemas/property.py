"""Property contracts.

Same Create / Update / Read split as leads and clients. Two fields are
deliberately read-only and therefore absent from Create and Update:

  * `view_count` / `save_count` — written by tracking, never by a client. A
    listing agent who could POST their own view count would make every
    engagement metric meaningless.
  * `days_on_market` — derived from `listed_at`, so accepting it would let the
    two disagree.

`price` is optional: "price on application" is a real listing state, and
forcing 0 would corrupt every average.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PropertyStatus = Literal["active", "pending", "sold", "off_market", "coming_soon"]
PropertyType = Literal[
    "single_family", "condo", "townhouse", "multi_family", "land", "commercial"
]
ListingKind = Literal["sale", "rent"]
RentPeriod = Literal["month", "week", "day"]

Money = Annotated[Decimal, Field(ge=0, le=Decimal("99999999999.99"), decimal_places=2)]
Bathrooms = Annotated[Decimal, Field(ge=0, le=Decimal("100"), decimal_places=1)]
Latitude = Annotated[Decimal, Field(ge=Decimal("-90"), le=Decimal("90"))]
Longitude = Annotated[Decimal, Field(ge=Decimal("-180"), le=Decimal("180"))]


class PropertyAgent(BaseModel):
    """Minimal listing-agent projection for list and detail views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class PropertyPhoto(BaseModel):
    """One image of a listing, with a URL the browser can render right away.

    `url` is short-lived (`PHOTO_URL_TTL_SECONDS`) and minted per response, so
    it is never stored, cached in our own database, or handed to a client that
    was not already authorised for the listing.
    """

    id: UUID
    filename: str
    content_type: str
    url: str
    is_cover: bool


class PropertyBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    mls_number: str | None = Field(default=None, max_length=40)
    status: PropertyStatus = "active"
    property_type: PropertyType = "single_family"
    listing_kind: ListingKind = "sale"
    rent_period: RentPeriod | None = None

    address_line1: str = Field(min_length=1, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=50)
    #: Optional: many listings genuinely have no postcode, and a placeholder in
    #: this field prints on exports and contracts as if it were real.
    postal_code: str | None = Field(default=None, max_length=20)
    country: str = Field(default="US", min_length=2, max_length=2)

    latitude: Latitude | None = None
    longitude: Longitude | None = None

    price: Money | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)

    bedrooms: int | None = Field(default=None, ge=0, le=100)
    bathrooms: Bathrooms | None = None
    square_feet: int | None = Field(default=None, ge=0, le=10_000_000)
    lot_size_sqft: int | None = Field(default=None, ge=0, le=1_000_000_000)
    year_built: int | None = Field(default=None, ge=1600, le=2200)

    listed_at: date | None = None
    description: str | None = Field(default=None, max_length=20_000)
    features: list[str] = Field(default_factory=list, max_length=40)

    client_id: UUID | None = None

    @model_validator(mode="after")
    def _coordinates_are_a_pair(self) -> PropertyBase:
        """One coordinate without the other is not a location.

        A latitude alone silently places every such listing on the Greenwich
        meridian once something starts mapping them.
        """
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude and longitude must be provided together"
            )
        return self

    @model_validator(mode="after")
    def _rentals_state_their_period(self) -> PropertyBase:
        """A rent needs a period; a sale must not carry one.

        Defaulted rather than rejected in the first case: "per month" is what a
        rental means everywhere unless it says otherwise, and refusing the
        payload would only make every caller type the same word. The reverse is
        an error, because a sale price per month is not a shorthand for
        anything — it is two different listings confused with each other.
        """
        if self.listing_kind == "rent" and self.rent_period is None:
            self.rent_period = "month"
        if self.listing_kind == "sale" and self.rent_period is not None:
            raise ValueError("rent_period only applies to a rental listing")
        return self


class PropertyCreate(PropertyBase):
    """New listing. Listing agent defaults to the creator when omitted."""

    listing_agent_id: UUID | None = None


class PropertyUpdate(BaseModel):
    """Partial update. Every field optional; unset fields are untouched."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    mls_number: str | None = Field(default=None, max_length=40)
    status: PropertyStatus | None = None
    property_type: PropertyType | None = None
    listing_kind: ListingKind | None = None
    rent_period: RentPeriod | None = None

    address_line1: str | None = Field(default=None, min_length=1, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, min_length=1, max_length=50)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)

    latitude: Latitude | None = None
    longitude: Longitude | None = None

    price: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    bedrooms: int | None = Field(default=None, ge=0, le=100)
    bathrooms: Bathrooms | None = None
    square_feet: int | None = Field(default=None, ge=0, le=10_000_000)
    lot_size_sqft: int | None = Field(default=None, ge=0, le=1_000_000_000)
    year_built: int | None = Field(default=None, ge=1600, le=2200)

    listed_at: date | None = None
    description: str | None = Field(default=None, max_length=20_000)
    features: list[str] | None = Field(default=None, max_length=40)

    client_id: UUID | None = None
    listing_agent_id: UUID | None = None


class PropertyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    mls_number: str | None
    status: str
    property_type: str
    listing_kind: str
    rent_period: str | None

    address_line1: str
    address_line2: str | None
    city: str
    state: str
    postal_code: str | None
    country: str
    full_address: str

    latitude: Decimal | None
    longitude: Decimal | None

    price: Decimal | None
    currency: str

    bedrooms: int | None
    bathrooms: Decimal | None
    square_feet: int | None
    lot_size_sqft: int | None
    year_built: int | None

    listed_at: date | None
    #: Derived from `listed_at`; null once sold.
    days_on_market: int | None
    view_count: int
    save_count: int

    description: str | None
    features: list[str]
    custom_fields: dict[str, Any]

    #: The language the listing was written in.
    source_locale: str = "en"
    #: The language `title`, `description` and `features` above are actually in.
    #: Usually the requested one; falls back to `source_locale` when no
    #: translation exists yet, because an empty description is worse than one
    #: in the wrong language.
    content_locale: str = "en"
    #: The text above came from a model and no one has checked it.
    content_is_machine: bool = False
    #: A person wrote this translation and the source has changed since.
    content_is_stale: bool = False

    client_id: UUID | None
    listing_agent: PropertyAgent | None

    #: The main photo, ready to render. Null when the listing has none — the
    #: card then draws its generated gradient, which is what every listing
    #: looked like before photography existed.
    cover_attachment_id: UUID | None = None
    cover_url: str | None = None

    created_at: datetime
    updated_at: datetime


class PropertyTranslationRead(BaseModel):
    """One listing's text in one language, with where it came from.

    The provenance is not decoration: an agent about to send this to a client
    needs to know whether a person has ever read it.
    """

    model_config = ConfigDict(from_attributes=True)

    locale: str
    title: str
    description: str | None
    features: list[str]
    #: No one has checked this text.
    is_machine: bool
    #: A person wrote it, and the listing has changed since.
    is_stale: bool
    translated_at: datetime | None
    edited_at: datetime | None


class PropertyTranslationUpdate(BaseModel):
    """An agent's correction to one language.

    Every field is required rather than patchable: a translation is one piece
    of prose, and accepting a title without its description invites a listing
    that is half corrected and half machine output with nothing to say which
    is which.
    """

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    features: list[str] = Field(default_factory=list, max_length=40)


class PropertyFilters(BaseModel):
    """Explicit filter parameters.

    Deliberately not a generic query DSL, for the reasons given in
    `LeadFilters` — injection surface, unbounded queries, and index
    requirements nobody can reason about.
    """

    search: str | None = Field(default=None, max_length=200)
    status: PropertyStatus | None = None
    property_type: PropertyType | None = None
    listing_kind: ListingKind | None = None
    listing_agent_id: UUID | None = None
    client_id: UUID | None = None
    city: str | None = Field(default=None, max_length=100)
    min_price: Money | None = None
    max_price: Money | None = None
    min_bedrooms: int | None = Field(default=None, ge=0, le=100)
    feature: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _price_range_is_coherent(self) -> PropertyFilters:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price")
        return self


class PropertyAssign(BaseModel):
    listing_agent_id: UUID
