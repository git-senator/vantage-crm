"""Deal endpoints.

HTTP concerns only. Note what is *not* here: no `stage_id` on the PATCH body.
Moving a deal between stages is `POST /deals/{id}/stage`, because it writes
history, recalculates probability, sets a close date and emits an activity —
side effects a field edit must not be able to trigger halfway.
"""

from __future__ import annotations

from decimal import Decimal
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
from app.models.deal import Deal
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.deal import (
    DealAssign,
    DealBoard,
    DealBoardColumn,
    DealCreate,
    DealFilters,
    DealRead,
    DealStageHistoryRead,
    DealStageTransition,
    DealUpdate,
)
from app.services.deal import DealService

router = APIRouter()


def _to_read(deal) -> DealRead:  # type: ignore[no-untyped-def]
    """Project an ORM row onto the response contract.

    `status` and `weighted_value` are Python properties derived from the stage
    and the value, so they are computed here rather than read from a column
    that could disagree with them.
    """
    return DealRead(
        id=deal.id,
        title=deal.title,
        status=deal.status,
        value=deal.value,
        currency=deal.currency,
        commission_amount=deal.commission_amount,
        commission_rate=deal.commission_rate,
        weighted_value=deal.weighted_value,
        probability=deal.probability,
        priority=deal.priority,
        expected_close_date=deal.expected_close_date,
        actual_close_date=deal.actual_close_date,
        lost_reason=deal.lost_reason,
        custom_fields=deal.custom_fields or {},
        pipeline_id=deal.pipeline_id,
        stage={
            "id": deal.stage.id,
            "key": deal.stage.key,
            "name": deal.stage.name,
            "position": deal.stage.position,
            "is_won": deal.stage.is_won,
            "is_lost": deal.stage.is_lost,
        },
        client={
            "id": deal.client.id,
            "display_name": deal.client.display_name,
        },
        listing=(
            {
                "id": deal.listing.id,
                "title": deal.listing.title,
                "full_address": deal.listing.full_address,
            }
            if deal.listing
            else None
        ),
        owner=(
            {
                "id": deal.owner.id,
                "full_name": deal.owner.full_name,
                "initials": deal.owner.initials,
                "avatar_hue": deal.owner.avatar_hue,
            }
            if deal.owner
            else None
        ),
        created_at=deal.created_at,
        updated_at=deal.updated_at,
    )


def _filters_from_query(**kwargs: object) -> DealFilters:
    # Built through Pydantic so an unknown status or malformed value is a 422
    # with a field-level message rather than a filter that matches nothing.
    return DealFilters.model_validate(kwargs)


