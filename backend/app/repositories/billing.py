"""Billing data access.

`billing_plans` is a global catalogue, so its repository is not tenant-scoped —
there is no `organization_id` to scope by, and every tenant reads the same rows.
`subscriptions` and `invoices` are tenant data and scope exactly like every
other CRM repository: a WHERE clause on top of RLS.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select

from app.models.billing import Invoice, Plan, Subscription
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class PlanRepository:
    def __init__(self, session) -> None:  # type: ignore[no-untyped-def]
        self.session = session

    async def get(self, plan_id: UUID) -> Plan | None:
        result = await self.session.execute(select(Plan).where(Plan.id == plan_id))
        plan: Plan | None = result.scalar_one_or_none()
        return plan

    async def get_by_key(self, key: str) -> Plan | None:
        result = await self.session.execute(select(Plan).where(Plan.key == key))
        plan: Plan | None = result.scalar_one_or_none()
        return plan

    async def list_active(self) -> Sequence[Plan]:
        query = (
            select(Plan)
            .where(Plan.is_active.is_(True))
            .order_by(Plan.price_cents.asc())
        )
        return list((await self.session.execute(query)).scalars().all())


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def get_for_org(self, organization_id: UUID) -> Subscription | None:
        query = select(Subscription).where(
            Subscription.organization_id == organization_id
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def get_by_provider_subscription(
        self, provider_subscription_id: str, organization_id: UUID
    ) -> Subscription | None:
        query = (
            select(Subscription)
            .where(Subscription.organization_id == organization_id)
            .where(
                Subscription.provider_subscription_id == provider_subscription_id
            )
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()


class InvoiceRepository(BaseRepository[Invoice]):
    model = Invoice

    async def get_by_provider_id(
        self, provider_invoice_id: str, organization_id: UUID
    ) -> Invoice | None:
        query = (
            select(Invoice)
            .where(Invoice.organization_id == organization_id)
            .where(Invoice.provider_invoice_id == provider_invoice_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[Invoice], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = select(Invoice).where(Invoice.organization_id == organization_id)
        if cursor is not None:
            query = query.where(
                func.row(Invoice.created_at, Invoice.id)
                < func.row(cursor.created_at, cursor.id)
            )
        query = query.order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(
            limit + 1
        )
        rows = list((await self.session.execute(query)).scalars().all())
        return rows[:limit], len(rows) > limit
