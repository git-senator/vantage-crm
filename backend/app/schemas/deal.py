"""Deal contracts.

The one thing worth reading carefully: **`DealUpdate` has no `stage_id`.**

Moving a deal between stages writes history, recalculates probability, sets or
clears the close date and emits an activity. That is a domain action with side
effects, not a field edit — exactly the reasoning that keeps `status` off
`LeadUpdate`. It lives at `POST /deals/{id}/stage`.

`status` is likewise absent from every write model: it is derived from the
stage, so accepting it would create a second source of truth for "did we win".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DealPriority = Literal["low", "medium", "high", "urgent"]
DealStatus = Literal["open", "won", "lost"]

Money = Annotated[Decimal, Field(ge=0, le=Decimal("99999999999.99"), decimal_places=2)]
#: 0.0250 is 2.5%. Bounded at 1 because a rate above 100% is a percentage
#: somebody forgot to divide.
Rate = Annotated[Decimal, Field(ge=0, le=Decimal("1"), decimal_places=4)]


class DealParty(BaseModel):
    """Minimal user projection for list and detail views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class DealStageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    position: int
    is_won: bool
    is_lost: bool


class DealClientSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str


class DealPropertySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    full_address: str


class DealBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    client_id: UUID
    property_id: UUID | None = None
    value: Money | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    commission_amount: Money | None = None
    commission_rate: Rate | None = None
    priority: DealPriority = "medium"
    expected_close_date: date | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class DealCreate(DealBase):
    """New deal.

    `pipeline_id` and `stage_id` are optional: omitting both lands the deal in
    the workspace's default pipeline at its first stage, which is what the
    quick-add path wants. `probability` defaults to the stage's
    `default_probability`.
    """

    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    owner_id: UUID | None = None


class DealUpdate(BaseModel):
    """Partial update. Every field optional; unset fields are untouched.

    `stage_id` is absent by design — see the module docstring. So is `status`,
    which is derived, and `actual_close_date`, which the transition sets.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    client_id: UUID | None = None
    property_id: UUID | None = None
    value: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    commission_amount: Money | None = None
    commission_rate: Rate | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    priority: DealPriority | None = None
    expected_close_date: date | None = None
    custom_fields: dict[str, Any] | None = None
    owner_id: UUID | None = None


class DealRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    #: Derived from the stage. Never stored, never accepted on write.
    status: str
    value: Decimal | None
    currency: str
    commission_amount: Decimal | None
    commission_rate: Decimal | None
    #: value x probability, the figure a forecast sums. Null when unpriced.
    weighted_value: Decimal | None
    probability: int
    priority: str
    expected_close_date: date | None
    actual_close_date: date | None
    lost_reason: str | None
    custom_fields: dict[str, Any]

    pipeline_id: UUID
    stage: DealStageSummary
    client: DealClientSummary
    listing: DealPropertySummary | None
    owner: DealParty | None

    created_at: datetime
    updated_at: datetime


class DealFilters(BaseModel):
    """Explicit filter parameters. Deliberately not a generic query DSL."""

    search: str | None = Field(default=None, max_length=200)
    #: Filters on the derived status by resolving it to terminal stage flags.
    status: DealStatus | None = None
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    owner_id: UUID | None = None
    client_id: UUID | None = None
    property_id: UUID | None = None
    priority: DealPriority | None = None
    min_value: Money | None = None
    max_value: Money | None = None
    expected_close_before: date | None = None
    expected_close_after: date | None = None

    @model_validator(mode="after")
    def _value_range_is_coherent(self) -> DealFilters:
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("min_value cannot exceed max_value")
        return self


class DealAssign(BaseModel):
    owner_id: UUID


class DealStageTransition(BaseModel):
    """Move a deal to another stage.

    `lost_reason` is required by the service when the target stage is a losing
    one — a lost deal with no reason is the single most useless row in a CRM,
    because it is exactly the data you want when asking why deals are lost.
    """

    to_stage_id: UUID
    note: str | None = Field(default=None, max_length=2_000)
    lost_reason: str | None = Field(default=None, max_length=2_000)
    #: Overrides the stage default when moving. Rarely needed.
    probability: int | None = Field(default=None, ge=0, le=100)


class DealStageHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    from_stage: DealStageSummary | None
    to_stage: DealStageSummary
    changed_by: DealParty | None
    changed_at: datetime
    #: Seconds spent in `from_stage`. Null on the creation row.
    duration_seconds: float | None
    note: str | None


class DealBoardColumn(BaseModel):
    """One Kanban column: a stage plus the deals sitting in it."""

    stage: DealStageSummary
    deals: list[DealRead]
    #: Sum of `value` across the column. The board header shows this.
    total_value: Decimal
    count: int


class DealBoard(BaseModel):
    pipeline_id: UUID
    pipeline_name: str
    columns: list[DealBoardColumn]
