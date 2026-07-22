"""Unified timeline — activities and notes, merged on one time axis.

Two sources, by decision: activities and notes. Task events arrive through the
activities they generate; the audit log stays out. See app/schemas/timeline.py.

Authorization reuses the child services' own rules rather than reinventing them:

  * the **per-entity** timeline checks parent readability once (via
    `EntityAccess`), then reads both sources through their *unchecked* paths —
    the parent has already been resolved, so re-checking per child would be
    redundant work;
  * the **feed** includes only the sources the caller may view: activities if
    they hold `activities.view`, notes if they hold `notes.view`, each within
    that permission's scope. A caller with neither gets an empty feed, not a
    403 — the timeline is a composite view, not a single resource.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.note import Note
from app.schemas.activity import ActivityFilters
from app.schemas.note import NoteFilters
from app.schemas.timeline import TimelineActor, TimelineItem
from app.services.activity import ActivityService, is_system_activity
from app.services.entity_access import EntityAccess
from app.services.note import NoteService
from app.services.rbac import AuthorizationContext, RbacService


def _actor(user) -> TimelineActor | None:  # type: ignore[no-untyped-def]
    if user is None:
        return None
    return TimelineActor(
        id=user.id,
        full_name=user.full_name,
        initials=user.initials,
        avatar_hue=user.avatar_hue,
    )


def _activity_item(activity: Activity) -> TimelineItem:
    return TimelineItem(
        kind="activity",
        id=activity.id,
        entity_type=activity.entity_type,
        entity_id=activity.entity_id,
        timestamp=activity.occurred_at,
        type=activity.type,
        title=activity.subject,
        body=activity.body,
        actor=_actor(activity.actor),
        is_system=is_system_activity(activity),
        metadata=activity.metadata_ or {},
    )


def _note_item(note: Note) -> TimelineItem:
    return TimelineItem(
        kind="note",
        id=note.id,
        entity_type=note.entity_type,
        entity_id=note.entity_id,
        timestamp=note.created_at,
        type="note",
        title=note.title,
        body=note.body,
        actor=_actor(note.author),
        is_pinned=note.is_pinned,
        metadata={"content_format": note.content_format},
    )


class TimelineService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.access = EntityAccess(session, auth)
        self.rbac = RbacService(session)
        self.activities = ActivityService(session, auth)
        self.notes = NoteService(session, auth)

    async def for_entity(
        self, *, entity_type: str, entity_id: UUID, limit: int = 50
    ) -> list[TimelineItem]:
        """One record's merged timeline, newest first.

        Parent readability is proven once here; the two source reads then use
        the unchecked paths, since re-resolving the same parent per child buys
        nothing.
        """
        await self.access.assert_readable(entity_type, entity_id)

        activities = await self.activities.list_for_entity_unchecked(
            organization_id=self.auth.organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )
        notes = await self.notes.list_for_entity_unchecked(
            organization_id=self.auth.organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )

        items = [_activity_item(a) for a in activities] + [
            _note_item(n) for n in notes
        ]
        items.sort(key=lambda item: item.timestamp, reverse=True)
        return items[:limit]

    async def feed(self, *, limit: int = 50) -> list[TimelineItem]:
        """The cross-entity feed: recent activity and notes within scope.

        Each source is included only if the caller can view it, and only within
        that permission's scope. Merged newest-first and capped — this backs the
        dashboard's "recent activity" panel, not deep pagination.
        """
        items: list[TimelineItem] = []

        if self.auth.scope_for("activities.view") is not None:
            activities, _ = await self.activities.list_feed(
                filters=ActivityFilters(), limit=limit
            )
            items.extend(_activity_item(a) for a in activities)

        if self.auth.scope_for("notes.view") is not None:
            notes, _ = await self.notes.list_feed(filters=NoteFilters(), limit=limit)
            items.extend(_note_item(n) for n in notes)

        items.sort(key=lambda item: item.timestamp, reverse=True)
        return items[:limit]
