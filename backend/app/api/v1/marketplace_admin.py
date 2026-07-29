"""Marketplace administration endpoints (Phase 9.3).

The publisher- and operator-facing surface: authoring a listing draft, moving it
through the publication pipeline (submit, review, publish), managing its versions,
and the marketplace operational analytics. Every handler is gated on
`settings.manage` inside the operations service, and the publication actions
delegate their runtime effects (provisioning a plugin) to the plugin platform.

Mounted under ``/marketplace/admin`` so the administrative surface is distinct
from the tenant-facing ``/marketplace`` catalogue.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.marketplace import (
    IntegrationListingRead,
    IntegrationVersionRead,
    ListingDraftCreate,
    MarketplaceAnalyticsRead,
    ReviewDecisionRequest,
    VersionCreateRequest,
)
from app.services.marketplace_operations import MarketplaceOperationsService

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------- analytics


@router.get("/analytics", response_model=MarketplaceAnalyticsRead)
async def marketplace_analytics(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> MarketplaceAnalyticsRead:
    return await MarketplaceOperationsService(session, auth, settings).analytics()


# ------------------------------------------------------- publication


@router.post(
    "/listings",
    response_model=IntegrationListingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_listing_draft(
    payload: ListingDraftCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationListingRead:
    result = await MarketplaceOperationsService(session, auth, settings).create_draft(
        user, payload
    )
    await session.commit()
    return result


@router.post(
    "/listings/{listing_id}/submit",
    response_model=IntegrationListingRead,
    dependencies=_CSRF,
)
async def submit_listing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationListingRead:
    result = await MarketplaceOperationsService(session, auth, settings).submit(
        user, listing_id
    )
    await session.commit()
    return result


@router.post(
    "/listings/{listing_id}/review",
    response_model=IntegrationListingRead,
    dependencies=_CSRF,
)
async def review_listing(
    listing_id: UUID,
    payload: ReviewDecisionRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationListingRead:
    result = await MarketplaceOperationsService(session, auth, settings).review(
        user, listing_id, payload.decision, payload.evidence
    )
    await session.commit()
    return result


@router.post(
    "/listings/{listing_id}/publish",
    response_model=IntegrationListingRead,
    dependencies=_CSRF,
)
async def publish_listing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationListingRead:
    result = await MarketplaceOperationsService(session, auth, settings).publish(
        user, listing_id
    )
    await session.commit()
    return result


# ------------------------------------------------------- versions


@router.get(
    "/listings/{listing_id}/versions", response_model=list[IntegrationVersionRead]
)
async def list_versions(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[IntegrationVersionRead]:
    return await MarketplaceOperationsService(
        session, auth, settings
    ).list_versions(listing_id)


@router.post(
    "/listings/{listing_id}/versions",
    response_model=IntegrationVersionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_version(
    listing_id: UUID,
    payload: VersionCreateRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationVersionRead:
    result = await MarketplaceOperationsService(session, auth, settings).create_version(
        user, listing_id, payload.version, payload.compatibility
    )
    await session.commit()
    return result
