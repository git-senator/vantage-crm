"""App marketplace & plugin platform endpoints (Phase 9.0).

The marketplace surface: the capability and category registries, the plugin
catalog (browse and publish), the tenant install lifecycle (install, enable,
disable, configure, uninstall), event subscriptions, and the marketplace
dashboard. Browsing the catalog needs only an authenticated member; every
management handler is gated on `settings.manage` inside its service.
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
from app.schemas.plugin import (
    CapabilityRead,
    ConfigureRequest,
    InstallationRead,
    InstallRequest,
    MarketplaceDashboard,
    PluginPublish,
    PluginRead,
    SubscribeRequest,
    SubscriptionRead,
)
from app.services.plugin import (
    MarketplaceDashboardService,
    PluginEventService,
    PluginInstallationService,
    PluginRegistryService,
    capabilities_catalogue,
    plugin_categories,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------ registries & dashboard


@router.get("/capabilities", response_model=list[CapabilityRead])
async def list_capabilities(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[CapabilityRead]:
    return [CapabilityRead(**c) for c in capabilities_catalogue()]


@router.get("/categories", response_model=list[str])
async def list_categories(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[str]:
    return plugin_categories()


@router.get("/dashboard", response_model=MarketplaceDashboard)
async def marketplace_dashboard(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> MarketplaceDashboard:
    return await MarketplaceDashboardService(session, auth).dashboard()


# ------------------------------------------------------------- catalog


@router.get("/catalog", response_model=list[PluginRead])
async def list_catalog(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    category: Annotated[str | None, Query(max_length=20)] = None,
) -> list[PluginRead]:
    return await PluginRegistryService(session, auth).list_catalog(category=category)


@router.post(
    "/catalog",
    response_model=PluginRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def publish_plugin(
    payload: PluginPublish,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> PluginRead:
    result = await PluginRegistryService(session, auth).publish(user, payload.manifest)
    await session.commit()
    return result


@router.post("/catalog/sync-first-party", dependencies=_CSRF)
async def sync_first_party(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> dict[str, int]:
    synced = await PluginRegistryService(session, auth).sync_first_party(user)
    await session.commit()
    return {"synced": synced}


@router.get("/catalog/{plugin_id}", response_model=PluginRead)
async def get_plugin(
    plugin_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> PluginRead:
    return await PluginRegistryService(session, auth).get(plugin_id)


@router.post(
    "/catalog/{plugin_id}/deprecate", response_model=PluginRead, dependencies=_CSRF
)
async def deprecate_plugin(
    plugin_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> PluginRead:
    result = await PluginRegistryService(session, auth).deprecate(user, plugin_id)
    await session.commit()
    return result


# ------------------------------------------------------------- installations


@router.get("/installations", response_model=list[InstallationRead])
async def list_installations(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
    install_status: Annotated[str | None, Query(max_length=12)] = None,
) -> list[InstallationRead]:
    return await PluginInstallationService(session, auth, settings).list_installations(
        status=install_status
    )


@router.post(
    "/installations",
    response_model=InstallationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def install_plugin(
    payload: InstallRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstallationRead:
    result = await PluginInstallationService(session, auth, settings).install(
        user, payload
    )
    await session.commit()
    return result


@router.get("/installations/{installation_id}", response_model=InstallationRead)
async def get_installation(
    installation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> InstallationRead:
    return await PluginInstallationService(session, auth, settings).get(installation_id)


@router.post(
    "/installations/{installation_id}/enable",
    response_model=InstallationRead,
    dependencies=_CSRF,
)
async def enable_installation(
    installation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstallationRead:
    result = await PluginInstallationService(session, auth, settings).enable(
        user, installation_id
    )
    await session.commit()
    return result


@router.post(
    "/installations/{installation_id}/disable",
    response_model=InstallationRead,
    dependencies=_CSRF,
)
async def disable_installation(
    installation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstallationRead:
    result = await PluginInstallationService(session, auth, settings).disable(
        user, installation_id
    )
    await session.commit()
    return result


@router.put(
    "/installations/{installation_id}/config",
    response_model=InstallationRead,
    dependencies=_CSRF,
)
async def configure_installation(
    installation_id: UUID,
    payload: ConfigureRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstallationRead:
    result = await PluginInstallationService(session, auth, settings).configure(
        user, installation_id, payload.config
    )
    await session.commit()
    return result


@router.delete(
    "/installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def uninstall_plugin(
    installation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    await PluginInstallationService(session, auth, settings).uninstall(
        user, installation_id
    )
    await session.commit()


# ------------------------------------------------------------- subscriptions


@router.get(
    "/installations/{installation_id}/subscriptions",
    response_model=list[SubscriptionRead],
)
async def list_subscriptions(
    installation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[SubscriptionRead]:
    return await PluginEventService(session, auth).list_subscriptions(installation_id)


@router.post(
    "/installations/{installation_id}/subscriptions",
    response_model=SubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def subscribe_installation(
    installation_id: UUID,
    payload: SubscribeRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> SubscriptionRead:
    result = await PluginEventService(session, auth).subscribe(
        user, installation_id, payload.event_type
    )
    await session.commit()
    return result


@router.delete(
    "/installations/{installation_id}/subscriptions/{event_type}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def unsubscribe_installation(
    installation_id: UUID,
    event_type: str,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await PluginEventService(session, auth).unsubscribe(
        user, installation_id, event_type
    )
    await session.commit()
