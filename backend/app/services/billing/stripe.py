"""Stripe billing provider.

Kept behind the `BillingProvider` abstraction, and behind a lazy import: the
`stripe` package is only needed when Stripe is the configured provider, so a
default (`manual`) deployment carries no dependency on it. Every Stripe object
is mapped to the provider dataclasses at the boundary, so nothing above this
file ever sees a Stripe type.

The tenant travels in metadata (`organization_id`) on the customer and the
subscription, so a webhook event resolves its own tenant — a subscription can
never be applied to the wrong organization.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.exceptions import AuthenticationError, ServiceUnavailableError
from app.core.logging import get_logger
from app.services.billing.provider import (
    ProviderEvent,
    ProviderInvoice,
    ProviderSubscription,
)

logger = get_logger(__name__)


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=UTC) if value else None


class StripeBillingProvider:
    name = "stripe"

    def __init__(self, settings: Settings) -> None:
        try:
            import stripe  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - only without the package
            raise ServiceUnavailableError(
                "BILLING_PROVIDER is 'stripe' but the stripe package is not installed."
            ) from exc
        self._stripe = stripe
        stripe.api_key = settings.STRIPE_API_KEY.get_secret_value()
        self._webhook_secret = settings.STRIPE_WEBHOOK_SECRET.get_secret_value()

    def _sub(self, obj: Any) -> ProviderSubscription:
        item = (obj.get("items", {}).get("data") or [{}])[0]
        price = (item.get("price") or {}).get("id")
        return ProviderSubscription(
            id=obj["id"],
            customer_id=obj["customer"],
            status=obj["status"],
            price_id=price,
            current_period_start=_ts(obj.get("current_period_start")),
            current_period_end=_ts(obj.get("current_period_end")),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
        )

    async def create_customer(
        self, *, organization_id: UUID, email: str, name: str
    ) -> str:
        customer = await self._stripe.Customer.create_async(
            email=email, name=name, metadata={"organization_id": str(organization_id)}
        )
        return str(customer["id"])

    async def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str | None,
        seats: int,
        organization_id: UUID,
    ) -> ProviderSubscription:
        subscription = await self._stripe.Subscription.create_async(
            customer=customer_id,
            items=[{"price": price_id, "quantity": seats}],
            metadata={"organization_id": str(organization_id)},
        )
        return self._sub(subscription)

    async def update_subscription(
        self,
        provider_subscription_id: str,
        *,
        price_id: str | None = None,
        seats: int | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> ProviderSubscription:
        params: dict[str, Any] = {}
        if cancel_at_period_end is not None:
            params["cancel_at_period_end"] = cancel_at_period_end
        if price_id is not None or seats is not None:
            current = await self._stripe.Subscription.retrieve_async(
                provider_subscription_id
            )
            item = current["items"]["data"][0]
            update: dict[str, Any] = {"id": item["id"]}
            if price_id is not None:
                update["price"] = price_id
            if seats is not None:
                update["quantity"] = seats
            params["items"] = [update]
        subscription = await self._stripe.Subscription.modify_async(
            provider_subscription_id, **params
        )
        return self._sub(subscription)

    async def cancel_subscription(
        self, provider_subscription_id: str, *, at_period_end: bool
    ) -> ProviderSubscription:
        if at_period_end:
            subscription = await self._stripe.Subscription.modify_async(
                provider_subscription_id, cancel_at_period_end=True
            )
        else:
            subscription = await self._stripe.Subscription.cancel_async(
                provider_subscription_id
            )
        return self._sub(subscription)

    async def create_portal_url(self, customer_id: str, return_url: str) -> str:
        session = await self._stripe.billing_portal.Session.create_async(
            customer=customer_id, return_url=return_url
        )
        return str(session["url"])

    async def list_invoices(
        self, customer_id: str, *, limit: int = 20
    ) -> list[ProviderInvoice]:
        result = await self._stripe.Invoice.list_async(customer=customer_id, limit=limit)
        return [self._invoice(obj) for obj in result["data"]]

    def _invoice(self, obj: Any) -> ProviderInvoice:
        return ProviderInvoice(
            id=obj["id"],
            number=obj.get("number"),
            status=obj.get("status", "open"),
            amount_due_cents=int(obj.get("amount_due", 0)),
            amount_paid_cents=int(obj.get("amount_paid", 0)),
            currency=str(obj.get("currency", "usd")).upper(),
            period_start=_ts(obj.get("period_start")),
            period_end=_ts(obj.get("period_end")),
            hosted_url=obj.get("hosted_invoice_url"),
            pdf_url=obj.get("invoice_pdf"),
            issued_at=_ts(obj.get("created")),
        )

    def verify_event(self, payload: bytes, signature: str | None) -> ProviderEvent:
        if not self._webhook_secret:
            raise AuthenticationError("Stripe webhooks are not configured.")
        if not signature:
            raise AuthenticationError("Missing Stripe signature.")
        try:
            event = self._stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
        except Exception as exc:
            logger.warning("stripe_webhook_invalid", extra={"error": str(exc)})
            raise AuthenticationError("Invalid Stripe signature.") from exc

        obj = event["data"]["object"]
        org_raw = (obj.get("metadata") or {}).get("organization_id")
        organization_id = UUID(org_raw) if org_raw else None
        kind = event["type"]

        subscription = self._sub(obj) if kind.startswith("customer.subscription") else None
        invoice = self._invoice(obj) if kind.startswith("invoice") else None
        return ProviderEvent(
            type=kind,
            organization_id=organization_id,
            subscription=subscription,
            invoice=invoice,
            raw=dict(event),
        )
