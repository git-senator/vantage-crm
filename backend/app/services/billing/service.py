"""Billing services: subscriptions, entitlements, quotas, and usage.

Four responsibilities, kept close because they share the same tenant subscription
as their input:

  * `SubscriptionService` — the write side. Subscribe, change plan, change seats,
    cancel, apply a provider webhook, record invoices. Every mutation goes
    through the provider abstraction and is audited; `settings.manage` gates it.
  * `EntitlementService` — what a tenant may do *right now*: its effective plan
    (the subscribed plan while entitled, the default plan otherwise), the
    features that plan grants, and the grace-aware status.
  * `QuotaService` — the numeric limits of that plan, and the one call a caller
    makes to enforce one. A missing quota key is unlimited.
  * `UsageMeter` — what the tenant has actually used, read from the durable
    record the earlier phases already keep: AI spend from the `ai_jobs` ledger,
    API calls from the API-key metrics, storage from attachment sizes, seats
    from the live user count.

None of this bypasses a business service — it reads their ledgers and metrics,
and enforcement is opt-in behind `BILLING_ENFORCED` so a tenant without a plan
is never blocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.models.billing import Invoice, Plan, Subscription
from app.models.user import User
from app.observability import metrics
from app.repositories.ai import AiJobRepository
from app.repositories.attachment import AttachmentRepository
from app.repositories.billing import (
    InvoiceRepository,
    PlanRepository,
    SubscriptionRepository,
)
from app.services.audit import AuditService
from app.services.billing import get_billing_provider
from app.services.billing.provider import ProviderEvent, ProviderInvoice
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

MANAGE_PERMISSION = "settings.manage"


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# ------------------------------------------------------------- entitlements


class EntitlementService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.subs = SubscriptionRepository(session)
        self.plans = PlanRepository(session)

    async def subscription(self) -> Subscription | None:
        return await self.subs.get_for_org(self.auth.organization_id)

    async def effective_plan(self, *, now: datetime | None = None) -> Plan | None:
        """The plan whose features and quotas apply right now.

        The subscribed plan while the subscription is entitled (active,
        trialing, or past-due within grace); otherwise the configured default
        plan — so a lapsed tenant falls back rather than keeping paid features.
        """
        from app.core.config import get_settings

        subscription = await self.subscription()
        if subscription is not None and subscription.is_entitled(now=now):
            return subscription.plan
        return await self.plans.get_by_key(get_settings().BILLING_DEFAULT_PLAN)

    async def features(self, *, now: datetime | None = None) -> dict[str, Any]:
        plan = await self.effective_plan(now=now)
        return dict(plan.features) if plan else {}

    async def has_feature(self, feature: str, *, now: datetime | None = None) -> bool:
        return bool((await self.features(now=now)).get(feature))

    async def require(self, feature: str) -> None:
        """Raise 403 unless the tenant's effective plan grants `feature`."""
        if not await self.has_feature(feature):
            raise PermissionDeniedError(
                f"Your plan does not include '{feature}'. Upgrade to enable it."
            )

    async def list_plans(self) -> list[Plan]:
        """The active plan catalogue, for the pricing/upgrade view."""
        return list(await self.plans.list_active())

    async def summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        """The tenant's current entitlement state, for the app to gate features."""
        subscription = await self.subscription()
        plan = await self.effective_plan(now=now)
        return {
            "plan_key": plan.key if plan else None,
            "status": subscription.status if subscription else "none",
            "features": dict(plan.features) if plan else {},
            "in_grace": bool(
                subscription
                and subscription.status == "past_due"
                and subscription.is_entitled(now=now)
            ),
        }


# -------------------------------------------------------------------- quotas


class QuotaService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.entitlements = EntitlementService(session, auth)

    async def limit(self, quota_key: str) -> int | None:
        """The tenant's limit for a quota, or None for unlimited."""
        plan = await self.entitlements.effective_plan()
        if plan is None:
            return None
        value = plan.quotas.get(quota_key)
        return int(value) if value is not None else None

    async def enforce(
        self, quota_key: str, current: int, *, adding: int = 1, resource: str
    ) -> None:
        """Raise 409 if adding `adding` would exceed the tenant's limit.

        A no-op when the limit is unlimited. The caller passes the current count
        rather than this service computing it, so the same enforcement works for
        any resource without this knowing how to count each one.
        """
        limit = await self.limit(quota_key)
        if limit is None:
            return
        if current + adding > limit:
            raise ConflictError(
                f"Your plan allows {limit} {resource}. Upgrade to add more."
            )


# --------------------------------------------------------------- usage meter


