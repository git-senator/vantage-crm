"""Deal intelligence contracts.

Every number arrives with its reasons — the health signals, and the ± factors
that produced the win probability. Money (`forecast_value`) crosses as a string,
the platform's rule for NUMERIC precision.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.lead_intelligence import (
    MissingFieldRead,
    RecommendationRead,
    RiskFlagRead,
    ScoredSignalRead,
)


class DealHealthRead(BaseModel):
    """The full, explainable deal health. `health` is the sum of `signals`; the
    win probability is the sum of `probability_factors` (clamped)."""

    health: int
    status: Literal["healthy", "at_risk", "critical", "won", "lost"]
    win_probability: int
    #: The deal's weighted forecast contribution, or null with no value. String.
    forecast_value: str | None
    is_stalled: bool
    signals: list[ScoredSignalRead]
    probability_factors: list[ScoredSignalRead]
    risks: list[RiskFlagRead]
    missing_info: list[MissingFieldRead]
    recommendations: list[RecommendationRead]
    scorer: str


class DealInsightResponse(BaseModel):
    health: DealHealthRead
    #: The model's prose over the deterministic health read.
    narrative: str


class AtRiskDeal(BaseModel):
    deal_id: UUID
    title: str
    stage: str
    health: int
    status: str
    win_probability: int
    is_stalled: bool
    #: The few signals that moved the health most.
    top_reasons: list[str]


def to_health_read(result: object) -> DealHealthRead:
    """A `DealHealth` dataclass into its wire shape."""
    from app.ai.deal_scoring import DealHealth

    assert isinstance(result, DealHealth)
    return DealHealthRead(
        health=result.health,
        status=result.status,
        win_probability=result.win_probability,
        forecast_value=result.forecast_value,
        is_stalled=result.is_stalled,
        signals=[
            ScoredSignalRead(key=s.key, label=s.label, points=s.points, reason=s.reason)
            for s in result.signals
        ],
        probability_factors=[
            ScoredSignalRead(key=s.key, label=s.label, points=s.points, reason=s.reason)
            for s in result.probability_factors
        ],
        risks=[
            RiskFlagRead(key=r.key, label=r.label, detail=r.detail) for r in result.risks
        ],
        missing_info=[
            MissingFieldRead(key=m.key, label=m.label) for m in result.missing_info
        ],
        recommendations=[
            RecommendationRead(action=r.action, reason=r.reason, priority=r.priority)
            for r in result.recommendations
        ],
        scorer=result.scorer,
    )
