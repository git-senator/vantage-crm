"""Organization (workspace) endpoints.

There is no `GET /organizations` collection and no `:id` path parameter. A
caller belongs to exactly one organization and the tenant is derived from their
token, never from the URL. Accepting an id from the client would create an
IDOR surface that RLS would then have to catch — better not to offer it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.v1.dependencies import (
    CurrentUser,
    OrganizationId,
    TenantSessionDep,
    verify_csrf,
)
from app.core.audit_actions import AuditAction
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.rbac import Role, UserRole
from app.repositories.organization import OrganizationRepository
from app.schemas.organization import (
    IntegrationsStatus,
    OrganizationMember,
    OrganizationRead,
    OrganizationUpdate,
)
from app.services.audit import AuditService, build_diff

router = APIRouter()


@router.get("/current/integrations", response_model=IntegrationsStatus)
async def get_integrations(
    _organization_id: OrganizationId,
    _user: CurrentUser,
) -> IntegrationsStatus:
    """Which real integrations are wired, so Settings shows the truth.

    Deployment-level config (not per-org yet): the calendar is connected when a
    service-account key and a calendar id are both present in the environment.
    """
    s = get_settings()
    return IntegrationsStatus(
        calendar_connected=bool(s.GOOGLE_CALENDAR_SA_B64 and s.GOOGLE_CALENDAR_ID),
        calendar_id=s.GOOGLE_CALENDAR_ID or None,
        calendar_timezone=s.GOOGLE_CALENDAR_TZ or None,
    )


@router.get("/current", response_model=OrganizationRead)
async def get_current_organization(
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _user: CurrentUser,
) -> OrganizationRead:
    """The caller's workspace."""
    repository = OrganizationRepository(session)
    organization = await repository.get_current(organization_id)
    if organization is None:
        raise NotFoundError("Organization not found.")
    return OrganizationRead.model_validate(organization)


@router.patch(
    "/current",
    response_model=OrganizationRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_current_organization(
    payload: OrganizationUpdate,
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _user: CurrentUser,
) -> OrganizationRead:
    """Update workspace settings.

    Phase 1.3 gates this behind `settings.manage`; until roles exist, any
    authenticated member of the workspace may edit it.
    """
    repository = OrganizationRepository(session)
    organization = await repository.get_current(organization_id)
    if organization is None:
        raise NotFoundError("Organization not found.")

    updates = payload.model_dump(exclude_unset=True)
    before = {field: getattr(organization, field) for field in updates}
    for field, value in updates.items():
        setattr(organization, field, value)
    await session.flush()

    await AuditService(session).record(
        action=AuditAction.ORGANIZATION_UPDATED,
        organization_id=organization_id,
        actor_id=_user.id,
        actor_email=_user.email,
        entity_type="organization",
        entity_id=organization.id,
        metadata={"changes": build_diff(before, updates)},
    )

    return OrganizationRead.model_validate(organization)


@router.get("/current/members", response_model=list[OrganizationMember])
async def list_members(
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _user: CurrentUser,
) -> list[OrganizationMember]:
    """Members of the caller's workspace, each with the roles they hold."""
    repository = OrganizationRepository(session)
    members = await repository.list_members(organization_id)

    # One query for every membership's role, folded into a per-user list — the
    # alternative (a relationship load per member) is N+1 over a page that is
    # already the whole workspace.
    rows = (
        await session.execute(
            select(UserRole.user_id, Role.key)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.organization_id == organization_id)
        )
    ).all()
    roles_by_user: dict[UUID, list[str]] = {}
    for user_id, role_key in rows:
        roles_by_user.setdefault(user_id, []).append(role_key)

    return [
        OrganizationMember(
            id=member.id,
            email=member.email,
            full_name=member.full_name,
            initials=member.initials,
            job_title=member.job_title,
            avatar_hue=member.avatar_hue,
            status=member.status,
            roles=roles_by_user.get(member.id, []),
            last_login_at=member.last_login_at,
            created_at=member.created_at,
        )
        for member in members
    ]
