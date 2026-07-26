"""The deterministic deal-health engine.

The deal counterpart of `lead_scoring.py`, and it holds to the same rule: the
numbers are a sum of signals, each carrying its reason, computed by rules with no
model, no randomness and no network. The same deal in the same pipeline context
always scores the same, and every figure reads back as the reasons behind it.

Two numbers, both explainable:

  * **health** (0-100) — how well-managed and progressing the deal is, the sum of
    a signal registry, the same shape as the lead score.
  * **win probability** (0-100) — the deal's own stage probability, *adjusted* by
    a set of stated ± factors (stalled, overdue, gone quiet). Adjusting a base
    rather than inventing a number is what keeps the probability explainable: it
    is "60% for the stage, minus 15 for stalling", not a guess.

Pipeline context enters as a feature, not a query. `pipeline_mean_days_in_stage`
is supplied by the caller, which gets it from the **Analytics Engine's** stage
velocity — the same mean-time-in-stage the dashboards show. So "stalled" here
means "far past the pipeline's own norm for this stage", reusing the analytics
statistic rather than inventing a threshold or recomputing it.

Nothing in this module reads a database. Feature extraction — including the
scoped fetch of the deal and the analytics call — lives in the service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

# Shared explainability primitives — the same records lead scoring uses.
from app.ai.explain import (
    MissingField,
    Recommendation,
    RiskFlag,
    ScoredSignal,
)

MIN_SCORE = 0
MAX_SCORE = 100

RULES_VERSION = "deal-rules-v1"

#: Days past the pipeline's own mean-time-in-stage before a deal is "stalled".
#: A multiple of the norm, not a fixed number, because a stage that normally
#: takes two days and one that normally takes two months should not share a
#: staleness threshold — reusing the analytics norm is the whole point.
_STALL_MULTIPLE = 2.0
#: A short sanity minimum for the *known-norm* case: a deal has not stalled if it
#: has been in a stage under a week, however fast that stage usually moves — a
#: norm-times-two of four days would flag noise, not a stall.
_STALL_MIN_DAYS = 7
#: The threshold when the analytics norm is unknown (a young pipeline with no
#: history yet) — three weeks in one stage is stalled by any reasonable read.
_STALL_FLOOR_DAYS = 21


@dataclass(frozen=True, slots=True)
class DealFeatures:
    """The inputs the rules read. Pure data, extracted under scope elsewhere."""

    stage_name: str
    stage_position: int
    is_won: bool
    is_lost: bool
    #: The deal's own probability — stage default unless an agent overrode it.
    probability: int
    priority: str
    has_value: bool
    value: Decimal | None
    has_property: bool
    has_commission: bool
    #: Days until the expected close; negative if the date has passed. `None`
    #: when no close date is set — "no date" and "due today" are different facts.
    days_to_expected_close: int | None
    #: Days the deal has sat in its current stage, from the latest transition.
    days_in_current_stage: int
    days_since_created: int
    days_since_updated: int
    #: The pipeline's mean days in *this deal's current stage*, from the Analytics
    #: Engine. `None` when there is not yet enough history to have a norm.
    pipeline_mean_days_in_stage: float | None


@dataclass(frozen=True, slots=True)
class DealHealth:
    """The full, explainable result."""

    health: int
    #: A label from the score band, or the terminal status for a closed deal.
    status: str
    win_probability: int
    #: The deal's weighted contribution to the forecast: value x win probability.
    #: `None` when the deal has no value. A string, because it is money.
    forecast_value: str | None
    is_stalled: bool
    signals: list[ScoredSignal] = field(default_factory=list)
    #: The ± adjustments that produced the win probability, each with its reason.
    probability_factors: list[ScoredSignal] = field(default_factory=list)
    risks: list[RiskFlag] = field(default_factory=list)
    missing_info: list[MissingField] = field(default_factory=list)
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
    evaluate: object  # Callable[[DealFeatures], tuple[int, str] | None]


def _stall_threshold(f: DealFeatures) -> float:
    # When the pipeline norm is known, trust it: double the mean, floored only by
    # a short week-long sanity minimum. The 21-day fallback is for when there is
    # no norm to reuse, not a minimum that would override real analytics data.
    if f.pipeline_mean_days_in_stage is not None and f.pipeline_mean_days_in_stage > 0:
        return max(f.pipeline_mean_days_in_stage * _STALL_MULTIPLE, _STALL_MIN_DAYS)
    return float(_STALL_FLOOR_DAYS)


def _is_stalled(f: DealFeatures) -> bool:
    if f.is_won or f.is_lost:
        return False
    return f.days_in_current_stage > _stall_threshold(f)


def _progression(f: DealFeatures) -> tuple[int, str] | None:
    # The deal's probability already rises with the stage, so it is the natural,
    # non-duplicated read of "how far along". Scaled to at most 30 points.
    points = round(f.probability * 0.3)
    return points, f"Stage probability is {f.probability}%."


def _momentum(f: DealFeatures) -> tuple[int, str] | None:
    d = f.days_since_updated
    if d <= 3:
        return 15, "Updated within the last few days."
    if d <= 7:
        return 10, "Updated within the last week."
    if d <= 21:
        return 3, "Last update was over a week ago."
    return -10, f"No update in {d} days."


def _time_in_stage(f: DealFeatures) -> tuple[int, str] | None:
    norm = f.pipeline_mean_days_in_stage
    if _is_stalled(f):
        if norm is not None:
            return (
                -15,
                f"Stalled — {f.days_in_current_stage} days in {f.stage_name}, "
                f"against a pipeline average of {norm:.0f}.",
            )
        return -15, f"Stalled — {f.days_in_current_stage} days in {f.stage_name}."
    if norm is not None and f.days_in_current_stage <= norm:
        return 10, f"Moving on pace for {f.stage_name}."
    return 3, f"{f.days_in_current_stage} days in {f.stage_name}."


def _close_date(f: DealFeatures) -> tuple[int, str] | None:
    d = f.days_to_expected_close
    if d is None:
        return -5, "No expected close date set."
    if d < 0 and not (f.is_won or f.is_lost):
        return -15, f"Past its expected close date by {abs(d)} days."
    if 0 <= d <= 30:
        return 12, "Expected to close within the month."
    return 6, "Has an expected close date."


def _value_defined(f: DealFeatures) -> tuple[int, str] | None:
    if f.has_value:
        return 10, "Deal value is on record."
    return -5, "No deal value captured."


_PRIORITY_POINTS = {
    "high": (5, "Flagged high priority."),
    "medium": (2, "Medium priority."),
    "low": (0, "Low priority."),
}


def _priority(f: DealFeatures) -> tuple[int, str] | None:
    return _PRIORITY_POINTS.get(f.priority)


SIGNALS: list[Signal] = [
    Signal("progression", "Stage progression", _progression),
    Signal("momentum", "Momentum", _momentum),
    Signal("time_in_stage", "Time in stage", _time_in_stage),
    Signal("close_date", "Close date", _close_date),
    Signal("value", "Value", _value_defined),
    Signal("priority", "Priority", _priority),
]


# -------------------------------------------------- win probability


def _win_probability(f: DealFeatures) -> tuple[int, list[ScoredSignal]]:
    """The deal's stage probability, adjusted by explainable ± factors.

    Adjusting a base the pipeline already assigned — rather than producing a
    number from nowhere — is what makes the probability defensible: every point
    of difference from the stage default has a stated cause.
    """
    if f.is_won:
        return 100, [ScoredSignal("won", "Won", 0, "The deal is won.")]
    if f.is_lost:
        return 0, [ScoredSignal("lost", "Lost", 0, "The deal is lost.")]

    base = f.probability
    factors: list[ScoredSignal] = [
        ScoredSignal("base", "Stage baseline", base, f"Stage probability is {base}%.")
    ]
    total = base

    if _is_stalled(f):
        factors.append(
            ScoredSignal("stalled", "Stalled", -15, "Stalled beyond the stage norm.")
        )
        total -= 15
    d = f.days_to_expected_close
    if d is not None and d < 0:
        factors.append(
            ScoredSignal("overdue", "Overdue", -15, "Past its expected close date.")
        )
        total -= 15
    if f.days_since_updated > 21:
        factors.append(
            ScoredSignal(
                "quiet", "Gone quiet", -10, f"No update in {f.days_since_updated} days."
            )
        )
        total -= 10
    if d is not None and 0 <= d <= 14 and not _is_stalled(f):
        factors.append(
            ScoredSignal("closing", "Closing soon", 5, "Approaching close on schedule.")
        )
        total += 5

    return max(0, min(100, total)), factors


# -------------------------------------------------- derived reads


def _status(f: DealFeatures, health: int) -> str:
    if f.is_won:
        return "won"
    if f.is_lost:
        return "lost"
    if health >= 70:
        return "healthy"
    if health >= 40:
        return "at_risk"
    return "critical"


def _risks(f: DealFeatures) -> list[RiskFlag]:
    risks: list[RiskFlag] = []
    if f.is_won or f.is_lost:
        return risks
    if _is_stalled(f):
        risks.append(
            RiskFlag(
                "stalled",
                "Stalled",
                f"{f.days_in_current_stage} days in {f.stage_name}.",
            )
        )
    d = f.days_to_expected_close
    if d is not None and d < 0:
        risks.append(
            RiskFlag("overdue", "Overdue", f"{abs(d)} days past expected close.")
        )
    if d is None:
        risks.append(RiskFlag("no_close_date", "No close date", "No forecast date set."))
    if f.days_since_updated > 21:
        risks.append(
            RiskFlag(
                "quiet", "Gone quiet", f"No update in {f.days_since_updated} days."
            )
        )
    if not f.has_value:
        risks.append(RiskFlag("no_value", "No value", "Deal value is not set."))
    if f.stage_position <= 1 and f.days_since_created > 30:
        risks.append(
            RiskFlag(
                "stuck_early",
                "Stuck early",
                f"Still in an early stage after {f.days_since_created} days.",
            )
        )
    return risks


def _missing_info(f: DealFeatures) -> list[MissingField]:
    missing: list[MissingField] = []
    if not f.has_value:
        missing.append(MissingField("value", "Deal value"))
    if f.days_to_expected_close is None:
        missing.append(MissingField("expected_close_date", "Expected close date"))
    if not f.has_property:
        missing.append(MissingField("property", "Linked property"))
    if not f.has_commission:
        missing.append(MissingField("commission", "Commission"))
    return missing


def _recommendations(f: DealFeatures, risks: list[RiskFlag]) -> list[Recommendation]:
    recs: list[Recommendation] = []
    if f.is_won or f.is_lost:
        return recs
    keys = {r.key for r in risks}

    if "stalled" in keys:
        recs.append(
            Recommendation(
                "Re-engage or advance the deal",
                f"It has sat in {f.stage_name} well past the pipeline norm.",
                "high",
            )
        )
    if "overdue" in keys:
        recs.append(
            Recommendation(
                "Update the close date or push to close",
                "The expected close date has already passed.",
                "high",
            )
        )
    if "quiet" in keys:
        recs.append(
            Recommendation(
                "Log an update or a next touch",
                "There has been no activity on this deal recently.",
                "medium",
            )
        )
    if "no_close_date" in keys:
        recs.append(
            Recommendation(
                "Set an expected close date",
                "A forecast date is needed to weight this deal.",
                "medium",
            )
        )
    if "no_value" in keys:
        recs.append(
            Recommendation(
                "Add the deal value",
                "Without a value the deal cannot be forecast or ranked.",
                "medium",
            )
        )
    return recs


# --------------------------------------------------------------- engine


class DealScorer(Protocol):
    """What every deal scorer implements — the seam an ML model slots into,
    producing the same explainable `DealHealth` from the same `DealFeatures`."""

    version: str

    def score(self, features: DealFeatures) -> DealHealth: ...


class RuleBasedDealScorer:
    version = RULES_VERSION

    def score(self, features: DealFeatures) -> DealHealth:
        signals: list[ScoredSignal] = []
        total = 0
        for signal in SIGNALS:
            outcome = signal.evaluate(features)  # type: ignore[operator]
            if outcome is None:
                continue
            points, reason = outcome
            total += points
            signals.append(ScoredSignal(signal.key, signal.label, points, reason))

        # A won deal is healthy by definition; a lost one is not. The signal sum
        # governs open deals; terminal deals take a fixed reading so the number
        # never disagrees with the outcome.
        if features.is_won:
            health = MAX_SCORE
        elif features.is_lost:
            health = MIN_SCORE
        else:
            health = max(MIN_SCORE, min(MAX_SCORE, total))

        win_probability, factors = _win_probability(features)
        forecast = (
            str(
                (features.value * Decimal(win_probability) / Decimal(100)).quantize(
                    Decimal("0.01")
                )
            )
            if features.has_value and features.value is not None
            else None
        )

        return DealHealth(
            health=health,
            status=_status(features, health),
            win_probability=win_probability,
            forecast_value=forecast,
            is_stalled=_is_stalled(features),
            signals=signals,
            probability_factors=factors,
            risks=_risks(features),
            missing_info=_missing_info(features),
            recommendations=_recommendations(features, _risks(features)),
            scorer=self.version,
        )


DEFAULT_SCORER: DealScorer = RuleBasedDealScorer()


def score_deal(features: DealFeatures, scorer: DealScorer | None = None) -> DealHealth:
    return (scorer or DEFAULT_SCORER).score(features)


def days_between(earlier: date, later: date) -> int:
    return (later - earlier).days


__all__ = [
    "DEFAULT_SCORER",
    "MAX_SCORE",
    "MIN_SCORE",
    "RULES_VERSION",
    "SIGNALS",
    "DealFeatures",
    "DealHealth",
    "DealScorer",
    "RuleBasedDealScorer",
    "Signal",
    "days_between",
    "score_deal",
]
