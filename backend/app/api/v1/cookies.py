"""Session cookie policy.

Centralised so the flags cannot drift between login, refresh, and logout — a
`Secure` flag missing on one path is a session-hijacking bug that is easy to
miss in review.

Cookie design (docs/SECURITY.md §2.1):

  access   httpOnly, SameSite=Lax,    Path=/
  refresh  httpOnly, SameSite=Strict, Path=/api/v1/auth
  csrf     readable by JS,            Path=/

The refresh cookie is scoped to the auth path so it is not attached to ordinary
API calls: it travels only when it is actually needed, shrinking its exposure.
The CSRF cookie is intentionally NOT httpOnly — the double-submit pattern
requires the client to read it and echo it in a header.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import Response

from app.core.config import Settings

REFRESH_COOKIE_PATH = "/api/v1/auth"


def _max_age(expires_at: datetime, now: datetime) -> int:
    return max(int((expires_at - now).total_seconds()), 0)


def set_session_cookies(
    response: Response,
    settings: Settings,
    *,
    access_token: str,
    access_expires_at: datetime,
    refresh_token: str,
    refresh_expires_at: datetime,
    csrf_token: str,
    now: datetime,
) -> None:
    response.set_cookie(
        key=settings.ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=_max_age(access_expires_at, now),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )

    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=_max_age(refresh_expires_at, now),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        # Strict: a refresh must never ride along on a cross-site request.
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
        domain=settings.COOKIE_DOMAIN,
    )

    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=_max_age(refresh_expires_at, now),
        # Readable by design — the client echoes it in X-CSRF-Token.
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    """Expire all session cookies.

    Path and domain must match the values used when setting, or the browser
    keeps the original cookie and the user stays logged in.
    """
    response.delete_cookie(
        key=settings.ACCESS_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        domain=settings.COOKIE_DOMAIN,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="strict",
    )
    response.delete_cookie(
        key=settings.CSRF_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
