"""Calendar reminders.

One cron sweep, per tenant, raising a notification for every event whose
reminder is now due. The centre decides whether that also becomes an email — the
job does not know and does not need to.

`reminded_at` is what makes the sweep safe to run every minute: it is set the
moment the notification is raised, so an event is reminded about exactly once
however many times the sweep sees it. Moving an event clears the stamp, because
the reminder that matters is the one for the new time.

The horizon is computed from the **longest** reminder lead time in play rather
than a fixed lookahead. A one-week reminder and a ten-minute one are both
legitimate, and a fixed window would either miss the first or scan uselessly far
ahead for the second.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.repositories.calendar import CalendarEventRepository
from app.services.notification_center import NotificationCenter
from app.workers.context import (
    active_organization_ids,
    tenant_scope,
    unscoped_scope,
)
from app.workers.runner import job

logger = get_logger(__name__)

#: The largest `reminder_minutes` the schema permits (one week), so the sweep's
#: horizon covers every reminder that could be due.
MAX_REMINDER_MINUTES = 10_080


@job(max_tries=2)
async def sweep_calendar_reminders(ctx: dict[str, Any]) -> int:
    """Raise reminders that have come due. Returns how many."""
    now = datetime.now(UTC)
    horizon = now + timedelta(minutes=MAX_REMINDER_MINUTES)
    raised = 0

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            events = await CalendarEventRepository(session).list_due_reminders(
                now=now, horizon=horizon
            )
            center = NotificationCenter(session)

            for event in events:
                assert event.reminder_minutes is not None  # the query filters it
                due_at = event.starts_at - timedelta(minutes=event.reminder_minutes)
                if due_at > now:
                    # Not yet. The query is bounded by the widest possible lead
                    # time, so most rows it returns are simply early.
                    continue

                await center.raise_notification(
                    organization_id=organization,
                    recipient_id=event.owner_id,
                    category="task",
                    type="calendar.reminder",
                    title=f"Upcoming: {event.title}",
                    body=(
                        f"{event.starts_at:%d %b %H:%M}"
                        + (f" — {event.location}" if event.location else "")
                    ),
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    metadata={"event_id": str(event.id)},
                )
                # Stamped in the same transaction as the notification, so the
                # two cannot disagree about whether this fired.
                event.reminded_at = now
                raised += 1

    if raised:
        logger.info("calendar_reminders_raised", extra={"count": raised})
    return raised
