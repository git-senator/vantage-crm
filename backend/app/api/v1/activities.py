"""Activity endpoints.

Two read shapes, with different authorization:

  * `GET /activities?entity_type=&entity_id=` — one record's timeline. Gated on
    being able to read that record, not on an activity permission: if you can
    see the lead, you can see what happened to it.
  * `GET /activities` — the cross-entity feed. Gated on `activities.view`,
    whose scope decides whose activity is visible.

Writes need `activities.manage` *and* readability of the parent, so nobody can
log into a timeline they cannot see.
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
from app.repositories.activity import ActivityCursor
from app.schemas.activity import (
    ActivityCreate,
    ActivityFilters,
    ActivityRead,
    ActivityUpdate,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, PageMeta
from app.services.activity import ActivityService, is_system_activity

router = APIRouter()


def _to_read(activity) -> ActivityRead:  # type: ignore[no-untyped-def]
    return ActivityRead(
        id=activity.id,
        entity_type=activity.entity_type,
        entity_id=activity.entity_id,
        type=activity.type,
        subject=activity.subject,
        body=activity.body,
        occurred_at=activity.occurred_at,
        metadata=activity.metadata_ or {},
        actor=(
            {
                "id": activity.actor.id,
                "full_name": activity.actor.full_name,
                "initials": activity.actor.initials,
                "avatar_hue": activity.actor.avatar_hue,
            }
            if activity.actor
            else None
        ),
        is_system=is_system_activity(activity),
        created_at=activity.created_at,
    )


@router.get("", response_model=Page[ActivityRead])
async def list_activities(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
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
    """Timeline or feed, depending on whether an entity is named.

    Naming an entity gives that record's timeline and needs only read access to
    it. Omitting one gives the caller's feed and needs `activities.view`.
    """
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
    service = ActivityService(session, auth)
    decoded = ActivityCursor.decode(cursor) if cursor else None

    if entity_type is not None and entity_id is not None:
        rows, has_more = await service.list_for_entity(
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
            cursor=decoded,
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


@router.get("/{activity_id}", response_model=ActivityRead)
async def get_activity(
    activity_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ActivityRead:
    return _to_read(await ActivityService(session, auth).get_activity(activity_id))


@router.post(
    "",
    response_model=ActivityRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_activity(
    payload: ActivityCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> ActivityRead:
    """Log a call, email, meeting, showing or note against a record."""
    activity = await ActivityService(session, auth).create_activity(payload, user)
    response.headers["Location"] = f"/api/v1/activities/{activity.id}"
    return _to_read(activity)


@router.patch(
    "/{activity_id}",
    response_model=ActivityRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_activity(
    activity_id: UUID,
    payload: ActivityUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> ActivityRead:
    """Correct an entry you logged. 409 on system-recorded activity."""
    return _to_read(
        await ActivityService(session, auth).update_activity(
            activity_id, payload, user
        )
    )


@router.delete(
    "/{activity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_activity(
    activity_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("activities.manage"))],
    user: CurrentUser,
) -> None:
    """Remove an entry you logged. 409 on system-recorded activity."""
    await ActivityService(session, auth).delete_activity(activity_id, user)
