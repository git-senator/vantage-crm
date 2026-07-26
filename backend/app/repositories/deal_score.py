"""Data access for stored deal scores."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal_score import DealScore


class DealScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_deal(
        self, deal_id: UUID, organization_id: UUID
    ) -> DealScore | None:
        query = (
            select(DealScore)
            .where(DealScore.deal_id == deal_id)
            .where(DealScore.organization_id == organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        organization_id: UUID,
        deal_id: UUID,
        health: int,
        status: str,
        win_probability: int,
        forecast_value: Decimal | None,
        is_stalled: bool,
        breakdown: dict,  # type: ignore[type-arg]
        scorer: str,
    ) -> None:
        """Replace this deal's score. `ON CONFLICT (deal_id)` — the row holds the
        current read, safe to overwrite because it reconstructs from breakdown."""
        from sqlalchemy import func as sa_func

        statement = insert(DealScore).values(
            organization_id=organization_id,
            deal_id=deal_id,
            health=health,
            status=status,
            win_probability=win_probability,
            forecast_value=forecast_value,
            is_stalled=is_stalled,
            breakdown=breakdown,
            scorer=scorer,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_deal_scores_deal",
            set_={
                "health": statement.excluded.health,
                "status": statement.excluded.status,
                "win_probability": statement.excluded.win_probability,
                "forecast_value": statement.excluded.forecast_value,
                "is_stalled": statement.excluded.is_stalled,
                "breakdown": statement.excluded.breakdown,
                "scorer": statement.excluded.scorer,
                "computed_at": sa_func.now(),
            },
        )
        await self.session.execute(statement)

    async def at_risk(
        self,
        organization_id: UUID,
        *,
        owner_ids: Sequence[UUID] | None,
        limit: int = 20,
    ) -> list[DealScore]:
        """The lowest-health open deals — the ones that need attention.

        Joined to `deals` under the caller's `deals.view` scope, so a stored
        score never surfaces a deal the caller could not open. Open deals only:
        a closed deal is not at risk, its outcome is settled.
        """
        from app.models.deal import Deal
        from app.models.pipeline import PipelineStage

        query = (
            select(DealScore)
            .join(Deal, Deal.id == DealScore.deal_id)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(DealScore.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
            .where(~PipelineStage.is_won)
            .where(~PipelineStage.is_lost)
            .order_by(DealScore.health.asc(), DealScore.computed_at.desc())
            .limit(limit)
        )
        if owner_ids is not None:
            query = query.where(Deal.owner_id.in_(owner_ids))
        return list((await self.session.execute(query)).scalars().all())


__all__ = ["DealScoreRepository"]
