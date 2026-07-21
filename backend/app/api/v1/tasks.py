"""Task endpoints.

`PATCH /tasks/{id}` cannot set `status='done'` — that is
`POST /tasks/{id}/complete`, which stamps `completed_at`, writes the completion
onto the linked record's timeline and audits. The same reasoning that keeps
`stage_id` off `DealUpdate`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.task import (
    TaskAssign,
    TaskComplete,
    TaskCreate,
    TaskFilters,
    TaskRead,
    TaskUpdate,
)
from app.services.task import TaskService

router = APIRouter()


def _to_read(task) -> TaskRead:  # type: ignore[no-untyped-def]
    return TaskRead(
        id=task.id,
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        due_at=task.due_at,
        completed_at=task.completed_at,
        is_overdue=task.is_overdue,
        entity_type=task.entity_type,
        entity_id=task.entity_id,
        assignee=(
            {
                "id": task.assignee.id,
                "full_name": task.assignee.full_name,
                "initials": task.assignee.initials,
                "avatar_hue": task.assignee.avatar_hue,
            }
            if task.assignee
            else None
        ),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _filters(**kwargs: object) -> TaskFilters:
    return TaskFilters.model_validate(kwargs)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.view"))],
    _user: CurrentUser,
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority: str | None = None,
    assignee_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    overdue: bool | None = None,
    due_before: Annotated[str | None, Query()] = None,
    due_after: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[TaskRead]:
    """Cursor-paginated tasks, newest first, scoped to the caller."""
    filters = _filters(
        search=search,
        status=status_filter,
        priority=priority,
        assignee_id=assignee_id,
        entity_type=entity_type,
        entity_id=entity_id,
        overdue=overdue,
        due_before=due_before,
        due_after=due_after,
    )
    rows, has_more = await TaskService(session, auth).list_tasks(
        filters=filters,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[TaskRead](
        data=[_to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get("/queue", response_model=list[TaskRead])
async def task_queue(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.view"))],
    _user: CurrentUser,
    assignee_id: UUID | None = None,
    overdue: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
) -> list[TaskRead]:
    """A work queue: soonest due first, undated last, open only.

    Ordered by deadline rather than creation date, because that is what a work
    queue is for. Backs the dashboard panel and the entity sidebars.
    """
    filters = _filters(assignee_id=assignee_id, overdue=overdue)
    rows = await TaskService(session, auth).queue(filters=filters, limit=limit)
    return [_to_read(row) for row in rows]


@router.get("/stats/statuses", response_model=dict[str, int])
async def status_counts(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.view"))],
    _user: CurrentUser,
) -> dict[str, int]:
    return await TaskService(session, auth).status_counts()


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.view"))],
    _user: CurrentUser,
) -> TaskRead:
    return _to_read(await TaskService(session, auth).get_task(task_id))


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_task(
    payload: TaskCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.manage"))],
    user: CurrentUser,
) -> TaskRead:
    task = await TaskService(session, auth).create_task(payload, user)
    response.headers["Location"] = f"/api/v1/tasks/{task.id}"
    return _to_read(task)


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.manage"))],
    user: CurrentUser,
) -> TaskRead:
    """Partial update. 409 on `status='done'` — use the complete endpoint."""
    return _to_read(
        await TaskService(session, auth).update_task(task_id, payload, user)
    )


@router.post(
    "/{task_id}/complete",
    response_model=TaskRead,
    dependencies=[Depends(verify_csrf)],
)
async def complete_task(
    task_id: UUID,
    payload: TaskComplete,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.manage"))],
    user: CurrentUser,
) -> TaskRead:
    """Finish a task: stamps completion, logs it on the linked record."""
    return _to_read(
        await TaskService(session, auth).complete_task(task_id, user, payload.note)
    )


@router.post(
    "/{task_id}/reopen",
    response_model=TaskRead,
    dependencies=[Depends(verify_csrf)],
)
async def reopen_task(
    task_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.manage"))],
    user: CurrentUser,
) -> TaskRead:
    """Undo a completion, clearing the timestamp with it."""
    return _to_read(await TaskService(session, auth).reopen_task(task_id, user))


@router.post(
    "/{task_id}/assign",
    response_model=TaskRead,
    dependencies=[Depends(verify_csrf)],
)
async def assign_task(
    task_id: UUID,
    payload: TaskAssign,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.manage"))],
    user: CurrentUser,
) -> TaskRead:
    return _to_read(
        await TaskService(session, auth).assign_task(task_id, payload.assignee_id, user)
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_task(
    task_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("tasks.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. The audit trail survives."""
    await TaskService(session, auth).delete_task(task_id, user)
