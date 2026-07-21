"""Task data access.

Follows the LeadRepository pattern, with the scope anchor being `assignee_id`
rather than `owner_id` — a task belongs to whoever has to do it.

Default ordering is by due date, soonest first, with undated tasks last: a task
list is a work queue, and sorting a work queue by creation date is useless.
Keyset pagination still keys on `(created_at, id)`, because `due_at` is
nullable and mutable and therefore cannot be a stable pagination key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.task import Task
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor
from app.schemas.task import TaskFilters


class TaskRepository(BaseRepository[Task]):
    model = Task

    def _visible(
        self, organization_id: UUID, assignee_ids: list[UUID] | None
    ) -> Select[tuple[Task]]:
        query = self.scoped_to_organization(self._base_query(), organization_id)
        if assignee_ids is not None:
            query = query.where(Task.assignee_id.in_(assignee_ids))
        return query.options(joinedload(Task.assignee)).execution_options(
            populate_existing=True
        )

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Task]], filters: TaskFilters
    ) -> Select[tuple[Task]]:
        if filters.status:
            query = query.where(Task.status == filters.status)
        if filters.priority:
            query = query.where(Task.priority == filters.priority)
        if filters.assignee_id:
            query = query.where(Task.assignee_id == filters.assignee_id)
        if filters.entity_type:
            query = query.where(Task.entity_type == filters.entity_type)
        if filters.entity_id:
            query = query.where(Task.entity_id == filters.entity_id)
        if filters.due_before:
            query = query.where(Task.due_at <= filters.due_before)
        if filters.due_after:
            query = query.where(Task.due_at >= filters.due_after)

        if filters.overdue is True:
            # Overdue is derived, so it becomes a predicate rather than a
            # column comparison: past due AND not finished.
            query = query.where(Task.due_at < datetime.now(UTC)).where(
                Task.status != "done"
            )
        elif filters.overdue is False:
            query = query.where(
                or_(
                    Task.due_at.is_(None),
                    Task.due_at >= datetime.now(UTC),
                    Task.status == "done",
                )
            )

        if filters.search:
            term = filters.search.strip()
            if term:
                query = query.where(
                    or_(
                        Task.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        Task.title.op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        assignee_ids: list[UUID] | None,
        filters: TaskFilters,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[Task], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._apply_filters(
            self._visible(organization_id, assignee_ids), filters
        )

        if cursor is not None:
            query = query.where(
                func.row(Task.created_at, Task.id)
                < func.row(cursor.created_at, cursor.id)
            )

        query = query.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit + 1)

        rows = list((await self.session.execute(query)).unique().scalars().all())
        return rows[:limit], len(rows) > limit

    async def list_queue(
        self,
        organization_id: UUID,
        *,
        assignee_ids: list[UUID] | None,
        filters: TaskFilters,
        limit: int = 25,
    ) -> list[Task]:
        """A work queue: soonest due first, undated last.

        Unpaginated and hard-capped — this backs the dashboard panel and the
        entity sidebars, which show a handful and link to the full list.
        """
        query = self._apply_filters(
            self._visible(organization_id, assignee_ids), filters
        )
        # NULLS LAST: a task with no deadline is not more urgent than one due
        # today, which is what a plain ASC would imply.
        query = query.order_by(
            Task.due_at.asc().nullslast(), Task.created_at.desc()
        ).limit(min(limit, MAX_PAGE_SIZE))
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get_visible(
        self, task_id: UUID, organization_id: UUID, assignee_ids: list[UUID] | None
    ) -> Task | None:
        query = self._visible(organization_id, assignee_ids).where(Task.id == task_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def count_by_status(
        self, organization_id: UUID, assignee_ids: list[UUID] | None
    ) -> dict[str, int]:
        query = (
            select(Task.status, func.count())
            .select_from(Task)
            .where(Task.organization_id == organization_id)
            .where(Task.deleted_at.is_(None))
            .group_by(Task.status)
        )
        if assignee_ids is not None:
            query = query.where(Task.assignee_id.in_(assignee_ids))
        rows = (await self.session.execute(query)).all()
        return dict(rows)  # type: ignore[arg-type]
