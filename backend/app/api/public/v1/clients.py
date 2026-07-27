"""Public client endpoints. Reuses `ClientService`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import CREATED_SORT, PUBLIC_V1_PREFIX, is_ascending
from app.api.v1.clients import to_read
from app.schemas.client import ClientCreate, ClientFilters, ClientRead, ClientUpdate
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.services.client import ClientService

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=Page[ClientRead], summary="List clients")
async def list_clients(
    principal: Annotated[MachinePrincipal, Depends(require_scope("contacts.view"))],
    search: Annotated[str | None, Query(max_length=200)] = None,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    owner_id: UUID | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    sort: CREATED_SORT = "-created_at",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[ClientRead]:
    filters = ClientFilters.model_validate(
        {
            "search": search,
            "type": type_filter,
            "status": status_filter,
            "owner_id": owner_id,
            "tag": tag,
        }
    )
    rows, has_more = await ClientService(
        principal.session, principal.auth
    ).list_clients(
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
    return Page[ClientRead](
        data=[to_read(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get("/{client_id}", response_model=ClientRead, summary="Retrieve a client")
async def get_client(
    client_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("contacts.view"))],
) -> ClientRead:
    return to_read(
        await ClientService(principal.session, principal.auth).get_client(client_id)
    )


@router.post(
    "",
    response_model=ClientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a client",
)
async def create_client(
    payload: ClientCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope("contacts.manage"))],
) -> ClientRead:
    actor = await actor_for(principal)
    client = await ClientService(principal.session, principal.auth).create_client(
        payload, actor
    )
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/clients/{client.id}"
    return to_read(client)


@router.patch("/{client_id}", response_model=ClientRead, summary="Update a client")
async def update_client(
    client_id: UUID,
    payload: ClientUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope("contacts.manage"))],
) -> ClientRead:
    actor = await actor_for(principal)
    client = await ClientService(principal.session, principal.auth).update_client(
        client_id, payload, actor
    )
    return to_read(client)


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a client",
)
async def delete_client(
    client_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope("contacts.manage"))],
) -> None:
    actor = await actor_for(principal)
    await ClientService(principal.session, principal.auth).delete_client(client_id, actor)
