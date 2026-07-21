"""Lead — a prospect, before they become a client.

The first CRM entity, and the reference for the rest. Points worth copying:

  * `owner_id` is what RBAC scope resolves against. Every scoped entity needs
    one, and it must be indexed together with `organization_id` because that
    pair is the leading predicate of nearly every query.
  * `search_vector` is a generated column, so it can never drift from the data.
    Maintaining it in application code guarantees it eventually will.
  * Money is NUMERIC. Floats must never touch a budget figure.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

# Pipeline position. TEXT + CHECK rather than a native enum: altering a
# PostgreSQL enum needs locks and migration gymnastics, and brokerages
# reconfigure their funnel constantly.
LEAD_STAGES = ("new", "contacted", "qualified", "touring", "unqualified")
LEAD_TEMPERATURES = ("hot", "warm", "cold")
LEAD_STATUSES = ("open", "converted", "lost")

LEAD_SOURCES = (
    "zillow",
    "website",
    "referral",
    "open_house",
    "instagram",
    "cold_call",
    "realtor_com",
    "other",
)


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "leads"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The RBAC scope anchor. SET NULL rather than CASCADE: deleting a user must
    # not delete their pipeline.
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ------------------------------------------------------------ identity
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # ------------------------------------------------------------ pipeline
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    temperature: Mapped[str] = mapped_column(String(10), nullable=False, default="warm")

    # ------------------------------------------------------------- details
    budget_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    budget_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    preferred_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(40)), nullable=False, default=list, server_default="{}"
    )

    # Written by the Phase 5 scoring service. Present now so the column does not
    # have to be added to a large table later.
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --------------------------------------------------------------- funnel
    # Set by conversion, alongside status='converted'. The lead is kept rather
    # than deleted: it is the top of the funnel, and Phase 4 conversion
    # reporting is computed from these rows.
    # `use_alter` breaks the leads <-> clients FK cycle: without it
    # `create_all` cannot order the two CREATE TABLEs and raises
    # CircularDependencyError, taking the whole integration suite with it. The
    # constraint is emitted as a separate ALTER once both tables exist, which
    # is exactly what the migration does by hand. Named explicitly because an
    # ALTER-added constraint must be nameable to be dropped.
    converted_client_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(
            "clients.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_leads_converted_client_id",
        ),
        nullable=True,
    )
    converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    # Generated by the database, so it cannot drift from the row. Keeping it in
    # sync from application code works until the one place that forgets.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(first_name, '') || ' ' || "
            "coalesce(last_name, '') || ' ' || "
            "coalesce(email::text, '') || ' ' || "
            "coalesce(phone, '') || ' ' || "
            "coalesce(preferred_location, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    owner: Mapped[User | None] = relationship(
        foreign_keys=[owner_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN ('new', 'contacted', 'qualified', 'touring', 'unqualified')",
            name="ck_leads_stage",
        ),
        CheckConstraint(
            "status IN ('open', 'converted', 'lost')", name="ck_leads_status"
        ),
        CheckConstraint(
            "temperature IN ('hot', 'warm', 'cold')", name="ck_leads_temperature"
        ),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="ck_leads_score"),
        CheckConstraint(
            "budget_min IS NULL OR budget_max IS NULL OR budget_min <= budget_max",
            name="ck_leads_budget_range",
        ),
        CheckConstraint("budget_min IS NULL OR budget_min >= 0", name="ck_leads_budget_min"),
        CheckConstraint("length(first_name) > 0", name="ck_leads_first_name"),
        CheckConstraint("length(last_name) > 0", name="ck_leads_last_name"),
        # The leading predicate of nearly every query is (org, owner) — RLS
        # supplies the org, scope supplies the owner.
        Index(
            "ix_leads_org_owner_created",
            "organization_id",
            "owner_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Keyset pagination orders by (created_at, id); this serves it directly.
        Index(
            "ix_leads_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_leads_org_stage", "organization_id", "stage"),
        Index("ix_leads_org_status", "organization_id", "status"),
        Index(
            "ix_leads_search",
            "search_vector",
            postgresql_using="gin",
        ),
        # Trigram index for fuzzy name matching — "Lindqvist" typed as
        # "Lindquist" should still find the record.
        Index(
            "ix_leads_name_trgm",
            text("(first_name || ' ' || last_name) gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __repr__(self) -> str:
        # No name or email: reprs end up in logs and tracebacks.
        return f"<Lead {self.id}>"
