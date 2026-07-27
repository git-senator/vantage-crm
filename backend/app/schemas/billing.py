"""Billing API contracts (Phase 7.5)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class PlanRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    key: str
    name: str
    description: str | None
    price_cents: int
    currency: str
    included_seats: int
    price_per_seat_cents: int
    features: dict[str, Any]
    quotas: dict[str, Any]
    is_public: bool


class SubscriptionRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    status: str
    seats: int
    plan: PlanRead
    provider: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    trial_end: datetime | None
    grace_period_end: datetime | None
    canceled_at: datetime | None
    created_at: datetime


class SubscribeRequest(BaseModel):
    plan_key: str = Field(min_length=1, max_length=40)
    seats: int | None = Field(default=None, ge=1, le=10_000)


class SeatUpdate(BaseModel):
    seats: int = Field(ge=1, le=10_000)


class CancelRequest(BaseModel):
    at_period_end: bool = True


class InvoiceRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    number: str | None
    status: str
    amount_due_cents: int
    amount_paid_cents: int
    currency: str
    period_start: datetime | None
    period_end: datetime | None
    hosted_invoice_url: str | None
    pdf_url: str | None
    issued_at: datetime | None
    created_at: datetime


class UsageRead(BaseModel):
    period_start: datetime
    seats_used: int
    ai_cost_usd: Decimal
    api_calls: int
    storage_bytes: int
    storage_gb: float


class EntitlementsRead(BaseModel):
    plan_key: str | None
    status: str
    features: dict[str, Any]
    in_grace: bool


class PortalSession(BaseModel):
    url: str
