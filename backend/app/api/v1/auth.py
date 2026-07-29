"""Authentication endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
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
    client_ip,
    enforce,
    enforce_login,
)
from app.core import rate_limit
from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import hash_refresh_token
from app.db.session import set_tenant_context
from app.models.mfa import MfaRecoveryCode
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    OrganizationSummary,
    PasswordChangeRequest,
    ProfileUpdate,
    SessionResponse,
    UserProfile,
)
from app.schemas.mfa import (
    MfaActivate,
    MfaChallengeRequired,
    MfaEnrolmentStarted,
    MfaPasswordConfirm,
    MfaRecoveryCodes,
    MfaStatus,
    MfaVerify,
)
from app.services.auth import AuthService, IssuedSession
from app.services.mfa import GENERIC_MFA_ERROR, MfaService, mfa_required_for
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


@router.post("/login", response_model=SessionResponse | MfaChallengeRequired)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    context: RequestContextDep,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse | MfaChallengeRequired:
    """Exchange credentials for a session, or for an MFA challenge.

    Tokens are returned as httpOnly cookies, never in the response body — a
    token readable by JavaScript is exfiltrable by any XSS.

    When the account has MFA on, **no session is issued here**. The response is
    a short-lived challenge token and nothing else — no cookies, no profile,
    not even a name. Returning any of that would confirm the password was
    correct, which is the one bit a credential-stuffing attacker is looking
    for, and would leave a usable session sitting behind an unproved factor.
    """
    # Before any database work: an attacker must not be able to make us do
    # Argon2 verification thousands of times.
    await enforce_login(request, response, payload.email)

    service = AuthService(session, settings)
    user = await service.verify_credentials(payload.email, payload.password, context)

    if user.mfa_enabled:
        # The rate limit is deliberately *not* cleared: the login is not
        # complete, and clearing it would let an attacker who has the password
        # keep the account unthrottled while grinding at the second factor.
        challenge, expires_at = MfaService(session, settings).issue_challenge(user)
        return MfaChallengeRequired(
            challenge_token=challenge, expires_at=expires_at
        )

    issued = await service.complete_authentication(user, context)

    # A user who mistyped twice then succeeded should not stay throttled.
    await clear_login_limits(request, payload.email)

    authorization = await RbacService(session).resolve(
        issued.user.id, issued.user.organization_id, use_cache=False
    )
    return _session_response(response, settings, issued, authorization)


@router.post("/mfa/verify", response_model=SessionResponse)
async def verify_mfa(
    payload: MfaVerify,
    request: Request,
    response: Response,
    settings: SettingsDep,
    context: RequestContextDep,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Exchange a challenge token and a code for a session.

    Rate-limited on its own key. Six digits is a space of 10^6, which an
    unthrottled endpoint would let an attacker walk in an afternoon once they
    hold a valid challenge token.
    """
    # The same per-IP limit the login endpoint uses. Six digits is a space of
    # 10^6, which an unthrottled endpoint would let an attacker walk in an
    # afternoon once they hold a valid challenge token.
    await enforce(rate_limit.LOGIN_PER_IP, client_ip(request), response=response)

    mfa = MfaService(session, settings)
    user_id, organization_id = mfa.decode_challenge(payload.challenge_token)

    await set_tenant_context(session, organization_id, user_id)
    user = await UserRepository(session).get_with_organization(
        user_id, organization_id
    )
    if user is None or not user.is_active:
        raise AuthenticationError(GENERIC_MFA_ERROR)

    await mfa.verify_second_factor(user, payload.code)

    issued = await AuthService(session, settings).complete_authentication(
        user, context
    )
    await clear_login_limits(request, user.email)

    authorization = await RbacService(session).resolve(
        user.id, user.organization_id, use_cache=False
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


@router.patch(
    "/me",
    response_model=UserProfile,
    dependencies=[Depends(verify_csrf)],
)
async def update_me(
    payload: ProfileUpdate,
    user: CurrentUser,
    authorization: Authorization,
    session: TenantSessionDep,
) -> UserProfile:
    """Update the caller's own editable profile fields.

    `initials` is derived from `full_name`, so it re-renders for free. Identity,
    role and status are not here — those are not self-editable.
    """
    data = payload.model_dump(exclude_unset=True)
    if data.get("full_name") is not None:
        user.full_name = data["full_name"].strip()
    if "job_title" in data:
        user.job_title = data["job_title"]
    if "phone" in data:
        user.phone = data["phone"]
    if data.get("avatar_hue") is not None:
        user.avatar_hue = data["avatar_hue"]

    # `user` is bound to this request's session (see change_password), so a flush
    # persists the edits; the session commits on a clean response. UserProfile
    # carries no server-updated column, and `organization` is already loaded, so
    # no refresh is needed — building the profile from the in-memory row is safe.
    await session.flush()
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


# --------------------------------------------------------------------- MFA
#
# Enrolment endpoints sit under /auth because they are authentication concerns,
# not profile settings — and because the login-side `/auth/mfa/verify` above
# has to live next to them to be readable as one flow.


@router.get("/mfa", response_model=MfaStatus)
async def mfa_status(
    settings: SettingsDep,
    session: TenantSessionDep,
    user: CurrentUser,
    auth: Authorization,
) -> MfaStatus:
    """Whether MFA is on, and whether this user's roles oblige it.

    `setup_required` is computed here rather than in the frontend so the role
    list lives in one place. A user who holds a required role and has not
    enrolled still has a valid session — see `mfa_required_for` for why
    refusing the login outright is the wrong shape.
    """
    remaining = (
        (
            await session.execute(
                select(MfaRecoveryCode)
                .where(MfaRecoveryCode.user_id == user.id)
                .where(MfaRecoveryCode.used_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return MfaStatus(
        enabled=user.mfa_enabled,
        enrolled_at=user.mfa_enrolled_at,
        recovery_codes_remaining=len(remaining),
        setup_required=not user.mfa_enabled
        and mfa_required_for(auth.role_keys, settings),
    )


@router.post(
    "/mfa/enroll",
    response_model=MfaEnrolmentStarted,
    dependencies=[Depends(verify_csrf)],
)
async def begin_mfa_enrolment(
    settings: SettingsDep,
    session: TenantSessionDep,
    user: CurrentUser,
) -> MfaEnrolmentStarted:
    """Issue a secret to scan. Nothing is enabled until it is proved.

    This is the only response that ever contains the secret.
    """
    secret, uri = await MfaService(session, settings).begin_enrolment(user)
    return MfaEnrolmentStarted(secret=secret, provisioning_uri=uri)


@router.post(
    "/mfa/activate",
    response_model=MfaRecoveryCodes,
    dependencies=[Depends(verify_csrf)],
)
async def activate_mfa(
    payload: MfaActivate,
    settings: SettingsDep,
    session: TenantSessionDep,
    user: CurrentUser,
) -> MfaRecoveryCodes:
    """Prove a code from the enrolled secret and turn MFA on.

    Returns the recovery codes **once**. They are stored hashed, so there is no
    endpoint that can show them again — one that could would mean storing them
    recoverably, which defeats hashing them.
    """
    codes = await MfaService(session, settings).activate(user, payload.code)
    return MfaRecoveryCodes(recovery_codes=codes)


@router.post(
    "/mfa/disable",
    response_model=MessageResponse,
    dependencies=[Depends(verify_csrf)],
)
async def disable_mfa(
    payload: MfaPasswordConfirm,
    settings: SettingsDep,
    session: TenantSessionDep,
    user: CurrentUser,
) -> MessageResponse:
    """Turn MFA off. Requires the password, not just a session.

    Removing a second factor is exactly what an attacker holding a stolen
    session would do to make their access durable, so this re-authenticates
    rather than trusting the cookie in hand.
    """
    await MfaService(session, settings).disable(user, payload.password)
    return MessageResponse(message="Two-factor authentication is off.")


@router.post(
    "/mfa/recovery-codes",
    response_model=MfaRecoveryCodes,
    dependencies=[Depends(verify_csrf)],
)
async def regenerate_recovery_codes(
    payload: MfaPasswordConfirm,
    settings: SettingsDep,
    session: TenantSessionDep,
    user: CurrentUser,
) -> MfaRecoveryCodes:
    """Replace the whole set, invalidating any unused ones."""
    codes = await MfaService(session, settings).regenerate_recovery_codes(
        user, payload.password
    )
    return MfaRecoveryCodes(recovery_codes=codes)
