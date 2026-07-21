"""Authentication endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.cookies import clear_session_cookies, set_session_cookies
from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    RefreshTokenDep,
    RequestContextDep,
    SettingsDep,
    TenantSessionDep,
    get_db,
    verify_csrf,
)
from app.api.v1.rate_limit_deps import (
    clear_login_limits,
    enforce,
    enforce_login,
)
from app.core import rate_limit
from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import hash_refresh_token
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    OrganizationSummary,
    PasswordChangeRequest,
    SessionResponse,
    UserProfile,
)
from app.services.auth import AuthService, IssuedSession
from app.services.rbac import AuthorizationContext, RbacService

router = APIRouter()


def _profile(
    user: User, authorization: AuthorizationContext | None = None
) -> UserProfile:
    """Build the response profile.

    Permissions are sent to the client so the UI can hide what the user cannot
    do. That is UX, not a control — every action is still authorized
    server-side (docs/SECURITY.md §1.4).
    """
    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        initials=user.initials,
        job_title=user.job_title,
        phone=user.phone,
        avatar_hue=user.avatar_hue,
        status=user.status,
        mfa_enabled=user.mfa_enabled,
        last_login_at=user.last_login_at,
        organization=OrganizationSummary.model_validate(user.organization),
        roles=list(authorization.role_keys) if authorization else [],
        permissions=authorization.permission_keys if authorization else [],
    )


def _session_response(
    response: Response,
    settings: Settings,
    issued: IssuedSession,
    authorization: AuthorizationContext | None = None,
) -> SessionResponse:
    now = datetime.now(UTC)
    set_session_cookies(
        response,
        settings,
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
        csrf_token=issued.csrf_token,
        now=now,
    )
    return SessionResponse(
        user=_profile(issued.user, authorization),
        expires_at=issued.access_expires_at,
        csrf_token=issued.csrf_token,
    )


@router.post("/login", response_model=SessionResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    context: RequestContextDep,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Exchange credentials for a session.

    Tokens are returned as httpOnly cookies, never in the response body — a
    token readable by JavaScript is exfiltrable by any XSS.
    """
    # Before any database work: an attacker must not be able to make us do
    # Argon2 verification thousands of times.
    await enforce_login(request, response, payload.email)

    service = AuthService(session, settings)
    issued = await service.authenticate(payload.email, payload.password, context)

    # A user who mistyped twice then succeeded should not stay throttled.
    await clear_login_limits(request, payload.email)

    authorization = await RbacService(session).resolve(
        issued.user.id, issued.user.organization_id, use_cache=False
    )
    return _session_response(response, settings, issued, authorization)


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    response: Response,
    settings: SettingsDep,
    context: RequestContextDep,
    refresh_token: RefreshTokenDep,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Rotate the refresh token and mint a new access token.

    Reuse of an already-spent token revokes the entire family — see
    docs/SECURITY.md §2.3.
    """
    if not refresh_token:
        raise AuthenticationError("No session to refresh.")

    # Keyed on the token, not the user: a compromised token being hammered
    # must not throttle the legitimate holder's other sessions.
    await enforce(
        rate_limit.REFRESH_PER_TOKEN,
        hash_refresh_token(refresh_token),
        response=response,
    )

    service = AuthService(session, settings)
    issued = await service.refresh(refresh_token, context)
    authorization = await RbacService(session).resolve(
        issued.user.id, issued.user.organization_id, use_cache=False
    )
    return _session_response(response, settings, issued, authorization)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    settings: SettingsDep,
    refresh_token: RefreshTokenDep,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """End the current session.

    Always returns 200. A logout that fails would leave the user believing they
    are signed out when they are not.
    """
    service = AuthService(session, settings)
    await service.logout(refresh_token)
    clear_session_cookies(response, settings)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=UserProfile)
async def me(user: CurrentUser, authorization: Authorization) -> UserProfile:
    """The authenticated user, their organization, roles and permissions."""
    return _profile(user, authorization)


@router.post(
    "/password/change",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_csrf)],
)
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    user: CurrentUser,
    settings: SettingsDep,
    session: TenantSessionDep,
) -> MessageResponse:
    """Change the password and invalidate every existing session.

    Revoking all sessions is deliberate: if the change was prompted by a
    suspected compromise, leaving other sessions alive defeats the purpose.
    """
    await enforce(rate_limit.PASSWORD_CHANGE, str(user.id), response=response)

    service = AuthService(session, settings)
    await service.change_password(user, payload.current_password, payload.new_password)
    clear_session_cookies(response, settings)
    return MessageResponse(message="Password changed. Please sign in again.")
