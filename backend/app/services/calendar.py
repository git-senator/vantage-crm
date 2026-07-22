"""Calendar business logic.

Follows the lead/deal pattern — `owner_id` is the scope anchor, so an agent sees
their own calendar, a manager the team's, an admin everyone's. Three things are
specific to scheduling:

**Conflicts are reported, never enforced.** `create` and `update` return the
overlapping events alongside the saved one. Refusing the write would be the
system claiming to know better than the person holding the calendar; staying
silent would let a double-booked showing reach a client. Reporting is the only
option that respects both.

**Booking for someone else is an assignment.** Putting an event on a colleague's
calendar is the same kind of act as reassigning their lead, so it goes through
the same `contacts.assign`-shaped check rather than being free.

**A scheduled event lands on the record's timeline.** A showing booked against a
property appears there, exactly as a task or a note does — the unified timeline
is only unified if everything that happens to a record reaches it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.calendar import CalendarEvent, EventAttendee
from app.models.user import User
from app.repositories.calendar import CalendarEventRepository
from app.repositories.user import UserRepository
from app.schemas.calendar import (
    AttendeeInput,
    CalendarEventCreate,
    CalendarEventUpdate,
    ScheduleConflict,
)
from app.services.activity import ActivityService
from app.services.audit import AuditService, build_diff
from app.services.entity_access import EntityAccess
from app.services.notification_center import NotificationCenter
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "calendar_event"

AUDITED_FIELDS = (
    "title",
    "location",
    "event_type",
    "status",
    "starts_at",
    "ends_at",
    "owner_id",
    "entity_type",
    "entity_id",
)


class CalendarService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.events = CalendarEventRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.activities = ActivityService(session)
        self.access = EntityAccess(session, auth)
        self.rbac = RbacService(session)

    # --------------------------------------------------------------- scope

    async def _owner_ids(self, permission: str) -> list[UUID] | None:
        scope = self.auth.require(permission)
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # ---------------------------------------------------------------- read

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        owner_id: UUID | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        event_type: str | None = None,
        include_cancelled: bool = False,
    ) -> list[CalendarEvent]:
        owner_ids = await self._owner_ids("activities.view")
        if entity_type is not None and entity_id is not None:
            await self.access.assert_readable(entity_type, entity_id)
        return await self.events.list_window(
            self.auth.organization_id,
            start=start,
            end=end,
            owner_ids=owner_ids,
            owner_filter=owner_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            include_cancelled=include_cancelled,
        )

    async def get_event(self, event_id: UUID) -> CalendarEvent:
        owner_ids = await self._owner_ids("activities.view")
        event = await self.events.get_visible(
            event_id, self.auth.organization_id, owner_ids
        )
        if event is None:
            raise NotFoundError("Event not found.")
        return event

    # --------------------------------------------------------------- write

    async def create_event(
        self, payload: CalendarEventCreate, actor: User
    ) -> tuple[CalendarEvent, list[ScheduleConflict]]:
        self.auth.require("activities.manage")

        owner_id = payload.owner_id or actor.id
        if owner_id != actor.id:
            await self._assert_can_book_for(owner_id)

        if payload.entity_type is not None and payload.entity_id is not None:
            # You cannot schedule against a record you cannot see.
            await self.access.assert_readable(payload.entity_type, payload.entity_id)

        event = CalendarEvent(
            organization_id=self.auth.organization_id,
            owner_id=owner_id,
            created_by=actor.id,
            **payload.model_dump(exclude={"owner_id", "attendees"}),
        )
        self.session.add(event)
        await self.session.flush()

        await self._replace_attendees(event, payload.attendees)

        conflicts = await self._conflicts_for(event)

        await self._log_on_entity(event, actor, verb="scheduled")
        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=event.id,
            metadata={
                "title": event.title,
                "starts_at": event.starts_at,
                "event_type": event.event_type,
            },
        )
        if owner_id != actor.id:
            await self._notify_owner(event, actor)

        logger.info(
            "calendar_event_created",
            extra={"event_id": str(event.id), "conflicts": len(conflicts)},
        )
        return await self.get_event(event.id), conflicts

    async def update_event(
        self, event_id: UUID, payload: CalendarEventUpdate, actor: User
    ) -> tuple[CalendarEvent, list[ScheduleConflict]]:
        owner_ids = await self._owner_ids("activities.manage")
        event = await self.events.get_visible(
            event_id, self.auth.organization_id, owner_ids
        )
        if event is None:
            raise NotFoundError("Event not found.")

        updates = payload.model_dump(exclude_unset=True)
        attendees = updates.pop("attendees", None)
        if not updates and attendees is None:
            return event, []

        if "owner_id" in updates and updates["owner_id"] != event.owner_id:
            await self._assert_can_book_for(updates["owner_id"])

        merged_start = updates.get("starts_at", event.starts_at)
        merged_end = updates.get("ends_at", event.ends_at)
        if merged_end <= merged_start:
            raise ConflictError("An event must end after it starts.")

        merged_type = updates.get("entity_type", event.entity_type)
        merged_id = updates.get("entity_id", event.entity_id)
        if (merged_type is None) != (merged_id is None):
            raise ConflictError(
                "An event is linked to a record or to nothing, not half of one."
            )
        if merged_type is not None and merged_id is not None:
            await self.access.assert_readable(merged_type, merged_id)

        before = {field: getattr(event, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(event, field, value)

        # A moved event needs reminding about again. Leaving `reminded_at` set
        # would silently drop the reminder for the new time, which is the one
        # the user actually cares about.
        if "starts_at" in updates:
            event.reminded_at = None

        await self.session.flush()

        if attendees is not None:
            await self._replace_attendees(
                event, [AttendeeInput.model_validate(row) for row in attendees]
            )

        conflicts = await self._conflicts_for(event)

        diff = build_diff(before, {f: getattr(event, f) for f in AUDITED_FIELDS})
        if diff:
            await self.audit.record(
                action=AuditAction.RECORD_UPDATED,
                organization_id=self.auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type=ENTITY_TYPE,
                entity_id=event.id,
                metadata=diff,
            )
        return await self.get_event(event.id), conflicts

    async def cancel_event(self, event_id: UUID, actor: User) -> CalendarEvent:
        """Cancel rather than delete.

        "What was I meant to be doing on Tuesday" is a real question, and an
        event that vanishes takes its answer with it. Cancelled events are
        excluded from the calendar view but stay queryable.
        """
        owner_ids = await self._owner_ids("activities.manage")
        event = await self.events.get_visible(
            event_id, self.auth.organization_id, owner_ids
        )
        if event is None:
            raise NotFoundError("Event not found.")
        if event.status == "cancelled":
            return event

        event.status = "cancelled"
        await self.session.flush()

        await self._log_on_entity(event, actor, verb="cancelled")
        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=event.id,
            metadata={"status": {"old": "confirmed", "new": "cancelled"}},
        )
        return event

    async def delete_event(self, event_id: UUID, actor: User) -> None:
        owner_ids = await self._owner_ids("activities.manage")
        event = await self.events.get_visible(
            event_id, self.auth.organization_id, owner_ids
        )
        if event is None:
            raise NotFoundError("Event not found.")

        event.deleted_at = datetime.now(UTC)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=event.id,
            metadata={"title": event.title},
        )

    # ------------------------------------------------------------ internal

    async def _conflicts_for(self, event: CalendarEvent) -> list[ScheduleConflict]:
        if event.status != "confirmed":
            # A tentative event is not claiming the slot, so it neither
            # conflicts nor is conflicted with.
            return []
        overlapping = await self.events.find_conflicts(
            self.auth.organization_id,
            owner_id=event.owner_id,
            start=event.starts_at,
            end=event.ends_at,
            exclude_event_id=event.id,
        )
        return [
            ScheduleConflict(
                event_id=row.id,
                title=row.title,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            )
            for row in overlapping
        ]

    async def _replace_attendees(
        self, event: CalendarEvent, attendees: list[AttendeeInput]
    ) -> None:
        """Replace the attendee set wholesale.

        Existing responses are preserved by identity: somebody who already
        accepted should not be reset to `needs_action` because a colleague was
        added to the same event.

        Reads and writes the rows explicitly rather than through
        `event.attendees`. On a freshly-flushed event the collection is not
        loaded, and touching it would emit a lazy load — which under asyncio
        raises `MissingGreenlet` rather than quietly doing the wrong thing.
        """
        existing = list(
            (
                await self.session.execute(
                    select(EventAttendee).where(EventAttendee.event_id == event.id)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        previous = {(row.user_id, row.email): row.response for row in existing}
        for row in existing:
            await self.session.delete(row)
        await self.session.flush()

        for attendee in attendees:
            email = attendee.email.strip().lower() if attendee.email else None
            if attendee.user_id is not None:
                member = await self.users.get(
                    attendee.user_id, self.auth.organization_id
                )
                if member is None:
                    # An id from another tenant. 404 rather than 403: the
                    # endpoint must not confirm which user ids exist elsewhere.
                    raise NotFoundError("Attendee not found.")
            self.session.add(
                EventAttendee(
                    organization_id=self.auth.organization_id,
                    event_id=event.id,
                    user_id=attendee.user_id,
                    email=email,
                    display_name=attendee.display_name,
                    response=previous.get(
                        (attendee.user_id, email), "needs_action"
                    ),
                )
            )
        await self.session.flush()

    async def _assert_can_book_for(self, owner_id: UUID) -> None:
        """Putting an event on someone else's calendar is an assignment."""
        member = await self.users.get(owner_id, self.auth.organization_id)
        if member is None:
            raise NotFoundError("User not found.")

        scope = self.auth.scope_for("activities.manage")
        if scope is None:  # pragma: no cover — the caller already required it
            raise PermissionDeniedError("This action requires activities.manage.")
        if scope is Scope.OWN:
            raise PermissionDeniedError(
                "You can only schedule events on your own calendar."
            )
        if scope is Scope.TEAM:
            teammates = await self.rbac.team_member_ids(
                self.auth.user_id, self.auth.organization_id
            )
            if owner_id not in teammates:
                raise PermissionDeniedError(
                    "You can only schedule events for your team."
                )

    async def _log_on_entity(
        self, event: CalendarEvent, actor: User, *, verb: str
    ) -> None:
        """Put the event on the linked record's timeline.

        The unified timeline is only unified if everything that happens to a
        record reaches it — a showing booked on a property belongs there next to
        the notes and the tasks.
        """
        if event.entity_type is None or event.entity_id is None:
            return
        await self.activities.record(
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            type="note",
            subject=f"{event.event_type.replace('_', ' ').title()} {verb}: {event.title}",
            body=event.location,
            metadata={"event_id": str(event.id), "source": "calendar"},
        )

    async def _notify_owner(self, event: CalendarEvent, actor: User) -> None:
        await NotificationCenter(self.session).raise_notification(
            organization_id=self.auth.organization_id,
            recipient_id=event.owner_id,
            actor_id=actor.id,
            category="task",
            type="calendar.event_scheduled",
            title=f"{actor.full_name} scheduled an event for you",
            body=f"{event.title} — {event.starts_at:%d %b %Y %H:%M}",
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            metadata={"event_id": str(event.id)},
        )
