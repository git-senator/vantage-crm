"""Deal — the transaction a brokerage actually gets paid for.

The central business entity. Everything else in the CRM exists to produce one
of these, and Phase 4's analytics are computed from them.

Three things are load-bearing:

  * **`status` is derived, not stored.** A deal is won or lost because of the
    stage it sits in. Storing it separately means two sources of truth that
    drift the first time someone moves a deal without updating the flag.
  * **`stage_id` is RESTRICT, not CASCADE.** Deleting a stage that still holds
    deals must fail loudly. Silently orphaning live transactions is not a
    recovery story.
  * **`DealStageHistory` is append-only and never soft-deleted.** It is the
    substrate for cycle-time analytics and Phase 5 training data; a history
    that can be rewritten is not evidence of anything.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Interval,
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
from app.models.client import Client
from app.models.pipeline import PipelineStage
from app.models.property import Property
from app.models.user import User

DEAL_PRIORITIES = ("low", "medium", "high", "urgent")

#: Derived from the stage, never stored. See `Deal.status`.
DEAL_STATUSES = ("open", "won", "lost")


class Deal(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "deals"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The RBAC scope anchor. Unlike properties, a deal is personal business —
    # an agent holds deals.view at OWN scope, so the leads/clients rules apply
    # here, including 404-never-403 for a deal outside scope.
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ---------------------------------------------------------- relationships
    # A deal without a client is not a deal — it is a note. RESTRICT rather
    # than SET NULL, so a client with live transactions cannot be hard-deleted
    # out from under them.
    client_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Optional: a buyer-representation deal has no listing of its own until an
    # offer is made.
    property_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True,
    )

    pipeline_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stage_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("pipeline_stages.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # --------------------------------------------------------------- details
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Money is NUMERIC throughout. A float here is a rounding error in
    # somebody's commission cheque.
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Authoritative. Computed from `commission_rate` at write time when a rate
    # is supplied and an amount is not — but never overwritten once set,
    # because flat fees and negotiated splits are real and must survive.
    commission_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    # NUMERIC(5,4): 0.0250 is 2.5%. Kept as the agreed figure for reporting
    # even when it does not reconcile with a negotiated amount.
    commission_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )

    probability: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default="medium", server_default="medium"
    )

    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Set by the transition into a terminal stage, cleared on reopen.
    actual_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(title, '') || ' ' || "
            "coalesce(lost_reason, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")
    stage: Mapped[PipelineStage] = relationship(lazy="joined")
    client: Mapped[Client] = relationship(lazy="joined")
    # Named `listing`, not `property`: an attribute called `property` shadows
    # the builtin decorator for the rest of the class body, so the derived
    # `status`/`weighted_value` properties below would fail to define. It also
    # reads better — this is the listing the deal is on.
    listing: Mapped[Property | None] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("length(title) > 0", name="ck_deals_title"),
        CheckConstraint("value IS NULL OR value >= 0", name="ck_deals_value"),
        CheckConstraint(
            "commission_amount IS NULL OR commission_amount >= 0",
            name="ck_deals_commission_amount",
        ),
        # A rate above 1 is a percentage someone forgot to divide by 100.
        CheckConstraint(
            "commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 1)",
            name="ck_deals_commission_rate",
        ),
        CheckConstraint(
            "probability >= 0 AND probability <= 100", name="ck_deals_probability"
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')", name="ck_deals_priority"
        ),
        # The leading predicate of nearly every query is (org, owner).
        Index(
            "ix_deals_org_owner_created",
            "organization_id",
            "owner_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Keyset pagination orders by (created_at, id).
        Index(
            "ix_deals_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The Kanban board's query: every deal in a pipeline, grouped by stage.
        Index(
            "ix_deals_org_pipeline_stage",
            "organization_id",
            "pipeline_id",
            "stage_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_deals_org_client", "organization_id", "client_id"),
        Index("ix_deals_org_property", "organization_id", "property_id"),
        Index(
            "ix_deals_org_expected_close", "organization_id", "expected_close_date"
        ),
        Index("ix_deals_search", "search_vector", postgresql_using="gin"),
        Index(
            "ix_deals_title_trgm",
            text("title gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    # ------------------------------------------------------------ derived

    @property
    def status(self) -> str:
        """open | won | lost, from the stage the deal sits in.

        Derived rather than stored so it cannot disagree with the stage. The
        board is the source of truth about where a deal is; a separate column
        would be a second one.
        """
        # Annotated optional deliberately. `Mapped[PipelineStage]` promises a
        # stage is always there, and after a load it is — but on a Deal that
        # has been constructed and not yet flushed the relationship is None,
        # and this property is reachable then. Without the annotation mypy
        # believes the guard is dead code.
        stage: PipelineStage | None = self.stage
        if stage is None:
            return "open"
        if stage.is_won:
            return "won"
        if stage.is_lost:
            return "lost"
        return "open"

    @property
    def is_closed(self) -> bool:
        return self.status in ("won", "lost")

    @property
    def weighted_value(self) -> Decimal | None:
        """Value x probability. What a forecast actually sums.

        Null when there is no value — a forecast of an unpriced deal is zero
        information, and returning 0 would understate the pipeline silently.
        """
        if self.value is None:
            return None
        return (self.value * Decimal(self.probability) / Decimal(100)).quantize(
            Decimal("0.01")
        )

    def __repr__(self) -> str:
        return f"<Deal {self.id}>"


class DealStageHistory(Base, UUIDPrimaryKeyMixin):
    """Append-only record of every stage a deal has passed through.

    Powers cycle-time analytics and is Phase 5 training data. No soft delete
    and no update path: history that can be rewritten is not evidence.

    `duration_in_stage` records how long the deal sat in `from_stage_id` before
    this transition — computed from the previous row's `changed_at`, so cycle
    time is a column read rather than a window function over the whole table.
    It is NULL on the creation row, which has no prior stage.
    """

    __tablename__ = "deal_stage_history"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deal_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: NULL on the row written when the deal is created.
    from_stage_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("pipeline_stages.id", ondelete="RESTRICT"),
        nullable=True,
    )
    to_stage_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("pipeline_stages.id", ondelete="RESTRICT"),
        nullable=False,
    )

    changed_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    duration_in_stage: Mapped[Any | None] = mapped_column(Interval, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    from_stage: Mapped[PipelineStage | None] = relationship(
        foreign_keys=[from_stage_id], lazy="joined"
    )
    to_stage: Mapped[PipelineStage] = relationship(
        foreign_keys=[to_stage_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint(
            "from_stage_id IS NULL OR from_stage_id <> to_stage_id",
            name="ck_deal_stage_history_moved",
        ),
        # The timeline query: one deal's history, newest first.
        Index("ix_deal_stage_history_deal", "deal_id", "changed_at"),
        Index("ix_deal_stage_history_org", "organization_id", "changed_at"),
        # Cycle-time analytics group by the stage left behind.
        Index("ix_deal_stage_history_from_stage", "organization_id", "from_stage_id"),
    )

    def __repr__(self) -> str:
        return f"<DealStageHistory {self.id}>"
