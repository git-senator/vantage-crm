"""Property intelligence contracts.

Every number arrives with its reasons — the quality signals, the pricing stance
and the benchmark behind it, the strengths and weaknesses. The generated content
(summary, description, SEO) is prose the model wrote over this read; it never
produced the numbers.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.lead_intelligence import (
    MissingFieldRead,
    RecommendationRead,
    ScoredSignalRead,
)

#: The content kinds the generation endpoint accepts.
ContentKind = Literal["summary", "description", "seo"]


class PricingInsightRead(BaseModel):
    """The asking price judged against the market's comparable median."""

    stance: Literal["above", "below", "in_line", "unknown"]
    #: Signed distance from the comparable median, percent; null when unknown.
    delta_pct: int | None
    benchmark: str | None
    sample_size: int
    reason: str


class PropertyQualityRead(BaseModel):
    """The full, explainable listing quality. `quality` is the clamped sum of
    `signals`; `completeness` is the fraction of the listing checklist filled."""

    quality: int
    grade: Literal["excellent", "good", "fair", "poor"]
    completeness: int
    pricing: PricingInsightRead
    signals: list[ScoredSignalRead]
    strengths: list[str]
    weaknesses: list[str]
    missing_info: list[MissingFieldRead]
    recommendations: list[RecommendationRead]
    scorer: str


class PropertyContentResponse(BaseModel):
    """A generated piece of listing content, plus the quality read it drew on."""

    kind: ContentKind
    #: The model's copy — a summary, a description, or SEO suggestions.
    content: str
    quality: PropertyQualityRead


class NeedsAttentionListing(BaseModel):
    property_id: UUID
    title: str
    city: str
    status: str
    quality: int
    grade: str
    completeness: int
    #: The few signals that moved the quality most.
    top_reasons: list[str]


def to_quality_read(result: object) -> PropertyQualityRead:
    """A `PropertyQuality` dataclass into its wire shape."""
    from app.ai.property_scoring import PropertyQuality

    assert isinstance(result, PropertyQuality)
    return PropertyQualityRead(
        quality=result.quality,
        grade=result.grade,
        completeness=result.completeness,
        pricing=PricingInsightRead(
            stance=result.pricing.stance,
            delta_pct=result.pricing.delta_pct,
            benchmark=result.pricing.benchmark,
            sample_size=result.pricing.sample_size,
            reason=result.pricing.reason,
        ),
        signals=[
            ScoredSignalRead(key=s.key, label=s.label, points=s.points, reason=s.reason)
            for s in result.signals
        ],
        strengths=result.strengths,
        weaknesses=result.weaknesses,
        missing_info=[
            MissingFieldRead(key=m.key, label=m.label) for m in result.missing_info
        ],
        recommendations=[
            RecommendationRead(action=r.action, reason=r.reason, priority=r.priority)
            for r in result.recommendations
        ],
        scorer=result.scorer,
    )
