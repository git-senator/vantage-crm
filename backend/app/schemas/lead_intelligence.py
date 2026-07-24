"""Lead intelligence contracts.

Every number arrives with the reasons that produced it. The response shape is
built around explainability: `signals` is not a debug extra, it is the score —
the total is their sum, and the API returns them because a score a user cannot
interrogate is a score they cannot trust or act on.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ScoredSignalRead(BaseModel):
    key: str
    label: str
    points: int
    reason: str


class RiskFlagRead(BaseModel):
    key: str
    label: str
    detail: str


class MissingFieldRead(BaseModel):
    key: str
    label: str


class RecommendationRead(BaseModel):
    action: str
    reason: str
    priority: Literal["high", "medium", "low"]


class LeadScoreRead(BaseModel):
    """The full, explainable score. The `score` is exactly the sum of `signals`
    (clamped to 0-100)."""

    score: int
    #: The engine's inferred temperature, distinct from the agent's on the lead.
    temperature: Literal["hot", "warm", "cold"]
    qualification: Literal["qualified", "nurture", "unqualified"]
    priority: Literal["high", "medium", "low"]
    buying_intent: Literal["strong", "moderate", "weak", "none"]
    signals: list[ScoredSignalRead]
    risks: list[RiskFlagRead]
    missing_info: list[MissingFieldRead]
    recommendations: list[RecommendationRead]
    #: Which engine produced this — rules-v1 today. On the wire so a client can
    #: tell a rule-based score from a future model-based one.
    scorer: str


class LeadInsightResponse(BaseModel):
    """A deterministic score plus the AI's grounded narrative over it."""

    score: LeadScoreRead
    #: The model's prose — a summary and next steps, grounded in the signals.
    #: The model never produced the number; it explains it.
    narrative: str


class PrioritisedLead(BaseModel):
    lead_id: UUID
    full_name: str
    stage: str
    score: int
    temperature: str
    priority: str
    buying_intent: str
    #: The few signals that moved the score most — the "why it ranks here" a
    #: prioritised list needs to be actionable rather than opaque.
    top_reasons: list[str]


def to_score_read(result: object) -> LeadScoreRead:
    """A `LeadScore` (the engine's dataclass) into its wire shape.

    Kept next to the schema, not in the service, so the mapping from the internal
    dataclass to the API lives in one place — and a field added to one is an
    obvious edit to the other.
    """
    from app.ai.lead_scoring import LeadScore

    assert isinstance(result, LeadScore)
    return LeadScoreRead(
        score=result.score,
        temperature=result.temperature,
        qualification=result.qualification,
        priority=result.priority,
        buying_intent=result.buying_intent,
        signals=[
            ScoredSignalRead(key=s.key, label=s.label, points=s.points, reason=s.reason)
            for s in result.signals
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
