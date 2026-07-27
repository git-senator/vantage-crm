"""Public task endpoints. Reuses `TaskService`.

Completion is its own endpoint (`POST /tasks/{id}/complete`): a PATCH cannot set
`status='done'`, because finishing a task stamps completion, writes onto the
linked record's timeline and audits — the same contract as the internal API.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import CREATED_SORT, PUBLIC_V1_PREFIX, is_ascending
from app.api.v1.tasks import _to_read
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.task import TaskComplete, TaskCreate, TaskFilters, TaskRead, TaskUpdate
from app.services.task import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=Page[TaskRead], summary="List tasks")
async def list_tasks(
    principal: Annotated[MachinePrincipal, Depends(require_scope("tasks.view"))],
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority: str | None = None,
    assignee_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    overdue: bool | None = None,
    due_before: Annotated[str | None, Query()] = None,
    due_after: Annotated[str | None, Query()] = None,
    sort: CREATED_SORT = "-created_at",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[TaskRead]:
    filters = TaskFilters.model_validate(
        {
            "search": search,
            "status": status_filter,
            "priority": priority,
            "assignee_id": assignee_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "overdue": overdue,
            "due_before": due_before,
            "due_after": due_after,
        }
    )
    rows, has_more = await TaskService(principal.session, principal.auth).list_tasks(
        filters=filters,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        ascending=is_ascending(sort),
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


@router.get("/{task_id}", response_model=TaskRead, summary="Retrieve a task")
async def get_task(
    task_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("tasks.view"))],
) -> TaskRead:
    return _to_read(await TaskService(principal.session, principal.auth).get_task(task_id))


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
)
async def create_task(
    payload: TaskCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope("tasks.manage"))],
) -> TaskRead:
    actor = await actor_for(principal)
    task = await TaskService(principal.session, principal.auth).create_task(payload, actor)
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/tasks/{task.id}"
    return _to_read(task)


@router.patch("/{task_id}", response_model=TaskRead, summary="Update a task")
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope("tasks.manage"))],
) -> TaskRead:
    actor = await actor_for(principal)
    return _to_read(
        await TaskService(principal.session, principal.auth).update_task(
            task_id, payload, actor
        )
    )


@router.post(
    "/{task_id}/complete",
    response_model=TaskRead,
    summary="Complete a task",
)
async def complete_task(
    task_id: UUID,
    payload: TaskComplete,
    principal: Annotated[MachinePrincipal, Depends(require_scope("tasks.manage"))],
) -> TaskRead:
    actor = await actor_for(principal)
    return _to_read(
        await TaskService(principal.session, principal.auth).complete_task(
            task_id, actor, payload.note
        )
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
)
async def delete_task(
    task_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("tasks.manage"))],
) -> None:
    actor = await actor_for(principal)
    await TaskService(principal.session, principal.auth).delete_task(task_id, actor)
