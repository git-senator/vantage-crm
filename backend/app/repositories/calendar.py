"""Calendar data access.

Range queries are the whole workload here, and the predicate that matters is the
**overlap** test, not containment:

    starts_at < window_end AND ends_at > window_start

An event that begins before the window and ends inside it is in the window. A
naive `starts_at BETWEEN ...` misses exactly the long events a user most needs
to see — the closing that started at 9 when they are looking at the afternoon.
Both bounds are strict, so an event ending precisely when the window opens is
not counted twice at a month boundary.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, and_
from sqlalchemy.orm import joinedload, selectinload

from app.models.calendar import CalendarEvent
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE


class CalendarEventRepository(BaseRepository[CalendarEvent]):
    model = CalendarEvent

    def _base(self, organization_id: UUID) -> Select[tuple[CalendarEvent]]:
        return (
            self.scoped_to_organization(self._base_query(), organization_id)
            .options(
                joinedload(CalendarEvent.owner),
                selectinload(CalendarEvent.attendees),
            )
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _scoped(
        query: Select[tuple[CalendarEvent]], owner_ids: list[UUID] | None
    ) -> Select[tuple[CalendarEvent]]:
        if owner_ids is None:
            return query
        return query.where(CalendarEvent.owner_id.in_(owner_ids))

    async def list_window(
        self,
        organization_id: UUID,
        *,
        start: datetime,
        end: datetime,
        owner_ids: list[UUID] | None,
        owner_filter: UUID | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        event_type: str | None = None,
        include_cancelled: bool = False,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[CalendarEvent]:
        """Everything overlapping a window, soonest first.

        Not paginated: a calendar view is bounded by its window, and a month of
        one workspace's events is a few hundred rows. `limit` is a safety rail
        against a caller asking for a decade, not a pagination mechanism.
        """
        query = self._scoped(self._base(organization_id), owner_ids).where(
            and_(CalendarEvent.starts_at < end, CalendarEvent.ends_at > start)
        )

        if owner_filter is not None:
            query = query.where(CalendarEvent.owner_id == owner_filter)
        if entity_type is not None:
            query = query.where(CalendarEvent.entity_type == entity_type)
        if entity_id is not None:
            query = query.where(CalendarEvent.entity_id == entity_id)
        if event_type is not None:
            query = query.where(CalendarEvent.event_type == event_type)
        if not include_cancelled:
            # A cancelled event stays in the table — "what was I meant to be
            # doing" is a real question — but it is not on the calendar.
            query = query.where(CalendarEvent.status != "cancelled")

        query = query.order_by(
            CalendarEvent.starts_at.asc(), CalendarEvent.id.asc()
        ).limit(min(limit, MAX_PAGE_SIZE))
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get_visible(
        self, event_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> CalendarEvent | None:
        query = self._scoped(
            self._base(organization_id).where(CalendarEvent.id == event_id),
            owner_ids,
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def find_conflicts(
        self,
        organization_id: UUID,
        *,
        owner_id: UUID,
        start: datetime,
        end: datetime,
        exclude_event_id: UUID | None = None,
    ) -> list[CalendarEvent]:
        """Confirmed events on one person's calendar that overlap this range.

        Tentative events do not conflict: a pencilled-in showing is exactly the
        thing you expect to be double-booked against while it is being
        arranged. Cancelled ones obviously do not either.

        `exclude_event_id` keeps an event from conflicting with itself when it
        is being edited — without it, every update would report one conflict.
        """
        query = (
            self._base_query()
            .where(CalendarEvent.organization_id == organization_id)
            .where(CalendarEvent.owner_id == owner_id)
            .where(CalendarEvent.status == "confirmed")
            .where(and_(CalendarEvent.starts_at < end, CalendarEvent.ends_at > start))
            .order_by(CalendarEvent.starts_at.asc())
            .limit(10)
        )
        if exclude_event_id is not None:
            query = query.where(CalendarEvent.id != exclude_event_id)
        return list((await self.session.execute(query)).unique().scalars().all())

    async def list_due_reminders(
        self, *, now: datetime, horizon: datetime, limit: int = 200
    ) -> list[CalendarEvent]:
        """Events whose reminder is due, for one tenant.

        The window is `now <= starts_at <= horizon` where the horizon accounts
        for the longest lead time being swept. Filtering on `reminded_at IS
        NULL` is what makes a re-run safe.
        """
        query = (
            self._base_query()
            .options(joinedload(CalendarEvent.owner))
            .where(CalendarEvent.reminder_minutes.is_not(None))
            .where(CalendarEvent.reminded_at.is_(None))
            .where(CalendarEvent.status != "cancelled")
            .where(CalendarEvent.starts_at >= now)
            .where(CalendarEvent.starts_at <= horizon)
            .order_by(CalendarEvent.starts_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())
