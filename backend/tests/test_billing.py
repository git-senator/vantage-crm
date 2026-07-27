"""Billing & metered plans (Phase 7.5).

What these pin down:

  * the manual provider synthesises ids/periods and calls nothing;
  * entitlements follow the subscription — the subscribed plan while entitled,
    the default plan once a past-due grace lapses or it cancels;
  * quotas enforce a plan's limits, and unlimited is a no-op;
  * the usage meter reads the durable record: AI spend, storage, seats, and the
    live API-key metric;
  * subscribe / change / seats / cancel go through the provider, are audited,
    and refuse to license fewer seats than are in use;
  * a provider event moves status, opens and clears the grace deadline, and
    records invoices;
  * the grace sweep cancels a lapsed subscription;
  * with enforcement on, minting an API key is gated by plan feature and quota.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.ai import AiJob
from app.models.audit import AuditLog
from app.models.billing import Subscription
from app.models.organization import Organization
from app.services.billing.catalog import seed_plans
from app.services.billing.provider import (
    ManualBillingProvider,
    ProviderEvent,
    ProviderInvoice,
    ProviderSubscription,
)
from app.services.billing.service import (
    EntitlementService,
    QuotaService,
    SubscriptionService,
    UsageMeter,
)
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": "test_secret_that_is_at_least_thirty_two_chars",
        "ENVIRONMENT": "test",
        "BILLING_PROVIDER": "manual",
        "BILLING_DEFAULT_PLAN": "free",
        "BILLING_GRACE_PERIOD_DAYS": 14,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, **grants: Scope) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants={"settings.manage": Scope.ALL, **grants},
    )


# ------------------------------------------------------------------ provider


class TestManualProvider:
    async def test_synthesises_a_subscription(self) -> None:
        provider = ManualBillingProvider()
        customer = await provider.create_customer(
            organization_id=uuid4(), email="a@b.test", name="A"
        )
        assert customer.startswith("manual_cus_")
        sub = await provider.create_subscription(
            customer_id=customer, price_id=None, seats=3, organization_id=uuid4()
        )
        assert sub.status == "active"
        assert sub.current_period_end is not None

    async def test_portal_and_webhook_are_unsupported(self) -> None:
        provider = ManualBillingProvider()
        with pytest.raises(ConflictError):
            await provider.create_portal_url("cus", "https://x")
        with pytest.raises(ConflictError):
            provider.verify_event(b"{}", None)


# --------------------------------------------------------------- entitlements


class TestEntitlements:
    async def test_default_plan_without_a_subscription(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "e1@vantage.example")
        entitlements = EntitlementService(db, _auth(organization, user.id))

        plan = await entitlements.effective_plan()
        assert plan is not None and plan.key == "free"
        assert await entitlements.has_feature("webhooks") is False

    async def test_subscribed_plan_grants_its_features(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "e2@vantage.example")
        auth = _auth(organization, user.id)
        await SubscriptionService(db, auth, _settings()).subscribe(
            user, plan_key="pro"
        )

        entitlements = EntitlementService(db, auth)
        plan = await entitlements.effective_plan()
        assert plan is not None and plan.key == "pro"
        assert await entitlements.has_feature("webhooks") is True

    async def test_past_due_falls_back_once_grace_lapses(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "e3@vantage.example")
        auth = _auth(organization, user.id)
        service = SubscriptionService(db, auth, _settings())
        subscription = await service.subscribe(user, plan_key="pro")

        # Provider says the subscription went past due.
        await service.apply_event(
            _sub_event(organization, subscription, status="past_due")
        )
        entitlements = EntitlementService(db, auth)
        # Within grace: still the pro plan.
        assert (await entitlements.effective_plan()).key == "pro"  # type: ignore[union-attr]
        # Past the grace deadline: back to the default plan.
        subscription.grace_period_end = datetime.now(UTC) - timedelta(days=1)
        await db.flush()
        assert (await entitlements.effective_plan()).key == "free"  # type: ignore[union-attr]


# -------------------------------------------------------------------- quotas


class TestQuotas:
    async def test_enforces_a_capped_quota(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "q1@vantage.example")
        quotas = QuotaService(db, _auth(organization, user.id))
        # Free allows one API key.
        await quotas.enforce("api_keys", current=0, adding=1, resource="API keys")
        with pytest.raises(ConflictError):
            await quotas.enforce("api_keys", current=1, adding=1, resource="API keys")

    async def test_unlimited_is_a_noop(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "q2@vantage.example")
        auth = _auth(organization, user.id)
        await SubscriptionService(db, auth, _settings()).subscribe(
            user, plan_key="enterprise"
        )
        quotas = QuotaService(db, auth)
        # Enterprise lists no api_keys quota — unlimited.
        assert await quotas.limit("api_keys") is None
        await quotas.enforce("api_keys", current=10_000, adding=1, resource="API keys")


# --------------------------------------------------------------- usage meter


class TestUsageMeter:
    async def test_reads_the_durable_record(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.observability import metrics

        metrics.REGISTRY.reset()
        user = await make_user(db, organization, "u1@vantage.example")
        user.status = "active"
        db.add(
            AiJob(
                organization_id=organization.id,
                feature="assistant",
                provider="echo",
                model="m",
                status="succeeded",
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=Decimal("0.010000"),
            )
        )
        await db.flush()
        metrics.record_api_key_request(organization_id=organization.id)

        summary = await UsageMeter(db, _auth(organization, user.id), _settings()).summary()
        assert summary["seats_used"] >= 1
        assert summary["ai_cost_usd"] == Decimal("0.010000")
        assert summary["api_calls"] == 1
        assert summary["storage_bytes"] == 0


# --------------------------------------------------------------- subscriptions


class TestSubscriptions:
    async def test_subscribe_creates_and_audits(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "s1@vantage.example")
        auth = _auth(organization, user.id)
        subscription = await SubscriptionService(db, auth, _settings()).subscribe(
            user, plan_key="pro", seats=7
        )
        await db.flush()

        assert subscription.status == "active"
        assert subscription.seats == 7
        assert subscription.provider == "manual"
        assert subscription.provider_subscription_id
        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "billing.subscription.created")
            )
        ).scalars().one()
        assert entry.entity_id == subscription.id

    async def test_subscribe_requires_settings_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "s2@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN},
        )
        with pytest.raises(PermissionDeniedError):
            await SubscriptionService(db, auth, _settings()).subscribe(
                user, plan_key="pro"
            )

    async def test_seats_cannot_drop_below_usage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        # Three active users in the workspace.
        actor = await make_user(db, organization, "s3a@vantage.example")
        actor.status = "active"
        for i in range(2):
            extra = await make_user(db, organization, f"s3b{i}@vantage.example")
            extra.status = "active"
        auth = _auth(organization, actor.id)
        service = SubscriptionService(db, auth, _settings())
        await service.subscribe(actor, plan_key="pro", seats=5)

        with pytest.raises(ConflictError):
            await service.set_seats(actor, 2)

    async def test_cancel_immediately_marks_canceled(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "s4@vantage.example")
        auth = _auth(organization, user.id)
        service = SubscriptionService(db, auth, _settings())
        await service.subscribe(user, plan_key="pro")

        subscription = await service.cancel(user, at_period_end=False)
        assert subscription.status == "canceled"
        assert subscription.canceled_at is not None

    async def test_event_opens_grace_and_records_invoice(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await seed_plans(db)
        user = await make_user(db, organization, "s5@vantage.example")
        auth = _auth(organization, user.id)
        service = SubscriptionService(db, auth, _settings())
        subscription = await service.subscribe(user, plan_key="pro")

        await service.apply_event(
            _sub_event(organization, subscription, status="past_due")
        )
        assert subscription.status == "past_due"
        assert subscription.grace_period_end is not None

        # Recovery clears the grace deadline.
        await service.apply_event(
            _sub_event(organization, subscription, status="active")
        )
        assert subscription.grace_period_end is None

        # An invoice event is recorded into history.
        await service.apply_event(
            ProviderEvent(
                type="invoice.paid",
                organization_id=organization.id,
                invoice=ProviderInvoice(
                    id="inv_1", number="A-1", status="paid",
                    amount_due_cents=4900, amount_paid_cents=4900, currency="USD",
                    period_start=None, period_end=None, hosted_url=None,
                    pdf_url=None, issued_at=datetime.now(UTC),
                ),
            )
        )
        rows, _ = await service.list_invoices(limit=10)
        assert len(rows) == 1
        assert rows[0].amount_paid_cents == 4900


# ------------------------------------------------------------- enforcement


class TestEnforcement:
    async def test_api_key_gated_by_plan_when_enforced(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.services.api_key import ApiKeyService

        await seed_plans(db)
        user = await make_user(db, organization, "en1@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings(BILLING_ENFORCED=True)

        # Default (free) plan does not include api_access.
        with pytest.raises(PermissionDeniedError):
            await ApiKeyService(db, settings).create(
                auth, user, name="k", scopes={}, expires_in_days=1
            )

        # On pro, api_access is granted and the quota has room.
        await SubscriptionService(db, auth, settings).subscribe(user, plan_key="pro")
        key, _secret = await ApiKeyService(db, settings).create(
            auth, user, name="k", scopes={}, expires_in_days=1
        )
        assert key.id is not None


# --------------------------------------------------------------- grace sweep


class TestGraceSweep:
    async def test_sweep_cancels_lapsed_subscription(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.workers.jobs.billing import sweep_subscription_grace

        await seed_plans(db)
        user = await make_user(db, organization, "g1@vantage.example")
        auth = _auth(organization, user.id)
        subscription = await SubscriptionService(db, auth, _settings()).subscribe(
            user, plan_key="pro"
        )
        subscription.status = "past_due"
        subscription.grace_period_end = datetime.now(UTC) - timedelta(days=1)
        await db.commit()

        canceled = await sweep_subscription_grace({})
        assert canceled >= 1

        # Verify through a fresh session — the job committed on its own
        # connection, and the test's committed session is done with.
        from app.db.session import session_scope

        async with session_scope(organization_id=organization.id) as verify:
            row = (
                await verify.execute(
                    select(Subscription).where(Subscription.id == subscription.id)
                )
            ).scalar_one()
            assert row.status == "canceled"


def _sub_event(
    organization: Organization, subscription: Subscription, *, status: str
) -> ProviderEvent:
    return ProviderEvent(
        type="customer.subscription.updated",
        organization_id=organization.id,
        subscription=ProviderSubscription(
            id=subscription.provider_subscription_id or "sub_x",
            customer_id=subscription.provider_customer_id or "cus_x",
            status=status,
            price_id=None,
            current_period_start=datetime.now(UTC),
            current_period_end=datetime.now(UTC) + timedelta(days=30),
        ),
    )
