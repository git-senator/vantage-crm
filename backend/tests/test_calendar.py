"""Calendar — windows, conflicts, attendees and reminders.

The two things worth reading first:

`TestWindowQueries` pins down **overlap, not containment**. An event that began
this morning and runs into the afternoon belongs in the afternoon's view, and a
`BETWEEN` on the start time would miss exactly the long events people most need
to see.

`TestConflicts` pins down that a conflict is **reported and not enforced**.
Refusing the write would be the system claiming to know better than the person
holding the calendar; staying silent would let a double-booked showing reach a
client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.activity import Activity
from app.models.calendar import CalendarEvent
from app.models.notification import Notification
from app.schemas.calendar import (
    AttendeeInput,
    CalendarEventCreate,
    CalendarEventUpdate,
)
from app.schemas.lead import LeadCreate
from app.services.calendar import CalendarService
from app.services.lead import LeadService
from app.workers.jobs.calendar import sweep_calendar_reminders
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration

BASE = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)


def _event(**overrides) -> CalendarEventCreate:  # type: ignore[no-untyped-def]
    data = {
        "title": "Showing at 88 Townsend",
        "event_type": "showing",
        "starts_at": BASE,
        "ends_at": BASE + timedelta(hours=1),
        **overrides,
    }
    return CalendarEventCreate(**data)


class TestValidation:
    def test_an_event_must_end_after_it_starts(self) -> None:
        with pytest.raises(ValueError, match="end after"):
            _event(ends_at=BASE - timedelta(hours=1))

    def test_a_record_link_is_all_or_nothing(self) -> None:
        with pytest.raises(ValueError, match="half"):
            _event(entity_type="lead")

    def test_an_attendee_needs_exactly_one_identity(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            AttendeeInput()
        with pytest.raises(ValueError, match="exactly one"):
            AttendeeInput(user_id=BASE and None, email=None)


class TestCreate:
    async def test_an_event_lands_on_the_owners_calendar(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        event, conflicts = await CalendarService(db, auth).create_event(
            _event(), user
        )
        assert event.owner_id == user.id
        assert event.status == "confirmed"
        assert conflicts == []

    async def test_attendees_can_be_internal_and_external_together(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A showing has an agent and a buyer, and the buyer has no user row."""
        user, auth = admin
        colleague = await make_user(db, organization, "colleague@vantage.example")

        event, _ = await CalendarService(db, auth).create_event(
            _event(
                attendees=[
                    AttendeeInput(user_id=colleague.id),
                    AttendeeInput(email="Buyer@Example.com", display_name="A Buyer"),
                ]
            ),
            user,
        )

        identities = {(a.user_id, a.email) for a in event.attendees}
        assert (colleague.id, None) in identities
        # Lowercased on write, so the unique constraint means what it looks
        # like it means.
        assert (None, "buyer@example.com") in identities

    async def test_an_attendee_from_another_tenant_is_a_404(
        self, db: AsyncSession, other_organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """404 rather than 403: the endpoint must not confirm which user ids
        exist in other workspaces."""
        user, auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        with pytest.raises(NotFoundError):
            await CalendarService(db, auth).create_event(
                _event(attendees=[AttendeeInput(user_id=outsider.id)]), user
            )

    async def test_a_linked_event_reaches_the_records_timeline(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The unified timeline is only unified if everything that happens to a
        record reaches it."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )

        await CalendarService(db, auth).create_event(
            _event(entity_type="lead", entity_id=lead.id), user
        )

        activities = (
            (
                await db.execute(
                    select(Activity).where(Activity.entity_id == lead.id)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert any("Showing scheduled" in a.subject for a in activities)

    async def test_scheduling_against_an_invisible_record_is_refused(
        self, db: AsyncSession, organization, rbac_seeded, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")

        with pytest.raises(NotFoundError):
            await CalendarService(db, agent_auth).create_event(
                _event(entity_type="lead", entity_id=lead.id), agent
            )


class TestBookingForOthers:
    async def test_an_agent_cannot_book_someone_elses_calendar(
        self, db: AsyncSession, organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Putting an event on a colleague's calendar is the same kind of act
        as reassigning their lead."""
        agent = await make_user(db, organization, "agent@vantage.example")
        other = await make_user(db, organization, "other@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")

        with pytest.raises(PermissionDeniedError, match="own calendar"):
            await CalendarService(db, agent_auth).create_event(
                _event(owner_id=other.id), agent
            )

    async def test_an_admin_can_and_the_owner_is_told(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        colleague = await make_user(db, organization, "colleague@vantage.example")

        event, _ = await CalendarService(db, auth).create_event(
            _event(owner_id=colleague.id), user
        )
        assert event.owner_id == colleague.id

        notification = (
            (
                await db.execute(
                    select(Notification).where(Notification.user_id == colleague.id)
                )
            )
            .unique()
            .scalar_one()
        )
        assert notification.type == "calendar.event_scheduled"


class TestWindowQueries:
    async def test_an_event_spanning_the_window_is_included(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Overlap, not containment. A `BETWEEN` on the start time would miss
        the closing that began at 9 when you are looking at the afternoon."""
        user, auth = admin
        service = CalendarService(db, auth)
        await service.create_event(
            _event(
                title="All morning",
                starts_at=BASE - timedelta(hours=3),
                ends_at=BASE + timedelta(hours=3),
            ),
            user,
        )

        found = await service.list_events(
            start=BASE, end=BASE + timedelta(minutes=30)
        )
        assert [row.title for row in found] == ["All morning"]

    async def test_an_event_ending_exactly_at_the_window_start_is_excluded(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Strict bounds, so an event is not counted twice at a boundary."""
        user, auth = admin
        service = CalendarService(db, auth)
        await service.create_event(
            _event(starts_at=BASE - timedelta(hours=1), ends_at=BASE), user
        )

        assert await service.list_events(start=BASE, end=BASE + timedelta(hours=1)) == []

    async def test_cancelled_events_are_off_the_calendar_but_still_queryable(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = CalendarService(db, auth)
        event, _ = await service.create_event(_event(), user)
        await service.cancel_event(event.id, user)

        window = {"start": BASE - timedelta(days=1), "end": BASE + timedelta(days=1)}
        assert await service.list_events(**window) == []
        assert len(await service.list_events(**window, include_cancelled=True)) == 1

    async def test_another_agents_events_are_not_visible(
        self, db: AsyncSession, organization, admin, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await CalendarService(db, auth).create_event(_event(), user)

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        found = await CalendarService(db, agent_auth).list_events(
            start=BASE - timedelta(days=1), end=BASE + timedelta(days=1)
        )
        assert found == []


class TestConflicts:
    async def test_an_overlap_is_reported_and_the_event_still_saves(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Double-booking is usually a mistake and occasionally deliberate — a
        broker covering two open houses on the same street."""
        user, auth = admin
        service = CalendarService(db, auth)
        first, _ = await service.create_event(_event(title="First"), user)

        second, conflicts = await service.create_event(
            _event(
                title="Second",
                starts_at=BASE + timedelta(minutes=30),
                ends_at=BASE + timedelta(minutes=90),
            ),
            user,
        )

        assert second.id is not None
        assert [c.event_id for c in conflicts] == [first.id]

    async def test_a_tentative_event_neither_conflicts_nor_is_conflicted_with(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A pencilled-in showing is exactly the thing you expect to be
        double-booked against while it is being arranged."""
        user, auth = admin
        service = CalendarService(db, auth)
        await service.create_event(_event(title="Pencilled", status="tentative"), user)

        _confirmed, conflicts = await service.create_event(
            _event(title="Confirmed"), user
        )
        assert conflicts == []

    async def test_an_event_does_not_conflict_with_itself_on_edit(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Without the exclusion every update would report one conflict."""
        user, auth = admin
        service = CalendarService(db, auth)
        event, _ = await service.create_event(_event(), user)

        _updated, conflicts = await service.update_event(
            event.id, CalendarEventUpdate(title="Renamed"), user
        )
        assert conflicts == []

    async def test_another_agents_calendar_is_not_a_conflict(
        self, db: AsyncSession, organization, admin, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        colleague = await make_user(db, organization, "colleague@vantage.example")
        service = CalendarService(db, auth)
        await service.create_event(_event(owner_id=colleague.id), user)

        _mine, conflicts = await service.create_event(_event(), user)
        assert conflicts == []


class TestUpdates:
    async def test_moving_an_event_re_arms_its_reminder(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Leaving `reminded_at` set would silently drop the reminder for the
        new time, which is the one the user cares about."""
        user, auth = admin
        service = CalendarService(db, auth)
        event, _ = await service.create_event(_event(reminder_minutes=30), user)

        row = (
            (await db.execute(select(CalendarEvent).where(CalendarEvent.id == event.id)))
            .unique()
            .scalar_one()
        )
        row.reminded_at = datetime.now(UTC)
        await db.flush()

        await service.update_event(
            event.id,
            CalendarEventUpdate(
                starts_at=BASE + timedelta(days=1),
                ends_at=BASE + timedelta(days=1, hours=1),
            ),
            user,
        )
        await db.refresh(row)
        assert row.reminded_at is None

    async def test_replacing_attendees_preserves_existing_responses(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Somebody who already accepted should not be reset because a
        colleague was added to the same event."""
        user, auth = admin
        colleague = await make_user(db, organization, "colleague@vantage.example")
        other = await make_user(db, organization, "other@vantage.example")
        service = CalendarService(db, auth)

        event, _ = await service.create_event(
            _event(attendees=[AttendeeInput(user_id=colleague.id)]), user
        )
        event.attendees[0].response = "accepted"
        await db.flush()

        updated, _ = await service.update_event(
            event.id,
            CalendarEventUpdate(
                attendees=[
                    AttendeeInput(user_id=colleague.id),
                    AttendeeInput(user_id=other.id),
                ]
            ),
            user,
        )

        responses = {a.user_id: a.response for a in updated.attendees}
        assert responses[colleague.id] == "accepted"
        assert responses[other.id] == "needs_action"


class TestReminders:
    async def test_a_due_reminder_notifies_once(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """`reminded_at` is what makes a five-minute sweep safe to run."""
        user, auth = admin
        soon = datetime.now(UTC) + timedelta(minutes=10)
        await CalendarService(db, auth).create_event(
            _event(
                starts_at=soon,
                ends_at=soon + timedelta(hours=1),
                reminder_minutes=30,
            ),
            user,
        )
        await db.commit()

        assert await sweep_calendar_reminders({}) == 1
        assert await sweep_calendar_reminders({}) == 0

        notifications = (
            (
                await db.execute(
                    select(Notification).where(
                        Notification.type == "calendar.reminder"
                    )
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert len(notifications) == 1

    async def test_a_reminder_that_is_not_due_yet_is_left_alone(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        later = datetime.now(UTC) + timedelta(hours=6)
        await CalendarService(db, auth).create_event(
            _event(
                starts_at=later,
                ends_at=later + timedelta(hours=1),
                reminder_minutes=15,
            ),
            user,
        )
        await db.commit()

        assert await sweep_calendar_reminders({}) == 0

    async def test_an_event_with_no_reminder_is_never_swept(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        soon = datetime.now(UTC) + timedelta(minutes=5)
        await CalendarService(db, auth).create_event(
            _event(starts_at=soon, ends_at=soon + timedelta(hours=1)), user
        )
        await db.commit()

        assert await sweep_calendar_reminders({}) == 0
