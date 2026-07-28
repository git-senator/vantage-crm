"""Developer SDK endpoints (Phase 9.1).

The developer platform surface: documentation generated from the SDK contract
(version, event contracts, capabilities, hooks, interfaces), a compatibility
check, a manifest validation endpoint, and per-installation diagnostics. Docs are
available to any authenticated member; validation and diagnostics are gated on
`settings.manage` inside their services.
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
from app.schemas.sdk import (
    CapabilityDocRead,
    CompatibilityRead,
    DiagnosticReportRead,
    EventContractRead,
    HookDocRead,
    InterfaceDocRead,
    SdkVersionInfo,
    ValidateRequest,
    ValidationReportRead,
)
from app.services.sdk import (
    PluginDiagnosticsService,
    SdkDocsService,
    SdkValidationService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------- documentation


@router.get("/version", response_model=SdkVersionInfo)
async def sdk_version(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> SdkVersionInfo:
    return SdkDocsService().version_info()


@router.get("/events", response_model=list[EventContractRead])
async def event_contracts(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[EventContractRead]:
    return SdkDocsService().event_docs()


@router.get("/capabilities", response_model=list[CapabilityDocRead])
async def capability_docs(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[CapabilityDocRead]:
    return SdkDocsService().capability_docs()


@router.get("/hooks", response_model=list[HookDocRead])
async def hook_docs(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[HookDocRead]:
    return SdkDocsService().hook_docs()


@router.get("/interfaces", response_model=list[InterfaceDocRead])
async def interface_docs(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[InterfaceDocRead]:
    return SdkDocsService().interface_docs()


@router.get("/compatibility", response_model=CompatibilityRead)
async def check_compatibility(
    _session: TenantSessionDep,
    _auth: Authorization,
    _user: CurrentUser,
    version: Annotated[str, Query(min_length=1, max_length=20)],
) -> CompatibilityRead:
    return SdkDocsService().compatibility(version)


# ------------------------------------------------------- validation


@router.post("/validate", response_model=ValidationReportRead, dependencies=_CSRF)
async def validate_manifest(
    payload: ValidateRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> ValidationReportRead:
    result = await SdkValidationService(session, auth, settings).validate(
        user, payload.manifest
    )
    await session.commit()
    return result


# ------------------------------------------------------- diagnostics


@router.get(
    "/diagnostics/{installation_id}",
    response_model=DiagnosticReportRead,
    status_code=status.HTTP_200_OK,
)
async def plugin_diagnostics(
    installation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> DiagnosticReportRead:
    return await PluginDiagnosticsService(session, auth, settings).diagnose(
        installation_id
    )
