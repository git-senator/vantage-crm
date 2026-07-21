"""Client endpoints.

HTTP concerns only — every authorization decision and business rule lives in
the service, exactly as in `app/api/v1/leads.py`. No ORM queries, no permission
branching, no scope arithmetic.
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
from app.schemas.client import (
    ClientAssign,
    ClientCreate,
    ClientFilters,
    ClientRead,
    ClientUpdate,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.services.client import ClientService

router = APIRouter()


def to_read(client) -> ClientRead:  # type: ignore[no-untyped-def]
    """Project an ORM row onto the response contract.

    Explicit rather than `from_attributes` alone, because `display_name` and
    `is_company` are Python properties and `owner` needs flattening.

    Public because the conversion endpoint on the leads router returns a
    client, and duplicating this projection there is how the two drift.
    """
    return ClientRead(
        id=client.id,
        first_name=client.first_name,
        last_name=client.last_name,
        company_name=client.company_name,
        display_name=client.display_name,
        is_company=client.is_company,
        email=client.email,
        phone=client.phone,
        type=client.type,
        status=client.status,
        address=client.address or {},
        lifetime_value=client.lifetime_value,
        currency=client.currency,
        client_since=client.client_since,
        notes=client.notes,
        tags=list(client.tags or []),
        custom_fields=client.custom_fields or {},
        source_lead_id=client.source_lead_id,
        owner=(
            {
                "id": client.owner.id,
                "full_name": client.owner.full_name,
                "initials": client.owner.initials,
                "avatar_hue": client.owner.avatar_hue,
            }
            if client.owner
            else None
        ),
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


@router.get("", response_model=Page[ClientRead])
async def list_clients(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.view"))],
    _user: CurrentUser,
    search: Annotated[str | None, Query(max_length=200)] = None,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    owner_id: UUID | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[ClientRead]:
    """Cursor-paginated clients, newest first, scoped to the caller's permission.

    An `agent` sees their own; a `manager` sees their team's; an `admin` sees
    everything — all through the same endpoint, because scope is applied in the
    query rather than branching here.
    """
    # Built through Pydantic so an unknown type or status is a 422 with a
    # field-level message rather than a filter that silently matches nothing.
    filters = ClientFilters.model_validate(
        {
            "search": search,
            "type": type_filter,
            "status": status_filter,
            "owner_id": owner_id,
            "tag": tag,
        }
    )

    service = ClientService(session, auth)
    rows, has_more = await service.list_clients(
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
    return Page[ClientRead](
        data=[to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get("/stats/types", response_model=dict[str, int])
async def type_counts(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.view"))],
    _user: CurrentUser,
) -> dict[str, int]:
    """Client counts per type, within the caller's scope."""
    return await ClientService(session, auth).type_counts()


@router.get("/{client_id}", response_model=ClientRead)
async def get_client(
    client_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.view"))],
    _user: CurrentUser,
) -> ClientRead:
    """One client. 404 when outside the caller's scope — never 403.

    Returning 403 would confirm the record exists, which is an existence oracle
    across tenants and teams.
    """
    return to_read(await ClientService(session, auth).get_client(client_id))


@router.post(
    "",
    response_model=ClientRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_client(
    payload: ClientCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.manage"))],
    user: CurrentUser,
) -> ClientRead:
    client = await ClientService(session, auth).create_client(payload, user)
    response.headers["Location"] = f"/api/v1/clients/{client.id}"
    return to_read(client)


@router.patch(
    "/{client_id}",
    response_model=ClientRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_client(
    client_id: UUID,
    payload: ClientUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.manage"))],
    user: CurrentUser,
) -> ClientRead:
    """Partial update. Only fields present in the body are touched."""
    client = await ClientService(session, auth).update_client(client_id, payload, user)
    return to_read(client)


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_client(
    client_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. The row and its audit trail survive."""
    await ClientService(session, auth).delete_client(client_id, user)


@router.post(
    "/{client_id}/assign",
    response_model=ClientRead,
    dependencies=[Depends(verify_csrf)],
)
async def assign_client(
    client_id: UUID,
    payload: ClientAssign,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.assign"))],
    user: CurrentUser,
) -> ClientRead:
    """Reassign ownership.

    Separate from update because it needs `contacts.assign` — an agent may edit
    their own clients without being able to move work onto a colleague.
    """
    client = await ClientService(session, auth).assign_client(
        client_id, payload.owner_id, user
    )
    return to_read(client)
