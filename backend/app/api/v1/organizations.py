"""Organization (workspace) endpoints.

There is no `GET /organizations` collection and no `:id` path parameter. A
caller belongs to exactly one organization and the tenant is derived from their
token, never from the URL. Accepting an id from the client would create an
IDOR surface that RLS would then have to catch — better not to offer it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import (
    CurrentUser,
    OrganizationId,
    TenantSessionDep,
    verify_csrf,
)
from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError
from app.repositories.organization import OrganizationRepository
from app.schemas.organization import (
    OrganizationMember,
    OrganizationRead,
    OrganizationUpdate,
)
from app.services.audit import AuditService, build_diff

router = APIRouter()


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
    """Members of the caller's workspace."""
    repository = OrganizationRepository(session)
    members = await repository.list_members(organization_id)
    return [OrganizationMember.model_validate(member) for member in members]
