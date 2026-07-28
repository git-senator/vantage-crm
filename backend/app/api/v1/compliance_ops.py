"""Compliance operations endpoints (Phase 8.3).

The compliance officer's surface: the controls registry, the live compliance
status from the checks framework, records of processing activities, evidence
collection, the DSAR workflow (over the existing GDPR pipeline), retention
execution (over the existing sweep), and the audit dashboard. Every handler is
gated on `settings.manage` inside its service.
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
from app.schemas.compliance_ops import (
    ComplianceDashboard,
    ComplianceStatus,
    ControlRead,
    EvidenceCreate,
    EvidenceRead,
    PrivacyRequestCreate,
    PrivacyRequestRead,
    ProcessingActivityCreate,
    ProcessingActivityRead,
    ProcessingActivityUpdate,
    RetentionRunResult,
)
from app.services.compliance_ops import (
    ComplianceDashboardService,
    ComplianceEvidenceService,
    ComplianceStatusService,
    DsarWorkflowService,
    ProcessingActivityService,
    RetentionExecutionService,
    controls_catalogue,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# --------------------------------------------------- controls & status


@router.get("/controls", response_model=list[ControlRead])
async def list_controls(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[ControlRead]:
    return [ControlRead(**c) for c in controls_catalogue()]


@router.get("/status", response_model=ComplianceStatus)
async def compliance_status(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> ComplianceStatus:
    return await ComplianceStatusService(session, auth, settings).status()


@router.get("/dashboard", response_model=ComplianceDashboard)
async def compliance_dashboard(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> ComplianceDashboard:
    return await ComplianceDashboardService(session, auth, settings).dashboard()


# ------------------------------------------------- processing activities


@router.get("/processing-activities", response_model=list[ProcessingActivityRead])
async def list_activities(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[ProcessingActivityRead]:
    return await ProcessingActivityService(session, auth).list_activities()


@router.post(
    "/processing-activities",
    response_model=ProcessingActivityRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_activity(
    payload: ProcessingActivityCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ProcessingActivityRead:
    result = await ProcessingActivityService(session, auth).create(user, payload)
    await session.commit()
    return result


@router.get("/processing-activities/{activity_id}", response_model=ProcessingActivityRead)
async def get_activity(
    activity_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ProcessingActivityRead:
    return await ProcessingActivityService(session, auth).get(activity_id)


@router.put(
    "/processing-activities/{activity_id}",
    response_model=ProcessingActivityRead,
    dependencies=_CSRF,
)
async def update_activity(
    activity_id: UUID,
    payload: ProcessingActivityUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ProcessingActivityRead:
    result = await ProcessingActivityService(session, auth).update(
        user, activity_id, payload
    )
    await session.commit()
    return result


@router.delete(
    "/processing-activities/{activity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_activity(
    activity_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await ProcessingActivityService(session, auth).delete(user, activity_id)
    await session.commit()


# ------------------------------------------------------------ evidence


@router.get("/evidence", response_model=list[EvidenceRead])
async def list_evidence(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    control_key: Annotated[str | None, Query(max_length=80)] = None,
) -> list[EvidenceRead]:
    return await ComplianceEvidenceService(session, auth).list_evidence(
        control_key=control_key
    )


@router.post(
    "/evidence",
    response_model=EvidenceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def collect_evidence(
    payload: EvidenceCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> EvidenceRead:
    result = await ComplianceEvidenceService(session, auth).collect(user, payload)
    await session.commit()
    return result


# ---------------------------------------------------- DSAR workflow


@router.get("/privacy-requests", response_model=list[PrivacyRequestRead])
async def list_privacy_requests(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> list[PrivacyRequestRead]:
    return await DsarWorkflowService(session, auth, settings).list_requests()


@router.post(
    "/privacy-requests",
    response_model=PrivacyRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_CSRF,
)
async def create_privacy_request(
    payload: PrivacyRequestCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> PrivacyRequestRead:
    result = await DsarWorkflowService(session, auth, settings).create(user, payload)
    await session.commit()
    return result


@router.get("/privacy-requests/{request_id}", response_model=PrivacyRequestRead)
async def get_privacy_request(
    request_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> PrivacyRequestRead:
    return await DsarWorkflowService(session, auth, settings).get(request_id)


# ---------------------------------------------------- retention hook


@router.post("/retention/run", response_model=RetentionRunResult, dependencies=_CSRF)
async def run_retention(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> RetentionRunResult:
    """Execute the retention sweep for this tenant now, honouring legal hold."""
    result = await RetentionExecutionService(session, auth, settings).run(user)
    await session.commit()
    return result