@router.get("", response_model=Page[DealRead])
async def list_deals(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.view"))],
    _user: CurrentUser,
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
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[DealRead]:
    """Cursor-paginated deals, newest first, scoped to the caller."""
    filters = _filters_from_query(
        search=search,
        status=status_filter,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_id=owner_id,
        client_id=client_id,
        property_id=property_id,
        priority=priority,
        min_value=min_value,
        max_value=max_value,
        expected_close_before=expected_close_before,
        expected_close_after=expected_close_after,
    )

    service = DealService(session, auth)
    rows, has_more = await service.list_deals(
        filters=filters,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
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


@router.get("/board", response_model=DealBoard)
async def deal_board(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.view"))],
    _user: CurrentUser,
    pipeline_id: UUID | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    owner_id: UUID | None = None,
    priority: str | None = None,
) -> DealBoard:
    """The Kanban board: one pipeline's stages, each with its deals.

    Grouped server-side rather than returning a flat list for the client to
    bucket, so the column totals are computed once against the same scoped set
    the cards come from — a client-side sum would silently exclude deals beyond
    the board cap.
    """
    filters = _filters_from_query(
        search=search, owner_id=owner_id, priority=priority
    )
    pipeline, deals = await DealService(session, auth).board(
        pipeline_id=pipeline_id, filters=filters
    )

    by_stage: dict[UUID, list[Deal]] = {}
    for deal in deals:
        by_stage.setdefault(deal.stage_id, []).append(deal)

    columns: list[DealBoardColumn] = []
    for stage in sorted(pipeline.stages, key=lambda s: (s.position, s.key)):
        rows = by_stage.get(stage.id, [])
        columns.append(
            DealBoardColumn(
                stage={
                    "id": stage.id,
                    "key": stage.key,
                    "name": stage.name,
                    "position": stage.position,
                    "is_won": stage.is_won,
                    "is_lost": stage.is_lost,
                },
                deals=[_to_read(row) for row in rows],
                total_value=sum(
                    (row.value or Decimal(0) for row in rows), Decimal(0)
                ),
                count=len(rows),
            )
        )

    return DealBoard(
        pipeline_id=pipeline.id, pipeline_name=pipeline.name, columns=columns
    )


@router.get("/{deal_id}", response_model=DealRead)
async def get_deal(
    deal_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.view"))],
    _user: CurrentUser,
) -> DealRead:
    """One deal. 404 when outside the caller's scope — never 403."""
    return _to_read(await DealService(session, auth).get_deal(deal_id))


@router.get("/{deal_id}/history", response_model=list[DealStageHistoryRead])
async def deal_history(
    deal_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.view"))],
    _user: CurrentUser,
) -> list[DealStageHistoryRead]:
    """Every stage this deal has passed through, newest first."""
    rows = await DealService(session, auth).stage_history(deal_id)
    return [
        DealStageHistoryRead(
            id=row.id,
            from_stage=(
                {
                    "id": row.from_stage.id,
                    "key": row.from_stage.key,
                    "name": row.from_stage.name,
                    "position": row.from_stage.position,
                    "is_won": row.from_stage.is_won,
                    "is_lost": row.from_stage.is_lost,
                }
                if row.from_stage
                else None
            ),
            to_stage={
                "id": row.to_stage.id,
                "key": row.to_stage.key,
                "name": row.to_stage.name,
                "position": row.to_stage.position,
                "is_won": row.to_stage.is_won,
                "is_lost": row.to_stage.is_lost,
            },
            changed_by=None,
            changed_at=row.changed_at,
            duration_seconds=(
                row.duration_in_stage.total_seconds()
                if row.duration_in_stage is not None
                else None
            ),
            note=row.note,
        )
        for row in rows
    ]


@router.post(
    "",
    response_model=DealRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_deal(
    payload: DealCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.manage"))],
    user: CurrentUser,
) -> DealRead:
    """Create a deal. Lands in the default pipeline's first stage unless told
    otherwise, and writes the opening stage-history row."""
    deal = await DealService(session, auth).create_deal(payload, user)
    response.headers["Location"] = f"/api/v1/deals/{deal.id}"
    return _to_read(deal)


@router.patch(
    "/{deal_id}",
    response_model=DealRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_deal(
    deal_id: UUID,
    payload: DealUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.manage"))],
    user: CurrentUser,
) -> DealRead:
    """Partial update. **Cannot move the deal between stages** — that is
    `POST /deals/{id}/stage`."""
    return _to_read(
        await DealService(session, auth).update_deal(deal_id, payload, user)
    )


@router.post(
    "/{deal_id}/stage",
    response_model=DealRead,
    dependencies=[Depends(verify_csrf)],
)
async def move_deal_stage(
    deal_id: UUID,
    payload: DealStageTransition,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.manage"))],
    user: CurrentUser,
) -> DealRead:
    """Move a deal to another stage.

    Writes stage history, resets probability to the stage default, sets or
    clears the close date, and emits a `stage_change` activity — atomically.

    409 when the stage belongs to another pipeline, when the deal is already
    there, or when marking a deal lost without a reason.
    """
    return _to_read(
        await DealService(session, auth).move_stage(deal_id, payload, user)
    )


@router.delete(
    "/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_deal(
    deal_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. The row, its history and its audit trail survive."""
    await DealService(session, auth).delete_deal(deal_id, user)


@router.post(
    "/{deal_id}/assign",
    response_model=DealRead,
    dependencies=[Depends(verify_csrf)],
)
async def assign_deal(
    deal_id: UUID,
    payload: DealAssign,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.manage"))],
    user: CurrentUser,
) -> DealRead:
    """Reassign ownership, bounded by the caller's `deals.manage` scope."""
    return _to_read(
        await DealService(session, auth).assign_deal(deal_id, payload.owner_id, user)
    )
