"""Public activity endpoints. Reuses `ActivityService`.

Reads are authenticated but not gated on a fixed permission here: naming a
record returns its timeline and needs only read access to that record, while
the bare feed needs `activities.view` — both decided inside the service,
exactly as the internal API does. The feed is always newest-first, so there is
no sort parameter.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachineAuth, MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import PUBLIC_V1_PREFIX
from app.api.v1.activities import _to_read
from app.repositories.activity import ActivityCursor
from app.schemas.activity import ActivityCreate, ActivityFilters, ActivityRead, ActivityUpdate
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, PageMeta
from app.services.activity import ActivityService

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=Page[ActivityRead], summary="List activities")
async def list_activities(
    principal: MachineAuth,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    actor_id: UUID | None = None,
    occurred_before: Annotated[str | None, Query()] = None,
    occurred_after: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[ActivityRead]:
    filters = ActivityFilters.model_validate(
        {
            "search": search,
            "type": type_filter,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "actor_id": actor_id,
            "occurred_before": occurred_before,
            "occurred_after": occurred_after,
        }
    )
    service = ActivityService(principal.session, principal.auth)
    decoded = ActivityCursor.decode(cursor) if cursor else None

    if entity_type is not None and entity_id is not None:
        rows, has_more = await service.list_for_entity(
            entity_type=entity_type, entity_id=entity_id, limit=limit, cursor=decoded
        )
    else:
        rows, has_more = await service.list_feed(
            filters=filters, limit=limit, cursor=decoded
        )

    next_cursor = (
        ActivityCursor(rows[-1].occurred_at, rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[ActivityRead](
        data=[_to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get(
    "/{activity_id}", response_model=ActivityRead, summary="Retrieve an activity"
)
async def get_activity(activity_id: UUID, principal: MachineAuth) -> ActivityRead:
    return _to_read(
        await ActivityService(principal.session, principal.auth).get_activity(activity_id)
    )


@router.post(
    "",
    response_model=ActivityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Log an activity",
)
async def create_activity(
    payload: ActivityCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope("activities.manage"))],
) -> ActivityRead:
    actor = await actor_for(principal)
    activity = await ActivityService(principal.session, principal.auth).create_activity(
        payload, actor
    )
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/activities/{activity.id}"
    return _to_read(activity)


@router.patch(
    "/{activity_id}", response_model=ActivityRead, summary="Update an activity"
)
async def update_activity(
    activity_id: UUID,
    payload: ActivityUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope("activities.manage"))],
) -> ActivityRead:
    actor = await actor_for(principal)
    return _to_read(
        await ActivityService(principal.session, principal.auth).update_activity(
            activity_id, payload, actor
        )
    )


@router.delete(
    "/{activity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an activity",
)
async def delete_activity(
    activity_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("activities.manage"))],
) -> None:
    actor = await actor_for(principal)
    await ActivityService(principal.session, principal.auth).delete_activity(
        activity_id, actor
    )