class UsageMeter:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.ai = AiJobRepository(session)
        self.attachments = AttachmentRepository(session)

    async def seats_used(self) -> int:
        query = (
            select(func.count())
            .select_from(User)
            .where(User.organization_id == self.auth.organization_id)
            .where(User.status == "active")
        )
        return int((await self.session.execute(query)).scalar() or 0)

    def api_calls(self) -> int:
        """API requests attributed to this tenant since the process last started.

        Read from the live metric the API-key auth records (Phase 7.4). It is a
        process-local counter, so it is the current window's usage rather than a
        durable month-to-date total — the honest thing for a live meter.
        """
        tenant = str(self.auth.organization_id)
        total = 0.0
        for labels, value in (
            metrics.REGISTRY.snapshot().get("api_key_requests_total", {}).items()
        ):
            if dict(labels).get("tenant") == tenant:
                total += value
        return int(total)

    async def summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        org = self.auth.organization_id
        ai_cost = await self.ai.spend_since(org, _month_start(moment))
        storage_bytes = await self.attachments.total_bytes(org)
        return {
            "period_start": _month_start(moment),
            "seats_used": await self.seats_used(),
            "ai_cost_usd": ai_cost,
            "api_calls": self.api_calls(),
            "storage_bytes": storage_bytes,
            "storage_gb": round(storage_bytes / (1024**3), 4),
        }


# --------------------------------------------------------------- subscriptions


