"""Activity timeline writes and reads.

Deliberately thin. An activity is a fact about the business, not a decision, so
there is no authorization logic here — the caller has already established that
it may act on the entity, and writing the timeline entry is part of that same
transaction.

Reads are scoped by the caller passing an entity the caller can already see.
This service never widens access: it takes an `entity_id` that the calling
service resolved under RBAC, so an activity list cannot become a way to read a
record the caller could not fetch directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity


class ActivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID | None,
        entity_type: str,
        entity_id: UUID,
        type: str,
        subject: str,
        body: str | None = None,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Activity:
        """Append one timeline entry.

        Flushed, not committed: the caller owns the transaction, so an activity
        cannot survive a rolled-back action that it claims to describe.
        """
        activity = Activity(
            organization_id=organization_id,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            type=type,
            subject=subject,
            body=body,
            metadata_=metadata or {},
        )
        if occurred_at is not None:
            activity.occurred_at = occurred_at

        self.session.add(activity)
        await self.session.flush()
        return activity

    async def list_for_entity(
        self,
        *,
        organization_id: UUID,
        entity_type: str,
        entity_id: UUID,
        limit: int = 50,
    ) -> list[Activity]:
        """One entity's timeline, newest first.

        Serves `ix_activities_entity` directly — the index is
        (organization_id, entity_type, entity_id, occurred_at DESC), which is
        this query exactly.
        """
        query = (
            select(Activity)
            .where(Activity.organization_id == organization_id)
            .where(Activity.entity_type == entity_type)
            .where(Activity.entity_id == entity_id)
            .order_by(Activity.occurred_at.desc(), Activity.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())
