"""Analytics aggregates.

Extends the Phase 2.8 dashboard repository rather than replacing it: that one
answers "what are the current totals", this one answers "how did they move, and
who moved them". Both take an already-resolved scope predicate and apply it the
way the entity's own repository does, so no analytics number can exceed what the
caller could reach by browsing.

Two things every method here holds to:

* **Aggregation stays in the database.** Counts, sums and averages are computed
  by Postgres over an index; nothing is loaded into Python to be tallied. At
  100k deals the difference is not a constant factor.
* **A period is half-open, `[start, end)`.** Inclusive-end date ranges are how
  a deal closed at 23:30 on the last day of the month goes missing from the
  month, and how one closed at midnight gets counted twice across two periods.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.analytics import MetricSnapshot
from app.models.client import Client
from app.models.deal import Deal, DealStageHistory
from app.models.lead import Lead
from app.models.pipeline import PipelineStage
from app.models.property import Property
from app.models.task import Task
from app.models.user import User

#: Returned instead of a number when the caller holds no grant on the metric's
#: entity. Distinct from zero, which means "you may see this, and it is zero".
NO_ACCESS = None


def _scoped(query: Select[Any], column: Any, ids: list[UUID] | None) -> Select[Any]:
    """Apply an owner IN predicate, or nothing for ALL scope."""
    return query.where(column.in_(ids)) if ids is not None else query


def _date_bounds(start: datetime, end: datetime) -> tuple[date, date]:
    """Half-open `[start, end)` translated onto a DATE column.

    `deals.actual_close_date` is a DATE, so the period's instants cannot be
    compared against it directly. Naively taking `start.date()` and `end.date()`
    as an exclusive range loses the current day for any period ending "now" —
    which is every period the dashboard asks for, and would show today's wins as
    zero until midnight.

    So the upper bound is the last day the interval actually touches: one
    microsecond before `end`. A period ending exactly at midnight therefore ends
    on the previous day, and one ending mid-afternoon includes today. Both are
    what the half-open instant range means, expressed in whole days.
    """
    return start.date(), (end - timedelta(microseconds=1)).date()


def _is_open() -> ColumnElement[bool]:
    """The "deal is still open" predicate.

    Not a column: `Deal.status` is a Python property derived from the stage so
    it cannot disagree with the board. Every query meaning "open" joins the
    stage and uses this — the same predicate `DealRepository` and the Phase 2.8
    dashboard use, so all three agree by construction.
    """
    return ~PipelineStage.is_won & ~PipelineStage.is_lost


class AnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- flows
    #
    # Everything counted *within* a period. All half-open on [start, end).

    async def leads_created(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .where(and_(Lead.created_at >= start, Lead.created_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Lead.owner_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def leads_converted(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        """Counted on `converted_at`, not on status.

        Conversion is a domain action with its own timestamp; deriving it from
        `status = 'converted'` would count a lead in whichever period you happen
        to ask, rather than the period it actually converted in.
        """
        query = (
            select(func.count())
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .where(Lead.converted_at.is_not(None))
            .where(and_(Lead.converted_at >= start, Lead.converted_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Lead.owner_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def clients_created(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Client)
            .where(Client.organization_id == organization_id)
            .where(Client.deleted_at.is_(None))
            .where(and_(Client.created_at >= start, Client.created_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Client.owner_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def deals_created(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Deal)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(and_(Deal.created_at >= start, Deal.created_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Deal.owner_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def closed_deal_stats(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> tuple[int, int, Decimal]:
        """`(won, lost, revenue_won)` for deals closed in the period.

        One query rather than three: the predicate and the join are identical,
        and three passes over the same rows to produce three numbers that always
        appear together is work for nothing.

        Counted on `closed_at`, which the stage transition sets. A deal opened
        in January and won in March is March's win — asking on `created_at`
        would credit the month the work started rather than the month it paid.
        """
        first_day, last_day = _date_bounds(start, end)
        query = (
            select(
                func.count().filter(PipelineStage.is_won),
                func.count().filter(PipelineStage.is_lost),
                func.coalesce(
                    func.sum(case((PipelineStage.is_won, Deal.value), else_=0)), 0
                ),
            )
            .select_from(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(Deal.actual_close_date.is_not(None))
            .where(Deal.actual_close_date >= first_day)
            .where(Deal.actual_close_date <= last_day)
        )
        result = await self.session.execute(_scoped(query, Deal.owner_id, owner_ids))
        row = result.one()
        return int(row[0] or 0), int(row[1] or 0), Decimal(row[2] or 0)

    async def sales_cycle_days(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> Decimal | None:
        """Mean days from opening to winning, for deals won in the period.

        None rather than zero when nothing closed: an average over no deals is
        undefined, and rendering it as "0 days" claims an impossibly fast cycle.
        """
        first_day, last_day = _date_bounds(start, end)
        query = (
            select(func.avg(Deal.actual_close_date - func.date(Deal.created_at)))
            .select_from(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(PipelineStage.is_won)
            .where(Deal.actual_close_date.is_not(None))
            .where(Deal.actual_close_date >= first_day)
            .where(Deal.actual_close_date <= last_day)
        )
        result = await self.session.execute(_scoped(query, Deal.owner_id, owner_ids))
        value = result.scalar()
        return Decimal(str(round(float(value), 2))) if value is not None else None

    async def listings_sold(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Property)
            .where(Property.organization_id == organization_id)
            .where(Property.deleted_at.is_(None))
            .where(Property.status == "sold")
            .where(and_(Property.updated_at >= start, Property.updated_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Property.listing_agent_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def tasks_completed(
        self, organization_id: UUID, assignee_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Task)
            .where(Task.organization_id == organization_id)
            .where(Task.deleted_at.is_(None))
            .where(Task.completed_at.is_not(None))
            .where(and_(Task.completed_at >= start, Task.completed_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Task.assignee_id, assignee_ids)
        )
        return int(result.scalar() or 0)

    async def activities_logged(
        self, organization_id: UUID, actor_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Activity)
            # `activities` carries no soft delete: a timeline entry records
            # something that happened, and history that can be removed is not
            # history. So there is no deleted_at predicate here.
            .where(Activity.organization_id == organization_id)
            .where(and_(Activity.occurred_at >= start, Activity.occurred_at < end))
        )
        result = await self.session.execute(
            _scoped(query, Activity.actor_id, actor_ids)
        )
        return int(result.scalar() or 0)

    # ------------------------------------------------------------ levels
    #
    # Readings at an instant. No date predicate — "as at now" is the only
    # question these answer, and a historical reading comes from a snapshot.

    async def leads_open(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> int:
        query = (
            select(func.count())
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .where(Lead.status == "open")
        )
        result = await self.session.execute(
            _scoped(query, Lead.owner_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def open_pipeline(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> tuple[Decimal, Decimal]:
        """`(open_value, weighted_value)`.

        Weighted uses the deal's own probability rather than its stage default:
        a deal whose probability was overridden is telling you something the
        stage does not know.
        """
        query = (
            select(
                func.coalesce(func.sum(Deal.value), 0),
                func.coalesce(
                    func.sum(Deal.value * func.coalesce(Deal.probability, 0) / 100.0), 0
                ),
            )
            .select_from(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(_is_open())
        )
        result = await self.session.execute(_scoped(query, Deal.owner_id, owner_ids))
        row = result.one()
        return Decimal(row[0] or 0), Decimal(row[1] or 0)

    async def listings_active(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> int:
        query = (
            select(func.count())
            .select_from(Property)
            .where(Property.organization_id == organization_id)
            .where(Property.deleted_at.is_(None))
            .where(Property.status == "active")
        )
        result = await self.session.execute(
            _scoped(query, Property.listing_agent_id, owner_ids)
        )
        return int(result.scalar() or 0)

    async def average_days_on_market(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Decimal | None:
        query = (
            select(
                func.avg(func.extract("epoch", func.now() - Property.listed_at) / 86400.0)
            )
            .select_from(Property)
            .where(Property.organization_id == organization_id)
            .where(Property.deleted_at.is_(None))
            .where(Property.status == "active")
            .where(Property.listed_at.is_not(None))
        )
        result = await self.session.execute(
            _scoped(query, Property.listing_agent_id, owner_ids)
        )
        value = result.scalar()
        return Decimal(str(round(float(value), 2))) if value is not None else None

    async def tasks_overdue(
        self, organization_id: UUID, assignee_ids: list[UUID] | None
    ) -> int:
        query = (
            select(func.count())
            .select_from(Task)
            .where(Task.organization_id == organization_id)
            .where(Task.deleted_at.is_(None))
            .where(Task.completed_at.is_(None))
            .where(Task.due_at.is_not(None))
            .where(Task.due_at < func.now())
        )
        result = await self.session.execute(
            _scoped(query, Task.assignee_id, assignee_ids)
        )
        return int(result.scalar() or 0)

    # -------------------------------------------------------- breakdowns

    async def pipeline_by_stage(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> list[tuple[UUID, str, int, Decimal]]:
        """`(stage_id, stage_name, deal_count, value)` for open deals.

        Ordered by the stage's own position so the funnel reads in pipeline
        order rather than by whichever stage happens to hold the most money.
        """
        query = (
            select(
                PipelineStage.id,
                PipelineStage.name,
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.value), 0),
            )
            .select_from(PipelineStage)
            .join(
                Deal,
                and_(
                    Deal.stage_id == PipelineStage.id,
                    Deal.deleted_at.is_(None),
                    *( [Deal.owner_id.in_(owner_ids)] if owner_ids is not None else [] ),
                ),
                isouter=True,
            )
            .where(PipelineStage.organization_id == organization_id)
            .group_by(PipelineStage.id, PipelineStage.name, PipelineStage.position)
            .order_by(PipelineStage.position)
        )
        return [
            (row[0], row[1], int(row[2] or 0), Decimal(row[3] or 0))
            for row in (await self.session.execute(query)).all()
        ]

    async def stage_velocity(
        self, organization_id: UUID, start: datetime, end: datetime
    ) -> list[tuple[UUID, str, Decimal, int]]:
        """`(stage_id, stage_name, mean_days_in_stage, transitions)`.

        Reads `deal_stage_history.duration_in_stage`, which the stage transition
        wrote at the time — a column read rather than a window function over
        every transition the workspace has ever recorded. This is what that
        column was added for in Phase 2.6.

        Grouped on the stage being *left*, since "how long do deals sit in
        Qualification" is a question about the stage they were in.
        """
        query = (
            select(
                PipelineStage.id,
                PipelineStage.name,
                func.avg(
                    func.extract("epoch", DealStageHistory.duration_in_stage) / 86400.0
                ),
                func.count(),
            )
            .select_from(DealStageHistory)
            .join(PipelineStage, PipelineStage.id == DealStageHistory.from_stage_id)
            .where(DealStageHistory.organization_id == organization_id)
            .where(DealStageHistory.duration_in_stage.is_not(None))
            .where(and_(DealStageHistory.changed_at >= start, DealStageHistory.changed_at < end))
            .group_by(PipelineStage.id, PipelineStage.name, PipelineStage.position)
            .order_by(PipelineStage.position)
        )
        return [
            (row[0], row[1], Decimal(str(round(float(row[2] or 0), 2))), int(row[3] or 0))
            for row in (await self.session.execute(query)).all()
        ]

    async def loss_reasons(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime, limit: int = 10,
    ) -> list[tuple[str, int, Decimal]]:
        """`(reason, count, value)` for deals lost in the period.

        The reason is required by the service when a deal moves to a losing
        stage, which is what makes this query worth running at all — a lost
        deal with no reason is the single most useless row in a CRM.
        """
        first_day, last_day = _date_bounds(start, end)
        query = (
            select(
                func.coalesce(Deal.lost_reason, "Not recorded"),
                func.count(),
                func.coalesce(func.sum(Deal.value), 0),
            )
            .select_from(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(PipelineStage.is_lost)
            .where(Deal.actual_close_date.is_not(None))
            .where(Deal.actual_close_date >= first_day)
            .where(Deal.actual_close_date <= last_day)
            # Grouped by the bare column, not the COALESCE. SQLAlchemy renders
            # the literal as a fresh bind parameter each time it is compiled, so
            # grouping by the expression produces two different placeholders and
            # Postgres rejects the statement. Every column inside the projected
            # COALESCE appears here, which is what the grouping rule requires.
            .group_by(Deal.lost_reason)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [
            (str(row[0]), int(row[1]), Decimal(row[2] or 0))
            for row in (
                await self.session.execute(_scoped(query, Deal.owner_id, owner_ids))
            ).all()
        ]

    async def lead_sources(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime,
    ) -> list[tuple[str, int, int]]:
        """`(source, created, converted)` — the funnel by acquisition channel.

        Both numbers in one pass, because "which source converts best" needs
        the pair and two queries would let them drift across a period boundary.
        """
        query = (
            select(
                Lead.source,
                func.count(),
                func.count().filter(Lead.converted_at.is_not(None)),
            )
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .where(and_(Lead.created_at >= start, Lead.created_at < end))
            .group_by(Lead.source)
            .order_by(func.count().desc())
        )
        return [
            (str(row[0]), int(row[1]), int(row[2]))
            for row in (
                await self.session.execute(_scoped(query, Lead.owner_id, owner_ids))
            ).all()
        ]

    async def agent_leaderboard(
        self, organization_id: UUID, owner_ids: list[UUID] | None,
        start: datetime, end: datetime, limit: int = 20,
    ) -> list[tuple[UUID, str, int, Decimal, int]]:
        """`(user_id, name, deals_won, revenue, leads_converted)` per agent.

        A LEFT JOIN from users, so an agent who closed nothing appears with
        zeros rather than vanishing — a leaderboard that silently omits the
        people who had a bad month is one nobody can manage from.
        """
        first_day, last_day = _date_bounds(start, end)
        won_deals = (
            select(
                Deal.owner_id.label("owner_id"),
                func.count().label("deals_won"),
                func.coalesce(func.sum(Deal.value), 0).label("revenue"),
            )
            .select_from(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(PipelineStage.is_won)
            .where(Deal.actual_close_date.is_not(None))
            .where(Deal.actual_close_date >= first_day)
            .where(Deal.actual_close_date <= last_day)
            .group_by(Deal.owner_id)
            .subquery()
        )
        converted = (
            select(
                Lead.owner_id.label("owner_id"),
                func.count().label("converted"),
            )
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .where(Lead.converted_at.is_not(None))
            .where(and_(Lead.converted_at >= start, Lead.converted_at < end))
            .group_by(Lead.owner_id)
            .subquery()
        )

        query = (
            select(
                User.id,
                User.full_name,
                func.coalesce(won_deals.c.deals_won, 0),
                func.coalesce(won_deals.c.revenue, 0),
                func.coalesce(converted.c.converted, 0),
            )
            .select_from(User)
            .join(won_deals, won_deals.c.owner_id == User.id, isouter=True)
            .join(converted, converted.c.owner_id == User.id, isouter=True)
            .where(User.organization_id == organization_id)
            .where(User.deleted_at.is_(None))
            .where(User.status == "active")
            .order_by(func.coalesce(won_deals.c.revenue, 0).desc())
            .limit(limit)
        )
        if owner_ids is not None:
            query = query.where(User.id.in_(owner_ids))

        return [
            (row[0], str(row[1]), int(row[2]), Decimal(row[3] or 0), int(row[4]))
            for row in (await self.session.execute(query)).all()
        ]

    # --------------------------------------------------------- snapshots

    async def upsert_snapshot(
        self,
        organization_id: UUID,
        owner_id: UUID | None,
        snapshot_date: date,
        metric_key: str,
        value: Decimal,
    ) -> None:
        """Write one reading, replacing any existing one for the same grain.

        Upsert rather than insert because the writer is a retried job: a second
        run must correct the row, not duplicate it or fail on the constraint.
        """
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(MetricSnapshot).values(
            organization_id=organization_id,
            owner_id=owner_id,
            snapshot_date=snapshot_date,
            metric_key=metric_key,
            value=value,
        )
        # `owner_id` is nullable and NULLs never collide, so the unique
        # constraint cannot serve as the conflict target for unowned rows.
        # Postgres 15+ resolves this with NULLS NOT DISTINCT, which the
        # migration declares on the constraint.
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_metric_snapshots_grain",
                set_={"value": statement.excluded.value},
            )
        )

    async def series(
        self,
        organization_id: UUID,
        metric_key: str,
        owner_ids: list[UUID] | None,
        start: date,
        end: date,
        *,
        summable: bool,
    ) -> list[tuple[date, Decimal]]:
        """A metric's daily readings across a range, rolled up over owners.

        `summable` decides how several owners' rows combine on one day: a flow
        adds (three agents booked £30k between them), a level takes the latest
        reading per owner and adds those (open pipeline is the sum of each
        agent's open pipeline). Averaging a level across owners would answer a
        question nobody asked.
        """
        aggregate = func.sum(MetricSnapshot.value)
        query = (
            select(MetricSnapshot.snapshot_date, aggregate)
            .where(MetricSnapshot.organization_id == organization_id)
            .where(MetricSnapshot.metric_key == metric_key)
            .where(MetricSnapshot.snapshot_date >= start)
            .where(MetricSnapshot.snapshot_date < end)
            .group_by(MetricSnapshot.snapshot_date)
            .order_by(MetricSnapshot.snapshot_date)
        )
        if owner_ids is not None:
            query = query.where(MetricSnapshot.owner_id.in_(owner_ids))

        return [
            (row[0], Decimal(row[1] or 0))
            for row in (await self.session.execute(query)).all()
        ]

    async def active_owner_ids(self, organization_id: UUID) -> list[UUID]:
        """Everyone a snapshot should be written for."""
        query = (
            select(User.id)
            .where(User.organization_id == organization_id)
            .where(User.deleted_at.is_(None))
            .where(User.status == "active")
        )
        return list((await self.session.execute(query)).scalars().all())
