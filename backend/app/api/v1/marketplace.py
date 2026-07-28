"""Integration marketplace endpoints (Phase 9.2).

The marketplace surface: the category and certification vocabularies, listing
discovery (browse and filter), the curated-template sync, the installation
workflow (install, enable, disable, uninstall), per-integration health, and the
marketplace dashboard. Browsing the catalog needs only an authenticated member;
every management handler is gated on ``settings.manage`` inside its service.

Installing an integration is installing its plugin — these handlers delegate to
the plugin platform through the marketplace services rather than restating the
lifecycle.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.marketplace import (
    CertificationTierRead,
    InstalledIntegrationRead,
    InstallListingRequest,
    IntegrationCategoryRead,
    IntegrationHealthRead,
    IntegrationListingRead,
    MarketplaceOverview,
)
from app.services.marketplace import (
    IntegrationHealthService,
    IntegrationInstallationService,
    IntegrationRegistryService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------ vocabularies & dashboard


@router.get("/categories", response_model=list[IntegrationCategoryRead])
async def list_categories(
    _session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[IntegrationCategoryRead]:
    return IntegrationRegistryService(_session, auth).categories()


@router.get("/certifications", response_model=list[CertificationTierRead])
async def list_certifications(
    _session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[CertificationTierRead]:
    return IntegrationRegistryService(_session, auth).certification_tiers()


@router.get("/dashboard", response_model=MarketplaceOverview)
async def marketplace_dashboard(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> MarketplaceOverview:
    return await IntegrationRegistryService(session, auth).dashboard()


@router.get("/installed", response_model=list[InstalledIntegrationRead])
async def list_installed(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[InstalledIntegrationRead]:
    return await IntegrationInstallationService(
        session, auth, settings
    ).list_installed()


@router.post("/sync", dependencies=_CSRF)
async def sync_templates(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> dict[str, int]:
    synced = await IntegrationRegistryService(session, auth).sync_templates(user)
    await session.commit()
    return {"synced": synced}


# ------------------------------------------------------------- listings


@router.get("/listings", response_model=list[IntegrationListingRead])
async def discover_listings(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    category: Annotated[str | None, Query(max_length=30)] = None,
    certification: Annotated[str | None, Query(max_length=16)] = None,
    auth_method: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> list[IntegrationListingRead]:
    return await IntegrationRegistryService(session, auth).discover(
        category=category,
        certification=certification,
        auth_method=auth_method,
        query=q,
    )


@router.get("/listings/{listing_id}", response_model=IntegrationListingRead)
async def get_listing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> IntegrationListingRead:
    return await IntegrationRegistryService(session, auth).get(listing_id)


@router.get("/listings/{listing_id}/health", response_model=IntegrationHealthRead)
async def listing_health(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationHealthRead:
    return await IntegrationHealthService(session, auth, settings).health(listing_id)


@router.post(
    "/listings/{listing_id}/install",
    response_model=InstalledIntegrationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def install_listing(
    listing_id: UUID,
    payload: InstallListingRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstalledIntegrationRead:
    result = await IntegrationInstallationService(session, auth, settings).install(
        user, listing_id, payload
    )
    await session.commit()
    return result


@router.post(
    "/listings/{listing_id}/enable",
    response_model=InstalledIntegrationRead,
    dependencies=_CSRF,
)
async def enable_listing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstalledIntegrationRead:
    result = await IntegrationInstallationService(session, auth, settings).enable(
        user, listing_id
    )
    await session.commit()
    return result


@router.post(
    "/listings/{listing_id}/disable",
    response_model=InstalledIntegrationRead,
    dependencies=_CSRF,
)
async def disable_listing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstalledIntegrationRead:
    result = await IntegrationInstallationService(session, auth, settings).disable(
        user, listing_id
    )
    await session.commit()
    return result


@router.delete(
    "/listings/{listing_id}/install",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def uninstall_listing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    await IntegrationInstallationService(session, auth, settings).uninstall(
        user, listing_id
    )
    await session.commit()
