"""Calendar contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventType = Literal["showing", "call", "meeting", "closing", "open_house", "personal"]
EventStatus = Literal["confirmed", "tentative", "cancelled"]
CalendarEntityType = Literal["lead", "client", "property", "deal"]
AttendeeResponse = Literal["needs_action", "accepted", "declined", "tentative"]


class AttendeeInput(BaseModel):
    """One attendee. Either a workspace user or an outside address.

    Both-or-neither is rejected rather than resolved to a default: an attendee
    who is neither a user nor an address is not an attendee, and one who is
    both is ambiguous about which identity the calendar should honour.
    """

    user_id: UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _exactly_one_identity(self) -> AttendeeInput:
        if (self.user_id is None) == (self.email is None):
            raise ValueError("An attendee needs exactly one of user_id or email.")
        return self


class AttendeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    email: str | None
    display_name: str | None
    response: str


class CalendarEventBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    location: str | None = Field(default=None, max_length=300)
    event_type: EventType = "meeting"
    status: EventStatus = "confirmed"
    starts_at: datetime
    ends_at: datetime
    is_all_day: bool = False
    #: Minutes before the start. `None` means no reminder; `0` means at the
    #: moment it starts, which is a different and legitimate choice.
    reminder_minutes: int | None = Field(default=None, ge=0, le=10_080)
    entity_type: CalendarEntityType | None = None
    entity_id: UUID | None = None

    @model_validator(mode="after")
    def _coherent(self) -> CalendarEventBase:
        if self.ends_at <= self.starts_at:
            raise ValueError("An event must end after it starts.")
        if (self.entity_type is None) != (self.entity_id is None):
            raise ValueError(
                "An event is linked to a record or to nothing, not half of one."
            )
        return self


class CalendarEventCreate(CalendarEventBase):
    #: Whose calendar. Defaults to the caller — booking for somebody else needs
    #: the assign permission, which the service checks.
    owner_id: UUID | None = None
    attendees: list[AttendeeInput] = Field(default_factory=list, max_length=50)


class CalendarEventUpdate(BaseModel):
    """Partial update. Attendees are replaced wholesale when supplied.

    A per-attendee patch API would need its own add/remove/update endpoints for
    a list that is almost always short and edited as a set in the UI.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    location: str | None = Field(default=None, max_length=300)
    event_type: EventType | None = None
    status: EventStatus | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_all_day: bool | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=10_080)
    entity_type: CalendarEntityType | None = None
    entity_id: UUID | None = None
    owner_id: UUID | None = None
    attendees: list[AttendeeInput] | None = Field(default=None, max_length=50)


class EventOwner(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class CalendarEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    location: str | None
    event_type: str
    status: str
    starts_at: datetime
    ends_at: datetime
    is_all_day: bool
    reminder_minutes: int | None
    entity_type: str | None
    entity_id: UUID | None
    owner: EventOwner
    attendees: list[AttendeeRead]
    created_at: datetime
    updated_at: datetime


class ScheduleConflict(BaseModel):
    """An overlapping event on the same person's calendar."""

    event_id: UUID
    title: str
    starts_at: datetime
    ends_at: datetime


class CalendarEventSaved(BaseModel):
    """The event, plus anything it collides with.

    Conflicts are **reported, not enforced**. Double-booking is usually a
    mistake and occasionally deliberate — a broker covering two open houses on
    the same street — so the API surfaces the overlap and lets the person with
    the calendar in front of them decide.
    """

    event: CalendarEventRead
    conflicts: list[ScheduleConflict] = Field(default_factory=list)
