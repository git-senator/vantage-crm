"""Composed dashboards, business analytics, forecasting and goals.

Sits on `AnalyticsService` rather than beside it: every panel here is a
different arrangement of the same scoped aggregates, and a second path to the
same numbers is a second path that can disagree with the first.

**Dashboards are assembled server-side.** A client making eight calls to build
one screen re-resolves the caller's team membership eight times and can render
panels from two different moments. One call, one period, one scope resolution.

**Forecasting is arithmetic, not prediction.** `booked + weighted pipeline`,
where the weight is each deal's own probability. No regression, no seasonality,
no confidence interval — those need history this product does not have yet, and
a forecast that looks statistical while being a guess is worse than one that is
visibly a sum. When there is a year of snapshots to fit against, this is the
function that changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import METRICS_BY_KEY
from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.analytics import Goal
from app.models.user import User
from app.services.analytics import AnalyticsService, MetricValue, Period
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

#: The named dashboards, and which metrics each leads with. Declared rather than
#: hard-coded into handlers so the set is readable in one place and the frontend
#: can be told what exists.
DASHBOARDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "executive": (
        "Executive",
        (
            "revenue_won",
            "pipeline_weighted_value",
            "deals_won",
            "win_rate",
            "leads_created",
            "lead_conversion_rate",
        ),
    ),
    "sales": (
        "Sales",
        (
            "deals_created",
            "deals_won",
            "deals_lost",
            "win_rate",
            "average_deal_value",
            "sales_cycle_days",
        ),
    ),
    "pipeline": (
        "Pipeline",
        (
            "pipeline_open_value",
            "pipeline_weighted_value",
            "deals_created",
            "sales_cycle_days",
        ),
    ),
    "agents": (
        "Agent performance",
        (
            "deals_won",
            "revenue_won",
            "leads_converted",
            "activities_logged",
            "tasks_completed",
        ),
    ),
    "revenue": (
        "Revenue",
        (
            "revenue_won",
            "average_deal_value",
            "listings_sold",
            "pipeline_weighted_value",
        ),
    ),
    "forecast": (
        "Forecast",
        ("pipeline_weighted_value", "pipeline_open_value", "revenue_won"),
    ),
}


class InsightsService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.analytics = AnalyticsService(session, auth)
        self.audit = AuditService(session)

    # -------------------------------------------------------- dashboards

    async def dashboard(self, key: str, period: Period) -> dict[str, Any]:
        """One named dashboard: its metrics plus whichever panels it needs.

        Panels are attached per dashboard rather than every dashboard returning
        everything: the agent view has no use for loss reasons, and computing
        them anyway is six aggregate queries a nobody reads.
        """
        self.auth.require("reports.view")

        definition = DASHBOARDS.get(key)
        if definition is None:
            raise NotFoundError("Unknown dashboard.")
        title, metric_keys = definition

        all_metrics = await self.analytics.kpis(period)
        wanted = {metric.key for metric in all_metrics} & set(metric_keys)
        # Ordered by the dashboard's own list, not by the registry: the first
        # tile is the one the dashboard is about.
        metrics = [
            metric
            for metric_key in metric_keys
            for metric in all_metrics
            if metric.key == metric_key and metric_key in wanted
        ]

        payload: dict[str, Any] = {
            "key": key,
            "title": title,
            "period": period,
            "metrics": metrics,
        }

        if key in ("executive", "pipeline", "sales", "forecast"):
            payload["stages"] = await self.pipeline_stages()
        if key in ("executive", "pipeline", "sales"):
            payload["velocity"] = await self.stage_velocity(period)
        if key in ("executive", "sales"):
            payload["reasons"] = (await self.win_loss(period))["reasons"]
        if key in ("executive", "agents", "revenue"):
            payload["agents"] = await self.agent_performance(period)
        if key in ("executive",):
            payload["sources"] = await self.lead_sources(period)
        if key in ("executive", "forecast", "revenue"):
            payload["forecast"] = await self.forecast(period)

        return payload

    # -------------------------------------------------- business analytics

    async def pipeline_stages(self) -> list[dict[str, Any]]:
        scope = await self.analytics.scope_for("deals.view")
        if scope is False:
            return []
        rows = await self.analytics.repo.pipeline_by_stage(self.auth.organization_id, scope)
        return [
            {
                "stage_id": stage_id,
                "stage_name": name,
                "deal_count": count,
                "value": value,
            }
            for stage_id, name, count, value in rows
        ]

    async def stage_velocity(self, period: Period) -> list[dict[str, Any]]:
        """Mean time in each stage, from the durations the transitions recorded.

        Not scoped by owner, deliberately: velocity is a property of the
        *pipeline*, and slicing it to one agent's deals produces a number so
        noisy it misleads. A caller who cannot see deals at all gets nothing.
        """
        if await self.analytics.scope_for("deals.view") is False:
            return []
        rows = await self.analytics.repo.stage_velocity(
            self.auth.organization_id, period.start, period.end
        )
        return [
            {
                "stage_id": stage_id,
                "stage_name": name,
                "mean_days": days,
                "transitions": transitions,
            }
            for stage_id, name, days, transitions in rows
        ]

    async def win_loss(self, period: Period) -> dict[str, Any]:
        scope = await self.analytics.scope_for("deals.view")
        if scope is False:
            return {"won": 0, "lost": 0, "win_rate": None, "reasons": []}

        won, lost, _revenue = await self.analytics.repo.closed_deal_stats(
            self.auth.organization_id, scope, period.start, period.end
        )
        reasons = await self.analytics.repo.loss_reasons(
            self.auth.organization_id, scope, period.start, period.end
        )
        closed = won + lost
        return {
            "won": won,
            "lost": lost,
            # None, not zero, when nothing closed: a win rate over no deals is
            # undefined, and 0% reads as "we lost everything".
            "win_rate": (Decimal(str(round(won / closed * 100, 1))) if closed else None),
            "reasons": [
                {"reason": reason, "count": count, "value": value}
                for reason, count, value in reasons
            ],
        }

    async def lead_sources(self, period: Period) -> list[dict[str, Any]]:
        scope = await self.analytics.scope_for("leads.view")
        if scope is False:
            return []
        rows = await self.analytics.repo.lead_sources(
            self.auth.organization_id, scope, period.start, period.end
        )
        return [
            {
                "source": source,
                "created": created,
                "converted": converted,
                "conversion_rate": (
                    Decimal(str(round(converted / created * 100, 1))) if created else None
                ),
            }
            for source, created, converted in rows
        ]

    async def agent_performance(self, period: Period) -> list[dict[str, Any]]:
        scope = await self.analytics.scope_for("deals.view")
        if scope is False:
            return []
        rows = await self.analytics.repo.agent_leaderboard(
            self.auth.organization_id, scope, period.start, period.end
        )
        return [
            {
                "user_id": user_id,
                "full_name": name,
                "deals_won": won,
                "revenue": revenue,
                "leads_converted": converted,
            }
            for user_id, name, won, revenue, converted in rows
        ]

    # ---------------------------------------------------------- forecast

    async def forecast(self, period: Period) -> dict[str, Any]:
        """Booked revenue plus weighted open pipeline.

        Deliberately simple arithmetic. The weight is each deal's own
        probability rather than its stage default, because a probability that
        has been overridden is somebody telling you something the stage does not
        know.

        `previous_actual` is the same-length trailing window, so "ahead or
        behind" compares like with like rather than against a period with a
        different number of trading days.
        """
        scope = await self.analytics.scope_for("deals.view")
        if scope is False:
            return {
                "period": period,
                "booked": Decimal(0),
                "weighted_pipeline": Decimal(0),
                "projected": Decimal(0),
                "previous_actual": None,
                "breakdown": [],
            }

        organization = self.auth.organization_id
        _won, _lost, booked = await self.analytics.repo.closed_deal_stats(
            organization, scope, period.start, period.end
        )
        _open_value, weighted = await self.analytics.repo.open_pipeline(organization, scope)

        previous = period.previous
        _pwon, _plost, previous_actual = await self.analytics.repo.closed_deal_stats(
            organization, scope, previous.start, previous.end
        )

        stages = await self.analytics.repo.pipeline_by_stage(organization, scope)

        return {
            "period": period,
            "booked": booked,
            "weighted_pipeline": weighted,
            "projected": booked + weighted,
            "previous_actual": previous_actual,
            # Per stage, so a reader can see which part of the pipeline the
            # projection is leaning on rather than trusting one number.
            "breakdown": [
                {"label": name, "weighted": value, "committed": value}
                for _stage_id, name, _count, value in stages
            ],
        }

    # -------------------------------------------------------------- goals

    async def list_goals(self, *, on: date | None = None) -> list[dict[str, Any]]:
        """Goals overlapping a date, with progress against the live metric.

        Progress is computed from the same scoped aggregate the dashboards use,
        so a goal card and a KPI tile can never disagree about the same number.
        """
        self.auth.require("reports.view")
        moment = on or datetime.now(UTC).date()

        query = (
            select(Goal)
            .where(Goal.organization_id == self.auth.organization_id)
            .where(Goal.period_start <= moment)
            .where(Goal.period_end >= moment)
            .order_by(Goal.period_start.desc())
        )
        goals = list((await self.session.execute(query)).scalars().all())

        out: list[dict[str, Any]] = []
        for goal in goals:
            current = await self._goal_progress(goal)
            out.append(
                {
                    "id": goal.id,
                    "owner_id": goal.owner_id,
                    "metric_key": goal.metric_key,
                    "target_value": goal.target_value,
                    "period_start": goal.period_start,
                    "period_end": goal.period_end,
                    "current_value": current,
                    "percent_complete": (
                        Decimal(str(round(float(current / goal.target_value * 100), 1)))
                        if current is not None and goal.target_value
                        else None
                    ),
                }
            )
        return out

    async def _goal_progress(self, goal: Goal) -> Decimal | None:
        """Where this goal stands, measured over its own window."""
        metric = METRICS_BY_KEY.get(goal.metric_key)
        if metric is None:
            return None

        window = Period(
            datetime.combine(goal.period_start, datetime.min.time(), tzinfo=UTC),
            min(
                datetime.combine(
                    goal.period_end + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                ),
                datetime.now(UTC),
            ),
            "goal period",
        )
        if window.end <= window.start:
            return None

        values = await self.analytics.kpis(window, compare=False)
        for value in values:
            if value.key == goal.metric_key:
                return value.value
        return None

    async def create_goal(
        self,
        *,
        owner_id: UUID | None,
        metric_key: str,
        target_value: Decimal,
        period_start: date,
        period_end: date,
        actor: User,
    ) -> Goal:
        """Set a target. Requires `reports.export`, the closest thing to an
        "analytics admin" grant the permission matrix has — setting a team's
        number is a management act, not a reading one."""
        self.auth.require("reports.export")

        if metric_key not in METRICS_BY_KEY:
            raise ConflictError(f"Unknown metric: {metric_key}")
        if period_end < period_start:
            raise ConflictError("A goal must end after it starts.")

        goal = Goal(
            organization_id=self.auth.organization_id,
            owner_id=owner_id,
            metric_key=metric_key,
            target_value=target_value,
            period_start=period_start,
            period_end=period_end,
            created_by=actor.id,
        )
        self.session.add(goal)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="goal",
            entity_id=goal.id,
            # str, not Decimal: the column is JSONB and a Decimal is not JSON.
            metadata={"metric": metric_key, "target": str(target_value)},
        )
        return goal

    async def delete_goal(self, goal_id: UUID, actor: User) -> None:
        self.auth.require("reports.export")
        goal = (
            await self.session.execute(
                select(Goal)
                .where(Goal.id == goal_id)
                .where(Goal.organization_id == self.auth.organization_id)
            )
        ).scalar_one_or_none()
        if goal is None:
            raise NotFoundError("Goal not found.")

        await self.session.delete(goal)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="goal",
            entity_id=goal_id,
            metadata={"metric": goal.metric_key},
        )


__all__ = ["DASHBOARDS", "InsightsService", "MetricValue"]
