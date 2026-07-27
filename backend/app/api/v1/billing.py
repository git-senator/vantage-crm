"""Billing endpoints.

Plans and entitlements are readable by any authenticated user — the app needs
them to render an upgrade page and to gate optional features. Everything that
changes what a tenant pays (subscribe, seats, cancel, portal, invoices, usage)
requires `settings.manage`, like the rest of the admin surface.

The provider webhook is the exception: no session, no CSRF. It is authenticated
by the provider's signature and routed to a tenant by the `organization_id` the
service put in the subscription's metadata — so a signed event can only ever be
applied to the organization it belongs to.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.db.session import get_session_factory, set_tenant_context
from app.schemas.billing import (
    CancelRequest,
    EntitlementsRead,
    InvoiceRead,
    PlanRead,
    PortalSession,
    SeatUpdate,
    SubscribeRequest,
    SubscriptionRead,
    UsageRead,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.services.billing import get_billing_provider
from app.services.billing.service import (
    EntitlementService,
    SubscriptionService,
    UsageMeter,
)
from app.workers.context import system_context

router = APIRouter()

_MANAGE = Depends(require("settings.manage"))


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[PlanRead]:
    plans = await EntitlementService(session, auth).list_plans()
    return [PlanRead.model_validate(plan) for plan in plans]


@router.get("/entitlements", response_model=EntitlementsRead)
async def entitlements(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> EntitlementsRead:
    """The tenant's effective plan, features, and grace state — for feature gating."""
    return EntitlementsRead.model_validate(
        await EntitlementService(session, auth).summary()
    )


@router.get("/subscription", response_model=SubscriptionRead, dependencies=[_MANAGE])
async def get_subscription(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> SubscriptionRead:
    """The current subscription. 404 when the workspace has none yet."""
    from app.core.exceptions import NotFoundError

    subscription = await SubscriptionService(session, auth, settings).get_current()
    if subscription is None:
        raise NotFoundError("This workspace has no subscription.")
    return SubscriptionRead.model_validate(subscription)


@router.post(
    "/subscription",
    response_model=SubscriptionRead,
    dependencies=[_MANAGE, Depends(verify_csrf)],
)
async def subscribe(
    payload: SubscribeRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> SubscriptionRead:
    """Subscribe, or move an existing subscription onto a new plan/seat count."""
    subscription = await SubscriptionService(session, auth, settings).subscribe(
        user, plan_key=payload.plan_key, seats=payload.seats
    )
    return SubscriptionRead.model_validate(subscription)


@router.post(
    "/subscription/seats",
    response_model=SubscriptionRead,
    dependencies=[_MANAGE, Depends(verify_csrf)],
)
async def update_seats(
    payload: SeatUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> SubscriptionRead:
    subscription = await SubscriptionService(session, auth, settings).set_seats(
        user, payload.seats
    )
    return SubscriptionRead.model_validate(subscription)


@router.post(
    "/subscription/cancel",
    response_model=SubscriptionRead,
    dependencies=[_MANAGE, Depends(verify_csrf)],
)
async def cancel(
    payload: CancelRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> SubscriptionRead:
    subscription = await SubscriptionService(session, auth, settings).cancel(
        user, at_period_end=payload.at_period_end
    )
    return SubscriptionRead.model_validate(subscription)


@router.get("/usage", response_model=UsageRead, dependencies=[_MANAGE])
async def usage(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> UsageRead:
    """Month-to-date usage: seats, AI spend, API calls, storage."""
    return UsageRead.model_validate(await UsageMeter(session, auth, settings).summary())


@router.get("/invoices", response_model=Page[InvoiceRead], dependencies=[_MANAGE])
async def list_invoices(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[InvoiceRead]:
    rows, has_more = await SubscriptionService(session, auth, settings).list_invoices(
        limit=limit, cursor=Cursor.decode(cursor) if cursor else None
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[InvoiceRead](
        data=[InvoiceRead.model_validate(row) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.post(
    "/portal", response_model=PortalSession, dependencies=[_MANAGE, Depends(verify_csrf)]
)
async def billing_portal(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> PortalSession:
    """A provider-hosted billing portal URL for managing payment and invoices."""
    url = await SubscriptionService(session, auth, settings).portal_url()
    return PortalSession(url=url)


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def billing_webhook(request: Request, settings: SettingsDep) -> Response:
    """Apply a provider webhook event to the tenant it belongs to.

    Authenticated by the provider's signature, not a session. The tenant comes
    from the event metadata, so a signed event can only touch its own workspace.
    """
    provider = get_billing_provider(settings)
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    event = provider.verify_event(payload, signature)

    if event.organization_id is not None:
        organization_id: UUID = event.organization_id
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await set_tenant_context(session, organization_id)
            auth = system_context(organization_id, "settings.manage")
            await SubscriptionService(session, auth, settings).apply_event(event)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
