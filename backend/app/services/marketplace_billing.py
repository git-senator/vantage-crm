"""Marketplace billing & monetization services (Phase 9.4).

The monetization layer over the marketplace. Four services, all reading the same
integration listing as their subject:

  * ``MarketplaceBillingService`` — pricing plans for a listing, the pricing view,
    and the billing dashboard.
  * ``IntegrationEntitlementService`` — a tenant's right to a paid integration:
    subscribe, cancel, read status, and the installation gate the operations
    service consults.
  * ``UsageMeteringService`` — recording metered usage and summarising it into a
    charge estimate.
  * ``RevenueTrackingService`` — recording revenue events split platform/developer
    and the revenue analytics.

No payment is processed and no invoice is generated. The existing billing provider
abstraction supplies only a *label* for where a real provider's handle would sit;
references are opaque strings. Money is integer cents throughout. Every mutation
is gated on ``settings.manage`` and runs under RLS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.marketplace.entitlement import (
    can_install,
    effective_status,
    is_entitled,
)
from app.marketplace.metering import (
    billable_units,
    meter_charge,
    validate_metric,
)
from app.marketplace.pricing import (
    Price,
    compute_charge,
    is_free,
    validate_interval,
    validate_pricing_model,
)
from app.marketplace.revenue import split_revenue, validate_kind
from app.models.marketplace import (
    IntegrationEntitlement,
    IntegrationListing,
    IntegrationPlan,
    RevenueEvent,
    UsageRecord,
)
from app.models.user import User
from app.repositories.marketplace import (
    IntegrationEntitlementRepository,
    IntegrationListingRepository,
    IntegrationPlanRepository,
    RevenueEventRepository,
    UsageRecordRepository,
)
from app.schemas.marketplace_billing import (
    EntitlementRead,
    IntegrationPlanRead,
    ListingPricingRead,
    MarketplaceBillingDashboard,
    PlanCreateRequest,
    RevenueAnalyticsRead,
    RevenueEventRead,
    UsageMetricRead,
    UsageSummaryRead,
)
from app.services.audit import AuditService
from app.services.billing import get_billing_provider
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"

_INTERVAL_DAYS = {"month": 30, "year": 365}


def _now() -> datetime:
    return datetime.now(UTC)


def _price(plan: IntegrationPlan) -> Price:
    return Price(
        model=plan.pricing_model,
        amount_cents=plan.amount_cents,
        currency=plan.currency,
        interval=plan.interval,
        included_units=plan.included_units,
        unit_amount_cents=plan.unit_amount_cents,
        trial_days=plan.trial_days,
    )


def _plan_read(plan: IntegrationPlan, listing_key: str) -> IntegrationPlanRead:
    return IntegrationPlanRead(
        id=plan.id,
        listing_key=listing_key,
        key=plan.key,
        name=plan.name,
        pricing_model=plan.pricing_model,
        amount_cents=plan.amount_cents,
        currency=plan.currency,
        interval=plan.interval,
        included_units=plan.included_units,
        unit_amount_cents=plan.unit_amount_cents,
        trial_days=plan.trial_days,
        is_free=is_free(_price(plan)),
        is_active=plan.is_active,
        created_at=plan.created_at,
    )


# =========================================================== pricing / dashboard


class MarketplaceBillingService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.plans = IntegrationPlanRepository(session)
        self.entitlements = IntegrationEntitlementRepository(session)
        self.revenue = RevenueEventRepository(session)
        self.audit = AuditService(session)

    async def create_plan(
        self, actor: User, listing_id: UUID, payload: PlanCreateRequest
    ) -> IntegrationPlanRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load_own(listing_id)
        try:
            validate_pricing_model(payload.pricing_model)
            validate_interval(payload.interval)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        if await self.plans.get_by_key(
            listing.id, payload.key, self.auth.organization_id
        ):
            raise AppError(f"A plan with key '{payload.key}' already exists.")

        plan = IntegrationPlan(
            publisher_organization_id=listing.publisher_organization_id,
            created_by=actor.id,
            listing_id=listing.id,
            key=payload.key,
            name=payload.name,
            pricing_model=payload.pricing_model,
            amount_cents=payload.amount_cents,
            currency=payload.currency,
            interval=payload.interval,
            included_units=payload.included_units,
            unit_amount_cents=payload.unit_amount_cents,
            trial_days=payload.trial_days,
        )
        self.session.add(plan)
        await self.session.flush()
        await self.session.refresh(plan)
        await self.audit.record(
            action=AuditAction.MARKETPLACE_PLAN_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="integration_plan",
            entity_id=plan.id,
            metadata={"listing_key": listing.key, "plan_key": plan.key},
        )
        return _plan_read(plan, listing.key)

    async def pricing(self, listing_id: UUID) -> ListingPricingRead:
        """The pricing view for a listing — available to any member."""
        listing = await self._load(listing_id)
        plans = await self.plans.list_for_listing(
            listing.id, self.auth.organization_id, active_only=True
        )
        reads = [_plan_read(plan, listing.key) for plan in plans]
        has_free = (not plans) or any(read.is_free for read in reads)
        has_paid = any(not read.is_free for read in reads)
        return ListingPricingRead(
            listing_key=listing.key,
            has_paid_plan=has_paid,
            has_free_option=has_free,
            plans=reads,
        )

    async def dashboard(self) -> MarketplaceBillingDashboard:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id
        totals = await self.revenue.totals(org)
        return MarketplaceBillingDashboard(
            active_plans=await self.plans.count_active(org),
            active_entitlements=await self.entitlements.count_active(org),
            gross_cents=totals["gross_cents"],
            platform_cents=totals["platform_cents"],
            developer_cents=totals["developer_cents"],
            by_kind=await self.revenue.gross_by_kind(org),
        )

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.listings.get_visible(
            listing_id, self.auth.organization_id
        )
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing

    async def _load_own(self, listing_id: UUID) -> IntegrationListing:
        listing = await self._load(listing_id)
        if listing.publisher_organization_id != self.auth.organization_id:
            raise AppError("A curated listing cannot be priced by a tenant.")
        return listing


# =========================================================== entitlements


class IntegrationEntitlementService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.plans = IntegrationPlanRepository(session)
        self.entitlements = IntegrationEntitlementRepository(session)
        self.audit = AuditService(session)

    async def subscribe(
        self, actor: User, listing_id: UUID, plan_key: str
    ) -> EntitlementRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        plan = await self.plans.get_by_key(
            listing.id, plan_key, self.auth.organization_id
        )
        if plan is None:
            raise NotFoundError("Plan not found for this integration.")
        if await self.entitlements.get_active(self.auth.organization_id, listing.id):
            raise ConflictError("This integration is already subscribed.")

        now = _now()
        price = _price(plan)
        trialing = plan.trial_days > 0 and not is_free(price)
        status = "trialing" if trialing else "active"
        trial_ends = now + timedelta(days=plan.trial_days) if trialing else None
        period_end = now + timedelta(days=_INTERVAL_DAYS.get(plan.interval, 30))

        entitlement = IntegrationEntitlement(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            listing_id=listing.id,
            plan_id=plan.id,
            status=status,
            provider=get_billing_provider(self.settings).name,
            provider_reference=f"mkt_{uuid4().hex[:16]}",
            trial_ends_at=trial_ends,
            current_period_end=period_end,
        )
        self.session.add(entitlement)
        await self.session.flush()
        await self.session.refresh(entitlement)

        # Book subscription revenue when the entitlement is active from the start
        # (a trial books nothing until it converts). Free plans book nothing.
        gross = compute_charge(price) if status == "active" else 0
        if gross > 0:
            await RevenueTrackingService(
                self.session, self.auth, self.settings
            ).record(
                listing,
                kind="subscription",
                gross_cents=gross,
                plan_id=plan.id,
                invoice_reference=entitlement.provider_reference,
            )

        await self._audit(
            AuditAction.MARKETPLACE_ENTITLEMENT_GRANTED, actor, listing, entitlement
        )
        return self._read(listing.key, plan.key, entitlement, now)

    async def cancel(self, actor: User, listing_id: UUID) -> EntitlementRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        entitlement = await self.entitlements.get_active(
            self.auth.organization_id, listing.id
        )
        if entitlement is None:
            raise NotFoundError("No active entitlement for this integration.")
        entitlement.status = "canceled"
        entitlement.canceled_at = _now()
        await self.session.flush()
        plan_key = await self._plan_key(entitlement.plan_id)
        await self._audit(
            AuditAction.MARKETPLACE_ENTITLEMENT_CANCELED, actor, listing, entitlement
        )
        return self._read(listing.key, plan_key, entitlement, _now())

    async def status(self, listing_id: UUID) -> EntitlementRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        entitlement = await self.entitlements.get_active(
            self.auth.organization_id, listing.id
        )
        if entitlement is None:
            return EntitlementRead(
                id=None,
                listing_key=listing.key,
                plan_key=None,
                status="none",
                effective_status="none",
                entitled=False,
                provider=None,
                trial_ends_at=None,
                current_period_end=None,
                canceled_at=None,
            )
        plan_key = await self._plan_key(entitlement.plan_id)
        return self._read(listing.key, plan_key, entitlement, _now())

    async def require_installable(self, listing: IntegrationListing) -> None:
        """The installation gate the operations service consults.

        A no-op unless ``MARKETPLACE_BILLING_ENFORCED`` is on, so existing install
        behaviour is preserved by default. When enforced, a paid-only integration
        needs a currently-entitled tenant; a free integration (or one offering a
        free plan) always installs.
        """
        if not self.settings.MARKETPLACE_BILLING_ENFORCED:
            return
        plans = await self.plans.list_for_listing(
            listing.id, self.auth.organization_id, active_only=True
        )
        has_paid = any(not is_free(_price(plan)) for plan in plans)
        has_free = (not plans) or any(is_free(_price(plan)) for plan in plans)
        entitlement = await self.entitlements.get_active(
            self.auth.organization_id, listing.id
        )
        entitled = entitlement is not None and is_entitled(
            effective_status(
                entitlement.status,
                trial_ends_at=entitlement.trial_ends_at,
                period_end=entitlement.current_period_end,
                now=_now(),
            )
        )
        if not can_install(
            has_paid_plan=has_paid, has_free_option=has_free, entitled=entitled
        ):
            raise AppError(
                "This integration requires an active subscription to install."
            )

    async def _plan_key(self, plan_id: UUID | None) -> str | None:
        if plan_id is None:
            return None
        plan = await self.plans.get_visible(plan_id, self.auth.organization_id)
        return plan.key if plan else None

    def _read(
        self,
        listing_key: str,
        plan_key: str | None,
        entitlement: IntegrationEntitlement,
        now: datetime,
    ) -> EntitlementRead:
        effective = effective_status(
            entitlement.status,
            trial_ends_at=entitlement.trial_ends_at,
            period_end=entitlement.current_period_end,
            now=now,
        )
        return EntitlementRead(
            id=entitlement.id,
            listing_key=listing_key,
            plan_key=plan_key,
            status=entitlement.status,
            effective_status=effective,
            entitled=is_entitled(effective),
            provider=entitlement.provider,
            trial_ends_at=entitlement.trial_ends_at,
            current_period_end=entitlement.current_period_end,
            canceled_at=entitlement.canceled_at,
        )

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.listings.get_visible(
            listing_id, self.auth.organization_id
        )
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing

    async def _audit(
        self,
        action: str,
        actor: User,
        listing: IntegrationListing,
        entitlement: IntegrationEntitlement,
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="integration_entitlement",
            entity_id=entitlement.id,
            metadata={"listing_key": listing.key, "status": entitlement.status},
        )


# =========================================================== usage metering


class UsageMeteringService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.plans = IntegrationPlanRepository(session)
        self.entitlements = IntegrationEntitlementRepository(session)
        self.usage = UsageRecordRepository(session)

    async def record(
        self, listing_id: UUID, metric: str, quantity: int
    ) -> UsageMetricRead:
        self.auth.require(MANAGE_PERMISSION)
        try:
            validate_metric(metric)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        if quantity < 0:
            raise AppError("Usage quantity cannot be negative.")
        listing = await self._load(listing_id)
        entitlement = await self.entitlements.get_active(
            self.auth.organization_id, listing.id
        )
        self.session.add(
            UsageRecord(
                organization_id=self.auth.organization_id,
                listing_id=listing.id,
                entitlement_id=entitlement.id if entitlement else None,
                metric=metric,
                quantity=quantity,
            )
        )
        await self.session.flush()
        return UsageMetricRead(metric=metric, quantity=quantity)

    async def summary(self, listing_id: UUID) -> UsageSummaryRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        totals = await self.usage.aggregate_by_metric(
            self.auth.organization_id, listing.id
        )
        total_units = sum(totals.values())
        estimate = await self._estimate_charge(listing, total_units)
        return UsageSummaryRead(
            listing_key=listing.key,
            metrics=[
                UsageMetricRead(metric=metric, quantity=quantity)
                for metric, quantity in sorted(totals.items())
            ],
            total_units=total_units,
            estimated_charge_cents=estimate,
        )

    async def _estimate_charge(
        self, listing: IntegrationListing, total_units: int
    ) -> int:
        """The metered charge for the current usage under the active usage plan,
        or zero when the tenant is not on a usage plan."""
        entitlement = await self.entitlements.get_active(
            self.auth.organization_id, listing.id
        )
        if entitlement is None or entitlement.plan_id is None:
            return 0
        plan = await self.plans.get_visible(
            entitlement.plan_id, self.auth.organization_id
        )
        if plan is None or plan.pricing_model != "usage":
            return 0
        units = billable_units(total_units, plan.included_units)
        return meter_charge(plan.unit_amount_cents, units)

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.listings.get_visible(
            listing_id, self.auth.organization_id
        )
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing


# =========================================================== revenue tracking


class RevenueTrackingService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.revenue = RevenueEventRepository(session)

    async def record(
        self,
        listing: IntegrationListing,
        *,
        kind: str,
        gross_cents: int,
        plan_id: UUID | None = None,
        invoice_reference: str | None = None,
        currency: str = "USD",
    ) -> RevenueEventRead:
        try:
            validate_kind(kind)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        split = split_revenue(gross_cents, self.settings.MARKETPLACE_PLATFORM_FEE_BPS)
        event = RevenueEvent(
            organization_id=self.auth.organization_id,
            listing_id=listing.id,
            plan_id=plan_id,
            # Attribute the developer share to the listing's publisher; NULL means
            # the platform (a curated listing).
            developer_org_id=listing.publisher_organization_id,
            kind=kind,
            gross_cents=split.gross_cents,
            platform_cents=split.platform_cents,
            developer_cents=split.developer_cents,
            currency=currency,
            invoice_reference=invoice_reference,
        )
        self.session.add(event)
        await self.session.flush()
        await self.session.refresh(event)
        return self._read(listing.key, event)

    async def analytics(self) -> RevenueAnalyticsRead:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id
        totals = await self.revenue.totals(org)
        events = await self.revenue.list_for_org(org)
        return RevenueAnalyticsRead(
            gross_cents=totals["gross_cents"],
            platform_cents=totals["platform_cents"],
            developer_cents=totals["developer_cents"],
            by_kind=await self.revenue.gross_by_kind(org),
            events=len(events),
        )

    async def events(self) -> list[RevenueEventRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.revenue.list_for_org(self.auth.organization_id)
        key_by_id = {
            row.id: row.key
            for row in await self.listings.discover(self.auth.organization_id)
        }
        return [
            self._read(key_by_id.get(row.listing_id, "unknown"), row) for row in rows
        ]

    def _read(self, listing_key: str, event: RevenueEvent) -> RevenueEventRead:
        return RevenueEventRead(
            id=event.id,
            listing_key=listing_key,
            kind=event.kind,
            gross_cents=event.gross_cents,
            platform_cents=event.platform_cents,
            developer_cents=event.developer_cents,
            currency=event.currency,
            developer_org_id=event.developer_org_id,
            invoice_reference=event.invoice_reference,
            occurred_at=event.occurred_at,
        )


__all__ = [
    "MANAGE_PERMISSION",
    "IntegrationEntitlementService",
    "MarketplaceBillingService",
    "RevenueTrackingService",
    "UsageMeteringService",
]
