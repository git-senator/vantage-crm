"""Per-tenant usage contract (Phase 7.4)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class AiFeatureUsage(BaseModel):
    feature: str
    calls: int
    cost_usd: Decimal


class AiUsage(BaseModel):
    calls: int
    cost_usd: Decimal
    by_feature: list[AiFeatureUsage]


class ApiKeyUsage(BaseModel):
    total: int
    active: int


class WebhookUsage(BaseModel):
    endpoints_total: int
    endpoints_active: int
    deliveries_by_status: dict[str, int]


class TenantUsage(BaseModel):
    period_start: datetime
    ai: AiUsage
    api_keys: ApiKeyUsage
    webhooks: WebhookUsage
