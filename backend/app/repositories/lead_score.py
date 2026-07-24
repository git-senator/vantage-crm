"""Data access for stored lead scores."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead_score import LeadScore


class LeadScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_lead(
        self, lead_id: UUID, organization_id: UUID
    ) -> LeadScore | None:
        query = (
            select(LeadScore)
            .where(LeadScore.lead_id == lead_id)
            .where(LeadScore.organization_id == organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        organization_id: UUID,
        lead_id: UUID,
        score: int,
        temperature: str,
        qualification: str,
        priority: str,
        buying_intent: str,
        breakdown: dict,  # type: ignore[type-arg]
        scorer: str,
    ) -> None:
        """Replace this lead's score with a fresh one.

        `ON CONFLICT (lead_id)` so a rescore overwrites rather than accumulating
        — the table holds the current read, and the score being reproducible
        from `breakdown` is what makes overwriting safe: no history is lost that
        cannot be recomputed.
        """
        from sqlalchemy import func as sa_func

        statement = insert(LeadScore).values(
            organization_id=organization_id,
            lead_id=lead_id,
            score=score,
            temperature=temperature,
            qualification=qualification,
            priority=priority,
            buying_intent=buying_intent,
            breakdown=breakdown,
            scorer=scorer,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_lead_scores_lead",
            set_={
                "score": statement.excluded.score,
                "temperature": statement.excluded.temperature,
                "qualification": statement.excluded.qualification,
                "priority": statement.excluded.priority,
                "buying_intent": statement.excluded.buying_intent,
                "breakdown": statement.excluded.breakdown,
                "scorer": statement.excluded.scorer,
                "computed_at": sa_func.now(),
            },
        )
        await self.session.execute(statement)

    async def ranked(
        self,
        organization_id: UUID,
        *,
        owner_ids: Sequence[UUID] | None,
        limit: int = 20,
    ) -> list[LeadScore]:
        """The highest-scoring leads, newest score first within a tie.

        `owner_ids` is the caller's resolved lead scope: `None` means ALL (no
        owner predicate), a list restricts to those owners. Joined to `leads` so
        the ranking obeys the same visibility the lead list does — a stored
        score never becomes a way to see a lead the caller could not.
        """
        from app.models.lead import Lead

        query = (
            select(LeadScore)
            .join(Lead, Lead.id == LeadScore.lead_id)
            .where(LeadScore.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .where(Lead.status == "open")
            .order_by(LeadScore.score.desc(), LeadScore.computed_at.desc())
            .limit(limit)
        )
        if owner_ids is not None:
            query = query.where(Lead.owner_id.in_(owner_ids))
        return list((await self.session.execute(query)).scalars().all())


__all__ = ["LeadScoreRepository"]
