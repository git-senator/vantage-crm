"""Dashboard aggregate queries.

The one place the dashboard's counts and sums become SQL. Each method takes the
scope predicate (`owner_ids` / `assignee_ids`, `None` meaning ALL) already
resolved by the service, and applies it exactly as the entity's own repository
does — so a dashboard total is the same population the list endpoint would show,
never wider.

Every query is a single scan with a `WHERE organization_id = …` leading the
predicate, which the tenant-first composite indexes serve. Counts and sums stay
in the database; nothing is loaded into Python to be tallied.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.deal import Deal
from app.models.lead import Lead
from app.models.pipeline import PipelineStage
from app.models.property import Property
from app.models.task import Task


def _scoped(query, column, ids: list[UUID] | None):  # type: ignore[no-untyped-def]
    """Apply an owner/assignee IN predicate, or nothing for ALL scope."""
    if ids is not None:
        return query.where(column.in_(ids))
    return query


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lead_counts(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> tuple[int, int]:
        """(open, total) leads in scope."""
        base = (
            select(
                func.count().filter(Lead.status == "open"),
                func.count(),
            )
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
        )
        row = (await self.session.execute(_scoped(base, Lead.owner_id, owner_ids))).one()
        return int(row[0] or 0), int(row[1] or 0)

    async def client_count(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> int:
        base = (
            select(func.count())
            .select_from(Client)
            .where(Client.organization_id == organization_id)
            .where(Client.deleted_at.is_(None))
        )
        return int(
            (await self.session.execute(_scoped(base, Client.owner_id, owner_ids))).scalar()
            or 0
        )

    async def property_counts(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> tuple[int, int]:
        """(active, total) listings in scope."""
        base = (
            select(
                func.count().filter(Property.status == "active"),
                func.count(),
            )
            .select_from(Property)
            .where(Property.organization_id == organization_id)
            .where(Property.deleted_at.is_(None))
        )
        row = (
            await self.session.execute(
                _scoped(base, Property.listing_agent_id, owner_ids)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)

    async def deal_stats(
        self,
        organization_id: UUID,
        owner_ids: list[UUID] | None,
        *,
        month_start: datetime,
    ) -> dict[str, object]:
        """Open pipeline, weighted forecast, and this-month wins, in scope.

        Joined to `pipeline_stages` because a deal's open/won/lost state is a
        property of the stage it sits in, never a column on the deal — the same
        derivation `Deal.status` makes, expressed as SQL.
        """
        is_open = ~PipelineStage.is_won & ~PipelineStage.is_lost
        weighted = func.coalesce(Deal.value, Decimal(0)) * Deal.probability / Decimal(100)

        query = (
            select(
                func.count().filter(is_open),
                func.coalesce(func.sum(Deal.value).filter(is_open), Decimal(0)),
                func.coalesce(func.sum(weighted).filter(is_open), Decimal(0)),
                func.count().filter(
                    PipelineStage.is_won & (Deal.actual_close_date >= month_start.date())
                ),
                func.coalesce(
                    func.sum(Deal.value).filter(
                        PipelineStage.is_won
                        & (Deal.actual_close_date >= month_start.date())
                    ),
                    Decimal(0),
                ),
            )
            .select_from(Deal)
            .join(PipelineStage, Deal.stage_id == PipelineStage.id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
        )
        row = (await self.session.execute(_scoped(query, Deal.owner_id, owner_ids))).one()
        return {
            "open_count": int(row[0] or 0),
            "open_value": Decimal(row[1] or 0),
            "weighted_value": Decimal(row[2] or 0),
            "won_this_month_count": int(row[3] or 0),
            "won_this_month_value": Decimal(row[4] or 0),
        }

    async def task_counts(
        self,
        organization_id: UUID,
        assignee_ids: list[UUID] | None,
        *,
        now: datetime,
    ) -> tuple[int, int]:
        """(open, overdue) tasks in scope. Overdue is a predicate, not a column."""
        not_done = Task.status != "done"
        overdue = case((not_done & (Task.due_at < now), 1))
        base = (
            select(
                func.count().filter(not_done),
                func.count(overdue),
            )
            .select_from(Task)
            .where(Task.organization_id == organization_id)
            .where(Task.deleted_at.is_(None))
        )
        row = (
            await self.session.execute(_scoped(base, Task.assignee_id, assignee_ids))
        ).one()
        return int(row[0] or 0), int(row[1] or 0)
