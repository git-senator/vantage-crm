"""Notification endpoints.

Every route here is scoped to **the caller**, not to a permission. There is no
`notifications.view` because there is nothing to grant: a notification belongs
to its recipient, and no role — including owner — has a legitimate reason to
read someone else's. Authentication is the whole authorization story, which is
why `CurrentUser` appears and `require(...)` does not.

There is no create endpoint. Notifications are raised by services in response to
something happening; an endpoint that let a client post one to another user
would be a phishing surface inside the product.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.dependencies import CurrentUser, TenantSessionDep, verify_csrf
from app.core.exceptions import NotFoundError
from app.models.notification import Notification
from app.schemas.common import Cursor
from app.schemas.notification import (
    NotificationList,
    NotificationPreferenceRead,
    NotificationPreferencesUpdate,
    NotificationRead,
    UnreadCount,
)
from app.services.notification_center import NotificationCenter

router = APIRouter()


def _to_read(notification: Notification) -> NotificationRead:
    return NotificationRead(
        id=notification.id,
        category=notification.category,
        type=notification.type,
        title=notification.title,
        body=notification.body,
        entity_type=notification.entity_type,
        entity_id=notification.entity_id,
        metadata_=notification.metadata_,
        actor=(
            {
                "id": notification.actor.id,
                "full_name": notification.actor.full_name,
                "initials": notification.actor.initials,
                "avatar_hue": notification.actor.avatar_hue,
            }
            if notification.actor
            else None
        ),
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


@router.get("", response_model=NotificationList)
async def list_notifications(
    session: TenantSessionDep,
    user: CurrentUser,
    unread_only: Annotated[bool, Query()] = False,
    category: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> NotificationList:
    """This user's notifications, newest first.

    The unread count travels with the list because the bell needs both and a
    separate call for a number the server just computed is a round trip for
    nothing.
    """
    parsed: Cursor | None = Cursor.decode(cursor) if cursor else None
    rows, _has_more, unread = await NotificationCenter(session).list_for_user(
        user.organization_id,
        user.id,
        unread_only=unread_only,
        category=category,
        limit=limit,
        cursor=parsed,
    )
    return NotificationList(
        data=[_to_read(row) for row in rows], unread_count=unread
    )


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(session: TenantSessionDep, user: CurrentUser) -> UnreadCount:
    """Just the badge. Cheap enough to poll — it is one partial-index count."""
    return UnreadCount(
        unread_count=await NotificationCenter(session).unread_count(
            user.organization_id, user.id
        )
    )


@router.post(
    "/{notification_id}/read",
    response_model=NotificationRead,
    dependencies=[Depends(verify_csrf)],
)
async def mark_read(
    notification_id: UUID,
    session: TenantSessionDep,
    user: CurrentUser,
) -> NotificationRead:
    """Idempotent: re-reading keeps the original timestamp, because "when did
    you first see this" is the useful fact."""
    notification = await NotificationCenter(session).mark_read(
        notification_id, user.organization_id, user.id
    )
    if notification is None:
        raise NotFoundError("Notification not found.")
    return _to_read(notification)


@router.post(
    "/read-all",
    response_model=UnreadCount,
    dependencies=[Depends(verify_csrf)],
)
async def mark_all_read(session: TenantSessionDep, user: CurrentUser) -> UnreadCount:
    await NotificationCenter(session).mark_all_read(user.organization_id, user.id)
    return UnreadCount(unread_count=0)


@router.get("/preferences", response_model=list[NotificationPreferenceRead])
async def get_preferences(
    session: TenantSessionDep, user: CurrentUser
) -> list[NotificationPreferenceRead]:
    """Every category, with defaults filled in for the ones never set.

    The defaults are materialised server-side so the frontend does not hold a
    second copy of them that can drift.
    """
    rows = await NotificationCenter(session).get_preferences(
        user.organization_id, user.id
    )
    return [NotificationPreferenceRead.model_validate(row) for row in rows]


@router.put(
    "/preferences",
    response_model=list[NotificationPreferenceRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_csrf)],
)
async def update_preferences(
    payload: NotificationPreferencesUpdate,
    session: TenantSessionDep,
    user: CurrentUser,
) -> list[NotificationPreferenceRead]:
    """PUT rather than PATCH: the client sends the set it is looking at, so two
    open preference screens conflict visibly instead of overwriting each other
    one switch at a time."""
    rows = await NotificationCenter(session).update_preferences(
        user.organization_id, user.id, payload.preferences
    )
    return [NotificationPreferenceRead.model_validate(row) for row in rows]
