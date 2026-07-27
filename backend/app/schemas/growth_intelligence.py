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


def to_growth_read(result: object, period: object) -> GrowthHealthRead:
    """A `GrowthHealth` dataclass plus its `Period` into the wire shape."""
    from app.ai.growth_scoring import GrowthHealth
    from app.services.analytics import Period

    assert isinstance(result, GrowthHealth)
    assert isinstance(period, Period)
    return GrowthHealthRead(
        score=result.score,
        band=result.band,
        period_label=period.label,
        period_start=period.start,
        period_end=period.end,
        signals=[
            ScoredSignalRead(key=s.key, label=s.label, points=s.points, reason=s.reason)
            for s in result.signals
        ],
        revenue_signals=result.revenue_signals,
        pipeline_insights=result.pipeline_insights,
        risks=[
            RiskFlagRead(key=r.key, label=r.label, detail=r.detail) for r in result.risks
        ],
        recommendations=[
            RecommendationRead(action=r.action, reason=r.reason, priority=r.priority)
            for r in result.recommendations
        ],
        scorer=result.scorer,
    )
