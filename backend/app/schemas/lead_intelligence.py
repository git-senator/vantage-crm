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


def to_score_read(result: object, locale: str = "en") -> LeadScoreRead:
    """A `LeadScore` (the engine's dataclass) into its wire shape, localized.

    Kept next to the schema, not in the service, so the mapping from the internal
    dataclass to the API lives in one place — and a field added to one is an
    obvious edit to the other. Every generated reason carries an i18n key; this
    renders it in `locale` (the `vg_locale` cookie), falling back to the English
    prose the engine also produced.
    """
    from app.ai.lead_i18n import render
    from app.ai.lead_scoring import LeadScore

    assert isinstance(result, LeadScore)

    def signal_reason(s: object) -> str:
        key = getattr(s, "i18n_key", "")
        return render(locale, key, getattr(s, "i18n_params", {})) if key else s.reason  # type: ignore[attr-defined]

    def risk_label(r: object) -> str:
        key = getattr(r, "label_key", "")
        return render(locale, key) if key else r.label  # type: ignore[attr-defined]

    def risk_detail(r: object) -> str:
        key = getattr(r, "detail_key", "")
        return render(locale, key, getattr(r, "i18n_params", {})) if key else r.detail  # type: ignore[attr-defined]

    def rec_action(r: object) -> str:
        key = getattr(r, "action_key", "")
        return render(locale, key) if key else r.action  # type: ignore[attr-defined]

    def rec_reason(r: object) -> str:
        key = getattr(r, "reason_key", "")
        return render(locale, key) if key else r.reason  # type: ignore[attr-defined]

    return LeadScoreRead(
        score=result.score,
        temperature=result.temperature,
        qualification=result.qualification,
        priority=result.priority,
        buying_intent=result.buying_intent,
        signals=[
            ScoredSignalRead(
                key=s.key, label=s.label, points=s.points, reason=signal_reason(s)
            )
            for s in result.signals
        ],
        risks=[
            RiskFlagRead(key=r.key, label=risk_label(r), detail=risk_detail(r))
            for r in result.risks
        ],
        missing_info=[
            MissingFieldRead(key=m.key, label=render(locale, f"lead_missing_{m.key}"))
            for m in result.missing_info
        ],
        recommendations=[
            RecommendationRead(
                action=rec_action(r), reason=rec_reason(r), priority=r.priority
            )
            for r in result.recommendations
        ],
        scorer=result.scorer,
    )
