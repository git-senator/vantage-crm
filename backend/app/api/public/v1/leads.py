"""Public lead endpoints.

The same `LeadService` the internal API uses — no repository access, no
authorization logic here. Authentication is by API key; the key's creator is
the actor and scope anchor for every operation.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import CREATED_SORT, PUBLIC_V1_PREFIX, is_ascending
from app.api.v1.leads import _to_read
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.lead import LeadCreate, LeadFilters, LeadRead, LeadUpdate
from app.services.lead import LeadService

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=Page[LeadRead], summary="List leads")
async def list_leads(
    principal: Annotated[MachinePrincipal, Depends(require_scope("leads.view"))],
    search: Annotated[str | None, Query(max_length=200)] = None,
    stage: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    source: str | None = None,
    temperature: str | None = None,
    owner_id: UUID | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    sort: CREATED_SORT = "-created_at",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[LeadRead]:
    filters = LeadFilters.model_validate(
        {
            "search": search,
            "stage": stage,
            "status": status_filter,
            "source": source,
            "temperature": temperature,
            "owner_id": owner_id,
            "tag": tag,
        }
    )
    rows, has_more = await LeadService(principal.session, principal.auth).list_leads(
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
    return Page[LeadRead](
        data=[_to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get("/{lead_id}", response_model=LeadRead, summary="Retrieve a lead")
async def get_lead(
    lead_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("leads.view"))],
) -> LeadRead:
    return _to_read(await LeadService(principal.session, principal.auth).get_lead(lead_id))


@router.post(
    "",
    response_model=LeadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a lead",
)
async def create_lead(
    payload: LeadCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope("leads.manage"))],
) -> LeadRead:
    actor = await actor_for(principal)
    lead = await LeadService(principal.session, principal.auth).create_lead(payload, actor)
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/leads/{lead.id}"
    return _to_read(lead)


@router.patch("/{lead_id}", response_model=LeadRead, summary="Update a lead")
async def update_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope("leads.manage"))],
) -> LeadRead:
    actor = await actor_for(principal)
    lead = await LeadService(principal.session, principal.auth).update_lead(
        lead_id, payload, actor
    )
    return _to_read(lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a lead",
)
async def delete_lead(
    lead_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("leads.manage"))],
) -> None:
    actor = await actor_for(principal)
    await LeadService(principal.session, principal.auth).delete_lead(lead_id, actor)
