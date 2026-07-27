"""Billing — plans, tenant subscriptions, and invoices (Phase 7.5).

Three tables, and one of them is deliberately *not* tenant-scoped:

  * ``Plan`` is a global catalogue, like ``permissions`` and the system roles.
    It carries no ``organization_id`` and no RLS — every tenant sees the same
    plans, and the plan a tenant is *on* is the subscription's job to say. Its
    features and quotas are JSONB so a plan change is data, not a migration.
  * ``Subscription`` is one row per tenant (unique on ``organization_id``),
    RLS-FORCEd. It holds the status, the licensed seat count, the billing
    period, the provider's ids, and the grace deadline that keeps a single
    failed charge from locking a paying customer out mid-cycle.
  * ``Invoice`` is the tenant's billing history, RLS-FORCEd, mirrored from the
    provider (or written directly under the manual provider).

Money is integer cents, never a float — the same discipline the AI ledger uses
for ``cost_usd``, for the same reason: a fraction that drifts per row drifts a
bill over a year of them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

#: Subscription lifecycle. Mirrors the provider's vocabulary so a synced event
#: maps straight across. `past_due` is the dunning state the grace period backs.
SUBSCRIPTION_STATUSES = (
    "trialing",
    "active",
    "past_due",
    "canceled",
    "incomplete",
    "paused",
)

#: Statuses that entitle a tenant to its plan outright (before the grace rule).
ENTITLED_STATUSES = frozenset({"trialing", "active"})


class Plan(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "billing_plans"

    #: Stable slug used everywhere a plan is referenced: `free`, `pro`.
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Base price per month, in cents.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    #: Seats bundled into the base price, and the price of each seat beyond them.
    included_seats: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    price_per_seat_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    #: `{feature_key: true}` — the entitlements this plan grants.
    features: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: `{quota_key: limit}` — a missing or null key means unlimited.
    quotas: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: The provider's price handle, when one backs this plan.
    provider_price_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    #: Whether the plan is offered on the pricing page, versus a legacy or
    #: bespoke plan a tenant can be on but not newly choose.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Plan {self.key}>"


class Subscription(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("billing_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    #: Licensed seats. Distinct from seats *used* (a live count of active users);
    #: seat management is keeping the first at or above the second.
    seats: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    provider: Mapped[str] = mapped_column(String(20), nullable=False, server_default="manual")
    provider_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When a `past_due` subscription stops being entitled to its plan. Set when
    #: the status becomes past_due, cleared when it recovers.
    grace_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    plan: Mapped[Plan] = relationship(Plan, lazy="joined")

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_subscriptions_org"),
        Index("ix_subscriptions_provider_sub", "provider_subscription_id"),
    )

    def is_entitled(self, *, now: datetime | None = None) -> bool:
        """Whether this subscription currently grants its plan.

        Active and trialing always do. A past-due subscription does until its
        grace deadline passes — the whole point of the deadline.
        """
        if self.status in ENTITLED_STATUSES:
            return True
        if self.status == "past_due":
            moment = now or datetime.now(UTC)
            return self.grace_period_end is None or self.grace_period_end > moment
        return False

    def __repr__(self) -> str:
        return f"<Subscription {self.organization_id} {self.status}>"


class Invoice(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "invoices"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    provider_invoice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")

    amount_due_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    amount_paid_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")

    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    hosted_invoice_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_invoices_org", "organization_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.number or self.id} {self.status}>"
