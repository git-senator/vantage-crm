"""Analytics contracts.

Every numeric value crosses the wire as a **string**, not a float. These are
counts and money; a revenue figure that has been through a JSON float has lost
the precision NUMERIC exists to protect, and the frontend already parses money
as a string everywhere else (see `Lead.budget_min`, `Deal.value`).

`value: null` means the caller has no grant on that metric's entity — not that
it is zero. The distinction is the whole reason the field is nullable.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PeriodName = Literal["today", "week", "month", "quarter", "year", "custom"]


class MetricRead(BaseModel):
    # Dashboards assemble their tiles from `MetricValue` dataclasses (see
    # InsightsService.dashboard), so validation must read attributes, not only
    # dicts — this lets `DashboardResponse.model_validate` build each tile
    # straight from a MetricValue (its `delta_percent` is a computed property).
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    unit: str
    kind: Literal["flow", "level"]
    higher_is_better: bool
    #: Null when the caller cannot see this metric.
    value: Decimal | None
    previous: Decimal | None = None
    #: Null when the previous period was zero — coming from nothing is not a
    #: percentage improvement.
    delta_percent: Decimal | None = None


class MetricDefinitionRead(BaseModel):
    key: str
    label: str
    description: str
    kind: str
    unit: str
    category: str
    higher_is_better: bool


class PeriodRead(BaseModel):
    # The forecast panel embeds the service's `Period` dataclass directly, so
    # (like MetricRead) validation must accept an attribute-bearing object, not
    # only a dict.
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    label: str


class KpiResponse(BaseModel):
    period: PeriodRead
    metrics: list[MetricRead]


class SeriesPoint(BaseModel):
    date: date
    value: Decimal


class SeriesResponse(BaseModel):
    metric: MetricDefinitionRead
    points: list[SeriesPoint]


class StageBreakdown(BaseModel):
    stage_id: UUID
    stage_name: str
    deal_count: int
    value: Decimal


class VelocityRow(BaseModel):
    stage_id: UUID
    stage_name: str
    mean_days: Decimal
    transitions: int


class LossReason(BaseModel):
    reason: str
    count: int
    value: Decimal


class SourceRow(BaseModel):
    source: str
    created: int
    converted: int
    #: Computed here rather than by the client so one definition of "conversion
    #: rate" exists. Null when nothing was created — a rate over no leads is
    #: undefined, not zero.
    conversion_rate: Decimal | None = None


class AgentRow(BaseModel):
    user_id: UUID
    full_name: str
    deals_won: int
    revenue: Decimal
    leads_converted: int


class FunnelResponse(BaseModel):
    stages: list[StageBreakdown]
    velocity: list[VelocityRow]
    sources: list[SourceRow]


class WinLossResponse(BaseModel):
    won: int
    lost: int
    win_rate: Decimal | None
    reasons: list[LossReason]


class ForecastPoint(BaseModel):
    label: str
    #: What the pipeline says, weighted by each deal's own probability.
    weighted: Decimal
    #: The unweighted total, for the optimistic bound.
    committed: Decimal


class ForecastResponse(BaseModel):
    period: PeriodRead
    #: Revenue already won in the period. The floor a forecast cannot go below.
    booked: Decimal
    #: Weighted open pipeline expected to close in the period.
    weighted_pipeline: Decimal
    #: `booked + weighted_pipeline`. The number to plan against.
    projected: Decimal
    #: Same-length trailing window, so "ahead or behind" is like-for-like.
    previous_actual: Decimal | None
    breakdown: list[ForecastPoint] = Field(default_factory=list)


class GoalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID | None
    metric_key: str
    target_value: Decimal
    period_start: date
    period_end: date
    #: Progress is computed against the same scoped aggregate the dashboards
    #: use, so a goal and a KPI can never disagree about the same number.
    current_value: Decimal | None = None
    percent_complete: Decimal | None = None


class GoalCreate(BaseModel):
    owner_id: UUID | None = None
    metric_key: str = Field(min_length=1, max_length=60)
    target_value: Decimal = Field(gt=0)
    period_start: date
    period_end: date


class DashboardResponse(BaseModel):
    """One of the named dashboards, assembled server-side.

    Assembled here rather than by the client making eight calls: the panels
    share a period and a scope resolution, and eight round trips would each
    re-resolve the caller's team membership.
    """

    key: str
    title: str
    period: PeriodRead
    metrics: list[MetricRead]
    stages: list[StageBreakdown] = Field(default_factory=list)
    agents: list[AgentRow] = Field(default_factory=list)
    velocity: list[VelocityRow] = Field(default_factory=list)
    reasons: list[LossReason] = Field(default_factory=list)
    sources: list[SourceRow] = Field(default_factory=list)
    forecast: ForecastResponse | None = None
