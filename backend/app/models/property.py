"""Property — a listing in the brokerage's inventory.

Structurally the third copy of the Leads pattern, but with one genuinely
different property that shapes everything: **listings are shared inventory.**

An agent holds `properties.view` at ALL scope and `properties.manage` at OWN
(see `app/core/permissions.py`). Every agent sees the whole book; only the
listing agent edits their own. That asymmetry is the reason scope is modelled
separately from permission, and it has two consequences here:

  * `listing_agent_id` is the scope anchor, not `owner_id`. The name matters —
    on a property, "owner" reads as the person who owns the real estate, which
    is a different party entirely (that is `client_id`).
  * A listing the caller can see but not edit must return **403, not 404**.
    The 404-everywhere rule elsewhere exists to avoid an existence oracle;
    there is no secret to protect when the record is already visible in the
    caller's own list, and a 404 there is simply confusing. See
    `PropertyService._load_for_write`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

# TEXT + CHECK rather than a native enum, for the same reason as Lead: altering
# a PostgreSQL enum needs locks and migration gymnastics.
PROPERTY_STATUSES = ("active", "pending", "sold", "off_market", "coming_soon")
PROPERTY_TYPES = (
    "single_family",
    "condo",
    "townhouse",
    "multi_family",
    "land",
    "commercial",
)


class Property(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "properties"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The RBAC scope anchor — the agent representing this listing. SET NULL
    # rather than CASCADE: an agent leaving must not delete the brokerage's
    # inventory.
    listing_agent_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The seller — the client whose property this is. Distinct from the listing
    # agent in every way, which is why it is a separate column and not an
    # overloaded "owner".
    client_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The main photo. A nullable FK to an attachment rather than a flag on the
    # attachment itself: "which photo leads" is a fact about the *listing*, and
    # a flag would let two rows claim the lead at once with nothing to stop
    # them. SET NULL because deleting the photo must not delete the listing —
    # the card falls back to its generated gradient until a new cover is set.
    #
    # The rest of the gallery needs no column: an attachment already knows the
    # record it hangs off (`entity_type='property'`, `entity_id`), so the
    # remaining photos *are* that list minus the cover.
    cover_attachment_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ------------------------------------------------------------- listing
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    mls_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    property_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="single_family"
    )

    # ------------------------------------------------------------- address
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")

    # Precision 9 scale 6: ±180.000000 fits, and 6 decimal places is ~11cm —
    # far beyond what a listing address needs.
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    # ------------------------------------------------------------ specifics
    # Money is NUMERIC. Floats must never touch a price.
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    bedrooms: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # NUMERIC(3,1), because half-baths are real and 2.5 is not an integer.
    bathrooms: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    square_feet: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_size_sqft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_built: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    listed_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Written by a later phase's view tracking. Present now so the columns do
    # not have to be added to a large table later.
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    save_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    features: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(60)), nullable=False, default=list, server_default="{}"
    )
    custom_fields: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    # --------------------------------------------------------------- audit
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -------------------------------------------------------------- search
    # Generated by the database, so it cannot drift from the row. Address and
    # MLS number are included because "1428 Sanchez" and "MLS-4471" are what
    # agents actually type into the box.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(title, '') || ' ' || "
            "coalesce(mls_number, '') || ' ' || "
            "coalesce(address_line1, '') || ' ' || "
            "coalesce(city, '') || ' ' || "
            "coalesce(state, '') || ' ' || "
            "coalesce(postal_code, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    listing_agent: Mapped[User | None] = relationship(
        foreign_keys=[listing_agent_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'pending', 'sold', 'off_market', 'coming_soon')",
            name="ck_properties_status",
        ),
        CheckConstraint(
            "property_type IN ('single_family', 'condo', 'townhouse', "
            "'multi_family', 'land', 'commercial')",
            name="ck_properties_type",
        ),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_properties_price"),
        CheckConstraint(
            "bedrooms IS NULL OR (bedrooms >= 0 AND bedrooms <= 100)",
            name="ck_properties_bedrooms",
        ),
        CheckConstraint(
            "bathrooms IS NULL OR (bathrooms >= 0 AND bathrooms <= 100)",
            name="ck_properties_bathrooms",
        ),
        CheckConstraint(
            "square_feet IS NULL OR square_feet >= 0", name="ck_properties_square_feet"
        ),
        CheckConstraint(
            "lot_size_sqft IS NULL OR lot_size_sqft >= 0",
            name="ck_properties_lot_size",
        ),
        # Upper bound is deliberately loose — "built in 2100" is a typo, but a
        # pre-construction listing legitimately sits a few years ahead.
        CheckConstraint(
            "year_built IS NULL OR (year_built >= 1600 AND year_built <= 2200)",
            name="ck_properties_year_built",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_properties_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_properties_longitude",
        ),
        CheckConstraint("length(title) > 0", name="ck_properties_title"),
        CheckConstraint("length(address_line1) > 0", name="ck_properties_address"),
        # An MLS number identifies a listing within a market. Unique per tenant,
        # partial because most listings are entered before one is issued.
        Index(
            "uq_properties_org_mls",
            "organization_id",
            "mls_number",
            unique=True,
            postgresql_where=text("mls_number IS NOT NULL AND deleted_at IS NULL"),
        ),
        # Unlike leads and clients, the leading access pattern is NOT
        # (org, agent) — every agent sees every listing, so the common query
        # filters on status and price, not on the scope anchor.
        Index(
            "ix_properties_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_properties_org_status_price",
            "organization_id",
            "status",
            "price",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Still needed: a manager filtering to their team's listings, and the
        # OWN-scope predicate on write.
        Index(
            "ix_properties_org_agent_created",
            "organization_id",
            "listing_agent_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_properties_org_type", "organization_id", "property_type"),
        Index("ix_properties_org_client", "organization_id", "client_id"),
        Index("ix_properties_search", "search_vector", postgresql_using="gin"),
        # Trigram over the address, so "Sanchez St" finds "Sanchez Street".
        Index(
            "ix_properties_address_trgm",
            text("(address_line1 || ' ' || city) gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    @property
    def full_address(self) -> str:
        parts = [self.address_line1, self.address_line2, self.city, self.state]
        joined = ", ".join(part for part in parts if part)
        return f"{joined} {self.postal_code}".strip()

    @property
    def days_on_market(self) -> int | None:
        """Derived, not stored.

        docs/DATABASE.md lists `days_on_market` as a column. It is computed here
        instead: a stored counter is correct on the day it is written and wrong
        every day after, so it needs a nightly job whose only purpose is to fix
        a number that arithmetic already gives for free.

        Sold listings freeze — days on market is a property of the *listing
        period*, and a sold home does not keep accruing.
        """
        if self.listed_at is None or self.status == "sold":
            return None
        return (date.today() - self.listed_at).days

    def __repr__(self) -> str:
        # No address: reprs end up in logs and tracebacks.
        return f"<Property {self.id}>"
