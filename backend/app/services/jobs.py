"""Reading the state of the background queue.

Deliberately read-only. A "retry this job" button is tempting and is not here:
the jobs in this system are all re-derivable from database state — the sweeps
re-find their work on the next tick — so a manual retry would duplicate a
mechanism that already exists, while adding a way for an admin to re-run
something at an arbitrary moment. When a job arrives whose work is genuinely
lost on failure, that will be the time to add one, along with the idempotency
guarantees that make it safe.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.job import JobFailure
from app.schemas.job import QueueHealth
from app.workers.queue import get_queue

logger = get_logger(__name__)


class JobMonitorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _visible(self, organization_id: UUID):  # type: ignore[no-untyped-def]
        # Tenant rows plus infrastructure rows, matching the table's RLS
        # policy. Stated here as well rather than relying on RLS alone: the
        # policy is the boundary, this is the intent, and a reader of this
        # query should not have to go and check which it is.
        return or_(
            JobFailure.organization_id == organization_id,
            JobFailure.organization_id.is_(None),
        )

    async def list_failures(
        self,
        organization_id: UUID,
        *,
        include_resolved: bool = False,
        limit: int = 50,
    ) -> list[JobFailure]:
        query = select(JobFailure).where(self._visible(organization_id))
        if not include_resolved:
            query = query.where(JobFailure.resolved_at.is_(None))
        query = query.order_by(JobFailure.last_failed_at.desc()).limit(limit)
        return list((await self.session.execute(query)).scalars().all())

    async def health(self, organization_id: UUID) -> QueueHealth:
        open_failures = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(JobFailure)
                    .where(self._visible(organization_id))
                    .where(JobFailure.resolved_at.is_(None))
                )
            ).scalar()
            or 0
        )

        # Queue depth is global rather than per-tenant — ARQ's queue is one
        # sorted set and does not partition by anything. It is reported anyway
        # because "nothing is being processed" is the failure a per-tenant
        # number would not reveal.
        try:
            queue = await get_queue()
            queued: int | None = int(await queue.zcard(queue.default_queue_name))
            reachable = True
        except Exception:
            logger.exception("queue_health_check_failed")
            queued, reachable = None, False

        return QueueHealth(
            queue_reachable=reachable,
            queued_jobs=queued,
            open_failures=open_failures,
        )
