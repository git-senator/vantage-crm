"""Notification data access.

Every read here takes `user_id` and filters on it. That is not a convenience
parameter — it is the visibility boundary. A notification belongs to its
recipient and to nobody else, including their manager and including an admin at
ALL scope: "who was told what" is not an administrative view of the CRM, it is
someone's inbox.

There is no `owner_ids` predicate for the same reason. Widening a scope must not
widen this.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Select, func, select, update
from sqlalchemy.orm import joinedload

from app.models.notification import Notification, NotificationPreference
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class NotificationRepository(BaseRepository[Notification]):
    model = Notification

    def _base(
        self, organization_id: UUID, user_id: UUID
    ) -> Select[tuple[Notification]]:
        return (
            select(Notification)
            .where(Notification.organization_id == organization_id)
            .where(Notification.user_id == user_id)
            .options(joinedload(Notification.actor))
            .execution_options(populate_existing=True)
        )

    async def list_page(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        unread_only: bool = False,
        category: str | None = None,
        limit: int = 30,
        cursor: Cursor | None = None,
    ) -> tuple[list[Notification], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._base(organization_id, user_id)

        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        if category:
            query = query.where(Notification.category == category)
        if cursor is not None:
            query = query.where(
                func.row(Notification.created_at, Notification.id)
                < func.row(cursor.created_at, cursor.id)
            )

        query = query.order_by(
            Notification.created_at.desc(), Notification.id.desc()
        ).limit(limit + 1)
        rows = list((await self.session.execute(query)).unique().scalars().all())
        return rows[:limit], len(rows) > limit

    async def get_for_user(
        self, notification_id: UUID, organization_id: UUID, user_id: UUID
    ) -> Notification | None:
        """Deliberately not an override of `BaseRepository.get`.

        The base signature is (id, organization_id), and a notification needs a
        third predicate that is not optional. Widening the base method would
        make `user_id` look like a filter someone could omit; a differently
        named method makes forgetting it impossible.
        """
        query = self._base(organization_id, user_id).where(
            Notification.id == notification_id
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def unread_count(self, organization_id: UUID, user_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.organization_id == organization_id)
            .where(Notification.user_id == user_id)
            .where(Notification.read_at.is_(None))
        )
        return int((await self.session.execute(query)).scalar() or 0)

    async def mark_all_read(
        self, organization_id: UUID, user_id: UUID, *, at: datetime
    ) -> int:
        """Bulk update rather than a read-then-write loop.

        A user with two thousand unread notifications should cost one statement,
        and the `read_at IS NULL` predicate keeps it from rewriting rows that are
        already read — which would also destroy the original read timestamps.
        """
        result = await self.session.execute(
            update(Notification)
            .where(Notification.organization_id == organization_id)
            .where(Notification.user_id == user_id)
            .where(Notification.read_at.is_(None))
            .values(read_at=at)
        )
        return int(cast("CursorResult[Any]", result).rowcount or 0)


class NotificationPreferenceRepository(BaseRepository[NotificationPreference]):
    model = NotificationPreference

    async def list_for_user(
        self, organization_id: UUID, user_id: UUID
    ) -> list[NotificationPreference]:
        query = (
            select(NotificationPreference)
            .where(NotificationPreference.organization_id == organization_id)
            .where(NotificationPreference.user_id == user_id)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def get_for_category(
        self, organization_id: UUID, user_id: UUID, category: str
    ) -> NotificationPreference | None:
        query = (
            select(NotificationPreference)
            .where(NotificationPreference.organization_id == organization_id)
            .where(NotificationPreference.user_id == user_id)
            .where(NotificationPreference.category == category)
        )
        return (await self.session.execute(query)).scalar_one_or_none()
