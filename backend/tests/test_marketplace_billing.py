"""Marketplace billing & monetization (Phase 9.4).

The properties that carry the milestone:

  * **Pricing is pure arithmetic** — free/flat/per-seat/usage charges are
    deterministic integer cents, and the revenue split reconciles exactly.
  * **Entitlement rules gate installation** — a paid-only integration needs a
    currently-entitled tenant; free ones always install. Enforcement is opt-in.
  * **Usage is metered and summarised into a charge estimate.**
  * **Revenue events are split platform/developer and attributed to a developer.**
  * **Tenant isolation holds**, management is gated on `settings.manage`, and the
    lifecycle acts are audited.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.marketplace.entitlement import can_install, effective_status, is_entitled
from app.marketplace.metering import (
    aggregate,
    billable_units,
    meter_charge,
    validate_metric,
)
from app.marketplace.pricing import Price, compute_charge, is_free, validate_pricing_model
from app.marketplace.revenue import split_revenue, validate_kind
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.marketplace import InstallListingRequest, ListingDraftCreate
from app.schemas.marketplace_billing import PlanCreateRequest
from app.services.marketplace import IntegrationRegistryService
from app.services.marketplace_billing import (
    IntegrationEntitlementService,
    MarketplaceBillingService,
    RevenueTrackingService,
    UsageMeteringService,
)
from app.services.marketplace_operations import MarketplaceOperationsService
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, manage: bool = True) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    grants = {"settings.manage": Scope.ALL} if manage else {"leads.view": Scope.OWN}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=grants,
    )


def _draft(key: str) -> ListingDraftCreate:
    return ListingDraftCreate(
        manifest={
            "key": key,
            "name": "Paid Integration",
            "version": "1.0.0",
            "description": "A paid integration.",
            "publisher": "Dev Co",
            "category": "integration",
            "capabilities": ["events.subscribe"],
            "events": ["deal.created"],
            "config_schema": [],
        },
        vendor="Dev Co",
        category="crm",
        summary="A paid integration.",
        auth_method="api_key",
    )


def _plan(**over: Any) -> PlanCreateRequest:
    base: dict[str, Any] = {
        "key": "pro",
        "name": "Pro",
        "pricing_model": "flat",
        "amount_cents": 1000,
    }
    base.update(over)
    return PlanCreateRequest(**base)


async def _owned_listing(db, auth, user, key):  # type: ignore[no-untyped-def]
    ops = MarketplaceOperationsService(db, auth, _settings())
    return await ops.create_draft(user, _draft(key))


# =============================================================== pure domain


class TestPricing:
    def test_compute_charge(self) -> None:
        assert compute_charge(Price(model="free")) == 0
        assert compute_charge(Price(model="flat", amount_cents=1500)) == 1500
        assert (
            compute_charge(Price(model="per_seat", amount_cents=500), seats=4) == 2000
        )
        usage = Price(
            model="usage", amount_cents=500, included_units=100, unit_amount_cents=2
        )
        assert compute_charge(usage, usage_units=150) == 500 + 50 * 2
        assert compute_charge(usage, usage_units=50) == 500  # under the allowance

    def test_is_free(self) -> None:
        assert is_free(Price(model="free"))
        assert is_free(Price(model="flat", amount_cents=0))
        assert not is_free(Price(model="flat", amount_cents=1))

    def test_validation(self) -> None:
        with pytest.raises(ValueError):
            validate_pricing_model("barter")
        with pytest.raises(ValueError):
            Price(model="flat", interval="fortnight")


class TestEntitlementRules:
    def test_effective_status(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=UTC)
        past = now - timedelta(days=1)
        future = now + timedelta(days=1)

        def eff(status: str, *, trial: Any = None, period: Any = None) -> str:
            return effective_status(
                status, trial_ends_at=trial, period_end=period, now=now
            )

        assert eff("canceled") == "canceled"
        assert eff("trialing", trial=past) == "expired"
        assert eff("trialing", trial=future) == "trialing"
        assert eff("active", period=past) == "expired"
        assert eff("active", period=future) == "active"

    def test_is_entitled_and_can_install(self) -> None:
        assert is_entitled("active") and is_entitled("trialing")
        assert not is_entitled("expired")
        # Free listings always install.
        assert can_install(has_paid_plan=False, has_free_option=True, entitled=False)
        # A free option makes a paid listing installable.
        assert can_install(has_paid_plan=True, has_free_option=True, entitled=False)
        # Paid-only requires entitlement.
        assert not can_install(has_paid_plan=True, has_free_option=False, entitled=False)
        assert can_install(has_paid_plan=True, has_free_option=False, entitled=True)


class TestMetering:
    def test_aggregate(self) -> None:
        totals = aggregate([("api_call", 3), ("api_call", 2), ("message", 5)])
        assert totals["api_call"] == 5 and totals["message"] == 5
        assert totals["record"] == 0  # zero-filled

    def test_charge(self) -> None:
        assert billable_units(150, 100) == 50
        assert billable_units(50, 100) == 0
        assert meter_charge(2, 50) == 100

    def test_validation(self) -> None:
        with pytest.raises(ValueError):
            validate_metric("smiles")


class TestRevenue:
    def test_split_reconciles(self) -> None:
        split = split_revenue(1000, 2000)  # 20%
        assert split.platform_cents == 200 and split.developer_cents == 800
        assert split.platform_cents + split.developer_cents == split.gross_cents

    def test_split_rounds_to_developer(self) -> None:
        # 999 * 20% = 199.8 -> platform floors to 199, developer gets the rest.
        split = split_revenue(999, 2000)
        assert split.platform_cents == 199 and split.developer_cents == 800

    def test_bps_clamped(self) -> None:
        assert split_revenue(100, 20_000).platform_cents == 100  # clamped to 100%
        assert split_revenue(-5, 2000).gross_cents == 0
        with pytest.raises(ValueError):
            validate_kind("tribute")


# =============================================================== pricing (db)


class TestPricingPlans:
    async def test_create_and_list(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "price@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await _owned_listing(db, auth, user, "paid_a")

        billing = MarketplaceBillingService(db, auth, settings)
        plan = await billing.create_plan(user, listing.id, _plan())
        assert plan.amount_cents == 1000 and plan.is_free is False

        pricing = await billing.pricing(listing.id)
        assert pricing.has_paid_plan is True and pricing.has_free_option is False
        assert [p.key for p in pricing.plans] == ["pro"]

        # A duplicate plan key is refused.
        with pytest.raises(AppError):
            await billing.create_plan(user, listing.id, _plan())

        # The plan-created act is audited.
        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "marketplace.plan.created")
            )
        ).scalars().all()
        assert len(audit) == 1

    async def test_cannot_price_curated(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cur@vantage.example")
        auth = _auth(organization, user.id)
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)
        curated = next(
            listing for listing in await registry.discover() if listing.key == "stripe"
        )
        billing = MarketplaceBillingService(db, auth, _settings())
        with pytest.raises(AppError):
            await billing.create_plan(user, curated.id, _plan())

    async def test_create_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nom@vantage.example")
        listing = await _owned_listing(
            db, _auth(organization, user.id), user, "paid_nom"
        )
        billing = MarketplaceBillingService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await billing.create_plan(user, listing.id, _plan())


# =============================================================== entitlements (db)


class TestEntitlements:
    async def test_subscribe_books_revenue_and_audits(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sub@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await _owned_listing(db, auth, user, "paid_sub")
        await MarketplaceBillingService(db, auth, settings).create_plan(
            user, listing.id, _plan()
        )

        entitlements = IntegrationEntitlementService(db, auth, settings)
        ent = await entitlements.subscribe(user, listing.id, "pro")
        assert ent.status == "active" and ent.entitled is True
        assert ent.provider == "manual"

        # A second subscribe is refused.
        with pytest.raises(ConflictError):
            await entitlements.subscribe(user, listing.id, "pro")

        # Subscription revenue was booked and split 20/80.
        revenue = await RevenueTrackingService(db, auth, settings).analytics()
        assert revenue.gross_cents == 1000
        assert revenue.platform_cents == 200 and revenue.developer_cents == 800
        assert revenue.by_kind.get("subscription") == 1000

        granted = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "marketplace.entitlement.granted"
                )
            )
        ).scalars().all()
        assert len(granted) == 1

        # Cancel clears entitlement.
        canceled = await entitlements.cancel(user, listing.id)
        assert canceled.status == "canceled" and canceled.entitled is False
        assert (await entitlements.status(listing.id)).status == "none"

    async def test_trial_grants_without_revenue(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "trial@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await _owned_listing(db, auth, user, "paid_trial")
        await MarketplaceBillingService(db, auth, settings).create_plan(
            user, listing.id, _plan(key="pro", trial_days=14)
        )
        ent = await IntegrationEntitlementService(db, auth, settings).subscribe(
            user, listing.id, "pro"
        )
        assert ent.status == "trialing" and ent.entitled is True
        assert ent.trial_ends_at is not None
        # A trial books nothing yet.
        revenue = await RevenueTrackingService(db, auth, settings).analytics()
        assert revenue.gross_cents == 0

    async def test_free_plan_no_revenue(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "free@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await _owned_listing(db, auth, user, "free_x")
        await MarketplaceBillingService(db, auth, settings).create_plan(
            user, listing.id, _plan(key="free", pricing_model="free", amount_cents=0)
        )
        ent = await IntegrationEntitlementService(db, auth, settings).subscribe(
            user, listing.id, "free"
        )
        assert ent.status == "active"
        assert (await RevenueTrackingService(db, auth, settings).analytics()).gross_cents == 0


# =============================================================== usage (db)


class TestUsageMetering:
    async def test_record_and_summarise(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "use@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await _owned_listing(db, auth, user, "metered_x")
        billing = MarketplaceBillingService(db, auth, settings)
        await billing.create_plan(
            user,
            listing.id,
            _plan(
                key="metered",
                pricing_model="usage",
                amount_cents=500,
                included_units=100,
                unit_amount_cents=2,
            ),
        )
        await IntegrationEntitlementService(db, auth, settings).subscribe(
            user, listing.id, "metered"
        )

        usage = UsageMeteringService(db, auth, settings)
        await usage.record(listing.id, "api_call", 120)
        await usage.record(listing.id, "api_call", 30)

        summary = await usage.summary(listing.id)
        assert summary.total_units == 150
        # 50 billable units beyond the 100 allowance, at 2c each.
        assert summary.estimated_charge_cents == 100

        with pytest.raises(AppError):
            await usage.record(listing.id, "smiles", 1)


# =============================================================== install gate (db)


class TestInstallEnforcement:
    async def _publish(self, db, auth, user, settings, key):  # type: ignore[no-untyped-def]
        ops = MarketplaceOperationsService(db, auth, settings)
        draft = await ops.create_draft(user, _draft(key))
        await ops.submit(user, draft.id)
        await ops.review(user, draft.id, "approved", None)
        await ops.publish(user, draft.id)
        return draft

    async def test_paid_only_requires_entitlement(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "gate@vantage.example")
        auth = _auth(organization, user.id)
        enforced = _settings(MARKETPLACE_BILLING_ENFORCED=True)
        listing = await self._publish(db, auth, user, enforced, "paid_gate")
        await MarketplaceBillingService(db, auth, enforced).create_plan(
            user, listing.id, _plan()
        )

        ops = MarketplaceOperationsService(db, auth, enforced)
        # Paid-only, no entitlement -> install refused.
        with pytest.raises(AppError):
            await ops.install(user, listing.id, InstallListingRequest())

        # Subscribe, then install succeeds.
        await IntegrationEntitlementService(db, auth, enforced).subscribe(
            user, listing.id, "pro"
        )
        installed = await ops.install(user, listing.id, InstallListingRequest())
        assert installed.listing_key == "paid_gate"

    async def test_unenforced_allows_install(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "noenf@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()  # enforcement off (default)
        listing = await self._publish(db, auth, user, settings, "paid_open")
        await MarketplaceBillingService(db, auth, settings).create_plan(
            user, listing.id, _plan()
        )
        ops = MarketplaceOperationsService(db, auth, settings)
        # Even a paid-only listing installs when enforcement is off.
        installed = await ops.install(user, listing.id, InstallListingRequest())
        assert installed.listing_key == "paid_open"


# =============================================================== isolation (db)


class TestTenantIsolation:
    async def test_revenue_and_plans_isolated(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "a@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await _owned_listing(db, auth, user, "paid_iso")
        await MarketplaceBillingService(db, auth, settings).create_plan(
            user, listing.id, _plan()
        )
        await IntegrationEntitlementService(db, auth, settings).subscribe(
            user, listing.id, "pro"
        )

        # A different tenant sees none of it: not the private listing, not its
        # revenue.
        them = await make_user(db, other_organization, "b@meridian.example")
        them_auth = _auth(other_organization, them.id)
        them_settings = _settings()
        their_revenue = await RevenueTrackingService(
            db, them_auth, them_settings
        ).analytics()
        assert their_revenue.gross_cents == 0
        with pytest.raises(NotFoundError):
            await MarketplaceBillingService(db, them_auth, them_settings).pricing(
                listing.id
            )
