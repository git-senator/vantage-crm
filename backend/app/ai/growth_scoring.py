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
from app.ai.growth_i18n import DEFAULT_LOCALE, render

MIN_SCORE = 0
MAX_SCORE = 100

RULES_VERSION = "growth-rules-v1"


@dataclass(frozen=True, slots=True)
class Reason:
    """A generated statement carrying its localization key and parameters.

    `text` is the rendered English — the fallback, and what the AI briefing and
    the stored breakdown read. `key`/`params` let the API boundary re-render the
    same statement in the caller's locale.
    """

    text: str
    key: str = ""
    params: dict = field(default_factory=dict)  # type: ignore[type-arg]


def _reason(key: str, params: dict | None = None) -> Reason:  # type: ignore[type-arg]
    """Build a `Reason`, rendering its English text from the shared catalog."""
    params = params or {}
    return Reason(render(DEFAULT_LOCALE, key, params), key, params)


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
    #: analog of a property's strengths and weaknesses. Each carries its own
    #: localization key; `.text` is the English rendering.
    revenue_signals: list[Reason] = field(default_factory=list)
    pipeline_insights: list[Reason] = field(default_factory=list)
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


# Each signal returns (points, template key, params) — never a finished
# sentence. The wording lives in the catalog (`growth_i18n.py`); params are
# pre-formatted strings so a template only interpolates.
_Outcome = tuple[int, str, dict]  # type: ignore[type-arg]


def _conversion(f: GrowthFeatures) -> _Outcome | None:
    r = f.lead_conversion_rate
    if r is None:
        return 0, "conversion_none", {}
    if r >= 25:
        return 18, "conversion_strong", {"r": f"{r:.0f}"}
    if r >= 15:
        return 13, "conversion_healthy", {"r": f"{r:.0f}"}
    if r >= 8:
        return 8, "conversion_moderate", {"r": f"{r:.0f}"}
    if r > 0:
        return 3, "conversion_low", {"r": f"{r:.0f}"}
    return 0, "conversion_zero", {}


def _win_rate(f: GrowthFeatures) -> _Outcome | None:
    r = f.win_rate
    if r is None:
        return 0, "win_none", {}
    if r >= 50:
        return 18, "win_strong", {"r": f"{r:.0f}"}
    if r >= 35:
        return 13, "win_healthy", {"r": f"{r:.0f}"}
    if r >= 20:
        return 8, "win_moderate", {"r": f"{r:.0f}"}
    return 3, "win_low", {"r": f"{r:.0f}"}


def _revenue_trend(f: GrowthFeatures) -> _Outcome | None:
    d = f.revenue_delta_pct
    if d is None:
        # No prior period to compare — score the presence of revenue, not a trend.
        return (10, "rev_booked", {}) if f.revenue_won > 0 else (0, "rev_none", {})
    if d >= 10:
        return 18, "rev_up", {"d": f"{d:.0f}"}
    if d > 0:
        return 12, "rev_up", {"d": f"{d:.0f}"}
    if d == 0:
        return 8, "rev_flat", {}
    if d > -15:
        return 0, "rev_down", {"d": f"{abs(d):.0f}"}
    return -10, "rev_down", {"d": f"{abs(d):.0f}"}


def _pipeline_coverage(f: GrowthFeatures) -> _Outcome | None:
    c = _coverage(f)
    if c is None:
        return (6, "pipe_building", {}) if f.pipeline_weighted_value > 0 else (
            -4,
            "pipe_none",
            {},
        )
    if c >= 1.5:
        return 16, "pipe_covers", {"x": f"{c:.1f}"}
    if c >= 1.0:
        return 11, "pipe_covers", {"x": f"{c:.1f}"}
    if c >= 0.5:
        return 5, "pipe_covers", {"x": f"{c:.1f}"}
    return -6, "pipe_thin", {"x": f"{c:.1f}"}


def _sales_cycle(f: GrowthFeatures) -> _Outcome | None:
    d = f.sales_cycle_days
    if d is None:
        return 0, "cycle_none", {}
    if d <= 30:
        return 12, "cycle_fast", {"d": f"{d:.0f}"}
    if d <= 60:
        return 8, "cycle_reasonable", {"d": f"{d:.0f}"}
    if d <= 90:
        return 3, "cycle_slow", {"d": f"{d:.0f}"}
    return -3, "cycle_long", {"d": f"{d:.0f}"}


