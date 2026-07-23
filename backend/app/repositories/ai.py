"""Data access for the AI job ledger."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AiJob


class AiJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def spend_since(self, organization_id: UUID, since: datetime) -> Decimal:
        """This org's total AI cost from `since` to now.

        The number the cost ceiling is compared against, so it runs before every
        dispatch. Only `succeeded` and `failed` jobs count — a `refused` job
        spent nothing, and counting refusals toward the ceiling would let a
        wall of refusals lock the tenant out on top of the ceiling that already
        did.
        """
        query = (
            select(func.coalesce(func.sum(AiJob.cost_usd), 0))
            .where(AiJob.organization_id == organization_id)
            .where(AiJob.created_at >= since)
            .where(AiJob.status != "refused")
        )
        result = await self.session.execute(query)
        return Decimal(result.scalar() or 0)

    async def usage_by_feature(
        self, organization_id: UUID, since: datetime
    ) -> list[tuple[str, int, Decimal]]:
        """`(feature, calls, cost)` per feature over the window, dearest first.

        For the admin usage view and for a tenant to see where its AI budget is
        going — a summary answers "what is this costing us" faster than a list of
        every call.
        """
        query = (
            select(
                AiJob.feature,
                func.count(),
                func.coalesce(func.sum(AiJob.cost_usd), 0),
            )
            .where(AiJob.organization_id == organization_id)
            .where(AiJob.created_at >= since)
            .where(AiJob.status != "refused")
            .group_by(AiJob.feature)
            .order_by(func.coalesce(func.sum(AiJob.cost_usd), 0).desc())
        )
        rows = (await self.session.execute(query)).all()
        return [(str(feature), int(calls), Decimal(cost)) for feature, calls, cost in rows]

    async def recent(
        self, organization_id: UUID, *, limit: int = 50
    ) -> list[AiJob]:
        query = (
            select(AiJob)
            .where(AiJob.organization_id == organization_id)
            .order_by(AiJob.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())


__all__ = ["AiJobRepository"]
