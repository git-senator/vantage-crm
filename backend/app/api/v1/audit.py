"""Audit log endpoints.

Read-only by design — there is no write endpoint, and the application role
cannot UPDATE or DELETE this table at the database level either. Gated on
`audit.view`, which only owner and admin hold: an audit trail reveals the
workspace's activity pattern and is itself sensitive.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import (
    AuthorizationContext,
    OrganizationId,
    TenantSessionDep,
    require,
)
from app.schemas.audit import AuditLogRead
from app.services.audit import AuditService

router = APIRouter()


@router.get("/", response_model=list[AuditLogRead])
async def list_audit_entries(
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _auth: Annotated[AuthorizationContext, Depends(require("audit.view"))],
    action: Annotated[str | None, Query(max_length=60)] = None,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    entity_id: UUID | None = None,
    actor_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditLogRead]:
    """Most recent entries for this workspace, newest first.

    Filters are explicit parameters rather than a query language: a generic
    filter DSL over an audit table is both an injection surface and an
    unbounded-query risk.
    """
    entries = await AuditService(session).list_entries(
        organization_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        limit=limit,
    )
    return [AuditLogRead.model_validate(entry) for entry in entries]