def _activity(f: GrowthFeatures) -> _Outcome | None:
    d = f.activities_delta_pct
    if f.activities_logged == 0:
        return -6, "act_none", {}
    if d is None:
        return 6, "act_logged", {"n": f.activities_logged}
    if d >= 0:
        return 12, "act_up", {"d": f"{d:.0f}", "n": f.activities_logged}
    if d > -25:
        return 6, "act_steady", {"d": f"{abs(d):.0f}"}
    return -6, "act_down", {"d": f"{abs(d):.0f}"}


def _deal_flow(f: GrowthFeatures) -> _Outcome | None:
    """Pipeline replenishment: are new deals being opened to replace the ones
    closing? A period that closes deals but opens none is drawing the pipeline
    down."""
    closed = f.deals_won + f.deals_lost
    if f.deals_created == 0:
        return (-4, "flow_no_new", {}) if closed > 0 else (0, "flow_none", {})
    if closed == 0:
        return 8, "flow_opened", {"n": f.deals_created}
    ratio = f.deals_created / closed
    if ratio >= 1.0:
        return 12, "flow_replenishing", {"n": f.deals_created, "c": closed}
    return 5, "flow_opened_vs", {"n": f.deals_created, "c": closed}


def _task_hygiene(f: GrowthFeatures) -> _Outcome | None:
    overdue = f.tasks_overdue
    if overdue == 0:
        return 6, "task_none", {}
    if overdue <= 5:
        return 2, "task_few", {"n": overdue}
    if overdue <= 15:
        return -3, "task_piling", {"n": overdue}
    return -8, "task_slipping", {"n": overdue}


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


def _revenue_signals(f: GrowthFeatures) -> list[Reason]:
    # `period` carries the English period label; the API boundary localizes it.
    signals: list[Reason] = []
    if f.revenue_delta_pct is not None:
        key = "rs_revenue_up" if f.revenue_delta_pct >= 0 else "rs_revenue_down"
        signals.append(
            _reason(
                key,
                {
                    "rev": f"{f.revenue_won:,.0f}",
                    "period": f.period_label,
                    "pct": f"{abs(f.revenue_delta_pct):.0f}",
                },
            )
        )
    elif f.revenue_won > 0:
        signals.append(
            _reason("rs_revenue_flat", {"rev": f"{f.revenue_won:,.0f}", "period": f.period_label})
        )
    if f.commission_earned > 0:
        signals.append(
            _reason(
                "rs_commission",
                {"c": f"{f.commission_earned:,.0f}", "period": f.period_label},
            )
        )
    if f.average_deal_value is not None:
        signals.append(_reason("rs_avg", {"v": f"{f.average_deal_value:,.0f}"}))
    coverage = _coverage(f)
    if coverage is not None:
        signals.append(
            _reason(
                "rs_coverage",
                {"v": f"{f.pipeline_weighted_value:,.0f}", "x": f"{coverage:.1f}"},
            )
        )
    elif f.pipeline_weighted_value > 0:
        signals.append(_reason("rs_stands", {"v": f"{f.pipeline_weighted_value:,.0f}"}))
    return signals


def _pipeline_insights(f: GrowthFeatures) -> list[Reason]:
    # `stage` carries the English stage name; the API boundary localizes it.
    stages = [s for s in f.pipeline_stages if s.deal_count > 0]
    if not stages:
        return []
    insights: list[Reason] = []

    heaviest = max(stages, key=lambda s: s.open_value)
    insights.append(
        _reason(
            "pi_heaviest",
            {
                "stage": heaviest.name,
                "value": f"{heaviest.open_value:,.0f}",
                "n": heaviest.deal_count,
            },
        )
    )

    timed = [s for s in stages if s.mean_days_in_stage is not None]
    if timed:
        slowest = max(timed, key=lambda s: s.mean_days_in_stage or 0.0)
        if slowest.mean_days_in_stage and slowest.mean_days_in_stage > 0:
            key = (
                "pi_slowest_bottleneck"
                if slowest.name == heaviest.name
                else "pi_slowest"
            )
            insights.append(
                _reason(
                    key,
                    {"stage": slowest.name, "days": f"{slowest.mean_days_in_stage:.0f}"},
                )
            )
    return insights


