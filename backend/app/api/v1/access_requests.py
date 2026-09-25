"""Access requests and invitations.

Three audiences on one router:

  * **The public** submits a request and accepts an invitation. These endpoints
    take no session — like `/auth/login`, they run before anyone is
    authenticated — and are rate-limited per IP.
  * **A reviewer** (`users.manage`) lists the queue and approves or rejects.
  * Approval provisions the account and emails a single-use link; the public
    accept endpoint is where that link is redeemed.

The submit and accept handlers use `get_db` (no tenant) because their tables are
tenant-less and, for accept, the service binds the invitation's own org before
writing the user. See app/services/access.py.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    RequestContextDep,
    SettingsDep,
    TenantSessionDep,
    get_db,
    require,
    verify_csrf,
)
from app.api.v1.rate_limit_deps import client_ip, enforce
from app.core import rate_limit
from app.core.config import Settings
from app.core.logging import get_logger
from app.models.organization import Organization
from app.models.user import User
from app.schemas.access import (
    AccessRequestApprove,
    AccessRequestCreate,
    AccessRequestRead,
    InvitationAccept,
    InvitationInfo,
)
from app.schemas.auth import MessageResponse
from app.services.access import AccessRequestService
from app.services.notifications.base import EmailAddress
from app.services.notifications.service import NotificationService

logger = get_logger(__name__)

router = APIRouter()


# ------------------------------------------------------------------ public


@router.post("", response_model=MessageResponse, status_code=202)
async def submit_access_request(
    payload: AccessRequestCreate,
    request: Request,
    response: Response,
    settings: SettingsDep,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Record a request to join. Always answers the same way.

    The response never reveals whether the email already belongs to an account —
    that would turn this form into a user-enumeration oracle, exactly what login
    is careful to avoid.
    """
    await enforce(rate_limit.REQUEST_ACCESS_PER_IP, client_ip(request), response=response)

    access_request = await AccessRequestService(session, settings).submit(payload)

    # Best-effort notification. A mail failure must not fail the submission —
    # the request is safely recorded and visible in the review queue regardless.
    await _notify_reviewers(settings, access_request.full_name, access_request.email,
                            access_request.requested_role, access_request.message)

    return MessageResponse(
        message="Thanks — your request has been received. "
        "You'll get an email if it's approved."
    )


@router.get("/invitations/{token}", response_model=InvitationInfo)
async def read_invitation(
    token: str,
    settings: SettingsDep,
    session: AsyncSession = Depends(get_db),
) -> InvitationInfo:
    """Validate an invitation link and return what the accept page shows."""
    service = AccessRequestService(session, settings)
    invitation = await service.get_invitation(token)

    # Reading the org name needs its tenant context bound (organizations is
    # RLS'd by id). The same bind lets us read the user's name.
    from app.db.session import set_tenant_context

    await set_tenant_context(session, invitation.organization_id, invitation.user_id)
    organization = await session.get(Organization, invitation.organization_id)
    user = await session.get(User, invitation.user_id)
    return InvitationInfo(
        email=invitation.email,
        full_name=user.full_name if user else invitation.email,
        organization_name=organization.name if organization else "ROSSA CRM",
    )


@router.post("/invitations/{token}/accept", response_model=MessageResponse)
async def accept_invitation(
    token: str,
    payload: InvitationAccept,
    request: Request,
    response: Response,
    settings: SettingsDep,
    context: RequestContextDep,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Set the password and activate the account. The link is spent on success."""
    await enforce(rate_limit.INVITE_ACCEPT_PER_IP, client_ip(request), response=response)

    await AccessRequestService(session, settings).accept(
        token, payload.password, context
    )
    return MessageResponse(message="Your password is set. You can sign in now.")


# ------------------------------------------------------------- review queue


@router.get(
    "",
    response_model=list[AccessRequestRead],
    dependencies=[Depends(require("users.manage"))],
)
async def list_access_requests(
    settings: SettingsDep,
    session: TenantSessionDep,
    _user: CurrentUser,
) -> list[AccessRequestRead]:
    requests = await AccessRequestService(session, settings).list_requests()
    return [AccessRequestRead.model_validate(r) for r in requests]


@router.post(
    "/{request_id}/approve",
    response_model=AccessRequestRead,
    dependencies=[Depends(require("users.manage")), Depends(verify_csrf)],
)
async def approve_access_request(
    request_id: UUID,
    payload: AccessRequestApprove,
    settings: SettingsDep,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    context: RequestContextDep,
) -> AccessRequestRead:
    """Admit the requester, provision the account, and email the invite link."""
    service = AccessRequestService(session, settings)
    access_request, raw_token = await service.approve(
        request_id, payload.role_key, auth, context
    )

    accept_url = f"{settings.PUBLIC_APP_URL.rstrip('/')}/accept-invite/{raw_token}"
    try:
        await NotificationService().send_user_invitation(
            to=EmailAddress(access_request.email, access_request.full_name),
            inviter_name=user.full_name,
            accept_url=accept_url,
        )
    except Exception:  # pragma: no cover - best effort; account is already created
        logger.exception("invitation_email_failed")

    return AccessRequestRead.model_validate(access_request)


@router.post(
    "/{request_id}/reject",
    response_model=AccessRequestRead,
    dependencies=[Depends(require("users.manage")), Depends(verify_csrf)],
)
async def reject_access_request(
    request_id: UUID,
    settings: SettingsDep,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    context: RequestContextDep,
) -> AccessRequestRead:
    service = AccessRequestService(session, settings)
    access_request = await service.reject(request_id, auth, context)
    return AccessRequestRead.model_validate(access_request)


# --------------------------------------------------------------- internal


async def _notify_reviewers(
    settings: Settings,
    full_name: str,
    email: str,
    requested_role: str | None,
    message: str | None,
) -> None:
    """Email whoever is configured to triage new requests. Never raises."""
    recipients = settings.ACCESS_REQUEST_NOTIFY_EMAILS
    if not recipients:
        return

    queue_url = f"{settings.PUBLIC_APP_URL.rstrip('/')}/requests"
    role_line = f"Requested role: {requested_role}\n" if requested_role else ""
    note_line = f"Message: {message}\n" if message else ""
    text_body = (
        f"New access request for ROSSA CRM:\n\n"
        f"Name: {full_name}\n"
        f"Email: {email}\n"
        f"{role_line}{note_line}\n"
        f"Review it: {queue_url}\n"
    )
    html_body = (
        f"<p>New access request for ROSSA CRM:</p>"
        f"<p><strong>{full_name}</strong><br>{email}</p>"
        + (f"<p>Requested role: {requested_role}</p>" if requested_role else "")
        + (f"<p>Message: {message}</p>" if message else "")
        + f'<p><a href="{queue_url}">Review it</a></p>'
    )

    service = NotificationService()
    for address in recipients:
        try:
            await service.send_raw(
                to=EmailAddress(address),
                subject=f"New access request — {full_name}",
                html_body=html_body,
                text_body=text_body,
                category="access_request",
            )
        except Exception:  # pragma: no cover - best effort
            logger.exception("access_request_notify_failed")
