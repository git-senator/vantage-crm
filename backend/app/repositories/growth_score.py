"""Data access for the stored growth score."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.growth_score import GrowthScore


class GrowthScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_org(self, organization_id: UUID) -> GrowthScore | None:
        query = select(GrowthScore).where(
            GrowthScore.organization_id == organization_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        organization_id: UUID,
        score: int,
        band: str,
        period_start: datetime,
        period_end: datetime,
        breakdown: dict,  # type: ignore[type-arg]
        scorer: str,
    ) -> None:
        """Replace this org's canonical growth read. `ON CONFLICT (organization_id)`
        — one row per tenant, safe to overwrite because it reconstructs from
        breakdown."""
        from sqlalchemy import func as sa_func

        statement = insert(GrowthScore).values(
            organization_id=organization_id,
            score=score,
            band=band,
            period_start=period_start,
            period_end=period_end,
            breakdown=breakdown,
            scorer=scorer,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_growth_scores_org",
            set_={
                "score": statement.excluded.score,
                "band": statement.excluded.band,
                "period_start": statement.excluded.period_start,
                "period_end": statement.excluded.period_end,
                "breakdown": statement.excluded.breakdown,
                "scorer": statement.excluded.scorer,
                "computed_at": sa_func.now(),
            },
        )
        await self.session.execute(statement)


__all__ = ["GrowthScoreRepository"]
