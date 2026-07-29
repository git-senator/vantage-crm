"""Marketplace billing & monetization contracts (Phase 9.4).

Money is integer cents throughout, matching the billing and AI ledgers. No secret
or provider credential is ever carried: an entitlement reports its opaque provider
reference, never a payment token, and no invoice is generated.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ------------------------------------------------------- pricing


class PlanCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    pricing_model: str = Field(min_length=1, max_length=16)
    amount_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    interval: str = Field(default="month", max_length=8)
    included_units: int = Field(default=0, ge=0)
    unit_amount_cents: int = Field(default=0, ge=0)
    trial_days: int = Field(default=0, ge=0)


class IntegrationPlanRead(BaseModel):
    id: UUID
    listing_key: str
    key: str
    name: str
    pricing_model: str
    amount_cents: int
    currency: str
    interval: str
    included_units: int
    unit_amount_cents: int
    trial_days: int
    is_free: bool
    is_active: bool
    created_at: datetime


class ListingPricingRead(BaseModel):
    listing_key: str
    has_paid_plan: bool
    has_free_option: bool
    plans: list[IntegrationPlanRead]


# ------------------------------------------------------- entitlements


class SubscribeRequest(BaseModel):
    plan_key: str = Field(min_length=1, max_length=40)


class EntitlementRead(BaseModel):
    id: UUID | None
    listing_key: str
    plan_key: str | None
    #: The stored status.
    status: str
    #: The effective status once trial/period deadlines are applied.
    effective_status: str
    entitled: bool
    provider: str | None
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    canceled_at: datetime | None


# ------------------------------------------------------- usage metering


class UsageRecordRequest(BaseModel):
    metric: str = Field(min_length=1, max_length=20)
    quantity: int = Field(ge=0)


class UsageMetricRead(BaseModel):
    metric: str
    quantity: int


class UsageSummaryRead(BaseModel):
    listing_key: str
    metrics: list[UsageMetricRead]
    total_units: int
    #: The estimated metered charge in cents for the current usage, given the
    #: tenant's active plan (0 when the plan is not usage-based).
    estimated_charge_cents: int


# ------------------------------------------------------- revenue


class RevenueEventRead(BaseModel):
    id: UUID
    listing_key: str
    kind: str
    gross_cents: int
    platform_cents: int
    developer_cents: int
    currency: str
    developer_org_id: UUID | None
    invoice_reference: str | None
    occurred_at: datetime


class RevenueAnalyticsRead(BaseModel):
    gross_cents: int
    platform_cents: int
    developer_cents: int
    by_kind: dict[str, int]
    events: int


class MarketplaceBillingDashboard(BaseModel):
    active_plans: int
    active_entitlements: int
    gross_cents: int
    platform_cents: int
    developer_cents: int
    by_kind: dict[str, int]


__all__ = [
    "EntitlementRead",
    "IntegrationPlanRead",
    "ListingPricingRead",
    "MarketplaceBillingDashboard",
    "PlanCreateRequest",
    "RevenueAnalyticsRead",
    "RevenueEventRead",
    "SubscribeRequest",
    "UsageMetricRead",
    "UsageRecordRequest",
    "UsageSummaryRead",
]
