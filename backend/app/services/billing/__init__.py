"""Billing (Phase 7.5): provider abstraction and the subscription/usage services.

`get_billing_provider` resolves the configured provider — `manual` by default,
so nothing here reaches an external service unless a deployment opts into Stripe.
The rest of the package (subscriptions, entitlements, quotas, the usage meter)
is provider-agnostic and reads the durable record the earlier phases already
keep: the AI ledger, the API-key metrics, and the attachment sizes.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.billing.provider import BillingProvider, ManualBillingProvider


def get_billing_provider(settings: Settings | None = None) -> BillingProvider:
    """Resolve the configured provider.

    Not cached: the manual provider is a trivial object, and the Stripe one is
    built from settings that a test may vary — a cache keyed on an unhashable
    Settings would be wrong on both counts.
    """
    resolved = settings or get_settings()
    if resolved.BILLING_PROVIDER == "stripe":
        from app.services.billing.stripe import StripeBillingProvider

        return StripeBillingProvider(resolved)
    return ManualBillingProvider()


__all__ = ["BillingProvider", "get_billing_provider"]
