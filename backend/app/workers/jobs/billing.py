"""Billing jobs: retire subscriptions whose grace period has lapsed.

A `past_due` subscription keeps its plan through the grace window so one failed
charge does not lock a paying customer out mid-cycle. When the window closes and
the charge still has not cleared, the subscription is marked `canceled` — which
drops the tenant to the default plan the next time entitlements are read.

Idempotent by construction: it only touches subscriptions that are still
`past_due` with a deadline in the past, so a second run finds nothing to do. The
database is the record of outstanding work; a lost run is a day's latency, not a
tenant that keeps paid features forever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.repositories.billing import SubscriptionRepository
from app.workers.context import active_organization_ids, tenant_scope, unscoped_scope
from app.workers.runner import job

logger = get_logger(__name__)


@job(max_tries=2)
async def sweep_subscription_grace(ctx: dict[str, Any]) -> int:
    """Cancel subscriptions whose past-due grace period has expired. Returns how
    many."""
    now = datetime.now(UTC)
    canceled = 0

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            subscription = await SubscriptionRepository(session).get_for_org(
                organization
            )
            if (
                subscription is not None
                and subscription.status == "past_due"
                and subscription.grace_period_end is not None
                and subscription.grace_period_end <= now
            ):
                subscription.status = "canceled"
                subscription.canceled_at = now
                canceled += 1

    if canceled:
        logger.info("subscriptions_grace_swept", extra={"count": canceled})
    return canceled
