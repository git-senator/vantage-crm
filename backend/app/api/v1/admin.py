"""Administration and operational monitoring.

Every endpoint requires `settings.manage` and is read-only. There is no
"retry", "resend" or "rescan" here — see `app/services/admin.py` for why.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep, require
from app.schemas.admin import (
    AdminOverview,
    AuditAnalytics,
    EmailDelivery,
    JobHistoryRow,
    NotificationDelivery,
    QueueStatus,
    StorageUsage,
    SystemHealth,
)
from app.schemas.observability import TenantUsage
from app.services.admin import DEFAULT_WINDOW_HOURS, AdminService
from app.services.observability import UsageService

router = APIRouter()

#: A week is as far back as these panels go. Beyond that the question is a
#: reporting question, and reporting is a different feature with a different
#: cost model.
MAX_WINDOW_HOURS = 168

WindowHours = Query(DEFAULT_WINDOW_HOURS, ge=1, le=MAX_WINDOW_HOURS)


@router.get("/overview", response_model=AdminOverview)
async def overview(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    hours: int = WindowHours,
) -> AdminOverview:
    """The whole dashboard in one round trip."""
    return AdminOverview.model_validate(await AdminService(session, auth).overview(hours=hours))


@router.get("/health", response_model=SystemHealth)
async def system_health(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> SystemHealth:
    """Dependency reachability, plus whether the work is actually happening.

    Distinct from `/health/ready`, which answers whether this instance should
    take traffic. A process can be perfectly ready while no worker has run a job
    since Sunday.
    """
    return SystemHealth.model_validate(await AdminService(session, auth).system_health())


@router.get("/queue", response_model=QueueStatus)
async def queue_status(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> QueueStatus:
    return QueueStatus.model_validate(await AdminService(session, auth).queue_health())


@router.get("/jobs", response_model=list[JobHistoryRow])
async def job_history(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    hours: int = WindowHours,
) -> list[JobHistoryRow]:
    """Failures grouped by job name — which job is broken, not forty copies."""
    rows = await AdminService(session, auth).job_history(hours=hours)
    return [JobHistoryRow.model_validate(row) for row in rows]


@router.get("/storage", response_model=StorageUsage)
async def storage_usage(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> StorageUsage:
    return StorageUsage.model_validate(await AdminService(session, auth).storage_usage())


@router.get("/email", response_model=EmailDelivery)
async def email_delivery(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    hours: int = WindowHours,
) -> EmailDelivery:
    return EmailDelivery.model_validate(
        await AdminService(session, auth).email_delivery(hours=hours)
    )


@router.get("/notifications", response_model=NotificationDelivery)
async def notification_delivery(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    hours: int = WindowHours,
) -> NotificationDelivery:
    return NotificationDelivery.model_validate(
        await AdminService(session, auth).notification_delivery(hours=hours)
    )


@router.get("/audit", response_model=AuditAnalytics)
async def audit_analytics(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    hours: int = WindowHours,
) -> AuditAnalytics:
    """Event volumes, top actors, and — the number to alert on — denials."""
    return AuditAnalytics.model_validate(
        await AdminService(session, auth).audit_analytics(hours=hours)
    )


@router.get(
    "/usage",
    response_model=TenantUsage,
    dependencies=[Depends(require("settings.manage"))],
)
async def tenant_usage(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> TenantUsage:
    """This tenant's month-to-date usage, read from the durable record.

    AI spend from the ledger, API-key counts, and webhook delivery totals — the
    per-tenant view the live `/metrics` scrape cannot answer across a restart.
    """
    return TenantUsage.model_validate(await UsageService(session, auth).tenant_usage())
