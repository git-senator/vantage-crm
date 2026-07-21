"""Client — someone you actively represent.

The second CRM entity. Structurally a copy of `Lead` (see that module for why
`owner_id`, the generated `search_vector` and NUMERIC money are the way they
are). Three things genuinely differ:

  * **Identity may be a person or a company.** Investor entities — LLCs, trusts,
    holding companies — are real clients, and forcing "Tanaka Holdings Co" into
    a first/last name pair loses information and sorts wrongly. All three name
    columns are therefore nullable, with a CHECK guaranteeing at least one
    usable identity.
  * `source_lead_id` carries the funnel link back to the originating lead, and
    is UNIQUE so a lead cannot be converted twice. That uniqueness is the
    concurrency guarantee, not a nicety — see `ClientService.convert_lead`.
  * `status` is an ordinary editable field here. On a lead it is not, because
    converting is a domain action; a client going dormant is just an edit.
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
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

# TEXT + CHECK rather than a native enum, for the same reason as Lead: altering
# a PostgreSQL enum needs locks and migration gymnastics.
CLIENT_TYPES = ("buyer", "seller", "investor", "landlord", "tenant", "other")
CLIENT_STATUSES = ("active", "under_contract", "dormant", "past")


class Client(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "clients"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The RBAC scope anchor. SET NULL rather than CASCADE: deleting a user must
    # not delete their book of business.
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ------------------------------------------------------------ identity
    # All nullable; ck_clients_identity is what makes the combination valid.
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # ----------------------------------------------------- classification
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="buyer")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # -------------------------------------------------------------- details
    address: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Writable for now. Once Deals ships this becomes derived from closed deal
    # value; the column stays, the write path moves.
    lifetime_value: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    client_since: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(40)), nullable=False, default=list, server_default="{}"
    )

    custom_fields: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    # --------------------------------------------------------------- funnel
    # SET NULL: purging a lead under a retention rule must not cascade into
    # deleting the client it became.
    source_lead_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
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
    # Generated by the database, so it cannot drift from the row.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(company_name, '') || ' ' || "
            "coalesce(first_name, '') || ' ' || "
            "coalesce(last_name, '') || ' ' || "
            "coalesce(email::text, '') || ' ' || "
            "coalesce(phone, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")

    __table_args__ = (
        # At least one usable identity. Without this a client can be created
        # with no name at all, which every list view then has to defend against.
        CheckConstraint(
            "(first_name IS NOT NULL AND last_name IS NOT NULL) "
            "OR company_name IS NOT NULL",
            name="ck_clients_identity",
        ),
        CheckConstraint(
            "type IN ('buyer', 'seller', 'investor', 'landlord', 'tenant', 'other')",
            name="ck_clients_type",
        ),
        CheckConstraint(
            "status IN ('active', 'under_contract', 'dormant', 'past')",
            name="ck_clients_status",
        ),
        CheckConstraint(
            "lifetime_value IS NULL OR lifetime_value >= 0",
            name="ck_clients_lifetime_value",
        ),
        # One lead converts to at most one client, enforced by the database
        # rather than by a read-then-write check that two transactions can both
        # pass. Partial, so the many clients created directly (NULL) do not
        # collide with each other.
        Index(
            "uq_clients_source_lead",
            "source_lead_id",
            unique=True,
            postgresql_where=text("source_lead_id IS NOT NULL"),
        ),
        # The leading predicate of nearly every query is (org, owner) — RLS
        # supplies the org, scope supplies the owner.
        Index(
            "ix_clients_org_owner_created",
            "organization_id",
            "owner_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Keyset pagination orders by (created_at, id); this serves it directly.
        Index(
            "ix_clients_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_clients_org_type", "organization_id", "type"),
        Index("ix_clients_org_status", "organization_id", "status"),
        Index("ix_clients_search", "search_vector", postgresql_using="gin"),
        # Trigram index for fuzzy name matching. Unlike the leads equivalent
        # every part needs coalescing, because any of the three may be NULL and
        # `'a' || NULL` is NULL — which would index nothing at all.
        Index(
            "ix_clients_name_trgm",
            text(
                "(coalesce(company_name, '') || ' ' || coalesce(first_name, '') "
                "|| ' ' || coalesce(last_name, '')) gin_trgm_ops"
            ),
            postgresql_using="gin",
        ),
    )

    @property
    def display_name(self) -> str:
        """What to show in a list. Company wins when both are present.

        A person at a company is filed under the company in this domain — that
        is the relationship being represented.
        """
        if self.company_name:
            return self.company_name
        return f"{self.first_name or ''} {self.last_name or ''}".strip()

    @property
    def is_company(self) -> bool:
        return self.company_name is not None

    def __repr__(self) -> str:
        # No name or email: reprs end up in logs and tracebacks.
        return f"<Client {self.id}>"
