"""Book viewings into Google Calendar.

The AI agent, when a client settles on a time, creates a calendar event so the
viewing lands in the brokerage's own Google Calendar — the place agents already
live. Auth is a **service account**: the CRM mints a signed JWT, exchanges it for
an access token, and calls the Calendar REST API directly. No google client
libraries, for the same reason the messaging and AI layers avoid vendor SDKs —
one HTTP client, one set of conventions.

The service account can only touch a calendar it was granted "make changes to
events" on, so the blast radius is exactly one calendar the owner chose to
share, and nothing else in their Google account.

Off by default: with no key the service reports `enabled = False` and callers
skip booking rather than failing. A booking that cannot happen is surfaced as a
result, never as a crash in the reply path.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass

import httpx
import jwt

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/calendar"
_API = "https://www.googleapis.com/calendar/v3"


class CalendarError(Exception):
    """A booking could not be made. Carries a human-readable reason."""


@dataclass(frozen=True, slots=True)
class BookedEvent:
    id: str
    html_link: str
    start: str


class GoogleCalendarService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        s = self._settings
        return bool(s.GOOGLE_CALENDAR_SA_B64 and s.GOOGLE_CALENDAR_ID)

    def _credentials(self) -> dict[str, str]:
        try:
            raw = base64.b64decode(self._settings.GOOGLE_CALENDAR_SA_B64)
            data = json.loads(raw)
        except (binascii.Error, ValueError) as exc:
            raise CalendarError("Calendar credentials are not valid.") from exc
        if not data.get("client_email") or not data.get("private_key"):
            raise CalendarError("Calendar credentials are incomplete.")
        return data

    async def _access_token(self, credentials: dict[str, str]) -> str:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": credentials["client_email"],
                "scope": _SCOPE,
                "aud": _TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            credentials["private_key"],
            algorithm="RS256",
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        if resp.status_code != 200:
            logger.warning("calendar_token_failed", extra={"status": resp.status_code})
            raise CalendarError("Could not authenticate with Google Calendar.")
        return resp.json()["access_token"]

    async def create_event(
        self,
        *,
        summary: str,
        start_iso: str,
        duration_minutes: int = 30,
        description: str | None = None,
    ) -> BookedEvent:
        """Create an event and return its id and link.

        `start_iso` is a local datetime (`YYYY-MM-DDTHH:MM:SS`) interpreted in
        the configured timezone — the agent extracts the time in the brokerage's
        local terms and this layer does not second-guess it. The end is derived
        from `duration_minutes` so a caller only has to know when it starts.
        """
        if not self.enabled:
            raise CalendarError("Calendar booking is not configured.")

        credentials = self._credentials()
        token = await self._access_token(credentials)

        start_local = _strip_zone(start_iso)
        end_local = _add_minutes(start_local, duration_minutes)
        tz = self._settings.GOOGLE_CALENDAR_TZ
        body = {
            "summary": summary,
            "description": description or "",
            "start": {"dateTime": start_local, "timeZone": tz},
            "end": {"dateTime": end_local, "timeZone": tz},
        }
        calendar_id = self._settings.GOOGLE_CALENDAR_ID
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{_API}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                "calendar_event_failed",
                extra={"status": resp.status_code, "body": resp.text[:300]},
            )
            raise CalendarError("Google Calendar refused the event.")
        j = resp.json()
        return BookedEvent(
            id=j.get("id", ""),
            html_link=j.get("htmlLink", ""),
            start=start_local,
        )


def _strip_zone(value: str) -> str:
    """Normalise to a naive local `YYYY-MM-DDTHH:MM:SS`.

    The agent may hand back an offset or a `Z`; the event carries its zone in the
    `timeZone` field, so a zone on the timestamp too would double-apply. Trailing
    zone markers are cut and seconds defaulted when absent.
    """
    v = value.strip().replace(" ", "T")
    for cut in ("Z", "+"):
        idx = v.find(cut, 10)
        if idx != -1:
            v = v[:idx]
    # A trailing "-HH:MM" offset (but not the date's own dashes).
    if v.count("-") > 2:
        v = v[: v.rfind("-")]
    if len(v) == 16:  # YYYY-MM-DDTHH:MM
        v += ":00"
    return v


def _add_minutes(start_local: str, minutes: int) -> str:
    t = time.strptime(start_local, "%Y-%m-%dT%H:%M:%S")
    end = time.localtime(time.mktime(t) + minutes * 60)
    return time.strftime("%Y-%m-%dT%H:%M:%S", end)
