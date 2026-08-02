"""Growth intelligence contracts.

The growth score arrives with the reasons that produced it — the signals, the
revenue signals and the pipeline insights, plus the risks and recommendations.
The briefing is the model's prose over that read; it never produced the number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.lead_intelligence import (
    RecommendationRead,
    RiskFlagRead,
    ScoredSignalRead,
)


class GrowthHealthRead(BaseModel):
    """The full, explainable growth read. `score` is the clamped sum of `signals`."""

    score: int
    band: Literal["thriving", "steady", "at_risk", "struggling"]
    period_label: str
    period_start: datetime
    period_end: datetime
    signals: list[ScoredSignalRead]
    #: Explainable statements about revenue and the pipeline.
    revenue_signals: list[str]
    pipeline_insights: list[str]
    risks: list[RiskFlagRead]
    recommendations: list[RecommendationRead]
    scorer: str


class GrowthBriefingResponse(BaseModel):
    growth: GrowthHealthRead
    #: The model's prose over the deterministic growth read.
    narrative: str


def _localized_params(locale: str, params: dict) -> dict:  # type: ignore[type-arg]
    """Localize nested display values (`period`, `stage`) inside a reason's params
    before interpolation, so a Russian sentence never embeds an English label."""
    from app.ai.growth_i18n import period_label, stage_label

    if not params or (("period" not in params) and ("stage" not in params)):
        return params
    out = dict(params)
    if "period" in out:
        out["period"] = period_label(locale, str(out["period"]))
    if "stage" in out:
        out["stage"] = stage_label(locale, str(out["stage"]))
    return out


def to_growth_read(result: object, period: object, locale: str = "en") -> GrowthHealthRead:
    """A `GrowthHealth` dataclass plus its `Period` into the wire shape, rendered
    in `locale`. Every generated statement carries a localization key; this is
    where those keys become the caller's language, so the frontend receives final
    text and its wire shape stays a plain string."""
    from app.ai.growth_i18n import render
    from app.ai.growth_scoring import GrowthHealth, Reason
    from app.services.analytics import Period

    assert isinstance(result, GrowthHealth)
    assert isinstance(period, Period)

    def signal_reason(s: object) -> str:
        assert hasattr(s, "reason")
        key = getattr(s, "i18n_key", "")
        if not key:
            return s.reason  # type: ignore[attr-defined]
        return render(locale, key, _localized_params(locale, getattr(s, "i18n_params", {})))

    def reason_text(r: Reason) -> str:
        if not r.key:
            return r.text
        return render(locale, r.key, _localized_params(locale, r.params))

    def risk_label(r: object) -> str:
        lk = getattr(r, "label_key", "")
        return render(locale, lk) if lk else r.label  # type: ignore[attr-defined]

    def risk_detail(r: object) -> str:
        dk = getattr(r, "detail_key", "")
        if not dk:
            return r.detail  # type: ignore[attr-defined]
        return render(locale, dk, _localized_params(locale, getattr(r, "i18n_params", {})))

    def rec_action(r: object) -> str:
        ak = getattr(r, "action_key", "")
        return render(locale, ak) if ak else r.action  # type: ignore[attr-defined]

    def rec_reason(r: object) -> str:
        rk = getattr(r, "reason_key", "")
        return render(locale, rk) if rk else r.reason  # type: ignore[attr-defined]

    return GrowthHealthRead(
        score=result.score,
        band=result.band,
        period_label=_localized_params(locale, {"period": period.label})["period"],
        period_start=period.start,
        period_end=period.end,
        signals=[
            ScoredSignalRead(
                key=s.key, label=s.label, points=s.points, reason=signal_reason(s)
            )
            for s in result.signals
        ],
        revenue_signals=[reason_text(s) for s in result.revenue_signals],
        pipeline_insights=[reason_text(s) for s in result.pipeline_insights],
        risks=[
            RiskFlagRead(key=r.key, label=risk_label(r), detail=risk_detail(r))
            for r in result.risks
        ],
        recommendations=[
            RecommendationRead(
                action=rec_action(r), reason=rec_reason(r), priority=r.priority
            )
            for r in result.recommendations
        ],
        scorer=result.scorer,
    )
