"""Per-tenant usage, read from the durable record.

Live metrics (the `/metrics` scrape) reset on restart and are process-local —
right for a rate source, wrong for "how much has this tenant used this month".
That question is answered here, from the tables that already keep the durable
truth: the AI ledger (`ai_jobs`), the API keys, and the webhook delivery
history. Nothing new is stored; this reads what the earlier phases already
record, scoped to the caller's tenant under RLS.

Gated on `settings.manage`, like the rest of the admin surface — usage is an
operational and billing-adjacent view, not per-user data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.ai import AiJobRepository
from app.repositories.api_key import ApiKeyRepository
from app.repositories.webhook import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
)
from app.services.rbac import AuthorizationContext

PERMISSION = "settings.manage"


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class UsageService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.ai = AiJobRepository(session)
        self.api_keys = ApiKeyRepository(session)
        self.webhook_endpoints = WebhookEndpointRepository(session)
        self.webhook_deliveries = WebhookDeliveryRepository(session)

    async def tenant_usage(self, *, now: datetime | None = None) -> dict[str, Any]:
        self.auth.require(PERMISSION)
        moment = now or datetime.now(UTC)
        org = self.auth.organization_id
        since = _month_start(moment)

        spend = await self.ai.spend_since(org, since)
        by_feature = await self.ai.usage_by_feature(org, since)
        ai_calls = sum(calls for _feature, calls, _cost in by_feature)

        keys = list(await self.api_keys.list_for_org(org))
        endpoints = list(await self.webhook_endpoints.list_for_org(org))
        deliveries = await self.webhook_deliveries.count_by_status(org)

        return {
            "period_start": since,
            "ai": {
                "calls": ai_calls,
                "cost_usd": spend,
                "by_feature": [
                    {"feature": feature, "calls": calls, "cost_usd": cost}
                    for feature, calls, cost in by_feature
                ],
            },
            "api_keys": {
                "total": len(keys),
                "active": sum(1 for key in keys if key.is_active),
            },
            "webhooks": {
                "endpoints_total": len(endpoints),
                "endpoints_active": sum(1 for e in endpoints if e.is_active),
                "deliveries_by_status": deliveries,
            },
        }


__all__ = ["PERMISSION", "UsageService"]
