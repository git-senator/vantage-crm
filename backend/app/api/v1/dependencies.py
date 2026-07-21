"""Request dependency chain.

Resolution order for an authenticated endpoint (docs/ARCHITECTURE.md §4.2):

    token -> current user -> tenant-bound DB session -> permission -> scope

The tenant context is bound inside the request transaction with `SET LOCAL`,
never `SET`. `SET` is connection-scoped, and connections are pooled, so it would
leak one tenant's scope into the next tenant's request — silently, with no error
raised. This is risk R8, rated Critical. See docs/DATABASE.md §2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.logging import org_id_var, user_id_var
from app.core.security import AccessTokenClaims, TokenDecodeError, decode_access_token
from app.db.session import get_session_factory, set_tenant_context
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.auth import RequestContext

SettingsDep = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------- session


async def get_db(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    """An unauthenticated transaction. Used by login, where there is no tenant yet."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        yield session


# ------------------------------------------------------------------ auth


def get_access_token(
    request: Request,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Read the access token from the cookie, or a Bearer header.

    The cookie is the browser path. The Bearer header exists for the Next.js
    BFF, which holds the cookie and forwards the token over the internal
    network, and for service-to-service calls later.
    """
    cookie_token = request.cookies.get(settings.ACCESS_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    raise AuthenticationError("Not authenticated.")


def get_token_claims(
    token: Annotated[str, Depends(get_access_token)],
    settings: SettingsDep,
) -> AccessTokenClaims:
    try:
        return decode_access_token(settings, token)
    except TokenDecodeError as exc:
        raise AuthenticationError(str(exc)) from exc


ClaimsDep = Annotated[AccessTokenClaims, Depends(get_token_claims)]


async def get_tenant_session(
    claims: ClaimsDep, settings: SettingsDep
) -> AsyncIterator[AsyncSession]:
    """A transaction with the RLS tenant context bound to the caller's org.

    `set_tenant_context` issues `set_config(..., is_local => true)`, which is
    transaction-scoped and therefore pool-safe.
    """
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_tenant_context(session, claims.organization_id, claims.subject)
        yield session


TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]


async def get_current_user(claims: ClaimsDep, session: TenantSessionDep) -> User:
    """Load the authenticated user and populate logging context.

    The token is re-checked against the database rather than trusted alone: a
    user deactivated 30 seconds ago still holds a valid signed token, and must
    not be served.
    """
    users = UserRepository(session)
    user = await users.get_with_organization(claims.subject, claims.organization_id)

    if user is None or not user.is_active:
        raise AuthenticationError("Account is not available.")

    # Every subsequent log line in this request carries these automatically.
    user_id_var.set(str(user.id))
    org_id_var.set(str(user.organization_id))

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_organization_id(claims: ClaimsDep) -> UUID:
    return claims.organization_id


OrganizationId = Annotated[UUID, Depends(get_organization_id)]


# --------------------------------------------------------------- context


def get_request_context(request: Request) -> RequestContext:
    """Client metadata for forensic logging.

    `X-Forwarded-For` is only trusted because uvicorn runs with
    `--proxy-headers` behind the Next.js BFF; it is never used for
    authorization decisions.
    """
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )
    return RequestContext(
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )


RequestContextDep = Annotated[RequestContext, Depends(get_request_context)]


# ------------------------------------------------------------------ csrf


def verify_csrf(
    request: Request,
    settings: SettingsDep,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Double-submit CSRF check for state-changing requests.

    Cookies are attached automatically by the browser, so cookie auth needs an
    explicit defence. The client reads the non-httpOnly CSRF cookie and echoes
    it in a header; a cross-site attacker can cause the cookie to be sent but
    cannot read it to set the header.
    """
    from app.core.security import constant_time_compare

    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    cookie_value = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not cookie_value or not x_csrf_token:
        raise AuthenticationError("Missing CSRF token.")

    if not constant_time_compare(cookie_value, x_csrf_token):
        raise AuthenticationError("Invalid CSRF token.")


def get_refresh_token(
    request: Request, settings: SettingsDep
) -> str | None:
    return request.cookies.get(settings.REFRESH_COOKIE_NAME)


RefreshTokenDep = Annotated[str | None, Depends(get_refresh_token)]

# Silences an unused-import warning while keeping the symbol available for
# routers that declare an explicit cookie parameter.
__all__ = [
    "ClaimsDep",
    "Cookie",
    "CurrentUser",
    "OrganizationId",
    "RefreshTokenDep",
    "RequestContextDep",
    "SettingsDep",
    "TenantSessionDep",
    "get_db",
    "verify_csrf",
]
