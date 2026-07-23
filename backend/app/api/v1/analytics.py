"""Analytics, dashboards, forecasting and goals.

Every handler resolves its period from the same query parameters, so a caller
can move any screen to any window without learning a second convention. The
period itself is echoed back in the response: a client that renders "this month"
without being told which month is one timezone bug away from lying.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.analytics.metrics import METRICS, METRICS_BY_KEY
from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.exceptions import NotFoundError
from app.schemas.analytics import (
    DashboardResponse,
    ForecastResponse,
    FunnelResponse,
    GoalCreate,
    GoalRead,
    KpiResponse,
    MetricDefinitionRead,
    PeriodName,
    SeriesResponse,
    WinLossResponse,
)
from app.services.analytics import AnalyticsService, Period, resolve_period
from app.services.insights import DASHBOARDS, InsightsService

router = APIRouter()

#: A month is the window most people mean when they say "how are we doing".
DEFAULT_PERIOD: PeriodName = "month"

#: Bounded so one request cannot ask for a series that scans years of snapshots.
MAX_SERIES_DAYS = 365


def get_period(
    period: PeriodName = Query(DEFAULT_PERIOD),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> Period:
    """The window every analytics handler works in.

    A dependency rather than three repeated parameters, so `custom` validation
    happens in exactly one place and every endpoint accepts the same shape.
    """
    return resolve_period(period, start=start, end=end)


PeriodDep = Annotated[Period, Depends(get_period)]


@router.get("/metrics", response_model=list[MetricDefinitionRead])
async def list_metrics(_user: CurrentUser) -> list[MetricDefinitionRead]:
    """The metric catalogue.

    Served from the registry so the frontend never hard-codes a label, a unit,
    or which direction is good — the last of which is how a dashboard ends up
    painting rising overdue tasks green.
    """
    return [
        MetricDefinitionRead(
            key=metric.key,
            label=metric.label,
            description=metric.description,
            kind=metric.kind,
            unit=metric.unit,
            category=metric.category,
            higher_is_better=metric.higher_is_better,
        )
        for metric in METRICS
    ]


@router.get("/kpis", response_model=KpiResponse)
async def kpis(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    period: PeriodDep,
) -> KpiResponse:
    """Headline numbers for the window, each against the previous one."""
    auth.require("reports.view")
    values = await AnalyticsService(session, auth).kpis(period)
    return KpiResponse(
        period=_period_out(period),
        metrics=[
            {
                "key": value.key,
                "label": value.label,
                "unit": value.unit,
                "kind": value.kind,
                "higher_is_better": value.higher_is_better,
                "value": value.value,
                "previous": value.previous,
                "delta_percent": value.delta_percent,
            }
            for value in values
        ],
    )


@router.get("/series/{metric_key}", response_model=SeriesResponse)
async def series(
    metric_key: str,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    days: int = Query(30, ge=1, le=MAX_SERIES_DAYS),
) -> SeriesResponse:
    """One metric over time — snapshots for history, today computed live."""
    auth.require("reports.view")

    metric = METRICS_BY_KEY.get(metric_key)
    if metric is None:
        raise NotFoundError(f"Unknown metric: {metric_key}")

    points = await AnalyticsService(session, auth).series(metric_key, days=days)
    return SeriesResponse(
        metric=MetricDefinitionRead(
            key=metric.key,
            label=metric.label,
            description=metric.description,
            kind=metric.kind,
            unit=metric.unit,
            category=metric.category,
            higher_is_better=metric.higher_is_better,
        ),
        points=[{"date": day, "value": value} for day, value in points],
    )


@router.get("/dashboards", response_model=list[dict[str, str]])
async def list_dashboards(_user: CurrentUser) -> list[dict[str, str]]:
    """Which dashboards exist, so the frontend does not keep its own list."""
    return [{"key": key, "title": title} for key, (title, _) in DASHBOARDS.items()]


@router.get("/dashboards/{key}", response_model=DashboardResponse)
async def dashboard(
    key: str,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    period: PeriodDep,
) -> DashboardResponse:
    """One named dashboard, assembled in a single round trip."""
    payload = await InsightsService(session, auth).dashboard(key, period)
    payload["period"] = _period_out(period)
    return DashboardResponse.model_validate(payload)


@router.get("/funnel", response_model=FunnelResponse)
async def funnel(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    period: PeriodDep,
) -> FunnelResponse:
    """Where deals sit, how long they take, and where they came from."""
    auth.require("reports.view")
    service = InsightsService(session, auth)
    return FunnelResponse.model_validate(
        {
            "stages": await service.pipeline_stages(),
            "velocity": await service.stage_velocity(period),
            "sources": await service.lead_sources(period),
        }
    )


@router.get("/win-loss", response_model=WinLossResponse)
async def win_loss(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    period: PeriodDep,
) -> WinLossResponse:
    auth.require("reports.view")
    return WinLossResponse.model_validate(await InsightsService(session, auth).win_loss(period))


@router.get("/forecast", response_model=ForecastResponse)
async def forecast(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    period: PeriodDep,
) -> ForecastResponse:
    """Booked revenue plus weighted pipeline. Arithmetic, not prediction."""
    auth.require("reports.view")
    payload = await InsightsService(session, auth).forecast(period)
    payload["period"] = _period_out(period)
    return ForecastResponse.model_validate(payload)


@router.get("/goals", response_model=list[GoalRead])
async def list_goals(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[GoalRead]:
    """Goals in force today, with progress measured over each goal's window."""
    rows = await InsightsService(session, auth).list_goals()
    return [GoalRead.model_validate(row) for row in rows]


@router.post("/goals", response_model=GoalRead, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: GoalCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> GoalRead:
    goal = await InsightsService(session, auth).create_goal(
        owner_id=payload.owner_id,
        metric_key=payload.metric_key,
        target_value=payload.target_value,
        period_start=payload.period_start,
        period_end=payload.period_end,
        actor=user,
    )
    await session.commit()
    return GoalRead.model_validate(goal)


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await InsightsService(session, auth).delete_goal(goal_id, user)
    await session.commit()


def _period_out(period: Period) -> dict[str, object]:
    return {"start": period.start, "end": period.end, "label": period.label}
