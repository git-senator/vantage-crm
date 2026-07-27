"""Integration data access.

Ordinary tenant-scoped reads on top of RLS — no SECURITY DEFINER lookups, because
an integration is never read without a tenant already bound (see the model
module). The one idempotent helper, ``subscribe``, upserts on the
``(connection, event_type)`` uniqueness so setting a subscription twice is a
no-op rather than a duplicate or an error.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from app.models.integration import (
    IntegrationConnection,
    IntegrationSubscription,
    IntegrationSyncRun,
)
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class IntegrationConnectionRepository(BaseRepository[IntegrationConnection]):
    model = IntegrationConnection

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[IntegrationConnection]:
        query = (
            select(IntegrationConnection)
            .where(IntegrationConnection.organization_id == organization_id)
            .order_by(IntegrationConnection.created_at.desc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def get_by_state(
        self, state: str, organization_id: UUID
    ) -> IntegrationConnection | None:
        query = (
            select(IntegrationConnection)
            .where(IntegrationConnection.organization_id == organization_id)
            .where(IntegrationConnection.oauth_state == state)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_pending(
        self, provider: str, organization_id: UUID
    ) -> IntegrationConnection | None:
        """A not-yet-completed install for this provider, so beginning twice
        reuses the same row rather than orphaning the first."""
        query = (
            select(IntegrationConnection)
            .where(IntegrationConnection.organization_id == organization_id)
            .where(IntegrationConnection.provider == provider)
            .where(IntegrationConnection.status == "pending")
            .order_by(IntegrationConnection.created_at.desc())
        )
        return (await self.session.execute(query)).scalars().first()

    async def list_active(
        self, organization_id: UUID
    ) -> Sequence[IntegrationConnection]:
        query = (
            select(IntegrationConnection)
            .where(IntegrationConnection.organization_id == organization_id)
            .where(IntegrationConnection.status == "active")
            .where(IntegrationConnection.disabled_at.is_(None))
        )
        return list((await self.session.execute(query)).scalars().all())

    async def list_due_for_sync(
        self, organization_id: UUID, *, before: datetime
    ) -> Sequence[IntegrationConnection]:
        """Active connections whose last sync is older than `before` (or never
        synced). The periodic sweep's work list."""
        query = (
            select(IntegrationConnection)
            .where(IntegrationConnection.organization_id == organization_id)
            .where(IntegrationConnection.status == "active")
            .where(IntegrationConnection.disabled_at.is_(None))
            .where(
                (IntegrationConnection.last_sync_at.is_(None))
                | (IntegrationConnection.last_sync_at < before)
            )
        )
        return list((await self.session.execute(query)).scalars().all())


class IntegrationSubscriptionRepository(BaseRepository[IntegrationSubscription]):
    model = IntegrationSubscription

    async def list_for_connection(
        self, connection_id: UUID, organization_id: UUID
    ) -> Sequence[IntegrationSubscription]:
        query = (
            select(IntegrationSubscription)
            .where(IntegrationSubscription.organization_id == organization_id)
            .where(IntegrationSubscription.connection_id == connection_id)
            .order_by(IntegrationSubscription.event_type.asc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def active_for_event(
        self, organization_id: UUID, event_type: str
    ) -> Sequence[IntegrationSubscription]:
        """Every active subscription across the tenant's connections that wants
        this event — the fan-out list for an outbox event."""
        query = (
            select(IntegrationSubscription)
            .join(
                IntegrationConnection,
                IntegrationConnection.id == IntegrationSubscription.connection_id,
            )
            .where(IntegrationSubscription.organization_id == organization_id)
            .where(IntegrationSubscription.event_type == event_type)
            .where(IntegrationSubscription.is_active.is_(True))
            .where(IntegrationConnection.status == "active")
            .where(IntegrationConnection.disabled_at.is_(None))
        )
        return list((await self.session.execute(query)).scalars().all())

    async def delete_for_connection(
        self, connection_id: UUID, organization_id: UUID
    ) -> None:
        for row in await self.list_for_connection(connection_id, organization_id):
            await self.session.delete(row)
        await self.session.flush()


class IntegrationSyncRunRepository(BaseRepository[IntegrationSyncRun]):
    model = IntegrationSyncRun

    async def list_for_connection(
        self,
        connection_id: UUID,
        organization_id: UUID,
        *,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[IntegrationSyncRun], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = (
            select(IntegrationSyncRun)
            .where(IntegrationSyncRun.organization_id == organization_id)
            .where(IntegrationSyncRun.connection_id == connection_id)
        )
        if cursor is not None:
            query = query.where(
                func.row(IntegrationSyncRun.created_at, IntegrationSyncRun.id)
                < func.row(cursor.created_at, cursor.id)
            )
        query = query.order_by(
            IntegrationSyncRun.created_at.desc(), IntegrationSyncRun.id.desc()
        ).limit(limit + 1)
        rows = list((await self.session.execute(query)).scalars().all())
        return rows[:limit], len(rows) > limit

    async def list_stale_pending(
        self, organization_id: UUID, *, before: datetime
    ) -> Sequence[IntegrationSyncRun]:
        """Pending runs created before `before` — the ones whose send job the
        fast path may have lost. The sweep re-enqueues them, so a triggered sync
        is at most one sweep late rather than stuck pending forever."""
        query = (
            select(IntegrationSyncRun)
            .where(IntegrationSyncRun.organization_id == organization_id)
            .where(IntegrationSyncRun.status == "pending")
            .where(IntegrationSyncRun.created_at < before)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def count_by_status(
        self, organization_id: UUID, *, connection_id: UUID | None = None
    ) -> dict[str, int]:
        query = (
            select(IntegrationSyncRun.status, func.count())
            .where(IntegrationSyncRun.organization_id == organization_id)
            .group_by(IntegrationSyncRun.status)
        )
        if connection_id is not None:
            query = query.where(IntegrationSyncRun.connection_id == connection_id)
        rows = (await self.session.execute(query)).all()
        return {row[0]: row[1] for row in rows}


__all__ = [
    "IntegrationConnectionRepository",
    "IntegrationSubscriptionRepository",
    "IntegrationSyncRunRepository",
]
