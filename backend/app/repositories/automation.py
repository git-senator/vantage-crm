"""Workflow data access.

Workflows have no per-user scope. They are workspace configuration, like roles
or pipelines, and the permission that guards them (`automations.manage`) is only
ever held at ALL — so there is no owner predicate here and adding one later
would be a change to the permission model, not to this file.

The queries that matter for throughput are `published_for_trigger`, which runs
once per CRM mutation, and the two maintenance queries the workers use.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import joinedload, selectinload

from app.models.automation import (
    Workflow,
    WorkflowEvent,
    WorkflowRun,
    WorkflowVersion,
)
from app.repositories.base import BaseRepository


class WorkflowRepository(BaseRepository[Workflow]):
    model = Workflow

    def _base(self, organization_id: UUID) -> Select[tuple[Workflow]]:
        return (
            self.scoped_to_organization(self._base_query(), organization_id)
            .options(joinedload(Workflow.author))
            .execution_options(populate_existing=True)
        )

    async def list_all(self, organization_id: UUID) -> list[Workflow]:
        query = self._base(organization_id).order_by(Workflow.created_at.desc())
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get(self, entity_id: UUID, organization_id: UUID) -> Workflow | None:
        query = self._base(organization_id).where(Workflow.id == entity_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    # ------------------------------------------------------------ versions

    async def get_version(
        self, version_id: UUID, organization_id: UUID
    ) -> WorkflowVersion | None:
        query = (
            select(WorkflowVersion)
            .where(WorkflowVersion.id == version_id)
            .where(WorkflowVersion.organization_id == organization_id)
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def latest_draft(
        self, workflow_id: UUID, organization_id: UUID
    ) -> WorkflowVersion | None:
        query = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .where(WorkflowVersion.organization_id == organization_id)
            .where(WorkflowVersion.status == "draft")
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def published_version(
        self, workflow_id: UUID, organization_id: UUID
    ) -> WorkflowVersion | None:
        query = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .where(WorkflowVersion.organization_id == organization_id)
            .where(WorkflowVersion.status == "published")
            .limit(1)
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def list_versions(
        self, workflow_id: UUID, organization_id: UUID, *, limit: int = 20
    ) -> list[WorkflowVersion]:
        query = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .where(WorkflowVersion.organization_id == organization_id)
            .order_by(WorkflowVersion.version.desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def highest_version(self, workflow_id: UUID, organization_id: UUID) -> int:
        query = (
            select(func.coalesce(func.max(WorkflowVersion.version), 0))
            .where(WorkflowVersion.workflow_id == workflow_id)
            .where(WorkflowVersion.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar_one())

    async def published_for_trigger(
        self, organization_id: UUID, trigger_type: str
    ) -> list[WorkflowVersion]:
        """Live versions listening for this event.

        The hot path: runs once for every CRM mutation in the workspace. Served
        by the partial index on `(organization_id, trigger_type)` where
        `status = 'published'`, joined to the workflow only to check the switch
        — which is why `is_enabled` lives on `workflows` and not on the version.
        """
        query = (
            select(WorkflowVersion)
            .join(Workflow, Workflow.id == WorkflowVersion.workflow_id)
            .where(WorkflowVersion.organization_id == organization_id)
            .where(WorkflowVersion.status == "published")
            .where(WorkflowVersion.trigger_type == trigger_type)
            .where(Workflow.is_enabled.is_(True))
            .where(Workflow.deleted_at.is_(None))
        )
        return list((await self.session.execute(query)).unique().scalars().all())


class WorkflowRunRepository(BaseRepository[WorkflowRun]):
    model = WorkflowRun

    async def list_recent(
        self,
        organization_id: UUID,
        *,
        workflow_id: UUID | None = None,
        limit: int = 50,
    ) -> list[WorkflowRun]:
        query = (
            select(WorkflowRun)
            .where(WorkflowRun.organization_id == organization_id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(limit)
        )
        if workflow_id is not None:
            query = query.where(WorkflowRun.workflow_id == workflow_id)
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get_with_steps(
        self, run_id: UUID, organization_id: UUID
    ) -> WorkflowRun | None:
        query = (
            select(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .where(WorkflowRun.organization_id == organization_id)
            .options(selectinload(WorkflowRun.steps))
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def list_pending(
        self, organization_id: UUID, *, limit: int = 100
    ) -> list[WorkflowRun]:
        """Runs created but not yet started.

        The safety net under the optimistic enqueue: a run exists the moment
        its event is dispatched, so a lost job costs latency rather than the
        automation never happening.
        """
        query = (
            select(WorkflowRun)
            .where(WorkflowRun.organization_id == organization_id)
            .where(WorkflowRun.status == "pending")
            .order_by(WorkflowRun.created_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def list_resumable(
        self, organization_id: UUID, *, now: datetime, limit: int = 100
    ) -> list[WorkflowRun]:
        """Parked runs whose timer has elapsed."""
        query = (
            select(WorkflowRun)
            .where(WorkflowRun.organization_id == organization_id)
            .where(WorkflowRun.status == "waiting")
            .where(WorkflowRun.resume_at.is_not(None))
            .where(WorkflowRun.resume_at <= now)
            .order_by(WorkflowRun.resume_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())


class WorkflowEventRepository(BaseRepository[WorkflowEvent]):
    model = WorkflowEvent

    async def list_undispatched(
        self, organization_id: UUID, *, limit: int = 200
    ) -> list[WorkflowEvent]:
        """The outbox sweep's query. Normally empty — the fast path dispatches
        within milliseconds — which is what the partial index is for."""
        query = (
            select(WorkflowEvent)
            .where(WorkflowEvent.organization_id == organization_id)
            .where(WorkflowEvent.dispatched_at.is_(None))
            .order_by(WorkflowEvent.occurred_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get_for_dispatch(
        self, event_id: UUID, organization_id: UUID
    ) -> WorkflowEvent | None:
        query = (
            select(WorkflowEvent)
            .where(WorkflowEvent.id == event_id)
            .where(WorkflowEvent.organization_id == organization_id)
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()
