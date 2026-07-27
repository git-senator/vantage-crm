"""Request dependency chain.

Resolution order for an authenticated endpoint (docs/ARCHITECTURE.md §4.2):

    token -> current user -> tenant-bound DB session -> permission -> scope

The tenant context is bound inside the request transaction with `SET LOCAL`,
never `SET`. `SET` is connection-scoped, and connections are pooled, so it would
leak one tenant's scope into the next tenant's request — silently, with no error
raised. This is risk R8, rated Critical. See docs/DATABASE.md §2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.logging import org_id_var, user_id_var
from app.core.security import (
    API_KEY_PREFIX,
    AccessTokenClaims,
    TokenDecodeError,
    decode_access_token,
)
from app.db.session import get_session_factory, set_tenant_context
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.auth import RequestContext
from app.services.rbac import AuthorizationContext, RbacService

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


# ------------------------------------------------------------ authorization


async def get_authorization(
    user: CurrentUser, session: TenantSessionDep
) -> AuthorizationContext:
    """Resolve the caller's permissions and scopes.

    Resolved from the database (Redis-cached), not read from the token. Roles
    live in the JWT but permissions do not, so a revocation takes effect within
    one access-token lifetime instead of requiring the user to log out.
    """
    return await RbacService(session).resolve(user.id, user.organization_id)


Authorization = Annotated[AuthorizationContext, Depends(get_authorization)]


def require(*permissions: str) -> Callable[..., Awaitable[AuthorizationContext]]:
    """Endpoint dependency asserting one or more permissions.

    Usage:

        @router.get("/leads", dependencies=[Depends(require("leads.view"))])

    or, when the handler needs the scope:

        async def list_leads(auth: Annotated[..., Depends(require("leads.view"))]):
            scope = auth.scope_for("leads.view")

    Multiple permissions are ANDed. Denial raises 403 and is logged — this is
    the signal that matters for detecting probing.
    """

    async def _dependency(auth: Authorization) -> AuthorizationContext:
        for permission in permissions:
            auth.require(permission)
        return auth

    return _dependency


# ---------------------------------------------------------- machine auth


def get_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Read an API key from `X-API-Key`, or a Bearer header carrying a `vk_` key.

    The Bearer path lets a machine reuse the standard `Authorization` header; it
    is only treated as an API key when the value has the key prefix, so a JWT
    Bearer token is never mistaken for one.
    """
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
        if candidate.startswith(API_KEY_PREFIX):
            return candidate
    raise AuthenticationError("Not authenticated.")


@dataclass(slots=True)
class MachinePrincipal:
    """A request authenticated by an API key: its tenant-bound session and the
    machine `AuthorizationContext` (with `api_key_id` set)."""

    session: AsyncSession
    auth: AuthorizationContext


async def get_machine_principal(
    request: Request, settings: SettingsDep
) -> AsyncIterator[MachinePrincipal]:
    """Authenticate an API key and yield a tenant-bound session + machine context.

    The counterpart of the user chain (`token → user → tenant session → auth`).
    The key resolves its own tenant, `ApiKeyService.authenticate` binds it on the
    session with `set_config(..., is_local => true)` inside the transaction — so
    the handler runs under RLS for the key's org, pool-safe, exactly as a user
    request does.
    """
    from app.services.api_key import ApiKeyService

    raw_key = get_api_key(
        x_api_key=request.headers.get("x-api-key"),
        authorization=request.headers.get("authorization"),
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        auth = await ApiKeyService(session, settings).authenticate(raw_key)
        org_id_var.set(str(auth.organization_id))
        if auth.user_id is not None:
            user_id_var.set(str(auth.user_id))
        yield MachinePrincipal(session=session, auth=auth)


MachineAuth = Annotated[MachinePrincipal, Depends(get_machine_principal)]


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
    "Authorization",
    "AuthorizationContext",
    "ClaimsDep",
    "Cookie",
    "CurrentUser",
    "MachineAuth",
    "MachinePrincipal",
    "OrganizationId",
    "RefreshTokenDep",
    "RequestContextDep",
    "SettingsDep",
    "TenantSessionDep",
    "get_db",
    "get_machine_principal",
    "require",
    "verify_csrf",
]
