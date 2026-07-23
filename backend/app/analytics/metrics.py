"""The metric registry.

One declaration per number the product reports, shared by the live aggregates,
the nightly snapshot writer, the dashboards and the report builder. The same
reason the automation registries are served rather than duplicated: four
hand-maintained lists of "what is pipeline value and how do you chart it" drift,
and the symptom is a dashboard and a report that disagree about revenue.

**The distinction that carries the file is `flow` versus `level`.**

  * A **flow** accumulates over a period. Leads created, deals won, revenue
    booked. Summing a flow across days is the right thing to do, and a chart of
    one is a bar per day.
  * A **level** is a reading at an instant. Open pipeline value, open lead
    count. Summing a level across days is *meaningless* — adding today's
    pipeline to yesterday's counts the same deals twice — so a chart of one is a
    line, and a period rollup takes the latest reading rather than the total.

Getting that wrong produces numbers that look plausible and are nonsense, which
is worse than an obvious error. Encoding it here means the aggregation layer can
refuse to sum a level rather than relying on every caller to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MetricKind = Literal["flow", "level"]

#: How a value should be rendered, and what "bigger" means.
MetricUnit = Literal["count", "currency", "percent", "days"]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    label: str
    description: str
    kind: MetricKind
    unit: MetricUnit
    category: str
    #: Which permission governs seeing this number. Analytics reuses the
    #: entity's own grant rather than inventing a separate "analytics scope",
    #: so a total can never exceed what the caller could reach by browsing.
    permission: str
    #: False when a rise is bad — cycle time, lost deals. The UI colours deltas
    #: from this rather than guessing from the metric name.
    higher_is_better: bool = True

    @property
    def summable(self) -> bool:
        """Whether adding this across periods produces a meaningful number."""
        return self.kind == "flow"


def _metric(
    key: str,
    label: str,
    description: str,
    kind: MetricKind,
    unit: MetricUnit,
    category: str,
    permission: str,
    *,
    higher_is_better: bool = True,
) -> MetricDefinition:
    return MetricDefinition(
        key=key,
        label=label,
        description=description,
        kind=kind,
        unit=unit,
        category=category,
        permission=permission,
        higher_is_better=higher_is_better,
    )


METRICS: tuple[MetricDefinition, ...] = (
    # ------------------------------------------------------------- leads
    _metric(
        "leads_created", "Leads created",
        "New leads added in the period.",
        "flow", "count", "Leads", "leads.view",
    ),
    _metric(
        "leads_converted", "Leads converted",
        "Leads that became clients. The funnel event, not a status edit.",
        "flow", "count", "Leads", "leads.view",
    ),
    _metric(
        "leads_open", "Open leads",
        "Leads still being worked, as at the reading.",
        "level", "count", "Leads", "leads.view",
    ),
    _metric(
        "lead_conversion_rate", "Lead conversion rate",
        "Converted leads as a share of leads created in the period.",
        "flow", "percent", "Leads", "leads.view",
    ),
    # ------------------------------------------------------------- deals
    _metric(
        "deals_created", "Deals opened",
        "Deals opened in the period.",
        "flow", "count", "Deals", "deals.view",
    ),
    _metric(
        "deals_won", "Deals won",
        "Deals that reached a winning stage in the period.",
        "flow", "count", "Deals", "deals.view",
    ),
    _metric(
        "deals_lost", "Deals lost",
        "Deals that reached a losing stage in the period.",
        "flow", "count", "Deals", "deals.view",
        higher_is_better=False,
    ),
    _metric(
        "revenue_won", "Revenue won",
        "Value of deals won in the period.",
        "flow", "currency", "Revenue", "deals.view",
    ),
    _metric(
        "commission_earned", "Commission earned",
        "Commission on deals won in the period, as agreed rather than recomputed.",
        "flow", "currency", "Revenue", "deals.view",
    ),
    _metric(
        "pipeline_open_value", "Open pipeline",
        "Total value of open deals, as at the reading.",
        "level", "currency", "Revenue", "deals.view",
    ),
    _metric(
        "pipeline_weighted_value", "Weighted pipeline",
        "Open deal value multiplied by each deal's probability.",
        "level", "currency", "Revenue", "deals.view",
    ),
    _metric(
        "win_rate", "Win rate",
        "Won deals as a share of deals that closed in the period.",
        "flow", "percent", "Deals", "deals.view",
    ),
    _metric(
        "average_deal_value", "Average deal size",
        "Mean value of deals won in the period.",
        "flow", "currency", "Revenue", "deals.view",
    ),
    _metric(
        "sales_cycle_days", "Sales cycle",
        "Mean days from a deal opening to it being won.",
        "flow", "days", "Deals", "deals.view",
        higher_is_better=False,
    ),
    # -------------------------------------------------------- properties
    _metric(
        "listings_active", "Active listings",
        "Listings on the market, as at the reading.",
        "level", "count", "Properties", "properties.view",
    ),
    _metric(
        "listings_sold", "Listings sold",
        "Listings that moved to sold in the period.",
        "flow", "count", "Properties", "properties.view",
    ),
    _metric(
        "average_days_on_market", "Days on market",
        "Mean days listed for listings still active.",
        "level", "days", "Properties", "properties.view",
        higher_is_better=False,
    ),
    # ---------------------------------------------------------- activity
    _metric(
        "tasks_completed", "Tasks completed",
        "Tasks finished in the period.",
        "flow", "count", "Productivity", "tasks.view",
    ),
    _metric(
        "tasks_overdue", "Overdue tasks",
        "Tasks past their due date and not done, as at the reading.",
        "level", "count", "Productivity", "tasks.view",
        higher_is_better=False,
    ),
    _metric(
        "activities_logged", "Activities logged",
        "Calls, emails, meetings and showings recorded in the period.",
        "flow", "count", "Productivity", "activities.view",
    ),
    _metric(
        "clients_created", "Clients added",
        "New clients in the period.",
        "flow", "count", "Clients", "contacts.view",
    ),
)

METRICS_BY_KEY: dict[str, MetricDefinition] = {m.key: m for m in METRICS}

#: Metrics the nightly snapshot writes. Derived ratios (`win_rate`,
#: `lead_conversion_rate`, `average_deal_value`) are deliberately absent: a
#: ratio stored per day cannot be re-aggregated — the average of daily win rates
#: is not the win rate for the month — so they are computed from their
#: components at read time instead.
SNAPSHOT_METRICS: tuple[str, ...] = (
    "leads_created",
    "leads_converted",
    "leads_open",
    "deals_created",
    "deals_won",
    "deals_lost",
    "revenue_won",
    "commission_earned",
    "pipeline_open_value",
    "pipeline_weighted_value",
    "listings_active",
    "listings_sold",
    "tasks_completed",
    "tasks_overdue",
    "activities_logged",
    "clients_created",
)

#: Ratios and the snapshotted components each is computed from, so a period
#: rollup recomputes the ratio rather than averaging averages.
#:
#: Every name here must be a real snapshot metric — `win_rate` is deals_won over
#: *closed*, and "closed" is not stored, so its components are the two counts
#: that sum to it. `validate_registry` enforces the reference, which is how the
#: previous non-existent `deals_closed` entry was caught.
DERIVED_METRICS: dict[str, tuple[str, str]] = {
    "lead_conversion_rate": ("leads_converted", "leads_created"),
    "win_rate": ("deals_won", "deals_lost"),
    "average_deal_value": ("revenue_won", "deals_won"),
}


def validate_registry() -> None:
    """Fail at import if a snapshot or derived metric is not declared.

    A typo here would otherwise become a metric the writer stores and no reader
    can name, or a ratio whose components silently do not exist.
    """
    unknown = set(SNAPSHOT_METRICS) - set(METRICS_BY_KEY)
    if unknown:
        raise ValueError(f"SNAPSHOT_METRICS names unknown metrics: {sorted(unknown)}")

    for ratio, components in DERIVED_METRICS.items():
        if ratio not in METRICS_BY_KEY:
            raise ValueError(f"DERIVED_METRICS names an unknown metric: {ratio}")
        # Both components, not just the numerator. A ratio whose denominator is
        # never snapshotted cannot be derived for any historical day — it would
        # render for today and go blank the moment anyone looked backwards.
        for component in components:
            if component not in SNAPSHOT_METRICS:
                raise ValueError(f"{ratio} is derived from unsnapshotted {component}")

    for metric in METRICS:
        if metric.kind == "level" and metric.key in DERIVED_METRICS:
            raise ValueError(f"{metric.key} cannot be both a level and a ratio")


validate_registry()
