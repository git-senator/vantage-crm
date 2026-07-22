"""Calendar events and their attendees.

A real-estate calendar is not a generic one. The events that matter — showings,
closings, open houses — are *about a record* and *involve people outside the
workspace*, and both of those shape the schema:

  * **Events carry the same polymorphic (`entity_type`, `entity_id`) pair** as
    notes, activities and attachments, so a showing appears on the property's
    timeline without a translation layer.
  * **Attendees are internal users or external contacts, in one table.** A
    showing has an agent and a buyer, and the buyer has no user row. Splitting
    them into `event_users` and `event_contacts` would double every query that
    asks "who is coming" — which is every query the UI makes.

Two decisions that are less obvious:

**Times are instants, not wall-clock.** `starts_at`/`ends_at` are `timestamptz`.
An all-day event is a flag over an instant range rather than a date, so one
column pair serves both and the "is it happening now" query never has to switch
on a type. The cost is that an all-day event is anchored to a timezone; that is
the honest trade when the alternative is two representations of time in one
table.

**Conflicts are detected, never prevented.** Double-booking an agent is usually
a mistake and occasionally deliberate — a broker covering two open houses on the
same street. The service reports overlaps and lets the user decide; refusing the
write would be the system claiming to know better than the person with the
calendar in front of them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

#: What kind of thing this is. Matches the prototype's vocabulary, with
#: `meeting` added — `internal` described who was there rather than what it was.
CALENDAR_EVENT_TYPES = (
    "showing",
    "call",
    "meeting",
    "closing",
    "open_house",
    "personal",
)

#: `tentative` is not decoration: a showing pencilled in pending a seller's
#: confirmation is a different thing from a booked one, and the difference is
#: what an agent scans the week for.
CALENDAR_EVENT_STATUSES = ("confirmed", "tentative", "cancelled")

CALENDAR_ENTITY_TYPES = ("lead", "client", "property", "deal")

ATTENDEE_RESPONSES = ("needs_action", "accepted", "declined", "tentative")


class CalendarEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "calendar_events"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Whose calendar this is. The scope anchor, like a lead's owner.
    owner_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)

    event_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="meeting", server_default="meeting"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="confirmed", server_default="confirmed"
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: How long before the start to remind. NULL means no reminder — distinct
    #: from 0, which means "at the moment it starts".
    reminder_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Set when the reminder job has fired, so a re-run cannot remind twice.
    reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_id], lazy="joined")
    attendees: Mapped[list[EventAttendee]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('showing', 'call', 'meeting', 'closing', "
            "'open_house', 'personal')",
            name="ck_calendar_events_type",
        ),
        CheckConstraint(
            "status IN ('confirmed', 'tentative', 'cancelled')",
            name="ck_calendar_events_status",
        ),
        CheckConstraint(
            "entity_type IS NULL OR "
            "entity_type IN ('lead', 'client', 'property', 'deal')",
            name="ck_calendar_events_entity_type",
        ),
        # An event that ends before it starts is not a scheduling preference,
        # it is corrupt data — and it would make every range query wrong.
        CheckConstraint("ends_at > starts_at", name="ck_calendar_events_range"),
        CheckConstraint("length(title) > 0", name="ck_calendar_events_title"),
        CheckConstraint(
            "reminder_minutes IS NULL OR reminder_minutes >= 0",
            name="ck_calendar_events_reminder",
        ),
        # The only query a calendar makes: everything in a window, for a tenant.
        # Ordered on `starts_at` so a month view is one index range scan.
        Index(
            "ix_calendar_events_window",
            "organization_id",
            "starts_at",
            "ends_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # One agent's day.
        Index(
            "ix_calendar_events_owner",
            "organization_id",
            "owner_id",
            "starts_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A record's schedule, for the detail page.
        Index(
            "ix_calendar_events_entity",
            "organization_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The reminder sweep's query. Partial and tiny: almost every row is
        # either already reminded or has no reminder at all.
        Index(
            "ix_calendar_events_reminders",
            "starts_at",
            postgresql_where=text(
                "reminder_minutes IS NOT NULL AND reminded_at IS NULL "
                "AND deleted_at IS NULL AND status <> 'cancelled'"
            ),
        ),
    )

    def __repr__(self) -> str:
        return f"<CalendarEvent {self.title} @ {self.starts_at}>"


class EventAttendee(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Somebody expected at an event — internal or external, one table.

    Exactly one of `user_id` and `email` is set, enforced by a CHECK. A showing
    has an agent (a user) and a buyer (an address), and querying "who is coming"
    should not mean two queries and a merge.
    """

    __tablename__ = "event_attendees"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: An internal attendee.
    user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    #: An external one. Lowercased on write so the unique constraint means what
    #: it looks like it means.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    response: Mapped[str] = mapped_column(
        String(20), nullable=False, default="needs_action", server_default="needs_action"
    )

    user: Mapped[User | None] = relationship(foreign_keys=[user_id], lazy="joined")
    event: Mapped[CalendarEvent] = relationship(back_populates="attendees")

    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) <> (email IS NULL)",
            name="ck_event_attendees_identity",
        ),
        CheckConstraint(
            "response IN ('needs_action', 'accepted', 'declined', 'tentative')",
            name="ck_event_attendees_response",
        ),
        # Nulls never collide in Postgres, so these two constraints cover
        # different rows and both are needed: one stops a user being added
        # twice, the other stops an address being.
        UniqueConstraint("event_id", "user_id", name="uq_event_attendees_user"),
        UniqueConstraint("event_id", "email", name="uq_event_attendees_email"),
        Index("ix_event_attendees_event", "event_id"),
        Index("ix_event_attendees_user", "organization_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<EventAttendee {self.user_id or self.email}>"
