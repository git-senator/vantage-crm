"""Role and permission endpoints.

Read access is gated on `roles.view`; assignment on `roles.manage`. Note that
even reading the role catalogue is permissioned — it reveals the workspace's
authorization model, which is reconnaissance for anyone probing.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import (
    AuthorizationContext,
    CurrentUser,
    OrganizationId,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import PERMISSIONS
from app.repositories.user import UserRepository
from app.schemas.rbac import (
    PermissionGrant,
    PermissionRead,
    RoleAssignmentRequest,
    RoleRead,
)
from app.services.audit import AuditService
from app.services.rbac import RbacService

router = APIRouter()


@router.get("/", response_model=list[RoleRead])
async def list_roles(
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _auth: Annotated[AuthorizationContext, Depends(require("roles.view"))],
) -> list[RoleRead]:
    """System roles plus any custom roles defined by this workspace."""
    roles = await RbacService(session).list_roles(organization_id)
    return [
        RoleRead(
            id=role.id,
            key=role.key,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            is_protected=role.is_protected,
            permissions=[
                PermissionGrant(key=grant.permission.key, scope=grant.scope)
                for grant in role.permissions
            ],
        )
        for role in roles
    ]


@router.get("/permissions", response_model=list[PermissionRead])
async def list_permissions(
    _auth: Annotated[AuthorizationContext, Depends(require("roles.view"))],
) -> list[PermissionRead]:
    """The full permission vocabulary.

    Served from the in-process registry rather than the database: it is the
    source of truth the database is seeded from, and it cannot drift.
    """
    return [
        PermissionRead(
            key=p.key, resource=p.resource, action=p.action, description=p.description
        )
        for p in PERMISSIONS
    ]


@router.post(
    "/users/{user_id}/assign",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
async def assign_role(
    user_id: UUID,
    payload: RoleAssignmentRequest,
    organization_id: OrganizationId,
    session: TenantSessionDep,
    actor: CurrentUser,
    _auth: Annotated[AuthorizationContext, Depends(require("roles.manage"))],
) -> None:
    """Grant a role to a member of this workspace.

    The target is resolved through the tenant-scoped repository first, so a
    user id from another organization is a 404 rather than a silent no-op.
    """
    target = await UserRepository(session).get(user_id, organization_id)
    if target is None:
        raise NotFoundError("User not found in this workspace.")

    await RbacService(session).assign_role(
        user_id=user_id,
        role_key=payload.role_key,
        organization_id=organization_id,
        granted_by=actor.id,
    )
    await AuditService(session).record(
        action=AuditAction.ROLE_ASSIGNED,
        organization_id=organization_id,
        actor_id=actor.id,
        actor_email=actor.email,
        entity_type="user",
        entity_id=user_id,
        metadata={"role": payload.role_key},
    )


@router.post(
    "/users/{user_id}/revoke",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
async def revoke_role(
    user_id: UUID,
    payload: RoleAssignmentRequest,
    organization_id: OrganizationId,
    session: TenantSessionDep,
    actor: CurrentUser,
    _auth: Annotated[AuthorizationContext, Depends(require("roles.manage"))],
) -> None:
    """Remove a role from a member.

    Refuses to strip the last owner: a workspace with no owner cannot manage
    billing or restore its own access, and recovery requires operator
    intervention.
    """
    if user_id == actor.id and payload.role_key == "owner":
        raise ConflictError("You cannot remove your own owner role.")

    target = await UserRepository(session).get(user_id, organization_id)
    if target is None:
        raise NotFoundError("User not found in this workspace.")

    await RbacService(session).revoke_role(
        user_id=user_id,
        role_key=payload.role_key,
        organization_id=organization_id,
    )
    await AuditService(session).record(
        action=AuditAction.ROLE_REVOKED,
        organization_id=organization_id,
        actor_id=actor.id,
        actor_email=actor.email,
        entity_type="user",
        entity_id=user_id,
        metadata={"role": payload.role_key},
    )
