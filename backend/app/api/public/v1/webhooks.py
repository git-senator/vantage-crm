"""Public webhook management endpoints (Phase 7.3).

Machine-authenticated, gated on `settings.manage` in the service — pointing a
tenant's events at an external URL is an egress decision. Writes accept an
`Idempotency-Key` like the rest of the public API (the sub-app middleware), and
errors are the shared RFC 9457 problem documents. The secret is returned once,
on create and rotate.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.public.v1.dependencies import MachinePrincipal, actor_for, require_scope
from app.api.public.v1.params import PUBLIC_V1_PREFIX
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.webhook import (
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookUpdate,
)
from app.services.webhook import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_MANAGE = "settings.manage"


def _created(endpoint, secret: str) -> WebhookCreated:  # type: ignore[no-untyped-def]
    return WebhookCreated(**WebhookRead.model_validate(endpoint).model_dump(), secret=secret)


@router.get("", response_model=list[WebhookRead], summary="List webhook endpoints")
async def list_webhooks(
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
) -> list[WebhookRead]:
    endpoints = await WebhookService(
        principal.session, principal.auth
    ).list_endpoints()
    return [WebhookRead.model_validate(row) for row in endpoints]


@router.post(
    "",
    response_model=WebhookCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook endpoint",
)
async def create_webhook(
    payload: WebhookCreate,
    response: Response,
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
) -> WebhookCreated:
    actor = await actor_for(principal)
    endpoint, secret = await WebhookService(principal.session, principal.auth).create(
        actor, name=payload.name, url=payload.url, event_types=payload.event_types
    )
    response.headers["Location"] = f"{PUBLIC_V1_PREFIX}/webhooks/{endpoint.id}"
    return _created(endpoint, secret)


@router.get(
    "/{webhook_id}", response_model=WebhookRead, summary="Retrieve a webhook endpoint"
)
async def get_webhook(
    webhook_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
) -> WebhookRead:
    endpoint = await WebhookService(principal.session, principal.auth).get_endpoint(
        webhook_id
    )
    return WebhookRead.model_validate(endpoint)


@router.patch(
    "/{webhook_id}", response_model=WebhookRead, summary="Update a webhook endpoint"
)
async def update_webhook(
    webhook_id: UUID,
    payload: WebhookUpdate,
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
) -> WebhookRead:
    actor = await actor_for(principal)
    endpoint = await WebhookService(principal.session, principal.auth).update(
        actor,
        webhook_id,
        name=payload.name,
        url=payload.url,
        event_types=payload.event_types,
        is_active=payload.is_active,
    )
    return WebhookRead.model_validate(endpoint)


@router.post(
    "/{webhook_id}/rotate-secret",
    response_model=WebhookCreated,
    summary="Rotate a webhook signing secret",
)
async def rotate_webhook_secret(
    webhook_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
) -> WebhookCreated:
    actor = await actor_for(principal)
    endpoint, secret = await WebhookService(
        principal.session, principal.auth
    ).rotate_secret(actor, webhook_id)
    return _created(endpoint, secret)


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook endpoint",
)
async def delete_webhook(
    webhook_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
) -> None:
    actor = await actor_for(principal)
    await WebhookService(principal.session, principal.auth).delete(actor, webhook_id)


@router.get(
    "/{webhook_id}/deliveries",
    response_model=Page[WebhookDeliveryRead],
    summary="List a webhook's delivery history",
)
async def list_deliveries(
    webhook_id: UUID,
    principal: Annotated[MachinePrincipal, Depends(require_scope(_MANAGE))],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[WebhookDeliveryRead]:
    rows, has_more = await WebhookService(
        principal.session, principal.auth
    ).list_deliveries(
        webhook_id,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[WebhookDeliveryRead](
        data=[WebhookDeliveryRead.model_validate(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )
