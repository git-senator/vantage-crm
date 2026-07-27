"""Enterprise maintenance jobs: process a GDPR request, sweep retention.

Thin orchestration over the enterprise services. A data request is the durable
record — the fast path enqueues it, and the retention sweep re-finds any pending
request whose enqueue was lost, so a request is at most one sweep late rather
than stuck. Retention deletion honours legal hold inside the service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger
from app.repositories.enterprise import DataRequestRepository
from app.services.enterprise import DataRequestProcessor, RetentionService
from app.workers.context import (
    active_organization_ids,
    tenant_scope,
    unscoped_scope,
)
from app.workers.queue import JobName, enqueue
from app.workers.runner import job

logger = get_logger(__name__)


@job(organization_arg=1, max_tries=3)
async def process_data_request(
    ctx: dict[str, Any], request_id: str, organization_id: str
) -> str:
    """Run one GDPR export or erasure request."""
    organization = UUID(organization_id)
    settings = get_settings()

    async with tenant_scope(organization) as session:
        request = await DataRequestRepository(session).get(
            UUID(request_id), organization
        )
        if request is None:
            return "gone"
        await DataRequestProcessor(session, settings).process(request)
        return request.status


@job(max_tries=2)
async def sweep_data_retention(ctx: dict[str, Any]) -> int:
    """Delete records past their retention window across every tenant, and
    re-enqueue any GDPR request the fast path lost. Returns rows deleted."""
    settings = get_settings()
    stale_before = datetime.now(UTC) - timedelta(minutes=2)
    deleted_total = 0

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            deleted = await RetentionService(session, settings).sweep(organization)
            deleted_total += sum(deleted.values())
            if deleted:
                logger.info(
                    "retention_swept",
                    extra={"organization_id": str(organization), "deleted": deleted},
                )
            stale = await DataRequestRepository(session).list_stale_pending(
                organization, before=stale_before
            )
            request_ids = [row.id for row in stale]

        for request_id in request_ids:
            await enqueue(
                JobName.PROCESS_DATA_REQUEST,
                str(request_id),
                str(organization),
                job_id=f"datareq:{request_id}",
            )

    return deleted_total
