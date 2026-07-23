# Analytics

Phase 5.1–5.5. The KPI engine, snapshot substrate, composed dashboards, business
analytics, forecasting and goals.

- [1. What analytics is, and is not](#1-what-analytics-is-and-is-not)
- [2. Scope: a metric is readable exactly when its entity is](#2-scope-a-metric-is-readable-exactly-when-its-entity-is)
- [3. The metric registry](#3-the-metric-registry)
- [4. Periods](#4-periods)
- [5. Snapshots, and the seam at today](#5-snapshots-and-the-seam-at-today)
- [6. Caching](#6-caching)
- [7. Dashboards](#7-dashboards)
- [8. Business analytics](#8-business-analytics)
- [9. Forecasting](#9-forecasting)
- [10. Goals](#10-goals)
- [11. API](#11-api)
- [12. Operations](#12-operations)

---

## 1. What analytics is, and is not

Analytics is a **read layer over the CRM's own tables**. It owns two tables —
`metric_snapshots` and `goals` — and neither is a source of truth for anything a
user typed. Every number it reports can be recomputed from leads, clients,
deals, properties, tasks and activities; the snapshot table is a cache with a
schema, not a ledger.

That framing decides several arguments in advance:

- A metric never has its own copy of a business rule. "Open" comes from the
  stage flags, exactly as it does on the deal board; there is no
  `deals.status = 'open'` column to drift from it.
- A discrepancy between a dashboard and a list view is always a bug in
  analytics, never a difference of opinion.
- Losing `metric_snapshots` entirely costs history and a rebuild
  (`backfill_metrics`), not data.

What it is **not**: a data warehouse, a BI tool, or a place to run arbitrary
user SQL. Phase 5.3 (reports) adds structured, validated querying on top of the
same aggregates. There is no path from a request to a query the backend did not
author.

---

## 2. Scope: a metric is readable exactly when its entity is

Analytics reuses each entity's own view grant. Deals counted in `revenue_won`
are exactly the deals the caller could list at `/deals`; leads in
`leads_created` are the ones at `/leads`. There is **no separate analytics
scope**.

This is the single most important decision in the module. A second definition of
what a user can see is a second thing to keep in step with the first, and the
first time they diverge is a disclosure — an aggregate is not a safe way to leak
data, because a count over one record is that record.

`AnalyticsService.scope_for(permission)` resolves to three states, and the
distinction between them is load-bearing:

| State        | Meaning                        | Effect on the query        |
| ------------ | ------------------------------ | -------------------------- |
| `None`       | ALL scope                      | no owner predicate         |
| `list[UUID]` | OWN or TEAM, resolved to ids   | `owner_id IN (...)`        |
| `False`      | the caller holds no such grant | the metric is not computed |

"Everyone" and "nobody" must not both collapse to an empty predicate. A caller
with no grant on deals receives `value: null` for every deal metric — **not
zero**. A blank panel is honest; a zero is a claim the caller is not entitled to
make and cannot distinguish from a quiet month.

`reports.view` gates the analytics surface itself; the per-entity grants decide
what is inside it. Both are required.

Unowned records (an unassigned lead) have `owner_id IS NULL` and are therefore
visible only at ALL scope — the same rule they follow on every list endpoint.

---

## 3. The metric registry

`app/analytics/metrics.py` is the single catalogue. Twenty-two metrics, each a
`MetricDefinition` carrying its label, description, unit, category, the
permission that gates it, whether higher is better, and its **kind**.

### Flows and levels

```
kind = "flow"   a quantity accumulated over an interval   leads_created, revenue_won
kind = "level"  a reading at an instant                   leads_open, pipeline_open_value
```

Only flows may be summed across days. Open pipeline on Monday plus open pipeline
on Tuesday is not a quantity anybody wants — it double-counts every deal that
was open on both. The registry declares the kind and the aggregation layer
refuses to sum a level, which is why the distinction is data rather than a
convention someone has to remember.

`higher_is_better` exists so the frontend never decides which direction is good.
Without it, a dashboard eventually paints rising `tasks_overdue` green.

### Commission

`commission_earned` sums the **stored** `commission_amount` on won deals, never
a figure recomputed from the rate. `DealService` computes the amount once and
then leaves it alone, because flat fees and negotiated splits are real; an
analytics layer that recomputed it would report a number the brokerage never
agreed to, and would disagree with the deal record it came from.

### Ratios are derived, never stored

`win_rate`, `lead_conversion_rate` and `average_deal_value` are **absent from
`SNAPSHOT_METRICS`** and computed at read time from components that *can* be
summed.

The reason is arithmetic: the average of thirty daily win rates is not the win
rate for the month. A day with one deal and a day with two hundred would count
equally. Storing a ratio per day makes any multi-day rollup silently wrong, and
wrong in a way that looks plausible.

`DERIVED_METRICS` maps each ratio to the snapshotted components it needs, and
`validate_registry()` — which runs at import — refuses to start if a component
is not itself snapshotted. A ratio whose denominator is never recorded renders
for today and goes blank the moment anyone looks backwards.

---

## 4. Periods

A `Period` is a **half-open interval** `[start, end)`. Inclusive ends are how a
deal closed at 23:30 on the last day of a month goes missing from that month,
and how one closed exactly at midnight gets counted in two.

`end` is always *now*, never the end of the calendar period. A month-to-date
figure that pretended to run to the 31st would divide by days that have not
happened and report a rate nobody achieved.

`period.previous` is the immediately preceding window **of the same length** —
not "the same month last year". Comparing fifteen days against fifteen days is
like-for-like; comparing fifteen elapsed days against a full previous month
always shows a collapse.

### The DATE boundary

`deals.actual_close_date` is a `DATE`, not an instant. Comparing it against a
period's timestamps needs care: naively taking `start.date()` and `end.date()`
as an exclusive range drops the current day for any period ending "now" — which
is every period a dashboard asks for. Today's closed deals would read zero until
midnight.

`_date_bounds()` therefore takes the last day the interval actually touches: one
microsecond before `end`. A period ending exactly at midnight ends on the
previous day; one ending mid-afternoon includes today. Both are what the
half-open instant range means, expressed in whole days.

For the same reason `actual_close_date` is written as `datetime.now(UTC).date()`
rather than `date.today()`. Every other timestamp in the system is UTC, and a
host-local close date would put a deal closed near midnight outside the very day
it closed on any server not running UTC.

---

## 5. Snapshots, and the seam at today

`metric_snapshots` holds one number, for one metric, for one owner, on one day.

### Why per-owner

Analytics rolls a scope up by **summing the ids the scope resolver returns**. An
organization-grain row could only ever answer an admin's question: handing an
agent an org-wide history would disclose more than any list endpoint allows, and
computing their slice would mean re-querying live data and abandoning the
snapshot entirely. Per-owner rows serve every scope from the same table with the
same predicate the list endpoints use.

### Why snapshots at all

A month of daily readings for one agent is thirty indexed row reads. The
equivalent live query is thirty aggregate scans over the whole deal table.

### The seam

Today has no row until tonight's job. So **history comes from snapshots and
today is computed live**, and that boundary is implemented in exactly one place
— `AnalyticsService.series` — rather than every dashboard deciding for itself
and half of them getting it wrong at midnight. A chart that goes flat at
midnight and fills in overnight reads as an outage.

### Idempotency

The writer upserts on `uq_metric_snapshots_grain`, declared **`NULLS NOT
DISTINCT`**. `owner_id` is nullable, and under the SQL default two NULLs are
never equal — so the `ON CONFLICT` target would never match an unowned row and
every run would insert a duplicate rather than correct yesterday's. Postgres 15+
is required for the clause; the project runs 16. The model declares it too
(`postgresql_nulls_not_distinct=True`) so the test schema built by `create_all`
cannot drift from the migration.

A day that has not started yet is never snapshotted. A row of zeros for tomorrow
looks like a day with no trading rather than a day that has not happened.

---

## 6. Caching

Rollups are cached in Redis for 60 seconds. A minute of staleness on a revenue
figure is invisible; an hour is a support ticket about numbers not updating
after a deal closed.

**The cache key includes a digest of the caller's resolved scope.** Keying on
organization and window alone would serve one agent's numbers to their
colleague — a cache-poisoning-by-omission bug that passes every single-user test
and leaks on the second concurrent user. `tests/test_analytics.py` asserts it
directly: two agents in one tenant, same window, different numbers.

A cache miss on an unreachable Redis is a slower request, never a failed one —
the same posture the rate limiter and the RBAC cache take.

Values are cached as **strings**, never floats. A revenue figure that
round-trips through a float has lost the precision `NUMERIC` exists to protect,
and a cache is not the place to quietly discard it. The API serialises money and
counts as strings for the same reason.

---

## 7. Dashboards

Six named dashboards — executive, sales, pipeline, agents, revenue, forecast —
declared in `app/services/insights.py::DASHBOARDS`, each with the metrics it
leads with and the panels it needs.

They are **assembled server-side, in one round trip**. A client making eight
calls to build one screen re-resolves the caller's team membership eight times
and can render panels captured at two different moments.

Panels are attached per dashboard rather than every dashboard returning
everything: the agent view has no use for loss reasons, and computing them
anyway is six aggregate queries nobody reads.

Metrics come back ordered by the dashboard's own list, not the registry's — the
first tile is the one the dashboard is about.

---

## 8. Business analytics

| Panel             | Source                                        | Note                                                        |
| ----------------- | --------------------------------------------- | ----------------------------------------------------------- |
| Pipeline by stage | `deals` ⋈ `pipeline_stages`                   | Open deals only, by the stage flags                          |
| Stage velocity    | `deal_stage_history.duration_in_stage`        | Recorded at transition time, not recomputed                  |
| Win/loss          | `closed_deal_stats` + `loss_reasons`          | Win rate is `null` when nothing closed, never `0`            |
| Lead sources      | `leads.source`                                | Conversion rate computed server-side, one definition of it   |
| Agent leaderboard | `users` LEFT JOIN deals/leads                 | LEFT JOIN so zero-performers still appear                    |

Two deliberate asymmetries:

**Velocity is not scoped by owner.** It is a property of the pipeline, and
slicing it to one agent's deals produces a number so noisy it misleads. A caller
who cannot see deals at all still gets nothing.

**A rate over nothing is `null`, not zero.** A win rate with no closed deals is
undefined; rendering `0%` reads as "we lost everything". The same applies to
conversion rate over no leads.

---

## 9. Forecasting

`booked + weighted open pipeline`, where the weight is each deal's own
probability rather than its stage default — a probability somebody overrode is
them telling you something the stage does not know.

This is **arithmetic, not prediction**. No regression, no seasonality, no
confidence interval. Those need history this product does not have yet, and a
forecast that looks statistical while being a guess is worse than one that is
visibly a sum. When there is a year of snapshots to fit against, `forecast()` is
the one function that changes.

The response separates `booked` (the floor — revenue already won) from
`weighted_pipeline` (the estimate), and breaks the pipeline down by stage so a
reader can see which part the projection leans on rather than trusting one
number. `previous_actual` is the same-length trailing window, so "ahead or
behind" compares like with like.

---

## 10. Goals

One metric, one target, one window. `owner_id` NULL means the whole workspace.

Deliberately not a general objectives model with key results and cascading
parents — a CRM goal is "book £2m this quarter", and the elaborate version is a
different product.

Progress is computed from **the same scoped aggregate the dashboards use**, over
the goal's own window, so a goal card and a KPI tile can never disagree about
the same number.

Note the inversion against snapshots: on a snapshot, `owner_id IS NULL` means
the record has no owner; on a goal it means the target belongs to everyone. The
two tables sit next to each other, so the difference is worth stating — a
snapshot describes what happened, a goal describes an intention, and a workspace
can hold an intention.

Creating a goal requires `reports.export`, the closest thing to an analytics
admin grant the matrix has. Setting a team's number is a management act, not a
reading one.

---

## 11. API

All under `/api/v1/analytics`. Every endpoint takes the same period parameters
(`?period=today|week|month|quarter|year|custom&start=&end=`) and echoes the
resolved window back — a client that renders "this month" without being told
which month is one timezone bug from lying.

| Method | Path                     | Permission        | Returns                                  |
| ------ | ------------------------ | ----------------- | ---------------------------------------- |
| GET    | `/metrics`               | authenticated     | the metric catalogue                     |
| GET    | `/kpis`                  | `reports.view`    | every visible metric, with deltas        |
| GET    | `/series/{metric_key}`   | `reports.view`    | daily points, snapshots + today live     |
| GET    | `/dashboards`            | authenticated     | which dashboards exist                   |
| GET    | `/dashboards/{key}`      | `reports.view`    | one assembled dashboard                  |
| GET    | `/funnel`                | `reports.view`    | stages, velocity, sources                |
| GET    | `/win-loss`              | `reports.view`    | won, lost, rate, reasons                 |
| GET    | `/forecast`              | `reports.view`    | booked, weighted, projected              |
| GET    | `/goals`                 | `reports.view`    | goals in force, with progress            |
| POST   | `/goals`                 | `reports.export`  | create a target                          |
| DELETE | `/goals/{id}`            | `reports.export`  | —                                        |

Numeric values cross the wire as **strings**, matching how the rest of the API
carries money.

---

## 12. Operations

**`snapshot_metrics`** — cron, 00:07 UTC daily. Writes *yesterday* for every
active owner in every tenant. Yesterday, not today: a snapshot for the day that
just ended is complete, while one for the current day is a partial reading the
next run would have to correct. Staggered off `:00` so it does not fire
alongside the quarter-hour sweeps.

Each tenant is processed inside `tenant_scope` under RLS, with a
`system_context` holding only the six view grants the snapshot reads. The writer
records numbers; it has no grant to change anything.

**`backfill_metrics(organization_id, days)`** — manual. Rebuilds history after a
deployment gap, bounded at 30 days per run so one call cannot occupy a worker
indefinitely. Safe to re-run: the upsert corrects rows rather than duplicating
them.

Both tables are tenant-scoped with RLS `ENABLE` + `FORCE` and the standard
`tenant_isolation` policy, like every business table.

Related: [JOBS.md](JOBS.md) for worker semantics, [PERMISSIONS.md](PERMISSIONS.md)
for scope resolution, [DATABASE.md](DATABASE.md) for the RLS model.
