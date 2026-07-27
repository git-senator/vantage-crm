"""Public deal endpoints. Reuses `DealService`.

Stage moves are their own endpoint (`POST /deals/{id}/stage`), not a field on
the update body — moving a deal writes history, resets probability and sets a
close date, side effects a plain field edit must not trigger. This mirrors the
internal API's contract exactly.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import CREATED_SORT, PUBLIC_V1_PREFIX, is_ascending
from app.api.v1.deals import _to_read
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.deal import (
    DealCreate,
    DealFilters,
    DealRead,
    DealStageTransition,
    DealUpdate,
)
from app.services.deal import DealService

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("", response_model=Page[DealRead], summary="List deals")
async def list_deals(
    principal: Annotated[MachinePrincipal, Depends(require_scope("deals.view"))],
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    pipeline_id: UUID | None = None,
    stage_id: UUID | None = None,
    owner_id: UUID | None = None,
    client_id: UUID | None = None,
    property_id: UUID | None = None,
    priority: str | None = None,
    min_value: Annotated[str | None, Query()] = None,
    max_value: Annotated[str | None, Query()] = None,
    expected_close_before: Annotated[str | None, Query()] = None,
    expected_close_after: Annotated[str | None, Query()] = None,
    sort: CREATED_SORT = "-created_at",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[DealRead]:
    filters = DealFilters.model_validate(
        {
            "search": search,
            "status": status_filter,
            "pipeline_id": pipeline_id,
            "stage_id": stage_id,
            "owner_id": owner_id,
            "client_id": client_id,
            "property_id": property_id,
            "priority": priority,
            "min_value": min_value,
            "max_value": max_value,
            "expected_close_before": expected_close_before,
            "expected_close_after": expected_close_after,
        }
    )
    rows, has_more = await DealService(principal.session, principal.auth).list_deals(
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
    return Page[DealRead](
        data=[_to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get("/{deal_id}", response_model=DealRead, summary="Retrieve a deal")
async def get_deal(
    deal_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("deals.view"))],
) -> DealRead:
    return _to_read(await DealService(principal.session, principal.auth).get_deal(deal_id))


@router.post(
    "",
    response_model=DealRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a deal",
)
async def create_deal(
    payload: DealCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope("deals.manage"))],
) -> DealRead:
    actor = await actor_for(principal)
    deal = await DealService(principal.session, principal.auth).create_deal(payload, actor)
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/deals/{deal.id}"
    return _to_read(deal)


@router.patch("/{deal_id}", response_model=DealRead, summary="Update a deal")
async def update_deal(
    deal_id: UUID,
    payload: DealUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope("deals.manage"))],
) -> DealRead:
    actor = await actor_for(principal)
    return _to_read(
        await DealService(principal.session, principal.auth).update_deal(
            deal_id, payload, actor
        )
    )


@router.post(
    "/{deal_id}/stage",
    response_model=DealRead,
    summary="Move a deal to another stage",
)
async def move_deal_stage(
    deal_id: UUID,
    payload: DealStageTransition,
    principal: Annotated[MachinePrincipal, Depends(require_scope("deals.manage"))],
) -> DealRead:
    actor = await actor_for(principal)
    return _to_read(
        await DealService(principal.session, principal.auth).move_stage(
            deal_id, payload, actor
        )
    )


@router.delete(
    "/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a deal",
)
async def delete_deal(
    deal_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("deals.manage"))],
) -> None:
    actor = await actor_for(principal)
    await DealService(principal.session, principal.auth).delete_deal(deal_id, actor)
