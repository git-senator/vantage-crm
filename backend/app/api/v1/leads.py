"""Lead endpoints.

Reference router for CRM resources. HTTP concerns only — every authorization
decision and business rule lives in the service.

Note what the handlers do *not* do: no ORM queries, no permission branching, no
scope arithmetic. A router that reaches past the service is how authorization
logic starts to scatter.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.clients import to_read as client_to_read
from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.schemas.client import ClientConvert, ClientRead
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.lead import (
    LeadAssign,
    LeadCreate,
    LeadFilters,
    LeadRead,
    LeadUpdate,
)
from app.services.client import ClientService
from app.services.lead import LeadService

router = APIRouter()


def _to_read(lead) -> LeadRead:  # type: ignore[no-untyped-def]
    """Project an ORM row onto the response contract.

    Explicit rather than `from_attributes` alone, because `full_name` is a
    Python property and `owner` needs flattening.
    """
    return LeadRead(
        id=lead.id,
        first_name=lead.first_name,
        last_name=lead.last_name,
        full_name=lead.full_name,
        email=lead.email,
        phone=lead.phone,
        stage=lead.stage,
        status=lead.status,
        source=lead.source,
        temperature=lead.temperature,
        budget_min=lead.budget_min,
        budget_max=lead.budget_max,
        currency=lead.currency,
        preferred_location=lead.preferred_location,
        notes=lead.notes,
        tags=list(lead.tags or []),
        score=lead.score,
        last_contacted_at=lead.last_contacted_at,
        custom_fields=lead.custom_fields or {},
        converted_client_id=lead.converted_client_id,
        converted_at=lead.converted_at,
        owner=(
            {
                "id": lead.owner.id,
                "full_name": lead.owner.full_name,
                "initials": lead.owner.initials,
                "avatar_hue": lead.owner.avatar_hue,
            }
            if lead.owner
            else None
        ),
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


@router.get("", response_model=Page[LeadRead])
async def list_leads(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.view"))],
    _user: CurrentUser,
    search: Annotated[str | None, Query(max_length=200)] = None,
    stage: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    source: str | None = None,
    temperature: str | None = None,
    owner_id: UUID | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[LeadRead]:
    """Cursor-paginated leads, newest first, scoped to the caller's permission.

    An `agent` sees their own; a `manager` sees their team's; an `admin` sees
    everything — all through the same endpoint, because scope is applied in the
    query rather than branching here.
    """
    # Built through Pydantic so an unknown stage or source is a 422 with a
    # field-level message rather than a filter that silently matches nothing.
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

    service = LeadService(session, auth)
    rows, has_more = await service.list_leads(
        filters=filters,
        limit=limit,
        # A malformed cursor yields the first page rather than a 400 — cursors
        # end up in bookmarked URLs.
        cursor=Cursor.decode(cursor) if cursor else None,
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


@router.get("/stats/stages", response_model=dict[str, int])
async def stage_counts(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.view"))],
    _user: CurrentUser,
) -> dict[str, int]:
    """Lead counts per pipeline stage, within the caller's scope."""
    return await LeadService(session, auth).stage_counts()


@router.get("/{lead_id}", response_model=LeadRead)
async def get_lead(
    lead_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.view"))],
    _user: CurrentUser,
) -> LeadRead:
    """One lead. 404 when outside the caller's scope — never 403.

    Returning 403 would confirm the record exists, which is an existence
    oracle across tenants and teams.
    """
    return _to_read(await LeadService(session, auth).get_lead(lead_id))


@router.post(
    "",
    response_model=LeadRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_lead(
    payload: LeadCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.manage"))],
    user: CurrentUser,
) -> LeadRead:
    lead = await LeadService(session, auth).create_lead(payload, user)
    response.headers["Location"] = f"/api/v1/leads/{lead.id}"
    return _to_read(lead)


@router.patch(
    "/{lead_id}",
    response_model=LeadRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.manage"))],
    user: CurrentUser,
) -> LeadRead:
    """Partial update. Only fields present in the body are touched."""
    lead = await LeadService(session, auth).update_lead(lead_id, payload, user)
    return _to_read(lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_lead(
    lead_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. The row and its audit trail survive."""
    await LeadService(session, auth).delete_lead(lead_id, user)


@router.post(
    "/{lead_id}/assign",
    response_model=LeadRead,
    dependencies=[Depends(verify_csrf)],
)
async def assign_lead(
    lead_id: UUID,
    payload: LeadAssign,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("leads.assign"))],
    user: CurrentUser,
) -> LeadRead:
    """Reassign ownership.

    Separate from update because it needs `leads.assign` — an agent may edit
    their own leads without being able to move work onto a colleague.
    """
    lead = await LeadService(session, auth).assign_lead(lead_id, payload.owner_id, user)
    return _to_read(lead)


@router.post(
    "/{lead_id}/convert",
    response_model=ClientRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def convert_lead(
    lead_id: UUID,
    payload: ClientConvert,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[
        Authorization, Depends(require("leads.manage", "contacts.manage"))
    ],
    user: CurrentUser,
) -> ClientRead:
    """Convert a lead into a client. Returns the new client.

    Lives on the leads router because the action starts from a lead, but the
    logic is `ClientService.convert_lead` — it produces a client and needs the
    client repository.

    Both permissions are required. `leads.manage` alone would let someone who
    can edit leads mint client records they could not otherwise create;
    `contacts.manage` alone would let them convert a lead they cannot see.

    409 if the lead has already been converted — conversion is one-shot, so the
    funnel history stays unambiguous.
    """
    client, _lead = await ClientService(session, auth).convert_lead(
        lead_id, payload, user
    )
    response.headers["Location"] = f"/api/v1/clients/{client.id}"
    return client_to_read(client)
