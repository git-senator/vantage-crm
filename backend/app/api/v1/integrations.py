"""Integration platform endpoints (Phase 7.7).

The signed-in surface a workspace uses to connect, manage and monitor external
integrations. Every handler delegates to `IntegrationService`, which is gated on
`settings.manage` — connecting a workspace to an external service is an egress
decision — and which reuses the encryption, billing, audit and worker
infrastructure rather than re-implementing any of it. Nothing here touches a
repository or a provider directly.
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
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.integration import (
    ConnectionHealth,
    ConnectionRead,
    InstallStart,
    InstallStartResult,
    OAuthCallback,
    ProviderRead,
    SubscriptionRead,
    SubscriptionUpdate,
    SyncRunRead,
    SyncTriggerResult,
)
from app.services.integration import IntegrationService

router = APIRouter()


@router.get("/providers", response_model=list[ProviderRead])
async def list_providers(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[ProviderRead]:
    """The integration catalogue, with each provider's configured/connected state."""
    return await IntegrationService(session, auth, settings).list_providers()


@router.get("/connections", response_model=list[ConnectionRead])
async def list_connections(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[ConnectionRead]:
    return await IntegrationService(session, auth, settings).list_connections()


@router.post(
    "/connections/install",
    response_model=InstallStartResult,
    dependencies=[Depends(verify_csrf)],
)
async def begin_install(
    payload: InstallStart,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> InstallStartResult:
    """Start the OAuth handshake. Returns the provider's consent URL and a state."""
    result = await IntegrationService(session, auth, settings).begin_install(
        user,
        provider=payload.provider,
        redirect_uri=payload.redirect_uri,
        scopes=payload.scopes,
    )
    await session.commit()
    return result


@router.post(
    "/connections/callback",
    response_model=ConnectionRead,
    dependencies=[Depends(verify_csrf)],
)
async def complete_install(
    payload: OAuthCallback,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> ConnectionRead:
    """Complete the handshake: exchange the code and activate the connection."""
    connection = await IntegrationService(session, auth, settings).complete_install(
        user, state=payload.state, code=payload.code, redirect_uri=payload.redirect_uri
    )
    await session.commit()
    return connection


@router.get("/connections/{connection_id}", response_model=ConnectionRead)
async def get_connection(
    connection_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> ConnectionRead:
    return await IntegrationService(session, auth, settings).get_connection(
        connection_id
    )


@router.delete(
    "/connections/{connection_id}",
    response_model=ConnectionRead,
    dependencies=[Depends(verify_csrf)],
)
async def disconnect(
    connection_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> ConnectionRead:
    """Revoke at the provider and tear the connection down."""
    connection = await IntegrationService(session, auth, settings).disconnect(
        user, connection_id
    )
    await session.commit()
    return connection


@router.get(
    "/connections/{connection_id}/subscriptions",
    response_model=list[SubscriptionRead],
)
async def list_subscriptions(
    connection_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[SubscriptionRead]:
    return await IntegrationService(session, auth, settings).list_subscriptions(
        connection_id
    )


@router.put(
    "/connections/{connection_id}/subscriptions",
    response_model=list[SubscriptionRead],
    dependencies=[Depends(verify_csrf)],
)
async def set_subscriptions(
    connection_id: UUID,
    payload: SubscriptionUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> list[SubscriptionRead]:
    """Replace the connection's event subscriptions with the given set."""
    result = await IntegrationService(session, auth, settings).set_subscriptions(
        user, connection_id, payload.event_types
    )
    await session.commit()
    return result


@router.post(
    "/connections/{connection_id}/sync",
    response_model=SyncTriggerResult,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_csrf)],
)
async def trigger_sync(
    connection_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> SyncTriggerResult:
    """Queue a manual sync. The run is processed by the worker."""
    result = await IntegrationService(session, auth, settings).trigger_sync(
        connection_id, trigger="manual"
    )
    await session.commit()
    return result


@router.get(
    "/connections/{connection_id}/runs",
    response_model=Page[SyncRunRead],
)
async def list_runs(
    connection_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[SyncRunRead]:
    """A connection's sync history, newest first."""
    rows, has_more = await IntegrationService(session, auth, settings).list_runs(
        connection_id,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[SyncRunRead](
        data=rows,
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get(
    "/connections/{connection_id}/health",
    response_model=ConnectionHealth,
)
async def connection_health(
    connection_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> ConnectionHealth:
    """A connection's current health and its run outcome counts."""
    return await IntegrationService(session, auth, settings).health(connection_id)
