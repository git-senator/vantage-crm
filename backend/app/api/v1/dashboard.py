"""Dashboard endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.schemas.dashboard import DashboardSummary
from app.services.dashboard import DashboardService

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> DashboardSummary:
    """Counts, pipeline value and recent activity — all within the caller's scope."""
    return await DashboardService(session, auth).summary()
