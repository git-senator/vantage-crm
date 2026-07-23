"""Analytics engine — the metric registry, scoped aggregates, snapshots, goals.

Four properties carry this module, and each has a test that fails loudly if it
regresses:

  * **Scope.** A metric is readable exactly when its entity is. An agent's
    numbers are their book; a metric they hold no grant on is `None`, not zero.
  * **The cache is scope-keyed.** Two agents in the same organization asking for
    the same window must not receive each other's numbers.
  * **Ratios are derived, never stored.** The average of daily win rates is not
    the month's win rate, so no ratio may appear in a snapshot.
  * **The snapshot seam.** History comes from `metric_snapshots`; today is
    computed live, so a chart does not go flat at midnight.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import DERIVED_METRICS, METRICS, SNAPSHOT_METRICS
from app.core.permissions import Scope
from app.models.organization import Organization
from app.schemas.client import ClientCreate
from app.schemas.deal import DealCreate, DealStageTransition
from app.schemas.lead import LeadCreate
from app.services.analytics import AnalyticsService, Period, resolve_period
from app.services.client import ClientService
from app.services.deal import DealService
from app.services.insights import InsightsService
from app.services.lead import LeadService
from app.services.pipeline import build_default_pipeline
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def pipeline(db: AsyncSession, organization: Organization):  # type: ignore[no-untyped-def]
    built = build_default_pipeline(organization.id)
    db.add(built)
    await db.flush()
    return built


def stage_by_key(pipeline, key: str):  # type: ignore[no-untyped-def]
    return next(stage for stage in pipeline.stages if stage.key == key)


@pytest.fixture
async def client_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await ClientService(db, auth).create_client(
        ClientCreate(first_name="Tomas", last_name="Vega", type="buyer"), user
    )


def _deal(client_id, **overrides):  # type: ignore[no-untyped-def]
    data: dict = {
        "title": "Deal",
        "client_id": client_id,
        "value": Decimal("400000.00"),
        **overrides,
    }
    return DealCreate(**data)


async def _win(db: AsyncSession, auth, user, pipeline, client_id, value="400000.00"):  # type: ignore[no-untyped-def]
    """Create a deal and close it won. Returns the deal."""
    service = DealService(db, auth)
    deal = await service.create_deal(_deal(client_id, value=Decimal(value)), user)
    await service.move_stage(
        deal.id,
        DealStageTransition(to_stage_id=stage_by_key(pipeline, "closed_won").id),
        user,
    )
    return deal


class TestRegistry:
    def test_no_ratio_is_ever_snapshotted(self) -> None:
        """The invariant the whole design rests on.

        A stored ratio cannot be re-aggregated — summing thirty daily win rates
        produces nonsense — so every derived metric must be absent from the
        snapshot set and computed from its components at read time.
        """
        snapshot_keys = set(SNAPSHOT_METRICS)
        assert snapshot_keys.isdisjoint(DERIVED_METRICS)

    def test_every_derived_metric_has_both_components_snapshotted(self) -> None:
        """Otherwise a ratio is underivable for any historical day."""
        snapshot_keys = set(SNAPSHOT_METRICS)
        for key, (numerator, denominator) in DERIVED_METRICS.items():
            assert numerator in snapshot_keys, key
            assert denominator in snapshot_keys, key

    def test_levels_are_not_summable(self) -> None:
        """Open pipeline on Monday plus open pipeline on Tuesday is not a
        quantity anyone wants. Only flows may be summed across days."""
        for metric in METRICS:
            assert metric.summable == (metric.kind == "flow")


class TestKpis:
    async def test_counts_reflect_created_records(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(LeadCreate(first_name="A", last_name="One"), user)
        await LeadService(db, auth).create_lead(LeadCreate(first_name="B", last_name="Two"), user)
        await DealService(db, auth).create_deal(_deal(client_record.id), user)

        values = {
            metric.key: metric.value
            for metric in await AnalyticsService(db, auth).kpis(
                resolve_period("month"), compare=False
            )
        }
        assert values["leads_created"] == 2
        assert values["deals_created"] == 1
        assert values["pipeline_open_value"] == Decimal("400000.0000")

    async def test_a_won_deal_becomes_revenue_today(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Closing is a DATE and the period ends at "now".

        If the boundary were computed naively this would read zero until
        midnight — the bug this assertion exists to catch.
        """
        user, auth = admin
        await _win(db, auth, user, pipeline, client_record.id)

        values = {
            metric.key: metric.value
            for metric in await AnalyticsService(db, auth).kpis(
                resolve_period("today"), compare=False
            )
        }
        assert values["deals_won"] == 1
        assert values["revenue_won"] == Decimal("400000.0000")

    async def test_win_rate_is_derived_not_stored(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        await _win(db, auth, user, pipeline, client_record.id)
        lost = await service.create_deal(_deal(client_record.id), user)
        await service.move_stage(
            lost.id,
            DealStageTransition(
                to_stage_id=stage_by_key(pipeline, "closed_lost").id,
                lost_reason="price",
            ),
            user,
        )

        values = {
            metric.key: metric.value
            for metric in await AnalyticsService(db, auth).kpis(
                resolve_period("month"), compare=False
            )
        }
        assert values["win_rate"] == Decimal("50.0")

    async def test_a_metric_without_a_grant_is_none_not_zero(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """A blank panel is honest. A zero is a claim the caller cannot make."""
        user = await make_user(db, organization, "narrow@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.ALL, "reports.view": Scope.ALL},
        )
        values = {
            metric.key: metric.value
            for metric in await AnalyticsService(db, auth).kpis(
                resolve_period("month"), compare=False
            )
        }
        assert values["leads_created"] == 0
        # No deals grant at all — the key is absent rather than reported as 0.
        assert "revenue_won" not in values


class TestScopeAndCaching:
    async def test_agents_do_not_see_each_others_numbers(
        self, db: AsyncSession, organization: Organization, rbac_seeded, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        await LeadService(db, one_auth).create_lead(
            LeadCreate(first_name="Ones", last_name="Lead"), one
        )

        period = resolve_period("month")
        first = {m.key: m.value for m in await AnalyticsService(db, one_auth).kpis(period)}
        second = {m.key: m.value for m in await AnalyticsService(db, two_auth).kpis(period)}

        assert first["leads_created"] == 1
        # The second agent shares the tenant and the window. If the cache key
        # omitted the scope digest, this would be 1 — one agent's numbers served
        # to another, a leak that passes every single-user test.
        assert second["leads_created"] == 0

    async def test_scope_digest_differs_between_callers(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "d1@vantage.example")
        two = await make_user(db, organization, "d2@vantage.example")
        one_service = AnalyticsService(db, await auth_for(db, one, "agent"))
        two_service = AnalyticsService(db, await auth_for(db, two, "agent"))

        assert await one_service._scope_digest() != await two_service._scope_digest()


class TestSnapshots:
    async def test_snapshot_writes_per_owner_and_series_reads_it_back(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Snap", last_name="Shot"), user
        )

        service = AnalyticsService(db, auth)
        today = datetime.now(UTC).date()
        written = await service.write_snapshot(today)
        assert written > 0

        points = await service.series("leads_created", days=7)
        assert any(day == today for day, _ in points)

    async def test_snapshot_is_idempotent(self, db: AsyncSession, admin, client_record) -> None:  # type: ignore[no-untyped-def]
        """A retried job must correct the row, not duplicate it.

        This is what `NULLS NOT DISTINCT` on the grain constraint buys: without
        it the unowned rows conflict with nothing and insert every run.
        """
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Once", last_name="Only"), user
        )
        service = AnalyticsService(db, auth)
        today = datetime.now(UTC).date()

        from sqlalchemy import func, select

        from app.models.analytics import MetricSnapshot

        async def rows() -> int:
            result = await db.execute(select(func.count()).select_from(MetricSnapshot))
            return int(result.scalar() or 0)

        first = await service.write_snapshot(today)
        after_first = await rows()
        second = await service.write_snapshot(today)

        assert first == second
        # The row count is unchanged: the second run updated in place.
        assert await rows() == after_first

    async def test_a_future_day_is_not_snapshotted(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        """A day that has not started is not a day with no trading."""
        _user, auth = admin
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
        assert await AnalyticsService(db, auth).write_snapshot(tomorrow) == 0


class TestPeriods:
    def test_periods_are_half_open(self) -> None:
        """An inclusive end double-counts a deal closed exactly at midnight."""
        now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
        month = resolve_period("month", now=now)
        assert month.start == datetime(2026, 3, 1, tzinfo=UTC)
        assert month.end == now

    def test_previous_is_the_same_length(self) -> None:
        now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
        month = resolve_period("month", now=now)
        assert month.previous.end == month.start
        assert (month.end - month.start) == (month.previous.end - month.previous.start)

    def test_a_custom_period_needs_both_ends(self) -> None:
        with pytest.raises(ValueError):
            resolve_period("custom")


class TestInsights:
    async def test_forecast_is_booked_plus_weighted_pipeline(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await _win(db, auth, user, pipeline, client_record.id, value="200000.00")
        # An open deal in qualification: 10% default probability.
        await DealService(db, auth).create_deal(
            _deal(client_record.id, value=Decimal("100000.00")), user
        )

        forecast = await InsightsService(db, auth).forecast(resolve_period("month"))
        assert forecast["booked"] == Decimal("200000.0000")
        assert forecast["weighted_pipeline"] == Decimal("10000.0000")
        assert forecast["projected"] == Decimal("210000.0000")

    async def test_win_loss_rate_is_none_when_nothing_closed(
        self, db: AsyncSession, admin, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """0% would read as "we lost everything". Undefined is not zero."""
        _user, auth = admin
        result = await InsightsService(db, auth).win_loss(resolve_period("month"))
        assert result["won"] == 0
        assert result["win_rate"] is None

    async def test_every_declared_dashboard_assembles(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        from app.services.insights import DASHBOARDS

        user, auth = admin
        await _win(db, auth, user, pipeline, client_record.id)

        service = InsightsService(db, auth)
        for key in DASHBOARDS:
            payload = await service.dashboard(key, resolve_period("month"))
            assert payload["key"] == key
            assert payload["metrics"], key

    async def test_an_unknown_dashboard_is_a_404(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        from app.core.exceptions import NotFoundError

        _user, auth = admin
        with pytest.raises(NotFoundError):
            await InsightsService(db, auth).dashboard("nope", resolve_period("month"))


class TestGoals:
    async def test_progress_is_measured_against_the_live_metric(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await _win(db, auth, user, pipeline, client_record.id, value="250000.00")

        service = InsightsService(db, auth)
        today = datetime.now(UTC).date()
        await service.create_goal(
            owner_id=None,
            metric_key="revenue_won",
            target_value=Decimal("500000"),
            period_start=today - timedelta(days=1),
            period_end=today + timedelta(days=30),
            actor=user,
        )

        goals = await service.list_goals()
        assert len(goals) == 1
        assert goals[0]["current_value"] == Decimal("250000.0000")
        assert goals[0]["percent_complete"] == Decimal("50.0")

    async def test_an_unknown_metric_is_rejected(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        from app.core.exceptions import ConflictError

        user, auth = admin
        today = datetime.now(UTC).date()
        with pytest.raises(ConflictError):
            await InsightsService(db, auth).create_goal(
                owner_id=None,
                metric_key="vibes",
                target_value=Decimal("10"),
                period_start=today,
                period_end=today,
                actor=user,
            )

    async def test_setting_a_goal_requires_more_than_read_access(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Setting a team's number is a management act, not a reading one."""
        from app.core.exceptions import PermissionDeniedError

        user = await make_user(db, organization, "reader@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"reports.view": Scope.OWN},
        )
        today = datetime.now(UTC).date()
        with pytest.raises(PermissionDeniedError):
            await InsightsService(db, auth).create_goal(
                owner_id=None,
                metric_key="revenue_won",
                target_value=Decimal("10"),
                period_start=today,
                period_end=today,
                actor=user,
            )


class TestTenantIsolation:
    async def test_another_tenants_records_are_not_counted(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"leads.view": Scope.ALL, "leads.manage": Scope.ALL},
        )
        await LeadService(db, outsider_auth).create_lead(
            LeadCreate(first_name="Foreign", last_name="Lead"), outsider
        )

        values = {
            metric.key: metric.value
            for metric in await AnalyticsService(db, admin_auth).kpis(
                resolve_period("month"), compare=False
            )
        }
        assert values["leads_created"] == 0


class TestPeriodArithmetic:
    def test_days_never_returns_zero(self) -> None:
        """`days` divides. A zero-length window must not divide by zero."""
        moment = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
        assert Period(moment, moment, "instant").days == 1
