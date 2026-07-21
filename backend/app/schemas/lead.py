"""Lead contracts.

Separate Create / Update / Read models rather than one shared model:

  * Create requires what a row needs; Update makes everything optional so a
    PATCH can carry one field.
  * Read declares exactly what crosses the boundary, so adding a column cannot
    silently start exposing it.
  * Neither Create nor Update accepts `organization_id`, `score` or audit
    columns — a client must not be able to set them, and omitting them from
    the model is stronger than stripping them later.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

LeadStage = Literal["new", "contacted", "qualified", "touring", "unqualified"]
LeadStatus = Literal["open", "converted", "lost"]
LeadTemperature = Literal["hot", "warm", "cold"]
LeadSource = Literal[
    "zillow",
    "website",
    "referral",
    "open_house",
    "instagram",
    "cold_call",
    "realtor_com",
    "other",
]

# Bounded so a client cannot post a megabyte of tags. The database has matching
# CHECK constraints — validation here gives a good error, the constraint
# guarantees the invariant.
Name = Annotated[str, Field(min_length=1, max_length=100)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("99999999999.99"), decimal_places=2)]


class LeadOwner(BaseModel):
    """Minimal owner projection for list and detail views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class LeadBase(BaseModel):
    first_name: Name
    last_name: Name
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    stage: LeadStage = "new"
    source: LeadSource = "other"
    temperature: LeadTemperature = "warm"
    budget_min: Money | None = None
    budget_max: Money | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    preferred_location: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _budget_range_is_coherent(self) -> LeadBase:
        """Mirrors the database CHECK.

        Validating here as well gives the user a field-level message instead of
        a 500 from a constraint violation.
        """
        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_min > self.budget_max
        ):
            raise ValueError("budget_min cannot exceed budget_max")
        return self


class LeadCreate(LeadBase):
    """New lead. Owner defaults to the creator when omitted."""

    owner_id: UUID | None = None


class LeadUpdate(BaseModel):
    """Partial update. Every field optional; unset fields are untouched.

    `status` is absent by design — converting or losing a lead is a domain
    action with side effects, not a field edit.
    """

    first_name: Name | None = None
    last_name: Name | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    stage: LeadStage | None = None
    source: LeadSource | None = None
    temperature: LeadTemperature | None = None
    budget_min: Money | None = None
    budget_max: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    preferred_location: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=10_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    owner_id: UUID | None = None


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    full_name: str
    email: str | None
    phone: str | None
    stage: str
    status: str
    source: str
    temperature: str
    budget_min: Decimal | None
    budget_max: Decimal | None
    currency: str
    preferred_location: str | None
    notes: str | None
    tags: list[str]
    score: int | None
    last_contacted_at: datetime | None
    custom_fields: dict[str, Any]
    owner: LeadOwner | None
    created_at: datetime
    updated_at: datetime


class LeadFilters(BaseModel):
    """Explicit filter parameters.

    Deliberately not a generic query DSL. A DSL over a tenant-scoped table is
    both an injection surface and an unbounded-query risk, and it makes the
    index requirements impossible to reason about.
    """

    search: str | None = Field(default=None, max_length=200)
    stage: LeadStage | None = None
    status: LeadStatus | None = None
    source: LeadSource | None = None
    temperature: LeadTemperature | None = None
    owner_id: UUID | None = None
    tag: str | None = Field(default=None, max_length=40)


class LeadAssign(BaseModel):
    owner_id: UUID
