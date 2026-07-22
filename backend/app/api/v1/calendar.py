"""Calendar endpoints.

The list takes an explicit window and requires it. A calendar without bounds is
a full-table scan dressed as a feature — and there is no sensible default,
because "this month" means something different to a month view, a day view and a
record's schedule panel.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.core.exceptions import ConflictError
from app.models.calendar import CalendarEvent
from app.schemas.calendar import (
    AttendeeRead,
    CalendarEventCreate,
    CalendarEventRead,
    CalendarEventSaved,
    CalendarEventUpdate,
)
from app.services.calendar import CalendarService

router = APIRouter()

#: The widest window a single request may ask for. A year of one workspace's
#: events is a reasonable export; a decade is a mistake or an attack.
MAX_WINDOW_DAYS = 400


def _to_read(event: CalendarEvent) -> CalendarEventRead:
    return CalendarEventRead(
        id=event.id,
        title=event.title,
        description=event.description,
        location=event.location,
        event_type=event.event_type,
        status=event.status,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        is_all_day=event.is_all_day,
        reminder_minutes=event.reminder_minutes,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        owner={
            "id": event.owner.id,
            "full_name": event.owner.full_name,
            "initials": event.owner.initials,
            "avatar_hue": event.owner.avatar_hue,
        },
        attendees=[
            AttendeeRead.model_validate(attendee) for attendee in event.attendees
        ],
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


@router.get("", response_model=list[CalendarEventRead])
async def list_events(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.view"))],
    _user: CurrentUser,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
    owner_id: UUID | None = None,
    entity_type: Annotated[str | None, Query(max_length=20)] = None,
    entity_id: UUID | None = None,
    event_type: Annotated[str | None, Query(max_length=20)] = None,
    include_cancelled: Annotated[bool, Query()] = False,
) -> list[CalendarEventRead]:
    """Everything overlapping the window, soonest first.

    Overlap, not containment: an event that began this morning and runs into
    the afternoon belongs in the afternoon's view. A `BETWEEN` on the start
    time would miss exactly the long events people most need to see.
    """
    if end <= start:
        raise ConflictError("The window must end after it starts.")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise ConflictError(
            f"A calendar window may span at most {MAX_WINDOW_DAYS} days."
        )

    rows = await CalendarService(session, auth).list_events(
        start=start,
        end=end,
        owner_id=owner_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        include_cancelled=include_cancelled,
    )
    return [_to_read(row) for row in rows]


@router.get("/{event_id}", response_model=CalendarEventRead)
async def get_event(
    event_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.view"))],
    _user: CurrentUser,
) -> CalendarEventRead:
    return _to_read(await CalendarService(session, auth).get_event(event_id))


@router.post(
    "",
    response_model=CalendarEventSaved,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_event(
    payload: CalendarEventCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> CalendarEventSaved:
    """Schedule an event.

    Succeeds even when it overlaps something — the response carries the
    conflicts rather than refusing. Double-booking is usually a mistake and
    occasionally deliberate, and the person with the calendar in front of them
    is better placed to tell which.
    """
    event, conflicts = await CalendarService(session, auth).create_event(payload, user)
    response.headers["Location"] = f"/api/v1/calendar/{event.id}"
    return CalendarEventSaved(event=_to_read(event), conflicts=conflicts)


@router.patch(
    "/{event_id}",
    response_model=CalendarEventSaved,
    dependencies=[Depends(verify_csrf)],
)
async def update_event(
    event_id: UUID,
    payload: CalendarEventUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> CalendarEventSaved:
    """Update an event. Moving it re-arms its reminder."""
    event, conflicts = await CalendarService(session, auth).update_event(
        event_id, payload, user
    )
    return CalendarEventSaved(event=_to_read(event), conflicts=conflicts)


@router.post(
    "/{event_id}/cancel",
    response_model=CalendarEventRead,
    dependencies=[Depends(verify_csrf)],
)
async def cancel_event(
    event_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> CalendarEventRead:
    """Cancel rather than delete.

    "What was I meant to be doing on Tuesday" is a real question, and an event
    that vanishes takes its answer with it.
    """
    event = await CalendarService(session, auth).cancel_event(event_id, user)
    return _to_read(await CalendarService(session, auth).get_event(event.id))


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_event(
    event_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> None:
    await CalendarService(session, auth).delete_event(event_id, user)
