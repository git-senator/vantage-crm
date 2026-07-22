"""Background-job endpoints — the dead-letter view.

Read-only, and gated on `settings.manage`, which only owner and admin hold. A
dead-letter list names the jobs that failed and the records they were acting on,
so it is closer to the audit log in sensitivity than to a CRM list.

`/health` is separate from the list on purpose. The failure a list cannot show
is "nothing is being processed at all" — an empty dead-letter view looks
identical whether the queue is healthy or the worker has been down since
Tuesday. The health endpoint answers that directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import (
    AuthorizationContext,
    OrganizationId,
    TenantSessionDep,
    require,
)
from app.schemas.job import JobFailureRead, QueueHealth
from app.services.jobs import JobMonitorService

router = APIRouter()


@router.get("/failures", response_model=list[JobFailureRead])
async def list_job_failures(
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _auth: Annotated[AuthorizationContext, Depends(require("settings.manage"))],
    include_resolved: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobFailureRead]:
    """Jobs that exhausted their retries, most recent first.

    Resolved rows are excluded by default: the view exists to show what is
    broken now, and history is one query parameter away when someone is asking
    a different question.
    """
    rows = await JobMonitorService(session).list_failures(
        organization_id, include_resolved=include_resolved, limit=limit
    )
    return [JobFailureRead.model_validate(row) for row in rows]


@router.get("/health", response_model=QueueHealth)
async def queue_health(
    organization_id: OrganizationId,
    session: TenantSessionDep,
    _auth: Annotated[AuthorizationContext, Depends(require("settings.manage"))],
) -> QueueHealth:
    """Is work being processed, and is anything stuck?"""
    return await JobMonitorService(session).health(organization_id)
