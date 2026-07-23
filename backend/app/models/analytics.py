"""Metric snapshots — the substrate for every time series in the product.

A snapshot is one number, for one metric, for one person, on one day.

**Why the grain is per-owner and not per-organization.** Analytics reuses the
CRM's scope rules: an agent sees their own numbers, a manager the team's, an
admin everything (docs/ANALYTICS.md §2). An org-grain snapshot could only ever
answer the admin's question — handing an agent an org-wide history would be a
larger disclosure than any list endpoint allows, and computing their slice would
mean re-querying live data and abandoning the snapshot entirely. Per-owner rows
roll up by summing the ids the scope resolver returns, which is the same
predicate the list endpoints use.

`owner_id` is nullable because some records genuinely have no owner — an
unassigned lead is still a lead that was created. Those rows are visible only at
ALL scope, which matches how an unowned record behaves everywhere else.

**Why snapshots at all, rather than always aggregating live.** A month of daily
readings for one agent is thirty indexed row reads; the equivalent live query is
thirty aggregate scans over the whole deal table with a date predicate that no
index serves well. The trade-off is staleness: today's row does not exist until
tonight, so "today" is always computed live and history comes from snapshots.
That seam is in one place — `AnalyticsService.series` — rather than every caller
deciding.

Levels and flows share this table but must not be aggregated the same way; the
metric registry declares which is which and the service enforces it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class MetricSnapshot(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "metric_snapshots"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Whose number this is. NULL for records with no owner, which are visible
    #: only at ALL scope — the same rule an unowned record follows elsewhere.
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric_key: Mapped[str] = mapped_column(String(60), nullable=False)

    #: NUMERIC, not float. These are counts and money, and a revenue figure that
    #: has been through a float has already lost the precision the column type
    #: exists to protect.
    value: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("length(metric_key) > 0", name="ck_metric_snapshots_key"),
        # One reading per person per metric per day. The writer upserts on this,
        # so a re-run backfills rather than duplicating — which matters because
        # the sweep is retried and may legitimately run twice.
        #
        # NULLS NOT DISTINCT because `owner_id` is nullable: under the SQL
        # default two NULLs are never equal, so the upsert's ON CONFLICT target
        # would never match an unowned row and every run would insert a fresh
        # duplicate instead of correcting yesterday's. Postgres 15+.
        UniqueConstraint(
            "organization_id",
            "owner_id",
            "snapshot_date",
            "metric_key",
            name="uq_metric_snapshots_grain",
            postgresql_nulls_not_distinct=True,
        ),
        # The series query: one metric across a date range for a set of owners.
        Index(
            "ix_metric_snapshots_series",
            "organization_id",
            "metric_key",
            "snapshot_date",
        ),
        # The leaderboard query: every owner's value for one metric on one day.
        Index(
            "ix_metric_snapshots_owner",
            "organization_id",
            "owner_id",
            "snapshot_date",
        ),
    )

    def __repr__(self) -> str:
        return f"<MetricSnapshot {self.metric_key} {self.snapshot_date} {self.value}>"


class Goal(Base, UUIDPrimaryKeyMixin):
    """A target for a metric over a period, for one person or the whole team.

    Deliberately not a general "objective" model with key results and cascading
    parents. A CRM goal is "book £2m this quarter" — one metric, one number, one
    window — and the elaborate version is a different product.

    `owner_id` NULL means the target is for the workspace as a whole rather than
    for nobody, which is the opposite of what it means on a snapshot. The
    distinction is worth stating because the two tables sit next to each other:
    a snapshot describes what happened and an unowned record has no owner; a
    goal describes an intention and the workspace can hold one.
    """

    __tablename__ = "goals"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    metric_key: Mapped[str] = mapped_column(String(60), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="ck_goals_period"),
        CheckConstraint("target_value > 0", name="ck_goals_target"),
        # NULL owner means "the whole workspace" here, and a workspace holds one
        # target per metric per period — so these NULLs must compare equal too.
        UniqueConstraint(
            "organization_id",
            "owner_id",
            "metric_key",
            "period_start",
            name="uq_goals_grain",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_goals_period", "organization_id", "period_start", "period_end"),
    )

    def __repr__(self) -> str:
        return f"<Goal {self.metric_key} {self.target_value}>"
