"""Billing provider abstraction.

Business logic depends on `BillingProvider`, never on Stripe — the same shape as
the storage, AI and email abstractions. The provider does the two things only an
external system can: talk to the payment processor, and verify its webhooks.
Everything else — what a plan grants, when a grace period lapses, which tenant a
subscription belongs to — is the service layer's, and stays testable without a
network.

`ManualBillingProvider` is the default and the test double at once: it synthes-
ises ids and periods and calls nothing, which is exactly right for a self-hosted
deployment that bills out of band and for a test that must not reach Stripe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.exceptions import ConflictError


@dataclass(frozen=True, slots=True)
class ProviderSubscription:
    id: str
    customer_id: str
    status: str
    price_id: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool = False


@dataclass(frozen=True, slots=True)
class ProviderInvoice:
    id: str
    number: str | None
    status: str
    amount_due_cents: int
    amount_paid_cents: int
    currency: str
    period_start: datetime | None
    period_end: datetime | None
    hosted_url: str | None
    pdf_url: str | None
    issued_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """A verified inbound provider event.

    `organization_id` is resolved from the object's metadata, which the service
    sets when it creates the customer/subscription — so an event carries its own
    tenant and cannot be applied to the wrong one.
    """

    type: str
    organization_id: UUID | None
    subscription: ProviderSubscription | None = None
    invoice: ProviderInvoice | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class BillingProvider(Protocol):
    name: str

    async def create_customer(
        self, *, organization_id: UUID, email: str, name: str
    ) -> str: ...

    async def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str | None,
        seats: int,
        organization_id: UUID,
    ) -> ProviderSubscription: ...

    async def update_subscription(
        self,
        provider_subscription_id: str,
        *,
        price_id: str | None = None,
        seats: int | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> ProviderSubscription: ...

    async def cancel_subscription(
        self, provider_subscription_id: str, *, at_period_end: bool
    ) -> ProviderSubscription: ...

    async def create_portal_url(self, customer_id: str, return_url: str) -> str: ...

    async def list_invoices(
        self, customer_id: str, *, limit: int = 20
    ) -> list[ProviderInvoice]: ...

    def verify_event(self, payload: bytes, signature: str | None) -> ProviderEvent: ...


def _period(now: datetime | None = None) -> tuple[datetime, datetime]:
    start = now or datetime.now(UTC)
    return start, start + timedelta(days=30)


class ManualBillingProvider:
    """No external calls: ids and periods are synthesised locally.

    Correct for a self-hosted deployment that reconciles payment out of band,
    and it is the provider the tests run against.
    """

    name = "manual"

    async def create_customer(
        self, *, organization_id: UUID, email: str, name: str
    ) -> str:
        return f"manual_cus_{uuid4().hex[:16]}"

    async def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str | None,
        seats: int,
        organization_id: UUID,
    ) -> ProviderSubscription:
        start, end = _period()
        return ProviderSubscription(
            id=f"manual_sub_{uuid4().hex[:16]}",
            customer_id=customer_id,
            status="active",
            price_id=price_id,
            current_period_start=start,
            current_period_end=end,
        )

    async def update_subscription(
        self,
        provider_subscription_id: str,
        *,
        price_id: str | None = None,
        seats: int | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> ProviderSubscription:
        start, end = _period()
        return ProviderSubscription(
            id=provider_subscription_id,
            customer_id="",
            status="active",
            price_id=price_id,
            current_period_start=start,
            current_period_end=end,
            cancel_at_period_end=bool(cancel_at_period_end),
        )

    async def cancel_subscription(
        self, provider_subscription_id: str, *, at_period_end: bool
    ) -> ProviderSubscription:
        start, end = _period()
        return ProviderSubscription(
            id=provider_subscription_id,
            customer_id="",
            status="active" if at_period_end else "canceled",
            price_id=None,
            current_period_start=start,
            current_period_end=end,
            cancel_at_period_end=at_period_end,
        )

    async def create_portal_url(self, customer_id: str, return_url: str) -> str:
        # No hosted portal exists for out-of-band billing; refusing is the honest
        # answer, and a 409 tells the caller this provider does not offer it.
        raise ConflictError("The manual billing provider has no customer portal.")

    async def list_invoices(
        self, customer_id: str, *, limit: int = 20
    ) -> list[ProviderInvoice]:
        return []

    def verify_event(self, payload: bytes, signature: str | None) -> ProviderEvent:
        raise ConflictError("The manual billing provider receives no webhooks.")
