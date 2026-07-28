"""Security operations endpoints (Phase 8.2).

The tenant security dashboard, the event and alert views admins investigate with,
and the ingestion points the auth BFF calls to feed sign-in signals into the
detection framework. Every handler is gated on `settings.manage` inside its
service. Nothing here authenticates a user — it observes and reports.
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
from app.schemas.security_ops import (
    AlertResolve,
    DeviceTrustUpdate,
    LoginRiskRequest,
    LoginRiskResult,
    SecurityAlertRead,
    SecurityDashboard,
    SecurityEventIngest,
    SecurityEventRead,
    TrustedDeviceRead,
)
from app.services.security_ops import (
    DeviceIntelligenceService,
    SecurityAlertService,
    SecurityDashboardService,
    SecurityEventService,
    SecurityOpsService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------------- events


@router.get("/events", response_model=Page[SecurityEventRead])
async def list_events(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    event_type: Annotated[str | None, Query(max_length=50)] = None,
    severity: Annotated[str | None, Query(max_length=10)] = None,
    user_id: Annotated[UUID | None, Query()] = None,
) -> Page[SecurityEventRead]:
    rows, has_more = await SecurityEventService(session, auth).list_events(
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        event_type_filter=event_type,
        severity=severity,
        user_id=user_id,
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[SecurityEventRead](
        data=rows, meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit)
    )


@router.post(
    "/events",
    response_model=SecurityEventRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def ingest_event(
    payload: SecurityEventIngest,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> SecurityEventRead:
    event = await SecurityEventService(session, auth).record(
        event_type_key=payload.event_type,
        user_id=payload.user_id,
        source_ip=payload.source_ip,
        user_agent=payload.user_agent,
        device_fingerprint=payload.device_fingerprint,
        severity=payload.severity,
        details=payload.details,
    )
    await session.commit()
    from app.services.security_ops import _event_read

    return _event_read(event)


@router.post(
    "/login-risk",
    response_model=LoginRiskResult,
    dependencies=_CSRF,
)
async def evaluate_login_risk(
    payload: LoginRiskRequest,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> LoginRiskResult:
    """Score a sign-in, record it, track the device, and raise any alerts."""
    result = await SecurityOpsService(session, auth, settings).evaluate_login(payload)
    await session.commit()
    return result


# ------------------------------------------------------------- alerts


@router.get("/alerts", response_model=Page[SecurityAlertRead])
async def list_alerts(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    alert_status: Annotated[str | None, Query(max_length=16)] = None,
    severity: Annotated[str | None, Query(max_length=10)] = None,
) -> Page[SecurityAlertRead]:
    rows, has_more = await SecurityAlertService(session, auth).list_alerts(
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        status=alert_status,
        severity=severity,
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[SecurityAlertRead](
        data=rows, meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit)
    )


@router.post("/alerts/{alert_id}/acknowledge", response_model=SecurityAlertRead, dependencies=_CSRF)
async def acknowledge_alert(
    alert_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> SecurityAlertRead:
    result = await SecurityAlertService(session, auth).acknowledge(user, alert_id)
    await session.commit()
    return result


@router.post("/alerts/{alert_id}/resolve", response_model=SecurityAlertRead, dependencies=_CSRF)
async def resolve_alert(
    alert_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    _payload: AlertResolve | None = None,
    dismiss: Annotated[bool, Query()] = False,
) -> SecurityAlertRead:
    result = await SecurityAlertService(session, auth).resolve(
        user, alert_id, dismissed=dismiss
    )
    await session.commit()
    return result


# ------------------------------------------------------------ devices


@router.get("/devices", response_model=list[TrustedDeviceRead])
async def list_devices(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    user_id: Annotated[UUID | None, Query()] = None,
) -> list[TrustedDeviceRead]:
    return await DeviceIntelligenceService(session, auth).list_devices(user_id=user_id)


@router.post("/devices/{device_id}/trust", response_model=TrustedDeviceRead, dependencies=_CSRF)
async def set_device_trust(
    device_id: UUID,
    payload: DeviceTrustUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> TrustedDeviceRead:
    result = await DeviceIntelligenceService(session, auth).set_trust(
        user, device_id, trusted=payload.trusted, label=payload.label
    )
    await session.commit()
    return result


# ---------------------------------------------------------- dashboard


@router.get("/dashboard", response_model=SecurityDashboard)
async def security_dashboard(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> SecurityDashboard:
    return await SecurityDashboardService(session, auth).dashboard()
