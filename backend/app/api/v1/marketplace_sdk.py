"""Marketplace SDK & access endpoints (Phase 9.6).

Three groups, mounted under ``/marketplace``:

  * ``/sdk`` — the capability registry, an application's SDK requirements, and a
    compatibility check.
  * ``/access`` — grant, revoke and read an application's access.
  * ``/events`` — the available events and an application's subscriptions.

Reading the capability registry and available events needs only an authenticated
member; every mutation and every application-scoped read is gated on
``settings.manage`` inside its service, which also enforces developer ownership
under RLS.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.marketplace_sdk import (
    AccessGrantRead,
    AccessGrantRequest,
    CompatibilityCheckRequest,
    CompatibilityReportRead,
    EventSubscriptionRead,
    EventSubscriptionRequest,
    SdkApplicationRead,
    SdkCapabilityRead,
    SdkRegisterRequest,
)
from app.services.marketplace_sdk import (
    MarketplaceAccessService,
    MarketplaceEventService,
    MarketplaceSdkService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------- SDK


@router.get("/sdk/capabilities", response_model=list[SdkCapabilityRead])
async def list_capabilities(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[SdkCapabilityRead]:
    return MarketplaceSdkService(session, auth).list_capabilities()


@router.post("/sdk/validate", response_model=CompatibilityReportRead)
async def validate_compatibility(
    payload: CompatibilityCheckRequest,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> CompatibilityReportRead:
    return MarketplaceSdkService(session, auth).validate(
        payload.sdk_version, payload.capabilities
    )


@router.post(
    "/sdk/requirements",
    response_model=SdkApplicationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def register_requirements(
    payload: SdkRegisterRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> SdkApplicationRead:
    result = await MarketplaceSdkService(session, auth).register(
        user, payload.application_id, payload.sdk_version, payload.capabilities
    )
    await session.commit()
    return result


@router.get(
    "/sdk/requirements/{application_id}", response_model=SdkApplicationRead
)
async def application_requirements(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> SdkApplicationRead:
    return await MarketplaceSdkService(session, auth).requirements(application_id)


# ------------------------------------------------------- access


@router.post(
    "/access/grant",
    response_model=AccessGrantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def grant_access(
    payload: AccessGrantRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> AccessGrantRead:
    result = await MarketplaceAccessService(session, auth).grant(
        user, payload.application_id, payload.capabilities
    )
    await session.commit()
    return result


@router.delete(
    "/access/{application_id}", response_model=AccessGrantRead, dependencies=_CSRF
)
async def revoke_access(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> AccessGrantRead:
    result = await MarketplaceAccessService(session, auth).revoke(
        user, application_id
    )
    await session.commit()
    return result


@router.get("/access/{application_id}", response_model=AccessGrantRead)
async def access_status(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> AccessGrantRead:
    return await MarketplaceAccessService(session, auth).status(application_id)


# ------------------------------------------------------- events


@router.get("/events/available", response_model=list[str])
async def available_events(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[str]:
    return MarketplaceEventService(session, auth).available_events()


@router.get(
    "/events/subscriptions", response_model=list[EventSubscriptionRead]
)
async def list_subscriptions(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    application_id: Annotated[UUID, Query()],
) -> list[EventSubscriptionRead]:
    return await MarketplaceEventService(session, auth).list_subscriptions(
        application_id
    )


@router.post(
    "/events/subscriptions",
    response_model=EventSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_subscription(
    payload: EventSubscriptionRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> EventSubscriptionRead:
    result = await MarketplaceEventService(session, auth).subscribe(
        user, payload.application_id, payload.event_name
    )
    await session.commit()
    return result


@router.delete(
    "/events/subscriptions",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def remove_subscription(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    application_id: Annotated[UUID, Query()],
    event_name: Annotated[str, Query(min_length=1, max_length=100)],
) -> None:
    await MarketplaceEventService(session, auth).unsubscribe(
        user, application_id, event_name
    )
    await session.commit()
