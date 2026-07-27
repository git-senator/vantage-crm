"""The deterministic growth-intelligence engine.

The org-level counterpart of the per-record engines (`lead_scoring.py`,
`deal_scoring.py`, `property_scoring.py`), and it holds to the same rule: the
number is a sum of signals, each carrying its reason, computed by rules with no
model, no randomness and no network. The same workspace metrics always produce
the same growth read, and every figure reads back as the reasons behind it.

Where the per-record engines score one entity, this one scores the *business*:
a single growth score for a workspace (or a manager's slice of it), built from
the aggregate metrics the Analytics Engine already computes. It reasons over the
whole funnel at once — leads, deals, properties, revenue, pipeline, activity —
which is exactly why its inputs are aggregates rather than a single record.

The inputs enter as features, not queries. `GrowthFeatures` is filled from
`AnalyticsService.kpis` and the pipeline breakdown — the same numbers the
dashboards render — so nothing here recomputes an analytics figure. This module
reads no database; the service does the (scoped) analytics reads and hands the
result in.

What it produces:

  * **growth score** (0-100) — the sum of a signal registry spanning conversion,
    win rate, revenue trend, pipeline coverage, sales cycle, activity, deal flow
    and task hygiene.
  * **revenue signals** and **pipeline insights** — explainable statements about
    where revenue and the pipeline stand, the org-level analog of a property's
    strengths and weaknesses.
  * **risks** and **recommendations** — the same explainability primitives every
    other engine uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

# Shared explainability primitives — the same records the other engines use.
from app.ai.explain import Recommendation, RiskFlag, ScoredSignal

MIN_SCORE = 0
MAX_SCORE = 100

RULES_VERSION = "growth-rules-v1"


@dataclass(frozen=True, slots=True)
class StageSnapshot:
    """One pipeline stage's open position, from the Analytics Engine's breakdown.

    `mean_days_in_stage` is the stage velocity — `None` when there is not yet
    enough history to have a norm.
    """

    name: str
    deal_count: int
    open_value: Decimal
    mean_days_in_stage: float | None


@dataclass(frozen=True, slots=True)
class GrowthFeatures:
    """The inputs the rules read. Pure aggregates, resolved under scope elsewhere.

    Rates are percentages (0-100) or `None` when the underlying denominator was
    zero — "no leads to convert" is not "a 0% conversion rate". Deltas are the
    percent change against the previous period, `None` when there is nothing to
    compare against.
    """

    lead_conversion_rate: float | None
    win_rate: float | None
    sales_cycle_days: float | None
    revenue_won: Decimal
    revenue_delta_pct: float | None
    prior_revenue_won: Decimal | None
    deals_won: int
    deals_lost: int
    deals_created: int
    leads_created: int
    leads_converted: int
    pipeline_open_value: Decimal
    pipeline_weighted_value: Decimal
    average_deal_value: Decimal | None
    commission_earned: Decimal
    activities_logged: int
    activities_delta_pct: float | None
    tasks_completed: int
    tasks_overdue: int
    listings_active: int
    listings_sold: int
    pipeline_stages: tuple[StageSnapshot, ...] = ()
    period_label: str = "the period"


@dataclass(frozen=True, slots=True)
class GrowthHealth:
    """The full, explainable result."""

    score: int
    #: A label from the score band — thriving / steady / at_risk / struggling.
    band: str
    signals: list[ScoredSignal] = field(default_factory=list)
    #: Explainable statements about revenue and the pipeline — the org-level
    #: analog of a property's strengths and weaknesses.
    revenue_signals: list[str] = field(default_factory=list)
    pipeline_insights: list[str] = field(default_factory=list)
    risks: list[RiskFlag] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    scorer: str = RULES_VERSION

    @property
    def top_reasons(self) -> list[str]:
        ranked = sorted(self.signals, key=lambda s: abs(s.points), reverse=True)
        return [s.reason for s in ranked[:3]]


# --------------------------------------------------------------- signals


@dataclass(frozen=True, slots=True)
class Signal:
    key: str
    label: str
    evaluate: object  # Callable[[GrowthFeatures], tuple[int, str] | None]


def _pct(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _coverage(f: GrowthFeatures) -> float | None:
    """Weighted pipeline as a multiple of last period's revenue — "how much
    forecastable work stands behind what we just booked". `None` with no prior
    revenue to divide by."""
    if f.prior_revenue_won is None or f.prior_revenue_won <= 0:
        return None
    return float(f.pipeline_weighted_value / f.prior_revenue_won)


def _conversion(f: GrowthFeatures) -> tuple[int, str] | None:
    r = f.lead_conversion_rate
    if r is None:
        return 0, "No leads created this period to convert."
    if r >= 25:
        return 18, f"Strong lead conversion at {r:.0f}%."
    if r >= 15:
        return 13, f"Healthy lead conversion at {r:.0f}%."
    if r >= 8:
        return 8, f"Moderate lead conversion at {r:.0f}%."
    if r > 0:
        return 3, f"Low lead conversion at {r:.0f}%."
    return 0, "No leads converted this period."


def _win_rate(f: GrowthFeatures) -> tuple[int, str] | None:
    r = f.win_rate
    if r is None:
        return 0, "No deals closed this period."
    if r >= 50:
        return 18, f"Strong win rate at {r:.0f}%."
    if r >= 35:
        return 13, f"Healthy win rate at {r:.0f}%."
    if r >= 20:
        return 8, f"Moderate win rate at {r:.0f}%."
    return 3, f"Low win rate at {r:.0f}%."


def _revenue_trend(f: GrowthFeatures) -> tuple[int, str] | None:
    d = f.revenue_delta_pct
    if d is None:
        # No prior period to compare — score the presence of revenue, not a trend.
        return (10, "Revenue booked this period.") if f.revenue_won > 0 else (
            0,
            "No revenue booked this period.",
        )
    if d >= 10:
        return 18, f"Revenue up {d:.0f}% on the previous period."
    if d > 0:
        return 12, f"Revenue up {d:.0f}% on the previous period."
    if d == 0:
        return 8, "Revenue flat on the previous period."
    if d > -15:
        return 0, f"Revenue down {abs(d):.0f}% on the previous period."
    return -10, f"Revenue down {abs(d):.0f}% on the previous period."


def _pipeline_coverage(f: GrowthFeatures) -> tuple[int, str] | None:
    c = _coverage(f)
    if c is None:
        return (6, "Pipeline is building.") if f.pipeline_weighted_value > 0 else (
            -4,
            "No weighted pipeline to carry future revenue.",
        )
    if c >= 1.5:
        return 16, f"Weighted pipeline covers {c:.1f}x last period's revenue."
    if c >= 1.0:
        return 11, f"Weighted pipeline covers {c:.1f}x last period's revenue."
    if c >= 0.5:
        return 5, f"Weighted pipeline covers {c:.1f}x last period's revenue."
    return -6, f"Thin pipeline — only {c:.1f}x last period's revenue."


def _sales_cycle(f: GrowthFeatures) -> tuple[int, str] | None:
    d = f.sales_cycle_days
    if d is None:
        return 0, "No completed sales cycle to measure."
    if d <= 30:
        return 12, f"Fast sales cycle at {d:.0f} days."
    if d <= 60:
        return 8, f"Reasonable sales cycle at {d:.0f} days."
    if d <= 90:
        return 3, f"Slow sales cycle at {d:.0f} days."
    return -3, f"Long sales cycle at {d:.0f} days."


def _activity(f: GrowthFeatures) -> tuple[int, str] | None:
    d = f.activities_delta_pct
    if f.activities_logged == 0:
        return -6, "No activity logged this period."
    if d is None:
        return 6, f"{f.activities_logged} activities logged."
    if d >= 0:
        return 12, f"Activity up {d:.0f}% — {f.activities_logged} logged."
    if d > -25:
        return 6, f"Activity down {abs(d):.0f}% but steady."
    return -6, f"Activity down {abs(d):.0f}% on the previous period."


def _deal_flow(f: GrowthFeatures) -> tuple[int, str] | None:
    """Pipeline replenishment: are new deals being opened to replace the ones
    closing? A period that closes deals but opens none is drawing the pipeline
    down."""
    closed = f.deals_won + f.deals_lost
    if f.deals_created == 0:
        return (-4, "No new deals opened this period.") if closed > 0 else (
            0,
            "No deal activity this period.",
        )
    if closed == 0:
        return 8, f"{f.deals_created} new deals opened."
    ratio = f.deals_created / closed
    if ratio >= 1.0:
        return 12, f"{f.deals_created} deals opened against {closed} closed — replenishing."
    return 5, f"{f.deals_created} deals opened against {closed} closed."


def _task_hygiene(f: GrowthFeatures) -> tuple[int, str] | None:
    overdue = f.tasks_overdue
    if overdue == 0:
        return 6, "No overdue tasks."
    if overdue <= 5:
        return 2, f"{overdue} overdue task(s)."
    if overdue <= 15:
        return -3, f"{overdue} overdue tasks are piling up."
    return -8, f"{overdue} overdue tasks — follow-up is slipping."


SIGNALS: list[Signal] = [
    Signal("conversion", "Lead conversion", _conversion),
    Signal("win_rate", "Win rate", _win_rate),
    Signal("revenue_trend", "Revenue trend", _revenue_trend),
    Signal("pipeline_coverage", "Pipeline coverage", _pipeline_coverage),
    Signal("sales_cycle", "Sales cycle", _sales_cycle),
    Signal("activity", "Activity", _activity),
    Signal("deal_flow", "Deal flow", _deal_flow),
    Signal("task_hygiene", "Task hygiene", _task_hygiene),
]


# -------------------------------------------------------- derived reads


def _band(score: int) -> str:
    if score >= 75:
        return "thriving"
    if score >= 55:
        return "steady"
    if score >= 35:
        return "at_risk"
    return "struggling"


def _revenue_signals(f: GrowthFeatures) -> list[str]:
    signals: list[str] = []
    if f.revenue_delta_pct is not None:
        direction = "up" if f.revenue_delta_pct >= 0 else "down"
        signals.append(
            f"Revenue {f.revenue_won:,.0f} won {f.period_label}, "
            f"{direction} {abs(f.revenue_delta_pct):.0f}% on the previous period."
        )
    elif f.revenue_won > 0:
        signals.append(f"Revenue {f.revenue_won:,.0f} won {f.period_label}.")
    if f.commission_earned > 0:
        signals.append(f"Commission earned {f.commission_earned:,.0f} {f.period_label}.")
    if f.average_deal_value is not None:
        signals.append(f"Average deal size {f.average_deal_value:,.0f}.")
    coverage = _coverage(f)
    if coverage is not None:
        signals.append(
            f"Weighted pipeline {f.pipeline_weighted_value:,.0f} covers "
            f"{coverage:.1f}x last period's revenue."
        )
    elif f.pipeline_weighted_value > 0:
        signals.append(
            f"Weighted pipeline stands at {f.pipeline_weighted_value:,.0f}."
        )
    return signals


def _pipeline_insights(f: GrowthFeatures) -> list[str]:
    stages = [s for s in f.pipeline_stages if s.deal_count > 0]
    if not stages:
        return []
    insights: list[str] = []

    heaviest = max(stages, key=lambda s: s.open_value)
    insights.append(
        f"{heaviest.name} holds the most open value at {heaviest.open_value:,.0f} "
        f"across {heaviest.deal_count} deal(s)."
    )

    timed = [s for s in stages if s.mean_days_in_stage is not None]
    if timed:
        slowest = max(timed, key=lambda s: s.mean_days_in_stage or 0.0)
        if slowest.mean_days_in_stage and slowest.mean_days_in_stage > 0:
            note = (
                " — the likely bottleneck."
                if slowest.name == heaviest.name
                else "."
            )
            insights.append(
                f"{slowest.name} is the slowest stage at "
                f"{slowest.mean_days_in_stage:.0f} days on average{note}"
            )
    return insights


def _risks(f: GrowthFeatures) -> list[RiskFlag]:
    risks: list[RiskFlag] = []
    if f.revenue_delta_pct is not None and f.revenue_delta_pct <= -15:
        risks.append(
            RiskFlag(
                "revenue_declining",
                "Revenue declining",
                f"Down {abs(f.revenue_delta_pct):.0f}% on the previous period.",
            )
        )
    if f.win_rate is not None and f.win_rate < 20:
        risks.append(
            RiskFlag("low_win_rate", "Low win rate", f"Only {f.win_rate:.0f}% of closed deals won.")
        )
    coverage = _coverage(f)
    if (coverage is not None and coverage < 0.5) or (
        coverage is None and f.pipeline_weighted_value <= 0
    ):
        risks.append(
            RiskFlag(
                "thin_pipeline",
                "Thin pipeline",
                "Weighted pipeline is light against recent revenue.",
            )
        )
    if f.sales_cycle_days is not None and f.sales_cycle_days > 90:
        risks.append(
            RiskFlag(
                "long_sales_cycle",
                "Long sales cycle",
                f"Deals take {f.sales_cycle_days:.0f} days on average to win.",
            )
        )
    if f.tasks_overdue > 15:
        risks.append(
            RiskFlag(
                "overdue_tasks",
                "Overdue tasks piling up",
                f"{f.tasks_overdue} tasks are past due.",
            )
        )
    if f.activities_delta_pct is not None and f.activities_delta_pct <= -25:
        risks.append(
            RiskFlag(
                "activity_falling",
                "Activity falling",
                f"Logged activity down {abs(f.activities_delta_pct):.0f}%.",
            )
        )
    if (
        f.lead_conversion_rate is not None
        and f.lead_conversion_rate < 5
        and f.leads_created > 0
    ):
        risks.append(
            RiskFlag(
                "weak_conversion",
                "Weak lead conversion",
                f"Only {f.lead_conversion_rate:.0f}% of new leads converted.",
            )
        )
    return risks


def _recommendations(f: GrowthFeatures, risks: list[RiskFlag]) -> list[Recommendation]:
    recs: list[Recommendation] = []
    keys = {r.key for r in risks}

    if "thin_pipeline" in keys:
        recs.append(
            Recommendation(
                "Build pipeline",
                "Weighted pipeline is light against recent revenue; open and "
                "qualify more deals to protect next period.",
                "high",
            )
        )
    if "revenue_declining" in keys:
        recs.append(
            Recommendation(
                "Focus on closing",
                "Revenue fell on the previous period; prioritise deals near their "
                "close date.",
                "high",
            )
        )
    if "low_win_rate" in keys:
        recs.append(
            Recommendation(
                "Review qualification and losses",
                "A low win rate points to weak qualification or a repeated loss "
                "reason worth investigating.",
                "medium",
            )
        )
    if "long_sales_cycle" in keys:
        recs.append(
            Recommendation(
                "Unblock the slowest stage",
                "Deals are spending a long time in the pipeline; target the "
                "bottleneck stage.",
                "medium",
            )
        )
    if "weak_conversion" in keys:
        recs.append(
            Recommendation(
                "Improve lead follow-up",
                "Few new leads are converting; tighten first-contact speed and "
                "nurture.",
                "medium",
            )
        )
    if "overdue_tasks" in keys:
        recs.append(
            Recommendation(
                "Clear the overdue backlog",
                "A growing overdue-task pile means follow-up is slipping.",
                "medium",
            )
        )
    if "activity_falling" in keys:
        recs.append(
            Recommendation(
                "Lift outreach",
                "Logged activity is falling, which leads pipeline down with it.",
                "low",
            )
        )
    return recs


# --------------------------------------------------------------- engine


class GrowthScorer(Protocol):
    """What every growth scorer implements — the seam an ML model slots into,
    producing the same explainable `GrowthHealth` from the same `GrowthFeatures`."""

    version: str

    def score(self, features: GrowthFeatures) -> GrowthHealth: ...


class RuleBasedGrowthScorer:
    version = RULES_VERSION

    def score(self, features: GrowthFeatures) -> GrowthHealth:
        signals: list[ScoredSignal] = []
        total = 0
        for signal in SIGNALS:
            outcome = signal.evaluate(features)  # type: ignore[operator]
            if outcome is None:
                continue
            points, reason = outcome
            total += points
            signals.append(ScoredSignal(signal.key, signal.label, points, reason))

        score = max(MIN_SCORE, min(MAX_SCORE, total))
        risks = _risks(features)

        return GrowthHealth(
            score=score,
            band=_band(score),
            signals=signals,
            revenue_signals=_revenue_signals(features),
            pipeline_insights=_pipeline_insights(features),
            risks=risks,
            recommendations=_recommendations(features, risks),
            scorer=self.version,
        )


#: The active scorer. A deployment swaps in an ML scorer by replacing this — the
#: only line that would change to move from rules to a model.
DEFAULT_SCORER: GrowthScorer = RuleBasedGrowthScorer()


def score_growth(
    features: GrowthFeatures, scorer: GrowthScorer | None = None
) -> GrowthHealth:
    return (scorer or DEFAULT_SCORER).score(features)


__all__ = [
    "DEFAULT_SCORER",
    "MAX_SCORE",
    "MIN_SCORE",
    "RULES_VERSION",
    "SIGNALS",
    "GrowthFeatures",
    "GrowthHealth",
    "GrowthScorer",
    "RuleBasedGrowthScorer",
    "Signal",
    "StageSnapshot",
    "score_growth",
]
