"""Analytics business logic: periods, scope, caching, and the snapshot seam.

Three things live here that are easy to get subtly wrong elsewhere.

**Scope.** Analytics reuses each entity's own view grant. The scope a caller
holds on deals decides which deals are counted, exactly as it does on the deal
list — there is no separate "analytics scope", because a second definition of
what a user can see is a second thing to keep in step and the first divergence
is a leak. A caller with no grant on an entity gets `None` for its metrics, not
zero: a blank panel is honest, a zero is a claim.

**The snapshot seam.** History comes from `metric_snapshots`, written nightly;
today has no row yet and is computed live. That boundary is implemented once, in
`series`, rather than every dashboard deciding for itself and half of them
getting it wrong at midnight.

**Caching.** Rollups are cached in Redis, and the cache key includes a digest of
the caller's resolved scope. Keying on organization alone would serve an agent's
own numbers to their colleague — the classic cache-poisoning-by-omission bug,
and one that looks correct in every single-user test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import (
    DERIVED_METRICS,
    METRICS,
    METRICS_BY_KEY,
    MetricDefinition,
)
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.repositories.analytics import AnalyticsRepository
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

#: Short. Analytics is read constantly while a dashboard is open, and a minute
#: of staleness on a revenue figure is invisible; an hour is a support ticket
#: about numbers not updating after a deal closed.
CACHE_TTL_SECONDS = 60

PeriodKey = Literal["today", "week", "month", "quarter", "year", "custom"]


@dataclass(frozen=True, slots=True)
class Period:
    """A half-open interval, `[start, end)`.

    Half-open throughout, because an inclusive end is how a deal closed at 23:30
    on the last day of a month goes missing from that month — and how one closed
    exactly at midnight is counted in two.
    """

    start: datetime
    end: datetime
    label: str

    @property
    def previous(self) -> Period:
        """The immediately preceding window of the same length.

        Used for deltas. Same length rather than "the same month last year", so
        a comparison is against a like-for-like amount of trading rather than
        against a period with a different number of working days.
        """
        span = self.end - self.start
        return Period(self.start - span, self.start, f"Previous {self.label}")

    @property
    def days(self) -> int:
        return max(1, (self.end - self.start).days)


def resolve_period(
    key: PeriodKey,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
) -> Period:
    """Turn a period name into an interval.

    `end` is always "now" rather than the end of the calendar period: a
    month-to-date figure that pretended to run to the 31st would divide by days
    that have not happened and report a rate nobody achieved.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    today = moment.replace(hour=0, minute=0, second=0, microsecond=0)

    match key:
        case "today":
            return Period(today, moment, "today")
        case "week":
            return Period(today - timedelta(days=today.weekday()), moment, "this week")
        case "month":
            return Period(today.replace(day=1), moment, "this month")
        case "quarter":
            first_month = 3 * ((today.month - 1) // 3) + 1
            return Period(today.replace(month=first_month, day=1), moment, "this quarter")
        case "year":
            return Period(today.replace(month=1, day=1), moment, "this year")
        case _:
            if start is None or end is None:
                raise ValueError("A custom period needs both a start and an end.")
            return Period(start, end, "the selected period")


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One number, with what it takes to render it honestly."""

    key: str
    label: str
    unit: str
    kind: str
    higher_is_better: bool
    #: `None` means the caller cannot see this metric — not that it is zero.
    value: Decimal | None
    previous: Decimal | None = None

    @property
    def delta_percent(self) -> Decimal | None:
        """Change against the previous period.

        `None` when the previous period was zero rather than infinity or a
        misleading 100%: coming from nothing is not a percentage improvement,
        and rendering one invites a comparison that does not exist.
        """
        if self.value is None or self.previous is None or self.previous == 0:
            return None
        return Decimal(
            str(round(float((self.value - self.previous) / self.previous * 100), 1))
        )


class AnalyticsService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = AnalyticsRepository(session)
        self.rbac = RbacService(session)
        self._scope_cache: dict[str, list[UUID] | None | Literal[False]] = {}

    # --------------------------------------------------------------- scope

    async def scope_for(self, permission: str) -> list[UUID] | None | Literal[False]:
        """Owner ids the caller's grant permits.

        `None` is ALL scope, a list is a restriction, and `False` means no grant
        at all — three states, because "everyone" and "nobody" must not both
        collapse to an empty predicate. Memoised per request: a dashboard asks
        for the same permission a dozen times and each resolution is a team
        lookup.
        """
        if permission in self._scope_cache:
            return self._scope_cache[permission]

        scope = self.auth.scope_for(permission)
        resolved: list[UUID] | None | Literal[False]
        if scope is None:
            resolved = False
        else:
            resolved = await self.rbac.owner_ids_for_scope(self.auth, scope)

        self._scope_cache[permission] = resolved
        return resolved

    async def _scope_digest(self) -> str:
        """A stable fingerprint of everything this caller can see.

        Part of every cache key. Without it one agent's cached dashboard is
        served to the next — a bug that passes every single-user test and leaks
        in production on the second concurrent user.
        """
        parts: list[str] = []
        for permission in sorted({metric.permission for metric in METRICS}):
            resolved = await self.scope_for(permission)
            if resolved is False:
                parts.append(f"{permission}:none")
            elif resolved is None:
                parts.append(f"{permission}:all")
            else:
                parts.append(f"{permission}:" + ",".join(sorted(str(i) for i in resolved)))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    # --------------------------------------------------------------- cache

    async def _cached(self, name: str, period: Period, compute):  # type: ignore[no-untyped-def]
        """Redis-cached rollup, keyed by tenant, scope and window.

        A cache miss on an unreachable Redis is a slower request, never a failed
        one — the same posture the rate limiter and the RBAC cache take.
        """
        key = (
            f"analytics:{self.auth.organization_id}:{await self._scope_digest()}"
            f":{name}:{int(period.start.timestamp())}:{int(period.end.timestamp())}"
        )
        try:
            redis = get_redis()
            cached = await redis.get(key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.warning("analytics_cache_read_failed", exc_info=True)

        value = await compute()

        try:
            await get_redis().setex(key, CACHE_TTL_SECONDS, json.dumps(value, default=str))
        except Exception:
            logger.warning("analytics_cache_write_failed", exc_info=True)

        return value

    # ------------------------------------------------------------ metrics

    async def _raw_metrics(self, period: Period) -> dict[str, Decimal | None]:
        """Every base metric for one window, or None where there is no grant."""
        organization = self.auth.organization_id
        values: dict[str, Decimal | None] = {}

        leads = await self.scope_for("leads.view")
        if leads is not False:
            values["leads_created"] = Decimal(
                await self.repo.leads_created(organization, leads, period.start, period.end)
            )
            values["leads_converted"] = Decimal(
                await self.repo.leads_converted(organization, leads, period.start, period.end)
            )
            values["leads_open"] = Decimal(await self.repo.leads_open(organization, leads))

        contacts = await self.scope_for("contacts.view")
        if contacts is not False:
            values["clients_created"] = Decimal(
                await self.repo.clients_created(organization, contacts, period.start, period.end)
            )

        deals = await self.scope_for("deals.view")
        if deals is not False:
            values["deals_created"] = Decimal(
                await self.repo.deals_created(organization, deals, period.start, period.end)
            )
            won, lost, revenue, commission = await self.repo.closed_deal_stats(
                organization, deals, period.start, period.end
            )
            values["deals_won"] = Decimal(won)
            values["deals_lost"] = Decimal(lost)
            values["revenue_won"] = revenue
            values["commission_earned"] = commission
            open_value, weighted = await self.repo.open_pipeline(organization, deals)
            values["pipeline_open_value"] = open_value
            values["pipeline_weighted_value"] = weighted
            values["sales_cycle_days"] = await self.repo.sales_cycle_days(
                organization, deals, period.start, period.end
            )

        properties = await self.scope_for("properties.view")
        if properties is not False:
            values["listings_active"] = Decimal(
                await self.repo.listings_active(organization, properties)
            )
            values["listings_sold"] = Decimal(
                await self.repo.listings_sold(organization, properties, period.start, period.end)
            )
            values["average_days_on_market"] = await self.repo.average_days_on_market(
                organization, properties
            )

        tasks = await self.scope_for("tasks.view")
        if tasks is not False:
            values["tasks_completed"] = Decimal(
                await self.repo.tasks_completed(organization, tasks, period.start, period.end)
            )
            values["tasks_overdue"] = Decimal(
                await self.repo.tasks_overdue(organization, tasks)
            )

        activities = await self.scope_for("activities.view")
        if activities is not False:
            values["activities_logged"] = Decimal(
                await self.repo.activities_logged(
                    organization, activities, period.start, period.end
                )
            )

        return values | self._derive(values)

    @staticmethod
    def _derive(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        """Ratios, computed from components rather than stored.

        A rate stored per day cannot be re-aggregated — the average of daily win
        rates is not the win rate for the month — so every ratio is derived at
        read time from numbers that *can* be summed.
        """
        derived: dict[str, Decimal | None] = {}

        won = values.get("deals_won")
        lost = values.get("deals_lost")
        if won is not None and lost is not None:
            closed = won + lost
            derived["win_rate"] = (
                Decimal(str(round(float(won / closed * 100), 1))) if closed else None
            )
            revenue = values.get("revenue_won")
            derived["average_deal_value"] = (
                Decimal(str(round(float(revenue / won), 2)))
                if revenue is not None and won
                else None
            )

        created = values.get("leads_created")
        converted = values.get("leads_converted")
        if created is not None and converted is not None:
            derived["lead_conversion_rate"] = (
                Decimal(str(round(float(converted / created * 100), 1))) if created else None
            )

        return derived

    async def kpis(
        self, period: Period, *, compare: bool = True
    ) -> list[MetricValue]:
        """Every metric for the window, with a like-for-like comparison.

        The previous period is a second full pass. That is deliberate: deriving
        it from snapshots would be cheaper and would disagree with the live
        figure for today, and two numbers side by side that were computed
        differently is exactly the discrepancy that destroys trust in a
        dashboard.
        """
        current = await self._cached(
            "kpis", period, lambda: self._serialise(self._raw_metrics(period))
        )
        previous: dict[str, Any] = {}
        if compare:
            window = period.previous
            previous = await self._cached(
                "kpis", window, lambda: self._serialise(self._raw_metrics(window))
            )

        out: list[MetricValue] = []
        for metric in METRICS:
            if metric.key not in current:
                continue
            out.append(
                MetricValue(
                    key=metric.key,
                    label=metric.label,
                    unit=metric.unit,
                    kind=metric.kind,
                    higher_is_better=metric.higher_is_better,
                    value=_as_decimal(current.get(metric.key)),
                    previous=_as_decimal(previous.get(metric.key)),
                )
            )
        return out

    @staticmethod
    async def _serialise(coroutine) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
        """Decimals to strings for the cache.

        Never floats. A revenue figure that round-trips through a float has lost
        the precision NUMERIC exists to protect, and a cache is not the place to
        quietly discard it.
        """
        values = await coroutine
        return {key: (str(value) if value is not None else None) for key, value in values.items()}

    # ------------------------------------------------------------- series

    async def series(
        self, metric_key: str, *, days: int = 30, now: datetime | None = None
    ) -> list[tuple[date, Decimal]]:
        """Daily readings for a metric, history from snapshots plus today live.

        The seam is here and nowhere else. A snapshot for today does not exist
        until tonight's job, so asking snapshots alone would show a dashboard
        that goes flat at midnight and fills in overnight — which reads as an
        outage.
        """
        metric = METRICS_BY_KEY.get(metric_key)
        if metric is None:
            raise ValueError(f"Unknown metric: {metric_key}")

        owner_ids = await self.scope_for(metric.permission)
        if owner_ids is False:
            return []

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        today = moment.date()
        start = today - timedelta(days=days)

        history = await self.repo.series(
            self.auth.organization_id,
            metric_key,
            owner_ids if owner_ids is not None else None,
            start,
            today,
            summable=metric.summable,
        )

        live = await self._today_value(metric, moment)
        if live is not None:
            history = [*history, (today, live)]
        return history

    async def _today_value(
        self, metric: MetricDefinition, moment: datetime
    ) -> Decimal | None:
        """Today's reading, computed live."""
        period = resolve_period("today", now=moment)
        values = await self._raw_metrics(period)
        return values.get(metric.key)

    # -------------------------------------------------------- snapshotting

    async def write_snapshot(self, snapshot_date: date, now: datetime | None = None) -> int:
        """Write one day's readings for every active owner. Returns rows written.

        Runs with whatever authorization context it was given — the nightly job
        passes a system context — and writes **per owner**, because that is the
        grain a scope rollup needs. Deriving an agent's history from an org-wide
        row is not possible, and re-querying live data to get it would make the
        snapshot pointless.
        """
        from app.repositories.analytics import AnalyticsRepository

        organization = self.auth.organization_id
        repo = AnalyticsRepository(self.session)
        owners = await repo.active_owner_ids(organization)

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        start = datetime.combine(snapshot_date, datetime.min.time(), tzinfo=UTC)
        period = Period(start, min(start + timedelta(days=1), moment), "day")
        if period.end <= period.start:
            # The requested day has not started yet. Writing a row of zeros for
            # it would look like a day with no trading rather than a day that
            # has not happened.
            return 0

        written = 0
        for owner in owners:
            scoped = [owner]
            values = await self._owner_metrics(organization, scoped, period)
            for key, value in values.items():
                if value is None:
                    continue
                await repo.upsert_snapshot(organization, owner, snapshot_date, key, value)
                written += 1

        return written

    async def _owner_metrics(
        self, organization: UUID, owner_ids: list[UUID], period: Period
    ) -> dict[str, Decimal | None]:
        """Base metrics for exactly one owner. Used only by the snapshot writer.

        Bypasses `scope_for` deliberately: the writer is not answering a user's
        question, it is recording each person's own numbers so that any future
        scope can be rolled up from them.
        """
        repo = self.repo
        won, lost, revenue, commission = await repo.closed_deal_stats(
            organization, owner_ids, period.start, period.end
        )
        open_value, weighted = await repo.open_pipeline(organization, owner_ids)

        return {
            "leads_created": Decimal(
                await repo.leads_created(organization, owner_ids, period.start, period.end)
            ),
            "leads_converted": Decimal(
                await repo.leads_converted(organization, owner_ids, period.start, period.end)
            ),
            "leads_open": Decimal(await repo.leads_open(organization, owner_ids)),
            "clients_created": Decimal(
                await repo.clients_created(organization, owner_ids, period.start, period.end)
            ),
            "deals_created": Decimal(
                await repo.deals_created(organization, owner_ids, period.start, period.end)
            ),
            "deals_won": Decimal(won),
            "deals_lost": Decimal(lost),
            "revenue_won": revenue,
            "commission_earned": commission,
            "pipeline_open_value": open_value,
            "pipeline_weighted_value": weighted,
            "listings_active": Decimal(await repo.listings_active(organization, owner_ids)),
            "listings_sold": Decimal(
                await repo.listings_sold(organization, owner_ids, period.start, period.end)
            ),
            "tasks_completed": Decimal(
                await repo.tasks_completed(organization, owner_ids, period.start, period.end)
            ),
            "tasks_overdue": Decimal(await repo.tasks_overdue(organization, owner_ids)),
            "activities_logged": Decimal(
                await repo.activities_logged(organization, owner_ids, period.start, period.end)
            ),
        }


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


__all__ = [
    "DERIVED_METRICS",
    "AnalyticsService",
    "MetricValue",
    "Period",
    "resolve_period",
]