def _risk(risk_key: str, params: dict | None = None) -> RiskFlag:  # type: ignore[type-arg]
    """A `RiskFlag` with English label/detail rendered from the catalog, plus the
    keys the API boundary re-renders for the caller's locale."""
    params = params or {}
    label_key = f"risk_{risk_key}_label"
    detail_key = f"risk_{risk_key}_detail"
    return RiskFlag(
        risk_key,
        render(DEFAULT_LOCALE, label_key),
        render(DEFAULT_LOCALE, detail_key, params),
        label_key=label_key,
        detail_key=detail_key,
        i18n_params=params,
    )


def _risks(f: GrowthFeatures) -> list[RiskFlag]:
    risks: list[RiskFlag] = []
    if f.revenue_delta_pct is not None and f.revenue_delta_pct <= -15:
        risks.append(_risk("revenue_declining", {"pct": f"{abs(f.revenue_delta_pct):.0f}"}))
    if f.win_rate is not None and f.win_rate < 20:
        risks.append(_risk("low_win_rate", {"pct": f"{f.win_rate:.0f}"}))
    coverage = _coverage(f)
    if (coverage is not None and coverage < 0.5) or (
        coverage is None and f.pipeline_weighted_value <= 0
    ):
        risks.append(_risk("thin_pipeline"))
    if f.sales_cycle_days is not None and f.sales_cycle_days > 90:
        risks.append(_risk("long_sales_cycle", {"days": f"{f.sales_cycle_days:.0f}"}))
    if f.tasks_overdue > 15:
        risks.append(_risk("overdue_tasks", {"n": f.tasks_overdue}))
    if f.activities_delta_pct is not None and f.activities_delta_pct <= -25:
        risks.append(_risk("activity_falling", {"pct": f"{abs(f.activities_delta_pct):.0f}"}))
    if (
        f.lead_conversion_rate is not None
        and f.lead_conversion_rate < 5
        and f.leads_created > 0
    ):
        risks.append(_risk("weak_conversion", {"pct": f"{f.lead_conversion_rate:.0f}"}))
    return risks


def _rec(risk_key: str, priority: str) -> Recommendation:
    """A `Recommendation` with English action/reason rendered from the catalog,
    plus the keys the API boundary re-renders for the caller's locale."""
    action_key = f"rec_{risk_key}_action"
    reason_key = f"rec_{risk_key}_reason"
    return Recommendation(
        render(DEFAULT_LOCALE, action_key),
        render(DEFAULT_LOCALE, reason_key),
        priority,
        action_key=action_key,
        reason_key=reason_key,
    )


def _recommendations(f: GrowthFeatures, risks: list[RiskFlag]) -> list[Recommendation]:
    recs: list[Recommendation] = []
    keys = {r.key for r in risks}

    if "thin_pipeline" in keys:
        recs.append(_rec("thin_pipeline", "high"))
    if "revenue_declining" in keys:
        recs.append(_rec("revenue_declining", "high"))
    if "low_win_rate" in keys:
        recs.append(_rec("low_win_rate", "medium"))
    if "long_sales_cycle" in keys:
        recs.append(_rec("long_sales_cycle", "medium"))
    if "weak_conversion" in keys:
        recs.append(_rec("weak_conversion", "medium"))
    if "overdue_tasks" in keys:
        recs.append(_rec("overdue_tasks", "medium"))
    if "activity_falling" in keys:
        recs.append(_rec("activity_falling", "low"))
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
            points, key, params = outcome
            total += points
            signals.append(
                ScoredSignal(
                    signal.key,
                    signal.label,
                    points,
                    render(DEFAULT_LOCALE, key, params),
                    i18n_key=key,
                    i18n_params=params,
                )
            )

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
    "Reason",
    "RuleBasedGrowthScorer",
    "Signal",
    "StageSnapshot",
    "score_growth",
]
