"""Data access for saved reports and their runs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import ReportDefinition, ReportRun

#: How many runs a history view returns. A report run history is read to answer
#: "did last night's go out" and "who exported this", both of which are recent
#: questions; the whole history stays queryable through the audit log.
RUN_PAGE_SIZE = 50


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------ definitions

    def _visible(self, organization_id: UUID, user_id: UUID | None):  # type: ignore[no-untyped-def]
        """Mine, or shared with the workspace.

        Visibility of the *definition* only. Which rows it returns is decided
        separately, against the runner's own scope — see ReportService.
        """
        query = (
            select(ReportDefinition)
            .where(ReportDefinition.organization_id == organization_id)
            .where(ReportDefinition.deleted_at.is_(None))
        )
        if user_id is None:
            # Machine work: the scheduler runs reports nobody is asking for.
            return query
        return query.where(
            or_(
                ReportDefinition.is_shared.is_(True),
                ReportDefinition.owner_id == user_id,
            )
        )

    async def list_visible(
        self, organization_id: UUID, user_id: UUID | None
    ) -> list[ReportDefinition]:
        query = self._visible(organization_id, user_id).order_by(ReportDefinition.name.asc())
        return list((await self.session.execute(query)).scalars().all())

    async def get_visible(
        self, definition_id: UUID, organization_id: UUID, user_id: UUID | None
    ) -> ReportDefinition | None:
        query = self._visible(organization_id, user_id).where(ReportDefinition.id == definition_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def due_for_schedule(
        self, organization_id: UUID, now: datetime
    ) -> list[ReportDefinition]:
        """Scheduled reports whose next run has come round.

        "Due" is computed from `last_run_at` rather than a stored next-run
        timestamp: a stored one drifts whenever a run is missed, and a report
        that silently stops after one outage is worse than one that runs late.
        A NULL `last_run_at` is always due — a newly scheduled report should not
        wait a full period for its first delivery.
        """
        query = (
            select(ReportDefinition)
            .where(ReportDefinition.organization_id == organization_id)
            .where(ReportDefinition.deleted_at.is_(None))
            .where(ReportDefinition.schedule != "none")
        )
        candidates = list((await self.session.execute(query)).scalars().all())
        return [c for c in candidates if _is_due(c, now)]

    # -------------------------------------------------------------- runs

    async def list_runs(
        self,
        organization_id: UUID,
        *,
        definition_id: UUID | None = None,
        requested_by: UUID | None = None,
        limit: int = RUN_PAGE_SIZE,
    ) -> list[ReportRun]:
        query = (
            select(ReportRun)
            .where(ReportRun.organization_id == organization_id)
            .order_by(ReportRun.started_at.desc())
            .limit(limit)
        )
        if definition_id is not None:
            query = query.where(ReportRun.definition_id == definition_id)
        if requested_by is not None:
            query = query.where(ReportRun.requested_by == requested_by)
        return list((await self.session.execute(query)).scalars().all())

    async def get_run(self, run_id: UUID, organization_id: UUID) -> ReportRun | None:
        query = (
            select(ReportRun)
            .where(ReportRun.id == run_id)
            .where(ReportRun.organization_id == organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def expired_runs(
        self, organization_id: UUID, cutoff: datetime, limit: int = 200
    ) -> list[ReportRun]:
        """Runs whose file has outlived its usefulness.

        The row is kept — it is the export audit trail. Only the object is
        swept.
        """
        query = (
            select(ReportRun)
            .where(ReportRun.organization_id == organization_id)
            .where(ReportRun.storage_key.is_not(None))
            .where(ReportRun.started_at < cutoff)
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())


#: How long a period is, for the purpose of "is this due". Months are 28 days
#: rather than calendar months on purpose: a monthly report that lands on the
#: 31st has no January date to be late for, and "roughly monthly, never skipped"
#: beats "exactly monthly, sometimes never".
_PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 28}


def _is_due(definition: ReportDefinition, now: datetime) -> bool:
    if definition.schedule == "none":
        return False
    if definition.last_run_at is None:
        return True
    days = _PERIOD_DAYS.get(definition.schedule)
    if days is None:  # pragma: no cover — the check constraint prevents this
        return False
    return (now - definition.last_run_at).total_seconds() >= days * 86_400


__all__ = ["RUN_PAGE_SIZE", "ReportRepository"]
