"""Data access for stored property scores."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property_score import PropertyScore


class PropertyScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_property(
        self, property_id: UUID, organization_id: UUID
    ) -> PropertyScore | None:
        query = (
            select(PropertyScore)
            .where(PropertyScore.property_id == property_id)
            .where(PropertyScore.organization_id == organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        organization_id: UUID,
        property_id: UUID,
        quality: int,
        grade: str,
        completeness: int,
        breakdown: dict,  # type: ignore[type-arg]
        scorer: str,
    ) -> None:
        """Replace this listing's score. `ON CONFLICT (property_id)` — the row
        holds the current read, safe to overwrite because it reconstructs from
        breakdown."""
        from sqlalchemy import func as sa_func

        statement = insert(PropertyScore).values(
            organization_id=organization_id,
            property_id=property_id,
            quality=quality,
            grade=grade,
            completeness=completeness,
            breakdown=breakdown,
            scorer=scorer,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_property_scores_property",
            set_={
                "quality": statement.excluded.quality,
                "grade": statement.excluded.grade,
                "completeness": statement.excluded.completeness,
                "breakdown": statement.excluded.breakdown,
                "scorer": statement.excluded.scorer,
                "computed_at": sa_func.now(),
            },
        )
        await self.session.execute(statement)

    async def needs_attention(
        self,
        organization_id: UUID,
        *,
        agent_ids: Sequence[UUID] | None,
        limit: int = 20,
    ) -> list[PropertyScore]:
        """The lowest-quality listings still on the market — the ones needing work.

        Joined to `properties` under the caller's `properties.view` scope, so a
        stored score never surfaces a listing the caller could not open. Active
        and pending only: a sold or off-market listing is not worth improving.

        `agent_ids is None` means ALL scope — the common case for properties,
        since listings are shared inventory — so the predicate disappears.
        """
        from app.models.property import Property

        query = (
            select(PropertyScore)
            .join(Property, Property.id == PropertyScore.property_id)
            .where(PropertyScore.organization_id == organization_id)
            .where(Property.deleted_at.is_(None))
            .where(Property.status.in_(("active", "pending")))
            .order_by(PropertyScore.quality.asc(), PropertyScore.computed_at.desc())
            .limit(limit)
        )
        if agent_ids is not None:
            query = query.where(Property.listing_agent_id.in_(agent_ids))
        return list((await self.session.execute(query)).scalars().all())


__all__ = ["PropertyScoreRepository"]
