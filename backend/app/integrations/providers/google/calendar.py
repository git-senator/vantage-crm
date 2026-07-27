"""Google Calendar provider.

`sync` pulls the primary calendar's events incrementally: the first run does a
time-bounded list and Google returns a `nextSyncToken`; subsequent runs pass it
back as the cursor and receive only what changed. The provider returns the count
and the new token — it does not write to the CRM, because normalising a calendar
event into a `calendar_event` row (and resolving conflicts) is the framework's
concern, not the vendor adapter's.
"""

from __future__ import annotations

from typing import Any

from app.integrations.framework.types import ProviderMetadata, SyncOutcome
from app.integrations.providers.google.base import GoogleOAuthProvider

_EVENTS_ENDPOINT = (
    "https://www.googleapis.com/calendar/v3/calendars/primary/events"
)


class GoogleCalendarProvider(GoogleOAuthProvider):
    metadata = ProviderMetadata(
        key="google_calendar",
        name="Google Calendar",
        category="calendar",
        description="Sync meetings and viewings with a Google Calendar.",
        default_scopes=(
            "https://www.googleapis.com/auth/calendar.events",
            "openid",
            "email",
        ),
        event_types=("calendar_event.created", "calendar_event.updated"),
        docs_url="https://developers.google.com/calendar",
    )

    async def sync(
        self,
        *,
        access_token: str,
        cursor: str | None,
        event: dict[str, Any] | None,
    ) -> SyncOutcome:
        params: dict[str, Any] = {"maxResults": 250, "singleEvents": "true"}
        if cursor:
            params["syncToken"] = cursor
        else:
            # A bounded first pull rather than the whole history.
            params["timeMin"] = _time_min()
        data = await self._get_json(access_token, _EVENTS_ENDPOINT, params)
        items = data.get("items", [])
        return SyncOutcome(
            items_processed=len(items),
            cursor=data.get("nextSyncToken") or cursor,
            detail={"kind": "calendar.events", "incremental": bool(cursor)},
        )


def _time_min() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=30)).isoformat()


__all__ = ["GoogleCalendarProvider"]
