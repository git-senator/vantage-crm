"""Authentication endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.cookies import clear_session_cookies, set_session_cookies
from app.api.v1.dependencies import (
    CurrentUser,
    RefreshTokenDep,
    RequestContextDep,
    SettingsDep,
    TenantSessionDep,
    get_db,
    verify_csrf,
)
from app.core.config import Settings
from app.core.exceptions import AuthenticationError
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

router = APIRouter()


def _profile(user: User, roles: list[str] | None = None) -> UserProfile:
    """Build the response profile.

    `roles`/`permissions` are populated by the RBAC layer in Phase 1.3. The
    fields exist now so the client contract does not change when they arrive.
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
        roles=roles or [],
        permissions=[],
    )


def _session_response(
    response: Response, settings: Settings, issued: IssuedSession
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
        user=_profile(issued.user),
        expires_at=issued.access_expires_at,
        csrf_token=issued.csrf_token,
    )


@router.post("/login", response_model=SessionResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    settings: SettingsDep,
    context: RequestContextDep,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Exchange credentials for a session.

    Tokens are returned as httpOnly cookies, never in the response body — a
    token readable by JavaScript is exfiltrable by any XSS.
    """
    service = AuthService(session, settings)
    issued = await service.authenticate(payload.email, payload.password, context)
    return _session_response(response, settings, issued)


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

    service = AuthService(session, settings)
    issued = await service.refresh(refresh_token, context)
    return _session_response(response, settings, issued)


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
async def me(user: CurrentUser) -> UserProfile:
    """The authenticated user, their organization, roles and permissions."""
    return _profile(user)


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
    service = AuthService(session, settings)
    await service.change_password(user, payload.current_password, payload.new_password)
    clear_session_cookies(response, settings)
    return MessageResponse(message="Password changed. Please sign in again.")
