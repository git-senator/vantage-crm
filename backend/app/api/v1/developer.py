"""Developer portal endpoints (Phase 7.6).

The signed-in surface a workspace's developers use to build against the public
API: the getting-started overview, the OpenAPI document, copy-paste quickstarts,
generated SDK downloads, a usage dashboard, their API keys split by environment,
and webhook management. Every handler delegates to a service that reuses the
existing platform — nothing here touches a repository.

The docs surfaces (overview, openapi, quickstart, sdks) are readable by any
authenticated member: they describe the public contract, not tenant data. The
administrative surfaces (usage, keys, webhooks) are gated on `settings.manage`
*inside the services they call*, so the gate lives in one place.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.public.v1.params import PUBLIC_V1_PREFIX
from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.developer import (
    ApiKeyGroups,
    DeveloperOverview,
    QuickstartRead,
    SdkPackageRead,
    SdkTargetRead,
)
from app.schemas.webhook import WebhookCreate, WebhookCreated, WebhookRead
from app.services.developer import DeveloperService
from app.services.observability import UsageService
from app.services.webhook import WebhookService

router = APIRouter()


def _base_url(request: Request) -> str:
    """Where this instance serves the public API, derived from the request host
    so a snippet or SDK is runnable against the instance the caller is on."""
    return str(request.base_url).rstrip("/") + PUBLIC_V1_PREFIX


def _webhook_created(endpoint: Any, secret: str) -> WebhookCreated:
    return WebhookCreated(
        **WebhookRead.model_validate(endpoint).model_dump(), secret=secret
    )


# --------------------------------------------------------------- docs surface


@router.get("/overview", response_model=DeveloperOverview)
async def developer_overview(
    request: Request,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> DeveloperOverview:
    """Getting-started state: API coordinates, entitlements, onboarding, SDKs."""
    return await DeveloperService(session, auth, settings).overview(
        base_url=_base_url(request)
    )


@router.get("/openapi.json")
async def developer_openapi(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> dict[str, Any]:
    """The public API's OpenAPI document, for tooling and the API explorer."""
    return DeveloperService(session, auth, settings).openapi()


@router.get("/quickstart", response_model=QuickstartRead)
async def developer_quickstart(
    request: Request,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> QuickstartRead:
    """Runnable first-call snippets (curl, TypeScript, Python)."""
    return DeveloperService(session, auth, settings).quickstart(
        base_url=_base_url(request)
    )


@router.get("/sdks", response_model=list[SdkTargetRead])
async def list_sdks(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[SdkTargetRead]:
    """The available SDK targets and the API version they generate against."""
    return DeveloperService(session, auth, settings).sdk_targets()


@router.get("/sdks/{language}", response_model=SdkPackageRead)
async def download_sdk(
    language: str,
    request: Request,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> SdkPackageRead:
    """A complete, versioned SDK package for `language`, generated on demand."""
    return DeveloperService(session, auth, settings).generate_sdk(
        language, base_url=_base_url(request)
    )


# ----------------------------------------------------------- admin surface


@router.get("/usage")
async def developer_usage(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Per-tenant usage dashboard: AI spend, API keys, and webhook deliveries."""
    return await UsageService(session, auth).tenant_usage()


@router.get("/api-keys", response_model=ApiKeyGroups)
async def developer_api_keys(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> ApiKeyGroups:
    """The workspace's keys, split into live and sandbox panels."""
    return await DeveloperService(session, auth, settings).api_keys()


# --------------------------------------------------------- webhook management


@router.get("/webhooks", response_model=list[WebhookRead])
async def list_developer_webhooks(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[WebhookRead]:
    endpoints = await WebhookService(session, auth).list_endpoints()
    return [WebhookRead.model_validate(row) for row in endpoints]


@router.post(
    "/webhooks",
    response_model=WebhookCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_developer_webhook(
    payload: WebhookCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> WebhookCreated:
    endpoint, secret = await WebhookService(session, auth).create(
        user, name=payload.name, url=payload.url, event_types=payload.event_types
    )
    await session.commit()
    return _webhook_created(endpoint, secret)


@router.post(
    "/webhooks/{webhook_id}/rotate-secret",
    response_model=WebhookCreated,
    dependencies=[Depends(verify_csrf)],
)
async def rotate_developer_webhook_secret(
    webhook_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> WebhookCreated:
    endpoint, secret = await WebhookService(session, auth).rotate_secret(
        user, webhook_id
    )
    await session.commit()
    return _webhook_created(endpoint, secret)


@router.delete(
    "/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_developer_webhook(
    webhook_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await WebhookService(session, auth).delete(user, webhook_id)
    await session.commit()
