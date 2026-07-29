"""Marketplace billing & monetization endpoints (Phase 9.4).

Mounted under ``/marketplace/billing``. The pricing view is available to any
authenticated member; every mutation and every analytics/dashboard read is gated
on ``settings.manage`` inside its service. No payment is processed and no invoice
is generated here — these endpoints manage pricing, entitlements, metered usage,
and revenue records.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.marketplace_billing import (
    EntitlementRead,
    IntegrationPlanRead,
    ListingPricingRead,
    MarketplaceBillingDashboard,
    PlanCreateRequest,
    RevenueAnalyticsRead,
    RevenueEventRead,
    SubscribeRequest,
    UsageMetricRead,
    UsageRecordRequest,
    UsageSummaryRead,
)
from app.services.marketplace_billing import (
    IntegrationEntitlementService,
    MarketplaceBillingService,
    RevenueTrackingService,
    UsageMeteringService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------- dashboards & analytics


@router.get("/dashboard", response_model=MarketplaceBillingDashboard)
async def billing_dashboard(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> MarketplaceBillingDashboard:
    return await MarketplaceBillingService(session, auth, settings).dashboard()


@router.get("/revenue", response_model=RevenueAnalyticsRead)
async def revenue_analytics(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> RevenueAnalyticsRead:
    return await RevenueTrackingService(session, auth, settings).analytics()


@router.get("/revenue/events", response_model=list[RevenueEventRead])
async def revenue_events(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[RevenueEventRead]:
    return await RevenueTrackingService(session, auth, settings).events()


# ------------------------------------------------------- pricing


@router.get("/listings/{listing_id}/pricing", response_model=ListingPricingRead)
async def listing_pricing(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> ListingPricingRead:
    return await MarketplaceBillingService(session, auth, settings).pricing(listing_id)


@router.post(
    "/listings/{listing_id}/plans",
    response_model=IntegrationPlanRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_plan(
    listing_id: UUID,
    payload: PlanCreateRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> IntegrationPlanRead:
    result = await MarketplaceBillingService(session, auth, settings).create_plan(
        user, listing_id, payload
    )
    await session.commit()
    return result


# ------------------------------------------------------- entitlements


@router.get("/listings/{listing_id}/entitlement", response_model=EntitlementRead)
async def entitlement_status(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> EntitlementRead:
    return await IntegrationEntitlementService(
        session, auth, settings
    ).status(listing_id)


@router.post(
    "/listings/{listing_id}/subscribe",
    response_model=EntitlementRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def subscribe(
    listing_id: UUID,
    payload: SubscribeRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> EntitlementRead:
    result = await IntegrationEntitlementService(session, auth, settings).subscribe(
        user, listing_id, payload.plan_key
    )
    await session.commit()
    return result


@router.delete(
    "/listings/{listing_id}/subscribe",
    response_model=EntitlementRead,
    dependencies=_CSRF,
)
async def cancel_subscription(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> EntitlementRead:
    result = await IntegrationEntitlementService(session, auth, settings).cancel(
        user, listing_id
    )
    await session.commit()
    return result


# ------------------------------------------------------- usage metering


@router.get("/listings/{listing_id}/usage", response_model=UsageSummaryRead)
async def usage_summary(
    listing_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> UsageSummaryRead:
    return await UsageMeteringService(session, auth, settings).summary(listing_id)


@router.post(
    "/listings/{listing_id}/usage",
    response_model=UsageMetricRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def record_usage(
    listing_id: UUID,
    payload: UsageRecordRequest,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> UsageMetricRead:
    result = await UsageMeteringService(session, auth, settings).record(
        listing_id, payload.metric, payload.quantity
    )
    await session.commit()
    return result
