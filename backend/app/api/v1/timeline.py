"""Timeline endpoints.

  * `GET /timeline?entity_type=&entity_id=` — one record's merged timeline
    (activities + notes), gated on reading that record.
  * `GET /timeline` — the cross-entity feed, showing whichever sources the
    caller may view, within scope. Backs the dashboard's recent-activity panel.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.schemas.common import MAX_PAGE_SIZE
from app.schemas.timeline import TimelineItem
from app.services.timeline import TimelineService

router = APIRouter()


@router.get("", response_model=list[TimelineItem])
async def get_timeline(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> list[TimelineItem]:
    """A record's merged timeline when an entity is named, otherwise the feed."""
    service = TimelineService(session, auth)
    if entity_type is not None and entity_id is not None:
        return await service.for_entity(
            entity_type=entity_type, entity_id=entity_id, limit=limit
        )
    return await service.feed(limit=limit)
