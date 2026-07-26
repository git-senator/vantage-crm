"""The deterministic listing-quality engine.

The property counterpart of `lead_scoring.py` and `deal_scoring.py`, holding to
the same rule: the numbers are a sum of signals, each carrying its reason,
computed by rules with no model, no randomness and no network. The same listing
in the same market context always scores the same, and every figure reads back
as the reasons behind it.

What it produces:

  * **quality** (0-100) — how complete and marketable the *listing* is, the sum
    of a signal registry (description depth, features, core specs, price, year
    built, mappable location, MLS syndication-readiness).
  * **completeness** (0-100%) — a flatter, separate reading: the fraction of a
    fixed checklist of listing fields that are filled in. Quality weights;
    completeness counts. A listing can be complete but thin, or rich but missing
    a key fact, and the two numbers say different things.
  * **strengths / weaknesses** — derived straight from the signals (a strong
    signal is a strength, a weak or absent one is a weakness) plus the pricing
    stance. Each is a reason string, so both are explainable by construction.
  * **pricing insight** — an explainable stance against a market benchmark:
    "priced 18% above the comparable median", or "unknown" when there is no
    comp to judge against.

Market context enters as a feature, not a query. `comp_median_price_per_sqft`,
`comp_sample_size` and `market_avg_days_on_market` are supplied by the caller,
which gets them from the **Analytics Engine** — the same aggregates the
dashboards render. So "overpriced" here means "above the market's own median for
comparable listings", reusing the analytics statistic rather than inventing one.

Nothing in this module reads a database. Feature extraction — including the
scoped fetch of the listing and the analytics calls — lives in the service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

# Shared explainability primitives — the same records lead and deal scoring use.
from app.ai.explain import (
    MissingField,
    Recommendation,
    ScoredSignal,
)

MIN_SCORE = 0
MAX_SCORE = 100

RULES_VERSION = "property-rules-v1"

#: A description shorter than this reads as a stub, not a listing. Long enough to
#: carry a room count and a selling sentence; short enough that a genuine
#: paragraph clears it.
_DESCRIPTION_THIN_CHARS = 180
#: A description past this is doing real marketing work.
_DESCRIPTION_RICH_CHARS = 400

#: How far off the comparable median a listing must be before the stance stops
#: being "in line". A price within a tenth of the market median is, for a comp
#: this coarse, simply the market price.
_PRICE_IN_LINE_BAND = 0.10

#: A listing sitting well past the market's own average days-on-market is stale —
#: the multiple, not a fixed number of days, so a fast market and a slow one are
#: judged on their own pace, the same discipline as deal stalled-detection.
_STALE_MULTIPLE = 1.5
#: The fallback when the market has no days-on-market average yet (a young
#: workspace) — three months on market is stale by any reasonable read.
_STALE_FLOOR_DAYS = 90


@dataclass(frozen=True, slots=True)
class PropertyFeatures:
    """The inputs the rules read. Pure data, extracted under scope elsewhere."""

    property_type: str
    status: str
    #: The listing's marketing prose, measured by length only — the engine judges
    #: presence and depth, never content.
    description_length: int
    feature_count: int
    has_price: bool
    price: Decimal | None
    has_bedrooms: bool
    has_bathrooms: bool
    has_square_feet: bool
    square_feet: int | None
    has_year_built: bool
    has_lot_size: bool
    #: Latitude and longitude both present — the listing can be pinned on a map.
    has_geo: bool
    has_mls: bool
    #: Days the listing has been on market, or `None` when never listed or sold
    #: (a sold listing's clock has stopped).
    days_on_market: int | None
    #: The market's median price per square foot for *comparable* listings (same
    #: property type), from the Analytics Engine. `None` when there is no comp.
    comp_median_price_per_sqft: Decimal | None
    #: How many comparable listings the median was taken over — a stance drawn
    #: from two comps is not one to lean on, and the reason says so.
    comp_sample_size: int
    #: The market's average days-on-market across active listings, from the
    #: Analytics Engine. `None` when there is not yet a norm.
    market_avg_days_on_market: float | None


@dataclass(frozen=True, slots=True)
class PricingInsight:
    """An explainable read on the asking price against the market.

    `stance` is the headline; `reason` is the sentence behind it. `delta_pct` is
    the signed distance from the comparable median (positive is above), `None`
    when there is nothing to compare against.
    """

    #: above / below / in_line / unknown.
    stance: str
    delta_pct: int | None
    #: The benchmark it was judged against, e.g. "$264/sqft median". `None` when
    #: unknown.
    benchmark: str | None
    sample_size: int
    reason: str


@dataclass(frozen=True, slots=True)
class PropertyQuality:
    """The full, explainable result."""

    quality: int
    #: A label from the score band — excellent / good / fair / poor.
    grade: str
    #: 0-100, the fraction of the listing checklist that is filled.
    completeness: int
    pricing: PricingInsight
    signals: list[ScoredSignal] = field(default_factory=list)
    #: Reason strings for what the listing does well and where it falls short —
    #: derived from the signals and the pricing, not a second opinion.
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
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
    evaluate: object  # Callable[[PropertyFeatures], tuple[int, str] | None]


def _description(f: PropertyFeatures) -> tuple[int, str] | None:
    n = f.description_length
    if n == 0:
        return -10, "No description — the listing has nothing to sell it."
    if n < _DESCRIPTION_THIN_CHARS:
        return 4, "Description is thin; a fuller one would market it better."
    if n < _DESCRIPTION_RICH_CHARS:
        return 12, "Has a solid description."
    return 18, "Rich, detailed description."


def _features_listed(f: PropertyFeatures) -> tuple[int, str] | None:
    n = f.feature_count
    if n == 0:
        return 0, "No features listed."
    if n <= 3:
        return 6, f"{n} feature(s) listed."
    return 12, f"{n} features listed — well detailed."


def _core_specs(f: PropertyFeatures) -> tuple[int, str] | None:
    # Beds, baths, square footage — the three facts a buyer filters on. Land has
    # no bed/bath, so it is judged on lot size instead and not penalised here.
    if f.property_type == "land":
        if f.has_lot_size:
            return 15, "Lot size is on record."
        return -6, "No lot size on a land listing."
    present = sum((f.has_bedrooms, f.has_bathrooms, f.has_square_feet))
    if present == 3:
        return 18, "Beds, baths and square footage all captured."
    if present == 2:
        return 10, "Two of beds, baths and square footage captured."
    if present == 1:
        return 3, "Only one core specification captured."
    return -8, "No beds, baths or square footage captured."


def _price_present(f: PropertyFeatures) -> tuple[int, str] | None:
    if f.has_price:
        return 12, "Asking price is set."
    return -12, "No asking price — buyers cannot evaluate the listing."


def _year_built(f: PropertyFeatures) -> tuple[int, str] | None:
    if f.property_type == "land":
        return None
    if f.has_year_built:
        return 6, "Year built is on record."
    return 0, "No year built captured."


def _mappable(f: PropertyFeatures) -> tuple[int, str] | None:
    if f.has_geo:
        return 6, "Geocoded — it can be shown on a map."
    return 0, "Not geocoded; it cannot be pinned on a map."


def _mls(f: PropertyFeatures) -> tuple[int, str] | None:
    if f.has_mls:
        return 5, "Has an MLS number — ready for syndication."
    return 0, "No MLS number yet."


SIGNALS: list[Signal] = [
    Signal("description", "Description", _description),
    Signal("features", "Features", _features_listed),
    Signal("core_specs", "Core specifications", _core_specs),
    Signal("price", "Asking price", _price_present),
    Signal("year_built", "Year built", _year_built),
    Signal("location", "Mappable location", _mappable),
    Signal("mls", "MLS syndication", _mls),
]


# --------------------------------------------------- completeness

#: The listing checklist. Completeness is the fraction of these that are filled —
#: a flat count, deliberately not weighted, so it answers "how much of the record
#: is filled in" rather than "how good is it". `land` drops the fields that do
#: not apply, so a complete land listing can still reach 100%.
def _completeness(f: PropertyFeatures) -> int:
    checks: list[bool] = [
        f.description_length > 0,
        f.feature_count > 0,
        f.has_price,
        f.has_geo,
        f.has_mls,
    ]
    if f.property_type == "land":
        checks.append(f.has_lot_size)
    else:
        checks += [
            f.has_bedrooms,
            f.has_bathrooms,
            f.has_square_feet,
            f.has_year_built,
        ]
    return round(100 * sum(checks) / len(checks))


# ------------------------------------------------------- pricing insight


def _pricing(f: PropertyFeatures) -> PricingInsight:
    """The asking price judged against the market's comparable median.

    Adjusting nothing and comparing against a benchmark the analytics layer
    already computed — rather than asserting "overpriced" from thin air — is what
    keeps this explainable: every stance names the median it was measured against
    and the sample behind it.
    """
    median = f.comp_median_price_per_sqft
    if (
        not f.has_price
        or f.price is None
        or not f.has_square_feet
        or f.square_feet is None
        or f.square_feet <= 0
        or median is None
        or median <= 0
        or f.comp_sample_size <= 0
    ):
        return PricingInsight(
            stance="unknown",
            delta_pct=None,
            benchmark=None,
            sample_size=f.comp_sample_size,
            reason="Not enough comparable priced listings to benchmark against.",
        )

    ppsf = f.price / Decimal(f.square_feet)
    delta = (ppsf - median) / median
    delta_pct = round(float(delta) * 100)
    benchmark = f"${median:,.0f}/sqft median across {f.comp_sample_size} comparable listings"

    if abs(float(delta)) <= _PRICE_IN_LINE_BAND:
        return PricingInsight(
            stance="in_line",
            delta_pct=delta_pct,
            benchmark=benchmark,
            sample_size=f.comp_sample_size,
            reason=f"Priced at ${ppsf:,.0f}/sqft, in line with the {benchmark}.",
        )
    direction = "above" if delta_pct > 0 else "below"
    return PricingInsight(
        stance=direction,
        delta_pct=delta_pct,
        benchmark=benchmark,
        sample_size=f.comp_sample_size,
        reason=(
            f"Priced at ${ppsf:,.0f}/sqft, {abs(delta_pct)}% {direction} the {benchmark}."
        ),
    )


# -------------------------------------------------------- derived reads


def _grade(quality: int) -> str:
    if quality >= 75:
        return "excellent"
    if quality >= 55:
        return "good"
    if quality >= 35:
        return "fair"
    return "poor"


def _stale_threshold(f: PropertyFeatures) -> float:
    if f.market_avg_days_on_market is not None and f.market_avg_days_on_market > 0:
        return f.market_avg_days_on_market * _STALE_MULTIPLE
    return float(_STALE_FLOOR_DAYS)


def _is_stale(f: PropertyFeatures) -> bool:
    return (
        f.status in ("active", "pending")
        and f.days_on_market is not None
        and f.days_on_market > _stale_threshold(f)
    )


def _missing_info(f: PropertyFeatures) -> list[MissingField]:
    missing: list[MissingField] = []
    if f.description_length == 0:
        missing.append(MissingField("description", "Description"))
    if f.feature_count == 0:
        missing.append(MissingField("features", "Feature list"))
    if not f.has_price:
        missing.append(MissingField("price", "Asking price"))
    if f.property_type == "land":
        if not f.has_lot_size:
            missing.append(MissingField("lot_size", "Lot size"))
    else:
        if not f.has_bedrooms:
            missing.append(MissingField("bedrooms", "Bedrooms"))
        if not f.has_bathrooms:
            missing.append(MissingField("bathrooms", "Bathrooms"))
        if not f.has_square_feet:
            missing.append(MissingField("square_feet", "Square footage"))
        if not f.has_year_built:
            missing.append(MissingField("year_built", "Year built"))
    if not f.has_geo:
        missing.append(MissingField("location", "Map coordinates"))
    if not f.has_mls:
        missing.append(MissingField("mls_number", "MLS number"))
    return missing


def _strengths_and_weaknesses(
    signals: list[ScoredSignal], pricing: PricingInsight, stale: bool
) -> tuple[list[str], list[str]]:
    """Read straight off the signals: a signal that helped is a strength, one that
    hurt or contributed nothing worth noting is a weakness. The pricing stance and
    a stale listing add to the weaknesses when they apply. No new judgement — the
    same facts as the score, split by sign."""
    strengths = [s.reason for s in signals if s.points >= 10]
    weaknesses = [s.reason for s in signals if s.points <= 0]
    if pricing.stance == "above":
        weaknesses.append(pricing.reason)
    elif pricing.stance in ("below", "in_line"):
        strengths.append(pricing.reason)
    if stale:
        weaknesses.append("Has been on the market longer than comparable listings.")
    return strengths, weaknesses


def _recommendations(
    f: PropertyFeatures,
    missing: list[MissingField],
    pricing: PricingInsight,
    stale: bool,
) -> list[Recommendation]:
    """Marketing next actions, each derived from the same facts as the score, so a
    recommendation is a rule with a stated reason, never a guess."""
    recs: list[Recommendation] = []
    keys = {m.key for m in missing}

    if "price" in keys:
        recs.append(
            Recommendation(
                "Set an asking price",
                "A listing without a price cannot be evaluated or matched to buyers.",
                "high",
            )
        )
    if "description" in keys:
        recs.append(
            Recommendation(
                "Write a listing description",
                "There is no prose selling the property.",
                "high",
            )
        )
    elif f.description_length < _DESCRIPTION_THIN_CHARS:
        recs.append(
            Recommendation(
                "Expand the description",
                "A fuller description markets the property better and helps search.",
                "medium",
            )
        )
    if "features" in keys:
        recs.append(
            Recommendation(
                "List the property's features",
                "Features drive buyer filtering and search matches.",
                "medium",
            )
        )
    core_missing = keys & {"bedrooms", "bathrooms", "square_feet", "lot_size"}
    if core_missing:
        recs.append(
            Recommendation(
                "Capture the core specifications",
                "Beds, baths and size are the facts buyers filter on.",
                "high" if len(core_missing) >= 2 else "medium",
            )
        )
    if pricing.stance == "above":
        recs.append(
            Recommendation(
                "Review the asking price",
                pricing.reason,
                "high" if stale else "medium",
            )
        )
    if stale:
        recs.append(
            Recommendation(
                "Refresh or reposition the listing",
                "It has been on the market longer than comparable listings.",
                "medium",
            )
        )
    if "location" in keys:
        recs.append(
            Recommendation(
                "Add map coordinates",
                "A geocoded listing can be shown on a map and in location search.",
                "low",
            )
        )
    if "mls_number" in keys:
        recs.append(
            Recommendation(
                "Add the MLS number",
                "An MLS number makes the listing ready to syndicate.",
                "low",
            )
        )
    return recs


# --------------------------------------------------------------- engine


class PropertyScorer(Protocol):
    """What every property scorer implements — the seam an ML model slots into,
    producing the same explainable `PropertyQuality` from the same
    `PropertyFeatures`."""

    version: str

    def score(self, features: PropertyFeatures) -> PropertyQuality: ...


class RuleBasedPropertyScorer:
    version = RULES_VERSION

    def score(self, features: PropertyFeatures) -> PropertyQuality:
        signals: list[ScoredSignal] = []
        total = 0
        for signal in SIGNALS:
            outcome = signal.evaluate(features)  # type: ignore[operator]
            if outcome is None:
                continue
            points, reason = outcome
            total += points
            signals.append(ScoredSignal(signal.key, signal.label, points, reason))

        quality = max(MIN_SCORE, min(MAX_SCORE, total))
        pricing = _pricing(features)
        stale = _is_stale(features)
        missing = _missing_info(features)
        strengths, weaknesses = _strengths_and_weaknesses(signals, pricing, stale)

        return PropertyQuality(
            quality=quality,
            grade=_grade(quality),
            completeness=_completeness(features),
            pricing=pricing,
            signals=signals,
            strengths=strengths,
            weaknesses=weaknesses,
            missing_info=missing,
            recommendations=_recommendations(features, missing, pricing, stale),
            scorer=self.version,
        )


#: The active scorer. A deployment swaps in an ML scorer by replacing this — the
#: only line that would change to move from rules to a model.
DEFAULT_SCORER: PropertyScorer = RuleBasedPropertyScorer()


def score_property(
    features: PropertyFeatures, scorer: PropertyScorer | None = None
) -> PropertyQuality:
    return (scorer or DEFAULT_SCORER).score(features)


__all__ = [
    "DEFAULT_SCORER",
    "MAX_SCORE",
    "MIN_SCORE",
    "RULES_VERSION",
    "SIGNALS",
    "PricingInsight",
    "PropertyFeatures",
    "PropertyQuality",
    "PropertyScorer",
    "RuleBasedPropertyScorer",
    "Signal",
    "score_property",
]