class SubscriptionService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.subs = SubscriptionRepository(session)
        self.plans = PlanRepository(session)
        self.invoices = InvoiceRepository(session)
        self.audit = AuditService(session)
        self.provider = get_billing_provider(settings)

    async def get_current(self) -> Subscription | None:
        self.auth.require(MANAGE_PERMISSION)
        return await self.subs.get_for_org(self.auth.organization_id)

    async def subscribe(
        self, actor: User, *, plan_key: str, seats: int | None = None
    ) -> Subscription:
        """Start a subscription, or move an existing one onto a new plan/seats."""
        self.auth.require(MANAGE_PERMISSION)
        plan = await self.plans.get_by_key(plan_key)
        if plan is None:
            raise NotFoundError("Plan not found.")

        resolved_seats = seats or plan.included_seats
        await self._assert_seats_cover_usage(resolved_seats)

        existing = await self.subs.get_for_org(self.auth.organization_id)
        if existing is None:
            return await self._create(actor, plan, resolved_seats)
        return await self._change(actor, existing, plan, resolved_seats)

    async def _create(
        self, actor: User, plan: Plan, seats: int
    ) -> Subscription:
        customer_id = await self.provider.create_customer(
            organization_id=self.auth.organization_id,
            email=actor.email,
            name=actor.full_name,
        )
        psub = await self.provider.create_subscription(
            customer_id=customer_id,
            price_id=plan.provider_price_id,
            seats=seats,
            organization_id=self.auth.organization_id,
        )
        subscription = Subscription(
            organization_id=self.auth.organization_id,
            plan_id=plan.id,
            status=psub.status,
            seats=seats,
            provider=self.provider.name,
            provider_customer_id=customer_id,
            provider_subscription_id=psub.id,
            current_period_start=psub.current_period_start,
            current_period_end=psub.current_period_end,
        )
        await self.subs.add(subscription)
        await self._audit(AuditAction.SUBSCRIPTION_CREATED, actor, subscription, plan)
        logger.info(
            "subscription_created",
            extra={"organization_id": str(self.auth.organization_id), "plan": plan.key},
        )
        return await self._reload(subscription)

    async def _change(
        self, actor: User, subscription: Subscription, plan: Plan, seats: int
    ) -> Subscription:
        if subscription.provider_subscription_id:
            psub = await self.provider.update_subscription(
                subscription.provider_subscription_id,
                price_id=plan.provider_price_id,
                seats=seats,
            )
            subscription.status = psub.status
            subscription.current_period_start = psub.current_period_start
            subscription.current_period_end = psub.current_period_end
        subscription.plan_id = plan.id
        subscription.seats = seats
        await self.session.flush()
        await self._audit(AuditAction.SUBSCRIPTION_UPDATED, actor, subscription, plan)
        logger.info(
            "subscription_changed",
            extra={"organization_id": str(self.auth.organization_id), "plan": plan.key},
        )
        return await self._reload(subscription)

    async def set_seats(self, actor: User, seats: int) -> Subscription:
        self.auth.require(MANAGE_PERMISSION)
        subscription = await self._load()
        await self._assert_seats_cover_usage(seats)
        if subscription.provider_subscription_id:
            await self.provider.update_subscription(
                subscription.provider_subscription_id, seats=seats
            )
        subscription.seats = seats
        await self.session.flush()
        await self._audit(
            AuditAction.SUBSCRIPTION_UPDATED, actor, subscription, subscription.plan
        )
        return await self._reload(subscription)

    async def cancel(self, actor: User, *, at_period_end: bool = True) -> Subscription:
        self.auth.require(MANAGE_PERMISSION)
        subscription = await self._load()
        if subscription.provider_subscription_id:
            await self.provider.cancel_subscription(
                subscription.provider_subscription_id, at_period_end=at_period_end
            )
        subscription.cancel_at_period_end = at_period_end
        if not at_period_end:
            subscription.status = "canceled"
            subscription.canceled_at = datetime.now(UTC)
        await self.session.flush()
        await self._audit(
            AuditAction.SUBSCRIPTION_CANCELED, actor, subscription, subscription.plan
        )
        logger.info(
            "subscription_canceled",
            extra={
                "organization_id": str(self.auth.organization_id),
                "at_period_end": at_period_end,
            },
        )
        return await self._reload(subscription)

    async def portal_url(self, return_url: str | None = None) -> str:
        self.auth.require(MANAGE_PERMISSION)
        subscription = await self._load()
        if not subscription.provider_customer_id:
            raise ConflictError("This subscription has no billing customer.")
        target = return_url or self.settings.STRIPE_PORTAL_RETURN_URL or ""
        return await self.provider.create_portal_url(
            subscription.provider_customer_id, target
        )

    async def list_invoices(
        self, *, limit: int, cursor: Any = None
    ) -> tuple[list[Invoice], bool]:
        self.auth.require(MANAGE_PERMISSION)
        return await self.invoices.list_for_org(
            self.auth.organization_id, limit=limit, cursor=cursor
        )

    # ----------------------------------------------------- webhook / sync

    async def apply_event(self, event: ProviderEvent) -> None:
        """Apply a verified provider event to this tenant's subscription.

        The tenant is already bound by the caller (resolved from the event's
        metadata). Subscription events move status, period and the grace
        deadline; invoice events are recorded into the history.
        """
        if event.subscription is not None:
            await self._sync_subscription(event)
        if event.invoice is not None:
            await self._record_invoice(event.invoice)

    async def _sync_subscription(self, event: ProviderEvent) -> None:
        assert event.subscription is not None
        incoming = event.subscription
        subscription = await self.subs.get_by_provider_subscription(
            incoming.id, self.auth.organization_id
        )
        if subscription is None:
            logger.warning(
                "billing_event_unknown_subscription",
                extra={"provider_subscription_id": incoming.id},
            )
            return

        subscription.status = incoming.status
        subscription.current_period_start = incoming.current_period_start
        subscription.current_period_end = incoming.current_period_end
        subscription.cancel_at_period_end = incoming.cancel_at_period_end

        if incoming.status == "past_due" and subscription.grace_period_end is None:
            subscription.grace_period_end = datetime.now(UTC) + timedelta(
                days=self.settings.BILLING_GRACE_PERIOD_DAYS
            )
        elif incoming.status in ("active", "trialing"):
            subscription.grace_period_end = None
        if incoming.status == "canceled" and subscription.canceled_at is None:
            subscription.canceled_at = datetime.now(UTC)
        await self.session.flush()

    async def _record_invoice(self, incoming: ProviderInvoice) -> None:
        existing = await self.invoices.get_by_provider_id(
            incoming.id, self.auth.organization_id
        )
        subscription = await self.subs.get_for_org(self.auth.organization_id)
        if existing is None:
            invoice = Invoice(
                organization_id=self.auth.organization_id,
                subscription_id=subscription.id if subscription else None,
                provider_invoice_id=incoming.id,
                number=incoming.number,
                status=incoming.status,
                amount_due_cents=incoming.amount_due_cents,
                amount_paid_cents=incoming.amount_paid_cents,
                currency=incoming.currency,
                period_start=incoming.period_start,
                period_end=incoming.period_end,
                hosted_invoice_url=incoming.hosted_url,
                pdf_url=incoming.pdf_url,
                issued_at=incoming.issued_at,
            )
            self.session.add(invoice)
        else:
            existing.status = incoming.status
            existing.amount_paid_cents = incoming.amount_paid_cents
        await self.session.flush()

    # ----------------------------------------------------------- helpers

    async def _load(self) -> Subscription:
        subscription = await self.subs.get_for_org(self.auth.organization_id)
        if subscription is None:
            raise NotFoundError("This workspace has no subscription.")
        return subscription

    async def _assert_seats_cover_usage(self, seats: int) -> None:
        used = await UsageMeter(self.session, self.auth, self.settings).seats_used()
        if seats < used:
            raise ConflictError(
                f"{used} seats are in use; reduce active users before setting {seats}."
            )

    async def _reload(self, subscription: Subscription) -> Subscription:
        await self.session.refresh(subscription, ["updated_at"])
        return subscription

    async def _audit(
        self, action: str, actor: User, subscription: Subscription, plan: Plan
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="subscription",
            entity_id=subscription.id,
            metadata={
                "plan": plan.key,
                "status": subscription.status,
                "seats": subscription.seats,
            },
        )


__all__ = [
    "MANAGE_PERMISSION",
    "EntitlementService",
    "QuotaService",
    "SubscriptionService",
    "UsageMeter",
]
